"""
Nightly insight job — runs ALL detectors, then generates the Daily Brief.

This is the "runs automatically every night" piece of the Insight Engine.
It is idempotent (findings upsert by deterministic id; human confirm/reject
status is preserved) and safe to run on a schedule.

What it does:
  1. Sprint-4 detectors  (anachronisms, voidable transfers, contradictions)
  2. Flow detectors       (money conflicts, open loops)
  3. Insight detectors    (LLC-timing, insurance changes)
  4. Daily Brief          (arrivals, deadlines, open loops, findings, questions)

Schedule it (Windows Task Scheduler), e.g. daily at 6am:
  schtasks /Create /SC DAILY /ST 06:00 /TN "MangoTreeNightly" ^
    /TR "cmd /c cd /d C:\\path\\to\\outlook_attachments && python scripts\\nightly_insight.py"

Or cron (Linux/macOS):
  0 6 * * *  cd /path/to/outlook_attachments && python scripts/nightly_insight.py

Usage (manual):  python scripts/nightly_insight.py
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.detectors import run_all
from src.detect.detectors_flow import run_flow_detectors
from src.detect.detectors_insight import run_insight_detectors
from src.utils.logger import logger


def main() -> int:
    # NOTE: this job does NOT ingest email. Gmail ingestion is handled
    # separately by the user. Each night we simply RE-RUN the detectors over
    # whatever is currently in the database (so any newly-ingested email is
    # picked up) and regenerate the Daily Brief for the frontend.
    started = datetime.now()
    print(f"=== NIGHTLY INSIGHT JOB — {started.isoformat(timespec='seconds')} ===")
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)

    totals = {}
    try:
        totals.update(run_all(m))                       # anachronism/voidable/contradiction
    except Exception as exc:  # noqa: BLE001
        logger.error(f"core detectors failed: {exc}")
    try:
        totals.update(run_flow_detectors(m, write=True))    # money_conflict/open_loop
    except Exception as exc:  # noqa: BLE001
        logger.error(f"flow detectors failed: {exc}")
    try:
        totals.update(run_insight_detectors(m, write=True))  # llc_timing/insurance
    except Exception as exc:  # noqa: BLE001
        logger.error(f"insight detectors failed: {exc}")
    m.close()

    print("Detector findings written (idempotent):")
    for k, v in totals.items():
        print(f"  {k:22s} {v}")

    # Generate the Daily Brief (its own read-only script).
    print("\nGenerating Daily Brief...")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "daily_brief.py")],
                          cwd=str(ROOT), capture_output=True, text=True)
    tail = (proc.stdout.strip().splitlines() or [""])[-3:]
    for line in tail:
        print(" ", line)

    print(f"\n=== NIGHTLY JOB DONE in {(datetime.now()-started).total_seconds():.0f}s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
