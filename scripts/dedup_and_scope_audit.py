"""Prove three things before the expensive stages run.

1. SHA-256 collapse actually happened between the E: drive corpus and email
   attachments — the same bytes must be one artifact, not two.
2. Opus 5 segregation is scoped to mail only. A disk file that never arrived as
   an attachment must never reach a billed call.
3. What extraction still owes, per source, so the finish estimate is measured
   rather than guessed.

The overlap set is the interesting one: those artifacts carry disk provenance
(``relative_path``) *and* mail provenance (``parent_email_shas``), which is only
possible if the upsert key collapsed them.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

DISK = {"relative_path": {"$exists": True}}
MAIL = {"parent_email_shas": {"$exists": True, "$ne": []}}


def main() -> None:
    art = get_mongo().artifacts

    print("\n  ARTIFACTS BY source_type")
    for row in art.aggregate([
        {"$group": {"_id": "$source_type", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"    {str(row['_id']):<16} {row['n']:>7,}")

    disk_only = art.count_documents({**DISK, "parent_email_shas": {"$exists": False}})
    both = art.count_documents({**DISK, **MAIL})
    mail_only = art.count_documents({**MAIL, "relative_path": {"$exists": False}})

    print("\n  DEDUP (SHA-256 collapse, E: drive vs email attachments)")
    print(f"    disk provenance only    {disk_only:>7,}")
    print(f"    mail provenance only    {mail_only:>7,}")
    print(f"    BOTH (collapsed)        {both:>7,}   <- one artifact, two origins")

    dupe_shas = len(art.distinct("sha256"))
    total = art.count_documents({})
    print(f"    distinct sha256         {dupe_shas:>7,}")
    print(f"    total artifact docs     {total:>7,}")
    print(f"    duplicate rows          {total - dupe_shas:>7,}   <- must be 0")

    print("\n  OPUS 5 SEGREGATION SCOPE")
    emails = art.count_documents({"source_type": "email"})
    atts = art.count_documents({"source_type": "attachment"})
    orphan_atts = art.count_documents(
        {"source_type": "attachment", "parent_email_shas": {"$in": [None, []]}}
    )
    print(f"    emails (1 call each)    {emails:>7,}")
    print(f"    attachments (ride along){atts:>7,}")
    print(f"      of which orphaned     {orphan_atts:>7,}   <- never sent, no parent")
    print(f"    disk files excluded     {disk_only:>7,}   <- folder already names the property")

    seg_done = art.count_documents({"source_type": "email", "segregation": {"$exists": True}})
    print(f"    emails already decided  {seg_done:>7,}")
    print(f"    emails pending          {emails - seg_done:>7,}")

    print("\n  EXTRACTION STATUS BY SOURCE")
    for row in art.aggregate([
        {"$match": {"source_type": {"$in": ["disk_file", "attachment"]}}},
        {"$group": {
            "_id": {"s": "$source_type", "st": "$extraction.status"},
            "n": {"$sum": 1},
        }},
        {"$sort": {"n": -1}},
    ]):
        key = f"{row['_id']['s']}/{row['_id']['st']}"
        print(f"    {key:<28} {row['n']:>7,}")

    print()


if __name__ == "__main__":
    main()
