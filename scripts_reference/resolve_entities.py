"""Sprint 3 · 3.1 — split combined multi-party owner entities into canonical
components and re-point document owners + OWNS edges. Dry-run by default.

  python -m scripts.resolve_entities            # dry-run (prints plan)
  python -m scripts.resolve_entities --live      # apply

Idempotent: retired combined entities get is_active=False + superseded_by; a
second run finds nothing left to split. Conservative: a combined entity whose
split is ambiguous (only 1 messy component, or >5 components) is left flagged
needs_review instead of guessed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.normalize import norm_name
from src.graph.resolve import (split_owner_string, resolve_component,
                               is_junk_component, has_internal_repeat)
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.logger import logger

KNOWN_DAVID = {norm_name(x) for x in [
    "IPA ASSET MANAGEMENT LLC", "IPA REALTY LLC", "ISLAND PROPERTIES & ASSOCIATES LLC",
    "31FO LLC", "27 WASHINGTON REALTY LLC", "DIRECTIONAL LENDING LLC",
]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents, docs, rels = m.db["entities"], m.db["documents"], m.db["relationships"]

    combined = list(ents.find({"needs_split": True}))
    logger.info(f"{len(combined)} combined entities to resolve "
                f"({'LIVE' if args.live else 'DRY-RUN'})")
    n_split = n_merge = n_review = n_new = n_edges = 0

    for e in combined:
        eid = e["_id"]
        raw = e.get("canonical_name") or ""
        owner_docs = list(docs.find({"owner_entity_id": eid},
                                    {"_id": 1, "property_ids": 1, "property_address": 1}))
        addr = next((d.get("property_address") for d in owner_docs if d.get("property_address")), "")
        comps = split_owner_string(raw)
        bad = [c for c in comps if is_junk_component(c)]
        if len(comps) == 1 and has_internal_repeat(comps[0]):
            bad.append(comps[0] + " (ocr-doubled)")

        if len(comps) == 0 or len(comps) > 5 or bad:
            n_review += 1
            why = (f"junk={bad}" if bad else f"{len(comps)} comps")
            logger.info(f"  REVIEW  {eid}\n     '{raw}' -> {why}")
            if args.live:
                ents.update_one({"_id": eid}, {"$set": {"needs_review": True,
                                "split_status": "needs_human", "updated_at": now}})
            continue

        resolved = [resolve_component(ents, c, addr, KNOWN_DAVID) for c in comps]
        kind_word = "MERGE" if len(comps) == 1 else "SPLIT"
        logger.info(f"  {kind_word:6s} {eid}")
        logger.info(f"     raw: '{raw}'")
        for c, r in zip(comps, resolved):
            logger.info(f"       -> {c!r}  [{r['entity_id']}] "
                        f"david={r['is_david']} {r['matched_on']}"
                        + ("  (NEW)" if r["created"] else ""))

        if not args.live:
            if len(comps) == 1:
                n_merge += 1
            else:
                n_split += 1
            n_new += sum(1 for r in resolved if r["created"])
            continue

        # ---- LIVE apply ----
        comp_ids: List[str] = []
        for r in resolved:
            cid = r["entity_id"]
            comp_ids.append(cid)
            if r["created"]:
                ents.update_one({"_id": cid}, {"$set": {
                    "_id": cid, "kind": r["kind"], "matter_id": DEFAULT_MATTER_ID,
                    "canonical_name": r["canonical_name"], "name_norm": r["name_norm"],
                    "aliases": [r["canonical_name"]],
                    "is_david": r["is_david"], "is_david_network": r["is_david"],
                    "side": r["side"], "david_flag_reason": r.get("david_flag_reason"),
                    "needs_review": not r["is_david"],
                    "source": "split_from_combined", "split_parent": eid,
                    "updated_at": now}, "$setOnInsert": {"created_at": now}}, upsert=True)
                n_new += 1
        # primary owner = first David component, else first
        primary = next((r["entity_id"] for r in resolved if r["is_david"]), comp_ids[0])
        prop_ids: set = set()
        for d in owner_docs:
            docs.update_one({"_id": d["_id"]}, {"$set": {
                "owner_entity_id": primary, "owner_entity_ids": comp_ids, "updated_at": now}})
            for pid in (d.get("property_ids") or []):
                prop_ids.add(pid)
        # OWNS edges: every component owns every linked property (with provenance)
        for cid in comp_ids:
            for pid in prop_ids:
                rels.update_one({"type": "OWNS", "src": cid, "dst": pid},
                                {"$set": {"type": "OWNS", "src": cid, "dst": pid,
                                          "confidence": 0.9, "source": "owner_split",
                                          "updated_at": now}}, upsert=True)
                n_edges += 1
        # retire combined node + its stale OWNS edges
        rels.delete_many({"type": "OWNS", "src": eid})
        ents.update_one({"_id": eid}, {"$set": {
            "is_active": False, "needs_split": False, "split_status": "done",
            "superseded_by": comp_ids, "updated_at": now}})
        if len(comps) == 1:
            n_merge += 1
        else:
            n_split += 1

    logger.info("================ RESOLVE SUMMARY ================")
    logger.info(f"split={n_split} merge={n_merge} review={n_review} "
                f"new_entities={n_new} owns_edges={n_edges} "
                f"({'APPLIED' if args.live else 'DRY-RUN — re-run with --live'})")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
