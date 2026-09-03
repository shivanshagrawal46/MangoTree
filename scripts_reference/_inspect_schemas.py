"""Quick schema introspection for emails / attachments / chunks."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def show(coll, name):
    print(f"\n=== {name} ===")
    print(f"count: {coll.estimated_document_count():,}")
    doc = coll.find_one()
    if not doc:
        print("(empty)")
        return
    for k, v in doc.items():
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            print(f"  {k}: list[{len(v)}] (numeric)")
        elif isinstance(v, str) and len(v) > 100:
            print(f"  {k}: str ({len(v)} chars)  -> {v[:80]!r}...")
        elif isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        elif isinstance(v, dict):
            print(f"  {k}: dict keys={list(v.keys())[:8]}")
        else:
            print(f"  {k}: {type(v).__name__} = {v!r}"[:120])


def main():
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    show(m.emails, "emails")
    show(m.attachments, "attachments")
    show(m.chunks, "email_chunks (chunks)")
    # Distinct values of "kind"
    print("\nchunk 'kind' values:", m.chunks.distinct("kind"))


if __name__ == "__main__":
    main()
