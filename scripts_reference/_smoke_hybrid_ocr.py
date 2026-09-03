"""Smoke test the hybrid RapidOCR + Claude Vision pipeline."""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor import extract_from_bytes
from src.extractor.claude_ocr import init_spend_guard, get_spend_guard


def main() -> int:
    s = Settings.load()
    init_spend_guard(s.ocr_vision_budget_usd)

    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    # Find a 2-5 MB PDF that has NOT been extracted yet — these are the ones
    # that previously broke RapidOCR.
    pipeline = [
        {"$match": {
            "sha256": {"$exists": True, "$ne": None},
            "filename": {"$regex": "\\.pdf$", "$options": "i"},
            "size_bytes": {"$gte": 1_000_000, "$lte": 5_000_000},
            "$or": [
                {"extraction.method": {"$exists": False}},
                {"extracted_text": {"$in": [None, ""]}},
            ],
        }},
        {"$group": {
            "_id": "$sha256",
            "filename": {"$first": "$filename"},
            "gridfs_id": {"$first": "$gridfs_id"},
            "size_bytes": {"$first": "$size_bytes"},
        }},
        {"$sort": {"size_bytes": -1}},
        {"$limit": 1},
    ]
    groups = list(m.attachments.aggregate(pipeline))
    if not groups:
        print("No medium-sized unextracted PDF found.")
        return 0

    g = groups[0]
    filename = g["filename"]
    size = int(g["size_bytes"])
    print(f"Smoke-testing hybrid pipeline on:")
    print(f"  filename: {filename}")
    print(f"  size:     {size/1024:.0f} KB")
    print(f"  vision:   enabled={s.ocr_vision_enabled} model={s.ocr_vision_model} min_pages={s.ocr_vision_min_pages}")
    print()

    buf = io.BytesIO()
    m.gridfs.download_to_stream(g["gridfs_id"], buf)
    data = buf.getvalue()

    t0 = time.time()
    result = extract_from_bytes(
        data, filename,
        ocr_lang=s.ocr_lang,
        ocr_min_chars=s.ocr_text_layer_min_chars,
        ocr_dpi=s.ocr_dpi,
        enable_ocr=True,
        vision_enabled=s.ocr_vision_enabled,
        vision_model=s.ocr_vision_model,
        vision_min_pages=s.ocr_vision_min_pages,
        vision_dpi=s.ocr_vision_dpi,
        vision_concurrency=s.ocr_vision_max_concurrency,
    )
    elapsed = time.time() - t0

    print(f"  method:           {result.method}")
    print(f"  pages:            {len(result.pages)}")
    print(f"  total chars:      {result.char_count}")
    print(f"  ocr_confidence:   {result.avg_ocr_confidence}")
    print(f"  elapsed:          {elapsed:.1f}s  ({elapsed/max(1,len(result.pages)):.1f}s/page)")
    if result.skipped_reason:
        print(f"  skipped reason:   {result.skipped_reason}")

    method_counts = {}
    for p in result.pages:
        method_counts[p.method] = method_counts.get(p.method, 0) + 1
    print(f"  per-page methods: {method_counts}")

    guard = get_spend_guard()
    if guard:
        print(f"  vision spend:     ${guard.spent:.4f}")

    if result.text:
        preview = result.text[:500].replace("\n", " | ")
        print(f"\n  preview (500 ch): {preview}")

    m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
