"""
PST file parser.

Walks every folder + message in a PST archive, parses transport headers,
extracts plain-text + HTML bodies, and reads every attachment as bytes.

Yields self-contained `ParsedEmail` objects so the rest of the pipeline does
not depend on libpff/libratom internals.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_string
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Generator, Iterable, List, Optional

from libratom.lib.pff import PffArchive

from src.utils.logger import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ParsedAttachment:
    filename: str
    display_name: Optional[str]
    size_bytes: int
    data: bytes
    content_type: Optional[str] = None
    is_inline: bool = False
    content_id: Optional[str] = None


@dataclass
class ParsedEmail:
    pst_entry_id: str
    folder_path: str

    subject: str = ""
    subject_normalized: str = ""

    sender: dict = field(default_factory=dict)
    to: List[dict] = field(default_factory=list)
    cc: List[dict] = field(default_factory=list)
    bcc: List[dict] = field(default_factory=list)
    reply_to: Optional[dict] = None

    date_sent: Optional[datetime] = None
    date_received: Optional[datetime] = None
    date_modified: Optional[datetime] = None

    body_text_raw: str = ""
    body_html: str = ""
    body_format: str = "text"

    internet_message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: List[str] = field(default_factory=list)
    conversation_topic: Optional[str] = None

    headers_raw: dict = field(default_factory=dict)
    headers_string: str = ""

    attachments: List[ParsedAttachment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RE_FWD_RE = re.compile(r"^\s*(re|fw|fwd|sv|aw|wg|tr)\s*:\s*", re.IGNORECASE)
_INVISIBLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _decode_bytes(value: Any) -> str:
    """Decode bytes from libpff (often UTF-8 / latin-1) to str."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-16-le", "cp1252", "latin-1"):
            try:
                return value.decode(encoding)
            except UnicodeDecodeError:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value)


def _strip_invisible(text: str) -> str:
    if not text:
        return ""
    return _INVISIBLE.sub("", text)


def _normalize_subject(subject: str) -> str:
    if not subject:
        return ""
    s = subject.strip().lower()
    while True:
        new = _RE_FWD_RE.sub("", s)
        if new == s:
            break
        s = new
    return s.strip()


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_addresses(raw: str | None) -> List[dict]:
    from src.utils.email_utils import parse_address_list
    return parse_address_list(raw)


def _parse_address(raw: str | None) -> Optional[dict]:
    from src.utils.email_utils import parse_address
    return parse_address(raw)


def _parse_references(raw: str | None) -> List[str]:
    if not raw:
        return []
    parts = re.findall(r"<([^>]+)>", raw)
    return [p.strip() for p in parts if p.strip()]


def _parse_date(raw: str | None) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        return _to_utc(dt)
    except (TypeError, ValueError):
        return None


def _parse_headers(headers_string: str) -> tuple[Message, dict]:
    """Parse RFC 822 transport headers into a Message + flat dict."""
    if not headers_string:
        return Message(), {}
    try:
        msg = message_from_string(headers_string)
    except Exception:
        return Message(), {}

    flat: dict[str, str] = {}
    for k, v in msg.items():
        key = k.lower()
        if key in flat:
            flat[key] = flat[key] + "\n" + str(v)
        else:
            flat[key] = str(v)
    return msg, flat


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class PSTParser:
    """Iterates messages in a PST and yields ParsedEmail."""

    def __init__(self, pst_path: Path) -> None:
        if not pst_path.exists():
            raise FileNotFoundError(f"PST not found: {pst_path}")
        self.pst_path = pst_path
        self._archive: PffArchive | None = None

    # ---- lifecycle ----
    def __enter__(self) -> "PSTParser":
        self._archive = PffArchive(str(self.pst_path))
        logger.info(
            f"Opened PST '{self.pst_path.name}' "
            f"({self._archive.message_count} messages)"
        )
        return self

    def __exit__(self, *_):
        if self._archive is not None:
            try:
                exit_fn = getattr(self._archive, "__exit__", None)
                if exit_fn:
                    exit_fn(None, None, None)
            except Exception:
                pass
            self._archive = None

    @property
    def message_count(self) -> int:
        return self._archive.message_count if self._archive else 0

    # ---- iteration ----
    def iter_messages(self) -> Generator[tuple[Any, str], None, None]:
        """Yield (pypff_message, folder_path) for every message in the PST."""
        if self._archive is None:
            raise RuntimeError("PSTParser must be used as context manager")
        yield from self._walk_folder(self._archive._data.get_root_folder(), "")

    def iter_message_ids(self) -> Generator[tuple[str, Any, str], None, None]:
        """
        Yield (pst_entry_id, message, folder_path) without parsing the
        message bodies/attachments. Useful for fast resume — caller can
        check whether the id is already ingested and skip the full parse.
        """
        for msg, folder_path in self.iter_messages():
            try:
                yield str(msg.identifier), msg, folder_path
            except Exception as exc:
                logger.warning(f"Could not read message identifier: {exc}")
                continue

    def _walk_folder(self, folder: Any, parent_path: str) -> Generator[tuple[Any, str], None, None]:
        try:
            name = _decode_bytes(folder.name) if folder.name else ""
        except Exception:
            name = ""

        path = f"{parent_path}/{name}" if parent_path and name else (name or parent_path or "Root")
        if path.startswith("/"):
            path = path[1:]

        try:
            n_messages = folder.number_of_sub_messages
        except Exception:
            n_messages = 0

        for i in range(n_messages):
            try:
                msg = folder.get_sub_message(i)
            except Exception as exc:
                logger.warning(f"Skip unreadable message #{i} in '{path}': {exc}")
                continue
            yield msg, path

        try:
            n_subfolders = folder.number_of_sub_folders
        except Exception:
            n_subfolders = 0

        for i in range(n_subfolders):
            try:
                sub = folder.get_sub_folder(i)
            except Exception as exc:
                logger.warning(f"Skip unreadable subfolder #{i} in '{path}': {exc}")
                continue
            yield from self._walk_folder(sub, path)

    # ---- per-message parsing ----
    def parse_message(
        self,
        message: Any,
        folder_path: str,
        attachment_max_bytes: int | None = None,
    ) -> ParsedEmail:
        pst_entry_id = str(message.identifier)

        subject = _strip_invisible(_decode_bytes(message.subject) or "")
        sender_name = _strip_invisible(_decode_bytes(message.sender_name) or "")
        conv_topic = _strip_invisible(_decode_bytes(message.conversation_topic) or "")

        plain = _decode_bytes(message.plain_text_body)
        html = _decode_bytes(message.html_body)

        if html and plain:
            body_format = "mixed"
        elif html:
            body_format = "html"
        elif plain:
            body_format = "text"
        else:
            rtf = _decode_bytes(message.rtf_body)
            if rtf:
                plain = rtf
                body_format = "rtf"
            else:
                body_format = "text"

        plain = _strip_invisible(plain)

        date_sent = _to_utc(message.client_submit_time)
        date_received = _to_utc(message.delivery_time)
        date_modified = _to_utc(message.modification_time)

        headers_string = _decode_bytes(message.transport_headers) or ""
        _, flat = _parse_headers(headers_string)

        # Sender: prefer headers (gives email), fall back to libpff sender_name
        from_field = _parse_address(flat.get("from")) or (
            {"name": sender_name, "email": ""} if sender_name else None
        ) or {"name": "", "email": ""}
        if from_field.get("email"):
            from src.utils.email_utils import domain_of
            from_field["domain"] = domain_of(from_field["email"])
        else:
            from_field["domain"] = ""

        to_list = _parse_addresses(flat.get("to"))
        cc_list = _parse_addresses(flat.get("cc"))
        bcc_list = _parse_addresses(flat.get("bcc"))

        reply_to = _parse_address(flat.get("reply-to"))

        from src.utils.email_utils import domain_of
        for lst in (to_list, cc_list, bcc_list):
            for addr in lst:
                addr["domain"] = domain_of(addr.get("email", ""))
        if reply_to and reply_to.get("email"):
            reply_to["domain"] = domain_of(reply_to["email"])

        # Headers can give us a more accurate sent date than client_submit_time
        header_date = _parse_date(flat.get("date"))
        if header_date and not date_sent:
            date_sent = header_date

        internet_message_id = None
        if flat.get("message-id"):
            mid = flat["message-id"].strip()
            m = re.search(r"<([^>]+)>", mid)
            internet_message_id = m.group(1) if m else mid

        in_reply_to = None
        if flat.get("in-reply-to"):
            m = re.search(r"<([^>]+)>", flat["in-reply-to"])
            in_reply_to = m.group(1) if m else flat["in-reply-to"].strip()

        references = _parse_references(flat.get("references"))

        attachments = self._parse_attachments(message, max_bytes=attachment_max_bytes)

        return ParsedEmail(
            pst_entry_id=pst_entry_id,
            folder_path=folder_path,
            subject=subject,
            subject_normalized=_normalize_subject(subject),
            sender=from_field,
            to=to_list,
            cc=cc_list,
            bcc=bcc_list,
            reply_to=reply_to,
            date_sent=date_sent,
            date_received=date_received,
            date_modified=date_modified,
            body_text_raw=plain,
            body_html=html,
            body_format=body_format,
            internet_message_id=internet_message_id,
            in_reply_to=in_reply_to,
            references=references,
            conversation_topic=conv_topic or None,
            headers_raw=flat,
            headers_string=headers_string,
            attachments=attachments,
        )

    # ---- attachment reading ----
    def _parse_attachments(
        self,
        message: Any,
        max_bytes: int | None = None,
    ) -> List[ParsedAttachment]:
        """
        Read attachments from a message.

        If `max_bytes` is set, attachments larger than that are still recorded
        (filename + size) but their bytes are NOT loaded into memory — `data`
        will be `b""`. This prevents the process from stalling on multi-GB
        embedded files.
        """
        out: List[ParsedAttachment] = []
        try:
            count = message.number_of_attachments
        except Exception:
            return out

        for i in range(count):
            try:
                att = message.get_attachment(i)
            except Exception as exc:
                logger.warning(f"Skip attachment #{i} of msg {message.identifier}: {exc}")
                continue

            try:
                filename = _decode_bytes(att.name) or f"attachment_{i}"
                try:
                    size = int(att.size or 0)
                except Exception:
                    size = 0

                data = b""
                if size > 0:
                    if max_bytes is not None and size > max_bytes:
                        logger.warning(
                            f"Skipping read of oversize attachment "
                            f"'{filename}' ({size:,} bytes > {max_bytes:,})"
                        )
                    else:
                        # Read in 4 MB chunks so we never block forever on
                        # one giant `read_buffer(huge_size)` call.
                        try:
                            att.seek_offset(0, 0)
                        except Exception:
                            pass
                        chunks: list[bytes] = []
                        remaining = size
                        chunk_size = 4 * 1024 * 1024
                        while remaining > 0:
                            n = min(chunk_size, remaining)
                            try:
                                buf = att.read_buffer(n)
                            except Exception as exc:
                                logger.warning(
                                    f"Read error on attachment '{filename}' "
                                    f"at offset {size - remaining}: {exc}"
                                )
                                break
                            if not buf:
                                break
                            chunks.append(buf)
                            remaining -= len(buf)
                        data = b"".join(chunks)

                out.append(
                    ParsedAttachment(
                        filename=filename,
                        display_name=filename,
                        size_bytes=len(data) if data else size,
                        data=data,
                    )
                )
            except Exception as exc:
                logger.warning(f"Failed to read attachment #{i}: {exc}")
                continue
        return out

    # ---- convenience ----
    def iter_parsed(
        self,
        attachment_max_bytes: int | None = None,
    ) -> Iterable[ParsedEmail]:
        for msg, folder_path in self.iter_messages():
            try:
                yield self.parse_message(
                    msg, folder_path, attachment_max_bytes=attachment_max_bytes
                )
            except Exception as exc:
                logger.exception(f"Failed to parse message in '{folder_path}': {exc}")
                continue
