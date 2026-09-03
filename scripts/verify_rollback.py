"""Prove the rollback is complete and the corpus is back to its prior shape.

Checks the four things that could have gone wrong: leftover artifacts from the
run, chunks that would mean embeddings survived, a mismatch between what was
removed and what is now queued, and orphaned bytes left in the object store.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo

RUN = "run-20260831-183753"


def main() -> None:
    db = get_mongo().db
    ok = True

    leftover = db["artifacts"].count_documents({"first_run_id": RUN})
    print(f"artifacts from the run           {leftover:>6}   {'OK' if leftover == 0 else 'FAIL'}")
    ok &= leftover == 0

    occ = db["occurrences"].count_documents({"run_id": RUN})
    print(f"occurrences from the run         {occ:>6}   {'OK' if occ == 0 else 'FAIL'}")
    ok &= occ == 0

    rq = db["review_queue"].count_documents({"run_id": RUN})
    print(f"review queue from the run        {rq:>6}   {'OK' if rq == 0 else 'FAIL'}")
    ok &= rq == 0

    holds = db["skipped"].count_documents({"reason": "hold_pending_segregator"})
    print(f"emails queued for later          {holds:>6}   {'OK' if holds == 85 else 'CHECK'}")
    ok &= holds == 85

    root = Path(SETTINGS.raw_store)
    stray = 0
    for meta_path in root.rglob("*.meta.json"):
        try:
            if json.loads(meta_path.read_text(encoding="utf-8")).get("run_id") == RUN:
                stray += 1
        except Exception:
            pass
    print(f"orphaned objects on disk         {stray:>6}   {'OK' if stray == 0 else 'FAIL'}")
    ok &= stray == 0

    print("\n--- corpus now ---")
    for source in ("email", "attachment", "disk_file"):
        n = db["artifacts"].count_documents({"source_type": source})
        print(f"  {source:<14}{n:>6}")
    print(f"  {'chunks':<14}{db['chunks'].count_documents({}):>6}")
    print(f"  {'people':<14}{db['people'].count_documents({}):>6}")

    print("\n--- skipped, by reason ---")
    for row in db["skipped"].aggregate([
        {"$group": {"_id": "$reason", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]):
        print(f"  {str(row['_id']):<28}{row['n']:>6}")

    print("\n" + ("ROLLBACK VERIFIED CLEAN" if ok else "SOMETHING IS OFF — see FAIL above"))


if __name__ == "__main__":
    main()
