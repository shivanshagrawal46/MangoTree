"""Canonical normalization for entity resolution + address/parcel identity.

These functions were duplicated across ingest_titles_full.py, reparse_titles.py,
ingest_title_reports.py and build_entities_from_llc.py. This is now the single
source of truth; the scripts should import from here to prevent drift (e.g. the
'227 W Neck' vs '227 West Neck' bug that came from divergent addr_core copies).

Behaviour is preserved byte-for-byte from the production implementations.
"""
from __future__ import annotations

import re
from typing import Optional

# --------------------------------------------------------------------------
# Names
# --------------------------------------------------------------------------
_SUFFIX_RE = re.compile(
    r"\b(llc|l\s*l\s*c|inc|incorporated|corp|corporation|co|company|ltd)\b\.?",
    re.IGNORECASE,
)


def norm_name(s: str) -> str:
    """Uppercase, strip LLC/INC/PC corporate suffixes + punctuation."""
    s = (s or "").upper()
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\bL\.?L\.?C\.?\b|\bINC\b|\bP\.?C\.?\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_suffixes(name_norm: str) -> str:
    """Remove all corporate suffix tokens for fuzzy matching."""
    return re.sub(r"\s+", " ", _SUFFIX_RE.sub(" ", name_norm or "")).strip()


def slug(s: str) -> str:
    """Lowercase alnum -> underscore slug for deterministic entity _id."""
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return s or "x"


# --------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------
def norm_addr(s: str) -> str:
    """Uppercase address normalizer (entity-store style): UNIT/APT removed,
    street types abbreviated."""
    s = (s or "").upper()
    s = re.sub(r"\bUNIT\b|\bAPT\b|#|\bSTE\b|\bSUITE\b", " ", s)
    repl = {"STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
            "LANE": "LN", "COURT": "CT", "BOULEVARD": "BLVD", "PLACE": "PL"}
    for k, v in repl.items():
        s = re.sub(rf"\b{k}\b", v, s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_address(addr: Optional[str]) -> str:
    """Lowercase token normalizer (dedup-identity style)."""
    if not addr:
        return ""
    a = addr.lower()
    a = re.sub(r"['\u2019]", "", a)
    a = re.sub(r"[^a-z0-9]+", " ", a).strip()
    return a


_STREET_SFX = {"rd", "road", "dr", "drive", "st", "street", "ave", "avenue", "ln", "lane",
               "ct", "court", "blvd", "pkwy", "pl", "place", "way", "path", "cir", "ter",
               "tri", "trail"}
_DIR_MAP = {"w": "west", "e": "east", "n": "north", "s": "south",
            "nw": "northwest", "ne": "northeast", "sw": "southwest", "se": "southeast"}
_DIRECTIONALS = {"west", "east", "north", "south", "northwest", "northeast",
                 "southwest", "southeast"}


def addr_core(addr_norm: str) -> str:
    """Property identity key = house number + directionals (canonicalized,
    position-independent) + FIRST real street word. 'W'=='West'; city,
    street-type suffix, and 'NEW' never enter the key. Input must already be
    norm_address()-style lowercase tokens (or raw — we tolerate both)."""
    toks = [_DIR_MAP.get(t, t) for t in (addr_norm or "").split() if t]
    if not toks:
        return ""
    house = toks[0]
    dirs = sorted({t for t in toks[1:] if t in _DIRECTIONALS})
    street = next((t for t in toks[1:] if t not in _DIRECTIONALS and t not in _STREET_SFX), "")
    return " ".join([house] + dirs + ([street] if street else []))


def address_key(addr: Optional[str]) -> str:
    """Convenience: raw address -> canonical property key in one call."""
    return addr_core(norm_address(addr))


# --------------------------------------------------------------------------
# Parcels
# --------------------------------------------------------------------------
def parcel_digits(parcel: Optional[str]) -> str:
    """Digits-only parcel identity (APN punctuation/section variants collapse)."""
    return re.sub(r"\D", "", parcel or "")


def normalize_parcel(p: Optional[str]) -> str:
    """Whitespace-stripped uppercase parcel string."""
    return re.sub(r"\s+", " ", (p or "").upper()).strip()


# --------------------------------------------------------------------------
# David address-coded LLC pattern
# --------------------------------------------------------------------------
def llc_matches_address(owner_name: str, address: str) -> bool:
    """David's signature: an LLC named after its own property (house number +
    street-initial). '132W130 LLC' -> 132 West 130th; '9RO LLC' -> 9 Roda.
    Strict: a token must START with the exact house number, next letter must
    match the street's first letter (avoids 'RH PHILLIPS'/'JDK COVE')."""
    if not owner_name or not address:
        return False
    am = re.match(r"\s*(\d+)\s+([A-Za-z])", address.strip())
    if not am:
        return False
    house, street0 = am.group(1), am.group(2).lower()
    for tok in re.findall(r"[0-9A-Za-z]+", owner_name):
        tm = re.match(r"^(\d+)([A-Za-z])", tok)
        if tm and tm.group(1) == house and tm.group(2).lower() == street0:
            return True
    return False
