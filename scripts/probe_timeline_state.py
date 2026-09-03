"""Has the timeline ever been built, and what would it be built from?

``mangotree/timeline`` exists and has a CLI command, but code existing is not
the same as data existing. If the events collection is empty the timeline is a
build task; if it is populated it is a refresh task.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.registry import PROPERTIES
from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()

    print("\n  COLLECTIONS PRESENT")
    for name in sorted(mongo.db.list_collection_names()):
        print(f"    {name:<24} {mongo.db[name].count_documents({}):>8,}")

    print(f"\n  REGISTERED PROPERTIES  {len(PROPERTIES)}")

    # What a timeline could be built from today: dated, property-tagged chunks.
    print("\n  DATED + PROPERTY-TAGGED MATERIAL PER PROPERTY")
    print(f"    {'property':<18} {'artifacts':>10} {'chunks':>8} {'dated':>8}")
    for prop in PROPERTIES:
        pid = prop.property_id
        arts = mongo.artifacts.count_documents({"property_ids": pid})
        chunks = mongo.chunks.count_documents({"property_ids": pid})
        dated = mongo.chunks.count_documents({"property_ids": pid, "date": {"$ne": None}})
        print(f"    {pid:<18} {arts:>10,} {chunks:>8,} {dated:>8,}")
    print()


if __name__ == "__main__":
    main()
