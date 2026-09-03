"""Run the thread-context pass over emails Opus 5 could not place alone."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

from mangotree.resolve.thread_context import ThreadContextRunner
from mangotree.storage.mongo import get_mongo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set")
        return 1

    mongo = get_mongo()
    before = mongo.artifacts.count_documents({"property_ids": {"$ne": []}})

    runner = ThreadContextRunner(mongo, key)
    stats = runner.run(limit=args.limit)

    after = mongo.artifacts.count_documents({"property_ids": {"$ne": []}})

    print("\n  THREAD-CONTEXT PASS")
    for key_, value in stats.as_dict().items():
        if key_ == "assignments":
            continue
        print(f"    {key_:<26} {value}")

    if stats.assignments:
        print("\n    newly assigned by property")
        for pid, n in sorted(stats.assignments.items(), key=lambda kv: -kv[1]):
            print(f"      {pid:<20} {n:>4}")

    print(f"\n    artifacts with a property  {before:,} -> {after:,}  (+{after - before})")
    print(f"    review queue now           {mongo.review_queue.count_documents({}):,}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
