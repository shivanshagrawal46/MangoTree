"""The firm's clock.

RKB works on US Eastern time; the server's clock is UTC. Every "what time is
it for the business" question — when the brief is due, which day an agenda
belongs to, whether it is 2 a.m. for the nightly sweep — goes through here, so
the schedule reads as the reader experiences it (admin directive 2026-09-07).
Stored timestamps stay UTC; only scheduling and day keys use this zone.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone, tzinfo

_NAME = os.environ.get("MT_TIMEZONE", "America/New_York")

try:
    from zoneinfo import ZoneInfo
    BUSINESS_TZ: tzinfo = ZoneInfo(_NAME)
except Exception:  # no tz database (bare Windows without tzdata): fixed EST as a last resort
    BUSINESS_TZ = timezone(timedelta(hours=-5), "EST")


def now_local() -> datetime:
    return datetime.now(BUSINESS_TZ)


def today_key() -> str:
    """The business date, YYYY-MM-DD, for per-day records (briefs, agendas, locks)."""
    return now_local().strftime("%Y-%m-%d")


def tz_label() -> str:
    return now_local().strftime("%Z") or _NAME
