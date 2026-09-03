"""Stamp existing change-detection cards with the date of their source document."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from pymongo import UpdateOne

from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    cards = list(mongo.db["cards"].find({"source_date": {"$exists": False}}, {"card_id": 1, "source_sha": 1, "created_at": 1}))
    dates = {a["sha256"]: a.get("date") for a in mongo.artifacts.find({"sha256": {"$in": [c["source_sha"] for c in cards]}}, {"sha256": 1, "date": 1})}
    ops = [UpdateOne({"card_id": c["card_id"]}, {"$set": {"source_date": dates.get(c["source_sha"]) or c["created_at"]}}) for c in cards]
    if ops:
        mongo.db["cards"].bulk_write(ops)
    print(f"  {len(ops)} cards stamped with source_date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
