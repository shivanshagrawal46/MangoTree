"""Find attachments left with no surviving parent email.

The rollback pulled deleted parent hashes off attachments that predated the run.
Any attachment whose only parents were those deleted emails is now orphaned: it
still holds text and chunks, but nothing connects it to a message, so its
provenance is broken and retrieval could surface a document with no context.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db

    total = db["artifacts"].count_documents({"source_type": "attachment"})
    print(f"attachments total: {total}\n")

    no_parent = list(db["artifacts"].find(
        {
            "source_type": "attachment",
            "$or": [
                {"parent_email_shas": {"$exists": False}},
                {"parent_email_shas": []},
                {"parent_email_shas": None},
            ],
        },
        {"sha256": 1, "filename": 1, "property_ids": 1, "first_run_id": 1},
    ))
    print(f"attachments with no parent: {len(no_parent)}")
    for a in no_parent:
        chunks = db["chunks"].count_documents({"artifact_sha": a["sha256"]})
        print(f"  {a.get('filename', '?')[:60]:<62} chunks={chunks}  "
              f"run={a.get('first_run_id', '?')}")

    # Dangling references are the other failure mode: a parent hash that points
    # at an email which no longer exists.
    dangling = 0
    for a in db["artifacts"].find(
        {"source_type": "attachment", "parent_email_shas": {"$type": "array"}},
        {"sha256": 1, "filename": 1, "parent_email_shas": 1},
    ):
        parents = a.get("parent_email_shas") or []
        if not parents:
            continue
        alive = db["artifacts"].count_documents(
            {"sha256": {"$in": parents}, "source_type": "email"}
        )
        if alive == 0:
            dangling += 1
            print(f"  DANGLING  {a.get('filename', '?')[:56]}")
    print(f"\nattachments whose parents all vanished: {dangling}")


if __name__ == "__main__":
    main()
