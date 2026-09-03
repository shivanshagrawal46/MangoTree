"""Hashing helpers used for deduplication and audit."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

_CHUNK = 1024 * 1024  # 1 MB


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_strings(parts: Iterable[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()
