"""What does a chunk actually carry, and how much of it is filterable?

A field stored on the chunk but absent from the Atlas index definition can be
read back once a result is returned, but it cannot narrow the search itself.
For a date that is the whole difference between "search March 2025" and "search
everything, then throw away what is not March 2025 after the fact".
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.index.vector_index import VECTOR_INDEX_DEFINITION
from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()

    present: Counter = Counter()
    total = 0
    for doc in mongo.chunks.find({}, {"embedding": 0}).limit(600):
        total += 1
        for key, value in doc.items():
            if value not in (None, "", []):
                present[key] += 1

    indexed = {
        f["path"] for f in VECTOR_INDEX_DEFINITION["fields"] if f["type"] == "filter"
    }

    print(f"\n  sampled {total} chunks\n")
    print(f"  {'field':<26} {'populated':>10}   filterable?")
    print("  " + "-" * 56)
    for field, count in present.most_common():
        if field in ("_id", "embedding"):
            continue
        mark = "yes" if field in indexed else "-- stored only --"
        print(f"  {field:<26} {count:>10,}   {mark}")

    missing = indexed - set(present)
    if missing:
        print(f"\n  declared as filter but never populated: {sorted(missing)}")
    print()


if __name__ == "__main__":
    main()
