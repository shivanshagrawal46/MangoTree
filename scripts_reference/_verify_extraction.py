"""Quick check: how does extraction look in MongoDB?"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main() -> int:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    print("Total attachment rows with extracted_text:",
          m.attachments.count_documents({"extracted_text": {"$nin": [None, ""]}}))
    print("Total attachment rows with extraction.method:",
          m.attachments.count_documents({"extraction.method": {"$exists": True}}))
    print()

    print("Method breakdown:")
    for r in m.attachments.aggregate([
        {"$match": {"extraction.method": {"$exists": True}}},
        {"$group": {"_id": "$extraction.method", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]):
        print(f"  {r['_id']}: {r['count']}")
    print()

    for method in ["pdf_text", "pdf_ocr", "docx", "image_ocr"]:
        samp = m.attachments.find_one(
            {"extraction.method": method},
            {"filename": 1, "extension": 1, "extracted_text": 1,
             "extraction.method": 1, "extraction.page_count": 1,
             "extraction.avg_ocr_confidence": 1},
        )
        if samp:
            print(f"--- {method} sample ---")
            print(f"  filename:   {samp['filename']}")
            print(f"  page_count: {samp['extraction']['page_count']}")
            if samp['extraction'].get('avg_ocr_confidence'):
                print(f"  ocr_conf:   {samp['extraction']['avg_ocr_confidence']:.3f}")
            preview = samp["extracted_text"][:500].replace("\n", " | ")
            print(f"  text ({len(samp['extracted_text'])} chars): {preview}")
            print()

    m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
