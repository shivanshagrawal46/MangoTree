"""Classify email attachments by document class.

Tier-2 context and the coverage inventory both key on `doc_class`, and every
attachment currently has none — so a title commitment that arrived by email reads
as "unclassified" while the identical document found on disk reads as
"title_commitment". Same classifier as the disk corpus, so the two agree.

    python scripts/classify_attachments.py           # report
    python scripts/classify_attachments.py --apply   # write
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.ingest.disk_ingest import classify_document, is_privileged
from mangotree.storage.mongo import get_mongo

APPLY = "--apply" in sys.argv


def main() -> None:
    db = get_mongo().db

    rows = list(db["artifacts"].find(
        {"source_type": "attachment"},
        {"sha256": 1, "filename": 1, "doc_class": 1, "text": 1},
    ))

    # The classifier falls back to a bare file-kind when no rule matches, so
    # these are the values that mean "not actually classified" and are worth a
    # second attempt against the document's own opening text.
    GENERIC = {"pdf", "document", "image", "spreadsheet", "unknown", "email",
               "archive", "video", "unclassified"}

    tally: Counter = Counter()
    updates = []
    for a in rows:
        filename = a.get("filename") or ""
        head = (a.get("text") or "")[:1500]

        doc_class = classify_document(filename, filename)
        if doc_class in GENERIC and head:
            # A scan named "ALTA Buyer's Settlement Statement (81).pdf" classifies
            # from its name; one named "doc1.pdf" only classifies from its first
            # page. Passing the opening text recovers the second case.
            from_text = classify_document(filename, head, from_body_text=True)
            if from_text not in GENERIC:
                doc_class = from_text

        tally[doc_class] += 1
        # Filename only, deliberately. Privilege restricts a document from
        # general answers, so a false positive silently withholds evidence. A
        # contract that merely mentions "attorney" in its text is not attorney
        # work product, and body-text matching flagged exactly that.
        privileged = is_privileged(filename, filename)
        if a.get("doc_class") != doc_class or bool(a.get("privileged")) != privileged:
            updates.append((a["sha256"], doc_class, filename, privileged))

    print("=" * 74)
    print(f"ATTACHMENT CLASSIFICATION ({len(rows)} attachments)")
    print("=" * 74)
    for cls, count in tally.most_common():
        print(f"  {count:>5}  {cls}")

    print(f"\n{len(updates)} would change\n")
    for sha, cls, name, priv in updates[:40]:
        flag = "  [PRIVILEGED]" if priv else ""
        print(f"  {cls:<28}{name[:58]}{flag}")
    if len(updates) > 40:
        print(f"  ... and {len(updates) - 40} more")

    if not APPLY:
        print("\n(report only — pass --apply to write)")
        return

    from pymongo import UpdateOne

    if updates:
        db["artifacts"].bulk_write(
            [UpdateOne(
                {"sha256": sha},
                {"$set": {"doc_class": cls, "privileged": priv}},
            ) for sha, cls, _, priv in updates],
            ordered=False,
        )
        print(f"\nclassified {len(updates)} attachments")


if __name__ == "__main__":
    main()
