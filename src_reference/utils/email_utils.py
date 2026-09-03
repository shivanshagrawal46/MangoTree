"""Helpers for parsing email addresses and headers."""
from __future__ import annotations

import re
from email.utils import getaddresses, parseaddr
from typing import Iterable

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def parse_address(raw: str | None) -> dict | None:
    """Parse a single 'Display Name <email@x.com>' style string."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    name, email = parseaddr(raw)
    name = (name or "").strip().strip('"').strip("'").strip()
    email = (email or "").strip().lower()
    if not email and not name:
        return None
    if not email:
        match = _EMAIL_RE.search(raw)
        if match:
            email = match.group(0).lower()
    if not email and name:
        return {"name": name, "email": ""}
    return {"name": name, "email": email}


def parse_address_list(raw: str | None) -> list[dict]:
    """Parse 'A <a@x.com>, B <b@x.com>; C <c@x.com>' into structured list."""
    if not raw:
        return []
    raw = raw.replace(";", ",")
    pairs = getaddresses([raw])
    out: list[dict] = []
    seen: set[str] = set()
    for name, email in pairs:
        name = (name or "").strip().strip('"').strip("'").strip()
        email = (email or "").strip().lower()
        if not email and not name:
            continue
        key = f"{name}|{email}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "email": email})
    return out


def extract_emails_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    return list({m.group(0).lower() for m in _EMAIL_RE.finditer(text)})


def domain_of(email: str) -> str:
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].lower()


def first_or_none(items: Iterable[dict]) -> dict | None:
    for item in items:
        return item
    return None
