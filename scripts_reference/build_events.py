"""Sprint 5 · 5.1 — event store. Every DATED fact becomes one indexed row in
`events/`, so timeline questions are date-range queries, not LLM reasoning.

Sources (all already in the DB):
  * grounded_facts on title docs: conveyance, mortgage, lien, judgment,
    lis_pendens, assignment (each with verbatim source_quote)
  * insurance docs: policy_effective / policy_cancelled
  * litigation docs: litigation_update
  * title docs themselves: title_search (as-of)

Each event: {event_type, date, date_kind, entity_ids[], property_id, doc_id,
source_quote, amount?, detail}. Deterministic _id -> idempotent. Undated facts
are skipped here (still available as grounded_facts), since the event store is
the TIMELINE backbone.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.dates import parse_date
from src.detect.detectors import _name_to_entity, _resolve
from src.utils.logger import logger


def _eid(*parts) -> str:
    return "ev_" + hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:18]


def main() -> int:
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents, docs, events = m.db["entities"], m.db["documents"], m.db["events"]
    name_idx = _name_to_entity(ents)

    def rid(name: str) -> Optional[str]:
        e = _resolve(name or "", name_idx)
        return e["_id"] if e else None

    ops: List[Dict[str, Any]] = []
    n = 0

    def emit(event_type, date, date_kind, doc_id, prop, quote, *, entity_ids=None,
             amount=None, detail=""):
        nonlocal n
        d = parse_date(date) if isinstance(date, str) else date
        if not d:
            return
        if getattr(d, "tzinfo", None) is None:
            d = d.replace(tzinfo=timezone.utc)
        eid = _eid(event_type, d.date(), prop, (quote or "")[:40])
        events.update_one({"_id": eid}, {"$set": {
            "_id": eid, "event_type": event_type, "date": d, "date_kind": date_kind,
            "entity_ids": [x for x in (entity_ids or []) if x], "property_id": prop,
            "doc_id": doc_id, "source_quote": quote, "amount": amount,
            "detail": detail, "updated_at": now}}, upsert=True)
        n += 1

    # ---- grounded facts on title docs ----
    for d in docs.find({"grounded_facts": {"$exists": True}},
                       {"grounded_facts": 1, "property_ids": 1}):
        gf = d.get("grounded_facts") or {}
        prop = (d.get("property_ids") or [None])[0]
        did = d["_id"]
        for it in gf.get("chain_of_title", []):
            emit("conveyance", it.get("dated") or it.get("recorded"), "recording_date", did, prop,
                 it.get("source_quote"), entity_ids=[rid(it.get("grantor")), rid(it.get("grantee"))],
                 amount=it.get("amount"),
                 detail=f"{it.get('grantor','?')} -> {it.get('grantee','?')} ({it.get('instrument_type','deed')})")
        for it in gf.get("mortgages", []):
            emit("mortgage", it.get("dated") or it.get("recorded"), "recording_date", did, prop,
                 it.get("source_quote"), entity_ids=[rid(it.get("lender")), rid(it.get("borrower"))],
                 amount=it.get("amount"), detail=f"mortgage {it.get('lender','?')} -> {it.get('borrower','?')}")
        for it in gf.get("liens", []):
            emit("lien", it.get("dated"), "document_date", did, prop, it.get("source_quote"),
                 entity_ids=[rid(it.get("creditor"))], amount=it.get("amount"),
                 detail=f"{it.get('lien_type','lien')} {it.get('creditor','')}")
        for it in gf.get("judgments", []):
            emit("judgment", it.get("entered"), "filing_date", did, prop, it.get("source_quote"),
                 entity_ids=[rid(it.get("creditor")), rid(it.get("debtor"))], amount=it.get("amount"),
                 detail=f"judgment {it.get('creditor','?')} v {it.get('debtor','?')}")
        for it in gf.get("lis_pendens", []):
            emit("lis_pendens", it.get("filed"), "filing_date", did, prop, it.get("source_quote"),
                 detail=f"lis pendens {it.get('case','')}")
        for it in gf.get("assignments", []):
            emit("assignment", it.get("dated"), "recording_date", did, prop, it.get("source_quote"),
                 entity_ids=[rid(it.get("assignor")), rid(it.get("assignee"))],
                 detail=f"assignment {it.get('assignor','?')} -> {it.get('assignee','?')}")

    # ---- insurance ----
    for d in docs.find({"source_type": "insurance"},
                       {"effective_date": 1, "expiration_date": 1, "is_cancellation": 1,
                        "insurer": 1, "property_ids": 1}):
        prop = (d.get("property_ids") or [None])[0]
        et = "policy_cancelled" if d.get("is_cancellation") else "policy_effective"
        emit(et, d.get("effective_date"), "effective_date", d["_id"], prop,
             f"{d.get('insurer','insurance')} {'cancellation' if d.get('is_cancellation') else 'coverage'}",
             detail=d.get("insurer") or "")

    # ---- litigation ----
    for d in docs.find({"source_type": "litigation_update"},
                       {"document_date": 1, "property_ids": 1, "sequence_no": 1}):
        for prop in (d.get("property_ids") or [None]):
            emit("litigation_update", d.get("document_date"), "filing_date", d["_id"], prop,
                 f"litigation update #{d.get('sequence_no')}", detail="MangoTree v David")

    # ---- title search as-of ----
    for d in docs.find({"source_type": "title_report"},
                       {"completed_date": 1, "search_date": 1, "property_ids": 1, "vendor": 1,
                        "is_update": 1}):
        prop = (d.get("property_ids") or [None])[0]
        emit("title_search", d.get("completed_date") or d.get("search_date"), "document_date",
             d["_id"], prop, f"{d.get('vendor','')} {'update' if d.get('is_update') else 'full'} search",
             detail=d.get("vendor") or "")

    from pymongo import ASCENDING, DESCENDING
    for keys, nm in [([("property_id", ASCENDING), ("date", ASCENDING)], "ix_prop_date"),
                     ([("entity_ids", ASCENDING), ("date", ASCENDING)], "ix_ent_date"),
                     ([("event_type", ASCENDING)], "ix_type"),
                     ([("date", DESCENDING)], "ix_date")]:
        try:
            events.create_index(keys, name=nm)
        except Exception:  # noqa: BLE001
            pass

    logger.info(f"events written/updated: {n}  total in store: {events.estimated_document_count()}")
    import collections
    c = collections.Counter(e.get("event_type") for e in events.find({}, {"event_type": 1}))
    logger.info(f"by type: {dict(c)}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
