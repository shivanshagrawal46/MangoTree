"""
Deadline radar (Sprint 5 — proactive insight).

Extracts dates-with-consequence from corpus text into a structured list so
a nightly job can surface "N days out" warnings without being asked. A
plain date ("we met on June 3") is ignored; a date tied to a consequence
("forecloses on July 7", "note matures July 15", "hearing scheduled for the
9th") is a deadline.

Pure logic: text in -> structured deadlines out. No DB/API. The nightly
job supplies `today` and persists results to a `deadlines` collection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

# Consequence keywords — the presence of one of these near a date is what
# promotes it from a mention to a deadline.
_CONSEQUENCE = re.compile(
    r"\b("
    r"foreclos\w*|hearing|matur\w*|deadline|due|expir\w*|closing|"
    r"sale|auction|takeover|take\s+control|file[d]?\s+by|filing|"
    r"payable|payment\s+due|adjourn\w*|so-?ordered|effective|"
    r"time[-\s]of[-\s]the[-\s]essence|toe\b|terminat\w*|forfeit\w*"
    r")\b",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "July 7, 2026" / "Jul 7 2026" / "7 July 2026"
_DATE_MDY = re.compile(
    r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_DATE_DMY = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|[\n\r]+")


@dataclass(frozen=True)
class Deadline:
    when: date
    consequence: str          # the keyword that triggered it
    sentence: str             # the surrounding sentence (context)
    days_out: Optional[int] = None   # relative to `today`, if provided

    def as_dict(self) -> dict:
        return {
            "when": self.when.isoformat(),
            "consequence": self.consequence,
            "sentence": self.sentence[:240],
            "days_out": self.days_out,
        }


def _parse_dates(text: str) -> List[date]:
    out: List[date] = []
    for m in _DATE_MDY.finditer(text):
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            try:
                out.append(date(int(m.group(3)), mon, int(m.group(2))))
            except ValueError:
                pass
    for m in _DATE_DMY.finditer(text):
        mon = _MONTHS.get(m.group(2).lower())
        if mon:
            try:
                out.append(date(int(m.group(3)), mon, int(m.group(1))))
            except ValueError:
                pass
    for m in _DATE_ISO.finditer(text):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in _DATE_SLASH.finditer(text):
        try:
            out.append(date(int(m.group(3)), int(m.group(1)), int(m.group(2))))
        except ValueError:
            pass
    return out


def extract_deadlines(
    text: str,
    *,
    today: Optional[date] = None,
) -> List[Deadline]:
    """Extract dates-with-consequence. Only dates in a sentence that also
    contains a consequence keyword are returned."""
    if not text:
        return []
    if isinstance(today, datetime):
        today = today.date()
    found: List[Deadline] = []
    seen: set = set()
    for sentence in _SENTENCE_SPLIT.split(text):
        cm = _CONSEQUENCE.search(sentence)
        if not cm:
            continue
        for d in _parse_dates(sentence):
            key = (d, cm.group(0).lower())
            if key in seen:
                continue
            seen.add(key)
            days_out = (d - today).days if today else None
            found.append(Deadline(
                when=d,
                consequence=cm.group(0).lower(),
                sentence=sentence.strip(),
                days_out=days_out,
            ))
    found.sort(key=lambda x: x.when)
    return found


def upcoming(deadlines: List[Deadline], *, within_days: int = 14) -> List[Deadline]:
    """Filter to deadlines that are in the future and within N days."""
    return [d for d in deadlines
            if d.days_out is not None and 0 <= d.days_out <= within_days]


__all__ = ["Deadline", "extract_deadlines", "upcoming"]
