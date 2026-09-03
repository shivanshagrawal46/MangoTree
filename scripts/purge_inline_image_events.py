"""Remove timeline events sourced from email signature images.

The deterministic pass creates one event per (artifact, property), and inline
images carry property ids like any other attachment — so a signature logo became
a dated entry on a property's chronology. One of them was assigned to twelve
properties, which put a meaningless event on twelve timelines.

Run after any timeline build that predates the ``is_inline_image`` exclusion.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    events = mongo.db["timeline_events"]

    shas = mongo.artifacts.distinct("sha256", {"is_inline_image": True})
    print(f"\n  inline-image artifacts     {len(shas):>6,}")

    before = events.count_documents({})
    doomed = events.count_documents({"source_sha": {"$in": shas}})
    affected = len(events.distinct("property_id", {"source_sha": {"$in": shas}}))
    print(f"  events they produced       {doomed:>6,}")
    print(f"  property timelines touched {affected:>6,}")

    result = events.delete_many({"source_sha": {"$in": shas}})
    print(f"\n  removed                    {result.deleted_count:>6,}")
    print(f"  events {before:,} -> {events.count_documents({}):,}\n")


if __name__ == "__main__":
    main()
