"""What is actually stored on a skip record, and can subjects be recovered?

The counterparty report is useless without subject lines — "11 messages from
advancecpa@gmail.com" is not something anyone can approve or reject. This checks
whether subjects were stored, and if not, what identifier survives that would let
them be fetched back from Gmail.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db

    total = db["skipped"].count_documents({})
    print(f"skipped records: {total}\n")

    field_presence: Counter = Counter()
    non_null: Counter = Counter()
    for s in db["skipped"].find():
        for key, value in s.items():
            field_presence[key] += 1
            if value not in (None, "", [], {}):
                non_null[key] += 1

    print(f"{'field':<26}{'present':>9}{'non-empty':>11}")
    for key, count in field_presence.most_common():
        print(f"  {key:<24}{count:>9}{non_null[key]:>11}")

    print("\n--- three full records ---")
    for s in db["skipped"].find().limit(3):
        print()
        for key, value in sorted(s.items()):
            print(f"  {key:<22}{repr(value)[:110]}")

    print("\n--- can we fetch subjects back from Gmail? ---")
    with_pid = db["skipped"].count_documents(
        {"provider_id": {"$exists": True, "$ne": None}}
    )
    print(f"  records with provider_id: {with_pid} / {total}")
    print("  (provider_id is the Gmail message id, so subjects are re-fetchable)")


if __name__ == "__main__":
    main()
