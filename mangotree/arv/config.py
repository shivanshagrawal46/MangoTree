"""ARV settings. Every key is optional: with none set the system still works —
Census geocoding, Claude web search for comps, FRED / Zillow / FEMA / Census
market context and manual comps need no account. A RentCast key (free tier:
50 calls a month) adds MLS-derived sale comps and an AVM to blend against.
"""
from __future__ import annotations

import os
from pathlib import Path

from mangotree.config.settings import PROJECT_ROOT


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


#: Claude Opus 5.5 at effort high — the analyst seat for valuation (admin ask 2026-10-14).
ARV_MODEL = _env("ARV_MODEL", "claude-opus-5-5")
ARV_EFFORT = _env("ARV_EFFORT", "high")
#: Server-side refusal fallback (routes a declined request to a sibling model
#: inside the same call). Harmless on valuation text; switched off automatically
#: if the API rejects the parameter.
ARV_FALLBACKS = _env("ARV_FALLBACKS", "1").lower() in ("1", "true", "yes")

RENTCAST_API_KEY = _env("RENTCAST_API_KEY")
ATTOM_API_KEY = _env("ATTOM_API_KEY")
#: Paid feeds added 2026-10-20 (see feeds.py): recorded sales and the subject's
#: public-record file from RealEstateAPI; condition grade and verified facts
#: for the chosen comps from HouseCanary (Basic plan, per-call billing).
REALESTATEAPI_KEY = _env("REALESTATEAPI_KEY")
HOUSECANARY_API_KEY = _env("HOUSECANARY_API_KEY")
HOUSECANARY_API_SECRET = _env("HOUSECANARY_API_SECRET")
#: How many comps the model shortlists from the scored candidates (the ones
#: HouseCanary is then paid to verify). Admin ask 2026-10-20: five.
SHORTLIST_SIZE = int(_env("ARV_SHORTLIST_SIZE", "5"))
SHORTLIST_POOL = int(_env("ARV_SHORTLIST_POOL", "10"))
GOOGLE_MAPS_API_KEY = _env("GOOGLE_MAPS_API_KEY")   # Street View / Places, optional
FRED_API_KEY = _env("FRED_API_KEY")                 # not needed: the CSV endpoint is keyless

#: Comparable-sale search defaults (appraiser practice: 1 mile / 12 months in
#: a suburb, tighter in a city, wider in the country). The engine widens
#: automatically when too few sales qualify, and says so.
COMP_RADIUS_MILES = float(_env("ARV_COMP_RADIUS_MILES", "1.0"))
COMP_MONTHS = int(_env("ARV_COMP_MONTHS", "12"))
COMP_TARGET = int(_env("ARV_COMP_TARGET", "6"))
COMP_MIN = 3

#: Local cache for the public market files (Zillow ZHVI is ~120 MB; fetched at
#: most once a day and only when a ZIP is actually looked up).
CACHE_DIR = Path(_env("ARV_CACHE_DIR") or (PROJECT_ROOT / "raw_store" / "arv_cache"))
HTTP_TIMEOUT = float(_env("ARV_HTTP_TIMEOUT", "25"))
USER_AGENT = "MangoTree-ARV/1.0 (RKB Consulting Group; internal underwriting tool)"


def providers_status() -> dict:
    return {
        "census_geocoder": True,
        "web_search": True,
        "rentcast": bool(RENTCAST_API_KEY),
        "attom": bool(ATTOM_API_KEY),
        "realestateapi": bool(REALESTATEAPI_KEY),
        "housecanary": bool(HOUSECANARY_API_KEY and HOUSECANARY_API_SECRET),
        "google_maps": bool(GOOGLE_MAPS_API_KEY),
        "model": ARV_MODEL,
        "effort": ARV_EFFORT,
    }
