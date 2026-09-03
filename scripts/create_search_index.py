"""Create or update the Atlas Search (Lucene) index on chunks, then prove it works.

Usage:
    python scripts/create_search_index.py            # create if absent, wait
    python scripts/create_search_index.py --update   # re-apply definition
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from mangotree.retrieve.search_index import (
    SEARCH_INDEX_NAME,
    SYNONYM_SET,
    create_search_index,
    search_index_status,
)
from mangotree.storage.mongo import get_mongo


def _probe(mongo, label: str, operator: dict, limit: int = 3) -> None:
    pipeline = [
        {"$search": {"index": SEARCH_INDEX_NAME, **operator}},
        {"$limit": limit},
        {"$project": {"_id": 0, "display_name": 1, "property_ids": 1,
                      "score": {"$meta": "searchScore"}}},
    ]
    try:
        rows = list(mongo.chunks.aggregate(pipeline))
    except Exception as exc:
        print(f"    {label:<34} ERROR {exc}")
        return
    print(f"    {label:<34} {len(rows)} hit(s)")
    for row in rows:
        print(f"        [{row['score']:.2f}] {str(row.get('display_name'))[:60]}  {row.get('property_ids')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()

    mongo = get_mongo()
    state = create_search_index(mongo, update=args.update, wait=True)
    print(f"\n  index '{SEARCH_INDEX_NAME}': {state}  {search_index_status(mongo)}")

    print("\n  LIVE PROBES")
    _probe(mongo, "bm25: payoff statement", {
        "text": {"query": "payoff statement", "path": ["text", "context"]}})
    _probe(mongo, "fuzzy: 'Bayshor' (OCR typo)", {
        "text": {"query": "Bayshor", "path": ["text", "context", "display_name"],
                 "fuzzy": {"maxEdits": 1, "prefixLength": 3}}})
    _probe(mongo, "phrase: 'deed of trust'", {
        "phrase": {"query": "deed of trust", "path": "text"}})
    _probe(mongo, "synonym: 'mortgage' -> deed of trust", {
        "text": {"query": "mortgage", "path": {"value": "text", "multi": "standard"}, "synonyms": SYNONYM_SET}})
    _probe(mongo, "filtered: chita_ct + 'draw'", {
        "compound": {
            "must": [{"text": {"query": "draw", "path": "text"}}],
            "filter": [{"equals": {"path": "property_ids", "value": "chita_ct"}}],
        }})
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
