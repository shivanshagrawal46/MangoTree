"""What the 25 unextracted disk files are.

Video was explicitly out of scope, so some of these are expected. Anything that
is not video is a gap worth knowing about before the corpus is called complete.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    artifacts = get_mongo().artifacts

    kinds = Counter()
    names = []
    for doc in artifacts.find(
        {"source_type": "disk_file", "extraction": {"$exists": False}},
        {"filename": 1, "extension": 1, "kind": 1, "content_type": 1},
    ):
        ext = (doc.get("extension") or "?").lower()
        kinds[ext] += 1
        if len(names) < 30:
            names.append(f"{ext:<8} {str(doc.get('filename'))[:56]}")

    print(f"\n  unextracted disk files by extension:")
    for ext, count in kinds.most_common():
        print(f"      {ext:<10} {count:>4}")

    print("\n  files:")
    for name in names:
        print(f"      {name}")
    print()


if __name__ == "__main__":
    main()
