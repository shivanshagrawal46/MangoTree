"""Write ``placement`` onto every artifact and chunk.

One token that says where an item stands — property / portfolio / unplaced /
business — so both search indexes can filter on it with a single equals clause.
Derived from fields the pipeline already writes; safe to re-run at any time.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    art, chunks = mongo.artifacts, mongo.chunks

    rules = [
        ("property", {"property_ids": {"$exists": True, "$ne": []}}),
        ("portfolio", {"$or": [{"property_ids": {"$exists": False}}, {"property_ids": []}],
                       "common_kind": "portfolio"}),
        ("business", {"$or": [{"property_ids": {"$exists": False}}, {"property_ids": []}],
                      "common_kind": "business"}),
        ("unplaced", {"$or": [{"property_ids": {"$exists": False}}, {"property_ids": []}],
                      "common_kind": {"$exists": False}}),
    ]

    print("\n  PLACEMENT BACKFILL")
    for name, query in rules:
        a = art.update_many(query, {"$set": {"placement": name}})
        c = chunks.update_many(query, {"$set": {"placement": name}})
        print(f"    {name:<10} artifacts={a.matched_count:>6,}  chunks={c.matched_count:>6,}")

    missing = chunks.count_documents({"placement": {"$exists": False}})
    print(f"    chunks without placement: {missing:,}   <- must be 0\n")
    return 0 if missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
