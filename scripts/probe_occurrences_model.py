"""How well can we answer "where else did this document appear?"

The corpus tracks appearance in two different ways, and they do not cover the
same things:

* ``occurrences`` collection — one row per (email, mailbox, folder). Written
  only for emails, so it answers "which mailboxes held this message".
* ``parent_email_shas`` / ``source_paths`` arrays on the artifact — written for
  attachments and disk files, so they answer "which emails carried this PDF"
  and "where on disk does it sit".

An attachment reused across several emails is the case that matters: it is one
artifact by SHA-256, and every email that carried it is evidence of a separate
event. This measures how often that happens and whether the link is reachable.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    spread: Counter = Counter()
    multi = []
    for doc in art.find(
        {"source_types": "attachment"},
        {"sha256": 1, "filename": 1, "parent_email_shas": 1, "source_paths": 1},
    ):
        parents = doc.get("parent_email_shas") or []
        spread[len(parents)] += 1
        if len(parents) > 1:
            multi.append(doc)

    print("\n  ATTACHMENTS BY NUMBER OF CARRYING EMAILS")
    for n, count in sorted(spread.items()):
        label = f"{n} email" + ("" if n == 1 else "s")
        flag = "   <- reused" if n > 1 else ""
        print(f"    {label:<12} {count:>6,}{flag}")

    total_links = sum(n * c for n, c in spread.items())
    print(f"\n    distinct attachments  {sum(spread.values()):>6,}")
    print(f"    attachment->email links{total_links:>6,}")
    print(f"    reused across emails  {len(multi):>6,}")

    print("\n  MOST-REUSED ATTACHMENTS")
    for doc in sorted(multi, key=lambda d: -len(d["parent_email_shas"]))[:8]:
        n = len(doc["parent_email_shas"])
        name = (doc.get("filename") or "(unnamed)")[:56]
        disk = len(doc.get("source_paths") or [])
        extra = f" + {disk} disk path(s)" if disk else ""
        print(f"    {n:>3} emails  {name}{extra}")

    print("\n  WHAT occurrences COVERS TODAY")
    # One pass to build the sha -> source_type map, rather than a lookup per
    # occurrence row: 3,400 Atlas round trips is 16 minutes of pure latency.
    types = {
        d["sha256"]: d.get("source_type")
        for d in art.find({}, {"sha256": 1, "source_type": 1})
    }
    kinds: Counter = Counter(
        types.get(occ.get("artifact_sha")) or "missing"
        for occ in mongo.occurrences.find({}, {"artifact_sha": 1})
    )
    for kind, n in kinds.most_common():
        print(f"    {str(kind):<14} {n:>7,}")
    print(f"    (attachments have no occurrence rows — they use parent_email_shas)")

    print("\n  CAN A SEARCH RESULT ANSWER 'WHERE ELSE?'")
    sample = mongo.chunks.find_one({"source_type": "attachment"}, {"embedding": 0}) or {}
    has = [k for k in ("artifact_sha", "parent_email_shas", "source_ref") if sample.get(k)]
    print(f"    fields on an attachment chunk: {has}")
    print("    -> parent emails are NOT on the chunk; a second lookup is required")
    print()


if __name__ == "__main__":
    main()
