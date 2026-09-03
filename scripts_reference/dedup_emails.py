"""
Find and remove duplicate emails.

Two emails are considered duplicates if their `content_hash` matches.
`content_hash` is computed at ingestion time from:
    sender + recipients + normalized subject + sent date + first 5K of body

For each group of duplicates we KEEP the "best" copy and DELETE the rest:
    1. Most attachments wins
    2. Tie -> oldest `ingested_at` wins
    3. Tie -> _id order

Deleted emails take their attachments + GridFS files with them so storage
is reclaimed. The action is logged in `dedup_runs` collection.

Usage:
    python scripts/dedup_emails.py              # report only
    python scripts/dedup_emails.py --apply      # actually delete duplicates
    python scripts/dedup_emails.py --apply --strategy=internet_message_id
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from tqdm import tqdm

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger


def _group_emails(mongo: MongoClientWrapper, key_field: str) -> dict:
    """Group email _ids by {key_field}. Skips docs where key is null/empty."""
    groups: dict[str, list[dict]] = defaultdict(list)
    cursor = mongo.emails.find(
        {key_field: {"$nin": [None, ""]}},
        projection={
            "_id": 1, key_field: 1, "attachment_count": 1,
            "ingested_at": 1, "subject": 1, "date": 1, "from": 1,
        },
        batch_size=500,
    )
    for doc in cursor:
        groups[doc[key_field]].append(doc)
    return {k: v for k, v in groups.items() if len(v) > 1}


def _ingested_rank(d: dict) -> float:
    """Rank component for 'earliest ingested wins'. Returns a value where
    HIGHER = better (so it composes with max()). Earlier timestamps score
    higher; a missing/invalid ingested_at ranks WORST (never beats a real
    date). Robust against Windows .timestamp() OverflowError on edge dates."""
    val = d.get("ingested_at")
    if not isinstance(val, datetime):
        return float("-inf")
    try:
        # Normalise to an absolute epoch regardless of tz-awareness so
        # naive/aware mixes compare consistently.
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return -val.timestamp()  # earlier (smaller) -> higher score
    except (OverflowError, OSError, ValueError):
        return float("-inf")


def _pick_keeper(group: list[dict]) -> dict:
    """Choose the email to KEEP among duplicates.

    Order of preference: (1) more attachments, (2) earliest ingested_at,
    (3) lowest _id as a stable tie-breaker."""
    def score(d):
        return (
            int(d.get("attachment_count") or 0),   # more attachments better
            _ingested_rank(d),                      # earlier ingested better
            -int(str(d["_id"]), 16),                # deterministic tie-breaker
        )
    return max(group, key=score)


def _delete_emails(mongo: MongoClientWrapper, ids: list[ObjectId]) -> tuple[int, int]:
    """Delete email docs + their attachments + GridFS blobs.
    Returns (deleted_attachments, deleted_emails)."""
    if not ids:
        return 0, 0

    # 1) Find their attachments
    att_docs = list(mongo.attachments.find(
        {"email_id": {"$in": ids}},
        projection={"_id": 1, "gridfs_id": 1},
    ))
    # 2) Delete GridFS blobs (best-effort)
    deleted_blobs = 0
    for d in att_docs:
        if d.get("gridfs_id"):
            try:
                mongo.gridfs.delete(d["gridfs_id"])
                deleted_blobs += 1
            except Exception as exc:
                logger.warning(f"Could not delete GridFS file {d['gridfs_id']}: {exc}")

    # 3) Delete attachment docs
    if att_docs:
        mongo.attachments.delete_many({"_id": {"$in": [a["_id"] for a in att_docs]}})

    # 4) Delete the emails themselves
    res = mongo.emails.delete_many({"_id": {"$in": ids}})
    return len(att_docs), res.deleted_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Find/remove duplicate emails.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete duplicates (default: report only)")
    parser.add_argument(
        "--strategy",
        choices=("content_hash", "internet_message_id", "both"),
        default="content_hash",
        help="Which key to dedup on (default: content_hash)",
    )
    args = parser.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    try:
        mongo.ping()

        keys: list[str] = []
        if args.strategy == "both":
            keys = ["internet_message_id", "content_hash"]
        else:
            keys = [args.strategy]

        all_ids_to_delete: set[ObjectId] = set()
        all_kept_ids: set[ObjectId] = set()
        report_per_key: dict[str, dict] = {}

        for key in keys:
            logger.info(f"Scanning duplicates by '{key}'…")
            groups = _group_emails(mongo, key)
            n_groups = len(groups)
            n_extra = sum(len(g) - 1 for g in groups.values())

            logger.info(
                f"  {key}: {n_groups:,} duplicate groups, "
                f"{n_extra:,} duplicate emails to remove"
            )
            report_per_key[key] = {"groups": n_groups, "extras": n_extra}

            for _key, group in groups.items():
                # Skip groups already covered by previous strategy
                group_ids = [d["_id"] for d in group]
                if any(i in all_kept_ids or i in all_ids_to_delete for i in group_ids):
                    # remove already-decided ones, work with remaining
                    group = [d for d in group if d["_id"] not in all_ids_to_delete]
                    if len(group) <= 1:
                        continue
                keeper = _pick_keeper(group)
                all_kept_ids.add(keeper["_id"])
                for d in group:
                    if d["_id"] != keeper["_id"]:
                        all_ids_to_delete.add(d["_id"])

        # Sample groups for the report
        if all_ids_to_delete:
            sample_query = {"_id": {"$in": list(all_ids_to_delete)[:5]}}
            for d in mongo.emails.find(sample_query, {"subject": 1, "from": 1, "date": 1}):
                subj = (d.get("subject") or "")[:80]
                sender = (d.get("from") or {}).get("email", "")
                logger.info(f"  example dup: {sender} | {d.get('date')} | {subj!r}")

        logger.info(
            f"Total duplicates to remove across strategies: "
            f"{len(all_ids_to_delete):,}"
        )

        if not args.apply:
            logger.info("Dry-run mode (use --apply to actually delete).")
            return 0

        if not all_ids_to_delete:
            logger.info("Nothing to do.")
            return 0

        # Delete in batches with progress
        ids_list = list(all_ids_to_delete)
        batch_size = 100
        total_attachments_deleted = 0
        total_emails_deleted = 0

        with tqdm(total=len(ids_list), desc="Deleting", unit="email") as bar:
            for i in range(0, len(ids_list), batch_size):
                chunk = ids_list[i: i + batch_size]
                a, e = _delete_emails(mongo, chunk)
                total_attachments_deleted += a
                total_emails_deleted += e
                bar.update(len(chunk))

        # Audit log
        mongo.db["dedup_runs"].insert_one({
            "started_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
            "strategy": args.strategy,
            "report": report_per_key,
            "emails_removed": total_emails_deleted,
            "attachments_removed": total_attachments_deleted,
        })

        logger.info(
            f"Removed {total_emails_deleted:,} emails and "
            f"{total_attachments_deleted:,} attachments. Storage reclaimed."
        )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
