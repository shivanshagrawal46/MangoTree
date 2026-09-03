"""Sprint 5 · timeline (5.2) + evidence packet (5.4).

timeline_for(): a correct, cited chronology from the `events/` store for a
property or entity (optionally date-bounded / flow-of-funds). The LLM never
orders events — it only narrates this.

evidence_packet(): a court-ready bundle for a property — every linked document
with custody (source file + SHA + pages), the grounded facts with verbatim
quotes, the event chronology, and any findings — the full provenance chain.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _date(d) -> Optional[str]:
    try:
        return d.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def timeline_for(m, *, property_id: Optional[str] = None, entity_id: Optional[str] = None,
                 event_types: Optional[List[str]] = None,
                 date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                 limit: int = 300) -> List[Dict[str, Any]]:
    events = m.db["events"]
    ents = m.db["entities"]
    q: Dict[str, Any] = {}
    if property_id:
        q["property_id"] = property_id
    if entity_id:
        q["entity_ids"] = entity_id
    if event_types:
        q["event_type"] = {"$in": event_types}
    if date_from or date_to:
        dd: Dict[str, Any] = {}
        if date_from:
            dd["$gte"] = date_from
        if date_to:
            dd["$lte"] = date_to
        q["date"] = dd
    rows = list(events.find(q).sort("date", 1).limit(limit))
    # resolve entity names for display
    ids = {x for r in rows for x in (r.get("entity_ids") or [])}
    names = {e["_id"]: e.get("canonical_name") or e.get("canonical_address") or e["_id"]
             for e in ents.find({"_id": {"$in": list(ids)}},
                                {"canonical_name": 1, "canonical_address": 1})}
    out = []
    for r in rows:
        out.append({
            "date": _date(r.get("date")), "date_kind": r.get("date_kind"),
            "event_type": r.get("event_type"), "detail": r.get("detail"),
            "amount": r.get("amount"),
            "entities": [names.get(x, x) for x in (r.get("entity_ids") or [])],
            "property_id": r.get("property_id"), "doc_id": r.get("doc_id"),
            "source_quote": r.get("source_quote"),
        })
    return out


_MONEY_EVENTS = ("conveyance", "mortgage", "lien", "judgment", "assignment")


def _money(s) -> Optional[float]:
    import re
    if s is None:
        return None
    mo = re.search(r"[\d,]+(?:\.\d{2})?", str(s).replace("$", ""))
    if not mo:
        return None
    try:
        return float(mo.group(0).replace(",", ""))
    except Exception:  # noqa: BLE001
        return None


def flow_of_funds(m, *, entity_id: Optional[str] = None, property_id: Optional[str] = None,
                  limit: int = 200) -> Dict[str, Any]:
    """Money-movement view: dated monetary events (conveyances, mortgages, liens,
    judgments) for an entity or property, with amounts parsed, chronological."""
    events = m.db["events"]
    q: Dict[str, Any] = {"event_type": {"$in": list(_MONEY_EVENTS)}}
    if entity_id:
        q["entity_ids"] = entity_id
    if property_id:
        q["property_id"] = property_id
    rows = list(events.find(q).sort("date", 1).limit(limit))
    flows, total = [], 0.0
    for r in rows:
        amt = _money(r.get("amount"))
        if amt:
            total += amt
        flows.append({"date": _date(r.get("date")), "type": r.get("event_type"),
                      "amount": amt, "detail": r.get("detail"),
                      "doc_id": r.get("doc_id"), "source_quote": r.get("source_quote")})
    return {"entity_id": entity_id, "property_id": property_id,
            "n_events": len(flows), "total_amount_seen": round(total, 2), "flows": flows}


def _year(v) -> Optional[int]:
    """Best-effort 4-digit year from a datetime or a free-text date string."""
    import re
    if v is None:
        return None
    try:
        return int(v.year)
    except Exception:  # noqa: BLE001
        pass
    mo = re.search(r"(19|20)\d{2}", str(v))
    return int(mo.group(0)) if mo else None


def property_graph(m, *, property_id: str) -> Dict[str, Any]:
    """Visualization-ready, fully-cited payload for the interactive property map:
    mortgages/encumbrances/conveyances bucketed by year, the title-report version
    chain, every linked document at a glance, the money-records graph (cheques/
    wires/settlements reconciled across docs), ownership intervals and the
    normalized event timeline. Everything carries doc_id + source_quote."""
    ents, docs = m.db["entities"], m.db["documents"]
    doss = m.db["property_dossier"].find_one({"_id": property_id}) or {}
    prop = ents.find_one({"_id": property_id}) or {}
    gf = doss.get("grounded_facts") or {}
    addr = doss.get("canonical_address") or prop.get("canonical_address") or property_id

    def _amt(x):
        return _money(x.get("amount"))

    # --- mortgages / encumbrances / conveyances (grounded facts, year-bucketed) ---
    mortgages = []
    for mtg in (gf.get("mortgages") or []):
        yr = _year(mtg.get("dated")) or _year(mtg.get("recorded"))
        mortgages.append({"year": yr, "dated": mtg.get("dated"), "recorded": mtg.get("recorded"),
                          "lender": mtg.get("lender"), "borrower": mtg.get("borrower"),
                          "amount": mtg.get("amount"), "amount_value": _amt(mtg),
                          "satisfied": bool(mtg.get("satisfied")),
                          "instrument_no": mtg.get("instrument_no"),
                          "doc_id": mtg.get("_doc_id"), "source_quote": mtg.get("source_quote")})
    mortgages.sort(key=lambda r: (r["year"] or 0))

    conveyances = []
    for c in (gf.get("chain_of_title") or []):
        yr = _year(c.get("dated")) or _year(c.get("recorded"))
        conveyances.append({"year": yr, "dated": c.get("dated"), "recorded": c.get("recorded"),
                            "grantor": c.get("grantor"), "grantee": c.get("grantee"),
                            "instrument_type": c.get("instrument_type"), "amount": c.get("amount"),
                            "amount_value": _amt(c), "doc_id": c.get("_doc_id"),
                            "source_quote": c.get("source_quote")})
    conveyances.sort(key=lambda r: (r["year"] or 0))

    def _enc(items, kind, dk, party_keys):
        out = []
        for x in (items or []):
            yr = _year(x.get(dk))
            out.append({"kind": kind, "year": yr, "date": x.get(dk),
                        "amount": x.get("amount"), "amount_value": _amt(x),
                        "parties": " · ".join(str(x.get(k)) for k in party_keys if x.get(k)),
                        "doc_id": x.get("_doc_id"), "source_quote": x.get("source_quote")})
        return out
    encumbrances = (_enc(gf.get("liens"), "lien", "dated", ["lien_type", "creditor"]) +
                    _enc(gf.get("judgments"), "judgment", "entered", ["creditor", "debtor"]) +
                    _enc(gf.get("lis_pendens"), "lis_pendens", "filed", ["case", "plaintiff"]) +
                    _enc(gf.get("assignments"), "assignment", "dated", ["assignor", "assignee"]))
    encumbrances.sort(key=lambda r: (r["year"] or 0))

    # --- title-report version chain (original -> updates) ---
    title_ids = (doss.get("title") or {}).get("doc_ids") or prop.get("title_doc_ids") or []
    title_versions = []
    for d in docs.find({"_id": {"$in": title_ids}}):
        title_versions.append({"doc_id": d["_id"], "vendor": d.get("vendor"),
                               "type": "update search" if d.get("is_update") else "full search",
                               "date": _date(d.get("completed_date") or d.get("search_date")
                                             or d.get("effective_date")),
                               "year": _year(d.get("completed_date") or d.get("search_date")
                                             or d.get("effective_date")),
                               "effective_date": _date(d.get("effective_date")),
                               "order_number": d.get("order_number"),
                               "pages": d.get("page_count"), "is_latest": bool(d.get("is_latest")),
                               "version_index": d.get("version_index"),
                               "version_count": d.get("version_count"),
                               "supersedes": d.get("supersedes")})
    title_versions.sort(key=lambda r: (r["date"] or ""))

    # --- every linked document at a glance ---
    ins_ids = (doss.get("insurance") or {}).get("doc_ids") or []
    eq_ids = (doss.get("equity") or {}).get("doc_ids") or []
    lit_ids = (doss.get("litigation") or {}).get("doc_ids") or []
    documents = []
    for d in docs.find({"_id": {"$in": title_ids + ins_ids + eq_ids + lit_ids}},
                       {"source_type": 1, "vendor": 1, "page_count": 1, "custody": 1,
                        "completed_date": 1, "search_date": 1, "effective_date": 1,
                        "is_update": 1, "insurer": 1, "order_number": 1}):
        dt = d.get("completed_date") or d.get("search_date") or d.get("effective_date")
        st = d.get("source_type")
        label = (f"{d.get('vendor') or ''} {'update' if d.get('is_update') else 'full'} search".strip()
                 if st == "title_report" else (d.get("insurer") or st or "document"))
        documents.append({"doc_id": d["_id"], "source_type": st, "label": label,
                          "date": _date(dt), "year": _year(dt), "pages": d.get("page_count"),
                          "sha256": (d.get("custody") or {}).get("sha256")})
    documents.sort(key=lambda r: (r["date"] or ""))

    # --- money-records graph (cheques / wires / settlement lines) ---
    money_records = []
    mtotal = 0.0
    for r in m.db["money_records"].find({"property_ids": property_id}):
        av = r.get("amount_value")
        if isinstance(av, (int, float)):
            mtotal += av
        money_records.append({"date": r.get("date"), "year": _year(r.get("date")),
                              "payer": r.get("payer"), "payee": r.get("payee"),
                              "amount": r.get("amount"), "amount_value": av,
                              "instrument": r.get("instrument"),
                              "instrument_no": r.get("instrument_no"),
                              "memo": r.get("memo"), "doc_id": r.get("document_id"),
                              "doc_category": r.get("doc_category"),
                              "reconciliation_id": r.get("reconciliation_id"),
                              "source_quote": r.get("source_quote")})
    money_records.sort(key=lambda r: (r["year"] or 0, r["date"] or ""))

    # --- ownership intervals + normalized timeline ---
    from src.graph.bitemporal import ownership_intervals
    _ivs = ownership_intervals(m.db["relationships"], property_id)
    nm = {e["_id"]: e.get("canonical_name") or e["_id"]
          for e in ents.find({"_id": {"$in": [iv["owner"] for iv in _ivs]}}, {"canonical_name": 1})}
    ownership = [{"owner": nm.get(iv["owner"], iv["owner"]), "as_of": _date(iv["as_of"]),
                  "until": _date(iv["until"]), "amount": iv.get("amount")} for iv in _ivs]
    events = timeline_for(m, property_id=property_id, limit=400)

    years = sorted({y for y in (
        [x["year"] for x in mortgages] + [x["year"] for x in conveyances] +
        [x["year"] for x in encumbrances] + [x["year"] for x in documents] +
        [x["year"] for x in money_records]) if y})
    open_mtg = sum(x["amount_value"] or 0 for x in mortgages if not x["satisfied"])
    total_mtg = sum(x["amount_value"] or 0 for x in mortgages)
    return {
        "property_id": property_id, "address": addr,
        "parcel_id": doss.get("parcel_id") or prop.get("parcel_id"),
        "is_david": doss.get("is_david"), "side": doss.get("side"),
        "summary": {
            "n_title_reports": len(title_versions), "n_mortgages": len(mortgages),
            "total_mortgage_amount": round(total_mtg, 2),
            "open_mortgage_amount": round(open_mtg, 2),
            "n_conveyances": len(conveyances), "n_encumbrances": len(encumbrances),
            "n_documents": len(documents), "n_money_records": len(money_records),
            "money_total": round(mtotal, 2),
            "year_min": (years[0] if years else None), "year_max": (years[-1] if years else None),
        },
        "years": years, "mortgages": mortgages, "conveyances": conveyances,
        "encumbrances": encumbrances, "title_versions": title_versions,
        "documents": documents, "money_records": money_records,
        "ownership": ownership, "events": events,
        "generated_at": datetime.utcnow().isoformat(),
    }


def evidence_packet(m, *, property_id: str) -> Dict[str, Any]:
    ents, docs = m.db["entities"], m.db["documents"]
    events, findings = m.db["events"], m.db["findings"]
    prop = ents.find_one({"_id": property_id})
    if not prop:
        return {"error": f"property {property_id} not found"}
    doss = m.db["property_dossier"].find_one({"_id": property_id}) or {}
    doc_ids = (prop.get("title_doc_ids") or []) + (prop.get("insurance_doc_ids") or []) + \
              (prop.get("equity_doc_ids") or []) + (prop.get("litigation_doc_ids") or [])
    doc_rows = []
    for d in docs.find({"_id": {"$in": doc_ids}},
                       {"source_type": 1, "custody": 1, "page_count": 1, "vendor": 1,
                        "property_address": 1, "grounded_facts": 1}):
        cust = d.get("custody") or {}
        doc_rows.append({
            "doc_id": d["_id"], "source_type": d.get("source_type"),
            "source_file": cust.get("source_file") or (cust.get("source_files") or [None])[0],
            "sha256": cust.get("sha256"), "pages": d.get("page_count"),
            "vendor": d.get("vendor"),
            "grounded_fact_counts": {k: len(v) for k, v in (d.get("grounded_facts") or {}).items()},
        })
    # bitemporal ownership chain (Sprint 3.2.3): each owner's as_of → until
    from src.graph.bitemporal import ownership_intervals
    _ivs = ownership_intervals(m.db["relationships"], property_id)
    ent_names = {e["_id"]: e.get("canonical_name") or e["_id"]
                 for e in ents.find({"_id": {"$in": [iv["owner"] for iv in _ivs]}},
                                    {"canonical_name": 1})}
    intervals = [{"owner": iv["owner"],
                  "owner_name": ent_names.get(iv["owner"], iv["owner"]),
                  "amount": iv.get("amount"), "source_quote": iv.get("source_quote"),
                  "as_of": _date(iv["as_of"]), "until": _date(iv["until"])}
                 for iv in _ivs]
    return {
        "property_id": property_id,
        "address": prop.get("canonical_address"), "parcel_id": prop.get("parcel_id"),
        "is_david": prop.get("is_david"), "side": prop.get("side"),
        "owners": doss.get("owners", []),
        "ownership_intervals": intervals,
        "equity": doss.get("equity", {}),
        "documents": doc_rows,
        "timeline": timeline_for(m, property_id=property_id),
        "findings": [{"type": f.get("finding_type"), "severity": f.get("severity"),
                      "title": f.get("title"), "status": f.get("status"),
                      "evidence": f.get("evidence")}
                     for f in findings.find({"property_id": property_id})],
        "generated_at": datetime.utcnow().isoformat(),
    }
