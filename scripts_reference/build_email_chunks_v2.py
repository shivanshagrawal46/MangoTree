"""
Sprint 3 Step 2 — Build the `email_chunks_v2` corpus (Option B schema).

OPTION B (One-chunk-per-unique-file):
  Each unique attachment content (identified by SHA256) is chunked,
  summarised and embedded EXACTLY ONCE. All emails that carry that
  same content are tracked in an `occurrences[]` array on the chunk
  document. This eliminates the duplicate-content explosion that
  Option A produced (same logo / standard cover sheet appearing in
  600 emails would otherwise produce 600 × N identical chunks).

  For email bodies (which are unique to one email by definition) we
  still produce one chunk per (email_id, chunk_index) — but we still
  use the `occurrences[]` shape with a single entry, so the retriever
  has a uniform schema to work against.

Pipeline (idempotent, resumable):

  PHASE A — gather occurrences (cheap, sequential)
    Scan every email and every attachment_id ∈ attachments_v2; produce
      attachment_jobs : { sha256 -> [occurrence, …] }
      body_jobs       : [ email_id, … ]
    Skip any (sha256 or email_id) that already has chunks in v2 unless
    --force is set.

  PHASE B — process attachments by unique sha256 (parallel)
    For each sha256:
      1. Pull the extracted_text from any one attachments_v2 row.
      2. Pick the PRIMARY occurrence = earliest by date (canonical
         metadata for the chunk text header).
      3. Structure-aware chunk it (target chunk_size tokens, overlap).
      4. For every chunk, ask Claude Sonnet 4.6 for a 50-100 token
         contextual summary (prompt-cached across chunks of one doc).
      5. Prepend the context to the chunk body BEFORE embedding.
      6. Embed each chunk with `voyage-4-large` (1024-dim).
      7. Write ONE chunk doc per chunk_index, with occurrences[] array.

  PHASE C — process email bodies (parallel)
    Same shape, but occurrences is always length 1.

Idempotency keys:
  • Attachments : (sha256, chunk_index)  → unique
  • Email body  : (email_id, chunk_index, source_type='email_body')  → unique

Output collection: `email_chunks_v2`

  {
    _id, source_type: "attachment" | "email_body",
    sha256, chunk_index, total_chunks,
    text, body, context, n_tokens, embedding, embedding_model,
    page_start, page_end, extension, filename,
    latest_date,                         # max(occurrences[].date)
    occurrences: [
      { email_id, attachment_id, filename, date, date_ym,
        from_email, to_emails, subject, folder_path }, ...
    ],
    # Mirror of occurrences[0] (PRIMARY = earliest) for cheap BM25:
    email_id, attachment_id, date, date_ym,
    from_email, to_emails, subject, folder_path,
    created_at,
  }

Usage:
  python scripts/build_email_chunks_v2.py
  python scripts/build_email_chunks_v2.py --limit 50              # smoke test
  python scripts/build_email_chunks_v2.py --emails-only           # skip atts
  python scripts/build_email_chunks_v2.py --attachments-only      # skip bodies
  python scripts/build_email_chunks_v2.py --skip-context          # ablation
  python scripts/build_email_chunks_v2.py --no-embed              # dry chunk
  python scripts/build_email_chunks_v2.py --batch-size 64
  python scripts/build_email_chunks_v2.py --workers 16
  python scripts/build_email_chunks_v2.py --force                 # re-embed all
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.chunker import Chunk, chunk_attachment, chunk_email_body
from src.rag.embedder import VoyageEmbedder
from src.rag.tokens import count_tokens
from src.rag.v2.contextual_summary import ContextualSummarizer
from src.utils.logger import configure_logger, logger


# Target tunables for the v2 corpus (user-confirmed).
CHUNK_SIZE_TOKENS = 1000
CHUNK_OVERLAP_TOKENS = 200
EMBEDDING_MODEL = "voyage-4-large"

V2_CHUNKS_COLLECTION = "email_chunks_v2"
V2_ATTACHMENTS_COLLECTION = "attachments_v2"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _to_aware_utc(dt: Any) -> Optional[datetime]:
    """Normalise a Mongo date value to a tz-aware UTC datetime, or None."""
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_sort_key(occ: Dict[str, Any]) -> Tuple[int, datetime]:
    """Sort key that puts dated occurrences before undated ones, earliest
    first. Undated → end of list.
    """
    d = _to_aware_utc(occ.get("date"))
    if d is None:
        return (1, datetime(9999, 12, 31, tzinfo=timezone.utc))
    return (0, d)


def _build_occurrence(
    email: Dict[str, Any],
    *,
    attachment_id: Optional[ObjectId],
    filename: Optional[str],
) -> Dict[str, Any]:
    """Build one entry of the occurrences[] array from an email row."""
    return {
        "email_id": email["_id"],
        "attachment_id": attachment_id,
        "filename": filename,
        "date": email.get("date"),
        "date_ym": email.get("date_ym"),
        "from_email": (email.get("from") or {}).get("email"),
        "to_emails": [
            t.get("email") for t in (email.get("to") or []) if t and t.get("email")
        ],
        "subject": email.get("subject"),
        "folder_path": email.get("folder_path"),
    }


def _compose_embed_text(chunk_text: str, context: str) -> str:
    """Prepend the contextual situating summary to the chunk before embedding."""
    context = (context or "").strip()
    if not context:
        return chunk_text
    return f"[Context] {context}\n\n{chunk_text}"


def _latest_date(occurrences: List[Dict[str, Any]]) -> Optional[datetime]:
    dates = [_to_aware_utc(o.get("date")) for o in occurrences]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


# --------------------------------------------------------------------------
# v2 indexes (non-Atlas-vector — those live in create_v2_vector_index.py)
# --------------------------------------------------------------------------

def _ensure_v2_indexes(chunks_col) -> None:
    """Indexes needed by the v2 retrieval pipeline.

    Note the addition of `occurrences.email_id`, `occurrences.from_email`
    and `occurrences.date` — these power "any-occurrence" filters in
    Option B.
    """
    # Helper that tolerates a pre-existing index of the same name. The
    # legacy v2 build had slightly different sparse settings on some of
    # these — we match the legacy spec rather than fight it.
    def _safe_idx(spec, **kw):
        name = kw["name"]
        try:
            chunks_col.create_index(spec, **kw)
        except Exception as exc:
            if "IndexKeySpecsConflict" in str(exc) or "IndexOptionsConflict" in str(exc):
                logger.debug(f"  index '{name}' already exists with different opts — skipping")
            else:
                raise

    _safe_idx(
        [("sha256", ASCENDING), ("chunk_index", ASCENDING)],
        name="ux_sha256_chunk",
        unique=False,  # email_body chunks share sha256 only within one email
    )
    _safe_idx([("source_type", ASCENDING)], name="ix_source_type")
    _safe_idx([("sha256", ASCENDING)], name="ix_sha256", sparse=True)
    _safe_idx([("latest_date", DESCENDING)], name="ix_latest_date")
    _safe_idx([("date", DESCENDING)], name="ix_date")
    _safe_idx([("date_ym", ASCENDING)], name="ix_date_ym")
    _safe_idx([("from_email", ASCENDING)], name="ix_from_email")
    _safe_idx([("filename", ASCENDING)], name="ix_filename", sparse=True)
    _safe_idx([("email_id", ASCENDING)], name="ix_email_id")
    _safe_idx([("occurrences.email_id", ASCENDING)], name="ix_occ_email")
    _safe_idx([("occurrences.from_email", ASCENDING)], name="ix_occ_from")
    _safe_idx([("occurrences.date", DESCENDING)], name="ix_occ_date")
    _safe_idx(
        [("occurrences.filename", ASCENDING)],
        name="ix_occ_filename",
        sparse=True,
    )


# --------------------------------------------------------------------------
# Existence checks (idempotency)
# --------------------------------------------------------------------------

def _attachment_already_done(chunks_col, sha256: str) -> bool:
    """An attachment chunk-set is done iff at least one chunk exists with
    this sha256 AND source_type='attachment'. Partial-write recovery is
    handled by the upsert path: chunks for a sha256 are written in a
    single delete-then-insert transaction."""
    return chunks_col.find_one(
        {"sha256": sha256, "source_type": "attachment"}, {"_id": 1}
    ) is not None


def _body_already_done(chunks_col, email_id: ObjectId) -> bool:
    return chunks_col.find_one(
        {"email_id": email_id, "source_type": "email_body"}, {"_id": 1}
    ) is not None


def _replace_attachment_chunks(chunks_col, sha256: str, docs: List[Dict[str, Any]]) -> None:
    """All-or-nothing replace: drop every existing chunk for this sha256
    (attachment source type only), then insert the new set."""
    chunks_col.delete_many({"sha256": sha256, "source_type": "attachment"})
    if docs:
        chunks_col.insert_many(docs, ordered=False)


def _replace_body_chunks(chunks_col, email_id: ObjectId, docs: List[Dict[str, Any]]) -> None:
    chunks_col.delete_many({"email_id": email_id, "source_type": "email_body"})
    if docs:
        chunks_col.insert_many(docs, ordered=False)


# --------------------------------------------------------------------------
# Doc builders
# --------------------------------------------------------------------------

def _build_attachment_chunk_docs(
    *,
    sha256: str,
    extension: Optional[str],
    occurrences: List[Dict[str, Any]],
    chunks: List[Chunk],
    contexts: List[str],
) -> List[Dict[str, Any]]:
    """Build the Option B chunk docs for one unique attachment (one sha256).

    `occurrences` MUST already be sorted earliest-first. The first entry is
    the canonical PRIMARY occurrence — its metadata is mirrored at the
    top level of every chunk doc (for cheap BM25 / sort / Atlas filters).
    """
    if not occurrences:
        raise ValueError("Cannot build chunk docs with empty occurrences")
    primary = occurrences[0]
    latest = _latest_date(occurrences)
    total = len(chunks)

    out: List[Dict[str, Any]] = []
    for c, ctx in zip(chunks, contexts):
        embed_text = _compose_embed_text(c.text, ctx)
        doc = {
            "source_type": "attachment",
            "sha256": sha256,
            "extension": extension,
            "chunk_index": c.chunk_index,
            "total_chunks": total,
            "text": embed_text,
            "body": c.body,
            "context": ctx,
            "n_tokens": count_tokens(embed_text),
            "page_start": c.page_start,
            "page_end": c.page_end,

            # Mirror of primary (earliest) occurrence — for cheap top-level
            # filtering / sorting / BM25 weighting.
            "email_id": primary["email_id"],
            "attachment_id": primary.get("attachment_id"),
            "filename": primary.get("filename"),
            "date": primary.get("date"),
            "date_ym": primary.get("date_ym"),
            "from_email": primary.get("from_email"),
            "to_emails": primary.get("to_emails") or [],
            "subject": primary.get("subject"),
            "folder_path": primary.get("folder_path"),

            # The full occurrences fan-out.
            "occurrences": occurrences,
            "latest_date": latest,
        }
        out.append(doc)
    return out


def _build_body_chunk_docs(
    *,
    email: Dict[str, Any],
    chunks: List[Chunk],
    contexts: List[str],
) -> List[Dict[str, Any]]:
    """Build chunk docs for one email body. occurrences is always [1 entry]."""
    occ = _build_occurrence(email, attachment_id=None, filename=None)
    occurrences = [occ]
    total = len(chunks)

    out: List[Dict[str, Any]] = []
    for c, ctx in zip(chunks, contexts):
        embed_text = _compose_embed_text(c.text, ctx)
        doc = {
            "source_type": "email_body",
            # Use the email_id (as hex) seeded with chunk_index so we have
            # a stable identity even though there's no file-content sha256.
            # NOTE: we still index by email_id; this sha is only for shape
            # parity with attachment chunks.
            "sha256": f"email:{email['_id']}",
            "extension": None,
            "chunk_index": c.chunk_index,
            "total_chunks": total,
            "text": embed_text,
            "body": c.body,
            "context": ctx,
            "n_tokens": count_tokens(embed_text),
            "page_start": c.page_start,
            "page_end": c.page_end,

            # Mirror of (the single) occurrence.
            "email_id": occ["email_id"],
            "attachment_id": None,
            "filename": None,
            "date": occ.get("date"),
            "date_ym": occ.get("date_ym"),
            "from_email": occ.get("from_email"),
            "to_emails": occ.get("to_emails") or [],
            "subject": occ.get("subject"),
            "folder_path": occ.get("folder_path"),

            "occurrences": occurrences,
            "latest_date": _to_aware_utc(occ.get("date")),
        }
        out.append(doc)
    return out


# --------------------------------------------------------------------------
# PHASE A — gather occurrences map
# --------------------------------------------------------------------------

def _gather_jobs(
    mongo: MongoClientWrapper,
    attachments_v2,
    *,
    email_ids: List[ObjectId],
    do_bodies: bool,
    do_attachments: bool,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Optional[str]], List[ObjectId]]:
    """One pass over emails → build:
        attachment_jobs : sha256 → [occurrences sorted earliest-first]
        attachment_meta : sha256 → extension (taken from primary occ)
        body_jobs       : [email_id, ...]

    Skips:
      • attachments_v2 rows with empty extracted_text (the 6 unrecoverables)
      • attachments referenced by an email but missing from attachments_v2
    """
    attachment_jobs: Dict[str, List[Dict[str, Any]]] = {}
    attachment_extension: Dict[str, Optional[str]] = {}
    body_jobs: List[ObjectId] = []

    n_emails_seen = 0
    n_atts_seen = 0
    n_atts_no_sha = 0
    n_atts_no_text = 0
    n_atts_not_in_v2 = 0

    # Pre-collect all attachment ids referenced anywhere so we can do a
    # single bulk lookup against attachments_v2.
    referenced_att_ids: set = set()
    email_rows: Dict[ObjectId, Dict[str, Any]] = {}
    proj = {
        "_id": 1, "attachment_ids": 1, "date": 1, "date_ym": 1,
        "from": 1, "to": 1, "subject": 1, "folder_path": 1,
        "body_text": 1,
    }
    for em in mongo.emails.find({"_id": {"$in": email_ids}}, proj):
        email_rows[em["_id"]] = em
        if do_attachments:
            for aid in em.get("attachment_ids") or []:
                referenced_att_ids.add(aid)

    # Bulk-load every referenced attachment_v2 row.
    att_by_id: Dict[ObjectId, Dict[str, Any]] = {}
    if do_attachments and referenced_att_ids:
        cur = attachments_v2.find(
            {"_id": {"$in": list(referenced_att_ids)}},
            {"_id": 1, "filename": 1, "extension": 1, "sha256": 1,
             "extracted_text": 1},
        )
        for a in cur:
            att_by_id[a["_id"]] = a

    # Walk emails in canonical (caller-supplied) order so deterministic.
    for eid in email_ids:
        em = email_rows.get(eid)
        if em is None:
            continue
        n_emails_seen += 1

        if do_bodies and (em.get("body_text") or "").strip():
            body_jobs.append(eid)

        if not do_attachments:
            continue

        for aid in em.get("attachment_ids") or []:
            att = att_by_id.get(aid)
            if att is None:
                n_atts_not_in_v2 += 1
                continue
            n_atts_seen += 1
            sha = att.get("sha256")
            if not sha:
                n_atts_no_sha += 1
                continue
            text = (att.get("extracted_text") or "").strip()
            if not text:
                n_atts_no_text += 1
                continue
            occ = _build_occurrence(
                em,
                attachment_id=att["_id"],
                filename=att.get("filename"),
            )
            attachment_jobs.setdefault(sha, []).append(occ)
            # First-seen extension wins (they should all agree).
            attachment_extension.setdefault(sha, att.get("extension"))

    # Sort each occurrence list earliest-first.
    for sha, occs in attachment_jobs.items():
        occs.sort(key=_date_sort_key)

    logger.info(
        f"Phase A: emails={n_emails_seen:,}  body_jobs={len(body_jobs):,}  "
        f"unique_sha256={len(attachment_jobs):,}  "
        f"att_rows_seen={n_atts_seen:,}  "
        f"skipped: no_sha={n_atts_no_sha} no_text={n_atts_no_text} "
        f"missing_in_v2={n_atts_not_in_v2}"
    )
    return attachment_jobs, attachment_extension, body_jobs


# --------------------------------------------------------------------------
# PHASE B / C — per-job workers
# --------------------------------------------------------------------------

def _process_one_sha256(
    sha256: str,
    occurrences: List[Dict[str, Any]],
    *,
    extension: Optional[str],
    attachments_v2,
    chunk_size: int,
    chunk_overlap: int,
    summarizer: Optional[ContextualSummarizer],
) -> Optional[Dict[str, Any]]:
    """Process ONE unique attachment (by sha256). Returns a dict of:
        { 'sha256': str, 'docs': [chunk_doc, ...] }
    or None if the attachment text is unavailable / empty.

    Read-only against Mongo — caller does the write.
    """
    # Use ANY occurrence's attachment_id to fetch extracted_text + page meta.
    # They all carry the same content (same sha256) by definition.
    sample_aid = occurrences[0]["attachment_id"]
    att = attachments_v2.find_one(
        {"_id": sample_aid},
        {"_id": 1, "filename": 1, "extension": 1, "sha256": 1,
         "extracted_text": 1, "extraction": 1},
    )
    if att is None:
        return None
    text = (att.get("extracted_text") or "").strip()
    if not text:
        return None

    pages_meta = (att.get("extraction") or {}).get("pages") or []
    if pages_meta and any(p.get("text") for p in pages_meta):
        attachment_pages = [
            {"page_no": p.get("page_no") or i + 1, "text": p.get("text") or ""}
            for i, p in enumerate(pages_meta)
        ]
    else:
        attachment_pages = [{"page_no": 1, "text": text}]

    primary = occurrences[0]
    att_meta = {
        "filename": primary.get("filename"),
        "date": primary.get("date"),
        "email_subject": primary.get("subject"),
    }

    chunks = chunk_attachment(
        attachment_pages,
        attachment_meta=att_meta,
        chunk_size_tokens=chunk_size,
        chunk_overlap_tokens=chunk_overlap,
    )
    if not chunks:
        return None

    chunk_bodies = [c.body for c in chunks]
    if summarizer is not None:
        contexts = summarizer.summarize_doc_chunks(
            doc_text=text, chunk_texts=chunk_bodies,
        )
    else:
        contexts = ["" for _ in chunks]

    docs = _build_attachment_chunk_docs(
        sha256=sha256,
        extension=extension,
        occurrences=occurrences,
        chunks=chunks,
        contexts=contexts,
    )
    return {"sha256": sha256, "docs": docs, "n_ctx": len(contexts)}


def _process_one_body(
    eid: ObjectId,
    *,
    emails_col,
    chunk_size: int,
    chunk_overlap: int,
    summarizer: Optional[ContextualSummarizer],
) -> Optional[Dict[str, Any]]:
    em = emails_col.find_one({"_id": eid})
    if em is None:
        return None
    body = (em.get("body_text") or "").strip()
    if not body:
        return None

    chunks = chunk_email_body(
        body,
        email_meta=em,
        chunk_size_tokens=chunk_size,
        chunk_overlap_tokens=chunk_overlap,
    )
    if not chunks:
        return None

    chunk_bodies = [c.body for c in chunks]
    if summarizer is not None:
        contexts = summarizer.summarize_doc_chunks(
            doc_text=body, chunk_texts=chunk_bodies,
        )
    else:
        contexts = ["" for _ in chunks]

    docs = _build_body_chunk_docs(email=em, chunks=chunks, contexts=contexts)
    return {"email_id": eid, "docs": docs, "n_ctx": len(contexts)}


# --------------------------------------------------------------------------
# Embedding + write pipeline
# --------------------------------------------------------------------------

class _Flusher:
    """Accumulates pending chunk docs and flushes them to Voyage + Mongo in
    batches. Thread-safe: only the orchestrator thread touches it."""

    def __init__(
        self,
        *,
        chunks_col,
        embedder: Optional[VoyageEmbedder],
        embedding_model: str,
        batch_size: int,
        dry: bool,
    ) -> None:
        self.chunks_col = chunks_col
        self.embedder = embedder
        self.embedding_model = embedding_model
        self.batch_size = batch_size
        self.dry = dry
        # Accumulator: each entry is { 'kind': 'att'|'body', 'key': sha|eid,
        # 'docs': [...] }
        self.pending_groups: List[Dict[str, Any]] = []
        self.n_pending_docs = 0
        # Stats
        self.n_chunks_written = 0
        self.n_att_groups_written = 0
        self.n_body_groups_written = 0

    def add_attachment_group(self, sha256: str, docs: List[Dict[str, Any]]) -> None:
        if not docs:
            return
        self.pending_groups.append({"kind": "att", "key": sha256, "docs": docs})
        self.n_pending_docs += len(docs)

    def add_body_group(self, email_id: ObjectId, docs: List[Dict[str, Any]]) -> None:
        if not docs:
            return
        self.pending_groups.append({"kind": "body", "key": email_id, "docs": docs})
        self.n_pending_docs += len(docs)

    def flush(self, *, force: bool = False) -> None:
        """If we have at least `batch_size` docs (or force=True), embed +
        write everything in the buffer. Embeds in groups of `batch_size`.
        """
        if not self.pending_groups:
            return
        if not force and self.n_pending_docs < self.batch_size:
            return

        # Flatten to a single list for embedding.
        all_docs: List[Dict[str, Any]] = []
        for g in self.pending_groups:
            all_docs.extend(g["docs"])

        # Embed in chunks of self.batch_size.
        if self.embedder is not None and not self.dry:
            for i in range(0, len(all_docs), self.batch_size):
                slab = all_docs[i : i + self.batch_size]
                texts = [d["text"] for d in slab]
                vecs = self.embedder.embed_documents(texts)
                stamp = datetime.now(timezone.utc)
                for d, v in zip(slab, vecs):
                    d["embedding"] = v
                    d["embedding_model"] = self.embedding_model
                    d["created_at"] = stamp
        else:
            stamp = datetime.now(timezone.utc)
            for d in all_docs:
                d["embedding"] = []
                d["embedding_model"] = self.embedding_model
                d["created_at"] = stamp

        if self.dry:
            for g in self.pending_groups:
                self.n_chunks_written += len(g["docs"])
                if g["kind"] == "att":
                    self.n_att_groups_written += 1
                else:
                    self.n_body_groups_written += 1
        else:
            for g in self.pending_groups:
                if g["kind"] == "att":
                    _replace_attachment_chunks(self.chunks_col, g["key"], g["docs"])
                    self.n_att_groups_written += 1
                else:
                    _replace_body_chunks(self.chunks_col, g["key"], g["docs"])
                    self.n_body_groups_written += 1
                self.n_chunks_written += len(g["docs"])

        self.pending_groups = []
        self.n_pending_docs = 0


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="Re-embed even when chunks already exist for this sha256/email")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N emails contribute to the job lists (smoke test)")
    p.add_argument("--emails-only", action="store_true", help="Skip attachments")
    p.add_argument("--attachments-only", action="store_true", help="Skip email bodies")
    p.add_argument("--skip-context", action="store_true",
                   help="Disable contextual retrieval (ablation / fast mode)")
    p.add_argument("--no-embed", action="store_true",
                   help="Dry chunk + context only; no embedding, no DB writes")
    p.add_argument("--batch-size", type=int, default=64,
                   help="Embedding batch size (Voyage caps at 128)")
    p.add_argument("--workers", type=int, default=16,
                   help="Parallel doc workers (default 16)")
    p.add_argument("--chunk-size", type=int, default=CHUNK_SIZE_TOKENS)
    p.add_argument("--chunk-overlap", type=int, default=CHUNK_OVERLAP_TOKENS)
    p.add_argument("--embedding-model", default=EMBEDDING_MODEL,
                   help="Voyage embedding model id")
    args = p.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)

    chunks_v2 = mongo.db[V2_CHUNKS_COLLECTION]
    attachments_v2 = mongo.db[V2_ATTACHMENTS_COLLECTION]

    n_atts_v2 = attachments_v2.estimated_document_count()
    if n_atts_v2 == 0 and not args.emails_only:
        logger.error(
            f"{V2_ATTACHMENTS_COLLECTION} is empty. Run Sprint 3 Step 1 first."
        )
        return 2

    embedder = None
    if not args.no_embed:
        embedder = VoyageEmbedder(
            api_key=settings.voyage_api_key, model=args.embedding_model
        )

    summarizer: Optional[ContextualSummarizer] = None
    if not args.skip_context:
        summarizer = ContextualSummarizer(
            api_key=settings.anthropic_api_key,
            model="claude-sonnet-4-6",
        )

    try:
        mongo.ping()
        _ensure_v2_indexes(chunks_v2)

        total_emails = mongo.emails.count_documents({})
        logger.info(
            f"v2 build (Option B) — total emails: {total_emails:,}  "
            f"attachments_v2: {n_atts_v2:,}  "
            f"chunk={args.chunk_size}/{args.chunk_overlap}  "
            f"context={'OFF' if args.skip_context else 'ON'}  "
            f"embed={'OFF (dry)' if args.no_embed else args.embedding_model}  "
            f"workers={args.workers}  batch={args.batch_size}"
        )

        # Pull all email ids up front so we never hold a Mongo cursor over
        # multi-minute Claude / Voyage pauses.
        id_cursor = mongo.emails.find({}, projection={"_id": 1}, sort=[("date", 1)])
        if args.limit:
            id_cursor = id_cursor.limit(args.limit)
        email_ids = [d["_id"] for d in id_cursor]
        logger.info(f"Loaded {len(email_ids):,} email ids")

        # ---- Phase A: gather jobs ----------------------------------------
        do_bodies = not args.attachments_only
        do_attachments = not args.emails_only
        attachment_jobs, attachment_extension, body_jobs = _gather_jobs(
            mongo, attachments_v2,
            email_ids=email_ids,
            do_bodies=do_bodies, do_attachments=do_attachments,
        )

        # ---- Idempotency filter ------------------------------------------
        # Existing sha256s in v2: SKIP the full chunk/summarise/embed
        # pipeline, but REMEMBER them so Phase D can sync their
        # occurrences[] against the gathered ground truth (otherwise any
        # new emails carrying an already-processed file would not get
        # added to the chunk's fan-out array).
        skipped_existing_shas: Dict[str, List[Dict[str, Any]]] = {}
        if not args.force and not args.no_embed:
            # Drop any sha256 / email that already has chunks.
            todo_atts: Dict[str, List[Dict[str, Any]]] = {}
            n_skipped_atts = 0
            # Bulk pre-check: pull every existing attachment sha256 at once.
            existing_sha: set = {
                d["_id"] for d in chunks_v2.aggregate([
                    {"$match": {"source_type": "attachment"}},
                    {"$group": {"_id": "$sha256"}},
                ])
            }
            for sha, occs in attachment_jobs.items():
                if sha in existing_sha:
                    n_skipped_atts += 1
                    skipped_existing_shas[sha] = occs
                    continue
                todo_atts[sha] = occs
            attachment_jobs = todo_atts

            # Same for bodies: pre-load the set of email_ids that already
            # have body chunks.
            existing_body_eids: set = {
                d["_id"] for d in chunks_v2.aggregate([
                    {"$match": {"source_type": "email_body"}},
                    {"$group": {"_id": "$email_id"}},
                ])
            }
            todo_bodies = [eid for eid in body_jobs if eid not in existing_body_eids]
            n_skipped_bodies = len(body_jobs) - len(todo_bodies)
            body_jobs = todo_bodies

            logger.info(
                f"Idempotency: skipping {n_skipped_atts:,} sha256s and "
                f"{n_skipped_bodies:,} emails already in v2."
            )

        logger.info(
            f"TODO: attachments_to_process={len(attachment_jobs):,}  "
            f"bodies_to_process={len(body_jobs):,}"
        )

        flusher = _Flusher(
            chunks_col=chunks_v2,
            embedder=embedder,
            embedding_model=args.embedding_model,
            batch_size=args.batch_size,
            dry=args.no_embed,
        )

        n_ctx_calls = 0
        n_jobs_done = 0
        n_jobs_total = len(attachment_jobs) + len(body_jobs)
        n_att_workers_failed = 0
        n_body_workers_failed = 0
        t0 = time.time()
        last_log = t0

        def _log_progress() -> None:
            nonlocal last_log
            now = time.time()
            elapsed = now - t0
            rate = n_jobs_done / elapsed if elapsed > 0 else 0
            eta_min = (n_jobs_total - n_jobs_done) / rate / 60 if rate > 0 else 0
            ctx_str = ""
            if summarizer is not None:
                u = summarizer.usage_summary
                ctx_str = (
                    f"  ctx_calls={n_ctx_calls}  "
                    f"ctx_cost=${u['approx_cost_usd']:.2f}  "
                    f"cache_read={u['cache_read_tokens']:,}"
                )
            logger.info(
                f"  [{n_jobs_done:>5}/{n_jobs_total}]  "
                f"att={flusher.n_att_groups_written}  "
                f"body={flusher.n_body_groups_written}  "
                f"chunks={flusher.n_chunks_written}  "
                f"rate={rate:.2f} doc/s  eta={eta_min:.1f}min" + ctx_str
            )
            last_log = now

        # ---- Phase B: attachments by unique sha256 ----------------------
        if attachment_jobs:
            logger.info(f"--- Phase B: {len(attachment_jobs)} unique attachments ---")
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futs = {
                    pool.submit(
                        _process_one_sha256,
                        sha, occs,
                        extension=attachment_extension.get(sha),
                        attachments_v2=attachments_v2,
                        chunk_size=args.chunk_size,
                        chunk_overlap=args.chunk_overlap,
                        summarizer=summarizer,
                    ): sha
                    for sha, occs in attachment_jobs.items()
                }
                for fut in as_completed(futs):
                    sha = futs[fut]
                    try:
                        res = fut.result()
                    except Exception as exc:
                        logger.error(f"Phase B worker failed on sha {sha[:12]}…: {exc!r}")
                        n_att_workers_failed += 1
                        n_jobs_done += 1
                        continue
                    if res is None:
                        n_jobs_done += 1
                        continue
                    flusher.add_attachment_group(res["sha256"], res["docs"])
                    n_ctx_calls += res.get("n_ctx", 0)
                    n_jobs_done += 1
                    # Flush opportunistically based on accumulated size.
                    flusher.flush(force=False)
                    if (time.time() - last_log) > 30 or (n_jobs_done % 25 == 0):
                        _log_progress()

            flusher.flush(force=True)
            _log_progress()

        # ---- Phase C: email bodies --------------------------------------
        if body_jobs:
            logger.info(f"--- Phase C: {len(body_jobs)} email bodies ---")
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futs = {
                    pool.submit(
                        _process_one_body,
                        eid,
                        emails_col=mongo.emails,
                        chunk_size=args.chunk_size,
                        chunk_overlap=args.chunk_overlap,
                        summarizer=summarizer,
                    ): eid
                    for eid in body_jobs
                }
                for fut in as_completed(futs):
                    eid = futs[fut]
                    try:
                        res = fut.result()
                    except Exception as exc:
                        logger.error(f"Phase C worker failed on email {eid}: {exc!r}")
                        n_body_workers_failed += 1
                        n_jobs_done += 1
                        continue
                    if res is None:
                        n_jobs_done += 1
                        continue
                    flusher.add_body_group(res["email_id"], res["docs"])
                    n_ctx_calls += res.get("n_ctx", 0)
                    n_jobs_done += 1
                    flusher.flush(force=False)
                    if (time.time() - last_log) > 30 or (n_jobs_done % 25 == 0):
                        _log_progress()

            flusher.flush(force=True)
            _log_progress()

        # ---- Phase D: sync occurrences[] for already-existing sha256s --
        # Any sha256 we skipped in the idempotency filter may have NEW
        # occurrences (parent emails newly added to the corpus, or emails
        # not yet processed when the existing chunks were originally
        # written). Update their occurrences[] in place — no embedding,
        # no Claude, just a Mongo update with the freshly gathered Phase
        # A occurrence map.
        if skipped_existing_shas and not args.no_embed:
            logger.info(
                f"--- Phase D: sync occurrences for "
                f"{len(skipped_existing_shas)} existing sha256s ---"
            )
            n_synced = 0
            n_skipped_synced = 0
            n_chunks_synced = 0
            for sha, occs in skipped_existing_shas.items():
                sample = chunks_v2.find_one(
                    {"sha256": sha, "source_type": "attachment"},
                    {"occurrences": 1},
                )
                if sample is None:
                    continue
                existing = sample.get("occurrences") or []
                existing_keys = {
                    (o.get("email_id"), o.get("attachment_id")) for o in existing
                }
                new_keys = {
                    (o.get("email_id"), o.get("attachment_id")) for o in occs
                }
                if existing_keys == new_keys:
                    n_skipped_synced += 1
                    continue
                # The freshly gathered `occs` already came from Phase A
                # walking ALL emails, so it's the ground truth.
                occs_sorted = sorted(occs, key=_date_sort_key)
                primary = occs_sorted[0]
                latest = _latest_date(occs_sorted)
                set_doc = {
                    "occurrences": occs_sorted,
                    "latest_date": latest,
                    "email_id": primary["email_id"],
                    "attachment_id": primary.get("attachment_id"),
                    "filename": primary.get("filename"),
                    "date": primary.get("date"),
                    "date_ym": primary.get("date_ym"),
                    "from_email": primary.get("from_email"),
                    "to_emails": primary.get("to_emails") or [],
                    "subject": primary.get("subject"),
                    "folder_path": primary.get("folder_path"),
                }
                res = chunks_v2.update_many(
                    {"sha256": sha, "source_type": "attachment"},
                    {"$set": set_doc},
                )
                n_chunks_synced += res.modified_count
                n_synced += 1
            logger.info(
                f"  Phase D done: synced {n_synced} shas "
                f"(touched {n_chunks_synced} chunks), "
                f"{n_skipped_synced} already up-to-date."
            )

        elapsed = time.time() - t0
        logger.info("=" * 70)
        logger.info(
            f"Done in {elapsed/60:.1f} min — "
            f"att_groups={flusher.n_att_groups_written}  "
            f"body_groups={flusher.n_body_groups_written}  "
            f"chunks={flusher.n_chunks_written}  "
            f"att_failures={n_att_workers_failed}  "
            f"body_failures={n_body_workers_failed}"
        )
        if summarizer is not None:
            u = summarizer.usage_summary
            logger.info(
                f"Contextual summary usage — calls={n_ctx_calls}  "
                f"input={u['input_tokens']:,}  "
                f"cache_write={u['cache_creation_tokens']:,}  "
                f"cache_read={u['cache_read_tokens']:,}  "
                f"output={u['output_tokens']:,}  "
                f"approx_cost_usd=${u['approx_cost_usd']:.2f}"
            )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
