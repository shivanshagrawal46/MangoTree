"""
Sprint 3 - Step 1:  Re-OCR every attachment with Claude Sonnet 4.6 Vision
and write the results to a NEW collection `attachments_v2`.

What this script does
---------------------
1. Reads (READ-ONLY) the live `attachments` collection.
2. Groups by sha256 so each unique binary is OCR'd exactly once even when
   the same file is attached to multiple emails.
3. For each unique binary:
     a. Streams the binary out of GridFS.
     b. Routes through the existing extractor:
          - born-digital PDFs  -> text layer  (free, perfect fidelity)
          - scanned / image PDFs -> Claude Vision   (every OCR-needed page)
          - DOCX / XLSX / TXT  -> native parser (no Vision needed)
          - PNG / JPG / TIFF   -> Claude Vision
        Settings ensure OCR_VISION_MIN_PAGES=1, so any page that lacks a
        clean text layer is sent to Vision; RapidOCR is kept only as a
        permanent-fallback when Vision itself errors out.
     c. Inserts ONE document per source attachment_id into `attachments_v2`
        (so the same hash that is attached to 4 emails creates 4 v2 rows
        that all share the identical extracted text). This preserves
        every join we have today.

Safety properties
-----------------
- Idempotent.  Resumable.  We never insert a duplicate.
- Read-only against `attachments` and GridFS (only INSERTs into a NEW
  collection `attachments_v2`).
- The live system (Sprint 2.5 RAG, `email_chunks`, vector index) continues
  to work the whole time.
- Spend guard caps the entire run at OCR_VISION_BUDGET_USD (set in .env).
  If the cap is hit, remaining pages fall back to RapidOCR or stay empty;
  the script logs the cause and exits cleanly.

Usage
-----
  python scripts/ocr_attachments_v2.py                 # full run
  python scripts/ocr_attachments_v2.py --workers 2     # file-level parallelism
  python scripts/ocr_attachments_v2.py --limit 5       # smoke test: first 5 files
  python scripts/ocr_attachments_v2.py --max-size-mb 5 # skip giant PDFs this pass
  python scripts/ocr_attachments_v2.py --force         # re-OCR even if v2 row exists

Resumability: re-running the same command picks up exactly where the last
run left off (skips every sha256 already present in `attachments_v2`).
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

from pymongo import ASCENDING

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor import extract_from_bytes
from src.utils.logger import configure_logger, logger


# Name of the destination collection. Lives next to `attachments` in the
# same database; the live one is never touched.
V2_COLLECTION = "attachments_v2"


def _read_gridfs(mongo: MongoClientWrapper, gridfs_id: Any) -> bytes:
    buf = io.BytesIO()
    mongo.gridfs.download_to_stream(gridfs_id, buf)
    return buf.getvalue()


def _ensure_v2_collection(mongo: MongoClientWrapper):
    """
    Create `attachments_v2` with sensible indexes if it doesn't exist.

    `_id` is the same ObjectId as `attachments._id`, so the implicit
    primary-key index already covers attachment-FK lookups. We just add
    the helpful secondary indexes for sha256 / email_id / filename queries.
    """
    coll = mongo.db[V2_COLLECTION]
    existing = coll.index_information()
    if "ix_v2_sha256" not in existing:
        coll.create_index([("sha256", ASCENDING)], name="ix_v2_sha256")
    if "ix_v2_email_id" not in existing:
        coll.create_index([("email_id", ASCENDING)], name="ix_v2_email_id")
    if "ix_v2_filename" not in existing:
        coll.create_index([("filename", ASCENDING)], name="ix_v2_filename")
    return coll


def _already_done_sha_set(v2: Any) -> set:
    """Return the set of sha256s already present in attachments_v2."""
    return {doc["sha256"] for doc in v2.find({}, {"sha256": 1, "_id": 0}) if doc.get("sha256")}


def _process_one(
    mongo: MongoClientWrapper,
    v2_coll: Any,
    *,
    sha256: str,
    rows: List[Dict[str, Any]],
    settings: Settings,
    force_vision: bool = False,
) -> Dict[str, Any]:
    """OCR one unique binary and insert one v2 row per source attachment_id."""
    import gc

    sample = rows[0]
    filename = sample.get("filename") or "attachment"
    gridfs_id = sample.get("gridfs_id")
    size = int(sample.get("size_bytes") or 0)

    if gridfs_id is None:
        return {"sha256": sha256, "skipped": "no_gridfs_id",
                "filename": filename, "size": size}

    try:
        data = _read_gridfs(mongo, gridfs_id)
    except Exception as exc:  # noqa: BLE001
        return {"sha256": sha256, "skipped": f"gridfs_read_error:{exc}",
                "filename": filename, "size": size}

    t0 = time.time()
    # force_vision: set the text-layer threshold impossibly high so EVERY page
    # is treated as "needs OCR" and goes through Claude Sonnet 4.6 Vision
    # (-> GPT-5 vision -> RapidOCR on failure). No born-digital text layer is used.
    ocr_min = 10_000_000 if force_vision else settings.ocr_text_layer_min_chars
    try:
        result = extract_from_bytes(
            data,
            filename,
            ocr_lang=settings.ocr_lang,
            ocr_min_chars=ocr_min,
            ocr_dpi=settings.ocr_dpi,
            enable_ocr=True,
            vision_enabled=settings.ocr_vision_enabled,
            vision_model=settings.ocr_vision_model,
            vision_min_pages=settings.ocr_vision_min_pages,  # = 1 from .env
            vision_dpi=settings.ocr_vision_dpi,
            vision_concurrency=settings.ocr_vision_max_concurrency,
        )
    finally:
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

    # Insert ONE v2 row per source attachment, REUSING the original `_id`.
    # This means every existing foreign-key reference (emails.attachment_ids[],
    # email_chunks.attachment_id) maps directly to the v2 row with no
    # translation table needed.
    v2_docs: List[Dict[str, Any]] = []
    for r in rows:
        v2_docs.append({
            "_id": r["_id"],                     # SAME ObjectId as attachments._id
            "email_id": r.get("email_id"),       # FK to parent email — unchanged
            "sha256": sha256,
            "filename": r.get("filename"),
            "gridfs_id": r.get("gridfs_id"),
            "size_bytes": r.get("size_bytes"),
            "extracted_text": result.text,
            "extraction": extraction_doc,
            "extracted_via": "vision_v2",
            "extracted_at": datetime.now(timezone.utc),
        })

    if v2_docs:
        # Insert with ordered=False so a single duplicate (re-run, partial
        # prior run) doesn't abort the whole batch — pymongo skips dupes
        # and processes the rest.
        try:
            v2_coll.insert_many(v2_docs, ordered=False)
        except Exception as exc:
            # BulkWriteError happens when some _ids already exist (resume
            # mid-batch). That's expected and safe — log and continue.
            msg = str(exc).lower()
            if "duplicate key" not in msg and "e11000" not in msg:
                raise

    return {
        "sha256": sha256,
        "filename": filename,
        "size": size,
        "method": result.method,
        "chars": result.char_count,
        "pages": len(result.pages),
        "elapsed": elapsed,
        "rows_inserted": len(v2_docs),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workers", type=int, default=2,
                   help="File-level parallelism (default 2; bump only if your "
                        "Anthropic tier supports more concurrent input-TPM)")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after N unique sha256 (smoke-test mode)")
    p.add_argument("--max-size-mb", type=float, default=0.0,
                   help="Skip attachments larger than this many MB on this pass")
    p.add_argument("--min-size-mb", type=float, default=0.0,
                   help="Only process attachments at least this big")
    p.add_argument("--force", action="store_true",
                   help="Re-OCR even sha256s already present in attachments_v2")
    p.add_argument("--force-vision", action="store_true",
                   help="Send EVERY page to Claude Sonnet 4.6 Vision (no "
                        "born-digital text layer). Fallback: GPT-5 vision -> RapidOCR.")
    p.add_argument("--sha-file", default=None,
                   help="Only OCR the sha256s listed in this file (one per line). "
                        "Used to scope OCR to a specific case/label's attachments.")
    args = p.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)

    # Arm the global spend guard for Claude Vision.
    from src.extractor.claude_ocr import init_spend_guard, get_spend_guard
    init_spend_guard(settings.ocr_vision_budget_usd)

    try:
        mongo.ping()
        v2_coll = _ensure_v2_collection(mongo)

        # Group all source attachments by sha256 so we OCR each binary once.
        pipeline = [
            {"$match": {"sha256": {"$exists": True, "$ne": None},
                        "gridfs_id": {"$exists": True, "$ne": None}}},
            {"$group": {
                "_id": "$sha256",
                "rows": {"$push": {
                    "_id": "$_id",
                    "email_id": "$email_id",
                    "filename": "$filename",
                    "gridfs_id": "$gridfs_id",
                    "size_bytes": "$size_bytes",
                }},
                "size": {"$max": "$size_bytes"},
            }},
            # Process small files first so the easy wins land fast and giant
            # scans don't hold up worker threads.
            {"$sort": {"size": 1}},
        ]
        groups = list(mongo.attachments.aggregate(pipeline, allowDiskUse=True))

        # Optional scope — restrict to a specific set of sha256s (e.g. one case).
        if args.sha_file:
            wanted = {ln.strip() for ln in Path(args.sha_file).read_text(
                encoding="utf-8").splitlines() if ln.strip()}
            before = len(groups)
            groups = [g for g in groups if g["_id"] in wanted]
            logger.info(f"sha-file scope: kept {len(groups):,} of {before:,} "
                        f"(from {len(wanted):,} requested sha256s)")

        # Resume — skip sha256s already in v2 (unless --force).
        if not args.force:
            done_set = _already_done_sha_set(v2_coll)
            if done_set:
                before = len(groups)
                groups = [g for g in groups if g["_id"] not in done_set]
                logger.info(
                    f"Resume: skipping {before - len(groups):,} unique sha256s "
                    f"already in {V2_COLLECTION}"
                )

        # Size filters.
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
            f"Sprint 3 / Step 1 — Claude Vision OCR -> {V2_COLLECTION}\n"
            f"  Unique attachments to OCR:   {total:,}\n"
            f"  Workers (file-level):        {args.workers}\n"
            f"  Vision model:                {settings.ocr_vision_model}\n"
            f"  FORCE VISION (no born-digital): {args.force_vision} "
            f"(every page -> Sonnet 4.6 Vision -> GPT-5 -> RapidOCR)\n"
            f"  Spend cap (whole run):       ${settings.ocr_vision_budget_usd:.2f}"
        )
        if total == 0:
            logger.info("Nothing to do.")
            return 0

        done = 0
        skipped = 0
        chars_total = 0
        pages_total = 0
        method_counts: Dict[str, int] = {}
        t_start = time.time()

        def runner(g):
            return _process_one(
                mongo, v2_coll,
                sha256=g["_id"], rows=g["rows"], settings=settings,
                force_vision=args.force_vision,
            )

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(runner, g) for g in groups]
            for fut in as_completed(futures):
                try:
                    r = fut.result()
                except Exception as exc:  # noqa: BLE001
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

                if done % 10 == 0 or done == total:
                    elapsed = time.time() - t_start
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total - done) / rate if rate > 0 else 0
                    spent = get_spend_guard().spent if get_spend_guard() else 0.0
                    logger.info(
                        f"  [{done:>5}/{total}] "
                        f"chars={chars_total:>10,}  pages={pages_total:>5}  "
                        f"rate={rate:.2f}/s  eta={eta/60:.1f}m  "
                        f"spent=${spent:.2f}  methods={method_counts}"
                    )

        elapsed = time.time() - t_start
        guard = get_spend_guard()
        logger.info(
            f"\nDONE in {elapsed/60:.1f} min  "
            f"({done} attachments processed, {skipped} skipped). "
            f"Methods: {method_counts}\n"
            f"Claude Vision spend: ${guard.spent:.3f} / ${guard.budget:.2f}"
        )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
