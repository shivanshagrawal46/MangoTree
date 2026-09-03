"""J1 — question-augmented re-embedding of every chunk. Resumable.

Usage:
    python scripts/night_job_questions.py --limit 50     # taste
    python scripts/night_job_questions.py                # everything not yet done
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.index import questions as qmod
from mangotree.index.questions import EMBED_VERSION, QuestionAugmenter
from mangotree.storage.mongo import get_mongo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=qmod.CONCURRENCY)
    args = parser.parse_args()
    qmod.CONCURRENCY = args.concurrency

    mongo = get_mongo()
    aug = QuestionAugmenter(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key)
    stats = aug.run(limit=args.limit)

    print("\n  QUESTION AUGMENTATION")
    for k, v in stats.as_dict().items():
        print(f"    {k:<20} {v:,}" if isinstance(v, int) else f"    {k:<20} {v}")
    done = mongo.chunks.count_documents({"embed_version": EMBED_VERSION})
    total = mongo.chunks.count_documents({})
    print(f"\n    chunks on {EMBED_VERSION}: {done:,} / {total:,}")

    print("\n  SAMPLE")
    for c in mongo.chunks.find({"embed_version": EMBED_VERSION, "questions.0": {"$exists": True}},
                               {"display_name": 1, "questions": 1, "property_ids": 1}).limit(4):
        print(f"    {str(c.get('display_name'))[:60]}  {c.get('property_ids')}")
        for q in c.get("questions") or []:
            print(f"        - {q}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
