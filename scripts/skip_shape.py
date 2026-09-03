"""What does a skip record look like, and was the dropped .eml ever recorded?

The pipeline calls _record_skip on rejection, so a skip record should exist for
'Email from Bill leroy.eml'. If it does not, the file left no trace at any layer
and the accounting is genuinely broken rather than merely hard to query.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db

    sample = db["skipped"].find_one()
    print("=== fields on a skip record ===")
    for key, value in (sample or {}).items():
        print(f"  {key:<22} = {repr(value)[:110]}")

    print("\n=== providers present in `skipped` ===")
    print(f"  {db['skipped'].distinct('provider')}")
    print(f"  mailboxes: {db['skipped'].distinct('mailbox')}")

    print("\n=== any skip record from the disk provider ===")
    disk_skips = list(db["skipped"].find({"provider": "disk"}).limit(10))
    print(f"  count: {db['skipped'].count_documents({'provider': 'disk'})}")
    for s in disk_skips:
        print(f"    {s.get('provider_id')}  reason={s.get('reason') or s.get('decision')}")

    print("\n=== hunt for 'leroy' across every string field in every collection ===")
    for name in ("skipped", "artifacts", "review_queue", "ingestion_errors",
                 "occurrences", "threads"):
        collection = db[name]
        found = 0
        for doc in collection.find():
            blob = repr(doc).lower()
            if "leroy" in blob:
                found += 1
                if found <= 3:
                    keys = [k for k, v in doc.items()
                            if isinstance(v, str) and "leroy" in v.lower()]
                    print(f"  {name}: match on {keys}")
                    for k in keys:
                        print(f"      {k} = {doc[k][:120]}")
        print(f"  {name:<18} {found} documents mention 'leroy'")

    print("\n=== disk ingestion run stats (was the .eml counted?) ===")
    for run in db["ingestion_runs"].find().sort("started_at", -1).limit(6):
        keys = {k: v for k, v in run.items()
                if k in ("run_id", "kind", "source", "seen", "emails_routed",
                         "stored", "duplicates", "errors", "unresolved",
                         "skipped", "status")}
        print(f"  {keys}")


if __name__ == "__main__":
    main()
