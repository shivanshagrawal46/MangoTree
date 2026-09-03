"""Is segregation working, or wedged?

``SegregationRunner.run`` submits every email to the pool *before* consuming any
result, and only the consumer persists. So a zero decision count early in the
run is expected — but it is also what a wedged run looks like, and the two are
worth telling apart before an hour goes by.

Times the per-email join to size the submission phase, and reports whether the
run document exists yet.
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

SAMPLE = 25


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    run = mongo.runs.find_one({"kind": "segregation"}, sort=[("started_at", -1)])
    if run:
        elapsed = time.time() - run["started_at"].timestamp()
        print(f"\n  run {run['run_id']}  status={run.get('status')}")
        print(f"  pending at start   {run.get('pending', 0):,}")
        print(f"  elapsed            {elapsed / 60:.1f} min")
    else:
        print("\n  no segregation run document yet")

    emails = list(art.find({"source_type": "email"}, {"sha256": 1}).limit(SAMPLE))
    started = time.time()
    for email in emails:
        list(art.find(
            {"source_types": "attachment", "parent_email_shas": email["sha256"]},
            {"sha256": 1, "filename": 1, "content_type": 1, "text": 1},
        ))
    per_query = (time.time() - started) / max(1, len(emails))

    total = art.count_documents({"source_type": "email"})
    print(f"\n  join latency       {per_query * 1000:.0f} ms/email")
    print(f"  submission phase   ~{per_query * total / 60:.1f} min for {total:,} emails")

    decided = art.count_documents({"source_type": "email", "segregation": {"$exists": True}})
    print(f"\n  decisions persisted{decided:>8,}")
    print(f"  errors queued      {mongo.review_queue.count_documents({'kind': 'segregation_error'}):>8,}")
    print()


if __name__ == "__main__":
    main()
