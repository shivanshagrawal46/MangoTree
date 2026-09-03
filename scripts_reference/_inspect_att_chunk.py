"""Show one full attachment chunk so we know how to join."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main():
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    doc = m.chunks.find_one({"source_type": "attachment"})
    if not doc:
        print("no attachment chunk found")
        return
    for k, v in doc.items():
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            print(f"  {k}: list[{len(v)}] (numeric)")
        elif isinstance(v, str) and len(v) > 100:
            print(f"  {k}: str ({len(v)} chars)  -> {v[:80]!r}...")
        elif isinstance(v, list):
            print(f"  {k}: list[{len(v)}] = {v[:3]!r}")
        elif isinstance(v, dict):
            print(f"  {k}: dict keys={list(v.keys())}")
        else:
            print(f"  {k}: {type(v).__name__} = {v!r}"[:120])


if __name__ == "__main__":
    main()
