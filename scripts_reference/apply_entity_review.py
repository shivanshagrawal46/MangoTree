"""Sprint 3 · 3.5.4 — alias-learning loop. Apply human-CONFIRMED entity_review
merge decisions: merge the duplicate into the canonical, union aliases (so the
resolver "learns" the variant), and re-point all references. Idempotent.

A reviewer sets entity_review.status='confirmed' (+ optional 'canonical' = which
id to keep). This script then executes those merges. Running with none confirmed
is a safe no-op — the loop is in place for when reviews happen.

  python -m scripts.apply_entity_review            # apply confirmed merges
"""
import sys
from datetime import datetime, timezone
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
ents, docs, rels = m.db["entities"], m.db["documents"], m.db["relationships"]
review = m.db["entity_review"]
now = datetime.now(timezone.utc)

confirmed = list(review.find({"kind": "entity_merge_candidate", "status": "confirmed"}))
logger.info(f"{len(confirmed)} confirmed merges to apply")
applied = 0
for r in confirmed:
    a, b = r["a"], r["b"]
    ea, eb = ents.find_one({"_id": a}), ents.find_one({"_id": b})
    if not ea or not eb:
        continue
    # canonical = explicit choice, else the one with more aliases
    canon = r.get("canonical") or (a if len(ea.get("aliases") or []) >= len(eb.get("aliases") or []) else b)
    dup = b if canon == a else a
    cdoc, ddoc = (ea if canon == a else eb), (eb if canon == a else ea)
    aliases = sorted(set((cdoc.get("aliases") or []) + (ddoc.get("aliases") or []) +
                         [ddoc.get("canonical_name")]) - {None})
    docs.update_many({"owner_entity_id": dup}, {"$set": {"owner_entity_id": canon, "updated_at": now}})
    docs.update_many({"owner_entity_ids": dup}, {"$addToSet": {"owner_entity_ids": canon}})
    docs.update_many({}, {"$pull": {"owner_entity_ids": dup}})
    rels.update_many({"src": dup}, {"$set": {"src": canon}})
    rels.update_many({"dst": dup}, {"$set": {"dst": canon}})
    ents.update_one({"_id": canon}, {"$set": {"aliases": aliases,
                    "is_david": bool(cdoc.get("is_david") or ddoc.get("is_david")),
                    "updated_at": now}})
    ents.update_one({"_id": dup}, {"$set": {"is_active": False, "merged_into": canon, "updated_at": now}})
    review.update_one({"_id": r["_id"]}, {"$set": {"status": "applied", "applied_at": now}})
    applied += 1
    logger.info(f"  merged {dup} -> {canon} (learned aliases: {aliases})")

logger.info(f"alias-learning: {applied} merges applied; "
            f"{review.count_documents({'status': 'pending'})} still pending review")
m.close()
sys.exit(0)
