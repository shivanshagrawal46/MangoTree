"""Quick live check of extraction progress."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main():
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    total_extracted = m.attachments.count_documents({"extraction.method": {"$exists": True}})
    total = m.attachments.count_documents({"sha256": {"$exists": True}})

    # Unique by sha256
    unique_total = len(m.attachments.distinct("sha256", {"sha256": {"$exists": True, "$ne": None}}))
    unique_extracted = len(m.attachments.distinct(
        "sha256",
        {"sha256": {"$exists": True, "$ne": None}, "extraction.method": {"$exists": True}},
    ))

    by_method = list(m.attachments.aggregate([
        {"$match": {"extraction.method": {"$exists": True}}},
        {"$group": {"_id": "$extraction.method", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]))

    five_min_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)
    recent = m.attachments.count_documents({"extraction.extracted_at": {"$gte": five_min_ago}})

    last = list(m.attachments.find(
        {"extraction.extracted_at": {"$exists": True}},
        {"filename": 1, "extraction.method": 1, "extraction.extracted_at": 1, "extraction.char_count": 1, "size_bytes": 1},
    ).sort("extraction.extracted_at", -1).limit(8))

    print(f"Unique (sha256) extracted: {unique_extracted}/{unique_total}")
    print(f"Total attachment docs extracted: {total_extracted}/{total}")
    print(f"Extracted in last 15 min: {recent}")
    print("\nBy method:")
    for row in by_method:
        print(f"  {row['_id']:<14} {row['n']}")
    print("\nMost recent 8:")
    for d in last:
        ts = d['extraction'].get('extracted_at')
        method = d['extraction'].get('method')
        chars = d['extraction'].get('char_count') or 0
        size = (d.get('size_bytes') or 0) / 1024
        print(f"  {ts}  {method:<10}  size={size:>6.0f}KB  chars={chars:>6}  {d.get('filename','?')[:60]}")

    m.close()


if __name__ == "__main__":
    main()
