"""Do the declared filter fields actually exist on chunks, corpus-wide?

A filter declared in the Atlas index but missing from the documents does not
error — it silently matches nothing. That is the dangerous failure mode: a query
scoped by it returns an empty or wrong candidate set and looks like "no results"
rather than "broken filter".
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.index.vector_index import VECTOR_INDEX_DEFINITION
from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    chunks = mongo.chunks
    total = chunks.count_documents({})

    print(f"\n  chunks total {total:,}\n")
    print(f"  {'declared filter':<20} {'present':>9} {'non-empty':>10}   verdict")
    print("  " + "-" * 62)

    for field in VECTOR_INDEX_DEFINITION["fields"]:
        if field["type"] != "filter":
            continue
        path = field["path"]
        present = chunks.count_documents({path: {"$exists": True}})
        non_empty = chunks.count_documents(
            {path: {"$exists": True, "$nin": [None, "", []]}}
        )
        if non_empty == 0:
            verdict = "BROKEN - matches nothing"
        elif non_empty < total * 0.5:
            verdict = f"partial ({100 * non_empty // total}%)"
        else:
            verdict = "ok"
        print(f"  {path:<20} {present:>9,} {non_empty:>10,}   {verdict}")

    # Fields the reference project filters on that we store but do not index.
    print("\n  STORED BUT NOT FILTERABLE (candidates to add)")
    for path in ("date", "artifact_sha", "doc_class", "display_name", "token_count"):
        non_empty = chunks.count_documents({path: {"$exists": True, "$nin": [None, "", []]}})
        print(f"    {path:<20} {non_empty:>9,} chunks carry a value")
    print()


if __name__ == "__main__":
    main()
