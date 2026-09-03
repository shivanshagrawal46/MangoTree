"""Add retrieval metadata to chunks written before those fields existed.

Cheap by design: none of this touches the embedding. The 12,800 vectors stay
exactly as they are, so this is a metadata update and an index rebuild, not a
re-embed. That distinction is what makes adding filter fields an hour's work
rather than a day's.

Works artifact by artifact rather than chunk by chunk, because every chunk of
one document shares the same parent metadata — computing it once per document
turns ~12,800 derivations into ~4,900.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

sys.path.insert(0, ".")

from pymongo import UpdateMany

from mangotree.index.metadata import chunk_metadata, occurrences_by_artifact
from mangotree.storage.mongo import get_mongo

BATCH = 400


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    mongo = get_mongo()
    art = mongo.artifacts

    shas = mongo.chunks.distinct("artifact_sha")
    print(f"\n  artifacts with chunks     {len(shas):,}")

    artifacts = {
        d["sha256"]: d
        for d in art.find(
            {"sha256": {"$in": shas}},
            {"sha256": 1, "source_type": 1, "filename": 1, "date": 1, "scope": 1,
             "property_ids": 1, "participants": 1, "relative_path": 1,
             "parent_email_shas": 1},
        )
    }
    occurrences = occurrences_by_artifact(mongo, shas)

    # Attachments inherit sender and dates from the emails that carried them.
    parent_shas = {
        p for d in artifacts.values() for p in (d.get("parent_email_shas") or [])
    }
    parents = {
        d["sha256"]: d
        for d in art.find(
            {"sha256": {"$in": list(parent_shas)}},
            {"sha256": 1, "date": 1, "participants": 1},
        )
    }
    parent_occ = occurrences_by_artifact(mongo, list(parent_shas))
    for sha, doc in parents.items():
        doc["_folders"] = [o.get("folder") for o in parent_occ.get(sha, []) if o.get("folder")]

    operations = []
    filled: Counter = Counter()

    for sha in shas:
        doc = artifacts.get(sha)
        if not doc:
            continue
        meta = chunk_metadata(
            doc,
            occurrences=occurrences.get(sha, []),
            parent_emails=[parents[p] for p in (doc.get("parent_email_shas") or []) if p in parents],
        )
        for key, value in meta.items():
            if value not in (None, [], ""):
                filled[key] += 1
        operations.append(UpdateMany({"artifact_sha": sha}, {"$set": meta}))

    print(f"  update operations         {len(operations):,}\n")
    print("  artifacts contributing a value per field")
    for field, n in filled.most_common():
        print(f"    {field:<22} {n:>6,}")

    if not args.apply:
        print("\n  DRY RUN — pass --apply to write\n")
        return 0

    for start in range(0, len(operations), BATCH):
        mongo.chunks.bulk_write(operations[start:start + BATCH], ordered=False)

    print("\n  CHUNK COVERAGE AFTER BACKFILL")
    total = mongo.chunks.count_documents({})
    for field in ("from_email", "date_ym", "date_year", "latest_date", "folder_path",
                  "filename", "extension", "scope", "parent_email_shas", "occurrence_count"):
        n = mongo.chunks.count_documents({field: {"$exists": True, "$nin": [None, [], ""]}})
        print(f"    {field:<22} {n:>6,} / {total:,}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
