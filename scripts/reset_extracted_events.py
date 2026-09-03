"""Drop timeline events read by a model other than the current one.

Mixing extractors across one timeline is worse than it sounds: the events sit
side by side in the same chronology with no visible difference, so a stretch
read by a weaker model looks exactly as authoritative as the rest. Whichever
model is in the ANALYST seat should have read all of it.

Deterministic events are never touched — they are derived from the artifact, not
read, and they are what guarantees coverage.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.models import Seat, model_for
from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    events = mongo.db["timeline_events"]
    current = model_for(Seat.ANALYST)

    print(f"\n  current extractor  {current}")
    print("\n  events by extractor")
    for row in events.aggregate([
        {"$group": {"_id": "$extracted_by", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"    {str(row['_id']):<24} {row['n']:>6,}")

    stale = events.delete_many(
        {"extracted_by": {"$nin": ["deterministic", current]}}
    )
    print(f"\n  removed stale-model events {stale.deleted_count:>6,}")
    print(f"  events remaining           {events.count_documents({}):>6,}")
    print()


if __name__ == "__main__":
    main()
