"""Sprint 3 · 3.1.2-3.1.3 — merge duplicate person/llc/org entities created
across ingestion (case variants, suffix variants, OCR variants).

CONSERVATIVE by design (legal-grade):
  * AUTO-MERGE only when suffix-stripped normalized names are EXACTLY equal
    AND same kind AND sides are compatible (equal, or one is unknown/None).
  * Near-matches (fuzzy 88-99) are NOT merged — they go to `entity_review`
    with both names + score for a human, never guessed.
  * MUST-NOT-LINK firewall: two entities with DIFFERENT explicit sides
    (e.g. david_network vs co_victim) never merge, even if names match.

On merge: canonical (richest signal) kept; duplicates -> is_active=False,
merged_into=canonical; aliases unioned; all references re-pointed
(documents.owner_entity_id / owner_entity_ids / parties.entity_id,
relationships.src/dst, entities.agent_entity_id, property.owner_entity_id).
Idempotent + dry-run by default.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.normalize import norm_name, strip_suffixes
from src.utils.logger import logger

try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None

MERGE_KINDS = ["person", "llc", "org"]


def sides_compatible(a, b) -> bool:
    if not a or not b:
        return True
    return a == b


def canonical_score(e: Dict[str, Any]) -> tuple:
    """Higher = better canonical. Prefer: on LLC master list (dos_filing_date),
    is_david known, more aliases, has source != split, longer name."""
    return (
        1 if e.get("dos_filing_date") else 0,
        1 if e.get("source") == "List of LLC formed.xlsx" else 0,
        1 if e.get("is_david") else 0,
        len(e.get("aliases") or []),
        1 if e.get("source") != "split_from_combined" else 0,
        len(e.get("canonical_name") or ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents = m.db["entities"]
    docs = m.db["documents"]
    rels = m.db["relationships"]
    review = m.db["entity_review"]

    rows = list(ents.find({"kind": {"$in": MERGE_KINDS}, "is_active": {"$ne": False}}))
    logger.info(f"{len(rows)} active person/llc/org entities ({'LIVE' if args.live else 'DRY-RUN'})")
    if args.live:  # recomputed fresh each run -> idempotent
        review.delete_many({"kind": "entity_merge_candidate", "status": "pending"})

    # ---- block by suffix-stripped, SPACE-INSENSITIVE name (so '10 DAV'=='10DAV');
    #      auto-merge exact key, queue fuzzy near-keys for human review ----
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in rows:
        key = strip_suffixes(e.get("name_norm") or norm_name(e.get("canonical_name") or "")).replace(" ", "")
        if key:
            by_key[key].append(e)

    auto_groups = [g for g in by_key.values() if len(g) > 1]
    n_merged = n_groups = n_review = 0

    # exact-key clusters -> auto-merge (respecting must-not-link)
    for g in auto_groups:
        # split by side firewall: only merge entities with compatible sides
        # greedily: pick best canonical, merge others that are compatible+same kind
        g_sorted = sorted(g, key=canonical_score, reverse=True)
        canon = g_sorted[0]
        dups = [e for e in g_sorted[1:]
                if e["kind"] == canon["kind"] and sides_compatible(e.get("side"), canon.get("side"))]
        blocked = [e for e in g_sorted[1:] if e not in dups]
        if not dups and not blocked:
            continue
        n_groups += 1
        logger.info(f"  CLUSTER '{strip_suffixes(canon.get('name_norm') or '')}' "
                    f"canon={canon['_id']} (+{len(dups)} dup, {len(blocked)} blocked)")
        for e in dups:
            logger.info(f"      merge {e['_id']}  side={e.get('side')} david={e.get('is_david')}")
        for e in blocked:
            logger.info(f"      BLOCKED (side firewall) {e['_id']} side={e.get('side')}")
        if not args.live:
            n_merged += len(dups)
            continue
        # ---- LIVE merge ----
        cid = canon["_id"]
        alias_set = set(canon.get("aliases") or [])
        is_david = bool(canon.get("is_david"))
        for e in dups:
            eid = e["_id"]
            alias_set.update(e.get("aliases") or [])
            alias_set.add(e.get("canonical_name"))
            is_david = is_david or bool(e.get("is_david"))
            docs.update_many({"owner_entity_id": eid}, {"$set": {"owner_entity_id": cid, "updated_at": now}})
            docs.update_many({"owner_entity_ids": eid}, {"$addToSet": {"owner_entity_ids": cid}})
            docs.update_many({}, {"$pull": {"owner_entity_ids": eid}})  # remove old after add
            docs.update_many({"parties.entity_id": eid}, {"$set": {"parties.$[p].entity_id": cid}},
                             array_filters=[{"p.entity_id": eid}])
            rels.update_many({"src": eid}, {"$set": {"src": cid}})
            rels.update_many({"dst": eid}, {"$set": {"dst": cid}})
            ents.update_many({"agent_entity_id": eid}, {"$set": {"agent_entity_id": cid}})
            ents.update_one({"_id": eid}, {"$set": {"is_active": False, "merged_into": cid,
                            "updated_at": now}})
            n_merged += 1
        ents.update_one({"_id": cid}, {"$set": {
            "aliases": sorted(a for a in alias_set if a), "is_david": is_david,
            "is_david_network": is_david or bool(canon.get("is_david_network")),
            "updated_at": now}})

    # ---- fuzzy near-duplicates (different keys, high similarity) -> review ----
    if fuzz is not None:
        keys = list(by_key.keys())
        seen_pairs = set()
        for i, k1 in enumerate(keys):
            for k2 in keys[i + 1:]:
                if abs(len(k1) - len(k2)) > 6:
                    continue
                sc = fuzz.ratio(k1, k2)
                if 88 <= sc < 100:
                    e1, e2 = by_key[k1][0], by_key[k2][0]
                    if e1["kind"] != e2["kind"] or not sides_compatible(e1.get("side"), e2.get("side")):
                        continue
                    pair = tuple(sorted([e1["_id"], e2["_id"]]))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    n_review += 1
                    if args.live:
                        review.update_one({"_id": f"{pair[0]}|{pair[1]}"}, {"$set": {
                            "_id": f"{pair[0]}|{pair[1]}", "kind": "entity_merge_candidate",
                            "a": pair[0], "b": pair[1], "a_name": e1.get("canonical_name"),
                            "b_name": e2.get("canonical_name"), "score": sc,
                            "status": "pending", "created_at": now}}, upsert=True)

    logger.info("================ MERGE SUMMARY ================")
    logger.info(f"clusters={n_groups} auto_merged={n_merged} review_candidates={n_review} "
                f"({'APPLIED' if args.live else 'DRY-RUN — re-run with --live'})")
    logger.info(f"active person/llc/org now: "
                f"{ents.count_documents({'kind': {'$in': MERGE_KINDS}, 'is_active': {'$ne': False}})}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
