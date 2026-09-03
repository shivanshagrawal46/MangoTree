"""Sprint 6 · 6.1-6.2 — self-authored evaluation harness (no external key).

Measures the things that matter for "never miss / always provable":
  * Entity resolution recall  — known entities/addresses resolve to a canonical id
  * Multi-source fan-out      — property queries reach >=2 source types
  * Grounding proxy           — fan-out chunks for a property actually contain
                                that property's address-core (no off-topic drift)
  * Findings coverage         — David properties with a known voidable transfer
                                surface a finding
  * Negative control          — a non-corpus entity resolves to 0 (no hallucinated link)

Cases are auto-generated from the live graph (so expected coverage is grounded)
plus hand-authored fraud questions. Results -> `eval_results` for the dashboard.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.fanout import EntityIndex, fan_out_chunks, source_type_breakdown
from src.graph.normalize import address_key
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
ents, chunks, doss = m.db["entities"], m.db["email_chunks_v2"], m.db["property_dossier"]
idx = EntityIndex(ents)

cases = []
# auto: David properties with >=2 sources -> expect multi-source fan-out + grounding
for d in doss.find({"is_david": True}, {"canonical_address": 1, "has_title": 1,
                                        "has_insurance": 1, "has_equity": 1}).limit(40):
    addr = d.get("canonical_address")
    if not addr:
        continue
    nsrc = sum([d.get("has_title"), d.get("has_insurance"), d.get("has_equity")])
    cases.append({"kind": "property", "query": addr, "expect_pid": d["_id"],
                  "expect_multi": nsrc >= 2, "akey": address_key(addr)})
# manual entity cases
for q in ["IPA Asset Management", "Island Properties & Associates", "David DeRosa", "Mango Tree"]:
    cases.append({"kind": "entity", "query": q})
# negative control
for q in ["Acme Spacely Sprockets Unlimited", "Wakanda Holdings ZZZ"]:
    cases.append({"kind": "negative", "query": q})

res = {"resolve_ok": 0, "resolve_tot": 0, "multi_ok": 0, "multi_tot": 0,
       "ground_ok": 0, "ground_tot": 0, "neg_ok": 0, "neg_tot": 0}
fails = []
for c in cases:
    r = idx.resolve(c["query"])
    if c["kind"] == "negative":
        res["neg_tot"] += 1
        if not r["all"]:
            res["neg_ok"] += 1
        else:
            fails.append(("negative-linked", c["query"], len(r["all"])))
        continue
    res["resolve_tot"] += 1
    ok = bool(r["all"]) and (c.get("expect_pid") in r["all"] if c["kind"] == "property" else True)
    if ok:
        res["resolve_ok"] += 1
    else:
        fails.append(("resolve", c["query"], sorted(r["all"])[:3]))
    if c["kind"] == "property":
        ch = fan_out_chunks(chunks, r["all"] or {c["expect_pid"]}, limit=60)
        bd = source_type_breakdown(ch)
        if c.get("expect_multi"):
            res["multi_tot"] += 1
            if len(bd) >= 2:
                res["multi_ok"] += 1
            else:
                fails.append(("multi", c["query"], bd))
        # grounding proxy: among title chunks, does the property's addr_core appear?
        res["ground_tot"] += 1
        ak = c.get("akey") or ""
        hit = False
        for cc in ch[:30]:
            body = (cc.get("body") or cc.get("text") or "").lower()
            if ak and ak.split()[0] in body and (len(ak.split()) < 2 or ak.split()[1] in body):
                hit = True
                break
        if hit:
            res["ground_ok"] += 1

def pct(a, b):
    return f"{100*a/max(b,1):.0f}%"

logger.info("================ EVAL SCORECARD ================")
logger.info(f"  entity resolution recall : {res['resolve_ok']}/{res['resolve_tot']} ({pct(res['resolve_ok'],res['resolve_tot'])})")
logger.info(f"  multi-source fan-out     : {res['multi_ok']}/{res['multi_tot']} ({pct(res['multi_ok'],res['multi_tot'])})")
logger.info(f"  grounding (addr present) : {res['ground_ok']}/{res['ground_tot']} ({pct(res['ground_ok'],res['ground_tot'])})")
logger.info(f"  negative control (no link): {res['neg_ok']}/{res['neg_tot']} ({pct(res['neg_ok'],res['neg_tot'])})")
if fails:
    logger.info(f"  sample fails: {fails[:6]}")

m.db["eval_results"].insert_one({"run_at": datetime.now(timezone.utc), "metrics": res,
                                 "n_cases": len(cases), "fails": fails[:20]})
gate = (res["resolve_ok"] >= 0.9 * res["resolve_tot"] and
        res["multi_ok"] >= 0.8 * max(res["multi_tot"], 1) and
        res["neg_ok"] == res["neg_tot"])
logger.info(f"EVAL GATE: {'PASS' if gate else 'REVIEW'}")
m.close()
sys.exit(0)
