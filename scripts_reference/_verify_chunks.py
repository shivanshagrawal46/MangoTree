"""Verify email_chunks collection state."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main() -> int:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    total = m.chunks.count_documents({})
    by_type = list(m.chunks.aggregate([
        {"$group": {"_id": "$source_type", "count": {"$sum": 1}}},
    ]))

    print(f"Total chunks indexed: {total:,}")
    for r in by_type:
        print(f"  {r['_id']}: {r['count']:,}")
    print()

    # Sample one chunk and validate fields
    samp = m.chunks.find_one({"source_type": "email_body"})
    if samp:
        print("Sample email_body chunk:")
        print(f"  _id:              {samp.get('_id')}")
        print(f"  email_id:         {samp.get('email_id')}")
        print(f"  source_hash:      {samp.get('source_hash')[:16]}...")
        print(f"  chunk_index:      {samp.get('chunk_index')}")
        print(f"  n_tokens:         {samp.get('n_tokens')}")
        print(f"  date:             {samp.get('date')}")
        print(f"  from_email:       {samp.get('from_email')}")
        print(f"  subject:          {samp.get('subject')}")
        emb = samp.get("embedding")
        print(f"  embedding length: {len(emb) if emb else 'MISSING'}")
        if emb:
            print(f"  embedding sample: [{emb[0]:.4f}, {emb[1]:.4f}, {emb[2]:.4f}, ...]")
        print(f"  text preview:")
        print("    " + (samp.get("text") or "")[:300].replace("\n", " | "))

    m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
