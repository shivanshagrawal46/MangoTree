"""
Repair pass: upgrade every title-report page that fell back to RapidOCR (or
failed) during the full-OCR run to a GPT-5 vision transcription, and splice the
corrected text into documents/.extracted_text.

Why: 154 pages were refused by Claude's content filter; the GPT-5 fallback
returned empty (reasoning-token bug, now fixed in claude_ocr.py), so RapidOCR
(lower fidelity) transcribed them. This re-does ONLY those pages via GPT-5.

How the splice works: we never stored per-page text, but RapidOCR + the page
render are deterministic — re-running RapidOCR on the same page reproduces the
exact text that was joined into extracted_text, so we can find-and-replace it
with the GPT-5 transcription. If the old text can't be located, the corrected
text is appended as a labeled correction block and the doc is flagged.

Also audits/removes stray docs not belonging to the main run (the 169-vs-166
anomaly: an interrupted smoke test kept writing after the --clean).

Usage:
  python -m scripts.repair_ocr_pages --dry-run   # show what would change (free)
  python -m scripts.repair_ocr_pages --live      # repair via GPT-5 + update DB
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.pdf import _render_page_to_image, _ocr_page_with_rapidocr
from src.extractor.claude_ocr import _ocr_page_via_openai
from src.utils.logger import logger
from scripts.ingest_title_reports import DOCS_COLLECTION
from scripts.ingest_titles_full import strip_watermarks, TITLE_ROOT

BAD_METHODS = {"ocr", "ocr_failed", "vision_failed", "render_failed", "ocr_capped"}


def find_source_pdf(doc: Dict[str, Any]) -> Optional[Path]:
    for rel in (doc.get("custody") or {}).get("source_files", []):
        # paths are stored relative to TITLE_ROOT for the year folders, and
        # relative to the drive root (F:\) for sibling folders like
        # 'Title reports till Apr 27 to May 31, 2026'.
        for base in (TITLE_ROOT, Path(TITLE_ROOT.anchor)):
            p = base / rel
            if p.exists():
                return p
    return None


def repair_page(fdoc, page_idx: int, method: str, s: Settings) -> Dict[str, Any]:
    """Render one page; GPT-5 it; reproduce old RapidOCR text for the splice."""
    out: Dict[str, Any] = {"page": page_idx + 1, "gpt": None, "old": None}
    page = fdoc[page_idx]
    img = _render_page_to_image(page, s.ocr_vision_dpi)
    gpt = _ocr_page_via_openai(img)
    if gpt:
        out["gpt"] = strip_watermarks(gpt)
    if method == "ocr":
        ok, pg, _ = _ocr_page_with_rapidocr(
            page, page_idx + 1, ocr_lang=s.ocr_lang, ocr_dpi=s.ocr_dpi)
        if ok and pg is not None and (pg.text or "").strip():
            out["old"] = strip_watermarks(pg.text)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="GPT-5 repair of RapidOCR fallback pages.")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    s = Settings.load()
    now = datetime.now(timezone.utc)
    mongo = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    mongo.ping()
    docs = mongo.db[DOCS_COLLECTION]

    # NOTE: the one-time stray-doc cleanup (169-vs-166, Jun 10) was removed:
    # with multiple legitimate ingest batches the created_at heuristic would
    # misclassify newer batches as strays. Identity-dedup handles duplicates.

    # ---- B. find affected pages ----
    affected: List[Dict[str, Any]] = []
    total_bad_pages = 0
    for d in docs.find({"source_type": "title_report"},
                       {"pages": 1, "custody.source_files": 1}):
        bad = [pg for pg in (d.get("pages") or []) if pg.get("method") in BAD_METHODS]
        if bad:
            affected.append({"_id": d["_id"], "bad": bad, "custody": d.get("custody", {})})
            total_bad_pages += len(bad)
    logger.info(f"docs with RapidOCR/failed pages: {len(affected)}  total pages to repair: {total_bad_pages}")
    if args.dry_run:
        logger.info("DRY RUN — re-run with --live to repair via GPT-5.")
        mongo.close()
        return 0

    # ---- C. repair ----
    repaired = unfound_splices = gpt_failed = 0
    for di, item in enumerate(affected, 1):
        doc = docs.find_one({"_id": item["_id"]}, {"extracted_text": 1, "pages": 1, "quality": 1})
        pdf = find_source_pdf(item)
        if pdf is None or doc is None:
            logger.warning(f"  [{di}/{len(affected)}] {item['_id']}: source PDF missing — flagged")
            docs.update_one({"_id": item["_id"]}, {"$set": {"quality.needs_review": True,
                            "quality.review_reasons": ["repair_source_missing"]}})
            continue
        fdoc = fitz.open(pdf)
        text = doc.get("extracted_text") or ""
        pages_meta = doc.get("pages") or []
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(repair_page, fdoc, pg["page"] - 1, pg.get("method", ""), s): pg
                    for pg in item["bad"]}
            for fut in as_completed(futs):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"  page repair error: {str(exc)[:120]}")
        fdoc.close()

        doc_unfound = 0
        for r in sorted(results, key=lambda x: x["page"]):
            if not r["gpt"]:
                gpt_failed += 1
                continue
            old, new = r["old"], r["gpt"]
            if old and old in text:
                text = text.replace(old, new, 1)
            else:
                text += f"\n\n[PAGE {r['page']} — CORRECTED OCR (GPT-5)]\n{new}"
                doc_unfound += 1
            for pg in pages_meta:
                if pg.get("page") == r["page"]:
                    pg["method"] = "openai_vision"
            repaired += 1
        unfound_splices += doc_unfound

        docs.update_one({"_id": item["_id"]}, {"$set": {
            "extracted_text": text, "pages": pages_meta,
            "quality.ocr_repaired_pages": len([r for r in results if r["gpt"]]),
            "quality.repair_splice_unlocated": doc_unfound,
            "ocr_repaired_at": now, "updated_at": now,
        }})
        logger.info(f"  [{di}/{len(affected)}] {item['_id']}: repaired "
                    f"{len([r for r in results if r['gpt']])}/{len(item['bad'])} pages"
                    f"{' (unlocated splices=' + str(doc_unfound) + ')' if doc_unfound else ''}")

    logger.info("================ REPAIR DONE ================")
    logger.info(f"pages repaired via GPT-5: {repaired}  gpt_failed: {gpt_failed}  "
                f"append-mode splices: {unfound_splices}")
    mongo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
