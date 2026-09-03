"""Characterise the two hard failures the post-run verification reported.

Both need a severity judgement before anyone decides whether to re-chunk:

* multi-property chunks — a chunk tagged with two properties is only a leak if
  the *artifact* it came from does not genuinely concern both. An email covering
  two deals legitimately produces such chunks; an attachment that inherited two
  properties because its parent was ambiguous does not.
* oversized chunks — whether the ceiling is breached by a long tail or by a
  systematic offset decides whether this is a bug or a tuning nit.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.chunk.tokens import count_tokens
from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    chunks, art = mongo.chunks, mongo.artifacts

    multi = list(chunks.find(
        {"property_ids.1": {"$exists": True}},
        {"chunk_id": 1, "property_ids": 1, "artifact_sha": 1, "source_type": 1},
    ))
    print(f"\n  MULTI-PROPERTY CHUNKS  {len(multi):,}")

    pairs = Counter(tuple(sorted(c.get("property_ids") or [])) for c in multi)
    print("    most common combinations")
    for combo, n in pairs.most_common(6):
        print(f"      {' + '.join(combo):<34} {n:>5,}")

    by_source = Counter(c.get("source_type") for c in multi)
    print(f"    by source            {dict(by_source)}")

    # Does the parent artifact itself claim both properties? If yes the chunk is
    # faithful to its document and the tagging is not a leak.
    shas = {c.get("artifact_sha") for c in multi if c.get("artifact_sha")}
    parents = {
        d["sha256"]: d
        for d in art.find({"sha256": {"$in": list(shas)}},
                          {"sha256": 1, "property_ids": 1, "segregation": 1})
    }
    faithful = inherited = 0
    for chunk in multi:
        parent = parents.get(chunk.get("artifact_sha")) or {}
        if set(chunk.get("property_ids") or []) <= set(parent.get("property_ids") or []):
            faithful += 1
            if (parent.get("segregation") or {}).get("fallback_used") == "inherited_from_email":
                inherited += 1
    print(f"    faithful to parent   {faithful:>5,} / {len(multi):,}")
    print(f"      of those inherited {inherited:>5,}")
    print(f"    distinct documents   {len(shas):>5,}")

    print("\n  CHUNK SIZE (ceiling 1200)")
    sizes = []
    for doc in chunks.find({}, {"text": 1, "context": 1}).limit(4000):
        sizes.append(count_tokens(doc.get("text") or ""))
    sizes.sort()
    over = [s for s in sizes if s > 1200]
    print(f"    sampled              {len(sizes):>5,}")
    print(f"    mean                 {sum(sizes) // max(1, len(sizes)):>5,}")
    print(f"    p95                  {sizes[int(len(sizes) * 0.95)]:>5,}")
    print(f"    max                  {max(sizes):>5,}")
    print(f"    over ceiling         {len(over):>5,}  ({100 * len(over) / max(1, len(sizes)):.1f}%)")
    print()


if __name__ == "__main__":
    main()
