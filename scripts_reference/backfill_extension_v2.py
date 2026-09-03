"""
Backfill the `extension` field on `attachments_v2` and `email_chunks_v2`.

The Sprint 3 Step 1 OCR script didn't populate this field, but every
filename has the extension baked in (e.g. "image665221.jpg",
"5.21 (02402791xB2F1A).pdf"). We derive it once and write it back to
both collections so the v2 retriever (Atlas filter on `extension`) and
any "show me PDFs only" style queries work.

Idempotent. Run anytime — only writes when value would change.

Usage:
  python scripts/backfill_extension_v2.py
  python scripts/backfill_extension_v2.py --dry-run
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateMany

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger


# Canonical extensions we accept. Anything else we still record but lowercased.
_KNOWN = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "txt", "csv", "tsv", "rtf",
    "jpg", "jpeg", "png", "gif", "tif", "tiff", "bmp", "webp",
    "html", "htm", "eml", "msg",
    "zip", "7z", "rar", "tar", "gz",
    "mp3", "wav", "m4a", "ogg", "flac",
    "json", "xml", "yaml", "yml", "log",
}


def _extension_from_filename(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    base = os.path.basename(str(name))
    # Strip trailing whitespace / hidden chars.
    base = base.strip()
    if "." not in base:
        return None
    ext = base.rsplit(".", 1)[-1].lower()
    # Drop weirdly long or empty extensions.
    if not ext or len(ext) > 8:
        return None
    return ext


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    atts = mongo.db["attachments_v2"]
    chunks = mongo.db["email_chunks_v2"]

    try:
        mongo.ping()
        # ---- 1. attachments_v2 ---------------------------------------
        logger.info("Backfilling extension on attachments_v2")
        t0 = time.time()
        # Bucket attachments by derived-extension and update in bulk.
        from collections import defaultdict
        by_ext: dict = defaultdict(list)
        for a in atts.find(
            {"filename": {"$exists": True, "$ne": None}},
            {"_id": 1, "filename": 1, "extension": 1},
        ):
            ext = _extension_from_filename(a.get("filename"))
            cur = a.get("extension")
            if ext == cur:
                continue
            if ext is None and cur in (None, ""):
                continue
            by_ext[ext].append(a["_id"])

        n_atts_total = sum(len(v) for v in by_ext.values())
        logger.info(f"  attachments needing update: {n_atts_total:,}  "
                    f"distinct extensions: {len(by_ext):,}")
        if not args.dry_run:
            for ext, ids in by_ext.items():
                for chunk_start in range(0, len(ids), 1000):
                    batch = ids[chunk_start : chunk_start + 1000]
                    atts.update_many(
                        {"_id": {"$in": batch}},
                        {"$set": {"extension": ext}},
                    )
        logger.info(f"  attachments_v2 done in {time.time()-t0:.1f}s")

        # Top 10 extension distribution
        dist = sorted(by_ext.items(), key=lambda x: -len(x[1]))[:10]
        for ext, ids in dist:
            logger.info(f"    .{ext or '(none)'}: {len(ids):,}")

        # ---- 2. email_chunks_v2 (attachment chunks only) -------------
        logger.info("Backfilling extension on email_chunks_v2 attachment chunks")
        t0 = time.time()
        by_ext_chunks: dict = defaultdict(list)
        for c in chunks.find(
            {"source_type": "attachment",
             "filename": {"$exists": True, "$ne": None}},
            {"_id": 1, "filename": 1, "extension": 1},
        ):
            ext = _extension_from_filename(c.get("filename"))
            cur = c.get("extension")
            if ext == cur:
                continue
            if ext is None and cur in (None, ""):
                continue
            by_ext_chunks[ext].append(c["_id"])
        n_chunks_total = sum(len(v) for v in by_ext_chunks.values())
        logger.info(f"  chunks needing update: {n_chunks_total:,}")
        if not args.dry_run:
            for ext, ids in by_ext_chunks.items():
                for chunk_start in range(0, len(ids), 1000):
                    batch = ids[chunk_start : chunk_start + 1000]
                    chunks.update_many(
                        {"_id": {"$in": batch}},
                        {"$set": {"extension": ext}},
                    )
        logger.info(f"  email_chunks_v2 done in {time.time()-t0:.1f}s")

        logger.info("=" * 60)
        logger.info(
            f"DONE — attachments_v2 updated: {n_atts_total:,}  "
            f"email_chunks_v2 updated: {n_chunks_total:,}"
            + ("  (dry-run)" if args.dry_run else "")
        )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
