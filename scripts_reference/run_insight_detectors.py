"""Run the insight detectors (LLC-timing + insurance changes).
Dry (read-only) by default; --apply writes findings.

  python scripts/run_insight_detectors.py           # dry: counts + samples
  python scripts/run_insight_detectors.py --apply   # write findings
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.detectors_insight import (
    detect_llc_transfer_timing, detect_insurance_changes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--samples", type=int, default=8)
    args = ap.parse_args()
    write = args.apply
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)

    print(f"{'APPLY' if write else 'DRY-RUN'}: insight detectors\n")
    llc = detect_llc_transfer_timing(m, write=write)
    print(f"llc_timing candidates: {len(llc)}")
    for f in llc[:args.samples]:
        print(f"  - {f.title}")
    ins = detect_insurance_changes(m, write=write)
    print(f"\ninsurance_change candidates: {len(ins)}")
    from collections import Counter
    bt = Counter(f.finding_type for f in ins)
    for t, n in bt.most_common():
        print(f"    {t}: {n}")
    for f in ins[:args.samples]:
        print(f"  - [{f.severity}] {f.title}")

    if not write:
        print("\nDRY-RUN: nothing written. Re-run with --apply to persist.")
    else:
        print("\nAPPLIED. Undo: db.findings.delete_many({detector:{$in:["
              "'detect_llc_transfer_timing','detect_insurance_changes']}})")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
