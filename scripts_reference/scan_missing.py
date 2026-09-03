"""
"What's missing" scanner (Sprint 2/3 recall diagnostics) — READ-ONLY.

Produces hard numbers on the recall gaps the improvement plan targets, so
we spend money (OCR/embedding) only where it's justified. Writes NOTHING
to the database. Safe to run any time.

Sections:
  1. Quoted-thread recall gap  — quoted text not represented as chunks
  2. RFC 2047 encoded subjects  — undecoded =?utf-8?..?= headers
  3. Mojibake bodies            — UTF-16/CJK garbage
  4. Extractor coverage         — attachments by extension; unreadable types
  5. OCR gaps                   — failed/capped/empty pages
  6. Chunk & graph health       — embedding/context/entity coverage

Usage:  python scripts/scan_missing.py [--json out.json] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.cleaner.quoted_text import (
    split_quoted_tail, iter_quoted_segments, quote_fingerprint,
    BUCKET_DUPLICATE,
)
from src.cleaner.headers import is_encoded_word, looks_like_mojibake

# Extensions the mainline extractor router currently SUPPORTS.
SUPPORTED_EXT = {".pdf", ".docx", ".txt", ".csv", ".log", ".md", ".rtf",
                 ".xlsx", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif",
                 ".tiff", ".webp"}
# Extensions the router SKIPS today (the Sprint 3 target).
KNOWN_UNSUPPORTED = {".doc", ".xls", ".msg", ".zip", ".ppt", ".pps",
                     ".eml", ".7z", ".rar"}


def section(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def scan(limit: int = 0) -> dict:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db = m.db
    out: dict = {}

    # ---- 1. Quoted-thread recall gap --------------------------------
    section("1. QUOTED-THREAD RECALL GAP")
    emails = db["emails"]
    proj = {"body_text": 1, "body_text_raw": 1}
    cur = emails.find({}, proj)
    if limit:
        cur = cur.limit(limit)
    docs = list(cur)
    # Fingerprints of the originals we already have (cleaned bodies).
    known = set()
    for d in docs:
        bt = d.get("body_text") or ""
        if bt.strip():
            known.add(quote_fingerprint(bt))
    n_with_tail = 0
    seg_total = 0
    seg_dup = 0
    seg_nondup = 0  # upper bound on novel/edited (needs fuzzy pass to split)
    for d in docs:
        raw = d.get("body_text_raw") or d.get("body_text") or ""
        _, tail = split_quoted_tail(raw)
        if not tail.strip():
            continue
        segs = iter_quoted_segments(tail)
        if not segs:
            continue
        n_with_tail += 1
        for seg in segs:
            seg_total += 1
            if quote_fingerprint(seg) in known:
                seg_dup += 1
            else:
                seg_nondup += 1
    out["quoted"] = {
        "emails_scanned": len(docs),
        "emails_with_quoted_tail": n_with_tail,
        "quoted_segments_total": seg_total,
        "segments_exact_duplicate": seg_dup,
        "segments_NOT_duplicate_recall_gap": seg_nondup,
    }
    print(f"  emails scanned................. {len(docs):,}")
    print(f"  emails with a quoted tail...... {n_with_tail:,}")
    print(f"  quoted segments total.......... {seg_total:,}")
    print(f"  - exact duplicates (skip)...... {seg_dup:,}")
    print(f"  - NOT duplicates (RECALL GAP).. {seg_nondup:,}")
    print("    ^ upper bound on quoted evidence not currently searchable;")
    print("      a fuzzy pass later splits this into novel vs edited-copy.")

    # ---- 2. RFC 2047 encoded subjects -------------------------------
    section("2. RFC 2047 ENCODED SUBJECTS (undecoded)")
    enc_n = 0
    examples = []
    for d in emails.find({}, {"subject": 1}):
        subj = d.get("subject") or ""
        if is_encoded_word(subj):
            enc_n += 1
            if len(examples) < 5:
                examples.append(subj[:80])
    out["encoded_subjects"] = {"count": enc_n, "examples": examples}
    print(f"  emails with encoded subject.... {enc_n:,}")
    for ex in examples:
        print(f"     e.g. {ex}")

    # ---- 3. Mojibake bodies -----------------------------------------
    section("3. MOJIBAKE BODIES (UTF-16/CJK garbage)")
    moji_n = 0
    moji_examples = []
    for d in emails.find({}, {"body_text": 1}):
        bt = d.get("body_text") or ""
        if bt and looks_like_mojibake(bt):
            moji_n += 1
            if len(moji_examples) < 3:
                moji_examples.append(str(d.get("_id")))
    out["mojibake"] = {"count": moji_n, "example_ids": moji_examples}
    print(f"  emails with mojibake body...... {moji_n:,}")

    # ---- 4. Extractor coverage --------------------------------------
    section("4. EXTRACTOR COVERAGE (attachments by extension)")
    att = db["attachments"]
    ext_counter = Counter()
    ext_empty = Counter()   # empty extracted_text by ext
    for d in att.find({}, {"extension": 1, "extracted_text": 1}):
        ext = (d.get("extension") or "").lower()
        ext_counter[ext] += 1
        txt = d.get("extracted_text") or ""
        if len(txt.strip()) < 10:
            ext_empty[ext] += 1
    unsupported = {e: n for e, n in ext_counter.items()
                   if e and e not in SUPPORTED_EXT}
    out["extensions"] = dict(ext_counter.most_common())
    out["unsupported_ext"] = unsupported
    out["empty_text_by_ext"] = dict(ext_empty.most_common())
    print("  by extension (top 20):")
    for e, n in ext_counter.most_common(20):
        flag = "" if e in SUPPORTED_EXT else "  <-- UNSUPPORTED" if e else ""
        empty = ext_empty.get(e, 0)
        print(f"     {e or '(none)':10s} {n:>6,}   empty_text={empty:<5} {flag}")
    print(f"\n  distinct unsupported-type attachments: "
          f"{sum(unsupported.values()):,} across {len(unsupported)} types")

    # attachments without a v2 counterpart (by _id reuse: v2 reuses _id)
    v2_ids = set(x["_id"] for x in db["attachments_v2"].find({}, {"_id": 1}))
    att_ids = set(x["_id"] for x in att.find({}, {"_id": 1}))
    missing_v2 = att_ids - v2_ids
    out["attachments_without_v2"] = len(missing_v2)
    print(f"  attachments with NO attachments_v2 row: {len(missing_v2):,}")

    # ---- 5. OCR gaps ------------------------------------------------
    section("5. OCR GAPS (attachments_v2 page methods)")
    v2 = db["attachments_v2"]
    method_counter = Counter()
    empty_docs = 0
    try:
        pipeline = [
            {"$unwind": "$extraction.pages"},
            {"$group": {"_id": "$extraction.pages.method", "n": {"$sum": 1}}},
        ]
        for r in v2.aggregate(pipeline, allowDiskUse=True):
            method_counter[r["_id"]] = r["n"]
    except Exception as exc:  # noqa: BLE001
        print("  (page-method aggregation unavailable:", exc, ")")
    for d in v2.find({}, {"extracted_text": 1}):
        if len((d.get("extracted_text") or "").strip()) < 10:
            empty_docs += 1
    bad_pages = sum(method_counter.get(k, 0)
                    for k in ("ocr_failed", "ocr_capped", "render_failed"))
    out["ocr"] = {
        "page_methods": dict(method_counter.most_common()),
        "bad_pages": bad_pages,
        "docs_with_empty_text": empty_docs,
    }
    print("  page methods:")
    for k, n in method_counter.most_common():
        mark = "  <-- GAP" if k in ("ocr_failed", "ocr_capped", "render_failed") else ""
        print(f"     {str(k):16s} {n:>8,}{mark}")
    print(f"  failed/capped/render-failed pages: {bad_pages:,}")
    print(f"  attachments_v2 with empty text...: {empty_docs:,}")

    # ---- 6. Chunk & graph health ------------------------------------
    section("6. CHUNK & GRAPH HEALTH")
    ch = db["email_chunks_v2"]
    total = ch.count_documents({})
    no_emb = ch.count_documents({"embedding.0": {"$exists": False}})
    no_ctx = ch.count_documents({"context": {"$in": [None, ""]}})
    no_ent = ch.count_documents({"entity_ids.0": {"$exists": False}})
    out["chunks"] = {
        "total": total, "missing_embedding": no_emb,
        "missing_context": no_ctx, "no_entity_link": no_ent,
    }
    print(f"  total chunks................... {total:,}")
    print(f"  missing embedding.............. {no_emb:,}")
    print(f"  missing context................ {no_ctx:,}")
    print(f"  no entity link................. {no_ent:,}")

    m.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str, default="")
    ap.add_argument("--limit", type=int, default=0,
                    help="limit emails scanned in section 1 (0 = all)")
    args = ap.parse_args()
    print("READ-ONLY missing-data scan. No writes will be performed.")
    out = scan(limit=args.limit)
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"\nfull report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
