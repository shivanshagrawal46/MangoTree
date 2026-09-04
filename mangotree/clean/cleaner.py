"""Email body cleaning. Originals are never mutated — this produces derived text.

Order matters:
  1. mojibake repair      (encoding damage, e.g. â€™ -> ')
  2. HTML -> text         (when the part is HTML)
  3. quoted-reply split   (new content vs the quoted thread, both retained)
  4. signature stripping  (trailing block, learned markers)
  5. whitespace normalise
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:  # optional but strongly recommended
    from ftfy import fix_text as _fix_text
except Exception:  # pragma: no cover
    _fix_text = None

try:
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None


# --------------------------------------------------------------------------
# 1. mojibake
# --------------------------------------------------------------------------
_MOJIBAKE_FALLBACK = {
    "â€™": "'", "â€˜": "'", "â€œ": '"', "â€\x9d": '"', "â€“": "-", "â€”": "—",
    "â€¦": "…", "Â ": " ", "Â": "", "ï»¿": "", "\u200b": "", "\ufeff": "",
}


def repair_mojibake(text: str) -> str:
    if not text:
        return ""
    if _fix_text is not None:
        try:
            return _fix_text(text)
        except Exception:  # pragma: no cover
            pass
    for bad, good in _MOJIBAKE_FALLBACK.items():
        text = text.replace(bad, good)
    return text


# --------------------------------------------------------------------------
# 2. html
# --------------------------------------------------------------------------
_TAG_BREAK = re.compile(r"(?i)<\s*(br|/p|/div|/tr|/li|/h[1-6])\s*/?>")
_TAG_ANY = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    if not html:
        return ""
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:  # pragma: no cover - parser missing
            soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "head", "meta", "title"]):
            tag.decompose()
        return soup.get_text("\n")
    text = _TAG_BREAK.sub("\n", html)
    return _TAG_ANY.sub(" ", text)


# --------------------------------------------------------------------------
# 3. quoted replies
# --------------------------------------------------------------------------
_QUOTE_MARKERS = [
    re.compile(r"^\s*On .{4,120}\bwrote:\s*$", re.I | re.M),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.I | re.M),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.I | re.M),
    re.compile(r"^\s*From:\s*.+\n\s*(Sent|Date):\s*.+$", re.I | re.M),
    re.compile(r"^\s*_{10,}\s*$", re.M),
    re.compile(r"^\s*Begin forwarded message:\s*$", re.I | re.M),
]


def split_quoted(text: str) -> Tuple[str, str]:
    """Return (new_content, quoted_thread). Quoted text is retained, not discarded."""
    if not text:
        return "", ""

    cut: Optional[int] = None
    for pattern in _QUOTE_MARKERS:
        match = pattern.search(text)
        if match and (cut is None or match.start() < cut):
            cut = match.start()

    # A run of '>' quoted lines also marks the boundary when it is not at the top.
    lines = text.split("\n")
    for idx, line in enumerate(lines):
        if line.lstrip().startswith(">") and idx > 0:
            offset = sum(len(l) + 1 for l in lines[:idx])
            if cut is None or offset < cut:
                cut = offset
            break

    if cut is None:
        return text.strip(), ""
    return text[:cut].strip(), text[cut:].strip()


# --------------------------------------------------------------------------
# 4. signatures
# --------------------------------------------------------------------------
_SIG_DELIM = re.compile(r"^\s*(--\s*|__+|—{2,})\s*$", re.M)
_SIG_HINTS = re.compile(
    r"(?i)^\s*(best regards|kind regards|regards|thanks(?: and regards)?|thank you|"
    r"sincerely|cheers|warm regards|sent from my (?:iphone|ipad|android|mobile))\b"
)
#: Whatever may legitimately trail a sign-off on the same line: punctuation only.
#: This is what separates the sign-off line "Thanks," from the sentence
#: "Thanks for the update." — stripping the latter would delete a real message.
_SIGNOFF_TAIL = re.compile(r"^[\s,.:;!—–-]*$")
_CONTACT_HINT = re.compile(
    r"(?i)(\b(?:tel|phone|mobile|cell|fax|direct|office)\b[:.\s]|"
    r"\b(?:www\.|https?://)|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b)"
)


def strip_signature(text: str) -> Tuple[str, str]:
    """Return (body_without_signature, signature). Conservative by design."""
    if not text:
        return "", ""

    match = _SIG_DELIM.search(text)
    if match:
        return text[: match.start()].strip(), text[match.start():].strip()

    lines = text.split("\n")
    # Look only at the tail — a sign-off in the middle is part of the message.
    window = min(len(lines), 12)
    for offset in range(max(0, len(lines) - window), len(lines)):
        line = lines[offset] or ""
        match = _SIG_HINTS.match(line)
        if not match:
            continue

        # The line must *be* the sign-off, not merely start with the word.
        # "Thanks," qualifies; "Thanks for the update." does not.
        if not _SIGNOFF_TAIL.match(line[match.end():]):
            continue

        # A sign-off at the very top is a greeting, not a signature.
        if not any(l.strip() for l in lines[:offset]):
            continue

        tail = "\n".join(lines[offset:])
        following = [l for l in lines[offset + 1:] if l.strip()]
        if _CONTACT_HINT.search(tail) or len(following) <= 5:
            return "\n".join(lines[:offset]).strip(), tail.strip()

    return text.strip(), ""


# --------------------------------------------------------------------------
# 5. whitespace
# --------------------------------------------------------------------------
_TRAILING_WS = re.compile(r"[ \t]+$", re.M)
_MANY_BLANKS = re.compile(r"\n{3,}")
_MANY_SPACES = re.compile(r"[ \t]{2,}")


def normalize_whitespace(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS.sub("", text)
    text = _MANY_SPACES.sub(" ", text)
    text = _MANY_BLANKS.sub("\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
@dataclass
class CleanedBody:
    body_clean: str
    quoted: str
    signature: str
    was_html: bool

    @property
    def full_text(self) -> str:
        """Everything, for retrieval — new content first, quoted context after."""
        parts = [p for p in (self.body_clean, self.quoted) if p]
        return "\n\n".join(parts)


def clean_body(raw_text: str = "", raw_html: str = "") -> CleanedBody:
    was_html = bool(raw_html and not raw_text)
    source = raw_text or ""
    if was_html:
        source = html_to_text(raw_html)

    source = repair_mojibake(source)
    new_content, quoted = split_quoted(source)
    body, signature = strip_signature(new_content)

    return CleanedBody(
        body_clean=normalize_whitespace(body),
        quoted=normalize_whitespace(quoted),
        signature=normalize_whitespace(signature),
        was_html=was_html,
    )
