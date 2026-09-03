"""
Sync the `occurrences[]` array on every attachment chunk in
`email_chunks_v2` against the GROUND TRUTH in `emails` + `attachments_v2`.

WHY THIS IS NEEDED
==================
The Option B collapse + idempotent-resume strategy has one corner case:
when a sha256 is ALREADY in the v2 chunks (e.g. it was processed before
the build was interrupted, then collapsed into Option B shape), the
resumed build SKIPS it under the idempotency check — so any NEW emails
that also carry that same byte-identical file never get appended to the
chunk's `occurrences[]`. The text/embedding/context are fine (the
content is identical), but the fan-out is incomplete.

This script reconciles that. For each unique sha256:

  1. Look up every attachments_v2 row with that sha256.
  2. Look up every email that references any of those attachments.
  3. Build the COMPLETE occurrences[] from those emails.
  4. If different from what's stored, update every chunk for that sha256
     in place (occurrences[], latest_date, total_chunks, and the mirror
     fields email_id/date/from_email/filename/subject/folder_path that
     reflect the PRIMARY [earliest] occurrence).

No Claude or Voyage calls. Pure Mongo. Idempotent — safe to run multiple
times. Can run while the build is running because it touches disjoint
sha256 sets (the build is creating NEW ones; this updates EXISTING ones).

Usage:
  python scripts/sync_occurrences_v2.py
  python scripts/sync_occurrences_v2.py --dry-run    # report only
  python scripts/sync_occurrences_v2.py --limit 50   # process first N shas
"""
from __future__ import annotations
import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from pymongo import UpdateMany

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger


V2_CHUNKS_COLLECTION = "email_chunks_v2"
V2_ATTACHMENTS_COLLECTION = "attachments_v2"


def _to_aware_utc(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _date_sort_key(occ: Dict[str, Any]) -> Tuple[int, datetime]:
    d = _to_aware_utc(occ.get("date"))
    if d is None:
        return (1, datetime(9999, 12, 31, tzinfo=timezone.utc))
    return (0, d)


def _build_occurrence(email: Dict[str, Any], *,
                       attachment_id: Optional[ObjectId],
                       filename: Optional[str]) -> Dict[str, Any]:
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


def _occ_key(occ: Dict[str, Any]) -> Tuple[Any, Any]:
    return (occ.get("email_id"), occ.get("attachment_id"))


def _latest_date(occurrences: List[Dict[str, Any]]) -> Optional[datetime]:
    dates = [_to_aware_utc(o.get("date")) for o in occurrences]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


def _occurrences_equal(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> bool:
    """Order-insensitive equality on the (email_id, attachment_id) keys."""
    if len(a) != len(b):
        return False
    keys_a = {_occ_key(o) for o in a}
    keys_b = {_occ_key(o) for o in b}
    return keys_a == keys_b


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute changes but do not write")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N sha256s (smoke test)")
    args = ap.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    chunks = mongo.db[V2_CHUNKS_COLLECTION]
    atts = mongo.db[V2_ATTACHMENTS_COLLECTION]
    emails = mongo.emails

    try:
        mongo.ping()

        # ---- 1. Enumerate every distinct sha256 in v2 attachment chunks
        logger.info("Step 1: enumerating distinct sha256 in v2 attachment chunks")
        t0 = time.time()
        distinct_shas: List[str] = [
            d["_id"] for d in chunks.aggregate([
                {"$match": {"source_type": "attachment", "sha256": {"$ne": None}}},
                {"$group": {"_id": "$sha256"}},
            ])
        ]
        logger.info(f"  found {len(distinct_shas):,} distinct sha256 "
                    f"in {time.time()-t0:.1f}s")

        if args.limit and args.limit < len(distinct_shas):
            distinct_shas = distinct_shas[: args.limit]
            logger.info(f"  --limit: capped to {len(distinct_shas)}")

        # ---- 2. Build a (sha256, attachment_id, filename) map ---------
        logger.info("Step 2: building sha → attachment_id map from attachments_v2")
        t0 = time.time()
        # Map: sha256 → list of (attachment_id, filename)
        sha_to_atts: Dict[str, List[Tuple[ObjectId, Optional[str]]]] = {}
        # Reverse map: attachment_id → sha256 (for fast lookup in step 3).
        aid_to_sha: Dict[ObjectId, str] = {}
        for a in atts.find(
            {"sha256": {"$in": distinct_shas}},
            {"_id": 1, "sha256": 1, "filename": 1},
        ):
            sha = a.get("sha256")
            if not sha:
                continue
            sha_to_atts.setdefault(sha, []).append((a["_id"], a.get("filename")))
            aid_to_sha[a["_id"]] = sha
        logger.info(
            f"  mapped {len(sha_to_atts):,} sha256s → {len(aid_to_sha):,} attachment rows "
            f"in {time.time()-t0:.1f}s"
        )

        # ---- 3. ONE pass over emails — build sha → occurrences[] map ---
        logger.info("Step 3: single-pass emails → occurrences map")
        t0 = time.time()
        sha_to_occs: Dict[str, List[Dict[str, Any]]] = {sha: [] for sha in distinct_shas}
        # Map per-sha-per-att filename so each occurrence carries the
        # filename as it appears in attachments_v2 (not the email).
        att_filename_by_id: Dict[ObjectId, Optional[str]] = {
            aid: fn for sha in distinct_shas
            for aid, fn in sha_to_atts.get(sha, [])
        }
        all_aids = list(aid_to_sha.keys())
        # Find every email referencing ANY of those attachment_ids in a
        # single query. Use a projection to keep memory light.
        cursor = emails.find(
            {"attachment_ids": {"$in": all_aids}},
            {
                "_id": 1, "date": 1, "date_ym": 1, "from": 1, "to": 1,
                "subject": 1, "folder_path": 1, "attachment_ids": 1,
            },
        )
        n_emails_seen = 0
        for em in cursor:
            n_emails_seen += 1
            for aid in em.get("attachment_ids") or []:
                sha = aid_to_sha.get(aid)
                if sha is None:
                    continue
                sha_to_occs[sha].append(_build_occurrence(
                    em,
                    attachment_id=aid,
                    filename=att_filename_by_id.get(aid),
                ))
        logger.info(
            f"  walked {n_emails_seen:,} emails  in {time.time()-t0:.1f}s"
        )

        # ---- 4. For each sha, compare with stored & update if different
        logger.info("Step 4: comparing stored occurrences to ground truth")
        t_start = time.time()
        last_log = t_start
        n_updated = 0
        n_unchanged = 0
        n_no_atts = 0
        n_chunks_touched = 0

        for i, sha in enumerate(distinct_shas, start=1):
            occs = sha_to_occs.get(sha) or []

            # Dedup defensively on (email_id, attachment_id)
            seen: set = set()
            uniq: List[Dict[str, Any]] = []
            for o in occs:
                k = _occ_key(o)
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(o)
            occurrences = sorted(uniq, key=_date_sort_key)
            if not occurrences:
                n_no_atts += 1
                continue

            sample = chunks.find_one(
                {"sha256": sha, "source_type": "attachment"},
                {"occurrences": 1},
            )
            if sample is None:
                continue
            existing_occs = sample.get("occurrences") or []
            if _occurrences_equal(existing_occs, occurrences):
                n_unchanged += 1
                continue

            primary = occurrences[0]
            latest = _latest_date(occurrences)
            total_chunks = chunks.count_documents(
                {"sha256": sha, "source_type": "attachment"}
            )
            set_doc = {
                "occurrences": occurrences,
                "latest_date": latest,
                "total_chunks": total_chunks,
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
            if not args.dry_run:
                res = chunks.update_many(
                    {"sha256": sha, "source_type": "attachment"},
                    {"$set": set_doc},
                )
                n_chunks_touched += res.modified_count
            n_updated += 1

            now = time.time()
            if (now - last_log) > 5 or (i % 200 == 0):
                rate = i / (now - t_start) if now > t_start else 0
                eta = (len(distinct_shas) - i) / rate if rate > 0 else 0
                logger.info(
                    f"  [{i:>4}/{len(distinct_shas)}]  "
                    f"updated={n_updated} unchanged={n_unchanged}  "
                    f"chunks_touched={n_chunks_touched}  "
                    f"rate={rate:.1f} sha/s  eta={eta:.0f}s"
                )
                last_log = now

        logger.info("=" * 70)
        logger.info(
            f"DONE in {time.time()-t_start:.1f}s — "
            f"sha256_scanned={len(distinct_shas):,}  "
            f"updated={n_updated:,}  unchanged={n_unchanged:,}  "
            f"chunks_touched={n_chunks_touched:,}  "
            f"orphan_shas={n_no_atts:,}"
            + ("  (dry-run)" if args.dry_run else "")
        )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
