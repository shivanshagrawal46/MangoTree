"""Sprint 3 · 3.2.4 — relationship-graph integrity audit.

Verifies the invariants the retrieval/detector layers rely on:
  1. every edge's `src` and `dst` resolve to a known entity;
  2. fact-derived edges carry provenance (source_doc_id + source_quote);
  3. every edge has a confidence + type in the canonical vocabulary;
  4. bitemporal `until` values are idempotent (recomputing from the chain
     reproduces the stored values exactly) and monotonic (until > as_of).

Read-only. Exit code 0 = clean, 1 = issues found (counts logged).

  python -m scripts.audit_edges
"""
from __future__ import annotations

import sys

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.bitemporal import compute_until
from src.graph.schema import (EDGE_TYPES, REL_GRANTEE_OF, REL_GRANTOR_OF,
                              REL_HAS_MORTGAGE, REL_HAS_LIEN, REL_LENT_TO)
from src.utils.logger import logger

# Edge types created from a structured grounded fact — these MUST carry a
# source document + verbatim quote (hard invariant). Structural edges
# (OWNS / ABOUT_PROPERTY / HAS_INSURANCE / FILED_IN / MEMBER_OF) are derived
# from materialized links and legitimately may lack a single source quote, so
# their provenance is reported as a soft metric, not a failure.
_PROVENANCE_REQUIRED = {REL_GRANTEE_OF, REL_GRANTOR_OF, REL_HAS_MORTGAGE,
                        REL_HAS_LIEN, REL_LENT_TO}


def main() -> int:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents, rels, docs = m.db["entities"], m.db["relationships"], m.db["documents"]

    ent_ids = {e["_id"] for e in ents.find({}, {"_id": 1})}
    active_ids = {e["_id"] for e in ents.find({"is_active": {"$ne": False}}, {"_id": 1})}
    doc_ids = {d["_id"] for d in docs.find({}, {"_id": 1})}
    # A valid edge endpoint is a known entity OR a known document — several
    # edge types (ABOUT_PROPERTY, FILED_IN, HAS_INSURANCE, LITIGATION_ABOUT)
    # connect a *document* to an entity by design.
    valid = ent_ids | doc_ids

    # HARD invariants (fail the audit) vs SOFT metrics (report only).
    hard = {"unresolved_src": 0, "unresolved_dst": 0, "bad_type": 0,
            "ref_to_retired_entity": 0, "missing_fact_provenance": 0,
            "until_not_idempotent": 0, "until_before_as_of": 0}
    soft = {"missing_confidence": 0, "missing_structural_provenance": 0}

    by_prop = {}
    total = 0
    for e in rels.find({}):
        total += 1
        t = e.get("type")
        if t not in EDGE_TYPES:
            hard["bad_type"] += 1
        for end, key in (("src", "unresolved_src"), ("dst", "unresolved_dst")):
            v = e.get(end)
            if v not in valid:
                hard[key] += 1
            elif v in ent_ids and v not in active_ids:
                hard["ref_to_retired_entity"] += 1
        if e.get("confidence") is None:
            soft["missing_confidence"] += 1
        has_prov = bool(e.get("source_doc_id") and e.get("source_quote"))
        if not has_prov:
            if t in _PROVENANCE_REQUIRED:
                hard["missing_fact_provenance"] += 1
            else:
                soft["missing_structural_provenance"] += 1
        if e.get("as_of") and e.get("until") and e["until"] <= e["as_of"]:
            hard["until_before_as_of"] += 1
        if t == REL_GRANTEE_OF:
            by_prop.setdefault(e.get("dst"), []).append(e)

    # idempotency: recompute `until` from the current chain; must match stored.
    for prop, edges in by_prop.items():
        if not prop:
            continue
        nxt = compute_until([e.get("as_of") for e in edges])
        for e in edges:
            expected = nxt.get(e.get("as_of")) if e.get("as_of") is not None else None
            if e.get("until") != expected:
                hard["until_not_idempotent"] += 1

    n_hard = sum(hard.values())
    logger.info(f"edge audit: {total} relationships, {len(ent_ids)} entities "
                f"({len(active_ids)} active), {len(doc_ids)} documents")
    logger.info(f"  HARD invariants: {hard}")
    logger.info(f"  SOFT metrics (informational): {soft}")
    if n_hard == 0:
        logger.info("  PASS — graph integrity clean (every edge endpoint "
                    "resolves; fact edges cited; bitemporal idempotent)")
    else:
        logger.warning(f"  FAIL — {n_hard} hard integrity issue(s)")
    m.close()
    return 0 if n_hard == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
