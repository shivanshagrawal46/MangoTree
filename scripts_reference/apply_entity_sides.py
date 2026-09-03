"""Sprint 3 · apply the user's locked entity-side classifications.

`side` taxonomy (canonical):
  our_side      - Mango Tree (Rakesh Sir's team) + our people/attorneys
  david_network - David + his shells/agents (IPA, Island Properties, address-coded LLCs...)
  third_party   - neutral third parties (GMR investor, title vendors, insurers)
  co_victim     - fellow fraud victims like us (Brian Detmer / his entities)
  unknown       - not yet classified

Only UNAMBIGUOUS assignments are made here. Combined/garbled multi-party
entities are flagged `needs_split=True` (resolved in people/LLC resolution),
never given a single misleading side. Idempotent.
"""
from __future__ import annotations
from datetime import datetime, timezone
from config.settings import Settings
from src.db.mongo import MongoClientWrapper

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
ents = m.db["entities"]
now = datetime.now(timezone.utc)


def set_side(eid: str, side: str, **extra):
    r = ents.update_one({"_id": eid}, {"$set": {"side": side, "side_source": "user_decision_2026_06_15",
                                                 "updated_at": now, **extra}})
    print(f"  {eid:55s} -> side={side}" + (" (not found)" if r.matched_count == 0 else ""))


# --- our side ---
set_side("ent_org_mangotree", "our_side", is_ours=True, is_david=False, is_david_network=False)

# --- David network (already is_david=True; stamp side for consistency) ---
ents.update_many({"is_david": True}, {"$set": {"side": "david_network", "updated_at": now}})
print(f"  stamped side=david_network on all is_david=True ({ents.count_documents({'is_david': True})})")

# --- third party investor: GMR (standalone only) ---
set_side("ent_per_gmr_real_estate_holdings_lp", "third_party", is_david=False, is_david_network=False)

# --- co-victim: Brian Detmer ---
set_side("ent_per_brian_detmer", "co_victim", is_david=False, is_david_network=False)

# --- flag combined/multi-party entities for split (do NOT set a single side) ---
COMBINED_MARKERS = [" & ", " AND ", ", L.P. AND", "ET AL", " GMR ", "DIRECTIONAL"]
flagged = 0
for e in ents.find({"kind": {"$in": ["llc", "person"]}}):
    nm = (e.get("canonical_name") or "")
    up = nm.upper()
    n_amp = up.count("&") + up.count(" AND ")
    if n_amp >= 1 and any(mk in up for mk in COMBINED_MARKERS) and len(nm) > 40:
        ents.update_one({"_id": e["_id"]}, {"$set": {
            "needs_split": True, "needs_review": True,
            "split_reason": "multi_party_owner_string", "updated_at": now}})
        flagged += 1
print(f"  flagged needs_split on {flagged} multi-party entities")

print("\n=== side distribution now ===")
import collections
c = collections.Counter(e.get("side") for e in ents.find({}, {"side": 1}))
for k, v in c.most_common():
    print(f"   {k}: {v}")
print("   needs_split:", ents.count_documents({"needs_split": True}))
m.close()
