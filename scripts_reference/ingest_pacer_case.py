"""Ingest a folder of PACER case-document PDFs into the corpus (generalized).

Same pipeline as the IPA ingest, parameterized for any case:
  1. FORCE-VISION OCR every page (Claude Sonnet 4.6 -> GPT-5 vision fallback;
     RapidOCR only if BOTH vision models fail). Store each PDF as a `documents`
     record (source_type=court_record, corpus=court_records,
     privilege_status=public_record) linked to the case entity, original bytes
     in GridFS. Idempotent by sha256.
  2. python -m scripts.chunk_embed_documents      (contextual summary + chunk + embed)
  3. python -m scripts.backfill_chunk_entities --sha-file <shas>   (entity linkage)

Example (Derosa):
  python -m scripts.ingest_pacer_case --live --workers 4 ^
     --docs-dir "E:\\PACER\\Derosa_David_6-2022bk12097\\documents" ^
     --case-id ent_case_derosa_6_2022bk12097 --case-number "6:22-bk-12097" ^
     --case-title "David Paul DeRosa" ^
     --court "United States Bankruptcy Court, Central District of California" ^
     --alias "David DeRosa" --alias "DeRosa" --alias "6:2022bk12097"
"""
from __future__ import annotations

import argparse
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.extractor import extract_from_bytes
from src.extractor.claude_ocr import init_spend_guard
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.hashing import sha256_bytes
from src.utils.logger import logger

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
    ap.add_argument("--docs-dir", required=True)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--case-number", required=True)
    ap.add_argument("--case-title", required=True)
    ap.add_argument("--court", default="United States Bankruptcy Court")
    ap.add_argument("--alias", action="append", default=[],
                    help="Extra alias for the case entity (repeatable).")
    ap.add_argument("--origin", default=None)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--smart", action="store_true",
                    help="Vision only on scanned pages; keep born-digital text layer.")
    ap.add_argument("--budget", type=float, default=250.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reocr", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    s = Settings.load()
    now = datetime.now(timezone.utc)
    init_spend_guard(args.budget)

    docs_dir = Path(args.docs_dir)
    case_id = args.case_id
    origin = args.origin or ("pacer_" + re.sub(r"[^a-z0-9]+", "_", args.case_number.lower()))

    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs, ents, rels = m.db["documents"], m.db["entities"], m.db["relationships"]
    gridfs_files = m.db["attachment_files.files"]

    files = sorted([p for p in docs_dir.glob("*.pdf")])
    if args.limit:
        files = files[: args.limit]
    logger.info(f"{len(files)} PACER PDFs in {docs_dir}")

    ocr_min_chars = s.ocr_text_layer_min_chars if args.smart else 10_000_000

    if not args.dry_run:
        aliases = list({args.case_title, args.case_number, *args.alias})
        ents.update_one({"_id": case_id}, {"$set": {
            "_id": case_id, "kind": "case", "matter_id": DEFAULT_MATTER_ID,
            "canonical_name": f"{args.case_title} — Bankruptcy {args.case_number}",
            "aliases": aliases, "case_number": args.case_number, "court": args.court,
            "source": "pacer", "updated_at": now,
        }, "$setOnInsert": {"created_at": now}}, upsert=True)

    counts = {"processed": 0, "skipped": 0, "failed": 0, "pages": 0}
    lock = threading.Lock()
    nfiles = len(files)

    def handle(idx_p):
        n, p = idx_p
        data = p.read_bytes()
        sha = sha256_bytes(data)
        doc_id = "doc_pacer_" + sha[:16]
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
                m.gridfs.upload_from_stream(p.name, data,
                                            metadata={"sha256": sha, "origin": origin})
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"    gridfs store failed for {p.name[:40]}: {exc}")

        doc = {
            "_id": doc_id, "source_type": "court_record",
            "instrument_subtype": "bankruptcy_filing", "matter_id": DEFAULT_MATTER_ID,
            "corpus": "court_records", "privilege_status": "public_record",
            "evidentiary_class": "court_record", "authority_score": 1.15,
            "case_ids": [case_id], "case_number": args.case_number,
            "case_title": args.case_title, "court": args.court,
            "docket_no": docket_no, "document_title": title, "document_date": dt,
            "page_count": npages, "extracted_text": text,
            "ocr_method": res.method, "ocr_avg_confidence": res.avg_ocr_confidence,
            "custody": {"source_files": [p.name], "source_path": str(p),
                        "sha256": sha, "origin": origin, "ingested_at": now},
            "quality": {"needs_review": len(text) < 200},
            "updated_at": now, "created_at": now,
        }
        docs.update_one({"_id": doc_id},
                        {"$set": doc, "$unset": {"chunked_at": "", "chunk_count": ""}},
                        upsert=True)
        rels.update_one({"type": "FILED_IN", "src": doc_id, "dst": case_id},
                        {"$set": {"type": "FILED_IN", "src": doc_id, "dst": case_id,
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

    logger.info("================ PACER CASE OCR INGEST DONE ================")
    logger.info(f"case={args.case_number} processed={counts['processed']} "
                f"skipped={counts['skipped']} failed={counts['failed']} "
                f"total_pages={counts['pages']}")
    logger.info(f"documents/ court_record now: {docs.count_documents({'source_type':'court_record'})}")
    if args.dry_run:
        logger.info("DRY RUN — re-run with --live to store.")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
