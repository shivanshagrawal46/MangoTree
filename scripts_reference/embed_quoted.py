"""
Embed NOVEL quoted passages into email_chunks_v2 (Sprint 2 wiring).

STRICTLY reuses the production pipeline:
  * chunking      -> src.rag.chunker._chunk_text via chunk_email_body core
  * context       -> src.rag.v2.contextual_summary.ContextualSummarizer
  * embed compose -> build_email_chunks_v2._compose_embed_text  ([Context] ..)
  * embeddings    -> src.rag.embedder.VoyageEmbedder (voyage-4-large, 1024d)
  * collection    -> email_chunks_v2

Only difference vs a normal email-body chunk: the in-text header explicitly
marks the passage as QUOTED HISTORY (not the email's own body), and the doc
carries quoted-specific provenance so retrieval + citations are correct.

SAFETY:
  * Default is DRY-RUN: builds + prints, embeds nothing, writes nothing.
  * --apply performs the paid build + insert, in batches, each doc stamped
    source_batch="quoted_recovery_v1" and source_type="email_quoted".
  * --undo removes EXACTLY this batch: delete_many({source_batch:...}).
  * Idempotent: _id key = (sha256="quoted:<fingerprint>", chunk_index).
  * Purely additive: never touches existing chunks.

Usage:
  python scripts/embed_quoted.py                 # dry-run (safe, free)
  python scripts/embed_quoted.py --limit 20      # dry-run small sample
  python scripts/embed_quoted.py --apply         # REAL: contextual + embed + insert
  python scripts/embed_quoted.py --undo          # remove the batch
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rapidfuzz import process, fuzz

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.chunker import chunk_email_body
from src.rag.tokens import count_tokens
from src.cleaner.quoted_text import (
    split_quoted_tail, iter_quoted_segments, normalize_quote, quote_fingerprint,
)
# Reuse the SAME body-cleaning / boilerplate logic the analyzer used.
from scripts.analyze_quoted import (
    clean_segment, is_boilerplate, MIN_BODY_CHARS, DUP_AT, EDIT_AT, MAXLEN,
)
from scripts.build_email_chunks_v2 import _compose_embed_text

SOURCE_BATCH = "quoted_recovery_v1"
SOURCE_TYPE = "email_quoted"
AUTHORITY = 0.75
CHUNK_SIZE_TOKENS = 1000
CHUNK_OVERLAP_TOKENS = 200
COLLECTION = "email_chunks_v2"

# ---- quoted-header metadata recovery -------------------------------------
_H_FROM = re.compile(r"\**from\**\s*:\s*(.+)", re.IGNORECASE)
_H_TO = re.compile(r"\**to\**\s*:\s*(.+)", re.IGNORECASE)
_H_SUBJ = re.compile(r"\**subject\**\s*:\s*(.+)", re.IGNORECASE)
_H_DATE = re.compile(r"\**(?:sent|date)\**\s*:\s*(.+)", re.IGNORECASE)
_ON_WROTE = re.compile(r"On\s+(.{3,80}?)\s+(.{1,120}?)\s+wrote:", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _clean_val(v: str) -> str:
    return re.sub(r"[<>\*\[\]]", "", v).strip().strip("|").strip()


def recover_original_meta(seg: str) -> Dict[str, Any]:
    """Parse the quoted message's own header to recover who/when originally."""
    meta: Dict[str, Any] = {"from": None, "to": None, "subject": None, "date_text": None}
    head = "\n".join(seg.splitlines()[:8])
    m = _H_FROM.search(head)
    if m:
        emails = _EMAIL_RE.findall(m.group(1))
        meta["from"] = emails[0] if emails else _clean_val(m.group(1))[:120]
    m = _H_TO.search(head)
    if m:
        emails = _EMAIL_RE.findall(m.group(1))
        meta["to"] = emails or [_clean_val(m.group(1))[:120]]
    m = _H_SUBJ.search(head)
    if m:
        meta["subject"] = _clean_val(m.group(1))[:200]
    m = _H_DATE.search(head)
    if m:
        meta["date_text"] = _clean_val(m.group(1))[:80]
    if not meta["from"]:
        mw = _ON_WROTE.search(seg[:300])
        if mw:
            emails = _EMAIL_RE.findall(mw.group(2))
            meta["from"] = emails[0] if emails else _clean_val(mw.group(2))[:120]
            meta["date_text"] = _clean_val(mw.group(1))[:80]
    return meta


def _quoted_header(orig: Dict[str, Any], found_email: Dict[str, Any]) -> str:
    """The in-text header. Makes it UNMISTAKABLE that this is quoted history,
    not the containing email's own body — so the model never misattributes."""
    o_from = orig.get("from") or "unknown sender"
    o_date = orig.get("date_text") or "undated"
    o_subj = orig.get("subject") or (found_email.get("subject") or "")
    found_from = (found_email.get("from") or {}).get("email") or "unknown"
    found_date = found_email.get("date")
    try:
        found_date_s = found_date.strftime("%Y-%m-%d")
    except AttributeError:
        found_date_s = str(found_date or "")
    return (
        f"[QUOTED MESSAGE - original from {o_from} ({o_date}); subject: {o_subj} "
        f"| RECOVERED from the quoted thread inside an email forwarded/sent by "
        f"{found_from} on {found_date_s} | This is QUOTED HISTORY, NOT the "
        f"sender's own message body.]"
    )


def gather_novel(db, limit: int = 0) -> List[Dict[str, Any]]:
    """Reproduce the analyzer's NOVEL classification and return, for each
    unique novel passage, its cleaned body + recovered meta + the emails it
    was found in (occurrences)."""
    emails = db["emails"]
    docs = list(emails.find({}, {"body_text": 1, "body_text_raw": 1, "from": 1,
                                 "to": 1, "subject": 1, "date": 1, "date_ym": 1,
                                 "folder_path": 1, "privilege_status": 1, "corpus": 1}))
    originals, orig_ids = [], []
    known = set()
    for d in docs:
        bt = (d.get("body_text") or "").strip()
        if bt:
            norm = normalize_quote(bt)[:MAXLEN]
            if len(norm) >= 40:
                originals.append(norm)
                orig_ids.append(str(d["_id"]))
                known.add(quote_fingerprint(bt))

    novel: Dict[str, Dict[str, Any]] = {}   # fingerprint -> record
    for d in docs:
        raw = d.get("body_text_raw") or d.get("body_text") or ""
        _, tail = split_quoted_tail(raw)
        if not tail.strip():
            continue
        for seg in iter_quoted_segments(tail):
            cleaned = clean_segment(seg)
            norm = normalize_quote(cleaned)[:MAXLEN]
            if len(norm) < MIN_BODY_CHARS or is_boilerplate(norm):
                continue
            fp = quote_fingerprint(cleaned)
            if fp in known:
                continue
            best = process.extractOne(norm, originals, scorer=fuzz.ratio, score_cutoff=EDIT_AT)
            if best is not None:   # duplicate or edited -> not novel
                continue
            rec = novel.get(fp)
            occ = _found_occurrence(d)
            if rec is None:
                novel[fp] = {
                    "fingerprint": fp,
                    "body": cleaned,
                    "orig_meta": recover_original_meta(seg),
                    "occurrences": [occ],
                    "primary_found": d,
                }
                if limit and len(novel) >= limit:
                    break
            else:
                rec["occurrences"].append(occ)
        if limit and len(novel) >= limit:
            break
    return list(novel.values())


def _found_occurrence(email: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "email_id": email["_id"],
        "date": email.get("date"),
        "date_ym": email.get("date_ym"),
        "from_email": (email.get("from") or {}).get("email"),
        "subject": email.get("subject"),
        "folder_path": email.get("folder_path"),
    }


def build_docs(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chunk each novel passage using the SAME chunker, with the quoted header.
    Embedding/context are added later (apply mode)."""
    out: List[Dict[str, Any]] = []
    for rec in records:
        found = rec["primary_found"]
        header = _quoted_header(rec["orig_meta"], found)
        # Reuse the production email-body chunker for identical splitting,
        # then swap its [Email — ..] header for the QUOTED header.
        chunks = chunk_email_body(
            rec["body"], email_meta={"subject": found.get("subject")},
            chunk_size_tokens=CHUNK_SIZE_TOKENS,
            chunk_overlap_tokens=CHUNK_OVERLAP_TOKENS,
        )
        for c in chunks:
            text_with_qhdr = f"{header}\n\n{c.body}"
            out.append({
                "_prelim": True,
                "source_type": SOURCE_TYPE,
                "source_batch": SOURCE_BATCH,
                "authority": AUTHORITY,
                "sha256": f"quoted:{rec['fingerprint']}",
                "chunk_index": c.chunk_index,
                "total_chunks": len(chunks),
                "text": text_with_qhdr,          # header + body (context prepended in apply)
                "body": c.body,
                "n_tokens": count_tokens(text_with_qhdr),
                "quoted_original": rec["orig_meta"],
                "found_in_email_id": found["_id"],
                "occurrences": rec["occurrences"],
                # primary mirror for BM25 / filters (dated by FOUND email;
                # original date is text-only until parsed to a real date)
                "email_id": found["_id"],
                "date": found.get("date"),
                "latest_date": found.get("date"),
                "date_ym": found.get("date_ym"),
                "from_email": (found.get("from") or {}).get("email"),
                "subject": found.get("subject"),
                "folder_path": found.get("folder_path"),
                # inherit privilege/corpus from the containing email (safe)
                "privilege_status": found.get("privilege_status") or "privileged",
                "corpus": found.get("corpus"),
                "embedding_model": "voyage-4-large",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="REAL run (paid: context+embed+insert)")
    ap.add_argument("--undo", action="store_true", help="delete the quoted_recovery_v1 batch")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6,
                    help="parallel workers for contextual summaries")
    args = ap.parse_args()

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db = m.db
    col = db[COLLECTION]

    if args.undo:
        n = col.count_documents({"source_batch": SOURCE_BATCH})
        res = col.delete_many({"source_batch": SOURCE_BATCH})
        print(f"UNDO: removed {res.deleted_count:,} chunks (was {n:,}).")
        m.close()
        return 0

    print("Gathering NOVEL quoted passages (read-only)...")
    records = gather_novel(db, limit=args.limit)
    docs = build_docs(records)
    print(f"  unique novel passages: {len(records):,}")
    print(f"  chunks to create.....: {len(docs):,}")

    # Show a representative doc so linkage + quoted-marking is auditable.
    if docs:
        d0 = docs[0]
        print("\n--- SAMPLE CHUNK (as it would be stored) ---")
        print("  _id key   :", d0["sha256"], "| chunk_index", d0["chunk_index"])
        print("  source    :", d0["source_type"], "| authority", d0["authority"])
        print("  found_in  :", d0["found_in_email_id"])
        print("  occurrences:", len(d0["occurrences"]))
        print("  quoted_original:", d0["quoted_original"])
        print("  TEXT (head):")
        print("   ", d0["text"][:400].replace("\n", "\n    "))

    if not args.apply:
        est_ctx = len(docs)
        est_emb = len(docs)
        print("\nDRY-RUN complete. NOTHING written, NOTHING embedded.")
        print(f"  On --apply: ~{est_ctx:,} contextual-summary calls (Sonnet) + "
              f"~{est_emb:,} embeddings (Voyage).")
        print("  Re-run with --apply to build for real (batched, reversible with --undo).")
        m.close()
        return 0

    # ---- APPLY: contextual summary (parallel) -> embed (batched) -> bulk write
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pymongo import ReplaceOne
    from src.rag.embedder import VoyageEmbedder
    from src.rag.v2.contextual_summary import ContextualSummarizer

    workers = max(1, args.workers)
    summarizer = ContextualSummarizer(api_key=s.anthropic_api_key, model="claude-sonnet-4-6")
    embedder = VoyageEmbedder(api_key=s.voyage_api_key, model="voyage-4-large")

    # Group chunks by passage (sha) so context uses the whole passage as doc.
    by_sha: Dict[str, List[Dict[str, Any]]] = {}
    for d in docs:
        by_sha.setdefault(d["sha256"], []).append(d)
    groups = list(by_sha.values())
    for g in groups:
        g.sort(key=lambda x: x["chunk_index"])

    # Phase 1 — contextual summaries in parallel (the bottleneck).
    print(f"Phase 1/3: contextual summaries ({len(groups):,} passages, {workers} workers)...")

    def _gen_ctx(group: List[Dict[str, Any]]) -> List[str]:
        doc_text = "\n\n".join(g["body"] for g in group)
        try:
            return summarizer.summarize_doc_chunks(doc_text, [g["text"] for g in group])
        except Exception:  # noqa: BLE001 — empty context fallback (matches builder)
            return ["" for _ in group]

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_gen_ctx, grp): grp for grp in groups}
        for fut in as_completed(futs):
            grp = futs[fut]
            for g, ctx in zip(grp, fut.result()):
                g["context"] = ctx
            done += 1
            if done % 250 == 0:
                print(f"    ...contexts {done:,}/{len(groups):,}")

    # Phase 2 — embeddings, batched internally by the embedder.
    all_chunks = [g for grp in groups for g in grp]
    print(f"Phase 2/3: embedding {len(all_chunks):,} chunks (voyage-4-large)...")
    embed_texts = [_compose_embed_text(g["text"], g.get("context", "")) for g in all_chunks]
    vectors = embedder.embed_documents(embed_texts)

    # Phase 3 — finalize + bulk upsert (batches of 500).
    print("Phase 3/3: writing to email_chunks_v2 (bulk upsert)...")
    now = datetime.now(timezone.utc)
    ops: List[Any] = []
    written = 0
    for g, et, emb in zip(all_chunks, embed_texts, vectors):
        g.pop("_prelim", None)
        g["text"] = et
        g["embedding"] = emb
        g["created_at"] = now
        ops.append(ReplaceOne(
            {"sha256": g["sha256"], "chunk_index": g["chunk_index"], "source_type": SOURCE_TYPE},
            g, upsert=True))
        if len(ops) >= 500:
            col.bulk_write(ops, ordered=False)
            written += len(ops); ops = []
            print(f"    ...written {written:,}/{len(all_chunks):,}")
    if ops:
        col.bulk_write(ops, ordered=False)
        written += len(ops)

    final = col.count_documents({"source_batch": SOURCE_BATCH})
    print(f"\nAPPLY complete: {written:,} quoted chunks written; "
          f"collection now holds {final:,} in batch '{SOURCE_BATCH}'.")
    print("  Undo anytime with: python scripts/embed_quoted.py --undo")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
