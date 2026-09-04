"""RFC822 parsing — one parser for Gmail, .eml files and (later) Outlook.

Using a single parser for every source is deliberate: it guarantees a message
ingested from Gmail and the same message ingested from a ``.eml`` file produce
byte-identical derived text, so dedup and comparison stay meaningful.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from mangotree.core.hashing import sha256_bytes

#: Inline images below this size are almost certainly signature logos or
#: tracking pixels — they must never enter the photo pipeline.
LOGO_MAX_BYTES = 25 * 1024

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".tiff"}


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str
    size: int
    sha256: str
    data: bytes = field(repr=False, default=b"")
    is_inline: bool = False
    content_id: Optional[str] = None
    likely_logo: bool = False


@dataclass
class ParsedEmail:
    headers: Dict[str, str]
    subject: str
    date: Optional[datetime]
    body_text: str
    body_html: str
    attachments: List[ParsedAttachment]
    message_id: Optional[str]
    in_reply_to: List[str]
    references: List[str]
    raw_sha256: str
    raw_size: int

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


#: Charset labels that appear in real mail but are not registered under that
#: name in Python's codec table. ``windows-874`` (Thai) is the one that actually
#: reached us; the rest are its immediate neighbours and are listed so the same
#: class of message never costs a second investigation.
_CHARSET_ALIASES = {
    "windows-874": "cp874",
    "windows-1250": "cp1250",
    "windows-1251": "cp1251",
    "windows-1252": "cp1252",
    "windows-1253": "cp1253",
    "windows-1254": "cp1254",
    "windows-1255": "cp1255",
    "windows-1256": "cp1256",
    "windows-1257": "cp1257",
    "windows-1258": "cp1258",
    "iso-8859-8-i": "iso-8859-8",
    "unicode-1-1-utf-7": "utf-7",
    "cp-850": "cp850",
    "ansi_x3.110-1983": "latin-1",
    "unknown-8bit": "latin-1",
    "x-unknown": "latin-1",
    "none": "utf-8",
}


def _decode_bytes(payload: bytes, charset: Optional[str]) -> str:
    """Decode to text without ever raising.

    A charset we cannot honour is a reason to read the message imperfectly, not
    a reason to drop it: the body still carries the property names, amounts and
    dates the pipeline exists to capture. Latin-1 is the final fallback because
    it maps every byte to some character and therefore cannot fail.
    """
    label = (charset or "utf-8").strip().strip('"\'').lower()
    for candidate in (_CHARSET_ALIASES.get(label, label), "utf-8", "latin-1"):
        try:
            return payload.decode(candidate, errors="replace")
        except (LookupError, UnicodeError, TypeError):
            continue
    return payload.decode("latin-1", errors="replace")


def _decode_part(part: EmailMessage) -> str:
    charset = None
    try:
        charset = part.get_content_charset()
    except Exception:
        pass

    try:
        payload = part.get_content()
        if isinstance(payload, bytes):
            return _decode_bytes(payload, charset)
        return str(payload or "")
    except Exception:
        # get_content() applies the charset itself and raises on an unknown one,
        # so the retry must decode the bytes by hand rather than repeat the call.
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            return ""
        if isinstance(payload, bytes):
            return _decode_bytes(payload, charset)
        return str(payload or "")


def _parse_date(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe(getter, default=""):
    """Call a header accessor that may raise on a malformed value."""
    try:
        return getter() or default
    except Exception:
        return default


def _raw_header_text(raw_value: Any) -> str:
    """Best-effort text for a header the strict parser refused.

    Falls back to RFC 2047 decoding so a header that merely failed structural
    validation (a malformed Date, say) still yields readable text.
    """
    try:
        import email.header

        decoded = email.header.decode_header(str(raw_value))
        parts = []
        for chunk, charset in decoded:
            if isinstance(chunk, bytes):
                parts.append(_decode_bytes(chunk, charset))
            else:
                parts.append(str(chunk))
        return "".join(parts)
    except Exception:
        try:
            return str(raw_value)
        except Exception:
            return ""


def _read_headers(message: EmailMessage) -> Dict[str, str]:
    """Read every header, refusing to let one bad header lose the message.

    ``message.items()`` parses headers through the strict policy, and a single
    unparseable value takes the whole call down with it — a malformed ``Date``
    raises TypeError deep inside ``parsedate_to_datetime``. Parsing each header
    on its own keeps the blast radius at one header: the good ones still get
    full RFC 2047 decoding, and the bad one degrades to raw text.
    """
    headers: Dict[str, str] = {}
    try:
        raw_items = list(message.raw_items())
    except Exception:
        raw_items = []

    for key, raw_value in raw_items:
        key_lc = key.lower()
        try:
            text = str(message.policy.header_fetch_parse(key, raw_value))
        except Exception:
            text = _raw_header_text(raw_value)
        # Preserve repeated headers (Received, References) by joining.
        headers[key_lc] = f"{headers[key_lc]}\n{text}" if key_lc in headers else text
    return headers


def _looks_like_logo(filename: str, content_type: str, size: int, is_inline: bool) -> bool:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    is_image = content_type.startswith("image/") or ext in _IMAGE_EXT
    if not is_image:
        return False
    if size <= LOGO_MAX_BYTES:
        return True
    # Inline images referenced by a signature block are usually small; anything
    # large enough to be a real site photo is kept even when inline.
    return is_inline and size <= LOGO_MAX_BYTES * 4


def parse_rfc822(raw_bytes: bytes) -> ParsedEmail:
    message: EmailMessage = email.message_from_bytes(
        raw_bytes, policy=email.policy.default
    )

    headers = _read_headers(message)

    body_text_parts: List[str] = []
    body_html_parts: List[str] = []
    attachments: List[ParsedAttachment] = []

    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            content_type = _safe(part.get_content_type).lower()
            disposition = _safe(part.get_content_disposition).lower()
            filename = _safe(part.get_filename)

            if disposition == "attachment" or (filename and content_type != "text/plain"):
                payload = part.get_payload(decode=True) or b""
                is_inline = disposition == "inline"
                attachments.append(
                    ParsedAttachment(
                        filename=filename or f"unnamed.{content_type.split('/')[-1] or 'bin'}",
                        content_type=content_type,
                        size=len(payload),
                        sha256=sha256_bytes(payload),
                        data=payload,
                        is_inline=is_inline,
                        content_id=(part.get("Content-ID") or "").strip("<>") or None,
                        likely_logo=_looks_like_logo(filename, content_type, len(payload), is_inline),
                    )
                )
            elif content_type == "text/plain":
                body_text_parts.append(_decode_part(part))
            elif content_type == "text/html":
                body_html_parts.append(_decode_part(part))
            elif disposition == "inline" and content_type.startswith("image/"):
                payload = part.get_payload(decode=True) or b""
                attachments.append(
                    ParsedAttachment(
                        filename=filename or "inline-image",
                        content_type=content_type,
                        size=len(payload),
                        sha256=sha256_bytes(payload),
                        data=payload,
                        is_inline=True,
                        content_id=(part.get("Content-ID") or "").strip("<>") or None,
                        likely_logo=_looks_like_logo(filename, content_type, len(payload), True),
                    )
                )
    else:
        content_type = _safe(message.get_content_type).lower()
        if content_type == "text/html":
            body_html_parts.append(_decode_part(message))
        else:
            body_text_parts.append(_decode_part(message))

    from mangotree.clean.cleaner import repair_mojibake
    from mangotree.ingest.threading import parse_message_ids

    message_ids = parse_message_ids(headers.get("message-id"))

    # Subjects suffer the same encoding damage as bodies and are a primary
    # property-resolution signal, so they get the same repair.
    subject = repair_mojibake(headers.get("subject", "")).strip()

    return ParsedEmail(
        headers=headers,
        subject=subject,
        date=_parse_date(headers.get("date", "")),
        body_text="\n".join(p for p in body_text_parts if p).strip(),
        body_html="\n".join(p for p in body_html_parts if p).strip(),
        attachments=attachments,
        message_id=message_ids[0] if message_ids else None,
        in_reply_to=parse_message_ids(headers.get("in-reply-to")),
        references=parse_message_ids(headers.get("references")),
        raw_sha256=sha256_bytes(raw_bytes),
        raw_size=len(raw_bytes),
    )
