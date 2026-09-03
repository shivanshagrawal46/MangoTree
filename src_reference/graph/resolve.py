"""Entity resolution — split combined multi-party owner entities into clean
canonical components, and merge exact duplicates.

Why: title reports list owners as free text ("A & B", "X (1%) AND Y (99%)",
"H AND W, HUSBAND AND WIFE", OCR-doubled names). Ingestion created ONE entity
per raw string, so David's interest can hide inside a combined node and a
property can look co-owned by a phantom. This module:

  1. Strips legal qualifiers (fractions, tenancy, spousal, heir/administrator).
  2. Splits on connectors (& / and) into component party names.
  3. Resolves each component to an EXISTING canonical entity (exact norm_name,
     then suffix-stripped, then fuzzy>=92) or creates a clean one.
  4. Classifies side (address-coded / known-David -> david_network).
  5. Returns a plan; the runner re-points doc owners + OWNS edges to all
     components and retires the combined node (idempotent, dry-run-able).

Conservative: a component that cannot be cleanly parsed is left for human
review rather than guessed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.graph.normalize import norm_name, strip_suffixes, slug, llc_matches_address

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    fuzz = None

# Qualifier/role phrases stripped BEFORE splitting. Substituted with a SPACE
# (not empty) so we never fuse two adjacent name tokens. Tenancy/survivorship
# clauses stop BEFORE a connector so a trailing co-owner is preserved.
_PCT = r"\d+\s*%?"
_QUALIFIER_RES = [
    re.compile(r",?\s*as\s+to\s+(a\s+)?" + _PCT + r"\s*(inte\w*rest)?", re.I),  # AS TO 99% (INTE(TE)REST)
    re.compile(r"\(\s*\d+\s*%\s*\)", re.I),                                       # (99%)
    re.compile(r",?\s*as\s+(joint\s+tenants|tenants\s+in\s+common)\b"
               r"(\s+with\s+(the\s+)?rights?\s+(to|of)\s+survivorship)?", re.I),
    re.compile(r"\bwith\s+(the\s+)?rights?\s+(to|of)\s+survivorship\b", re.I),
    re.compile(r",?\s*(as\s+)?husband\s+and\s+wife\b", re.I),
    re.compile(r",?\s*his\s+wife\b", re.I),
    re.compile(r",?\s*her\s+husband\b", re.I),
    re.compile(r"\bn/?k/?a\b.*$", re.I),            # "now known as"
    re.compile(r"\bf/?k/?a\b.*$", re.I),            # "formerly known as"
    re.compile(r",?\s*heir(-|\s)?(at(-|\s)?law)?\s+to\s+the\s+e\w*\b.*$", re.I),
    re.compile(r",?\s*as\s+administrator\b.*$", re.I),
    re.compile(r"\binte\w*rest\b", re.I),           # stray OCR "interest"/"inteterest"
]
_CONNECTOR_RE = re.compile(r"\s+(?:&|and)\s+", re.I)
_ROLE_ONLY = {"husband", "wife", "his wife", "her husband", "heir", "associates",
              "associate", "assoc", "estate", "sons", "co", "company", "interest"}
# Company-name connectors: '&'/'and' followed by these are part of ONE firm name,
# not a co-owner separator (e.g. 'Island Properties & Associates').
_COMPANY_TAIL_RE = re.compile(r"\s+(?:&|and)\s+(associates?|assoc|sons|co|company)\b", re.I)
_AMP = "\x00AMP\x00"


def _clean_part(p: str) -> str:
    p = p.strip().strip(",").strip()
    p = re.sub(r",?\s*(husband|wife)\s*$", "", p, flags=re.I).strip().strip(",").strip()
    return p


def split_owner_string(raw: str) -> List[str]:
    """Split a raw combined owner string into component party names.
    Single-element result = dedup/merge target (e.g. OCR-doubled firm name)."""
    if not raw:
        return []
    s = raw
    # protect company-name ampersands so '& Associates' is not a co-owner split
    s = _COMPANY_TAIL_RE.sub(lambda mo: f" {_AMP} {mo.group(1)}", s)
    for rx in _QUALIFIER_RES:
        s = rx.sub(" ", s)
    s = re.sub(r"\s{2,}", " ", s).strip().strip(",").strip()
    parts = [_clean_part(p).replace(_AMP, "&") for p in _CONNECTOR_RE.split(s)]
    out: List[str] = []
    seen = set()
    for p in parts:
        p = re.sub(r"\s{2,}", " ", p).strip()
        if not p or len(p) < 3 or p.lower() in _ROLE_ONLY:
            continue
        key = norm_name(p)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def has_internal_repeat(name: str) -> bool:
    """Detect OCR-doubled names ('ISLAND PROPERTY & ASSOCIATES ISLAND
    PROPERTIES & ASSOCIATES') — a significant (>=4 char) token appears 2+ times.
    Such strings are ambiguous and routed to human review, not auto-created."""
    toks = [t for t in norm_name(name).split() if len(t) >= 4]
    seen = set()
    for t in toks:
        if t in seen:
            return True
        seen.add(t)
    return False


def is_junk_component(name: str) -> bool:
    """A component we should NOT turn into an entity (leftover qualifier word,
    pure suffix, or empty after normalization)."""
    n = norm_name(name)
    if not n or len(n) < 3:
        return True
    if n.lower() in _ROLE_ONLY:
        return True
    if re.fullmatch(r"(LLC|INC|CORP|CO|ESTATE|ASSOCIATES?|INTEREST)\s*", n + " ", re.I):
        return True
    return False


def classify_side(name: str, address: str = "", known_david: Optional[set] = None) -> Dict[str, Any]:
    n = norm_name(name)
    is_david = False
    reason = None
    if known_david and n in known_david:
        is_david, reason = True, "known_david"
    elif llc_matches_address(name, address):
        is_david, reason = True, "address_coded_llc"
    elif re.search(r"\bIPA\b|ISLAND PROPERT|ISLAND PROPERTY", name.upper()):
        is_david, reason = True, "ipa_island_network"
    return {"is_david": is_david, "side": "david_network" if is_david else None,
            "david_flag_reason": reason}


def resolve_component(ents, name: str, address: str = "",
                      known_david: Optional[set] = None) -> Dict[str, Any]:
    """Resolve one component name to an existing canonical entity or describe a
    new one. Returns {entity_id, kind, is_david, side, created, matched_on}."""
    n = norm_name(name)
    kind = "llc" if re.search(r"\bLLC\b|L\.?L\.?C", name.upper()) else "person"
    # 1) exact name_norm
    found = ents.find_one({"name_norm": n, "kind": {"$in": ["llc", "person", "org"]},
                           "$or": [{"needs_split": {"$ne": True}}, {"needs_split": {"$exists": False}}]},
                          {"_id": 1, "is_david": 1, "side": 1, "kind": 1})
    if found:
        return {"entity_id": found["_id"], "kind": found.get("kind", kind),
                "is_david": bool(found.get("is_david")), "side": found.get("side"),
                "created": False, "matched_on": "name_norm"}
    # 2) suffix-stripped fuzzy >=92
    stripped = strip_suffixes(n)
    if stripped and fuzz is not None:
        best, best_sc = None, 0.0
        for e in ents.find({"kind": {"$in": ["llc", "person", "org"]}},
                           {"_id": 1, "name_norm": 1, "is_david": 1, "side": 1, "kind": 1,
                            "needs_split": 1}):
            if e.get("needs_split"):
                continue
            cand = strip_suffixes(e.get("name_norm") or "")
            if not cand:
                continue
            sc = 100.0 if cand == stripped else fuzz.ratio(stripped, cand)
            if sc > best_sc:
                best, best_sc = e, sc
        if best is not None and best_sc >= 92.0:
            return {"entity_id": best["_id"], "kind": best.get("kind", kind),
                    "is_david": bool(best.get("is_david")), "side": best.get("side"),
                    "created": False, "matched_on": f"fuzzy:{best_sc:.0f}"}
    # 3) new clean entity
    cls = classify_side(name, address, known_david)
    eid = ("ent_llc_" if kind == "llc" else "ent_per_") + slug(name)
    return {"entity_id": eid, "kind": kind, "is_david": cls["is_david"],
            "side": cls["side"], "david_flag_reason": cls.get("david_flag_reason"),
            "canonical_name": name, "name_norm": n, "created": True, "matched_on": "new"}
