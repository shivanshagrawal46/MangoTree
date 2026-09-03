"""
Sprint 2 · Step 2 — chunk + contextual-summary + embed every Phase-3 document
(title reports, insurance, equity schedule, service agreement, litigation) into
the LIVE vector corpus `email_chunks_v2`, so a query reaches this content too.

  * Chunking : structural 1000/200 (same as the email corpus).
  * Context  : Claude Sonnet 4.6 per-chunk situating summary (prompt-cached).
  * Embedding: voyage-4-large (1024-dim), same index the emails use.
  * Each chunk carries document_id + source_type + corpus + privilege +
    property_ids/entity_refs + owner + dates, so entity fan-out works.

RESUMABLE / CRASH-SAFE:
  * A document is processed all-or-nothing: its old chunks are deleted, new
    ones inserted, THEN `chunked_at` is stamped on the document. A crash before
    the stamp leaves the doc un-stamped -> it is simply redone next run. No
    partial/duplicate state. Re-run the SAME command to continue where it
    stopped.

Usage:
  python -m scripts.chunk_embed_documents            # process all pending
  python -m scripts.chunk_embed_documents --limit 5  # smoke test first 5
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.chunker import _chunk_text
from src.rag.tokens import count_tokens
from src.rag.embedder import VoyageEmbedder
from src.rag.v2.contextual_summary import ContextualSummarizer
from src.utils.logger import logger

CHUNKS = "email_chunks_v2"
TARGET_TYPES = ["title_report", "insurance", "equity_schedule",
                "service_agreement", "litigation_update", "court_record"]


def _fmt_date(d) -> str:
    try:
        return d.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return ""


def header_for(doc: Dict[str, Any]) -> str:
    st = doc.get("source_type")
    if st == "title_report":
        bits = [doc.get("vendor") or "", doc.get("property_address") or "",
                f"Order {doc.get('order_number')}" if doc.get("order_number") else "",
                ("update" if doc.get("is_update") else "full") + " search",
                _fmt_date(doc.get("completed_date") or doc.get("search_date"))]
        return "[Title Report — " + " | ".join(b for b in bits if b) + "]"
    if st == "insurance":
        bits = [doc.get("insurer") or "", doc.get("named_insured") or "",
                ", ".join(doc.get("covered_addresses") or [])[:60],
                str(doc.get("policy_year") or ""),
                "CANCELLATION" if doc.get("is_cancellation") else "evidence of coverage"]
        return "[Insurance — " + " | ".join(b for b in bits if b) + "]"
    if st == "equity_schedule":
        return "[Equity Schedule — David properties for sheriff sale | as of 2025-03-25]"
    if st == "service_agreement":
        return "[Service Agreement — Mango Tree & Island Properties (David)]"
    if st == "litigation_update":
        return f"[Litigation Update — #{doc.get('sequence_no')} | {_fmt_date(doc.get('document_date'))}]"
    if st == "court_record":
        bits = [f"Docket #{doc.get('docket_no')}" if doc.get("docket_no") else "",
                doc.get("document_title") or "",
                f"{doc.get('case_title') or ''} {doc.get('case_number') or ''}".strip(),
                _fmt_date(doc.get("document_date"))]
        return "[Court Record — " + " | ".join(b for b in bits if b) + "]"
    return f"[{st}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reembed", action="store_true",
                    help="re-do docs even if already chunked")
    ap.add_argument("--doc-id", default=None,
                    help="re-chunk/embed ONLY this document _id (scoped, cheap)")
    ap.add_argument("--shard", default=None,
                    help="k/N disjoint worker shard by hash(_id), e.g. 0/4")
    args = ap.parse_args()
    s = Settings.load()
    now = datetime.now(timezone.utc)
    size, overlap = s.chunk_size_tokens, s.chunk_overlap_tokens

    summarizer = ContextualSummarizer(s.anthropic_api_key, model="claude-sonnet-4-6")
    embedder = VoyageEmbedder(s.voyage_api_key, model="voyage-4-large")
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs, chunks = m.db["documents"], m.db[CHUNKS]
    from pymongo import ASCENDING
    for keys, nm in [([("document_id", ASCENDING)], "ix_document_id"),
                     ([("entity_refs.properties", ASCENDING)], "ix_entity_props"),
                     ([("primary_property_id", ASCENDING)], "ix_primary_prop")]:
        try:
            chunks.create_index(keys, name=nm)
        except Exception:  # noqa: BLE001
            pass

    q: Dict[str, Any] = {"source_type": {"$in": TARGET_TYPES}}
    if args.doc_id:
        q = {"_id": args.doc_id}            # scoped single-doc re-embed
        chunks.delete_many({"document_id": args.doc_id})  # drop stale chunks first
    elif not args.reembed:
        q["chunked_at"] = {"$exists": False}
    pending = list(docs.find(q, {"_id": 1}).sort("_id", ASCENDING))
    if args.shard:
        import hashlib
        sk, sn = (int(x) for x in args.shard.split("/"))
        pending = [r for r in pending
                   if int(hashlib.md5(str(r["_id"]).encode()).hexdigest(), 16) % sn == sk]
        logger.info(f"shard {sk}/{sn}: {len(pending)} docs in this worker")
    if args.limit:
        pending = pending[: args.limit]
    total_docs = docs.count_documents({"source_type": {"$in": TARGET_TYPES}})
    logger.info(f"{len(pending)} documents to chunk/embed "
                f"({total_docs - len(pending)} already done) | chunk {size}/{overlap}")

    done = total_chunks = 0
    for n, ref in enumerate(pending, 1):
        d = docs.find_one({"_id": ref["_id"]})
        text = (d.get("extracted_text") or "").strip()
        if not text:
            docs.update_one({"_id": d["_id"]}, {"$set": {"chunked_at": now, "chunk_count": 0,
                            "chunk_skip_reason": "empty_text"}})
            logger.info(f"  [{n}/{len(pending)}] {d['_id']}: empty text — skipped")
            continue
        header = header_for(d)
        body_budget = max(128, size - count_tokens(header) - 4)
        bodies = _chunk_text(text, max_tokens=body_budget, overlap_tokens=overlap)
        full_texts = [f"{header}\n\n{b}" for b in bodies]
        try:
            summaries = summarizer.summarize_doc_chunks(text, bodies)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"  [{n}/{len(pending)}] {d['_id']}: summary failed ({str(exc)[:80]}); using no-context")
            summaries = [""] * len(bodies)
        embed_texts = [(f"[Context] {summaries[i]}\n\n{full_texts[i]}" if summaries[i] else full_texts[i])
                       for i in range(len(bodies))]
        vectors = embedder.embed_documents(embed_texts)

        prop_ids = d.get("property_ids") or []
        chunk_docs = []
        for i, body in enumerate(bodies):
            chunk_docs.append({
                "_id": f"{d['_id']}::{i}", "source_type": d["source_type"],
                "document_id": d["_id"], "sha256": (d.get("custody") or {}).get("sha256"),
                "chunk_index": i, "total_chunks": len(bodies),
                "text": embed_texts[i], "body": body, "context": summaries[i],
                "n_tokens": count_tokens(embed_texts[i]),
                "embedding": vectors[i], "embedding_model": "voyage-4-large",
                "matter_id": d.get("matter_id"),
                "corpus": d.get("corpus"), "privilege_status": d.get("privilege_status"),
                "doc_source_type": d["source_type"], "doc_authority_score": d.get("authority_score"),
                "vendor": d.get("vendor"), "is_update": d.get("is_update"),
                "property_ids": prop_ids, "primary_property_id": (prop_ids[0] if prop_ids else None),
                "owner_entity_id": d.get("owner_entity_id"),
                "case_ids": d.get("case_ids") or [],
                "entity_refs": {"properties": prop_ids, "cases": d.get("case_ids") or []},
                "property_address": d.get("property_address"),
                "doc_date": (d.get("completed_date") or d.get("search_date")
                             or d.get("effective_date") or d.get("document_date")
                             or d.get("as_of_date")),
                "latest_date": (d.get("completed_date") or d.get("search_date")
                                or d.get("effective_date") or d.get("document_date")),
                "created_at": now,
            })
        # all-or-nothing: clear old chunks for this doc, insert fresh, THEN stamp
        chunks.delete_many({"document_id": d["_id"]})
        if chunk_docs:
            chunks.insert_many(chunk_docs, ordered=False)
        docs.update_one({"_id": d["_id"]}, {"$set": {"chunked_at": now, "chunk_count": len(chunk_docs)}})
        done += 1
        total_chunks += len(chunk_docs)
        u = summarizer.total_usage
        logger.info(f"  [{n}/{len(pending)}] {d['source_type']} {d['_id'][:34]} -> "
                    f"{len(chunk_docs)} chunks | sonnet_in={getattr(u,'input_tokens',0)} "
                    f"cache_read={getattr(u,'cache_read_tokens',0)}")

    logger.info("================ CHUNK+EMBED DONE (this run) ================")
    logger.info(f"docs processed={done}  chunks written={total_chunks}")
    logger.info(f"email_chunks_v2 now: {chunks.estimated_document_count()} total chunks; "
                f"phase3 docs chunked={docs.count_documents({'source_type':{'$in':TARGET_TYPES},'chunked_at':{'$exists':True}})}"
                f"/{total_docs}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
