"""Apply the widened filter set to the Atlas vector index.

No re-embedding: the 12,800 vectors are untouched. Atlas rebuilds the index
structure so the new fields become filterable during the approximate-nearest-
neighbour scan rather than after it.

Verifies afterwards that every declared filter is actually populated on the
chunks — a filter with no data behind it does not error, it silently matches
nothing, which is how the ``scope`` field went unnoticed.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from mangotree.index.vector_index import (
    VECTOR_INDEX_DEFINITION,
    VECTOR_INDEX_NAME,
    create_vector_index,
)
from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    chunks = mongo.chunks
    total = chunks.count_documents({})

    filters = [f["path"] for f in VECTOR_INDEX_DEFINITION["fields"] if f["type"] == "filter"]
    print(f"\n  chunks {total:,} · declaring {len(filters)} filter fields\n")

    print("  COVERAGE BEFORE REBUILD")
    empty = []
    for path in filters:
        n = chunks.count_documents({path: {"$exists": True, "$nin": [None, "", []]}})
        flag = ""
        if n == 0:
            flag = "   <- NOTHING BEHIND IT"
            empty.append(path)
        print(f"    {path:<20} {n:>7,} / {total:,}{flag}")

    if empty:
        print(f"\n  refusing to declare filters with no data: {empty}")
        return 1

    print("\n  updating index definition...")
    create_vector_index(mongo, update=True, wait=False)

    deadline = time.time() + 900
    while time.time() < deadline:
        info = next(iter(chunks.list_search_indexes(VECTOR_INDEX_NAME)), None)
        if info and info.get("queryable") and info.get("status") == "READY":
            print(f"  index READY and queryable")
            break
        print(f"    status={info.get('status') if info else '?'}")
        time.sleep(15)
    else:
        print("  timed out waiting for the index to become queryable")
        return 1

    print("\n  LIVE FILTER TEST")
    for path, value in (
        ("scope", "property"),
        ("date_year", 2025),
        ("extension", ".pdf"),
    ):
        n = chunks.count_documents({path: value})
        print(f"    {path} == {value!r:<12} matches {n:,} chunks")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
