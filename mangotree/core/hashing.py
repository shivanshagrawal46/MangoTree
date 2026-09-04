"""Content addressing. Every original is identified by the SHA-256 of its bytes."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

_CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


_WS = re.compile(r"\s+")


def content_fingerprint(subject: str, body: str, sender: str) -> str:
    """Whitespace-insensitive fingerprint for third-way (content) dedup.

    Deliberately ignores formatting differences so the same message forwarded
    through two providers collapses even when one of them rewrapped the body.
    """
    norm = _WS.sub(" ", f"{sender}|{subject}|{body}".lower()).strip()
    return sha256_text(norm)
