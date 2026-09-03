"""
Audit `attachments_v2` for gaps. We need ZERO missing pages for the legal
RAG use case. This script enumerates every category of failure:

 1. SKIPPED attachments  — unsupported file types we never tried to OCR.
 2. CONTENT-FILTER blocked pages — pages Anthropic refused (fell back to
    RapidOCR but we want to verify the fallback actually produced text).
 3. EMPTY pages — pages with text == "" for ANY reason (render failure,
    OCR failure, content filter w/ no RapidOCR result, etc.).
 4. DOCS with at least one empty page.
 5. Per-extension breakdown of the skipped set, so we can decide what to
    rescue (e.g. .eml, .msg, .heic, .rtf, .htm).

It's READ-ONLY; safe to run any time.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def _ext(name: str) -> str:
    if not name or "." not in name:
        return "(no-ext)"
    return name.rsplit(".", 1)[-1].lower()


def main() -> None:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    coll = m.db["attachments_v2"]

    # =====================================================================
    # 1. SKIPPED attachments
    # =====================================================================
    print("=" * 78)
    print("1. SKIPPED FILES  (extraction.method == 'skipped')")
    print("=" * 78)
    skipped_total = coll.count_documents({"extraction.method": "skipped"})
    skipped_unique = len(coll.distinct("sha256", {"extraction.method": "skipped"}))
    print(f"  rows: {skipped_total},  unique sha256: {skipped_unique}")

    # Skipped reason breakdown
    reasons = Counter()
    ext_counter = Counter()
    samples_per_ext: dict[str, list] = {}
    seen_sha = set()
    for d in coll.find(
        {"extraction.method": "skipped"},
        {"filename": 1, "extraction.skipped_reason": 1, "sha256": 1, "size_bytes": 1},
    ):
        if d["sha256"] in seen_sha:
            continue
        seen_sha.add(d["sha256"])
        reasons[d.get("extraction", {}).get("skipped_reason", "(none)")] += 1
        ext = _ext(d.get("filename", ""))
        ext_counter[ext] += 1
        samples_per_ext.setdefault(ext, []).append(
            (d.get("filename", ""), d.get("size_bytes", 0))
        )
    print()
    print("  by skipped_reason (unique sha256):")
    for r, n in reasons.most_common():
        print(f"    {r:<30}: {n}")
    print()
    print("  by extension (unique sha256):")
    for ext, n in ext_counter.most_common():
        sizes = [s for _, s in samples_per_ext[ext]]
        total_mb = sum(sizes) / 1_048_576 if sizes else 0
        sample_names = ", ".join(f for f, _ in samples_per_ext[ext][:2])
        print(f"    .{ext:<8}: {n:>4}  (total {total_mb:>6.1f} MB)  e.g. {sample_names}")

    # =====================================================================
    # 2 + 3. EMPTY PAGES across non-skipped docs
    # =====================================================================
    print()
    print("=" * 78)
    print("2. EMPTY PAGES inside non-skipped docs")
    print("=" * 78)

    pipe_methods = ["pdf_text", "pdf_ocr", "pdf_mixed", "image_ocr", "docx", "xlsx", "raw_text"]
    empty_pages = 0
    blocked_pages = 0
    docs_with_any_empty_page = 0
    docs_with_only_empty_pages = 0
    page_method_counter = Counter()
    affected_docs_unique_sha = set()
    blocked_docs_unique_sha = set()

    cur = coll.find(
        {"extraction.method": {"$in": pipe_methods}},
        {
            "sha256": 1,
            "filename": 1,
            "extraction.method": 1,
            "extraction.pages": 1,
        },
    )
    for d in cur:
        pages = d.get("extraction", {}).get("pages", []) or []
        if not pages:
            continue  # docx/xlsx/raw_text — no per-page detail
        has_empty = False
        all_empty = True
        for p in pages:
            mth = p.get("method", "")
            page_method_counter[mth] += 1
            txt = p.get("text", "") or ""
            if not txt.strip():
                empty_pages += 1
                has_empty = True
                if mth in ("vision_failed", "ocr_failed", "render_failed",
                           "vision_skipped_budget", "content_filter"):
                    blocked_pages += 1
                    blocked_docs_unique_sha.add(d["sha256"])
            else:
                all_empty = False
        if has_empty:
            docs_with_any_empty_page += 1
            affected_docs_unique_sha.add(d["sha256"])
        if all_empty and pages:
            docs_with_only_empty_pages += 1

    print(f"  Total pages observed (in docs that report per-page data): "
          f"{sum(page_method_counter.values()):,}")
    print(f"  Empty pages (text=='' for any reason): {empty_pages}")
    print(f"  Blocked / failed pages (method == failure tag): {blocked_pages}")
    print(f"  Docs with >=1 empty page: {docs_with_any_empty_page} "
          f"(unique sha256: {len(affected_docs_unique_sha)})")
    print(f"  Docs with ALL pages empty: {docs_with_only_empty_pages}")
    print(f"  Unique sha256 with >=1 blocked page: {len(blocked_docs_unique_sha)}")

    print()
    print("  Page method distribution:")
    for mth, n in page_method_counter.most_common():
        print(f"    {mth:<24}: {n:>6}")

    # =====================================================================
    # 4. List of unique docs that lost content
    # =====================================================================
    print()
    print("=" * 78)
    print("3. DOCS WITH ANY EMPTY PAGE  (sample)")
    print("=" * 78)
    sample = list(coll.find(
        {"sha256": {"$in": list(affected_docs_unique_sha)}},
        {"sha256": 1, "filename": 1, "extraction.method": 1,
         "extraction.page_count": 1, "extraction.char_count": 1},
    ).limit(20))
    seen2 = set()
    for d in sample:
        if d["sha256"] in seen2:
            continue
        seen2.add(d["sha256"])
        print(f"  sha256={d['sha256'][:12]}  pages={d.get('extraction', {}).get('page_count', 0):>3}  "
              f"chars={d.get('extraction', {}).get('char_count', 0):>7}  "
              f"method={d.get('extraction', {}).get('method', ''):<10}  "
              f"file={d.get('filename', '')[:60]}")

    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  Skipped unique binaries (no text at all):  {skipped_unique}")
    print(f"  Unique binaries with at least 1 empty page: "
          f"{len(affected_docs_unique_sha)}")
    print(f"  Unique binaries with at least 1 blocked/failed page: "
          f"{len(blocked_docs_unique_sha)}")
    print(f"  Empty pages overall: {empty_pages}")
    print(f"  Of those, content-filter / vision-failed: {blocked_pages}")


if __name__ == "__main__":
    main()
