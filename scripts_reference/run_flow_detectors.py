"""Run the Sprint 5 flow detectors. Dry (read-only) by default; --apply writes.

  python scripts/run_flow_detectors.py            # dry: counts + samples
  python scripts/run_flow_detectors.py --apply    # write findings
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.detectors_flow import detect_instrument_conflicts, detect_open_loops


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--samples", type=int, default=6)
    args = ap.parse_args()
    write = args.apply
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)

    print(f"{'APPLY' if write else 'DRY-RUN'}: flow detectors\n")
    ic = detect_instrument_conflicts(m, write=write)
    print(f"money_conflict candidates: {len(ic)}")
    for f in ic[:args.samples]:
        print(f"  - {f.title}")
    ol = detect_open_loops(m, write=write)
    print(f"\nopen_loop candidates: {len(ol)}")
    for f in ol[:args.samples]:
        print(f"  - {f.title}")
        print(f"      {f.detail[:180]}")

    if not write:
        print("\nDRY-RUN: nothing written. Re-run with --apply to persist findings.")
    else:
        print("\nAPPLIED. Undo: db.findings.delete_many({detector:{$in:["
              "'detect_instrument_conflicts','detect_open_loops']}})")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
