"""
Build the RAG index: chunk every email body + attachment text and embed
each chunk with Voyage AI, then upsert into the `email_chunks` collection.

Pipeline (idempotent):
  1. For each email in `emails`:
       a. Chunk `body_text` with metadata header (sender/date/subject).
       b. For each attachment with `extracted_text`, page-aware chunk it.
  2. Skip emails that already have all-fresh chunks (matched by
     `source_hash`). Run with --force to re-embed everything.
  3. Embed chunks in batches with `input_type='document'`.
  4. Upsert into `email_chunks`. Each chunk doc has the shape:

       {
         email_id, attachment_id (optional), source_type,
         source_hash, chunk_index, page_start, page_end,
         text, body, n_tokens,
         date, date_ym, from_email, to_emails, subject, folder_path,
         filename (for attachments),
         embedding: [float * 1024],
         created_at,
       }

Why a separate collection?
  • Atlas Vector Search needs a fixed-shape `path` field. Putting embeddings
    on the email document would explode storage; plus an email may yield
    1-30 chunks.
  • Filters (date range, sender) live as plain fields on each chunk, so the
    Atlas $vectorSearch `filter` clause works with simple equality/range
    expressions and NEVER needs to chase a $lookup.

Idempotency contract:
  • A chunk's identity = (email_id OR attachment_id, chunk_index,
    source_hash). source_hash is sha256 of the cleaned source text.
  • If source_hash matches an existing chunk in the same slot, we skip
    embedding (cost saver). If the body changes (re-clean run), the hash
    changes and we re-embed.

Usage:
  python scripts/embed_corpus.py
  python scripts/embed_corpus.py --workers 4
  python scripts/embed_corpus.py --force
  python scripts/embed_corpus.py --limit 10        # smoke test
  python scripts/embed_corpus.py --emails-only     # skip attachments
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from pymongo import UpdateOne

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.chunker import chunk_attachment, chunk_email_body, Chunk
from src.rag.embedder import VoyageEmbedder
from src.utils.logger import configure_logger, logger


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _build_email_chunk_docs(email: Dict[str, Any], chunks: List[Chunk], source_hash: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    base = {
        "email_id": email["_id"],
        "source_type": "email_body",
        "source_hash": source_hash,
        "date": email.get("date"),
        "date_ym": email.get("date_ym"),
        "from_email": (email.get("from") or {}).get("email"),
        "to_emails": [t.get("email") for t in (email.get("to") or []) if t and t.get("email")],
        "subject": email.get("subject"),
        "folder_path": email.get("folder_path"),
        "internet_message_id": email.get("internet_message_id"),
    }
    for c in chunks:
        doc = dict(base)
        doc.update({
            "chunk_index": c.chunk_index,
            "text": c.text,
            "body": c.body,
            "n_tokens": c.n_tokens,
            "page_start": c.page_start,
            "page_end": c.page_end,
        })
        out.append(doc)
    return out


def _build_attachment_chunk_docs(
    email: Dict[str, Any],
    attachment: Dict[str, Any],
    chunks: List[Chunk],
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
        doc = dict(base)
        doc.update({
            "chunk_index": c.chunk_index,
            "text": c.text,
            "body": c.body,
            "n_tokens": c.n_tokens,
            "page_start": c.page_start,
            "page_end": c.page_end,
        })
        out.append(doc)
    return out


def _existing_hash_for(
    mongo: MongoClientWrapper,
    *,
    email_id: ObjectId,
    attachment_id: Optional[ObjectId] = None,
    source_type: str,
) -> Optional[str]:
    """Return the current source_hash of the chunks we have for this source, if any."""
    q: Dict[str, Any] = {
        "email_id": email_id,
        "source_type": source_type,
    }
    if attachment_id is not None:
        q["attachment_id"] = attachment_id
    sample = mongo.chunks.find_one(q, {"source_hash": 1})
    return sample.get("source_hash") if sample else None


def _replace_chunks(
    mongo: MongoClientWrapper,
    *,
    email_id: ObjectId,
    attachment_id: Optional[ObjectId],
    source_type: str,
    new_docs: List[Dict[str, Any]],
) -> None:
    """Atomically delete existing chunks for this source and insert new ones."""
    q: Dict[str, Any] = {"email_id": email_id, "source_type": source_type}
    if attachment_id is not None:
        q["attachment_id"] = attachment_id
    mongo.chunks.delete_many(q)
    if new_docs:
        mongo.chunks.insert_many(new_docs, ordered=False)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true", help="Re-embed even if hashes match")
    p.add_argument("--limit", type=int, default=0, help="Stop after N emails (smoke test)")
    p.add_argument("--emails-only", action="store_true", help="Skip attachments")
    p.add_argument("--attachments-only", action="store_true", help="Skip email bodies")
    p.add_argument("--batch-size", type=int, default=64, help="Embed batch size (default 64)")
    args = p.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    embedder = VoyageEmbedder(api_key=settings.voyage_api_key, model=settings.embedding_model)

    try:
        mongo.ping()
        mongo.ensure_indexes()

        total_emails = mongo.emails.count_documents({})
        logger.info(f"Total emails: {total_emails:,}")

        # Fetch all email _ids up front so we never hold a long-lived
        # MongoDB cursor (which would expire during the multi-minute
        # rate-limit pauses during embedding). We then page through the
        # ids list and run a fresh find() per email.
        id_cursor = mongo.emails.find({}, projection={"_id": 1}, sort=[("date", 1)])
        if args.limit:
            id_cursor = id_cursor.limit(args.limit)
        email_ids = [d["_id"] for d in id_cursor]
        logger.info(f"Loaded {len(email_ids):,} email ids to process")

        # Pending docs to embed across all sources.
        pending_docs: List[Dict[str, Any]] = []   # docs awaiting embedding
        # Track replacements so we can stage delete-then-insert atomically per source.
        pending_groups: List[Dict[str, Any]] = []  # [{"key":(email_id,att_id,src), "docs":[...]}, ...]

        n_seen = 0
        n_email_groups = 0
        n_att_groups = 0
        n_chunks_total = 0
        n_skipped_email = 0
        n_skipped_att = 0
        n_no_body = 0
        n_no_attext = 0
        t0 = time.time()

        def _flush(force_all: bool = False) -> None:
            nonlocal n_chunks_total, pending_docs, pending_groups
            if not pending_docs:
                return
            target = args.batch_size
            while pending_docs and (force_all or len(pending_docs) >= target):
                batch = pending_docs[:target] if not force_all else list(pending_docs)
                texts = [d["text"] for d in batch]
                vecs = embedder.embed_documents(texts)
                for d, v in zip(batch, vecs):
                    d["embedding"] = v
                    d["created_at"] = datetime.now(timezone.utc)
                if force_all:
                    pending_docs = []
                else:
                    pending_docs = pending_docs[target:]

                # Now apply group replacements that are fully embedded.
                # A group is "ready" when none of its docs are still in pending_docs.
                still_pending_ids = {id(d) for d in pending_docs}
                ready, not_ready = [], []
                for grp in pending_groups:
                    if any(id(d) in still_pending_ids for d in grp["docs"]):
                        not_ready.append(grp)
                    else:
                        ready.append(grp)
                pending_groups = not_ready

                for grp in ready:
                    email_id, attachment_id, src = grp["key"]
                    _replace_chunks(
                        mongo,
                        email_id=email_id,
                        attachment_id=attachment_id,
                        source_type=src,
                        new_docs=grp["docs"],
                    )
                    n_chunks_total += len(grp["docs"])

        for _eid in email_ids:
            email = mongo.emails.find_one({"_id": _eid})
            if email is None:
                continue
            n_seen += 1

            # ----- Email body -----
            if not args.attachments_only:
                body = (email.get("body_text") or "").strip()
                if body:
                    src_hash = _sha256_text(body)
                    existing = _existing_hash_for(
                        mongo, email_id=email["_id"], source_type="email_body"
                    )
                    if (existing == src_hash) and not args.force:
                        n_skipped_email += 1
                    else:
                        chunks = chunk_email_body(
                            body,
                            email_meta=email,
                            chunk_size_tokens=settings.chunk_size_tokens,
                            chunk_overlap_tokens=settings.chunk_overlap_tokens,
                        )
                        if chunks:
                            docs = _build_email_chunk_docs(email, chunks, src_hash)
                            pending_docs.extend(docs)
                            pending_groups.append({
                                "key": (email["_id"], None, "email_body"),
                                "docs": docs,
                            })
                            n_email_groups += 1
                else:
                    n_no_body += 1

            # ----- Attachments -----
            if not args.emails_only:
                att_ids = email.get("attachment_ids") or []
                if att_ids:
                    atts = list(mongo.attachments.find(
                        {"_id": {"$in": att_ids}},
                        {"_id": 1, "filename": 1, "extension": 1, "sha256": 1,
                         "extracted_text": 1, "extraction": 1},
                    ))
                    for att in atts:
                        atext = (att.get("extracted_text") or "").strip()
                        if not atext:
                            n_no_attext += 1
                            continue
                        src_hash = _sha256_text(atext)
                        existing = _existing_hash_for(
                            mongo,
                            email_id=email["_id"],
                            attachment_id=att["_id"],
                            source_type="attachment",
                        )
                        if (existing == src_hash) and not args.force:
                            n_skipped_att += 1
                            continue

                        # Page-aware chunking: use per-page text if the
                        # extractor saved it; otherwise fall back to a
                        # single page containing the full extracted_text.
                        pages_meta = (att.get("extraction") or {}).get("pages") or []
                        if pages_meta and any(p.get("text") for p in pages_meta):
                            attachment_pages = [
                                {"page_no": p.get("page_no") or i + 1,
                                 "text": p.get("text") or ""}
                                for i, p in enumerate(pages_meta)
                            ]
                        else:
                            attachment_pages = [{"page_no": 1, "text": atext}]

                        att_meta = {
                            "filename": att.get("filename"),
                            "date": email.get("date"),
                            "email_subject": email.get("subject"),
                        }
                        chunks = chunk_attachment(
                            attachment_pages,
                            attachment_meta=att_meta,
                            chunk_size_tokens=settings.chunk_size_tokens,
                            chunk_overlap_tokens=settings.chunk_overlap_tokens,
                        )
                        if chunks:
                            docs = _build_attachment_chunk_docs(email, att, chunks, src_hash)
                            pending_docs.extend(docs)
                            pending_groups.append({
                                "key": (email["_id"], att["_id"], "attachment"),
                                "docs": docs,
                            })
                            n_att_groups += 1

            _flush(force_all=False)

            if n_seen % 50 == 0:
                elapsed = time.time() - t0
                rate = n_seen / elapsed if elapsed > 0 else 0
                logger.info(
                    f"  [{n_seen:>5}/{total_emails}] "
                    f"email_groups={n_email_groups}  att_groups={n_att_groups}  "
                    f"chunks={n_chunks_total}  "
                    f"skipped(email/att)={n_skipped_email}/{n_skipped_att}  "
                    f"rate={rate:.2f} emails/s"
                )

        # Final flush
        _flush(force_all=True)

        elapsed = time.time() - t0
        logger.info(
            f"Done in {elapsed/60:.1f} min — emails seen: {n_seen}, "
            f"chunks indexed: {n_chunks_total} "
            f"(email groups: {n_email_groups}, attachment groups: {n_att_groups}). "
            f"Skipped same-hash → email: {n_skipped_email}, att: {n_skipped_att}. "
            f"No body: {n_no_body}, no attachment text: {n_no_attext}."
        )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
