"""Tolerant legal date parsing for grounded-fact strings.

Title text dates come as 'March 5, 2021', '3/5/2021', '05/03/2021',
'recorded 4/1/2022', etc. We extract a confident date or None. Year sanity
1900-2030 to reject page numbers / instrument numbers misread as dates.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

try:
    from dateutil import parser as _dup
except Exception:  # noqa: BLE001
    _dup = None

_MONTHS = ("january|february|march|april|may|june|july|august|september|"
           "october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_RE_TEXT = re.compile(rf"\b({_MONTHS})\.?\s+\d{{1,2}},?\s+(19|20)\d{{2}}", re.I)
_RE_SLASH = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-](19|20)?\d{2}\b")
_RE_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _sane(d: Optional[datetime]) -> Optional[datetime]:
    if d is None:
        return None
    if 1900 <= d.year <= 2030:
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    return None


def parse_date(s: str) -> Optional[datetime]:
    """Best-effort single date from a free-text string."""
    if not s:
        return None
    s = str(s).strip()
    for rx in (_RE_TEXT, _RE_SLASH):
        mo = rx.search(s)
        if mo and _dup is not None:
            try:
                return _sane(_dup.parse(mo.group(0), fuzzy=True, default=datetime(2000, 1, 1)))
            except Exception:  # noqa: BLE001
                pass
    if _dup is not None:
        try:
            return _sane(_dup.parse(s, fuzzy=True, default=datetime(2000, 1, 1)))
        except Exception:  # noqa: BLE001
            pass
    mo = _RE_YEAR.search(s)
    if mo:
        try:
            return _sane(datetime(int(mo.group(0)), 1, 1))
        except Exception:  # noqa: BLE001
            return None
    return None
