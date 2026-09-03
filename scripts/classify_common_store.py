"""Classify the confident-common store: portfolio (property chats see it) vs business.

Usage:
    python scripts/classify_common_store.py               # everything not yet classified
    python scripts/classify_common_store.py --limit 40    # a taste before the full run
    python scripts/classify_common_store.py --force       # redo, e.g. after a prompt change
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

from mangotree.resolve import common_classifier
from mangotree.resolve.common_classifier import CommonClassificationRunner
from mangotree.storage.mongo import get_mongo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--concurrency", type=int, default=common_classifier.CONCURRENCY)
    args = parser.parse_args()

    common_classifier.CONCURRENCY = args.concurrency
    common_classifier.WINDOW = args.concurrency * 4

    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set")
        return 1

    mongo = get_mongo()
    runner = CommonClassificationRunner(mongo, key)
    stats = runner.run(limit=args.limit, force=args.force)

    print("\n  COMMON-STORE CLASSIFICATION")
    for k, v in stats.as_dict().items():
        if k == "topics":
            continue
        print(f"    {k:<18} {v:,}" if isinstance(v, int) else f"    {k:<18} {v}")

    if stats.topics:
        print("\n    topics")
        for topic, n in sorted(stats.topics.items(), key=lambda kv: -kv[1])[:25]:
            print(f"      {topic:<28} {n:>5,}")

    art = mongo.artifacts
    print("\n  COMMON STORE NOW")
    for kind in ("portfolio", "business"):
        print(f"    {kind:<12} {art.count_documents({'scope': 'common', 'common_kind': kind}):>6,}")
    print(f"    {'unclassified':<12} "
          f"{art.count_documents({'scope': 'common', 'resolution_status': 'no_property', 'common_kind': {'$exists': False}}):>6,}")
    print(f"    {'unplaced':<12} "
          f"{art.count_documents({'scope': 'common', 'resolution_status': 'needs_review'}):>6,}   <- human queue, untouched")

    for kind in ("portfolio", "business"):
        print(f"\n  SAMPLE {kind.upper()}")
        cursor = art.find(
            {"common_kind": kind, "common_classification.run_id": runner.run_id},
            {"subject": 1, "filename": 1, "common_classification.reasoning": 1,
             "common_classification.confidence": 1},
        ).limit(8)
        for d in cursor:
            label = d.get("subject") or d.get("filename") or "?"
            cc = d.get("common_classification") or {}
            print(f"    [{cc.get('confidence', 0):.2f}] {str(label)[:55]:<55} | {str(cc.get('reasoning', ''))[:80]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
