"""Sprint 4 · 4.7 — property dossier (materialized view).

Per canonical property, precompute the standard facts a trustee/grid needs:
owner(s)+side, latest title status + version chain, insurance in force,
equity figures, mortgages/liens/chain-of-title (from grounded_facts), and
linked litigation. Stored in `property_dossier`; refreshed on demand. Powers
fast single-property answers AND the instant portfolio grid (no live agent).

Idempotent: upsert by property_id. Re-run anytime (e.g. after grounded
extraction completes) to refresh.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

COLLECTION = "property_dossier"
_GF_KEYS = ["chain_of_title", "mortgages", "liens", "lis_pendens", "judgments", "assignments"]


def _date(d):
    try:
        return d.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


# encumbrance fact types where "current vs prior-owner" matters for the grid
_ENCUMBRANCE_KEYS = ["mortgages", "liens", "lis_pendens", "judgments"]


def _fact_date(it: Dict[str, Any]):
    from src.detect.dates import parse_date
    return parse_date(it.get("dated") or it.get("recorded") or it.get("date") or "")


def _era_split(items: List[Dict[str, Any]], current_since):
    """Split encumbrance facts into current-owner era vs prior-owner vs undated.

    `current_since` = the current owner's acquisition date. Instruments dated
    on/after it are attributable to the current owner's tenure; earlier ones are
    prior-owner encumbrances (often satisfied at the conveyance); undated ones
    can't be placed. Honest, date-grounded — no guessing.
    """
    cur = prior = undated = 0
    for it in items:
        d = _fact_date(it)
        if d is None or current_since is None:
            undated += 1
        elif d >= current_since:
            cur += 1
        else:
            prior += 1
    return {"current_owner_era": cur, "prior_owner": prior, "undated": undated}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents, docs, rels = m.db["entities"], m.db["documents"], m.db["relationships"]
    doss = m.db[COLLECTION]

    # Skip retired/merged properties (is_active=False) so a merged-away
    # duplicate never gets its dossier rebuilt back into the grid.
    props = list(ents.find({"kind": "property", "is_active": {"$ne": False}}))
    doss.delete_many({"_id": {"$in": [e["_id"] for e in
                     ents.find({"kind": "property", "is_active": False}, {"_id": 1})]}})
    logger.info(f"building dossiers for {len(props)} active properties")
    n = 0
    agg_gf = 0
    for p in props:
        pid = p["_id"]
        title_ids = p.get("title_doc_ids") or []
        ins_ids = p.get("insurance_doc_ids") or []
        eq_ids = p.get("equity_doc_ids") or []
        lit_ids = p.get("litigation_doc_ids") or []

        title_docs = list(docs.find({"_id": {"$in": title_ids}}))
        # latest title = is_latest flag else max date
        def tdate(d):
            return d.get("completed_date") or d.get("search_date") or d.get("effective_date")
        latest = None
        for d in title_docs:
            if d.get("is_latest"):
                latest = d
                break
        if latest is None and title_docs:
            latest = max(title_docs, key=lambda d: (tdate(d) is not None, tdate(d)))

        # aggregate grounded facts across this property's title docs
        gf: Dict[str, List[Dict[str, Any]]] = {k: [] for k in _GF_KEYS}
        for d in title_docs:
            facts = d.get("grounded_facts") or {}
            for k in _GF_KEYS:
                for item in (facts.get(k) or []):
                    item = dict(item)
                    item["_doc_id"] = d["_id"]
                    gf[k].append(item)
        agg_gf += sum(len(v) for v in gf.values())

        ins_docs = list(docs.find({"_id": {"$in": ins_ids}},
                                  {"insurer": 1, "effective_date": 1, "expiration_date": 1,
                                   "is_cancellation": 1, "policy_year": 1}))
        ins_in_force = any(not d.get("is_cancellation") for d in ins_docs)

        # current owner's acquisition date = latest grantee date in the chain
        # of title (anchors the current-vs-prior encumbrance split).
        _acq = [_fact_date(it) for it in gf["chain_of_title"]]
        _acq = [d for d in _acq if d is not None]
        _current_since = max(_acq) if _acq else None

        # owners via OWNS edges
        owner_edges = list(rels.find({"type": "OWNS", "dst": pid}, {"src": 1}))
        owner_ids = [e["src"] for e in owner_edges]
        owners = list(ents.find({"_id": {"$in": owner_ids}},
                                {"canonical_name": 1, "side": 1, "is_david": 1}))

        dossier = {
            "_id": pid, "property_id": pid,
            "canonical_address": p.get("canonical_address"),
            "parcel_id": p.get("parcel_id"), "county": p.get("county"),
            "is_david": bool(p.get("is_david")), "side": p.get("side"),
            "owners": [{"entity_id": o["_id"], "name": o.get("canonical_name"),
                        "side": o.get("side"), "is_david": o.get("is_david")} for o in owners],
            "title": {
                "count": len(title_docs),
                "latest_doc_id": latest["_id"] if latest else None,
                "latest_date": _date(tdate(latest)) if latest else None,
                "latest_vendor": latest.get("vendor") if latest else None,
                "doc_ids": title_ids,
            },
            "insurance": {
                "count": len(ins_docs), "in_force": ins_in_force,
                "insurers": sorted({d.get("insurer") for d in ins_docs if d.get("insurer")}),
                "doc_ids": ins_ids,
            },
            "equity": {
                "equity": p.get("equity"), "mkt_value": p.get("mkt_value"),
                "mortgage_amount": p.get("mortgage_amount"),
                "re_taxes_owed": p.get("re_taxes_owed"), "lender": p.get("lender"),
                "lis_pendens": p.get("lis_pendens"),
                "active_foreclosure": p.get("active_foreclosure"),
                "fraudulent_flag": p.get("fraudulent_flag"),
                "doc_ids": eq_ids,
            },
            "litigation": {"count": len(lit_ids), "doc_ids": lit_ids},
            "grounded_facts": gf,
            "fact_counts": {k: len(gf[k]) for k in _GF_KEYS},
            # Sprint 8 grid fix: fact_counts above are CUMULATIVE (every
            # instrument ever recorded, incl. prior-owner & satisfied). Scope
            # them so the grid can show "current vs historical" instead of an
            # alarming raw total (e.g. "21 mortgages" mostly prior-owner).
            "fact_counts_basis": "cumulative_historical",
            "current_owner_since": _date(_current_since),
            "fact_counts_scoped": {k: _era_split(gf[k], _current_since)
                                   for k in _ENCUMBRANCE_KEYS},
            "has_title": bool(title_ids), "has_insurance": bool(ins_ids),
            "has_equity": bool(eq_ids), "has_litigation": bool(lit_ids),
            "refreshed_at": now,
        }
        doss.update_one({"_id": pid}, {"$set": dossier}, upsert=True)
        n += 1

    from pymongo import ASCENDING
    for keys, nm in [([("is_david", ASCENDING)], "ix_david"),
                     ([("side", ASCENDING)], "ix_side"),
                     ([("has_litigation", ASCENDING)], "ix_lit")]:
        try:
            doss.create_index(keys, name=nm)
        except Exception:  # noqa: BLE001
            pass
    logger.info(f"dossiers built={n}  grounded_facts aggregated={agg_gf}")
    logger.info(f"  david properties: {doss.count_documents({'is_david': True})}  "
                f"in-force insurance: {doss.count_documents({'insurance.in_force': True})}  "
                f"with litigation: {doss.count_documents({'has_litigation': True})}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
