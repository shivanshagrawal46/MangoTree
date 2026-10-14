"""The ARV job, end to end, and the instant recompute.

``run`` is what the API starts as a job: it emits a ``phase`` event as each
stage starts and finishes, ``partial`` events carrying the pieces of the file
as they land (so the page fills in while the model is still thinking), and
returns the finished document. ``recompute`` is the deterministic half alone —
called on every edit (a comp toggled, an assumption changed, a rehab line
typed) and back in well under a second.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from mangotree.core.logging import logger

from . import analyst, config as cfg, engine, market as market_mod, providers, rehab as rehab_mod
from .geocode import GeocodeError, geocode
from .store import ArvStore

Emit = Callable[[str, Dict[str, Any]], None]

SUBJECT_FIELDS = ("address", "unit", "property_type", "beds", "baths", "sqft", "lot_sqft", "year_built", "stories", "garage_spaces", "basement", "pool",
                  "condition_current", "condition_after", "quality", "notes", "lat", "lon",
                  # as repaired, when the plan changes the house itself
                  "beds_after", "baths_after", "sqft_after",
                  # structure, systems, site, situation
                  "roof_age_years", "heating", "cooling", "foundation", "exterior", "parking", "zoning", "view", "waterfront", "occupancy", "hoa_monthly",
                  "known_issues", "planned_work", "fact_sources")
NUMERIC = ("beds", "baths", "sqft", "lot_sqft", "year_built", "stories", "garage_spaces", "lat", "lon", "beds_after", "baths_after", "sqft_after", "roof_age_years", "hoa_monthly")
INTEGER = ("beds", "sqft", "lot_sqft", "year_built", "stories", "garage_spaces", "beds_after", "sqft_after", "roof_age_years")
BOOLEAN = ("pool", "waterfront")
LISTS = ("known_issues", "planned_work")


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def clean_subject(s: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in SUBJECT_FIELDS:
        v = (s or {}).get(k)
        if k in NUMERIC:
            v = _num(v)
            if v is not None and k in INTEGER:
                v = int(round(v))
        elif k in BOOLEAN:
            v = None if v in (None, "", "unknown") else bool(v)
        elif k in LISTS:
            v = [str(x).strip() for x in (v or []) if str(x).strip()] if isinstance(v, (list, tuple)) else []
        elif k == "fact_sources":
            v = {str(kk): str(vv) for kk, vv in (v or {}).items()} if isinstance(v, dict) else {}
        elif isinstance(v, str):
            v = v.strip() or None
        out[k] = v
    out["property_type"] = out.get("property_type") if out.get("property_type") in engine.PROPERTY_TYPES else "single_family"
    out["condition_after"] = out.get("condition_after") if out.get("condition_after") in engine.CONDITION_RANK else "C2"
    out["condition_current"] = out.get("condition_current") if out.get("condition_current") in engine.CONDITION_RANK else None
    out["basement"] = out.get("basement") if out.get("basement") in ("none", "unfinished", "finished") else "unknown"
    out["occupancy"] = out.get("occupancy") if out.get("occupancy") in ("vacant", "owner", "tenant") else None
    return out


def as_repaired(subject: Dict[str, Any]) -> Dict[str, Any]:
    """The house the comps are compared with: today's facts, with the room
    count and living area the plan produces where those are given."""
    out = dict(subject)
    for k in ("beds", "baths", "sqft"):
        v = out.get(k + "_after")
        if v not in (None, 0):
            out[k] = v
    return out


def new_document(*, subject: Dict[str, Any], assumptions: Dict[str, Any], rehab: Dict[str, Any], settings: Dict[str, Any], user: Dict[str, Any],
                 property_id: Optional[str] = None, label: Optional[str] = None) -> Dict[str, Any]:
    st = {"use_rentcast": bool(cfg.RENTCAST_API_KEY), "use_web_search": True, "radius_mi": cfg.COMP_RADIUS_MILES, "months": cfg.COMP_MONTHS,
          "target": cfg.COMP_TARGET, "run_analyst": True, "web_searches": 10}
    st.update({k: v for k, v in (settings or {}).items() if v is not None})
    if not cfg.RENTCAST_API_KEY:
        st["use_rentcast"] = False
    return {"analysis_id": ArvStore.new_id(), "status": "queued", "created_by": user.get("user_id"), "created_by_name": user.get("name"),
            "property_id": property_id, "label": label, "subject_input": clean_subject(subject), "subject": clean_subject(subject),
            "assumptions": dict(assumptions or {}), "rehab": dict(rehab or {}), "settings": st, "comps": [], "documents": [], "answers": [], "chat": [],
            "phases": [], "data_sources": [], "cost_usd": 0.0}


def _phase(doc: Dict[str, Any], emit: Optional[Emit], key: str, label: str, status: str, detail: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    row = next((p for p in doc["phases"] if p["key"] == key), None)
    if row is None:
        row = {"key": key, "label": label}
        doc["phases"].append(row)
    row.update({"status": status, "detail": detail, "at": now})
    if emit:
        emit("phase", dict(row))


def merge_records(subject: Dict[str, Any], records: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the person typed wins; public records fill the blanks."""
    if not records:
        return subject
    out = dict(subject)
    filled = []
    for k in ("property_type", "beds", "baths", "sqft", "lot_sqft", "year_built", "stories", "garage_spaces", "basement", "pool"):
        if out.get(k) in (None, "unknown") and records.get(k) not in (None, "unknown"):
            out[k] = records[k]
            filled.append(k)
    out["filled_from_records"] = filled
    return out


def recompute(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic half: rehab budget, sales comparison, underwriting."""
    subj = as_repaired(doc["subject"])
    geo = doc.get("geocode") or {}
    state = geo.get("state") or None
    sqft = _num(subj.get("sqft")) or 0.0
    doc["rehab_budget"] = rehab_mod.budget(doc.get("rehab") or {}, sqft=sqft, state=state)
    avm = doc.get("avm")
    doc["valuation"] = engine.value(subj, doc.get("comps") or [], doc.get("market") or {}, avm, doc.get("settings") or {})
    arv = (doc["valuation"] or {}).get("arv")
    a = dict(doc.get("assumptions") or {})
    rec = doc.get("records") or {}
    if a.get("property_taxes_annual") in (None, "") and rec.get("tax_annual"):
        a["property_taxes_annual"] = rec["tax_annual"]
        a["property_taxes_basis"] = "county record %s" % rec.get("tax_year", "")
    if a.get("hoa_monthly") in (None, "") and rec.get("hoa_monthly"):
        a["hoa_monthly"] = rec["hoa_monthly"]
    doc["underwriting"] = engine.underwrite(arv, doc["rehab_budget"]["total"], a, state=state, sqft=sqft or None, market=doc.get("market"))
    doc["computed_at"] = datetime.now(timezone.utc).isoformat()
    return doc


def run(doc: Dict[str, Any], store: ArvStore, emit: Optional[Emit] = None, *, stages: Optional[List[str]] = None) -> Dict[str, Any]:
    """Run every stage (or only ``stages``: any of geocode, records, market,
    comps, analyst). Saves after each stage so a crash mid-way leaves the
    pieces that landed."""
    stages = stages or ["geocode", "records", "market", "comps", "analyst"]
    st = doc.get("settings") or {}
    doc["status"] = "running"
    doc["error"] = None
    sources = set(doc.get("data_sources") or [])

    def say(text: str):
        if emit:
            emit("status", {"text": text})

    def partial(key: str, value: Any):
        if emit:
            emit("partial", {"key": key, "value": value})

    try:
        # ---------------------------------------------------------- geocode
        if "geocode" in stages or not doc.get("geocode"):
            _phase(doc, emit, "geocode", "Locate the address", "running")
            try:
                geo = geocode(doc["subject"].get("address") or "")
            except GeocodeError as exc:
                _phase(doc, emit, "geocode", "Locate the address", "failed", str(exc))
                raise
            doc["geocode"] = geo
            doc["subject"]["lat"], doc["subject"]["lon"] = geo["lat"], geo["lon"]
            sources.add(geo["provider"])
            _phase(doc, emit, "geocode", "Locate the address", "done", "%s (%s, tract %s)" % (geo["formatted"], geo["provider"], geo.get("tract") or "n/a"))
            partial("geocode", geo)
            store.save(doc)

        # ---------------------------------------------------------- records
        if "records" in stages:
            if st.get("use_rentcast") and cfg.RENTCAST_API_KEY:
                _phase(doc, emit, "records", "Public records (RentCast)", "running")
                try:
                    rec = providers.rentcast_record(doc["geocode"]["formatted"])
                    doc["records"] = rec
                    doc["subject"] = merge_records(doc["subject"], rec)
                    if rec:
                        sources.add("rentcast")
                        _phase(doc, emit, "records", "Public records (RentCast)", "done",
                               "%s · filled %s" % (rec.get("formatted_address"), ", ".join(doc["subject"].get("filled_from_records") or []) or "nothing new"))
                    else:
                        _phase(doc, emit, "records", "Public records (RentCast)", "done", "no record for this address")
                except Exception as exc:
                    logger.warning("rentcast record: %s", exc)
                    _phase(doc, emit, "records", "Public records (RentCast)", "failed", str(exc)[:160])
                partial("subject", doc["subject"])
                partial("records", doc.get("records"))
            else:
                _phase(doc, emit, "records", "Public records", "skipped", "no RentCast key — using what was entered")
            store.save(doc)

        # ----------------------------------------------------------- market
        if "market" in stages:
            _phase(doc, emit, "market", "Market context (FRED, Zillow, FEMA, Census)", "running")
            got = []

            def on_market(k, v):
                if v is not None:
                    got.append(k)
                partial("market_" + k, v)
            doc["market"] = market_mod.gather(doc["geocode"], emit=on_market)
            sources.update({"mortgage": "fred", "zhvi": "zillow_zhvi", "flood": "fema_nfhl", "acs": "census_acs"}[k] for k in got)
            _phase(doc, emit, "market", "Market context (FRED, Zillow, FEMA, Census)", "done",
                   ", ".join(got) + ("; gaps: " + "; ".join(doc["market"].get("gaps") or []) if doc["market"].get("gaps") else ""))
            partial("market", doc["market"])
            store.save(doc)

        # ------------------------------------------------------------ comps
        if "comps" in stages:
            keep = [c for c in (doc.get("comps") or []) if c.get("source") == "manual"]
            found: List[Dict[str, Any]] = []
            radius, months = float(st.get("radius_mi") or cfg.COMP_RADIUS_MILES), int(st.get("months") or cfg.COMP_MONTHS)
            if st.get("use_rentcast") and cfg.RENTCAST_API_KEY:
                _phase(doc, emit, "avm", "RentCast value and comparables", "running")
                try:
                    avm = providers.rentcast_avm(doc["geocode"]["formatted"], as_repaired(doc["subject"]), radius_mi=radius, months=months, comp_count=int(st.get("target") or 6) * 3)
                    if avm:
                        found.extend(avm.pop("comps"))
                        doc["avm"] = avm
                        sources.add("rentcast")
                        _phase(doc, emit, "avm", "RentCast value and comparables", "done",
                               ("AVM $%s (%s comparables)" % (format(int(avm["value"]), ","), len(found))) if avm.get("value") else ("%s comparables; %s" % (len(found), avm.get("note") or "no value")))
                    else:
                        _phase(doc, emit, "avm", "RentCast value and comparables", "done", "no estimate returned")
                except Exception as exc:
                    logger.warning("rentcast avm: %s", exc)
                    _phase(doc, emit, "avm", "RentCast value and comparables", "failed", str(exc)[:160])
                partial("avm", doc.get("avm"))
            else:
                _phase(doc, emit, "avm", "RentCast value and comparables", "skipped", "no RentCast key")
            if st.get("use_web_search", True):
                _phase(doc, emit, "web", "AI web search for recent sales", "running")
                try:
                    web = analyst.discover_comps(as_repaired(doc["subject"]), doc["geocode"], radius_mi=radius, months=months, emit=say,
                                                 max_searches=int(st.get("web_searches") or 10))
                    doc["web_search"] = {"notes": web.get("notes"), "searches": web.get("searches"), "usage": web.get("usage"), "found": len(web.get("comps") or [])}
                    doc["cost_usd"] = round(float(doc.get("cost_usd") or 0) + float((web.get("usage") or {}).get("total_cost_usd") or 0), 4)
                    analyst._geocode_comps(web["comps"], doc["subject"], emit=say)
                    found.extend(web["comps"])
                    sources.add("web_search")
                    _phase(doc, emit, "web", "AI web search for recent sales", "done", "%d sale(s) reported after %d searches" % (len(web["comps"]), web.get("searches") or 0))
                except Exception as exc:
                    logger.warning("web comps: %s", exc)
                    doc["web_search"] = {"notes": "failed: %s" % str(exc)[:200], "searches": 0, "found": 0}
                    _phase(doc, emit, "web", "AI web search for recent sales", "failed", str(exc)[:160])
            else:
                _phase(doc, emit, "web", "AI web search for recent sales", "skipped", "switched off")
            doc["comps"] = providers.dedupe(keep + found, doc["subject"])
            partial("comps", doc["comps"])
            store.save(doc)

        # ---------------------------------------------------------- compute
        _phase(doc, emit, "compute", "Score, adjust, reconcile, underwrite", "running")
        recompute(doc)
        v = doc["valuation"]
        _phase(doc, emit, "compute", "Score, adjust, reconcile, underwrite", "done",
               ("ARV $%s (%s - %s), %s" % (format(int(v["arv"]), ","), format(int(v["arv_low"]), ","), format(int(v["arv_high"]), ","), v["confidence"]["label"])) if v.get("arv") else v.get("reconciliation", {}).get("error", "no value"))
        partial("valuation", v)
        partial("rehab_budget", doc["rehab_budget"])
        partial("underwriting", doc["underwriting"])
        store.save(doc)

        # ---------------------------------------------------------- analyst
        if "analyst" in stages and st.get("run_analyst", True):
            _phase(doc, emit, "analyst", "AI review", "running")
            try:
                an = analyst.analyze(doc, emit=say)
                an["at"] = datetime.now(timezone.utc).isoformat()
                doc["analysis"] = an
                doc["cost_usd"] = round(float(doc.get("cost_usd") or 0) + float((an.get("usage") or {}).get("total_cost_usd") or 0), 4)
                _phase(doc, emit, "analyst", "AI review", "done", an.get("headline", "")[:200])
                partial("analysis", an)
            except Exception as exc:
                logger.exception("arv analyst failed")
                doc["analysis_error"] = str(exc)[:300]
                _phase(doc, emit, "analyst", "AI review", "failed", str(exc)[:200])
        doc["status"] = "done"
    except Exception as exc:
        doc["status"] = "failed"
        doc["error"] = ("%s: %s" % (type(exc).__name__, exc))[:400]
        raise
    finally:
        doc["data_sources"] = sorted(sources)
        store.save(doc)
    return doc
