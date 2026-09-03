"""
Sprint 3 — Step 1.5  ::  Rescue every `skipped` attachment in attachments_v2.

Goal
----
ZERO data loss. The OCR pass left 153 unique binaries with
`extraction.method == 'skipped'` because the v1 extractor didn't support
their format (.doc, .xls, .htm, .eml, no-extension, .mp3, plus images
RapidOCR couldn't read). This script routes each of those through a
format-specific handler in `src.extractor.rescue` and **updates the
existing `attachments_v2` rows IN PLACE** so foreign-key joins stay
intact.

Idempotent + resumable
----------------------
We only touch rows where `extraction.method == 'skipped'`. After a
successful rescue the row's method changes to e.g. 'doc' / 'xls' /
'html' / 'eml' / 'audio_whisper' / 'image_vision', so re-running picks
up only what's still skipped. Cheap to run repeatedly.

Audit trail
-----------
Every rescued row gets:
    extracted_text   = <new text>
    extraction       = <fresh per-format ExtractionResult dict>
    extracted_via    = 'rescue_v1'
    extracted_at     = <utc now>
    rescue_reason    = <v1 skipped_reason>          # what we rescued from

Usage
-----
  python scripts/rescue_skipped.py                       # full rescue
  python scripts/rescue_skipped.py --ext .doc            # one extension
  python scripts/rescue_skipped.py --limit 5             # smoke test
  python scripts/rescue_skipped.py --dry-run             # no DB writes
  python scripts/rescue_skipped.py --skip-audio          # defer .mp3
  python scripts/rescue_skipped.py --workers 4           # parallelism
"""
from __future__ import annotations

import argparse
import gc
import io
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.rescue import rescue_extract
from src.utils.logger import configure_logger, logger


V2_COLLECTION = "attachments_v2"
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _read_gridfs(mongo: MongoClientWrapper, gridfs_id: Any) -> bytes:
    buf = io.BytesIO()
    mongo.gridfs.download_to_stream(gridfs_id, buf)
    return buf.getvalue()


def _build_extraction_doc(result, elapsed: float) -> Dict[str, Any]:
    return {
        "method": result.method,
        "char_count": result.char_count,
        "avg_ocr_confidence": result.avg_ocr_confidence,
        "page_count": len(result.pages),
        "pages": [
            {
                "page_no": p.page_no,
                "method": p.method,
                "ocr_confidence": p.ocr_confidence,
                "char_count": len(p.text),
                "text": p.text,
            }
            for p in result.pages
        ],
        "extracted_at": datetime.now(timezone.utc),
        "skipped_reason": result.skipped_reason,
        "elapsed_sec": round(elapsed, 3),
    }


def _process_one(
    mongo: MongoClientWrapper,
    v2_coll,
    sha256: str,
    rows: List[Dict[str, Any]],
    dry_run: bool,
) -> Dict[str, Any]:
    """Stream the binary once, run the rescue handler, update every row
    that shares this sha256."""
    sample = rows[0]
    filename = sample.get("filename", "") or ""
    gridfs_id = sample.get("gridfs_id")
    size = sample.get("size_bytes", 0) or 0
    v1_reason = (sample.get("extraction") or {}).get("skipped_reason")

    t0 = time.time()
    try:
        data = _read_gridfs(mongo, gridfs_id)
    except Exception as exc:
        logger.warning(f"  GridFS read failed for sha={sha256[:12]} ({filename!r}): {exc}")
        return {
            "sha256": sha256, "filename": filename, "size": size,
            "method": "gridfs_error", "chars": 0, "rows_updated": 0,
            "elapsed": time.time() - t0,
        }

    try:
        result = rescue_extract(data, filename, skipped_reason=v1_reason)
    finally:
        del data
        gc.collect()
    elapsed = time.time() - t0

    extraction_doc = _build_extraction_doc(result, elapsed)
    n_updated = 0

    if not dry_run:
        update = {
            "$set": {
                "extracted_text": result.text,
                "extraction": extraction_doc,
                "extracted_via": "rescue_v1",
                "extracted_at": datetime.now(timezone.utc),
                "rescue_reason": v1_reason,
            }
        }
        # Update EVERY row that shares this sha256 (a single binary can
        # be attached to multiple emails — each gets its own v2 row).
        res = v2_coll.update_many({"sha256": sha256}, update)
        n_updated = res.modified_count

    return {
        "sha256": sha256,
        "filename": filename,
        "size": size,
        "method": result.method,
        "skipped_reason": result.skipped_reason,
        "chars": result.char_count,
        "elapsed": elapsed,
        "rows_updated": n_updated,
        "v1_reason": v1_reason,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workers", type=int, default=2,
                   help="Parallel rescue workers (default 2). MS Word "
                        "COM serialises per-thread; do NOT raise this above "
                        "~4 if you have lots of .doc files.")
    p.add_argument("--ext", type=str, default="",
                   help="Restrict to one extension, e.g. .doc / .xls / .htm")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N unique sha256 (smoke-test)")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write to MongoDB; just print what would happen")
    p.add_argument("--skip-audio", action="store_true",
                   help="Skip .mp3 / .wav etc. (use after you've set "
                        "OPENAI_API_KEY for a second pass)")
    args = p.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)

    # Arm the Claude Vision spend guard for image re-OCR + TNEF-embedded
    # scanned PDFs. Larger budget now that we unwrap winmail.dat documents.
    from src.extractor.claude_ocr import init_spend_guard
    init_spend_guard(min(150.0, settings.ocr_vision_budget_usd))

    mongo.ping()
    v2_coll = mongo.db[V2_COLLECTION]

    pipeline: List[Dict[str, Any]] = [
        {"$match": {"extraction.method": "skipped"}},
        {"$group": {
            "_id": "$sha256",
            "rows": {"$push": {
                "_id": "$_id",
                "email_id": "$email_id",
                "filename": "$filename",
                "gridfs_id": "$gridfs_id",
                "size_bytes": "$size_bytes",
                "extraction": "$extraction",
            }},
            "filename_any": {"$first": "$filename"},
            "size_any": {"$first": "$size_bytes"},
        }},
        {"$sort": {"size_any": 1}},
    ]
    groups = list(v2_coll.aggregate(pipeline))

    # Filter by extension if requested.
    if args.ext:
        ext = args.ext.lower()
        if not ext.startswith("."):
            ext = "." + ext
        groups = [
            g for g in groups
            if (g.get("filename_any") or "").lower().endswith(ext)
        ]

    # Drop audio if requested.
    if args.skip_audio:
        groups = [
            g for g in groups
            if Path(g.get("filename_any", "") or "").suffix.lower() not in AUDIO_EXTS
        ]

    if args.limit > 0:
        groups = groups[: args.limit]

    total = len(groups)
    if total == 0:
        logger.info("Nothing to rescue — every attachments_v2 row has text. Exit.")
        return 0

    # Sanity preview by extension.
    by_ext: Counter = Counter()
    for g in groups:
        e = Path(g.get("filename_any", "") or "").suffix.lower() or "(no-ext)"
        by_ext[e] += 1

    logger.info(
        "Sprint 3 / Step 1.5 — Rescue\n"
        f"  Skipped binaries to rescue: {total}\n"
        f"  By extension: {dict(by_ext)}\n"
        f"  Workers: {args.workers}\n"
        f"  Dry-run: {args.dry_run}\n"
        f"  Skip audio: {args.skip_audio}"
    )

    counts: Counter = Counter()
    reasons: Counter = Counter()
    total_chars = 0
    rescued = 0
    failed = 0

    start = time.time()
    futures = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for g in groups:
            f = pool.submit(
                _process_one, mongo, v2_coll, g["_id"], g["rows"], args.dry_run
            )
            futures[f] = g["_id"]

        done = 0
        for fut in as_completed(futures):
            sha = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:
                failed += 1
                logger.exception(f"  rescue failed for sha={sha[:12]}: {exc}")
                continue
            done += 1
            counts[res["method"]] += 1
            if res["method"] == "skipped":
                reasons[res.get("skipped_reason") or "(none)"] += 1
            else:
                rescued += 1
                total_chars += res["chars"]
            if done % 10 == 0 or done == total:
                elapsed = time.time() - start
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate / 60 if rate > 0 else 0
                logger.info(
                    f"  [{done:>4}/{total}]  chars={total_chars:>10,}  "
                    f"rescued={rescued}  still_skipped={done - rescued}  "
                    f"rate={rate:.2f}/s  eta={eta:.1f}m  "
                    f"methods={dict(counts)}"
                )

    elapsed_min = (time.time() - start) / 60
    logger.info("")
    logger.info(
        f"DONE in {elapsed_min:.1f} min  "
        f"({rescued} rescued / {total} attempted / {failed} hard-failed)"
    )
    logger.info(f"Method tally: {dict(counts)}")
    if reasons:
        logger.info(f"Remaining skip reasons (need follow-up): {dict(reasons)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
