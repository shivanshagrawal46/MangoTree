"""Build (or rebuild) the money ledger for one or all properties with Fable 5.1.

    python scripts/build_ledger.py                 # all 15
    python scripts/build_ledger.py varnum 9th_st_nw
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.ledger.builder import LedgerBuilder, portfolio_summary
from mangotree.storage.mongo import get_mongo


def main() -> int:
    mongo = get_mongo()
    ids = [a for a in sys.argv[1:] if not a.startswith("-")] or None
    b = LedgerBuilder(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    stats = b.run(ids, concurrency=4)
    print("\n  LEDGER BUILD", stats.as_dict())
    ps = portfolio_summary(mongo)
    print(f"\n  established {ps['established']}/{ps['properties']}   invested={ps['invested']}  returned={ps['returned']}  billed={ps['billed']}  owed={ps['owed']} ({ps['owed_properties']} props)")
    for r in ps["per_property"]:
        owed = (r.get("owed") or {}).get("owed_total") if r.get("owed") else None
        print(f"    {r['property_id']:<14} est={str(r['established']):<5} rows={r['entries']:>3}  invested={r['invested']}  returned={r['returned']}  billed={r['billed']}  owed={owed}  disc={r['discrepancies']} gaps={r['gaps']} risks={r['risks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
