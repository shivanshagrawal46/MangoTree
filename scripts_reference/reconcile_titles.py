"""
DEFINITIVE reconciliation: for EVERY source PDF in every title folder, read the
FULL dedup key and check whether it exists in documents/.

  ProTitle key : address + order# + completed_date + index_date
  Prowess  key : address + order_type + search_date + old_eff + new_eff

For born-digital files the key is read from the text layer (free). For scanned
files we OCR just the SUMMARY pages (first 3) via Claude Vision to read the key
fields — cheap, and gives the exact dates (no address-only shortcut).

Output: per folder + grand total — matched vs NOT-in-DB, with every unmatched
file listed (an unmatched file = a report we have NOT ingested → must fix).
"""
import argparse
import io
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz
from PIL import Image

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger
from scripts.ingest_title_reports import (
    detect_vendor, parse_report, parse_prowess, is_first_page_excerpt, _parse_date,
)
from scripts.reparse_titles import addr_core
from scripts.ingest_titles_full import norm_address

FOLDERS = [
    r"F:\Title reports",
    r"F:\Title reports till Apr 27 to May 31, 2026",
    r"F:\Full_Search_Main",
    r"F:\Update_Search_Main",
]
KEY_PAGES = 3   # summary/key fields live on the first pages


def summary_text(p: Path, s: Settings) -> str:
    """Text of the first KEY_PAGES pages: text-layer if present, else Claude
    Vision OCR of just those pages (cheap)."""
    try:
        doc = fitz.open(str(p))
    except Exception:
        return ""
    try:
        n = min(KEY_PAGES, len(doc))
        txt = "\n".join((doc[i].get_text("text") or "") for i in range(n))
        if len(txt.strip()) >= 120:
            return txt
        # scanned → OCR first pages
        from src.extractor.claude_ocr import ocr_pages_via_claude
        imgs = []
        for i in range(n):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(180 / 72.0, 180 / 72.0), alpha=False)
            imgs.append((i + 1, Image.open(io.BytesIO(pix.tobytes("png")))))
        pages = ocr_pages_via_claude(imgs, model=s.ocr_vision_model, max_concurrency=4)
        return "\n".join(pg.text or "" for pg in pages)
    finally:
        doc.close()


def file_key(text: str) -> Optional[Tuple]:
    vendor = detect_vendor(text)
    if vendor == "protitle":
        r = parse_report(text)
        return ("PT", addr_core(norm_address(r.get("property_address"))),
                r.get("order_number"),
                _parse_date(r.get("completed_date")), _parse_date(r.get("index_date")))
    if vendor == "prowess":
        r = parse_prowess(text)
        return ("PW", addr_core(norm_address(r.get("property_address"))),
                r.get("order_type"),
                _parse_date(r.get("search_date")), _parse_date(r.get("old_effective_date")),
                _parse_date(r.get("new_effective_date")))
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=60.0)
    args = ap.parse_args()
    s = Settings.load()
    from src.extractor.claude_ocr import init_spend_guard, get_spend_guard
    init_spend_guard(args.budget)
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs = m.db["documents"]

    # ---- build DB key index ----
    db_keys: Dict[Tuple, str] = {}
    for d in docs.find({"source_type": "title_report"},
                       {"vendor": 1, "address_norm": 1, "order_number": 1, "completed_date": 1,
                        "index_date": 1, "order_type": 1, "search_date": 1,
                        "old_effective_date": 1, "new_effective_date": 1,
                        "has_embedded_original": 1}):
        ac = addr_core(d.get("address_norm") or "")
        if d.get("vendor") == "protitle":
            db_keys[("PT", ac, d.get("order_number"), d.get("completed_date"), d.get("index_date"))] = d["_id"]
        else:
            db_keys[("PW", ac, d.get("order_type"), d.get("search_date"),
                     d.get("old_effective_date"), d.get("new_effective_date"))] = d["_id"]
            # an update that embeds its original also covers a PT full-search key
    logger.info(f"DB title docs: {docs.count_documents({'source_type':'title_report'})}  "
                f"distinct keys: {len(db_keys)}")

    grand_match, grand_miss, grand_excerpt, grand_unknown = 0, 0, 0, 0
    misses: List[str] = []
    for fol in FOLDERS:
        folder = Path(fol)
        if not folder.exists():
            logger.info(f"\n=== {fol} : NOT FOUND ===")
            continue
        pdfs = [p for p in folder.rglob("*") if p.suffix.lower() == ".pdf"]
        fmatch = fmiss = fexc = funk = 0
        for p in pdfs:
            if is_first_page_excerpt(p.name):
                fexc += 1
                continue
            k = file_key(summary_text(p, s))
            if k is None:
                funk += 1
                misses.append(f"[UNKNOWN vendor] {fol}\\{p.name}")
                continue
            if k in db_keys:
                fmatch += 1
            else:
                fmiss += 1
                misses.append(f"[NOT IN DB] {fol}\\{p.name}  key={k}")
        logger.info(f"\n=== {fol} ({len(pdfs)} pdfs) ===")
        logger.info(f"   matched in DB={fmatch}  NOT in DB={fmiss}  excerpts={fexc}  unknown={funk}")
        grand_match += fmatch; grand_miss += fmiss; grand_excerpt += fexc; grand_unknown += funk

    logger.info("\n================ GRAND TOTAL ================")
    logger.info(f"matched={grand_match}  NOT-in-DB={grand_miss}  excerpts={grand_excerpt}  unknown={grand_unknown}")
    g = get_spend_guard()
    if g:
        logger.info(f"Vision spend: ${g.spent:.2f}")
    if misses:
        logger.info(f"\n--- {len(misses)} files NOT matched (must review/ingest) ---")
        for x in misses:
            logger.info("   " + x)
    else:
        logger.info("\nEVERY source file matches a DB report by full key. Nothing missing.")
    m.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
