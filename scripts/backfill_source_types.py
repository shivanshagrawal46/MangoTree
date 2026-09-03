"""Populate ``source_types`` on artifacts ingested before the field existed.

``source_type`` is singular and last-writer-wins, so bytes that arrived from two
places kept only whichever pass ran last. 122 email attachments also live in the
disk corpus, and because the disk pass ran last they read as ``disk_file`` —
which made them invisible to segregation's attachment join, so their parent
email would have been judged without the document it was carrying.

Origins are re-derived from provenance that was never overwritten:

    parent_email_shas  ->  attachment      (only the mail pipeline writes it)
    relative_path      ->  disk_file       (only the disk pass writes it)
    source_type=email  ->  email

Idempotent: re-running rewrites the same sets. Safe alongside a live stage, as
it only ever adds a field no running code writes.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from pymongo import UpdateOne

from mangotree.storage.mongo import get_mongo

BATCH = 1000


def origins_for(doc: dict) -> list:
    origins = set()
    if doc.get("parent_email_shas"):
        origins.add("attachment")
    if doc.get("relative_path"):
        origins.add("disk_file")
    if doc.get("source_type") == "email":
        origins.add("email")
    # Nothing inferable — trust the singular field rather than leaving it blank.
    if not origins and doc.get("source_type"):
        origins.add(doc["source_type"])
    return sorted(origins)


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    projection = {"sha256": 1, "source_type": 1, "parent_email_shas": 1, "relative_path": 1}
    operations = []
    tally: Counter = Counter()
    written = 0

    for doc in art.find({}, projection):
        origins = origins_for(doc)
        if not origins:
            tally["<none>"] += 1
            continue
        tally["+".join(origins)] += 1
        operations.append(UpdateOne({"_id": doc["_id"]}, {"$set": {"source_types": origins}}))
        if len(operations) >= BATCH:
            art.bulk_write(operations, ordered=False)
            written += len(operations)
            operations = []

    if operations:
        art.bulk_write(operations, ordered=False)
        written += len(operations)

    print(f"\n  artifacts stamped        {written:>7,}")
    print("\n  ORIGIN COMBINATIONS")
    for combo, count in tally.most_common():
        marker = "   <- collapsed, both origins" if "+" in combo else ""
        print(f"    {combo:<26} {count:>7,}{marker}")

    # The segregation join runs once per email, so it needs to be an index hit.
    # Single-key only: both fields are arrays, and Mongo refuses to compound two
    # of those. ``parent_email_shas`` is the selective half anyway — it narrows
    # to one email's attachments, after which the origin test is trivial.
    art.create_index("parent_email_shas")
    print("\n  index created on parent_email_shas")

    reachable = art.count_documents({"source_types": "attachment"})
    print(f"  attachments now reachable by the join   {reachable:>7,}")
    print()


if __name__ == "__main__":
    main()
