"""Collapse duplicate change cards: same property + same source document -> keep one.

Keeps the highest-significance card (earliest on ties); the others are marked
``superseded`` rather than deleted, so the audit trail stays intact.
"""
from __future__ import annotations

import sys
from collections import defaultdict

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> int:
    coll = get_mongo().db["cards"]
    groups = defaultdict(list)
    for c in coll.find({"status": {"$in": ["new", "seen"]}}, {"card_id": 1, "property_id": 1, "source_sha": 1, "significance": 1, "created_at": 1}):
        groups[(c["property_id"], c["source_sha"])].append(c)
    superseded = 0
    for _, cards in groups.items():
        if len(cards) < 2:
            continue
        cards.sort(key=lambda c: (-int(c.get("significance") or 0), c["created_at"]))
        for dup in cards[1:]:
            coll.update_one({"card_id": dup["card_id"]}, {"$set": {"status": "superseded", "superseded_by": cards[0]["card_id"]}})
            superseded += 1
    print(f"  {superseded} duplicate cards superseded; {coll.count_documents({'status': 'new'})} live cards remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
