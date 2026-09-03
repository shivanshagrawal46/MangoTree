"""Targeted manual split of the mixed-side combined LLC record on 2034 Route 44.

The auto-resolver (resolve_entities) routes this 4-LLC comma string to human
review. Per user decision (2026-06-17):
  2034 LLC               -> david_network (address-coded for 2034 Route 44)
  7 Harding Realty LLC   -> david_network
  No Nebraska Realty LLC -> co_victim  (Brian's)
  Washington New Realty  -> co_victim  (Brian's; reuse existing entity)

Splitting PRESERVES the connection (each component keeps an OWNS edge to the
same property) while making each individually correct + side-labelled — so the
system can say "both David's and Brian's LLCs are involved in 2034 Route 44".

  python -m scripts.split_combined_2034            # DRY-RUN
  python -m scripts.split_combined_2034 --live      # apply
Idempotent.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.normalize import norm_name, slug
from src.graph.schema import SIDE_DAVID, SIDE_COVICTIM
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.logger import logger

BLOB_ID = ("ent_llc_no_nebraska_realty_llc_2034_llc_7_harding_realty_llc_"
           "washington_new_realty_llc")

COMPONENTS = [
    {"name": "2034 LLC", "side": SIDE_DAVID, "is_david": True},
    {"name": "7 Harding Realty LLC", "side": SIDE_DAVID, "is_david": True},
    {"name": "No Nebraska Realty LLC", "side": SIDE_COVICTIM, "is_david": False},
    {"name": "Washington New Realty LLC", "side": SIDE_COVICTIM, "is_david": False},
]


def main() -> int:
    live = "--live" in sys.argv
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents, docs, rels, chunks = (m.db["entities"], m.db["documents"],
                                m.db["relationships"], m.db["email_chunks_v2"])

    blob = ents.find_one({"_id": BLOB_ID})
    if not blob:
        logger.error(f"blob {BLOB_ID} not found (already split?)")
        m.close()
        return 0

    # properties the blob is linked to (OWNS edges + owner docs)
    prop_ids = {e["dst"] for e in rels.find({"type": "OWNS", "src": BLOB_ID}, {"dst": 1})}
    owner_docs = list(docs.find({"owner_entity_id": BLOB_ID}, {"_id": 1, "property_ids": 1}))
    for d in owner_docs:
        prop_ids.update(d.get("property_ids") or [])
    n_chunks = chunks.count_documents({"entity_ids": BLOB_ID})

    logger.info(f"=== split 2034 blob ({'LIVE' if live else 'DRY-RUN'}) ===")
    logger.info(f"blob linked to properties: {sorted(prop_ids) or '(none)'} | "
                f"owner_docs={len(owner_docs)} | chunks referencing blob={n_chunks}")

    # resolve/create each component
    comp_ids = []
    for c in COMPONENTS:
        existing = ents.find_one({"kind": "llc", "name_norm": norm_name(c["name"])},
                                 {"_id": 1, "side": 1})
        cid = existing["_id"] if existing else "ent_llc_" + slug(c["name"])
        comp_ids.append(cid)
        logger.info(f"  component {c['name']!r} -> {cid} side={c['side']} "
                    f"is_david={c['is_david']}" + ("  (existing)" if existing else "  (NEW)"))
        if live:
            ents.update_one({"_id": cid}, {"$set": {
                "_id": cid, "kind": "llc", "matter_id": DEFAULT_MATTER_ID,
                "canonical_name": c["name"], "name_norm": norm_name(c["name"]),
                "side": c["side"], "is_david": c["is_david"],
                "is_david_network": c["is_david"], "is_ours": False,
                "side_source": "manual_split_2034_user_2026_06_17",
                "split_parent": BLOB_ID, "source": "manual_split", "updated_at": now},
                "$addToSet": {"aliases": c["name"]},
                "$setOnInsert": {"created_at": now}}, upsert=True)

    if live:
        # re-point OWNS edges: every component owns every linked property
        for cid in comp_ids:
            for pid in prop_ids:
                rels.update_one({"type": "OWNS", "src": cid, "dst": pid}, {"$set": {
                    "type": "OWNS", "src": cid, "dst": pid, "confidence": 0.9,
                    "source": "manual_split_2034", "updated_at": now}}, upsert=True)
        rels.delete_many({"type": "OWNS", "src": BLOB_ID})
        # re-point owner docs
        primary = comp_ids[0]
        for d in owner_docs:
            docs.update_one({"_id": d["_id"]}, {"$set": {
                "owner_entity_id": primary, "owner_entity_ids": comp_ids, "updated_at": now}})
        # re-link chunks: swap blob id -> the 4 components
        david_ids = {comp_ids[i] for i, c in enumerate(COMPONENTS) if c["is_david"]}
        for ch in chunks.find({"entity_ids": BLOB_ID},
                              {"entity_ids": 1, "entity_refs": 1, "entity_sides": 1}):
            ids = set(ch.get("entity_ids") or []); ids.discard(BLOB_ID); ids |= set(comp_ids)
            refs = ch.get("entity_refs") or {}
            llcs = set(refs.get("llcs") or []); llcs.discard(BLOB_ID); llcs |= set(comp_ids)
            refs["llcs"] = sorted(llcs)
            sides = set(ch.get("entity_sides") or []) | {c["side"] for c in COMPONENTS}
            chunks.update_one({"_id": ch["_id"]}, {"$set": {
                "entity_ids": sorted(ids), "entity_refs": refs,
                "entity_sides": sorted(s for s in sides if s),
                "touches_david": bool(ids & david_ids) or ch.get("touches_david", False)}})
        # retire the blob
        ents.update_one({"_id": BLOB_ID}, {"$set": {
            "is_active": False, "needs_split": False, "needs_review": False,
            "split_status": "done", "superseded_by": comp_ids, "updated_at": now}})
        logger.info(f"  APPLIED: 4 components live, OWNS re-pointed to "
                    f"{len(prop_ids)} property(ies), {n_chunks} chunk(s) relinked, blob retired")
    else:
        logger.info("  DRY-RUN — re-run with --live to apply.")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
