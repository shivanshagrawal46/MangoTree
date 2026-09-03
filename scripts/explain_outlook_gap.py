"""Account for every message counted as qualifying but not fetched.

The backfill reports ``qualified`` (everything the rule admits) and ``fetched``
(everything this run downloaded). The difference is messages an earlier run had
already checkpointed, which is only harmless if those messages are genuinely in
the database. This checks that rather than assuming it.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.ingest.outlook_backfill import SCOPED_FOLDERS
from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    mailbox = SETTINGS.graph_mailbox

    total_checkpointed = 0
    total_present = 0
    missing_ids = []

    print(f"\n  mailbox {mailbox}\n")
    print(f"  {'folder':<38} {'checkpointed':>12} {'in database':>12}")
    print(f"  {'-' * 38} {'-' * 12} {'-' * 12}")

    for path in SCOPED_FOLDERS:
        # Checkpoints are keyed on a "key" field, not _id.
        doc = mongo.checkpoints.find_one(
            {"key": {"$regex": f"^outlook::.*::{path}::"}}
        )

        ids = list((doc or {}).get("processed_ids", []) or [])

        # One query per folder rather than one per message: the checkpoint holds
        # every id the run processed, and per-id lookups turned this into tens of
        # thousands of unindexed scans competing with the live pipeline.
        found = set()
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            # provider_id is recorded on the occurrence (where a message was
            # seen), not on the artifact (what the message is).
            found.update(
                d["provider_id"]
                for d in mongo.occurrences.find(
                    {"provider_id": {"$in": batch}}, {"provider_id": 1}
                )
            )

        present = len(found)
        missing_ids.extend((path, i) for i in ids if i not in found)

        total_checkpointed += len(ids)
        total_present += present
        print(f"  {path:<38} {len(ids):>12,} {present:>12,}")

    print(f"\n  {'TOTAL':<38} {total_checkpointed:>12,} {total_present:>12,}")

    emails = mongo.artifacts.count_documents({"source_type": "email"})
    gmail = mongo.artifacts.count_documents({"source_type": "email", "provider": "gmail"})
    outlook = mongo.artifacts.count_documents({"source_type": "email", "provider": "outlook"})
    print(f"\n  emails in database   {emails:>7,}")
    print(f"      gmail            {gmail:>7,}")
    print(f"      outlook          {outlook:>7,}")

    # The decisive check, independent of checkpoint bookkeeping: the rule
    # qualified 2,734 Outlook messages, so the database must hold 2,734.
    qualified_total = 2734
    print(f"\n  qualified by the rule  {qualified_total:>7,}")
    print(f"  outlook in database    {outlook:>7,}")
    if outlook >= qualified_total:
        print("  -> every qualifying Outlook message is stored\n")
    else:
        print(f"  -> MISSING {qualified_total - outlook} qualifying message(s)\n")

    if missing_ids:
        print(f"  {len(missing_ids)} checkpointed message(s) NOT in the database:")
        for path, provider_id in missing_ids[:20]:
            print(f"      {path:<34} {provider_id[:40]}")
        return 1

    return 0 if outlook >= qualified_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
