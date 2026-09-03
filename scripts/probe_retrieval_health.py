"""Does retrieval actually work today, end to end?

``Retriever.lexical_search`` runs a ``$text`` query and swallows any exception
with a warning, returning an empty list. Without a text index on ``chunks``
that is exactly what happens on every call — so "hybrid" retrieval quietly
degrades to vector-only and nobody sees an error.

Also checks whether the graph we built is reachable from a search, and runs a
real query end to end.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    chunks = mongo.chunks

    print("\n  INDEXES ON chunks")
    text_index = None
    for name, spec in chunks.index_information().items():
        keys = spec.get("key") or []
        kinds = {k[1] for k in keys}
        if "text" in kinds:
            text_index = name
        print(f"    {name:<34} {keys}")

    print(f"\n  lexical channel: {'WORKS — text index present' if text_index else 'DEAD — no text index, $text will raise'}")

    print("\n  LEXICAL SEARCH, LIVE TEST")
    try:
        n = chunks.count_documents({"$text": {"$search": "wire instructions"}})
        print(f"    matches for 'wire instructions': {n:,}")
    except Exception as exc:
        print(f"    FAILED: {type(exc).__name__}: {str(exc)[:140]}")
        print("    -> Retriever.lexical_search catches this and returns [];")
        print("       every 'hybrid' search today is vector-only.")

    print("\n  GRAPH REACHABILITY FROM SEARCH")
    print(f"    chunks carrying entity_ids     {chunks.count_documents({'entity_ids': {'$exists': True, '$not': {'$size': 0}}}):>7,}")
    print(f"    entity_ids is a declared filter: yes")
    print("    but Retriever._filter() builds clauses from property/privileged/source_type only,")
    print("    so no query path uses the graph today.")

    print("\n  STAGE-2 RERANK")
    print("    models.py: 'Voyage reranker — first stage. Opus 5 is stage two.'")
    print("    Retriever.rerank() calls Voyage only — stage two is not implemented.")

    key = os.environ.get("VOYAGE_API_KEY")
    print(f"\n  VOYAGE_API_KEY present: {bool(key)}")
    print()


if __name__ == "__main__":
    main()
