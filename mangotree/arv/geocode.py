"""Address → coordinates and geography, United States only.

Primary: the US Census Bureau geocoder (free, no key, returns the matched
address, the TIGER coordinates, state/county FIPS and the census tract — the
tract is what the ACS lookup needs). Fallback: OpenStreetMap Nominatim, which
knows more building-level points but no FIPS. Both are rate-limited public
services, so results are cached in memory for the life of the process.
"""
from __future__ import annotations

import math
import re
import threading
from typing import Any, Dict, Optional

import requests

from mangotree.core.logging import logger

from .config import HTTP_TIMEOUT, USER_AGENT

_CACHE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_NAME_TO_ABBR = {v.lower(): k for k, v in STATES.items()}


class GeocodeError(ValueError):
    pass


_KEEP_UPPER = {"NW", "NE", "SW", "SE", "N", "S", "E", "W", "US", "DC"}


def _pretty(s: str) -> str:
    """Census returns SHOUTING addresses; title-case them but keep NW / SE / US."""
    out = []
    for w in (s or "").strip().split():
        out.append(w if w.upper() in _KEEP_UPPER else (w.title() if w.isalpha() or w[:1].isdigit() else w))
    return " ".join(out)


def _headers() -> Dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _census(address: str) -> Optional[Dict[str, Any]]:
    url = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
    params = {"address": address, "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json"}
    r = requests.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    matches = (((r.json() or {}).get("result") or {}).get("addressMatches")) or []
    if not matches:
        return None
    m = matches[0]
    comp = m.get("addressComponents") or {}
    geos = m.get("geographies") or {}
    tract = (geos.get("Census Tracts") or [{}])[0]
    county = (geos.get("Counties") or [{}])[0]
    coords = m.get("coordinates") or {}
    # addressComponents carries the TIGER range (fromAddress/toAddress), not the
    # house number; the matched address string has the number the person typed.
    matched = m.get("matchedAddress") or ""
    line1 = _pretty(matched.split(",")[0]) or None
    return {
        "provider": "census",
        "matched_address": matched,
        "lat": float(coords.get("y")), "lon": float(coords.get("x")),
        "line1": line1,
        "city": _pretty(comp.get("city") or "") or None, "state": comp.get("state"), "zip": comp.get("zip"),
        "county": county.get("BASENAME") or county.get("NAME"), "county_fips": county.get("GEOID"),
        "state_fips": tract.get("STATE") or county.get("STATE"), "tract_geoid": tract.get("GEOID"),
        "tract": tract.get("TRACT"), "precision": "address_range",
    }


def _nominatim(address: str) -> Optional[Dict[str, Any]]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "jsonv2", "limit": 1, "countrycodes": "us", "addressdetails": 1}
    r = requests.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    rows = r.json() or []
    if not rows:
        return None
    m = rows[0]
    a = m.get("address") or {}
    state_name = (a.get("state") or "").lower()
    state = _NAME_TO_ABBR.get(state_name) or ("DC" if "district of columbia" in state_name else None)
    house = a.get("house_number") or ""
    road = a.get("road") or ""
    return {
        "provider": "nominatim",
        "matched_address": m.get("display_name"),
        "lat": float(m["lat"]), "lon": float(m["lon"]),
        "line1": (house + " " + road).strip() or None,
        "city": a.get("city") or a.get("town") or a.get("village") or a.get("suburb"),
        "state": state, "zip": a.get("postcode"), "county": a.get("county"),
        "county_fips": None, "state_fips": None, "tract_geoid": None, "tract": None,
        "precision": "rooftop" if m.get("osm_type") in ("node", "way") and house else "street",
    }


def _enrich_geographies(out: Dict[str, Any]) -> None:
    """Census tract / county FIPS for a point the Census address matcher missed
    (the ACS lookup needs them). Best effort."""
    try:
        url = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
        params = {"x": out["lon"], "y": out["lat"], "benchmark": "Public_AR_Current", "vintage": "Current_Current", "format": "json"}
        r = requests.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        geos = ((r.json() or {}).get("result") or {}).get("geographies") or {}
        tract = (geos.get("Census Tracts") or [{}])[0]
        county = (geos.get("Counties") or [{}])[0]
        if tract.get("GEOID"):
            out.update({"tract_geoid": tract.get("GEOID"), "tract": tract.get("TRACT"), "state_fips": tract.get("STATE"),
                        "county_fips": county.get("GEOID") or (tract.get("STATE", "") + tract.get("COUNTY", "")), "county": out.get("county") or county.get("BASENAME")})
    except Exception as exc:
        logger.info("census geographies enrichment failed: %s", exc)


def normalize_query(address: str) -> str:
    s = re.sub(r"\s+", " ", (address or "").strip())
    s = re.sub(r",?\s*(USA|United States|US)\.?$", "", s, flags=re.I)
    return s


def geocode(address: str) -> Dict[str, Any]:
    """Resolve one US address. Raises GeocodeError when nothing matches or the
    match is outside the United States."""
    q = normalize_query(address)
    if len(q) < 6:
        raise GeocodeError("Enter a street address, city and state (or ZIP).")
    with _LOCK:
        hit = _CACHE.get(q.lower())
    if hit:
        return dict(hit)
    out: Optional[Dict[str, Any]] = None
    errors = []
    has_number = bool(re.match(r"^\d+[A-Za-z]?\b", q))
    for fn in (_census, _nominatim):
        try:
            out = fn(q)
            # A street-level match for a numbered address is the wrong house, not
            # a near miss: refuse it rather than value somebody else's property.
            if out and has_number and out.get("precision") == "street":
                errors.append("only the street matched, not the house number")
                out = None
            if out:
                break
        except Exception as exc:
            errors.append(fn.__name__.strip("_") + ": " + type(exc).__name__)
            logger.info("geocode %s failed for %r: %s", fn.__name__, q, exc)
    if not out:
        raise GeocodeError("Address not found in the United States. Check the street number, city and state." + ((" (" + "; ".join(errors) + ")") if errors else ""))
    if out.get("provider") == "nominatim" and not out.get("tract"):
        _enrich_geographies(out)
    if out.get("state") and out["state"].upper() not in STATES:
        raise GeocodeError("This tool values property in the United States only.")
    out["state"] = (out.get("state") or "").upper() or None
    out["state_name"] = STATES.get(out["state"] or "", None)
    out["query"] = q
    tail = " ".join(y for y in [out.get("state"), out.get("zip")] if y)
    out["formatted"] = ", ".join(x for x in [out.get("line1"), out.get("city"), tail] if x) or out.get("matched_address") or q
    with _LOCK:
        _CACHE[q.lower()] = dict(out)
    return out


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
