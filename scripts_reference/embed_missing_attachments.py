"""
Targeted finisher: embed only attachments that have non-empty extracted_text
but no chunks yet. Joins by attachments.sha256 == chunks.sha256.

This is dramatically faster than walking all emails when most of the
attachment corpus is already embedded — perfect for resuming after a
shutdown.

Usage:
    python scripts/embed_missing_attachments.py
    python scripts/embed_missing_attachments.py --batch-size 32
    python scripts/embed_missing_attachments.py --limit 10   # smoke
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from pymongo import InsertOne

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.chunker import chunk_attachment
from src.rag.embedder import VoyageEmbedder
from src.utils.logger import configure_logger, logger


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _build_attachment_chunk_docs(
    email: Dict[str, Any],
    attachment: Dict[str, Any],
    chunks,
    source_hash: str,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    base = {
        "email_id": email["_id"],
        "attachment_id": attachment["_id"],
        "source_type": "attachment",
        "source_hash": source_hash,
        "date": email.get("date"),
        "date_ym": email.get("date_ym"),
        "from_email": (email.get("from") or {}).get("email"),
        "to_emails": [t.get("email") for t in (email.get("to") or []) if t and t.get("email")],
        "subject": email.get("subject"),
        "folder_path": email.get("folder_path"),
        "filename": attachment.get("filename"),
        "extension": attachment.get("extension"),
        "sha256": attachment.get("sha256"),
        "extraction_method": (attachment.get("extraction") or {}).get("method"),
        "ocr_confidence": (attachment.get("extraction") or {}).get("avg_ocr_confidence"),
    }
    for c in chunks:
        d = dict(base)
        d.update({
            "chunk_index": c.chunk_index,
            "text": c.text,
            "body": c.body,
            "n_tokens": c.n_tokens,
            "page_start": c.page_start,
            "page_end": c.page_end,
        })
        out.append(d)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--limit", type=int, default=0, help="Process N attachments only")
    args = p.parse_args()

    s = Settings.load()
    configure_logger(s.logs_dir)
    mongo = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    embedder = VoyageEmbedder(api_key=s.voyage_api_key, model=s.embedding_model)

    try:
        mongo.ping()

        # 1) Build set of sha256s that already have chunks.
        logger.info("Computing already-embedded sha256 set...")
        embedded_sha = set(
            mongo.chunks.distinct("sha256", {"source_type": "attachment"})
        )
        logger.info(f"  already embedded: {len(embedded_sha):,} unique attachments")

        # 2) Build target list: one attachment per missing sha256.
        #    Project only what we need (NO extracted_text) so the sort
        #    stays under the 32MB in-memory limit. We'll fetch text per
        #    doc later when actually embedding.
        logger.info("Finding attachments with extracted_text but no chunk...")
        pipeline = [
            {"$match": {"extracted_text": {"$type": "string", "$ne": ""}}},
            {"$project": {"_id": 1, "email_id": 1, "sha256": 1,
                          "size_bytes": 1}},
            {"$group": {
                "_id": "$sha256",
                "doc_id": {"$first": "$_id"},
                "email_id": {"$first": "$email_id"},
                "size_bytes": {"$max": "$size_bytes"},
            }},
            {"$sort": {"size_bytes": 1}},
        ]
        groups = list(mongo.attachments.aggregate(pipeline, allowDiskUse=True))
        missing = [g for g in groups if g["_id"] not in embedded_sha]
        logger.info(f"  total unique attachments w/ text: {len(groups):,}")
        logger.info(f"  MISSING (to embed now):           {len(missing):,}")

        if args.limit:
            missing = missing[: args.limit]
            logger.info(f"  --limit applied → processing {len(missing)}")

        if not missing:
            logger.info("Nothing to do. All attachments embedded already.")
            return 0

        # 3) Process missing in chunks; embed in batches; insert.
        t0 = time.time()
        n_done = 0
        n_chunks_inserted = 0
        n_skipped_no_email = 0
        n_skipped_no_chunks = 0

        pending_docs: List[Dict[str, Any]] = []

        def flush(final: bool = False) -> None:
            nonlocal pending_docs, n_chunks_inserted
            if not pending_docs:
                return
            while pending_docs and (final or len(pending_docs) >= args.batch_size):
                take = pending_docs if final else pending_docs[: args.batch_size]
                texts = [d["text"] for d in take]
                vecs = embedder.embed_documents(texts)
                ops = []
                now = datetime.now(timezone.utc)
                for d, v in zip(take, vecs):
                    d["embedding"] = v
                    d["created_at"] = now
                    ops.append(InsertOne(d))
                mongo.chunks.bulk_write(ops, ordered=False)
                n_chunks_inserted += len(ops)
                if final:
                    pending_docs = []
                else:
                    pending_docs = pending_docs[args.batch_size :]

        for grp in missing:
            sha = grp["_id"]
            email_id = grp.get("email_id")
            if not email_id:
                n_skipped_no_email += 1
                continue
            email = mongo.emails.find_one({"_id": email_id})
            if not email:
                n_skipped_no_email += 1
                continue
            attachment = mongo.attachments.find_one({"_id": grp["doc_id"]})
            if not attachment:
                continue

            atext = (attachment.get("extracted_text") or "").strip()
            if not atext:
                continue
            src_hash = _sha256_text(atext)

            # Page-aware chunking when per-page text exists.
            pages_meta = (attachment.get("extraction") or {}).get("pages") or []
            if pages_meta and any(p.get("text") for p in pages_meta):
                attachment_pages = [
                    {"page_no": p.get("page_no") or (i + 1),
                     "text": p.get("text") or ""}
                    for i, p in enumerate(pages_meta)
                ]
            else:
                attachment_pages = [{"page_no": 1, "text": atext}]

            att_meta = {
                "filename": attachment.get("filename"),
                "date": email.get("date"),
                "email_subject": email.get("subject"),
            }
            chunks = chunk_attachment(
                attachment_pages,
                attachment_meta=att_meta,
                chunk_size_tokens=s.chunk_size_tokens,
                chunk_overlap_tokens=s.chunk_overlap_tokens,
            )
            if not chunks:
                n_skipped_no_chunks += 1
                continue

            docs = _build_attachment_chunk_docs(email, attachment, chunks, src_hash)
            pending_docs.extend(docs)
            n_done += 1
            flush(final=False)

            if n_done % 25 == 0:
                el = time.time() - t0
                rate = n_done / el if el > 0 else 0
                eta_min = (len(missing) - n_done) / rate / 60 if rate > 0 else 0
                logger.info(
                    f"  [{n_done:>4}/{len(missing)}] "
                    f"chunks_inserted={n_chunks_inserted}  "
                    f"rate={rate:.2f} att/s  eta={eta_min:.1f} min"
                )
                gc.collect()

        flush(final=True)

        elapsed = time.time() - t0
        logger.info(
            f"DONE in {elapsed/60:.1f} min  "
            f"attachments processed: {n_done}  "
            f"chunks inserted: {n_chunks_inserted}  "
            f"skipped(no_email/no_chunks)={n_skipped_no_email}/{n_skipped_no_chunks}"
        )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
