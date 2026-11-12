"""US Eastern time, for anything a contractor or the accountant reads.

RKB's contractors and its Buildertrend account work on US Eastern time, so bill
notices give every date and time in Eastern — labelled EST in winter and EDT in
summer, because a time labelled "EST" in July would be an hour wrong.

Two different things arrive here:

* A moment the system recorded itself (a tick on the Bills page). Those are
  stored in UTC; ``to_et`` converts them.
* A date and time read out of Buildertrend (a bill's date). Buildertrend gives
  these as the builder's own wall-clock time with no zone attached (its fields
  that ARE in UTC say so in their name, e.g. ``commentDateUtc``), and the loader
  stored them as they came. ``wall_clock_et`` reads the stored value as Eastern
  wall-clock time rather than shifting it by five hours.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                      # no time-zone data on this machine: the US rule, by hand
    _ET = None


def _dst_bounds(year: int):
    """US daylight time: second Sunday of March 2:00 to first Sunday of November 2:00 (local)."""
    march = datetime(year, 3, 8)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    nov = datetime(year, 11, 1)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return start.replace(hour=2), end.replace(hour=2)


def _offset_for_wall(naive: datetime) -> timedelta:
    start, end = _dst_bounds(naive.year)
    return timedelta(hours=-4) if start <= naive < end else timedelta(hours=-5)


def to_et(moment: Optional[datetime]) -> Optional[datetime]:
    """A recorded moment (UTC, or naive meaning UTC) as Eastern time."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if _ET is not None:
        return moment.astimezone(_ET)
    utc = moment.astimezone(timezone.utc).replace(tzinfo=None)
    off = _offset_for_wall(utc + timedelta(hours=-5))
    return (utc + off).replace(tzinfo=timezone(off, "EDT" if off == timedelta(hours=-4) else "EST"))


def wall_clock_et(stored: Optional[datetime]) -> Optional[datetime]:
    """A Buildertrend date-time, stored as it came, read as Eastern wall-clock time."""
    if stored is None:
        return None
    naive = stored.replace(tzinfo=None)
    if _ET is not None:
        return naive.replace(tzinfo=_ET)
    off = _offset_for_wall(naive)
    return naive.replace(tzinfo=timezone(off, "EDT" if off == timedelta(hours=-4) else "EST"))


def label(et: Optional[datetime], *, time: bool = True) -> str:
    """"11 Nov 2026, 6:15 PM EST" — or the date alone."""
    if et is None:
        return "not recorded"
    day = f"{et.day} {et:%b %Y}"
    if not time:
        return day
    hour = et.hour % 12 or 12
    return f"{day}, {hour}:{et:%M} {'AM' if et.hour < 12 else 'PM'} {et.tzname() or 'ET'}"


def took(start: Optional[datetime], end: Optional[datetime]) -> str:
    """"2 days 5 hours", "5 hours 20 minutes", "12 minutes" — the time between two moments."""
    if start is None or end is None:
        return "not known"
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        return "not known (the paid time is before the bill date)"
    minutes, hours, days = seconds // 60, seconds // 3600, seconds // 86400

    def n(k: int, word: str) -> str:
        return f"{k} {word}{'' if k == 1 else 's'}"
    if days >= 1:
        return n(days, "day") + (f" {n(hours % 24, 'hour')}" if hours % 24 else "")
    if hours >= 1:
        return n(hours, "hour") + (f" {n(minutes % 60, 'minute')}" if minutes % 60 else "")
    return n(max(1, minutes), "minute")
