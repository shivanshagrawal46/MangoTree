"""Sprint 5 + 6 gates (5.8, 6.5) + provenance/clean-mode self-tests.

5.8: timeline returns a correctly-ordered cited chronology; flow-of-funds works;
     Clean mode leaks ZERO privileged chunks.
6.5: latest eval scorecard meets targets (resolution >=0.9, multi-source >=0.8,
     zero hallucinated links).
"""
import sys
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.timeline.builder import timeline_for, flow_of_funds
from src.graph.fanout import EntityIndex, fan_out_chunks
from src.rag.provenance import clean_mode_filter, provenance_footer, is_clean_safe
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
db = m.db
ents, chunks = db["entities"], db["email_chunks_v2"]
idx = EntityIndex(ents)

# pick a David property with rich events
p = ents.find_one({"kind": "property", "is_david": True}, {"_id": 1, "canonical_address": 1},
                  sort=[("_id", 1)])
pid = p["_id"]

# 5.8a timeline ordered + cited
tl = timeline_for(m, property_id=pid, limit=50)
ordered = all((tl[i]["date"] or "") <= (tl[i + 1]["date"] or "9999") for i in range(len(tl) - 1))
cited = all(e.get("source_quote") is not None for e in tl) if tl else False

# 5.8b flow of funds
fof = flow_of_funds(m, property_id=pid)

# 5.8c clean-mode leak test: fan out with clean filter -> 0 privileged
res = idx.resolve(p.get("canonical_address") or "")
clean_chunks = fan_out_chunks(chunks, res["all"] or {pid}, exclude_privileged=True, limit=80)
leaks = sum(0 if is_clean_safe(c) else 1 for c in clean_chunks)
foot = provenance_footer(clean_chunks, mode="clean")

logger.info("================ SPRINT 5 GATE ================")
logger.info(f"  timeline: {len(tl)} events ordered={ordered} cited={cited}")
logger.info(f"  flow_of_funds: {fof['n_events']} events, total seen {fof['total_amount_seen']}")
logger.info(f"  clean-mode chunks={len(clean_chunks)} privileged_leaks={leaks} "
            f"footer.clean_mode_leak={foot['clean_mode_leak']}")
s5 = ordered and (cited or not tl) and leaks == 0 and not foot["clean_mode_leak"]
logger.info(f"SPRINT 5 GATE: {'PASS' if s5 else 'REVIEW'}")

# 6.5 eval targets
ev = db["eval_results"].find_one(sort=[("run_at", -1)])
met = (ev or {}).get("metrics", {})
def rate(a, b):
    return met.get(a, 0) / max(met.get(b, 1), 1)
s6 = bool(met) and rate("resolve_ok", "resolve_tot") >= 0.9 and \
    rate("multi_ok", "multi_tot") >= 0.8 and met.get("neg_ok") == met.get("neg_tot")
logger.info("================ SPRINT 6 GATE ================")
logger.info(f"  latest eval: {met}")
logger.info(f"SPRINT 6 GATE: {'PASS' if s6 else 'REVIEW'}")
m.close()
sys.exit(0 if (s5 and s6) else 1)
