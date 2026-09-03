"""Sprint 7 · 7.6 — robust numeric/date normalization for reconciliation.

Legal text states the same value many ways: '$1.45M', '1,450,000.00',
'$1,450,000', 'one million four hundred fifty thousand'. To compare amounts
across sources (contradiction detection, flow-of-funds, evidence), we normalize
to a canonical float + tolerant equality. Dates reuse src.detect.dates.parse_date.
"""
from __future__ import annotations

import re
from typing import List, Optional

from src.detect.dates import parse_date  # re-export-friendly

_MULT = {"k": 1_000, "m": 1_000_000, "mm": 1_000_000, "b": 1_000_000_000,
         "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_NUM_RE = re.compile(r"(\$\s*)?(\d[\d,]*(?:\.\d+)?)\s*(k|mm|m|b|thousand|million|billion)?", re.I)


def normalize_money(s) -> Optional[float]:
    """Parse the first monetary amount in a string to a float (applying K/M/B)."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    mo = _NUM_RE.search(str(s))
    if not mo:
        return None
    try:
        val = float(mo.group(2).replace(",", ""))
    except ValueError:
        return None
    mult = (mo.group(3) or "").lower()
    if mult in _MULT:
        val *= _MULT[mult]
    return val


def all_money(s) -> List[float]:
    out: List[float] = []
    for mo in _NUM_RE.finditer(str(s or "")):
        try:
            v = float(mo.group(2).replace(",", ""))
        except ValueError:
            continue
        m = (mo.group(3) or "").lower()
        if m in _MULT:
            v *= _MULT[m]
        out.append(v)
    return out


def money_matches(a, b, *, rel_tol: float = 0.01, abs_tol: float = 1.0) -> bool:
    """True if two amounts reconcile within tolerance (handles rounding/format)."""
    fa, fb = normalize_money(a), normalize_money(b)
    if fa is None or fb is None:
        return False
    return abs(fa - fb) <= max(abs_tol, rel_tol * max(abs(fa), abs(fb)))


def dates_match(a, b, *, days_tol: int = 0) -> bool:
    da, db = parse_date(str(a)) if a else None, parse_date(str(b)) if b else None
    if not da or not db:
        return False
    return abs((da - db).days) <= days_tol


def normalize_date_iso(s) -> Optional[str]:
    d = parse_date(str(s)) if s else None
    return d.strftime("%Y-%m-%d") if d else None
