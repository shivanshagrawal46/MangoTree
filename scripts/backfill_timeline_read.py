"""Stamp ``timeline_read`` on every document the full Opus 5 timeline pass covered.

The runner used to remember only documents that *produced* an event. Documents
Opus read and found nothing in — about half — were re-read on every run, so a
per-property refresh after one new email cost hundreds of calls. The runner now
stamps ``timeline_read`` as it goes; this marks what was read before the stamp
existed.

Rule: an artifact is marked if it matches the timeline query, has text, and was
created before the last complete full-corpus timeline run finished. Anything
newer is left for the next run to read.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.config.models import Seat, model_for
from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    model = model_for(Seat.ANALYST)
    # The full pass: the completed timeline run that read the most documents.
    runs = list(mongo.runs.find({"kind": "timeline", "status": "complete"}))
    if not runs:
        print("  no complete timeline run found; nothing to backfill")
        return 1
    full = max(runs, key=lambda r: (r.get("extract_stats") or {}).get("documents", 0))
    finished = full["finished_at"]
    docs = (full.get("extract_stats") or {}).get("documents", 0)
    print(f"  full pass {full['run_id']}: {docs} documents read, finished {finished:%Y-%m-%d %H:%M}")

    query = {
        "source_type": {"$in": ["disk_file", "email", "attachment"]},
        "property_ids.0": {"$exists": True},
        "is_inline_image": {"$ne": True},
        "created_at": {"$lt": finished},
        "timeline_read": {"$exists": False},
        "$or": [{"text": {"$nin": [None, ""]}}, {"body_clean": {"$nin": [None, ""]}}],
    }
    if "--dry" in sys.argv:
        print(f"  would mark {mongo.artifacts.count_documents(query):,} artifacts")
        return 0
    res = mongo.artifacts.update_many(query, {"$set": {"timeline_read": {
        "model": model, "at": datetime.now(timezone.utc), "run_id": full["run_id"], "backfilled": True}}})
    print(f"  marked {res.modified_count:,} artifacts as read by {model}")
    print(f"  still unread: {mongo.artifacts.count_documents({**{k: v for k, v in query.items() if k != 'created_at'}}):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
