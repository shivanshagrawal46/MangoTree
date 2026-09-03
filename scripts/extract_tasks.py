"""Opus 5 task extraction for every property (or a subset). Idempotent.

    python scripts/extract_tasks.py
    python scripts/extract_tasks.py --property chita_ct --property varnum
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo
from mangotree.tasks.extractor import TaskExtractor
from mangotree.tasks.store import TaskStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--property", action="append", default=None)
    parser.add_argument("--concurrency", type=int, default=5)
    args = parser.parse_args()

    mongo = get_mongo()
    ex = TaskExtractor(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    stats = ex.run(args.property, concurrency=args.concurrency)

    print("\n  TASK EXTRACTION")
    for k, v in stats.as_dict().items():
        if k == "per_property":
            continue
        print(f"    {k:<18} {v:,}" if isinstance(v, int) else f"    {k:<18} {v}")
    print("\n    per property")
    for pid, out in sorted(stats.per_property.items()):
        print(f"      {pid:<14} tasks={out['tasks']:>3} done={out['done']:>3} wes={out['wes']:>3} dropped={out['dropped']}")
    c = TaskStore(mongo).counts()
    print(f"\n    by owner: {c['by_owner']}")
    print(f"    by status: {c['by_status']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
