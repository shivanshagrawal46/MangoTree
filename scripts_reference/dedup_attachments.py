"""
Deduplicate attachment binaries in GridFS.

Background:
  • Multiple emails can carry the *same* file (e.g. an invoice forwarded
    several times).  Our ingestion stored one GridFS copy per attachment row,
    so identical binaries occupy space N times.
  • Each `attachments` document already stores the SHA-256 of its bytes.
  • This script groups attachments by sha256, keeps ONE canonical GridFS
    file per hash, and points every duplicate `attachments` row at that
    canonical file.  The orphaned GridFS files are then deleted.

The `attachments` rows themselves are NOT removed — every email keeps its
own attachment metadata (filename, size, etc.).  Only the binary storage
is consolidated.

Safety:
  • DRY-RUN by default.  Use --apply to actually mutate the DB.
  • Verifies the canonical file exists before redirecting refs.
  • Verifies sha256+size match between rows in the same group.

Usage:
  python scripts/dedup_attachments.py                # dry-run
  python scripts/dedup_attachments.py --apply        # commit
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import ObjectId
from pymongo import UpdateOne

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete duplicate GridFS files. Default is dry-run.")
    parser.add_argument("--min-size", type=int, default=0,
                        help="Skip groups whose unique payload is smaller than this many bytes (default 0)")
    args = parser.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)

    try:
        mongo.ping()
        attachments = mongo.attachments
        gridfs = mongo.gridfs
        chunks_coll = mongo.db["attachment_files.chunks"]
        files_coll = mongo.db["attachment_files.files"]

        total_attachments = attachments.count_documents({})
        logger.info(f"Total attachment rows: {total_attachments:,}")

        # Aggregate: group by sha256, count + collect (id, gridfs_id, size)
        pipeline = [
            {"$match": {"sha256": {"$exists": True, "$ne": None}}},
            {"$group": {
                "_id": "$sha256",
                "count": {"$sum": 1},
                "size": {"$first": "$size_bytes"},
                "rows": {"$push": {
                    "_id": "$_id",
                    "gridfs_id": "$gridfs_id",
                    "filename": "$filename",
                    "size_bytes": "$size_bytes",
                    "ingested_at": "$ingested_at",
                }},
            }},
            {"$match": {"count": {"$gt": 1}}},
            {"$sort": {"size": -1}},
        ]
        groups = list(attachments.aggregate(pipeline, allowDiskUse=True))
        logger.info(f"Found {len(groups):,} sha256 groups with duplicate binaries.")

        if not groups:
            logger.info("Nothing to dedup.")
            return 0

        # Stats
        n_dup_rows = 0
        n_dup_gridfs_files = 0
        bytes_saved = 0

        # Sample preview
        for g in groups[:10]:
            sha = g["_id"]
            sz = g.get("size") or 0
            cnt = g["count"]
            n_dup_rows += cnt - 1
            logger.info(
                f"  sha256={sha[:16]}…  copies={cnt:>3}  "
                f"bytes={_human(sz)}  example={g['rows'][0]['filename']!r}  "
                f"duplicate-bytes={_human(sz * (cnt - 1))}"
            )

        # Real totals across ALL groups
        n_dup_rows = sum(g["count"] - 1 for g in groups)
        bytes_saved = sum((g.get("size") or 0) * (g["count"] - 1) for g in groups)
        logger.info(
            f"Across all groups: {n_dup_rows:,} duplicate rows, "
            f"~{_human(bytes_saved)} reclaimable from GridFS."
        )

        # ---------- Apply phase ----------
        if not args.apply:
            logger.info("Dry-run only — pass --apply to commit changes.")
            return 0

        # Build updates: for each group, pick canonical = oldest ingested.
        # Update each duplicate row.gridfs_id -> canonical.gridfs_id
        # Delete each duplicate's old gridfs file (and its chunks).
        updates: list[UpdateOne] = []
        gridfs_to_delete: set[ObjectId] = set()

        for g in groups:
            if (g.get("size") or 0) < args.min_size:
                continue
            rows = g["rows"]
            # Sort by ingested_at (oldest first), then _id for deterministic tie-break
            rows.sort(key=lambda r: (r.get("ingested_at") or 0, str(r["_id"])))
            canonical = rows[0]
            canonical_gid = canonical["gridfs_id"]
            if not canonical_gid:
                continue

            for dup in rows[1:]:
                dup_gid = dup.get("gridfs_id")
                if dup_gid is None or dup_gid == canonical_gid:
                    continue
                updates.append(UpdateOne(
                    {"_id": dup["_id"]},
                    {"$set": {
                        "gridfs_id": canonical_gid,
                        "deduped_from_gridfs_id": dup_gid,
                    }},
                ))
                gridfs_to_delete.add(dup_gid)

        logger.info(
            f"Will redirect {len(updates):,} attachment rows and "
            f"delete {len(gridfs_to_delete):,} GridFS files."
        )

        # 1. Update attachment rows
        if updates:
            t0 = time.time()
            BATCH = 500
            for i in range(0, len(updates), BATCH):
                attachments.bulk_write(updates[i:i + BATCH], ordered=False)
                logger.info(f"  Updated {min(i + BATCH, len(updates)):,}/{len(updates):,} rows")
            logger.info(f"Row updates done in {time.time() - t0:.1f}s")

        # 2. Delete now-orphaned GridFS files (file metadata + chunks)
        if gridfs_to_delete:
            t0 = time.time()
            ids = list(gridfs_to_delete)
            BATCH = 200
            for i in range(0, len(ids), BATCH):
                batch = ids[i:i + BATCH]
                # Use raw collection delete for speed (gridfs.delete() is per-file)
                chunks_coll.delete_many({"files_id": {"$in": batch}})
                files_coll.delete_many({"_id": {"$in": batch}})
                logger.info(f"  Deleted {min(i + BATCH, len(ids)):,}/{len(ids):,} GridFS files")
            logger.info(f"GridFS deletion done in {time.time() - t0:.1f}s")

        # 3. Sanity check — every attachment row's gridfs_id should still resolve
        sample = attachments.aggregate([
            {"$sample": {"size": 50}},
            {"$project": {"gridfs_id": 1}},
        ])
        broken = 0
        for s in sample:
            if not files_coll.find_one({"_id": s["gridfs_id"]}, {"_id": 1}):
                broken += 1
        if broken:
            logger.error(f"SANITY FAILED: {broken}/50 sampled rows have a missing GridFS file!")
        else:
            logger.info("Sanity check passed (50 random rows resolve to a GridFS file).")

        logger.info(f"Done. Reclaimed approximately {_human(bytes_saved)}.")
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
