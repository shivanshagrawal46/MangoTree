"""Close review entries whose question has already been answered.

Most of the queue was raised by the deterministic keyword matcher during
ingestion, before Opus 5 existed in the pipeline. Every one of those artifacts
has since been read by the model and given a decision — either a property, or a
considered "this concerns no property at all". Either way the question the entry
asks has an answer on the record, and leaving it open would send a person to
re-decide something already decided.

Entries are marked resolved rather than deleted: the audit trail is the point of
a review queue, and "this was raised and then answered by the model on this date"
is exactly what someone reviewing the file later needs to see.

An entry stays open when the artifact has no segregation decision at all, or
when the model itself asked for a human.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, ".")

from pymongo import UpdateOne

from mangotree.storage.mongo import get_mongo

BATCH = 500


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args()

    mongo = get_mongo()
    art = mongo.artifacts
    queue = mongo.review_queue

    entries = list(queue.find({}, {"artifact_sha": 1, "kind": 1, "resolved": 1, "status": 1}))
    shas = {e.get("artifact_sha") for e in entries if e.get("artifact_sha")}

    # One pass for every artifact the queue references.
    state = {
        d["sha256"]: d
        for d in art.find(
            {"sha256": {"$in": [s for s in shas if s and not s.startswith("disk::")]}},
            {"sha256": 1, "property_ids": 1, "segregation": 1, "resolution_status": 1},
        )
    }

    operations = []
    tally: Counter = Counter()
    now = datetime.now(timezone.utc)

    for entry in entries:
        sha = entry.get("artifact_sha") or ""
        doc = state.get(sha)

        if sha.startswith("disk::") or not doc:
            # Keyed by disk path, or the artifact is gone. Nothing to check
            # against, so it stays for a human.
            tally["kept: no artifact to check"] += 1
            continue

        seg = doc.get("segregation") or {}
        if not seg:
            tally["kept: never segregated"] += 1
            continue

        if doc.get("resolution_status") == "needs_review":
            tally["kept: model asked for a human"] += 1
            continue

        outcome = (
            f"assigned {', '.join(doc['property_ids'])}"
            if doc.get("property_ids")
            else "model ruled it concerns no property"
        )
        tally[f"closed: {'assigned' if doc.get('property_ids') else 'no property'}"] += 1
        operations.append(UpdateOne(
            {"_id": entry["_id"]},
            {"$set": {
                "resolved": True,
                "status": "closed",
                "resolved_at": now,
                "resolved_by": "opus5_segregation",
                "resolution": outcome,
                "resolution_note": (
                    "Raised before Opus 5 ran; the model has since decided this "
                    "artifact, so no human decision is outstanding."
                ),
            }},
        ))

    print(f"\n  review entries examined  {len(entries):,}")
    for label, n in tally.most_common():
        print(f"    {label:<34} {n:>6,}")
    print(f"\n  would close              {len(operations):,}")

    if not args.apply:
        print("  DRY RUN — pass --apply to write\n")
        return 0

    for start in range(0, len(operations), BATCH):
        queue.bulk_write(operations[start:start + BATCH], ordered=False)

    open_now = queue.count_documents({"resolved": {"$ne": True}})
    print(f"  closed                   {len(operations):,}")
    print(f"  still open               {open_now:,}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
