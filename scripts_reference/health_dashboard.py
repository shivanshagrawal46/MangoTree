"""
Operational health dashboard (Sprint 6) — READ-ONLY.

One command to see whether the system is healthy: corpus coverage,
quoted-recovery status, findings ledger, money graph, and the key gap
counters — each with a PASS/WARN indicator against a target. Writes
nothing. Complements scripts/scan_missing.py (which is the deep recall
diagnostic); this is the at-a-glance operational view.

Usage:  python scripts/health_dashboard.py [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db = m.db
    ch = db["email_chunks_v2"]
    rows = []  # (label, value, status)

    def add(label, value, ok=True, warn=False):
        status = "WARN" if warn else ("PASS" if ok else "FAIL")
        rows.append((label, str(value), status))

    # --- corpus coverage ---
    total = ch.count_documents({})
    emb = ch.count_documents({"embedding.0": {"$exists": True}})
    ctx = ch.count_documents({"context": {"$nin": [None, ""]}})
    ent = ch.count_documents({"entity_ids.0": {"$exists": True}})
    add("chunks total", f"{total:,}")
    add("  embedding coverage", f"{_pct(emb,total)}%", ok=(emb == total), warn=(emb != total))
    add("  context coverage", f"{_pct(ctx,total)}%", ok=(ctx == total), warn=(ctx != total))
    add("  entity-linked", f"{_pct(ent,total)}% ({ent:,})")

    # --- quoted recovery ---
    qb = {"source_batch": "quoted_recovery_v1"}
    q_total = ch.count_documents(qb)
    q_ent = ch.count_documents({**qb, "entity_ids.0": {"$exists": True}})
    q_dated = ch.count_documents({**qb, "date_source": "quoted_original"})
    add("quoted-recovery chunks", f"{q_total:,}")
    add("  entity-linked", f"{_pct(q_ent,q_total)}% ({q_ent:,})")
    add("  original-dated", f"{_pct(q_dated,q_total)}% ({q_dated:,})")

    # --- findings ledger ---
    fnd = db["findings"]
    f_total = fnd.count_documents({})
    f_pending = fnd.count_documents({"status": "pending"})
    f_conf = fnd.count_documents({"status": "confirmed"})
    from collections import Counter
    by_type = Counter(d.get("finding_type") for d in fnd.find({}, {"finding_type": 1}))
    add("findings total", f"{f_total:,}  (pending {f_pending:,} / confirmed {f_conf:,})")
    for t, n in by_type.most_common():
        add(f"  {t}", f"{n:,}")

    # --- money graph ---
    mr = db["money_records"]
    total_amt = 0.0
    n_val = 0
    for d in mr.find({}, {"amount_value": 1}):
        v = d.get("amount_value")
        if isinstance(v, (int, float)):
            total_amt += v; n_val += 1
    add("money_records", f"{mr.count_documents({}):,}  (${total_amt:,.2f} across {n_val:,})")

    # --- gap counters (targets = 0) ---
    encoded = ch.count_documents({"subject": {"$regex": r"=\?[^?]+\?[bBqQ]\?"}})
    add("encoded subjects in chunks (target 0)", f"{encoded:,}", ok=(encoded == 0), warn=(encoded > 0))
    # privileged leak sentinel is per-answer; here just report privilege mix
    priv = ch.count_documents({"privilege_status": "privileged"})
    add("privileged chunks", f"{priv:,} ({_pct(priv,total)}%)")

    # --- graph ---
    add("entities / relationships",
        f"{db['entities'].count_documents({}):,} / {db['relationships'].count_documents({}):,}")
    add("events / property_dossier",
        f"{db['events'].count_documents({}):,} / {db['property_dossier'].count_documents({}):,}")

    # --- render ---
    print("=" * 66)
    print(" EVIDENCE ENGINE — HEALTH DASHBOARD")
    print("=" * 66)
    for label, value, status in rows:
        tag = "" if status == "PASS" and not label.startswith(" ") is False else ""
        mark = {"PASS": "  ", "WARN": "! ", "FAIL": "X "}[status]
        print(f" {mark}{label:<42} {value}")
    print("=" * 66)

    if args.json:
        Path(args.json).write_text(json.dumps(
            [{"label": l, "value": v, "status": st} for l, v, st in rows],
            indent=2), encoding="utf-8")
        print(f" full report -> {args.json}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
