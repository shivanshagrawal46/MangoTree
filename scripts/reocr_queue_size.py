"""How much work the recovery stage has left, and where the blanks sit in it.

The stage walks every document that has vision pages and re-reads the ones that
are blocked or low-confidence. If the documents holding the genuinely blank
pages sit late in that walk, the outstanding count stays flat for a long time
while real work is happening — which looks identical to a stall.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.models import OCR as OCR_CFG
from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    floor = OCR_CFG.confidence_floor

    total = with_targets = 0
    position_of_blank_docs = []

    cursor = mongo.artifacts.find(
        {"extraction.detail.vision_pages": {"$exists": True}},
        {"filename": 1, "extraction.detail.vision_pages": 1},
    )

    for doc in cursor:
        total += 1
        pages = ((doc.get("extraction") or {}).get("detail") or {}).get("vision_pages") or []
        targets = [
            p for p in pages
            if p.get("blocked") or (p.get("confidence") or 0) < floor
        ]
        if targets:
            with_targets += 1
            hard = [p for p in pages if p.get("blocked") and p.get("needs_human")]
            if hard:
                position_of_blank_docs.append(
                    (with_targets, len(hard), str(doc.get("filename", "?"))[:46])
                )

    print(f"\n  confidence floor                {floor}")
    print(f"  documents with vision pages     {total:>6}")
    print(f"  documents the stage will visit  {with_targets:>6}")

    if position_of_blank_docs:
        print("\n  where the genuinely blank documents sit in that walk:")
        for position, count, name in position_of_blank_docs:
            print(f"      #{position:<5} {count:>3} blank page(s)  {name}")
    print()


if __name__ == "__main__":
    main()
