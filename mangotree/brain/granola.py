"""Granola — Rakesh Sir's meeting notes, read only.

A personal API key (``GRANOLA_API_KEY`` in .env) gives every note he recorded:
title, Granola's own summary, and the transcript line by line with the time of
each line. Nothing is ever written to Granola.

What Granola knows about who spoke (checked on his notes, 2026-11-18):

* ``attribution: me``   — his microphone. Rakesh Sir, or anyone in the room with him.
* ``attribution: them`` — the other side; ``name`` is there only when the call ran
  in an app that names its speakers (Zoom, Meet). A phone call has no name.
* a note where every line is ``me`` is a call played on his speaker: both sides
  are recorded as one voice, so nobody can be named.

Only notes that already have Granola's summary are returned by the API, so a call
shows up a few minutes after it ends.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

BASE = "https://public-api.granola.ai/v1"
#: The folder Rakesh Sir drops a call into for it to be taken (user, 2026-11-18).
FOLDER_NAME = "RKB-ROI"
NOT_SEPARATED = "Voices not separated"
RAKESH = "Rakesh Sir"
UNNAMED = "Other person (not named)"


class GranolaError(RuntimeError):
    pass


def _when(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _same_name(a: str, b: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", a.lower()) == re.sub(r"[^a-z0-9]", "", b.lower())


class Granola:
    def __init__(self, key: Optional[str] = None, session=None):
        from mangotree.config import settings as _settings  # noqa: F401  (loads .env)
        self.key = key or os.environ.get("GRANOLA_API_KEY") or ""
        self.http = session or requests.Session()

    @property
    def ready(self) -> bool:
        return bool(self.key)

    def _get(self, path: str, **params) -> Dict[str, Any]:
        if not self.key:
            raise GranolaError("GRANOLA_API_KEY is not set")
        last = None
        for attempt in range(5):
            r = self.http.get(BASE + path, headers={"Authorization": "Bearer " + self.key},
                              params={k: v for k, v in params.items() if v is not None}, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                last = r
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise GranolaError(f"Granola {path}: {r.status_code} {r.text[:200]}")
            return r.json()
        raise GranolaError(f"Granola {path}: {getattr(last, 'status_code', '?')} after 5 tries")

    def _pages(self, path: str, key: str, **params) -> Iterable[Dict[str, Any]]:
        cursor = None
        while True:
            d = self._get(path, cursor=cursor, **params)
            for row in d.get(key) or []:
                yield row
            cursor = d.get("cursor")
            if not d.get("hasMore") or not cursor:
                return

    # ------------------------------------------------------------------ reads
    def folders(self) -> List[Dict[str, Any]]:
        return list(self._pages("/folders", "folders"))

    def folder_id(self, name: str = FOLDER_NAME) -> Optional[str]:
        for f in self.folders():
            if _same_name(f.get("name") or "", name):
                return f.get("id")
        return None

    def notes(self, *, created_after: Any = None, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Note headers (id, title, created_at), oldest first."""
        after = created_after.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(created_after, datetime) else created_after
        rows = list(self._pages("/notes", "notes", created_after=after, folder_id=folder_id, page_size=30))
        return sorted(rows, key=lambda n: n.get("created_at") or "")

    def note(self, note_id: str) -> Dict[str, Any]:
        """One note with its transcript."""
        try:
            return self._get(f"/notes/{note_id}", include="transcript")
        except GranolaError as exc:
            if "413" not in str(exc):
                raise
        d = self._get(f"/notes/{note_id}")
        d["transcript"] = list(self._pages(f"/notes/{note_id}/transcript", "transcript"))
        return d


# ------------------------------------------------------------- the transcript
def speakers(note: Dict[str, Any]) -> Dict[str, Any]:
    """Who Granola heard: names, how many lines each, and whether the voices are separated at all."""
    lines = note.get("transcript") or []
    separated = any(((t.get("speaker") or {}).get("attribution") == "them") for t in lines)
    count: Dict[str, int] = {}
    for t in lines:
        who = speaker_of(t, separated)
        count[who] = count.get(who, 0) + 1
    return {"separated": separated, "lines": count,
            "named": sorted({(t.get("speaker") or {}).get("name") for t in lines if (t.get("speaker") or {}).get("name")})}


def speaker_of(line: Dict[str, Any], separated: bool) -> str:
    s = line.get("speaker") or {}
    if not separated:
        return NOT_SEPARATED
    if s.get("attribution") == "me" or s.get("source") == "microphone":
        return RAKESH
    return str(s.get("name") or "").strip() or UNNAMED


#: Words that mean the digits near them are a secret: a payment card, a bank account, an identity number.
_SECRET_WORDS = re.compile(r"card number|credit card|debit card|security code|\bcvv\b|\bcvc\b|routing number|account number|social security|\bssn\b|"
                           r"कार्ड नंबर|सिक्योरिटी कोड|अकाउंट नंबर", re.I)
_LONG_NUMBER = re.compile(r"(?<![\d$.,])(?:\d[ \-]?){12,19}(?![\d.,]*\d)")
_DIGITS = re.compile(r"(?<![$\d.,])\d{2,}(?:[ \-/]\d{1,})*(?![\d]*[.,]\d)")
REMOVED = "[number removed]"


def scrub(rows: List[Dict[str, Any]]) -> int:
    """Take payment-card, bank-account and identity numbers out of a transcript before it is kept.
    People read these aloud on calls (paying a supplier by card). A run of twelve or more digits is
    always removed. Where a turn talks about a card, a security code, an expiry, an account or an
    identity number, every bare number of two or more digits in that turn and the next two is
    removed as well — money amounts (with a $ or a decimal point) are left. Returns how many were removed."""
    n, hot = 0, 0
    for t in rows:
        words, k = _LONG_NUMBER.subn(REMOVED + " ", t["words"])
        n += k
        if _SECRET_WORDS.search(words):
            hot = 3
        if hot:
            words, k = _DIGITS.subn(REMOVED, words)
            n += k
            hot -= 1
        t["words"] = words
    return n


def turns(note: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The transcript as turns: consecutive lines of one speaker joined, each with the time it began (UTC)."""
    lines = note.get("transcript") or []
    separated = any(((t.get("speaker") or {}).get("attribution") == "them") for t in lines)
    out: List[Dict[str, Any]] = []
    for t in lines:
        words = str(t.get("text") or "").strip()
        if not words:
            continue
        who = speaker_of(t, separated)
        at = _when(t.get("start_time"))
        if out and out[-1]["who"] == who and len(out[-1]["words"]) < 1200:
            out[-1]["words"] += " " + words
        else:
            out.append({"at": at, "who": who, "words": words})
    scrub(out)
    return out


def times(note: Dict[str, Any]) -> Dict[str, Optional[datetime]]:
    """When the call really ran: first and last spoken line; else the calendar slot; else when the note was made."""
    lines = note.get("transcript") or []
    start = _when(lines[0].get("start_time")) if lines else None
    end = _when(lines[-1].get("end_time")) if lines else None
    cal = note.get("calendar_event") or {}
    return {"start": start or _when(cal.get("scheduled_start_time")) or _when(note.get("created_at")),
            "end": end or _when(cal.get("scheduled_end_time"))}
