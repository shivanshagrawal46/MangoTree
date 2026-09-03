"""Generate today's Wes agenda (top three per property) with Fable 5.1.

    python scripts/run_wes_agenda.py                # all properties, skip ones already done today
    python scripts/run_wes_agenda.py --force varnum # regenerate one
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.briefing.wes_agenda import WesAgenda
from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    force = "--force" in sys.argv
    ids = [a for a in sys.argv[1:] if not a.startswith("-")] or None
    out = WesAgenda(mongo, anthropic_api_key=SETTINGS.anthropic_api_key).run(ids, force=force, concurrency=4)
    for pid, r in sorted(out.items()):
        print(f"  {pid:<14} {r}")
    if "--mark-daily" in sys.argv:
        # Tell the scheduler today's money/Wes run is done so it does not repeat it.
        mongo.db["scheduled_runs"].insert_one({"job": "money_wes", "day": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                               "ok": True, "detail": "run manually via scripts", "at": datetime.now(timezone.utc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
