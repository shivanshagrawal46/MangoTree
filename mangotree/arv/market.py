"""Free market context for a subject. Every source is best-effort: a slow or
blocked public endpoint produces a labelled gap, never a stalled valuation.

  FRED        30-year fixed mortgage rate (weekly, keyless CSV)
  Zillow      ZHVI by ZIP — typical home value, 12-month and 3-month change
              (public research CSV, ~120 MB, cached on disk for a day)
  FEMA NFHL   flood zone at the point (ArcGIS REST)
  Census ACS  tract median home value, median household income, owner share,
              median year built (5-year estimates, keyless for light use)
"""
from __future__ import annotations

import csv
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from mangotree.core.logging import logger

from .config import CACHE_DIR, HTTP_TIMEOUT, USER_AGENT

_H = {"User-Agent": USER_AGENT}
_MEM: Dict[str, Any] = {}
_LOCK = threading.Lock()


def _memo(key: str, ttl: float, fn):
    with _LOCK:
        hit = _MEM.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _LOCK:
        _MEM[key] = (time.time(), v)
    return v


# ----------------------------------------------------------------- FRED
def mortgage_rate() -> Optional[Dict[str, Any]]:
    def fetch():
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": "MORTGAGE30US"}, headers=_H, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        rows = [ln.split(",") for ln in r.text.strip().splitlines()[1:] if "," in ln]
        rows = [(d, float(v)) for d, v in rows if v not in (".", "")]
        if not rows:
            return None
        d, v = rows[-1]
        cutoff = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=364)).strftime("%Y-%m-%d")
        year_ago = next((x for x in reversed(rows) if x[0] <= cutoff), None)
        return {"series": "MORTGAGE30US", "as_of": d, "rate_pct": v, "year_ago_pct": year_ago[1] if year_ago else None,
                "history": [{"date": a, "rate": b} for a, b in rows[-52:]], "source": "FRED / Freddie Mac PMMS"}
    try:
        return _memo("fred30", 6 * 3600, fetch)
    except Exception as exc:
        logger.info("FRED unavailable: %s", exc)
        return None


# --------------------------------------------------------------- Zillow
_ZHVI_URL = "https://files.zillowstatic.com/research/public_csvs/zhvi/Zip_zhvi_uc_sfrcondo_tier_0.33_0.67_sm_sa_month.csv"
_ZHVI_INDEX: Dict[str, Dict[str, Any]] = {}
_ZHVI_DATES: List[str] = []
_ZHVI_LOADED_AT: Optional[float] = None
_ZHVI_LOCK = threading.Lock()


def _zhvi_path():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / "zhvi_zip.csv"


def _load_zhvi(max_age_hours: float = 24.0) -> bool:
    """Download once a day; index rows by ZIP in memory (about 27k rows)."""
    global _ZHVI_LOADED_AT, _ZHVI_DATES
    with _ZHVI_LOCK:
        if _ZHVI_LOADED_AT and time.time() - _ZHVI_LOADED_AT < max_age_hours * 3600 and _ZHVI_INDEX:
            return True
        p = _zhvi_path()
        fresh = p.exists() and (time.time() - p.stat().st_mtime) < max_age_hours * 3600
        if not fresh:
            try:
                with requests.get(_ZHVI_URL, headers=_H, timeout=(10, 180), stream=True) as r:
                    r.raise_for_status()
                    tmp = p.with_suffix(".part")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                    tmp.replace(p)
            except Exception as exc:
                logger.info("ZHVI download failed: %s", exc)
                if not p.exists():
                    return False
        try:
            with open(p, newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader)
                _ZHVI_DATES = [h for h in header if len(h) == 10 and h[4] == "-"]
                first = header.index(_ZHVI_DATES[0])
                zip_idx = header.index("RegionName")
                metro_idx = header.index("Metro") if "Metro" in header else None
                county_idx = header.index("CountyName") if "CountyName" in header else None
                _ZHVI_INDEX.clear()
                for row in reader:
                    _ZHVI_INDEX[row[zip_idx].zfill(5)] = {"vals": row[first:first + len(_ZHVI_DATES)],
                                                          "metro": row[metro_idx] if metro_idx is not None else "",
                                                          "county": row[county_idx] if county_idx is not None else ""}
            _ZHVI_LOADED_AT = time.time()
            return True
        except Exception as exc:
            logger.info("ZHVI parse failed: %s", exc)
            return False


def zhvi_for_zip(zip5: Optional[str]) -> Optional[Dict[str, Any]]:
    if not zip5:
        return None
    if not _load_zhvi():
        return None
    row = _ZHVI_INDEX.get(str(zip5).zfill(5))
    if not row:
        return None
    series = [(d, float(v)) for d, v in zip(_ZHVI_DATES, row["vals"]) if v not in ("", None)]
    if len(series) < 4:
        return None
    d, v = series[-1]

    def back(n):
        return series[-1 - n][1] if len(series) > n else None
    v12, v3, v36 = back(12), back(3), back(36)
    return {"zip": str(zip5).zfill(5), "as_of": d, "value": round(v), "metro": row["metro"] or None, "county": row["county"] or None,
            "change_12m_pct": round((v / v12 - 1) * 100, 2) if v12 else None,
            "change_3m_pct": round((v / v3 - 1) * 100, 2) if v3 else None,
            "change_36m_pct": round((v / v36 - 1) * 100, 2) if v36 else None,
            "monthly_rate_pct": round(((v / v12) ** (1 / 12) - 1) * 100, 3) if v12 else None,
            "history": [{"date": a, "value": round(b)} for a, b in series[-36:]],
            "source": "Zillow ZHVI (ZIP, all homes, smoothed, seasonally adjusted)"}


# ----------------------------------------------------------------- FEMA
def flood_zone(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    def fetch():
        url = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
        params = {"geometry": "%s,%s" % (lon, lat), "geometryType": "esriGeometryPoint", "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                  "outFields": "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STATIC_BFE", "returnGeometry": "false", "f": "json"}
        r = requests.get(url, params=params, headers=_H, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        feats = (r.json() or {}).get("features") or []
        if not feats:
            return {"zone": "X (unmapped / minimal)", "sfha": False, "note": "No NFHL flood zone polygon at this point.", "source": "FEMA NFHL"}
        a = feats[0].get("attributes") or {}
        zone = a.get("FLD_ZONE") or "?"
        sfha = str(a.get("SFHA_TF") or "").upper() == "T"
        return {"zone": zone, "subtype": a.get("ZONE_SUBTY"), "sfha": sfha, "bfe": a.get("STATIC_BFE"),
                "note": "Special Flood Hazard Area: a lender will require flood insurance." if sfha else "Outside the Special Flood Hazard Area.",
                "source": "FEMA NFHL"}
    try:
        return _memo("fema:%.4f,%.4f" % (lat, lon), 24 * 3600, fetch)
    except Exception as exc:
        logger.info("FEMA NFHL unavailable: %s", exc)
        return None


# ------------------------------------------------------------------ ACS
_ACS_VARS = {"B25077_001E": "median_home_value", "B19013_001E": "median_household_income", "B25035_001E": "median_year_built",
             "B25003_001E": "occupied_units", "B25003_002E": "owner_occupied", "B25004_001E": "vacant_units", "B01003_001E": "population"}


def acs_tract(state_fips: Optional[str], county_fips: Optional[str], tract: Optional[str]) -> Optional[Dict[str, Any]]:
    if not (state_fips and county_fips and tract):
        return None
    county3 = str(county_fips)[-3:]

    def fetch():
        url = "https://api.census.gov/data/2023/acs/acs5"
        params = {"get": ",".join(["NAME"] + list(_ACS_VARS)), "for": "tract:" + str(tract), "in": "state:%s county:%s" % (state_fips, county3)}
        r = requests.get(url, params=params, headers=_H, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        rows = r.json()
        if not rows or len(rows) < 2:
            return None
        rec = dict(zip(rows[0], rows[1]))
        out: Dict[str, Any] = {"name": rec.get("NAME"), "vintage": "ACS 2019-2023 5-year", "source": "US Census Bureau ACS"}
        for var, key in _ACS_VARS.items():
            try:
                v = float(rec.get(var))
                out[key] = None if v < 0 else int(v)
            except (TypeError, ValueError):
                out[key] = None
        if out.get("occupied_units") and out.get("owner_occupied") is not None:
            out["owner_share_pct"] = round(100 * out["owner_occupied"] / out["occupied_units"], 1)
        total = (out.get("occupied_units") or 0) + (out.get("vacant_units") or 0)
        if total and out.get("vacant_units") is not None:
            out["vacancy_pct"] = round(100 * out["vacant_units"] / total, 1)
        return out
    try:
        return _memo("acs:%s%s%s" % (state_fips, county3, tract), 7 * 24 * 3600, fetch)
    except Exception as exc:
        logger.info("ACS unavailable: %s", exc)
        return None


_GAP_TEXT = {"mortgage": "FRED mortgage rate unavailable", "zhvi": "Zillow ZHVI for this ZIP unavailable",
             "flood": "FEMA flood zone lookup unavailable", "acs": "Census ACS tract statistics unavailable"}


def gather(geo: Dict[str, Any], emit=None) -> Dict[str, Any]:
    """Everything the free sources know about the subject's market, in parallel,
    each with its own deadline."""
    jobs = {
        "mortgage": lambda: mortgage_rate(),
        "zhvi": lambda: zhvi_for_zip(geo.get("zip")),
        "flood": lambda: flood_zone(geo["lat"], geo["lon"]) if geo.get("lat") is not None else None,
        "acs": lambda: acs_tract(geo.get("state_fips"), geo.get("county_fips"), geo.get("tract")),
    }
    out: Dict[str, Any] = {"gathered_at": datetime.now(timezone.utc).isoformat(), "gaps": []}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {k: ex.submit(fn) for k, fn in jobs.items()}
        for k, f in futs.items():
            try:
                out[k] = f.result(timeout=200 if k == "zhvi" else 40)
            except Exception as exc:
                out[k] = None
                logger.info("market %s: %s", k, exc)
            if out[k] is None:
                out["gaps"].append(_GAP_TEXT[k])
            if emit:
                emit(k, out[k])
    return out
