"""
Header decoding + mojibake detection (Sprint 2 — encoding correctness).

Two independent, pure utilities:

1. `decode_mime_header` — RFC 2047 encoded-word decoding. The .eml and
   Gmail ingestion paths currently store Subject / display names RAW, so
   court exports show titles like
       =?utf-8?B?UkU6IDMxIEZvcnQgSGlsbCBEcml2ZS...?=
   and BM25 over subjects is degraded. This decodes them. It is
   IDEMPOTENT: a already-plain header is returned unchanged, so wiring it
   into ingestion is behaviour-preserving for clean headers.

2. `looks_like_mojibake` / `try_fix_mojibake` — detect the UTF-16-LE-as-
   UTF-8 → CJK garbage class that bit the PST path (8 emails), so it can
   be caught at parse time instead of reactively. Mirrors the heuristic in
   the one-off `_scan_mojibake.py`.

No DB, no network. Fully unit-testable.
"""
from __future__ import annotations

import re
from email.header import decode_header, make_header
from typing import Optional

try:
    import ftfy  # optional; improves the fix step
    _HAS_FTFY = True
except ImportError:  # pragma: no cover
    _HAS_FTFY = False


# ---------------------------------------------------------------------------
# RFC 2047 header decoding
# ---------------------------------------------------------------------------
_ENCODED_WORD = re.compile(r"=\?[^?]+\?[bBqQ]\?[^?]*\?=")


def is_encoded_word(value: str) -> bool:
    """True if the string contains at least one RFC 2047 encoded-word."""
    return bool(value) and _ENCODED_WORD.search(value) is not None


def decode_mime_header(value: Optional[str]) -> str:
    """Decode RFC 2047 encoded-words to Unicode. Idempotent for plain text.

    Handles multi-part encoded headers ("=?utf-8?B?..?= =?utf-8?Q?..?=")
    and malformed inputs gracefully (returns best-effort, never raises)."""
    if not value:
        return ""
    if not is_encoded_word(value):
        return value
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — never let a bad header crash ingest
        # Best-effort per-fragment decode.
        out = []
        for part, enc in decode_header(value):
            if isinstance(part, bytes):
                try:
                    out.append(part.decode(enc or "utf-8", errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    out.append(part.decode("utf-8", errors="replace"))
            else:
                out.append(part)
        return "".join(out)


# ---------------------------------------------------------------------------
# Mojibake detection (UTF-16-LE decoded as UTF-8/cp1252 -> CJK garbage)
# ---------------------------------------------------------------------------
_CJK = re.compile(
    r"[\u3000-\u9fff\uac00-\ud7af\uff00-\uffef]"  # CJK/Hangul/fullwidth
)


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(_CJK.findall(text))
    return cjk / max(1, len(text))


def looks_like_mojibake(text: str, *, threshold: float = 0.30) -> bool:
    """Heuristic: a Latin-script legal email should have ~0 CJK characters.
    A high CJK ratio signals a byte-swapped/mis-decoded body. We also
    confirm the garbage 'un-decodes' back toward markup, mirroring the
    production scanner, to avoid flagging genuine CJK text."""
    if cjk_ratio(text) < threshold:
        return False
    # Confirmation: if re-encoding the suspected garbage as UTF-16-LE and
    # reading it as cp1252 recovers ASCII-ish markup, it's mojibake.
    try:
        recovered = text.encode("utf-16-le", errors="ignore").decode("cp1252", errors="ignore")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return False
    ascii_ratio = sum(1 for c in recovered if 32 <= ord(c) < 127) / max(1, len(recovered))
    return ascii_ratio > 0.6


def try_fix_mojibake(text: str) -> str:
    """Attempt to recover a mojibake body. Prefers ftfy when available;
    falls back to the utf-16-le/cp1252 round-trip. Returns the original
    text unchanged if it doesn't look like mojibake or recovery fails."""
    if not text or not looks_like_mojibake(text):
        return text
    if _HAS_FTFY:
        fixed = ftfy.fix_text(text)
        if cjk_ratio(fixed) < 0.05:
            return fixed
    try:
        recovered = text.encode("utf-16-le", errors="ignore").decode("cp1252", errors="ignore")
        if cjk_ratio(recovered) < 0.05:
            return recovered
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return text


__all__ = [
    "is_encoded_word", "decode_mime_header",
    "cjk_ratio", "looks_like_mojibake", "try_fix_mojibake",
]
