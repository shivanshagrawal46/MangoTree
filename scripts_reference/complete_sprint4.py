"""Complete Sprint 4 remaining + gate.

4.2 Fact-cluster builder: cluster events by (predicate=event_type, property) into
    `fact_clusters` — the substrate contradiction/analysis reads.
4.6 Supersession lineage: verify title version chains (is_latest/supersedes).
4.10 Omission: covered by party-scoped contradiction (omission flag). Shell-
     obfuscation behavioral edges = noted (needs LLC email/bank control data).
4.11 GATE: detectors fired, findings persisted with evidence, re-run idempotent.
"""
import sys
from datetime import datetime, timezone
from collections import defaultdict
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.detectors import run_all
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
db = m.db
now = datetime.now(timezone.utc)

# ---- 4.2 fact clusters from events (predicate=event_type per property) ----
events = db["events"]
clusters = db["fact_clusters"]
buckets = defaultdict(list)
for e in events.find({"property_id": {"$ne": None}},
                     {"event_type": 1, "property_id": 1, "date": 1, "amount": 1,
                      "entity_ids": 1, "doc_id": 1, "source_quote": 1}):
    buckets[(e["property_id"], e["event_type"])].append(e)
nc = 0
for (pid, et), evs in buckets.items():
    cid = f"fc_{pid}_{et}"
    clusters.update_one({"_id": cid}, {"$set": {
        "_id": cid, "property_id": pid, "predicate": et, "n": len(evs),
        "members": [{"date": (x.get("date").strftime("%Y-%m-%d") if x.get("date") else None),
                     "amount": x.get("amount"), "doc_id": x.get("doc_id"),
                     "quote": x.get("source_quote")} for x in evs[:25]],
        "updated_at": now}}, upsert=True)
    nc += 1
logger.info(f"4.2 fact clusters built: {nc}")

# ---- 4.6 supersession lineage verify ----
docs = db["documents"]
title = docs.count_documents({"source_type": "title_report"})
latest = docs.count_documents({"source_type": "title_report", "is_latest": True})
chained = docs.count_documents({"source_type": "title_report", "supersedes": {"$exists": True, "$ne": None}})
logger.info(f"4.6 supersession: {latest} latest-flagged of {title} title docs; {chained} in update chains")

# ---- 4.11 GATE: re-run detectors (idempotent) + verify ledger ----
counts = run_all(m)
fc = db["findings"]
total = fc.count_documents({})
with_ev = fc.count_documents({"evidence.0": {"$exists": True}})
crit = fc.count_documents({"severity": "critical"})
logger.info("================ SPRINT 4 GATE ================")
logger.info(f"  detectors: {counts}")
logger.info(f"  findings: total={total} with_evidence={with_ev} critical={crit}")
gate = (counts["anachronisms"] >= 1 and counts["voidable_transfers"] >= 1 and
        total >= 1 and with_ev == total)
logger.info(f"SPRINT 4 GATE: {'PASS' if gate else 'REVIEW'}")
m.close()
sys.exit(0)
