"""Two paid feeds added 2026-10-20 so the comp grid rests on closed prices
and a condition grade instead of listing prices.

  RealEstateAPI  (realestateapi.com, key REALESTATEAPI_KEY)
      reapi_profile   the subject's full public-record file: facts, deed
                      history with arm's-length flags, every mortgage with
                      the lender, MLS history, distress and vacancy flags
      reapi_comps     recorded sales near the subject: deed price and date,
                      MLS sold price where one exists, facts, distance
      reapi_evidence  the same deed / MLS history for a shortlisted comp, so
                      the model can tell a renovated resale from an auction

  HouseCanary    (api.housecanary.com, HOUSECANARY_API_KEY + _SECRET)
      hc_enrich       one batch for the chosen comps and the subject:
                      details_advanced (condition grade C1-C6, verified
                      living area, rooms, year built) and sales_history
                      (recorded arm's-length sales). Billed per property per
                      endpoint — $0.50 each on the Basic plan — so it is only
                      called for the five or six addresses that matter.

Nothing here raises past the pipeline: a failed feed is logged, reported in
the phase line, and the run goes on with what it has.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from mangotree.core.logging import logger

from .config import HOUSECANARY_API_KEY, HOUSECANARY_API_SECRET, HTTP_TIMEOUT, REALESTATEAPI_KEY, USER_AGENT
from .geocode import haversine_miles
from .providers import new_comp

REAPI_BASE = "https://api.realestateapi.com"
HC_BASE = "https://api.housecanary.com"
#: HouseCanary list prices on the Basic plan (per successful property per endpoint).
HC_PRICE = {"details_advanced": 0.50, "sales_history": 0.50}

_REAPI_TYPES = {"SFR": "single_family", "CONDO": "condo", "TOWNHOUSE": "townhouse", "MOBILE": "manufactured", "LAND": "land"}
_HC_TYPES = {"SFD": "single_family", "SINGLE_FAMILY": "single_family", "TOWNHOUSE": "townhouse", "CONDO": "condo", "CONDOMINIUM": "condo",
             "MULTI_FAMILY": "multi_2_4", "MANUFACTURED": "manufactured", "MOBILE_HOME": "manufactured"}


def _f(v) -> Optional[float]:
    try:
        if v in (None, "", "null"):
            return None
        x = float(v)
        return x
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    x = _f(v)
    return int(round(x)) if x is not None else None


def _d(v) -> Optional[str]:
    return str(v)[:10] if v else None


def _pos(v) -> Optional[float]:
    """A size or count that is zero in the record means unknown."""
    x = _f(v)
    return x if x and x > 0 else None


# ================================================================ RealEstateAPI
def _reapi(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not REALESTATEAPI_KEY:
        raise RuntimeError("REALESTATEAPI_KEY is not set")
    r = requests.post(REAPI_BASE + path, json=body, headers={"x-api-key": REALESTATEAPI_KEY, "Content-Type": "application/json", "User-Agent": USER_AGENT},
                      timeout=max(HTTP_TIMEOUT, 45))
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code == 429:
        raise RuntimeError("RealEstateAPI rate limit reached")
    if r.status_code in (401, 403):
        raise RuntimeError("RealEstateAPI refused the key (%s): %s" % (r.status_code, data.get("message") or ""))
    if r.status_code >= 400:
        raise RuntimeError("RealEstateAPI %s: %s" % (r.status_code, data.get("message") or data.get("error") or "request failed"))
    return data


def _mortgage(m: Dict[str, Any]) -> Dict[str, Any]:
    return {"lender": m.get("lenderName"), "amount": _f(m.get("amount")), "loan_type": m.get("loanType"), "position": m.get("position"),
            "rate_pct": _f(m.get("interestRate")), "rate_type": m.get("interestRateType"), "term": " ".join(str(x) for x in [m.get("term"), m.get("termType")] if x) or None,
            "recorded": _d(m.get("recordingDate")), "document_date": _d(m.get("documentDate")), "maturity": _d(m.get("maturityDate")), "open": m.get("open"),
            "assumable": m.get("assumable")}


def _sale(s: Dict[str, Any]) -> Dict[str, Any]:
    return {"date": _d(s.get("saleDate")) or _d(s.get("recordingDate")), "recorded": _d(s.get("recordingDate")), "price": _f(s.get("saleAmount")),
            "arms_length": s.get("armsLength"), "transaction_type": s.get("transactionType"), "document_type": s.get("documentType"),
            "buyer": s.get("buyerNames"), "seller": s.get("sellerNames"), "purchase_method": s.get("purchaseMethod"), "ltv": _f(s.get("ltv")),
            "prior_owner_months": _i(s.get("priorOwnerMonthsOwned"))}


def _mls_event(m: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": m.get("status"), "date": _d(m.get("statusDate")) or _d(m.get("lastStatusDate")), "price": _f(m.get("price")), "dom": _i(m.get("daysOnMarket")),
            "beds": _i(m.get("beds")), "baths": _f(m.get("baths")), "agent": m.get("agentName"), "office": m.get("agentOffice"), "type": m.get("type")}


def _basement(pi: Dict[str, Any]) -> str:
    t = str(pi.get("basementType") or "").upper()
    if not t:
        return "unknown"
    if "NO BASEMENT" in t or t in ("NONE",):
        return "none"
    fin = _f(pi.get("basementSquareFeetFinished")) or 0
    pct = _f(pi.get("basementFinishedPercent")) or 0
    return "finished" if fin > 0 or pct >= 50 or "FINISH" in t and "UNFINISH" not in t else "unfinished"


def reapi_profile(address: str) -> Optional[Dict[str, Any]]:
    """Everything the public record says about the subject, shaped for the
    file. ``facts`` fills blanks in the subject (typed facts win); the rest is
    shown on the overview and given to the model."""
    data = _reapi("/v2/PropertyDetail", {"address": address, "comps": False})
    x = data.get("data") or {}
    if not x:
        return None
    pi = x.get("propertyInfo") or {}
    lot = x.get("lotInfo") or {}
    own = x.get("ownerInfo") or {}
    tax = x.get("taxInfo") or {}
    sales = [_sale(s) for s in (x.get("saleHistory") or [])]
    sales.sort(key=lambda s: s.get("date") or "", reverse=True)
    arms = [s for s in sales if s.get("arms_length") and (s.get("price") or 0) > 1000]
    mortgages = [_mortgage(m) for m in (x.get("currentMortgages") or [])]
    history = [_mortgage(m) for m in (x.get("mortgageHistory") or [])]
    mls = [_mls_event(m) for m in (x.get("mlsHistory") or [])]
    mls.sort(key=lambda m: m.get("date") or "", reverse=True)
    units = _i(pi.get("unitsCount"))
    ptype = _REAPI_TYPES.get(str(x.get("propertyType") or "").upper())
    if ptype is None and str(x.get("propertyType") or "").upper() == "MFR" and units and units >= 2:
        ptype = "multi_2_4"
    lot_sqft = _pos(pi.get("lotSquareFeet") or lot.get("lotSquareFeet"))
    if lot_sqft is not None and lot_sqft < 200:   # a parsed "11-6X56" style legal description, not an area
        lot_sqft = None
    facts = {
        "property_type": ptype, "beds": _i(pi.get("bedrooms")), "baths": _f(pi.get("bathrooms")), "sqft": _pos(pi.get("livingSquareFeet")) or _pos(pi.get("buildingSquareFeet")),
        "lot_sqft": lot_sqft, "year_built": _i(pi.get("yearBuilt")), "stories": _pos(pi.get("stories")), "garage_spaces": _pos(pi.get("parkingSpaces")) if pi.get("garageType") else None,
        "basement": _basement(pi), "pool": bool(pi.get("pool")) if pi.get("pool") is not None else None,
        "heating": pi.get("heatingType"), "cooling": pi.get("airConditioningType"), "roof": pi.get("roofMaterial") or pi.get("roofConstruction"),
        "construction": pi.get("construction"), "zoning": lot.get("zoning"), "property_use": pi.get("propertyUse") or lot.get("propertyUse"),
        "county_condition": pi.get("buildingCondition"),
    }
    flags = {k: x.get(k) for k in ("vacant", "ownerOccupied", "absenteeOwner", "preForeclosure", "foreclosureInfo", "auction", "bankOwned", "taxLien", "lien", "judgment",
                                   "deathTransfer", "inherited", "quitClaim", "freeClear", "highEquity", "cashBuyer", "investorBuyer", "corporateOwned") if x.get(k)}
    return {
        "source": "realestateapi", "fetched_at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "reapi_id": x.get("id"),
        "address": ((pi.get("address") or {}).get("label")), "apn": lot.get("apn"), "legal_description": lot.get("legalDescription"),
        "facts": facts, "lat": _f(pi.get("latitude")), "lon": _f(pi.get("longitude")),
        "owner": {"name": own.get("owner1FullName") or own.get("companyName"), "second": own.get("owner2FullName"), "mail_address": (own.get("mailAddress") or {}).get("label"),
                  "absentee": own.get("absenteeOwner"), "corporate": own.get("corporateOwned")},
        "last_sale": arms[0] if arms else (sales[0] if sales else None), "sale_history": sales[:8],
        "mortgages": mortgages, "mortgage_history": history[:8], "open_mortgage_balance": _f(x.get("openMortgageBalance")),
        "estimated_value": _f(x.get("estimatedValue")), "estimated_equity": _f(x.get("estimatedEquity")), "equity_pct": _f(x.get("equityPercent")),
        "mls": {"status": x.get("mlsStatus"), "type": x.get("mlsType"), "listing_price": _f(x.get("mlsListingPrice")), "sold_price": _f(x.get("mlsSoldPrice")),
                "sold_date": _d(x.get("mlsLastSaleDate")), "listing_date": _d(x.get("mlsListingDate")), "dom": _i(x.get("mlsDaysOnMarket")), "has_photos": x.get("mlsHasPhotos"),
                "history": mls[:6], "keywords": {k: v for k, v in (x.get("mlsKeywords") or {}).items() if v}},
        "tax": {"assessed_value": _f(tax.get("assessedValue")), "market_value": _f(tax.get("marketValue")), "annual": _f(tax.get("taxAmount")), "year": tax.get("year") or tax.get("assessmentYear"),
                "delinquent_year": tax.get("taxDelinquentYear")},
        "flood": {"zone": x.get("floodZone"), "description": x.get("floodZoneDescription")} if x.get("floodZone") is not None else None,
        "flags": flags, "units": units,
        "neighborhood": (x.get("neighborhood") or {}).get("name") if isinstance(x.get("neighborhood"), dict) else x.get("neighborhood"),
    }


def reapi_comps(address: str, subject: Dict[str, Any], *, radius_mi: float, months: int, max_results: int = 25) -> Dict[str, Any]:
    """Recorded sales near the subject. The deed amount is the sale price
    (basis closed); when only an MLS sold price exists that is used and said.
    Transfers that are not sales ($0, nominal, non-arm's-length) are returned
    separately so the screen can show what was set aside and why."""
    body = {"address": address, "max_radius_miles": max(0.25, min(radius_mi, 5.0)), "max_results": max(5, min(max_results, 50)), "max_days_back": max(90, int(months * 30.4375))}
    data = _reapi("/v3/PropertyComps", body)
    rows = data.get("comps") or data.get("data") or []
    comps: List[Dict[str, Any]] = []
    set_aside: List[Dict[str, Any]] = []
    for c in rows:
        addr = c.get("address") or {}
        label = addr.get("address") if isinstance(addr, dict) else str(addr)
        deed_price, deed_date = _f(c.get("lastSaleAmount")), _d(c.get("lastSaleDate"))
        mls_price, mls_date = _f(c.get("mlsSoldPrice")), _d(c.get("mlsLastSaleDate"))
        note_bits = []
        price, date, basis = deed_price, deed_date, "closed"
        if not price or price < 1000:
            if mls_price and mls_date:
                price, date = mls_price, mls_date
                note_bits.append("MLS sold price; the deed shows %s" % ("$%s" % format(int(deed_price), ",") if deed_price else "no amount"))
            else:
                set_aside.append({"address": label, "deed_amount": deed_price, "deed_date": deed_date, "why": "deed records no market price (%s)" % ("$%s" % format(int(deed_price), ",") if deed_price else "$0")})
                continue
        elif mls_price and mls_date and abs(mls_price - price) / price > 0.03 and mls_date[:7] == (date or "")[:7]:
            note_bits.append("MLS sold price $%s" % format(int(mls_price), ","))
        if c.get("vacant"):
            note_bits.append("vacant")
        if c.get("cashBuyer"):
            note_bits.append("cash buyer")
        if c.get("mlsListingPrice") or c.get("mlsListingDate"):
            pass
        ptype = _REAPI_TYPES.get(str(c.get("propertyType") or "").upper())  # MFR is left unknown: Baltimore rowhouses carry it
        lot = _pos(c.get("lotSquareFeet"))
        if lot is not None and lot < 200:
            lot = None
        comps.append(new_comp(
            source="reapi", address=label, lat=_f(c.get("latitude")), lon=_f(c.get("longitude")),
            distance_mi=round(_f(c.get("distance")), 2) if _f(c.get("distance")) is not None else None,
            sale_price=price, sale_date=date, price_basis=basis, listing_price=_f(c.get("mlsListingPrice")) if c.get("mlsListingPrice") else None,
            property_type=ptype, beds=_i(c.get("bedrooms")), baths=_f(c.get("bathrooms")), sqft=_pos(c.get("squareFeet")), lot_sqft=lot, year_built=_i(c.get("yearBuilt")),
            note=", ".join(note_bits) or None,
            raw={"reapi_id": c.get("id"), "deed_amount": deed_price, "deed_date": deed_date, "mls_sold_price": mls_price, "mls_sold_date": mls_date,
                 "mls_listing_date": _d(c.get("mlsListingDate")), "mls_listing_price": _f(c.get("mlsListingPrice")), "estimated_value": _f(c.get("estimatedValue")),
                 "open_mortgage_balance": _f(c.get("openMortgageBalance")), "vacant": c.get("vacant"), "cash_buyer": c.get("cashBuyer"), "absentee_owner": c.get("absenteeOwner"),
                 "county_type": c.get("propertyType"), "land_use": c.get("landUse"), "assessed": _f(c.get("taxAssessedValue"))},
        ))
    for c in comps:
        if c.get("distance_mi") is None and c.get("lat") is not None and subject.get("lat") is not None:
            c["distance_mi"] = round(haversine_miles(subject["lat"], subject["lon"], c["lat"], c["lon"]), 2)
    avm = {"source": "RealEstateAPI AVM", "value": _f(data.get("reapiAvm")), "low": _f(data.get("reapiAvmLow")), "high": _f(data.get("reapiAvmHigh"))} if _f(data.get("reapiAvm")) else None
    return {"comps": comps, "set_aside": set_aside, "avm": avm, "warning": data.get("warning"), "fallback": data.get("fallback"), "returned": len(rows)}


def reapi_evidence(comp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Deed and MLS history for one shortlisted comp. Sets ``arms_length``,
    ``dom`` and the listing price on the comp and returns the evidence block
    the model reads."""
    addr = comp.get("address")
    if not addr:
        return None
    body: Dict[str, Any] = {"comps": False}
    rid = (comp.get("raw") or {}).get("reapi_id")
    if rid:
        body["id"] = rid
    else:
        body["address"] = addr
    data = _reapi("/v2/PropertyDetail", body)
    x = data.get("data") or {}
    if not x:
        return None
    pi = x.get("propertyInfo") or {}
    sales = sorted((_sale(s) for s in (x.get("saleHistory") or [])), key=lambda s: s.get("date") or "", reverse=True)
    mls = sorted((_mls_event(m) for m in (x.get("mlsHistory") or [])), key=lambda m: m.get("date") or "", reverse=True)
    # The sale on the grid is the latest one; its arm's-length flag decides whether it is a market price.
    latest = next((s for s in sales if s.get("price") and comp.get("sale_price") and abs(s["price"] - float(comp["sale_price"])) < 1), sales[0] if sales else None)
    if latest is not None and latest.get("arms_length") is not None:
        comp["arms_length"] = bool(latest["arms_length"])
    prior = [s for s in sales if latest is None or s is not latest]
    prior_sale = next((s for s in prior if (s.get("price") or 0) > 1000), None)
    closed = next((m for m in mls if str(m.get("status") or "").lower() in ("closed", "sold")), None)
    if closed:
        if comp.get("dom") is None and closed.get("dom") is not None:
            comp["dom"] = closed["dom"]
    listed = next((m for m in mls if str(m.get("status") or "").lower() in ("active", "pending", "under contract", "contingent", "listed")), None)
    if comp.get("listing_price") is None and (listed or closed):
        comp["listing_price"] = (listed or closed).get("price")
    for k in ("sqft", "beds", "baths", "year_built"):
        src = {"sqft": _pos(pi.get("livingSquareFeet")) or _pos(pi.get("buildingSquareFeet")), "beds": _i(pi.get("bedrooms")), "baths": _f(pi.get("bathrooms")), "year_built": _i(pi.get("yearBuilt"))}[k]
        if comp.get(k) in (None, 0) and src:
            comp[k] = src
    flip = None
    if latest and prior_sale and latest.get("date") and prior_sale.get("date"):
        try:
            d1 = datetime.fromisoformat(latest["date"][:10]); d0 = datetime.fromisoformat(prior_sale["date"][:10])
            months = (d1 - d0).days / 30.4375
            if months <= 24 and latest.get("price") and prior_sale.get("price"):
                flip = {"bought": prior_sale["price"], "bought_on": prior_sale["date"], "resold": latest["price"], "resold_on": latest["date"], "months_between": round(months, 1),
                        "gain_pct": round((latest["price"] / prior_sale["price"] - 1) * 100)}
        except ValueError:
            pass
    ev = {
        "latest_sale": latest, "prior_sale": prior_sale, "resale_within_24_months": flip,
        "mls_closed": closed, "mls_listing": listed, "mls_events": mls[:5], "mls_has_photos": x.get("mlsHasPhotos"),
        "county_condition": pi.get("buildingCondition"), "vacant": x.get("vacant"), "owner_occupied": x.get("ownerOccupied"),
        "flags": {k: True for k in ("preForeclosure", "auction", "bankOwned", "taxLien", "deathTransfer", "inherited", "quitClaim", "cashBuyer", "investorBuyer", "corporateOwned") if x.get(k)},
        "estimated_value": _f(x.get("estimatedValue")), "units": _i(pi.get("unitsCount")), "basement": _basement(pi),
    }
    comp["evidence"] = ev
    if comp.get("basement") in (None, "unknown") and ev["basement"] != "unknown":
        comp["basement"] = ev["basement"]
    if comp.get("arms_length") is False:
        comp["note"] = ", ".join(x for x in [comp.get("note"), "deed flags a non-arm's-length transfer"] if x)
    elif flip:
        comp["note"] = ", ".join(x for x in [comp.get("note"), "resold %d months after a $%s purchase (%+d%%)" % (round(flip["months_between"]), format(int(flip["bought"]), ","), flip["gain_pct"])] if x)
    return ev


# ================================================================== HouseCanary
def _hc_post(endpoint: str, items: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not (HOUSECANARY_API_KEY and HOUSECANARY_API_SECRET):
        raise RuntimeError("HouseCanary credentials are not set")
    version = "v3" if endpoint in ("details_advanced",) else "v2"
    r = requests.post("%s/%s/property/%s" % (HC_BASE, version, endpoint), json=items, auth=(HOUSECANARY_API_KEY, HOUSECANARY_API_SECRET),
                      headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"}, timeout=max(HTTP_TIMEOUT, 60))
    if r.status_code == 401:
        raise RuntimeError("HouseCanary refused the credentials")
    if r.status_code == 403:
        raise RuntimeError("HouseCanary: this account is not enabled for property/%s (Analytics API component)" % endpoint)
    if r.status_code == 429:
        raise RuntimeError("HouseCanary rate limit reached")
    if r.status_code >= 400:
        raise RuntimeError("HouseCanary %s: %s" % (r.status_code, r.text[:200]))
    out = r.json()
    return out if isinstance(out, list) else [out]


def _split(address: str) -> Dict[str, str]:
    """'1631 N Durham St, Baltimore, MD 21213' -> address + zipcode (what HouseCanary matches best on)."""
    parts = [p.strip() for p in str(address or "").split(",") if p.strip()]
    item: Dict[str, str] = {"address": parts[0] if parts else str(address or "")}
    tail = parts[-1] if len(parts) > 1 else ""
    tok = tail.split()
    if tok and tok[-1].isdigit() and len(tok[-1]) == 5:
        item["zipcode"] = tok[-1]
        if len(tok) >= 2 and len(tok[-2]) == 2:
            item["state"] = tok[-2]
    if len(parts) >= 3:
        item["city"] = parts[-2]
    return item


def _grade(v) -> Optional[str]:
    if v in (None, ""):
        return None
    s = str(v).upper()
    if s.startswith("C") and s[1:].isdigit():
        return s if s in ("C1", "C2", "C3", "C4", "C5", "C6") else None
    n = _i(v)
    return ("C%d" % n) if n and 1 <= n <= 6 else None


def hc_details(address: str, *, details: Dict[str, Any]) -> Dict[str, Any]:
    """Shape one details_advanced result."""
    hc = details.get("hc") or {}
    pr = details.get("public_records") or {}
    pick = lambda k, sub=None: ((hc.get(k) if sub is None else (hc.get(k) or {}).get(sub)) if hc else None) or ((pr.get(k) if sub is None else (pr.get(k) or {}).get(sub)) if pr else None)
    ptype = _HC_TYPES.get(str(hc.get("property_type") or "").upper()) or _HC_TYPES.get(str(pr.get("property_type") or "").upper())
    has_b = pick("basement", "has")
    fin = _f(pick("basement", "finished_pct")) or 0
    basement = "unknown" if has_b is None else ("none" if not has_b else ("finished" if fin >= 50 else "unfinished"))
    return {
        "condition": _grade(hc.get("condition_class")), "county_condition": pr.get("building_condition_code"), "building_quality": pick("building_quality"),
        "sqft": _pos(pick("living_area")) or _pos(pick("living_area_above_grade")), "beds": _i(pick("bedrooms")), "baths": _f(pick("bathrooms", "total")) or _f(pick("bathrooms", "full")),
        "year_built": _i(pick("year_built")), "lot_sqft": _pos(pick("lot_size")), "stories": _pos(pick("stories")), "property_type": ptype,
        "garage_spaces": _pos(pick("parking", "garage")), "parking": pick("parking", "description"), "basement": basement, "pool": hc.get("pool") if hc.get("pool") is not None else pr.get("pool"),
        "heating": ", ".join(hc.get("heating") or pr.get("heating") or []) or None, "cooling": ", ".join(hc.get("cooling") or pr.get("cooling") or []) or None,
        "roof": pick("roof_material") or pick("roof_type"), "exterior": pick("walls_exterior_material"), "units": _i(pick("units_total")),
        "zoning": (hc.get("zoning") or {}).get("code") if isinstance(hc.get("zoning"), dict) else None,
        "assessment": details.get("assessment"), "slug": (details.get("subject_address") or {}).get("slug"), "address_full": (details.get("subject_address") or {}).get("address_full"),
    }


def hc_enrich(addresses: List[str]) -> Dict[str, Any]:
    """details_advanced + sales_history for a handful of addresses in two
    batch calls. Returns {address: {...details, sales: [...]}} keyed by the
    address string passed in, plus the cost and any per-address errors."""
    addresses = [a for a in dict.fromkeys(a for a in addresses if a)]
    if not addresses:
        return {"by_address": {}, "cost_usd": 0.0, "errors": []}
    items = [_split(a) for a in addresses]
    out: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    billed = 0
    try:
        det = _hc_post("details_advanced", items)
    except Exception as exc:
        raise RuntimeError("details_advanced: %s" % exc)
    for a, row in zip(addresses, det):
        if "subject_address" in row or "hc" in row or "public_records" in row:   # v3 shape: the result itself
            result, status = row, None
        else:                                                                    # v2 shape: wrapped with api_code
            block = row.get("property/details_advanced") or {}
            if block.get("api_code") not in (0, None) or not block.get("result"):
                errors.append("%s: %s" % (a, block.get("api_code_description") or "no match"))
                continue
            result, status = block["result"], ((row.get("address_info") or {}).get("status") or {}).get("match")
        if not (result.get("hc") or result.get("public_records")):
            errors.append("%s: no match" % a)
            continue
        billed += 1
        out[a] = hc_details(a, details=result)
        out[a]["match"] = True if status is None else status
    try:
        sh = _hc_post("sales_history", items)
    except Exception as exc:
        errors.append("sales_history: %s" % exc)
        sh = []
    for a, row in zip(addresses, sh):
        block = row.get("property/sales_history") or {}
        if block.get("api_code") not in (0, None):
            continue
        res = block.get("result") or []
        if res:
            billed += 1
        sales = [{"date": s.get("record_date"), "price": _f(s.get("amount")), "event": s.get("event_type"), "buyer": " ".join(x for x in [s.get("grantee_1_forenames"), s.get("grantee_1")] if x) or None,
                  "seller": " ".join(x for x in [s.get("grantor_1_forenames"), s.get("grantor_1")] if x) or None} for s in res]
        sales.sort(key=lambda s: s.get("date") or "", reverse=True)
        out.setdefault(a, {})["sales"] = sales[:6]
    cost = round(billed * 0.50, 2)
    return {"by_address": out, "cost_usd": cost, "errors": errors, "billed_properties": billed}


def apply_hc_to_comp(comp: Dict[str, Any], hc: Dict[str, Any]) -> List[str]:
    """Write HouseCanary's facts onto a comp: the condition grade (unless a
    person set one), blanks in size and rooms, and the recorded sale check.
    Returns what changed."""
    changed: List[str] = []
    if hc.get("condition") and not comp.get("condition_source") in ("manual", "document") and comp.get("condition") in (None, ""):
        comp["condition"] = hc["condition"]
        comp["condition_source"] = "housecanary"
        changed.append("condition %s" % hc["condition"])
    elif hc.get("condition") and comp.get("condition") and comp.get("condition") != hc["condition"] and comp.get("condition_source") != "manual":
        comp["condition_hc"] = hc["condition"]
    for k in ("sqft", "beds", "baths", "year_built", "lot_sqft", "garage_spaces"):
        if comp.get(k) in (None, 0) and hc.get(k):
            comp[k] = hc[k]
            changed.append(k)
    if comp.get("basement") in (None, "unknown") and hc.get("basement") not in (None, "unknown"):
        comp["basement"] = hc["basement"]
        changed.append("basement")
    # County "multi-family" on a one-unit rowhouse is noise (Baltimore codes rentals that way); only take a
    # HouseCanary type that cannot knock an analyst pick off the grid for a coding reason.
    if comp.get("property_type") is None and hc.get("property_type") and (hc["property_type"] != "multi_2_4" or (hc.get("units") or 0) >= 2):
        comp["property_type"] = hc["property_type"]
        changed.append("type")
    sales = hc.get("sales") or []
    if sales:
        arms = [s for s in sales if s.get("event") == "arms_length_sale" and (s.get("price") or 0) > 1000]
        top = arms[0] if arms else None
        comp["hc_sale"] = top
        if top and comp.get("sale_price"):
            same = abs(float(top["price"]) - float(comp["sale_price"])) / float(comp["sale_price"]) < 0.02
            comp["price_verified"] = "housecanary" if same else None
            if same and comp.get("price_basis") != "closed":
                comp["price_basis"] = "closed"
                comp["sale_date"] = top.get("date") or comp.get("sale_date")
                changed.append("closed price confirmed")
            elif not same and comp.get("price_basis") != "closed" and top.get("date") and top["date"] >= (comp.get("sale_date") or "")[:10]:
                comp["sale_price"], comp["sale_date"], comp["price_basis"] = top["price"], top["date"], "closed"
                changed.append("closed price from the deed")
        if top and comp.get("arms_length") is None:
            comp["arms_length"] = True
        if not arms and sales and comp.get("arms_length") is None and sales[0].get("event") and "arms" not in str(sales[0]["event"]):
            comp["arms_length"] = False
    comp["hc"] = {k: hc.get(k) for k in ("condition", "county_condition", "building_quality", "sqft", "beds", "baths", "year_built", "units", "match")}
    return changed
