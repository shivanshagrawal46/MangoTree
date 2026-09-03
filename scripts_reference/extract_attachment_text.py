"""
Extract text from every UNIQUE attachment binary in GridFS.

Pipeline:
  1. Group `attachments` by sha256 → unique binaries only
     (after dedup_attachments.py the gridfs_id of duplicates already
     points at the same canonical file, but we still group by sha256 so
     we never re-OCR the same bytes twice).
  2. For each unique binary, stream from GridFS and route to the right
     extractor (PyMuPDF / PaddleOCR / python-docx / openpyxl / raw).
  3. Persist the result on the `attachments` row(s) themselves:
       extracted_text:        full plain-text of the document
       extraction:            {method, char_count, avg_ocr_confidence,
                               page_count, pages: [{page_no, text, method, conf}],
                               extracted_at, skipped_reason}
     Every row that shares this sha256 receives the same payload (so that
     downstream chunking can find the text without an extra join).

Idempotency:
  • Rows that already have `extraction.method` and a non-empty
    `extracted_text` are skipped unless --force is passed.

Performance:
  • Born-digital PDFs run in ~50ms each.
  • OCR is the bottleneck (~2-5 sec per page). PaddleOCR is single-process
    by default, but we parallelise over *unique sha256s* using a
    ThreadPoolExecutor (`--workers`, default 2). Inside PaddleOCR a Lock
    serialises the predictor; threading still helps because PDF rendering
    & file I/O can run concurrently.

Usage:
  python scripts/extract_attachment_text.py
  python scripts/extract_attachment_text.py --workers 4
  python scripts/extract_attachment_text.py --force
  python scripts/extract_attachment_text.py --no-ocr        # dry pass: text-layer only
  python scripts/extract_attachment_text.py --limit 50      # quick smoke test
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateMany

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor import extract_from_bytes
from src.utils.logger import configure_logger, logger


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _read_gridfs(mongo: MongoClientWrapper, gridfs_id) -> bytes:
    buf = io.BytesIO()
    mongo.gridfs.download_to_stream(gridfs_id, buf)
    return buf.getvalue()


def _process_one(
    mongo: MongoClientWrapper,
    sha256: str,
    rows: List[Dict[str, Any]],
    *,
    enable_ocr: bool,
    ocr_lang: str,
    ocr_dpi: int,
    ocr_min_chars: int,
    vision_enabled: bool,
    vision_model: str,
    vision_min_pages: int,
    vision_dpi: int,
    vision_concurrency: int,
) -> Dict[str, Any]:
    import gc

    sample = rows[0]
    filename = sample.get("filename") or "attachment"
    gridfs_id = sample.get("gridfs_id")
    size = int(sample.get("size_bytes") or 0)

    if gridfs_id is None:
        return {"sha256": sha256, "skipped": "no_gridfs_id", "filename": filename, "size": size}

    try:
        data = _read_gridfs(mongo, gridfs_id)
    except Exception as exc:
        return {"sha256": sha256, "skipped": f"gridfs_read_error:{exc}", "filename": filename, "size": size}

    t0 = time.time()
    try:
        result = extract_from_bytes(
            data,
            filename,
            ocr_lang=ocr_lang,
            ocr_min_chars=ocr_min_chars,
            ocr_dpi=ocr_dpi,
            enable_ocr=enable_ocr,
            vision_enabled=vision_enabled,
            vision_model=vision_model,
            vision_min_pages=vision_min_pages,
            vision_dpi=vision_dpi,
            vision_concurrency=vision_concurrency,
        )
    finally:
        # Free large byte buffers BEFORE the next attachment loads.
        del data
        gc.collect()
    elapsed = time.time() - t0

    extraction_doc = {
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

    row_ids = [r["_id"] for r in rows]
    mongo.attachments.update_many(
        {"_id": {"$in": row_ids}},
        {"$set": {
            "extracted_text": result.text,
            "extraction": extraction_doc,
        }},
    )

    return {
        "sha256": sha256,
        "filename": filename,
        "size": size,
        "method": result.method,
        "chars": result.char_count,
        "pages": len(result.pages),
        "elapsed": elapsed,
        "rows_updated": len(row_ids),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workers", type=int, default=2, help="Parallel workers (default 2)")
    p.add_argument("--force", action="store_true", help="Re-extract even if already done")
    p.add_argument("--no-ocr", action="store_true", help="Disable OCR (text-layer only)")
    p.add_argument("--limit", type=int, default=0, help="Stop after N unique sha256 (smoke test)")
    p.add_argument("--max-size-mb", type=float, default=0.0,
                   help="Skip attachments larger than this many MB (do them in a later pass)")
    p.add_argument("--min-size-mb", type=float, default=0.0,
                   help="Only process attachments at least this big")
    args = p.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)

    # Arm the Claude Vision spend guard for this run.
    if settings.ocr_vision_enabled:
        from src.extractor.claude_ocr import init_spend_guard

        init_spend_guard(settings.ocr_vision_budget_usd)

    try:
        mongo.ping()

        match_stage = {"sha256": {"$exists": True, "$ne": None}}
        if not args.force:
            match_stage["$or"] = [
                {"extraction.method": {"$exists": False}},
                {"extracted_text": {"$in": [None, ""]}},
            ]

        pipeline = [
            {"$match": match_stage},
            {"$group": {
                "_id": "$sha256",
                "rows": {"$push": {
                    "_id": "$_id",
                    "filename": "$filename",
                    "gridfs_id": "$gridfs_id",
                    "size_bytes": "$size_bytes",
                }},
                "size": {"$max": "$size_bytes"},
            }},
            # Process SMALL files first so the easy wins land fast and giant
            # scans don't hold up worker threads. Use --max-size-mb to skip
            # the giants entirely on this pass.
            {"$sort": {"size": 1}},
        ]
        groups = list(mongo.attachments.aggregate(pipeline, allowDiskUse=True))

        # Apply size filters from CLI.
        before = len(groups)
        if args.max_size_mb > 0:
            cap = int(args.max_size_mb * 1024 * 1024)
            groups = [g for g in groups if (g.get("size") or 0) <= cap]
        if args.min_size_mb > 0:
            floor = int(args.min_size_mb * 1024 * 1024)
            groups = [g for g in groups if (g.get("size") or 0) >= floor]
        if before != len(groups):
            logger.info(
                f"Size filter: kept {len(groups):,} of {before:,} "
                f"(min={args.min_size_mb}MB, max={args.max_size_mb}MB)"
            )

        if args.limit:
            groups = groups[: args.limit]

        total = len(groups)
        logger.info(
            f"Unique attachments to extract: {total:,} "
            f"(workers={args.workers}, ocr={'off' if args.no_ocr else 'on'}, sort=size_asc)"
        )
        if total == 0:
            logger.info("Nothing to do.")
            return 0

        done = 0
        skipped = 0
        chars_total = 0
        pages_total = 0
        method_counts: Dict[str, int] = {}
        t0 = time.time()

        def runner(g):
            return _process_one(
                mongo,
                g["_id"],
                g["rows"],
                enable_ocr=not args.no_ocr,
                ocr_lang=settings.ocr_lang,
                ocr_dpi=settings.ocr_dpi,
                ocr_min_chars=settings.ocr_text_layer_min_chars,
                vision_enabled=settings.ocr_vision_enabled,
                vision_model=settings.ocr_vision_model,
                vision_min_pages=settings.ocr_vision_min_pages,
                vision_dpi=settings.ocr_vision_dpi,
                vision_concurrency=settings.ocr_vision_max_concurrency,
            )

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(runner, g) for g in groups]
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                except Exception as exc:
                    logger.error(f"Worker error: {exc}")
                    continue

                done += 1
                if "skipped" in r:
                    skipped += 1
                    method_counts["skipped"] = method_counts.get("skipped", 0) + 1
                else:
                    method_counts[r["method"]] = method_counts.get(r["method"], 0) + 1
                    chars_total += r["chars"]
                    pages_total += r["pages"]

                if done % 25 == 0 or done == total:
                    elapsed = time.time() - t0
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    logger.info(
                        f"  [{done:>5}/{total}] "
                        f"chars={chars_total:>10,}  pages={pages_total:>5}  "
                        f"rate={rate:.2f}/s  eta={eta/60:.1f} min  "
                        f"methods={method_counts}"
                    )

        elapsed = time.time() - t0
        logger.info(
            f"Done in {elapsed/60:.1f} min — "
            f"{done} attachments processed ({skipped} skipped). "
            f"Methods: {method_counts}"
        )
        if settings.ocr_vision_enabled:
            from src.extractor.claude_ocr import get_spend_guard
            g = get_spend_guard()
            if g is not None:
                logger.info(
                    f"Claude Vision OCR spend this run: ${g.spent:.3f} "
                    f"(budget ${g.budget:.2f})"
                )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
