"""Ingest the IPA Asset Management PACER case documents into the corpus.

Source: E:\\PACER\\IPA_asset_8-2025bk72526\\documents  (136 court-record PDFs)
Case:   8:25-bk-72526  ("IPA Asset Management, LLC", U.S. Bankruptcy Court E.D.N.Y.)

Pipeline (this script does step 1; steps 2-3 are the shared doc pipeline):
  1. FORCE-VISION OCR every page (Claude Sonnet 4.6 -> GPT-5 vision fallback;
     RapidOCR only if BOTH vision models fail a page). Store each PDF as a
     `documents` record (source_type=court_record, corpus=court_records,
     privilege_status=public_record) linked to the IPA case entity, with the
     original bytes in GridFS. Idempotent by sha256.
  2. `python -m scripts.chunk_embed_documents`   (contextual summary + chunk + embed)
  3. `python -m scripts.backfill_chunk_entities --sha-file <shas>`  (entity linkage)

Usage:
  python -m scripts.ingest_pacer_ipa --live            # force-vision (default)
  python -m scripts.ingest_pacer_ipa --live --smart    # vision only on scanned pages
  python -m scripts.ingest_pacer_ipa --live --budget 250 --limit 5
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.extractor import extract_from_bytes
from src.extractor.claude_ocr import init_spend_guard, get_spend_guard
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.hashing import sha256_bytes
from src.utils.logger import logger

DOCS_DIR = Path(r"E:\PACER\IPA_asset_8-2025bk72526\documents")
CASE_ID = "ent_case_ipa_8_2025bk72526"
CASE_NUMBER = "8:25-bk-72526"
CASE_TITLE = "IPA Asset Management, LLC"
COURT = "United States Bankruptcy Court, Eastern District of New York"
ORIGIN = "pacer_ipa_8_2025bk72526"

_NAME_RE = re.compile(r"^(\d+)_(\d{2}-\d{2}-\d{4})_(.*)\.pdf$", re.I)


def parse_name(name: str):
    m = _NAME_RE.match(name)
    if not m:
        return None, None, name.rsplit(".", 1)[0].replace("-", " ").strip()
    seq = int(m.group(1))
    try:
        dt = datetime.strptime(m.group(2), "%m-%d-%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        dt = None
    rest = m.group(3)
    dm = re.match(r"(\d+)-", rest)
    docket_no = int(dm.group(1)) if dm else seq
    title = re.sub(r"\s+", " ", rest.replace("-", " ")).strip()
    return docket_no, dt, title


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--smart", action="store_true",
                    help="Vision only on scanned pages; keep born-digital text layer.")
    ap.add_argument("--budget", type=float, default=250.0,
                    help="Claude vision spend cap for this run (USD).")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reocr", action="store_true",
                    help="Re-OCR documents already stored.")
    ap.add_argument("--workers", type=int, default=1,
                    help="Document-level parallelism (each doc still parallelizes "
                         "its own pages). Combine with a higher "
                         "CLAUDE_VISION_MAX_INFLIGHT for real speedup.")
    args = ap.parse_args()

    s = Settings.load()
    now = datetime.now(timezone.utc)
    init_spend_guard(args.budget)

    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs, ents, rels = m.db["documents"], m.db["entities"], m.db["relationships"]
    gridfs_files = m.db["attachment_files.files"]

    files = sorted([p for p in DOCS_DIR.glob("*.pdf")])
    if args.limit:
        files = files[: args.limit]
    logger.info(f"{len(files)} PACER PDFs in {DOCS_DIR}")

    force_vision = not args.smart
    if force_vision:
        ocr_min_chars = 10_000_000  # sentinel: every page -> vision
    else:
        ocr_min_chars = s.ocr_text_layer_min_chars

    if not args.dry_run:
        ents.update_one({"_id": CASE_ID}, {"$set": {
            "_id": CASE_ID, "kind": "case", "matter_id": DEFAULT_MATTER_ID,
            "canonical_name": f"{CASE_TITLE} — Bankruptcy {CASE_NUMBER} (EDNY)",
            "aliases": [CASE_TITLE, "IPA Asset Management", CASE_NUMBER,
                        "25-72526", "8:2025bk72526", "IPA Asset"],
            "case_number": CASE_NUMBER, "court": COURT,
            "source": "pacer", "updated_at": now,
        }, "$setOnInsert": {"created_at": now}}, upsert=True)

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    counts = {"processed": 0, "skipped": 0, "failed": 0, "pages": 0}
    lock = threading.Lock()
    nfiles = len(files)

    def handle(idx_p):
        n, p = idx_p
        data = p.read_bytes()
        sha = sha256_bytes(data)
        doc_id = "doc_pacer_ipa_" + sha[:16]
        docket_no, dt, title = parse_name(p.name)

        existing = docs.find_one({"_id": doc_id}, {"extracted_text": 1})
        if existing and (existing.get("extracted_text") or "").strip() and not args.reocr:
            with lock:
                counts["skipped"] += 1
            logger.info(f"  [{n}/{nfiles}] SKIP (already OCR'd) #{docket_no} {p.name[:50]}")
            return

        if args.dry_run:
            logger.info(f"  [{n}/{nfiles}] would OCR #{docket_no} {dt.date() if dt else '?'} {p.name[:50]}")
            return

        try:
            res = extract_from_bytes(
                data, p.name,
                ocr_lang=s.ocr_lang, ocr_min_chars=ocr_min_chars, ocr_dpi=s.ocr_dpi,
                enable_ocr=True, vision_enabled=True, vision_model=s.ocr_vision_model,
                vision_min_pages=1, vision_dpi=s.ocr_vision_dpi,
                vision_concurrency=s.ocr_vision_max_concurrency)
            text = (res.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            with lock:
                counts["failed"] += 1
            logger.warning(f"  [{n}/{nfiles}] OCR FAILED {p.name[:50]}: {exc}")
            return

        npages = len(res.pages or [])

        if not gridfs_files.find_one({"metadata.sha256": sha}, {"_id": 1}):
            try:
                m.gridfs.upload_from_stream(
                    p.name, data, metadata={"sha256": sha, "origin": ORIGIN})
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"    gridfs store failed for {p.name[:40]}: {exc}")

        doc = {
            "_id": doc_id, "source_type": "court_record",
            "instrument_subtype": "bankruptcy_filing", "matter_id": DEFAULT_MATTER_ID,
            "corpus": "court_records", "privilege_status": "public_record",
            "evidentiary_class": "court_record", "authority_score": 1.15,
            "case_ids": [CASE_ID], "case_number": CASE_NUMBER,
            "case_title": CASE_TITLE, "court": COURT,
            "docket_no": docket_no, "document_title": title, "document_date": dt,
            "page_count": npages, "extracted_text": text,
            "ocr_method": res.method, "ocr_avg_confidence": res.avg_ocr_confidence,
            "custody": {"source_files": [p.name], "source_path": str(p),
                        "sha256": sha, "origin": ORIGIN, "ingested_at": now},
            "quality": {"needs_review": len(text) < 200},
            "updated_at": now, "created_at": now,
        }
        docs.update_one({"_id": doc_id},
                        {"$set": doc, "$unset": {"chunked_at": "", "chunk_count": ""}},
                        upsert=True)
        rels.update_one({"type": "FILED_IN", "src": doc_id, "dst": CASE_ID},
                        {"$set": {"type": "FILED_IN", "src": doc_id, "dst": CASE_ID,
                                  "as_of": dt, "updated_at": now}}, upsert=True)
        with lock:
            counts["processed"] += 1
            counts["pages"] += npages
        logger.info(f"  [{n}/{nfiles}] OK #{docket_no} pages={npages} chars={len(text)} "
                    f"method={res.method}  {title[:40]}")

    work = list(enumerate(files, 1))
    if args.workers > 1 and not args.dry_run:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(as_completed([pool.submit(handle, ip) for ip in work]))
    else:
        for ip in work:
            handle(ip)

    processed, skipped, failed, total_pages = (
        counts["processed"], counts["skipped"], counts["failed"], counts["pages"])
    logger.info("================ PACER IPA OCR INGEST DONE ================")
    logger.info(f"processed={processed} skipped={skipped} failed={failed} total_pages={total_pages}")
    logger.info(f"documents/ court_record now: {docs.count_documents({'source_type':'court_record'})}")
    if args.dry_run:
        logger.info("DRY RUN — re-run with --live to store.")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
