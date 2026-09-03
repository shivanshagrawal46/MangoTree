"""
Collapse the legacy `email_chunks_v2` documents into Option B shape.

WHAT THIS DOES
==============
Before:   one chunk per (email_id, attachment_id, chunk_index)
After:    one chunk per (sha256, chunk_index) with an occurrences[] array
          listing every (email_id, attachment_id, filename, date, ...)
          parent that carries the byte-identical content.

For each (sha256, chunk_index) group:
  1. Pick the EARLIEST-dated chunk as the survivor (keeps its existing
     _id and embedding — both are valid for the canonical content).
  2. Build the `occurrences[]` array by extracting per-email metadata
     from every chunk in the group, sorted earliest-first.
  3. Update the survivor: add occurrences[], add latest_date, set the
     top-level mirror fields (email_id / date / from_email / ...) to
     the FIRST (earliest) occurrence so BM25 and sort have a sane
     scalar to work against.
  4. Delete every other chunk in the group.

For email-body chunks (one chunk per (email_id, chunk_index), no
sha256 fan-out), we just wrap each existing chunk in occurrences=[1]
and add latest_date — no merging needed.

This script is SAFE TO RUN MULTIPLE TIMES — it detects already-
collapsed chunks (occurrences field present) and skips them.

Usage:
  python scripts/collapse_v2_optionb.py            # full pass
  python scripts/collapse_v2_optionb.py --dry-run  # report only, no writes
  python scripts/collapse_v2_optionb.py --limit 100  # process N sha256s
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from pymongo import UpdateOne, UpdateMany, DeleteMany

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger


V2_CHUNKS_COLLECTION = "email_chunks_v2"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _to_aware_utc(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_sort_key(d: Dict[str, Any]) -> Tuple[int, datetime]:
    dt = _to_aware_utc(d.get("date"))
    if dt is None:
        return (1, datetime(9999, 12, 31, tzinfo=timezone.utc))
    return (0, dt)


def _build_occurrence_from_chunk(c: Dict[str, Any]) -> Dict[str, Any]:
    """Pull occurrence fields out of a legacy chunk doc."""
    return {
        "email_id": c.get("email_id"),
        "attachment_id": c.get("attachment_id"),
        "filename": c.get("filename"),
        "date": c.get("date"),
        "date_ym": c.get("date_ym"),
        "from_email": c.get("from_email"),
        "to_emails": c.get("to_emails") or [],
        "subject": c.get("subject"),
        "folder_path": c.get("folder_path"),
    }


def _occurrence_key(occ: Dict[str, Any]) -> Tuple[Any, Any]:
    """Stable dedup key for occurrences — (email_id, attachment_id)."""
    return (occ.get("email_id"), occ.get("attachment_id"))


def _latest_date(occurrences: List[Dict[str, Any]]) -> Optional[datetime]:
    dates = [_to_aware_utc(o.get("date")) for o in occurrences]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute changes but do not write")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N attachment-sha256 groups (smoke test)")
    args = ap.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    chunks = mongo.db[V2_CHUNKS_COLLECTION]

    try:
        mongo.ping()
        total = chunks.estimated_document_count()
        logger.info(f"email_chunks_v2 holds ~{total:,} docs")

        # ---- Phase 1: attachment chunks -------------------------------
        # Group by (sha256, chunk_index). Skip docs that already have
        # occurrences[] (idempotent re-runs).
        logger.info("Phase 1: collapsing attachment chunks by (sha256, chunk_index)")

        # Pull all attachment chunks WITHOUT occurrences (so re-runs are
        # cheap). Project only the fields we need.
        proj = {
            "_id": 1, "sha256": 1, "chunk_index": 1,
            "email_id": 1, "attachment_id": 1, "filename": 1,
            "date": 1, "date_ym": 1, "from_email": 1, "to_emails": 1,
            "subject": 1, "folder_path": 1,
        }
        att_q = {"source_type": "attachment", "occurrences": {"$exists": False}}
        n_att_legacy = chunks.count_documents(att_q)
        logger.info(f"  legacy attachment chunks needing collapse: {n_att_legacy:,}")

        # Group in memory: { (sha256, chunk_index) : [chunk_doc, ...] }
        groups: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        t0 = time.time()
        for c in chunks.find(att_q, proj):
            sha = c.get("sha256")
            ci = c.get("chunk_index")
            if not sha or ci is None:
                continue
            groups.setdefault((sha, ci), []).append(c)
        logger.info(f"  built {len(groups):,} (sha,chunk_idx) groups "
                    f"in {time.time()-t0:.1f}s")

        # Count distinct sha256s.
        distinct_shas = {sha for (sha, _) in groups.keys()}
        logger.info(f"  distinct sha256 attachments: {len(distinct_shas):,}")

        if args.limit and args.limit < len(distinct_shas):
            keep_shas = set(list(distinct_shas)[: args.limit])
            groups = {k: v for k, v in groups.items() if k[0] in keep_shas}
            logger.info(f"  --limit: kept {len(groups):,} groups for "
                        f"{len(keep_shas):,} sha256s")

        # ---- Process groups ------------------------------------------
        n_collapsed = 0
        n_singletons = 0
        n_chunks_to_delete = 0
        ops: List[Any] = []
        # Batch size for bulk_write — Mongo limit is 1000, we go 500.
        BULK = 500

        def _flush_ops():
            nonlocal ops
            if not ops:
                return
            if args.dry_run:
                ops = []
                return
            chunks.bulk_write(ops, ordered=False)
            ops = []

        for (sha, ci), members in groups.items():
            members_sorted = sorted(members, key=_date_sort_key)
            survivor = members_sorted[0]

            # Build the occurrences[] (dedup by (email_id, attachment_id)).
            seen_keys: set = set()
            occurrences: List[Dict[str, Any]] = []
            for m in members_sorted:
                occ = _build_occurrence_from_chunk(m)
                k = _occurrence_key(occ)
                if k in seen_keys:
                    continue
                seen_keys.add(k)
                occurrences.append(occ)

            latest = _latest_date(occurrences)
            primary = occurrences[0]

            set_doc: Dict[str, Any] = {
                "occurrences": occurrences,
                "latest_date": latest,
                "total_chunks": None,  # filled below
                # Mirror to primary (earliest) occurrence so BM25/sort
                # have a sane scalar even when the survivor's own
                # top-level metadata was from a non-primary email.
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

            ops.append(UpdateOne({"_id": survivor["_id"]}, {"$set": set_doc}))

            if len(members_sorted) > 1:
                dead_ids = [m["_id"] for m in members_sorted[1:]]
                ops.append(DeleteMany({"_id": {"$in": dead_ids}}))
                n_chunks_to_delete += len(dead_ids)
                n_collapsed += 1
            else:
                n_singletons += 1

            if len(ops) >= BULK:
                _flush_ops()

        _flush_ops()

        logger.info(
            f"  Phase 1 done: {n_collapsed:,} collapsed groups "
            f"(deleted {n_chunks_to_delete:,} dup chunks), "
            f"{n_singletons:,} singletons just got occurrences[]."
        )

        # ---- Phase 1b: backfill total_chunks --------------------------
        # `total_chunks` should equal len(distinct chunk_index for this sha).
        # We compute it now in a single aggregate per sha to avoid an N+1.
        if not args.dry_run:
            logger.info("  Phase 1b: backfilling total_chunks per sha256")
            t1 = time.time()
            # For every (sha256, source_type=attachment), count chunks.
            pipe = [
                {"$match": {"source_type": "attachment", "sha256": {"$ne": None}}},
                {"$group": {"_id": "$sha256", "n": {"$sum": 1}}},
            ]
            count_by_sha = {d["_id"]: d["n"] for d in chunks.aggregate(pipe)}
            ops = []
            for sha, n in count_by_sha.items():
                # UpdateMany — every chunk for this sha256 gets the same
                # total_chunks value. Using UpdateOne here was the bug
                # that left 4,926 chunks without total_chunks.
                ops.append(UpdateMany(
                    {"sha256": sha, "source_type": "attachment"},
                    {"$set": {"total_chunks": n}},
                ))
                if len(ops) >= BULK:
                    chunks.bulk_write(ops, ordered=False)
                    ops = []
            if ops:
                chunks.bulk_write(ops, ordered=False)
            logger.info(f"  total_chunks backfilled in {time.time()-t1:.1f}s")

        # ---- Phase 2: body chunks -------------------------------------
        logger.info("Phase 2: wrapping email-body chunks into occurrences[]")
        body_q = {"source_type": "email_body", "occurrences": {"$exists": False}}
        n_body_legacy = chunks.count_documents(body_q)
        logger.info(f"  legacy body chunks needing wrap: {n_body_legacy:,}")

        ops = []
        n_body_done = 0
        # Stream — each body chunk is independent, no merging needed.
        for c in chunks.find(body_q, proj):
            occ = _build_occurrence_from_chunk(c)
            occurrences = [occ]
            latest = _to_aware_utc(occ.get("date"))
            ops.append(UpdateOne({"_id": c["_id"]}, {"$set": {
                "occurrences": occurrences,
                "latest_date": latest,
                # If sha256 is missing for some legacy body chunks, give
                # them a deterministic id derived from email_id.
                **(
                    {"sha256": f"email:{c['email_id']}"}
                    if not c.get("sha256") else {}
                ),
            }}))
            n_body_done += 1
            if len(ops) >= BULK:
                _flush_ops()

        _flush_ops()
        logger.info(f"  Phase 2 done: wrapped {n_body_done:,} body chunks")

        # ---- Phase 2b: body total_chunks ------------------------------
        if not args.dry_run:
            t2 = time.time()
            pipe = [
                {"$match": {"source_type": "email_body"}},
                {"$group": {"_id": "$email_id", "n": {"$sum": 1}}},
            ]
            count_by_eid = {d["_id"]: d["n"] for d in chunks.aggregate(pipe)}
            ops = []
            for eid, n in count_by_eid.items():
                ops.append(UpdateMany(
                    {"email_id": eid, "source_type": "email_body"},
                    {"$set": {"total_chunks": n}},
                ))
                if len(ops) >= BULK:
                    chunks.bulk_write(ops, ordered=False)
                    ops = []
            if ops:
                chunks.bulk_write(ops, ordered=False)
            logger.info(f"  body total_chunks backfilled in {time.time()-t2:.1f}s")

        # ---- Final integrity report -----------------------------------
        final_total = chunks.estimated_document_count()
        n_with_occ = chunks.count_documents({"occurrences": {"$exists": True}})
        n_missing_occ = chunks.count_documents({"occurrences": {"$exists": False}})
        logger.info("=" * 70)
        logger.info(
            f"FINAL: total={final_total:,}  "
            f"with_occurrences={n_with_occ:,}  "
            f"missing_occurrences={n_missing_occ:,}"
        )
        if n_missing_occ > 0:
            logger.warning(
                f"  → {n_missing_occ:,} chunks still missing occurrences. "
                "These are likely orphaned legacy rows (no sha256 or "
                "chunk_index). Inspect with `find({occurrences: {$exists: false}})`."
            )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
