"""Are unresolved items still searchable, and which mailboxes did we actually pull?

Two questions that decide real things:

* An email with no property must still be chunked and embedded, or "unresolved"
  would silently mean "deleted from search" — a far worse outcome than being
  filed in the common store.
* The disk corpus contains .msg exports. Whether those are redundant with the
  Outlook API pull depends entirely on which mailboxes the API covered.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    shas = [d["sha256"] for d in art.find({"resolution_status": "needs_review"}, {"sha256": 1})]
    in_q = {"artifact_sha": {"$in": shas}}

    print(f"\n  needs_review artifacts        {len(shas):>7,}")
    print(f"    with at least one chunk     {len(mongo.chunks.distinct('artifact_sha', in_q)):>7,}")
    print(f"    chunks they produced        {mongo.chunks.count_documents(in_q):>7,}")
    print(f"    of those embedded           {mongo.chunks.count_documents({**in_q, 'embedding_model': 'voyage-4-large'}):>7,}")
    print(f"    of those with Tier-1 context{mongo.chunks.count_documents({**in_q, 'context': {'$exists': True, '$ne': ''}}):>7,}")

    print("\n  MAILBOXES PULLED (occurrences)")
    for row in mongo.occurrences.aggregate([
        {"$group": {"_id": {"m": "$mailbox", "p": "$provider"}, "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"    {str(row['_id']['p']):<10} {str(row['_id']['m']):<32} {row['n']:>7,}")

    print("\n  DISK-ROUTED EMAILS (.eml already sent through the mail pipeline)")
    print(f"    occurrences with mailbox=disk {mongo.occurrences.count_documents({'mailbox': 'disk'}):>7,}")
    print()


if __name__ == "__main__":
    main()
