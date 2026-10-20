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

from . import analyst, config as cfg, engine, feeds, market as market_mod, providers, rehab as rehab_mod
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
          "target": cfg.COMP_TARGET, "run_analyst": True, "web_searches": 10,
          # 2026-10-20: recorded sales + subject file (RealEstateAPI), analyst shortlist, HouseCanary condition on the shortlist
          "use_reapi": bool(cfg.REALESTATEAPI_KEY), "use_shortlist": True, "shortlist_size": cfg.SHORTLIST_SIZE,
          "use_housecanary": bool(cfg.HOUSECANARY_API_KEY and cfg.HOUSECANARY_API_SECRET)}
    st.update({k: v for k, v in (settings or {}).items() if v is not None})
    if not cfg.RENTCAST_API_KEY:
        st["use_rentcast"] = False
    if not cfg.REALESTATEAPI_KEY:
        st["use_reapi"] = False
    if not (cfg.HOUSECANARY_API_KEY and cfg.HOUSECANARY_API_SECRET):
        st["use_housecanary"] = False
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


def _shortlist_and_verify(doc: Dict[str, Any], store: ArvStore, emit: Optional[Emit], say, partial, sources: set, *, radius: float, months: int) -> None:
    """Rules pick the eligible candidates and rank them; Opus 5.5 chooses the
    best few from the top of that list using deed and MLS evidence; HouseCanary
    then verifies only those few plus the subject (condition grade, living
    area, recorded sale). Every step is optional and failure-tolerant."""
    st = doc.get("settings") or {}
    subj = as_repaired(doc["subject"])
    size = int(st.get("shortlist_size") or cfg.SHORTLIST_SIZE)
    comps = doc.get("comps") or []
    for c in comps:  # a fresh run forgets the last run's picks
        c.pop("ai_pick", None)
        c.pop("ai_rank", None)
        c.pop("ai_reason", None)
    pool: List[Dict[str, Any]] = []
    label = "Analyst shortlist (Opus 5.5)"
    if st.get("use_shortlist", True) and comps:
        _phase(doc, emit, "shortlist", label, "running")
        try:
            sel = engine.select(subj, comps, radius=radius, months=months, target=max(size, 3))
            ranked = sorted([c for c in comps if not c.get("fails") and c.get("included") is not False], key=lambda c: -(c.get("similarity") or 0))
            pool = ranked[:max(cfg.SHORTLIST_POOL, size)]
            # Fit alone favours the nearest cheap deeds; the renovated resales the ARV needs are often a street
            # further or a size band away. Add the best-priced closed sales so the model sees both tiers.
            closed = sorted([c for c in ranked if c.get("price_basis") == "closed" and c not in pool], key=lambda c: -(c.get("sale_price") or 0))
            top_price = max([c.get("sale_price") or 0 for c in ranked if c.get("price_basis") == "closed"] or [0])
            pool += [c for c in closed if (c.get("sale_price") or 0) >= 0.5 * top_price][:6]
            # Deed and MLS history for the pool, so the model can tell a renovated resale from an auction.
            if st.get("use_reapi") and cfg.REALESTATEAPI_KEY:
                for c in pool:
                    if c.get("evidence") is None:
                        try:
                            feeds.reapi_evidence(c)
                        except Exception as exc:
                            logger.info("reapi evidence %r: %s", c.get("address"), exc)
                pool = [c for c in pool if c.get("arms_length") is not False] or pool
            if len(pool) > size:
                out = analyst.shortlist(subj, pool, size=size, emit=say)
                doc["shortlist"] = out
                doc["cost_usd"] = round(float(doc.get("cost_usd") or 0) + float((out.get("usage") or {}).get("total_cost_usd") or 0), 4)
                _phase(doc, emit, "shortlist", label, "done", "%d chosen from %d candidates (%d qualified of %d)" % (len(out["picks"]), len(pool), sel["qualified"], sel["candidates"]))
            else:
                for i, c in enumerate(pool):
                    c["ai_pick"], c["ai_rank"], c["ai_reason"] = True, i + 1, "one of the only %d qualifying sales" % len(pool)
                doc["shortlist"] = {"picks": [{"comp_id": c["comp_id"], "rank": c["ai_rank"], "reason": c["ai_reason"], "address": c.get("address")} for c in pool], "rejected": [],
                                    "notes": "Only %d sale(s) qualified, so all of them are used; there was no choice to make." % len(pool), "size": size, "candidates": len(pool)}
                _phase(doc, emit, "shortlist", label, "done", "only %d qualifying sale(s); all kept" % len(pool))
        except Exception as exc:
            logger.warning("shortlist: %s", exc)
            _phase(doc, emit, "shortlist", label, "failed", str(exc)[:160])
        partial("shortlist", doc.get("shortlist"))
    else:
        _phase(doc, emit, "shortlist", label, "skipped", "switched off")
    picks = [c for c in comps if c.get("ai_pick")] or pool[:size]
    hlabel = "HouseCanary: condition grade and verified facts"
    if st.get("use_housecanary") and cfg.HOUSECANARY_API_KEY and cfg.HOUSECANARY_API_SECRET and picks:
        _phase(doc, emit, "housecanary", hlabel, "running")
        try:
            subject_addr = (doc.get("geocode") or {}).get("formatted") or doc["subject"].get("address")
            addrs = [subject_addr] + [c.get("address") for c in picks if c.get("address")]
            res = feeds.hc_enrich(addrs)
            by = res["by_address"]
            changed = {}
            for c in picks:
                hc = by.get(c.get("address"))
                if hc:
                    ch = feeds.apply_hc_to_comp(c, hc)
                    if ch:
                        changed[c.get("address")] = ch
            sub_hc = by.get(subject_addr)
            if sub_hc and sub_hc.get("match"):
                facts = {k: sub_hc.get(k) for k in ("property_type", "beds", "baths", "sqft", "lot_sqft", "year_built", "stories", "garage_spaces", "basement", "pool")}
                before_fill = set(doc["subject"].get("filled_from_records") or [])
                doc["subject"] = merge_records(doc["subject"], facts)
                doc["subject"]["filled_from_records"] = sorted(before_fill | set(doc["subject"].get("filled_from_records") or []))
                if doc["subject"].get("condition_current") is None and sub_hc.get("condition"):
                    doc["subject"]["condition_current"] = sub_hc["condition"]
                    fs = dict(doc["subject"].get("fact_sources") or {})
                    fs["condition_current"] = "HouseCanary condition grade"
                    doc["subject"]["fact_sources"] = fs
            doc["housecanary"] = {"subject": sub_hc, "comps": {c["comp_id"]: c.get("hc") for c in picks if c.get("hc")}, "changed": changed, "errors": res.get("errors"),
                                  "cost_usd": res.get("cost_usd"), "billed_properties": res.get("billed_properties"), "at": datetime.now(timezone.utc).isoformat()}
            doc["data_cost_usd"] = round(float(doc.get("data_cost_usd") or 0) + float(res.get("cost_usd") or 0), 2)
            sources.add("housecanary")
            graded = sum(1 for c in picks if c.get("condition_source") == "housecanary" or c.get("condition_hc"))
            detail = "%d of %d comps graded%s; subject %s; $%.2f" % (graded, len(picks), (", subject %s" % sub_hc["condition"]) if sub_hc and sub_hc.get("condition") else "",
                                                                   "matched" if sub_hc and sub_hc.get("match") else "not matched", float(res.get("cost_usd") or 0))
            if res.get("errors"):
                detail += "; " + "; ".join(res["errors"][:2])
            _phase(doc, emit, "housecanary", hlabel, "done", detail)
        except Exception as exc:
            logger.warning("housecanary: %s", exc)
            _phase(doc, emit, "housecanary", hlabel, "failed", str(exc)[:160])
        partial("subject", doc["subject"])
        partial("housecanary", doc.get("housecanary"))
    elif picks:
        _phase(doc, emit, "housecanary", hlabel, "skipped", "no HouseCanary credentials" if not cfg.HOUSECANARY_API_KEY else "switched off")
    partial("comps", comps)
    store.save(doc)


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
            if st.get("use_reapi") and cfg.REALESTATEAPI_KEY:
                label = "Property file (RealEstateAPI): deeds, mortgages, MLS history"
                _phase(doc, emit, "profile", label, "running")
                try:
                    prof = feeds.reapi_profile(doc["geocode"]["formatted"])
                    doc["profile"] = prof
                    if prof:
                        doc["subject"] = merge_records(doc["subject"], prof.get("facts") or {})
                        if not doc.get("records"):
                            ls = prof.get("last_sale") or {}
                            tax = prof.get("tax") or {}
                            doc["records"] = {"source": "realestateapi", "formatted_address": prof.get("address"), "last_sale_price": ls.get("price"), "last_sale_date": ls.get("date"),
                                              "tax_annual": tax.get("annual"), "tax_year": tax.get("year"), "assessed_value": tax.get("assessed_value"),
                                              "zoning": (prof.get("facts") or {}).get("zoning")}
                        sources.add("realestateapi")
                        m = prof.get("mortgages") or []
                        first = (" ($%s %s)" % (format(int(m[0]["amount"]), ","), m[0].get("lender") or "")) if m and m[0].get("amount") else ""
                        _phase(doc, emit, "profile", label, "done", "%d deed event(s), %d open mortgage(s)%s, MLS %s; filled %s" % (
                            len(prof.get("sale_history") or []), len(m), first, (prof.get("mls") or {}).get("status") or "none",
                            ", ".join(doc["subject"].get("filled_from_records") or []) or "nothing new"))
                    else:
                        _phase(doc, emit, "profile", label, "done", "no record for this address")
                except Exception as exc:
                    logger.warning("reapi profile: %s", exc)
                    _phase(doc, emit, "profile", label, "failed", str(exc)[:160])
                partial("subject", doc["subject"])
                partial("records", doc.get("records"))
                partial("profile", doc.get("profile"))
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
            if st.get("use_reapi") and cfg.REALESTATEAPI_KEY:
                _phase(doc, emit, "reapi", "Recorded sales nearby (RealEstateAPI)", "running")
                try:
                    rs = feeds.reapi_comps(doc["geocode"]["formatted"], doc["subject"], radius_mi=radius, months=months, max_results=30)
                    found.extend(rs["comps"])
                    doc["reapi_comps"] = {"found": len(rs["comps"]), "set_aside": rs["set_aside"], "avm": rs.get("avm"), "warning": rs.get("warning"), "returned": rs.get("returned")}
                    if rs.get("avm") and not doc.get("avm"):
                        doc["avm"] = dict(rs["avm"], as_of=datetime.now(timezone.utc).date().isoformat(), note="RealEstateAPI model value for the property as it stands (not as repaired).")
                    sources.add("realestateapi")
                    _phase(doc, emit, "reapi", "Recorded sales nearby (RealEstateAPI)", "done",
                           "%d recorded sale(s) within %.1f mi / %d months; %d transfer(s) set aside (no market price)" % (len(rs["comps"]), radius, months, len(rs["set_aside"])))
                except Exception as exc:
                    logger.warning("reapi comps: %s", exc)
                    _phase(doc, emit, "reapi", "Recorded sales nearby (RealEstateAPI)", "failed", str(exc)[:160])
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
            _shortlist_and_verify(doc, store, emit, say, partial, sources, radius=radius, months=months)
        elif "shortlist" in stages:
            # Re-pick from the comps already on file (no new search): the analyst shortlist and HouseCanary again.
            radius, months = float(st.get("radius_mi") or cfg.COMP_RADIUS_MILES), int(st.get("months") or cfg.COMP_MONTHS)
            _shortlist_and_verify(doc, store, emit, say, partial, sources, radius=radius, months=months)

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
