"""Smoke test the new RapidOCR backend on real corpus attachments."""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ["OCR_ENGINE"] = "rapidocr"

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor import extract_from_bytes


def main() -> int:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    # Pick a few small-to-medium UNEXTRACTED attachments (~50KB-2MB).
    pipeline = [
        {"$match": {
            "sha256": {"$exists": True, "$ne": None},
            "size_bytes": {"$gte": 50_000, "$lte": 2_000_000},
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
        {"$sort": {"size_bytes": 1}},
        {"$limit": 3},
    ]
    groups = list(m.attachments.aggregate(pipeline))
    if not groups:
        print("No unextracted attachments found.")
        return 0

    print(f"Smoke testing RapidOCR on {len(groups)} attachments:\n")
    for g in groups:
        filename = g.get("filename") or "?"
        size = int(g.get("size_bytes") or 0)
        print(f"--- {filename}  ({size/1024:.0f} KB) ---")
        try:
            buf = io.BytesIO()
            m.gridfs.download_to_stream(g["gridfs_id"], buf)
            data = buf.getvalue()
        except Exception as exc:
            print(f"  GridFS read failed: {exc}")
            continue

        t0 = time.time()
        result = extract_from_bytes(
            data, filename,
            ocr_lang=s.ocr_lang,
            ocr_min_chars=s.ocr_text_layer_min_chars,
            ocr_dpi=s.ocr_dpi,
            enable_ocr=True,
        )
        elapsed = time.time() - t0

        print(f"  method:     {result.method}")
        print(f"  pages:      {len(result.pages)}")
        print(f"  chars:      {result.char_count}")
        print(f"  ocr_conf:   {result.avg_ocr_confidence}")
        print(f"  elapsed:    {elapsed:.1f}s  ({elapsed/max(1,len(result.pages)):.1f}s/page)")
        if result.skipped_reason:
            print(f"  skipped:    {result.skipped_reason}")
        if result.text:
            preview = result.text[:300].replace("\n", " | ")
            print(f"  preview:    {preview}")
        print()

    m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
