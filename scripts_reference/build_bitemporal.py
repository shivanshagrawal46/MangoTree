"""Sprint 3 · 3.2.3 — build bitemporal ownership intervals.

Closes `until` on GRANTEE_OF + OWNS edges from the recorded chain of title so
the graph answers "who owned it on date X". Idempotent; dry-run by default.

  python -m scripts.build_bitemporal            # dry-run (counts only)
  python -m scripts.build_bitemporal --live     # apply
"""
from __future__ import annotations

import sys

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.bitemporal import build_ownership_intervals
from src.utils.logger import logger


def main() -> int:
    live = "--live" in sys.argv
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    counts = build_ownership_intervals(m.db["relationships"], live=live)
    logger.info(f"bitemporal ownership ({'LIVE' if live else 'DRY-RUN'}): {counts}")
    if not live:
        logger.info("  re-run with --live to write `until` onto the edges")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
