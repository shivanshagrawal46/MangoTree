"""Where the facts about the subject and the comparable sales come from.

Every comp carries ``source`` so the screen and the model know how much to
trust it:

  rentcast   MLS-derived records and an AVM with comparables (needs a key;
             the free developer tier is 50 calls a month). The comparables'
             ``price`` is the *listed* price of the matching listing, so it is
             labelled ``price_basis: listed`` unless a closed price is known.
  web        found by Claude with web search on public listing pages. Useful
             when no data key is set; figures are unverified and weighted down.
  manual     typed in by the person (a realtor's CMA, a closed deal they know).

A comp is a plain dict so it can be stored, edited and re-scored freely:
  comp_id, source, address, lat, lon, distance_mi, sale_price, sale_date,
  price_basis ('closed' | 'listed' | 'estimated'), listing_price, dom,
  property_type, beds, baths, sqft, lot_sqft, year_built, garage_spaces,
  basement, pool, condition (C1-C6 or None), url, note, included (bool|None),
  manual_adjustments [{label, amount, why}], raw
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from mangotree.core.logging import logger

from .config import HTTP_TIMEOUT, RENTCAST_API_KEY, USER_AGENT
from .geocode import haversine_miles

RENTCAST_BASE = "https://api.rentcast.io/v1"
_RC_TYPES = {"Single Family": "single_family", "Townhouse": "townhouse", "Condo": "condo", "Multi-Family": "multi_2_4", "Apartment": "multi_2_4",
             "Manufactured": "manufactured", "Land": "land"}
_RC_TYPES_OUT = {v: k for k, v in _RC_TYPES.items()}


def new_comp(**fields) -> Dict[str, Any]:
    base = {"comp_id": uuid.uuid4().hex[:10], "source": "manual", "address": None, "lat": None, "lon": None, "distance_mi": None,
            "sale_price": None, "sale_date": None, "price_basis": "closed", "listing_price": None, "dom": None, "property_type": None,
            "beds": None, "baths": None, "sqft": None, "lot_sqft": None, "year_built": None, "garage_spaces": None, "basement": "unknown",
            "pool": None, "condition": None, "url": None, "note": None, "included": None, "manual_adjustments": [], "raw": None}
    base.update({k: v for k, v in fields.items() if k in base})
    return base


def _rc_get(path: str, params: Dict[str, Any]) -> Any:
    if not RENTCAST_API_KEY:
        raise RuntimeError("RENTCAST_API_KEY is not set")
    r = requests.get(RENTCAST_BASE + path, params=params, headers={"X-Api-Key": RENTCAST_API_KEY, "Accept": "application/json", "User-Agent": USER_AGENT},
                     timeout=HTTP_TIMEOUT)
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        raise RuntimeError("RentCast rate limit reached for this key")
    if r.status_code == 400:
        try:
            msg = (r.json() or {}).get("message") or ""
        except ValueError:
            msg = ""
        raise RuntimeError("RentCast: %s" % (msg or "bad request"))
    if r.status_code in (401, 403):
        # RentCast says why in the body: an invalid key, or a key with no active plan (the free
        # Developer plan must be activated on app.rentcast.io/app/api before the key works).
        try:
            msg = (r.json() or {}).get("message") or (r.json() or {}).get("error") or ""
        except ValueError:
            msg = ""
        raise RuntimeError("RentCast refused the key (%s): %s" % (r.status_code, msg or "check the plan on app.rentcast.io/app/api"))
    r.raise_for_status()
    return r.json()


def _basement_from_features(f: Dict[str, Any]) -> str:
    if not f:
        return "unknown"
    if f.get("basement") is None:
        return "unknown"
    if not f.get("basement"):
        return "none"
    t = str(f.get("basementType") or "").lower()
    return "finished" if "finish" in t and "unfinish" not in t else "unfinished"


def rentcast_record(address: str) -> Optional[Dict[str, Any]]:
    """Public-record facts for the subject, to prefill what the person has not
    typed: beds, baths, living area, lot, year built, type, last sale, taxes."""
    rows = _rc_get("/properties", {"address": address, "limit": 1})
    if not rows:
        return None
    r = rows[0] if isinstance(rows, list) else rows
    feats = r.get("features") or {}
    taxes = r.get("propertyTaxes") or {}
    assess = r.get("taxAssessments") or {}
    last_tax = taxes.get(max(taxes)) if taxes else None
    last_assess = assess.get(max(assess)) if assess else None
    hist = r.get("history") or {}
    sales = sorted(((k, v) for k, v in hist.items() if (v or {}).get("event") == "Sale"), key=lambda kv: kv[0])
    return {
        "source": "rentcast", "formatted_address": r.get("formattedAddress"), "lat": r.get("latitude"), "lon": r.get("longitude"),
        "property_type": _RC_TYPES.get(r.get("propertyType") or "", None), "beds": r.get("bedrooms"), "baths": r.get("bathrooms"),
        "sqft": r.get("squareFootage"), "lot_sqft": r.get("lotSize"), "year_built": r.get("yearBuilt"), "stories": feats.get("floorCount"),
        "garage_spaces": feats.get("garageSpaces") if feats.get("garage") else (0 if feats.get("garage") is False else None),
        "basement": _basement_from_features(feats), "pool": feats.get("pool"), "owner_occupied": r.get("ownerOccupied"),
        "last_sale_price": r.get("lastSalePrice"), "last_sale_date": (r.get("lastSaleDate") or "")[:10] or None,
        "sale_history": [{"date": (v.get("date") or k)[:10], "price": v.get("price")} for k, v in sales][-5:],
        "tax_annual": (last_tax or {}).get("total"), "tax_year": max(taxes) if taxes else None,
        "assessed_value": (last_assess or {}).get("value"), "assessed_year": max(assess) if assess else None,
        "zoning": r.get("zoning"), "county": r.get("county"), "hoa_monthly": (r.get("hoa") or {}).get("fee"),
        "raw_keys": sorted(r.keys()),
    }


def rentcast_avm(address: str, subject: Dict[str, Any], *, radius_mi: float, months: int, comp_count: int = 15) -> Optional[Dict[str, Any]]:
    """RentCast's value estimate with the comparables it used. The comparables
    become candidate comps (source rentcast, price basis listed)."""
    params: Dict[str, Any] = {"address": address, "compCount": max(5, min(comp_count, 25)), "maxRadius": max(0.5, radius_mi), "daysOld": max(90, months * 30)}
    if subject.get("property_type") in _RC_TYPES_OUT:
        params["propertyType"] = _RC_TYPES_OUT[subject["property_type"]]
    for k_in, k_out in (("beds", "bedrooms"), ("baths", "bathrooms"), ("sqft", "squareFootage")):
        if subject.get(k_in) is not None:
            params[k_out] = subject[k_in]
    relaxed = None
    try:
        d = _rc_get("/avm/value", params)
    except RuntimeError as exc:
        if "insufficient comparables" not in str(exc).lower():
            raise
        # Nothing of this size and room count nearby (a rural house, a composite living area): ask again
        # with no size or room filter and a wider net, keep the comparables for the grid, and drop the
        # value itself, which would be a value for a different kind of house.
        wide = {k: v for k, v in params.items() if k not in ("bedrooms", "bathrooms", "squareFootage")}
        wide["maxRadius"] = min(10.0, max(3.0, radius_mi * 3))
        d = _rc_get("/avm/value", wide)
        relaxed = "RentCast found no comparable of this size and room count within %.1f mi, so it cannot value the house as described; its nearby sales are on the grid (searched %.0f mi, any size)." % (max(0.5, radius_mi), wide["maxRadius"])
    if not d:
        return None
    comps = []
    for c in d.get("comparables") or []:
        sale_date = (c.get("removedDate") or c.get("lastSeenDate") or c.get("listedDate") or "")[:10] or None
        basis = "listed"
        price = c.get("price")
        if c.get("lastSalePrice") and c.get("lastSaleDate"):
            price, sale_date, basis = c["lastSalePrice"], c["lastSaleDate"][:10], "closed"
        comps.append(new_comp(
            source="rentcast", address=c.get("formattedAddress"), lat=c.get("latitude"), lon=c.get("longitude"),
            distance_mi=round(float(c["distance"]), 2) if c.get("distance") is not None else None, sale_price=price, sale_date=sale_date,
            price_basis=basis, listing_price=c.get("price"), dom=c.get("daysOnMarket"), property_type=_RC_TYPES.get(c.get("propertyType") or "", None),
            beds=c.get("bedrooms"), baths=c.get("bathrooms"), sqft=c.get("squareFootage"), lot_sqft=c.get("lotSize"), year_built=c.get("yearBuilt"),
            note=("RentCast correlation %.2f" % c["correlation"]) if c.get("correlation") is not None else None,
            raw={"id": c.get("id"), "status": c.get("status"), "listingType": c.get("listingType"), "correlation": c.get("correlation"), "daysOld": c.get("daysOld")},
        ))
    if relaxed:
        return {"source": "RentCast AVM", "value": None, "low": None, "high": None, "as_of": datetime.now(timezone.utc).date().isoformat(), "comps": comps, "note": relaxed}
    return {"source": "RentCast AVM", "value": d.get("price"), "low": d.get("priceRangeLow"), "high": d.get("priceRangeHigh"),
            "as_of": datetime.now(timezone.utc).date().isoformat(), "comps": comps,
            "note": "RentCast's model value for the property as it stands (not as repaired); comparables carry the listing price unless a closed sale is known."}


def dedupe(comps: List[Dict[str, Any]], subject: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Same address from two sources: keep the better-documented one (closed
    price over listed, data feed over web), merge what the other knew."""
    seen: Dict[str, Dict[str, Any]] = {}
    rank = {"closed": 2, "listed": 1, "estimated": 0}
    srank = {"manual": 3, "document": 3, "rentcast": 2, "attom": 2, "web": 1}
    out: List[Dict[str, Any]] = []
    for c in comps:
        key = " ".join(str(c.get("address") or "").lower().replace(",", " ").split())[:40]
        if c.get("lat") is not None and subject.get("lat") is not None and c.get("distance_mi") is None:
            c["distance_mi"] = round(haversine_miles(subject["lat"], subject["lon"], c["lat"], c["lon"]), 2)
        if not key:
            out.append(c)
            continue
        prev = seen.get(key)
        if prev is None:
            seen[key] = c
            out.append(c)
            continue
        better = c if (rank.get(c.get("price_basis"), 0), srank.get(c.get("source"), 0)) > (rank.get(prev.get("price_basis"), 0), srank.get(prev.get("source"), 0)) else prev
        other = prev if better is c else c
        for k, v in other.items():
            if better.get(k) in (None, "unknown", [], "") and v not in (None, "unknown", [], ""):
                better[k] = v
        better["note"] = ", ".join(x for x in [better.get("note"), "also found by " + str(other.get("source"))] if x)
        if better is c:
            out[out.index(prev)] = c
            seen[key] = c
    return out


def recent_cutoff(months: int) -> str:
    return (datetime.now() - timedelta(days=int(months * 30.4375))).date().isoformat()
