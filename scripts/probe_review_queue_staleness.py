"""How much of the review queue is still real work?

The queue holds entries from two different eras: the deterministic resolver that
ran at ingest time, and Opus 5 segregation that ran after it. An artifact the
resolver could not place may since have been placed by the model, in which case
its queue entry is answered and should not cost anyone a decision.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    live = stale = orphan = 0
    by_kind_stale: Counter = Counter()

    for entry in mongo.review_queue.find({}, {"artifact_sha": 1, "kind": 1, "reference": 1}):
        sha = entry.get("artifact_sha") or (entry.get("reference") or "").replace("attachment:", "")
        if not sha or sha.startswith("disk::"):
            orphan += 1
            continue
        doc = art.find_one({"sha256": sha}, {"property_ids": 1, "segregation": 1}) or {}
        if not doc:
            orphan += 1
        elif doc.get("property_ids") and doc.get("segregation"):
            stale += 1
            by_kind_stale[entry.get("kind")] += 1
        else:
            live += 1

    print("\n  REVIEW QUEUE")
    print(f"    answered since queueing (stale) {stale:>6,}")
    print(f"    still needing a human           {live:>6,}")
    print(f"    no artifact / disk-path key     {orphan:>6,}")
    print("\n    stale by kind")
    for kind, n in by_kind_stale.most_common():
        print(f"      {str(kind):<28} {n:>6,}")

    print("\n  ENTITY LINKAGE (exists-and-non-empty, not just $ne)")
    linked = mongo.chunks.count_documents(
        {"entity_ids": {"$exists": True, "$not": {"$size": 0}}}
    )
    total = mongo.chunks.count_documents({})
    print(f"    chunks with entities           {linked:>6,} / {total:,}")
    print()


if __name__ == "__main__":
    main()
