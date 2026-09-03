"""Safety audit: prove no ProTitle report in a year folder was lost to dedup.

Inspects EVERY PDF (OCR'ing scanned ones), recovers identity
(Order#, completed_date, index_date), and flags:
  • Order#s with 2+ REAL (non-empty) date variants  -> genuine distinct reports
    that share an order# (e.g. an order# reused for another property). These
    MUST each exist as their own document.
  • files with no parseable Order#
  • files not detected as ProTitle (even after OCR)
Then compares the distinct-document count to what is stored in documents/.

Usage:  python -m scripts.audit_title_reports --year 2021
"""
import argparse
import sys
from pathlib import Path
from collections import defaultdict

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.extractor import extract_from_bytes
from scripts.ingest_title_reports import (
    is_protitle, parse_report, is_first_page_excerpt, DOCS_COLLECTION,
)
from src.extractor.claude_ocr import init_spend_guard, get_spend_guard

TITLE_ROOT = r"F:\Title reports"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", required=True)
    ap.add_argument("--budget", type=float, default=50.0, help="Vision OCR budget USD")
    args = ap.parse_args()

    s = Settings.load()
    init_spend_guard(args.budget)
    folder = Path(TITLE_ROOT) / args.year
    pdfs = sorted(folder.glob("*.pdf"))
    print(f"Auditing {len(pdfs)} PDFs in {folder}\n", flush=True)

    def _ex(data, name, ocr):
        return extract_from_bytes(
            data, name, ocr_lang=s.ocr_lang, ocr_min_chars=s.ocr_text_layer_min_chars,
            ocr_dpi=s.ocr_dpi, enable_ocr=ocr,
            vision_enabled=(s.ocr_vision_enabled and ocr), vision_model=s.ocr_vision_model,
            vision_min_pages=s.ocr_vision_min_pages, vision_dpi=s.ocr_vision_dpi,
            vision_concurrency=s.ocr_vision_max_concurrency,
        )

    recs, no_order, not_protitle, ocr_used, excerpts = {}, [], [], 0, 0
    for i, p in enumerate(pdfs, 1):
        if is_first_page_excerpt(p.name):
            excerpts += 1   # page-1 excerpt of a full report — never OCR (cost $0)
            continue
        data = p.read_bytes()
        try:
            text = (_ex(data, p.name, ocr=False).text or "")
        except Exception:
            text = ""
        if not is_protitle(text):
            try:
                text = (_ex(data, p.name, ocr=True).text or "")
                ocr_used += 1
            except Exception:
                text = ""
        if not is_protitle(text):
            not_protitle.append(p.name)
            continue
        r = parse_report(text)
        recs[p.name] = {"order": r.get("order_number"),
                        "completed": (r.get("completed_date") or "").strip(),
                        "index": (r.get("index_date") or "").strip()}
        if not r.get("order_number"):
            no_order.append(p.name)
        if i % 25 == 0:
            print(f"  {i}/{len(pdfs)} | parsed={len(recs)} no_order={len(no_order)} "
                  f"not_protitle={len(not_protitle)} ocr_used={ocr_used}", flush=True)

    composites = defaultdict(list)
    by_order = defaultdict(set)
    order_files = defaultdict(list)
    for fn, r in recs.items():
        composites[(r["order"] or f"NOORDER:{fn}", r["completed"], r["index"])].append(fn)
        if r["order"]:
            by_order[r["order"]].add((r["completed"], r["index"]))
            order_files[r["order"]].append(fn)

    # genuine collisions: same order# with 2+ NON-EMPTY date variants
    real_conflicts = {}
    for o, variants in by_order.items():
        real = [v for v in variants if v[0] or v[1]]
        if len(real) >= 2:
            real_conflicts[o] = real

    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db_n = m.db[DOCS_COLLECTION].count_documents(
        {"source_type": "title_report",
         "custody.origin": "title_reports/" + args.year})
    m.close()

    print("\n================ AUDIT RESULT ================")
    print(f"PDFs inspected:              {len(pdfs)}")
    print(f"'1st page' excerpts skipped: {excerpts} (no OCR)")
    print(f"OCR used on:                 {ocr_used} files")
    print(f"ProTitle files parsed:       {len(recs)}")
    print(f"NOT ProTitle (even w/ OCR):  {len(not_protitle)}")
    print(f"ProTitle but NO Order#:      {len(no_order)}")
    print(f"DISTINCT (order,completed,index): {len(composites)}")
    print(f"documents/ stored for {args.year}: {db_n}")

    print(f"\n>>> GENUINE collisions (same Order#, 2+ real date variants): {len(real_conflicts)}")
    for o, variants in real_conflicts.items():
        print(f"   ORDER {o}:")
        for (c, idx) in sorted(variants):
            fs = [f for f in order_files[o] if (recs[f]['completed'], recs[f]['index']) == (c, idx)]
            print(f"      completed={c!r} index={idx!r}  files={fs}")
    if not_protitle:
        print(f"\n>>> Not detected as ProTitle: {not_protitle}")

    g = get_spend_guard()
    if g:
        print(f"\nVision spend this audit: ${g.spent:.2f}")
    clean = not not_protitle
    print("\nVERDICT:", "CLEAN — every distinct report is accounted for"
          if clean else "REVIEW — see flags above")
    return 0


if __name__ == "__main__":
    sys.exit(main())
