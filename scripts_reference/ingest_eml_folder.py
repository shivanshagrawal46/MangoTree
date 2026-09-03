"""
Sprint 1 — Ingest the David / AA_Fund `.eml` corpus into MongoDB.

Source layout (on F:\ by default):

    F:\AA_Fund\<YYYY-MM>\<id>__<date>__<subject>__<hash>.eml
    F:\AA_Fund\<YYYY-MM>\<id>__<date>__<subject>__<hash>\   (pre-extracted attachments)

Each .eml is a standard Gmail-export RFC822 message (with a leading mbox
"From " envelope line we strip). Attachments are ALSO embedded in the MIME,
which we decode authoritatively (so we get correct inline/attachment flags
and can skip signature logos).

What this script does — mirrors `src/pipeline/ingestion.py` so the SAME
downstream pipeline (ocr_attachments_v2 -> build_email_chunks_v2) works
unchanged:

  • parse each .eml (headers, threading, plain/HTML body)
  • clean the body with the existing cleaner (signatures, quoted replies,
    disclaimers, noise) — reused verbatim from src/cleaner
  • decode MIME attachments; SKIP inline signature logos (image001.png etc.)
  • write `emails` docs (idempotent on a namespaced pst_entry_id) + store
    attachments in GridFS via the existing EmailRepository
  • stamp every email with the evidentiary spine:
      corpus=fraud_communications, privilege_status=adverse_party,
      evidentiary_class=party_admission, custody{...}

Real duplicate attachments (e.g. the same title report attached to several
emails) are NOT skipped here — the SHA-256 dedup in build_email_chunks_v2
collapses them into one chunk-set with an occurrences[] fan-out, preserving
the provenance of every email that carried them. Only inline signature
LOGOS are dropped (that's the "company logo" noise).

Usage:
    # Safe dry-run on one month — parses + reports, writes NOTHING:
    python -m scripts.ingest_eml_folder --root "F:\\AA_Fund" --month 2024-06 --dry-run

    # Ingest one month for real:
    python -m scripts.ingest_eml_folder --root "F:\\AA_Fund" --month 2024-06

    # Ingest everything (resumable — re-running skips already-ingested):
    python -m scripts.ingest_eml_folder --root "F:\\AA_Fund"

    # Limit for a quick test:
    python -m scripts.ingest_eml_folder --root "F:\\AA_Fund" --limit 25 --dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from datetime import datetime, timezone
from email import message_from_bytes
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime, parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tqdm import tqdm

from config.settings import Settings
from src.cleaner import clean_email_body, html_to_text
from src.db.mongo import MongoClientWrapper
from src.db.repository import EmailRepository
from src.rag import evidence_schema as ev
from src.utils.hashing import sha256_bytes, sha256_strings
from src.utils.logger import logger


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fwd|fw|sv|aw|wg|tr)\s*:\s*", re.IGNORECASE)
_INLINE_IMAGE_NAME_RE = re.compile(
    r"^image\d+\.(png|jpe?g|gif|bmp|tif|tiff|webp)$", re.IGNORECASE
)
# Inline signature logos: small inline images. 50 KB threshold keeps real
# property photos (typically >100 KB) while dropping logos (~5-40 KB).
_SIGNATURE_LOGO_MAX_BYTES = 50 * 1024


def _normalize_subject(subject: str) -> str:
    s = subject or ""
    prev = None
    while prev != s:
        prev = s
        s = _SUBJECT_PREFIX_RE.sub("", s)
    return s.strip()


def _strip_mbox_from_line(raw: bytes) -> bytes:
    """Gmail exports prepend an mbox `From <id> <date>` envelope line that is
    NOT a real header (no colon). Strip it so the email parser sees clean
    RFC822 headers."""
    if raw.startswith(b"From ") and not raw.startswith(b"From:"):
        nl = raw.find(b"\n")
        if nl != -1:
            return raw[nl + 1 :]
    return raw


def _addr(value: Optional[str]) -> Dict[str, str]:
    name, email = parseaddr(value or "")
    return {"email": (email or "").strip().lower(), "name": (name or "").strip()}


def _addr_list(msg: Message, header: str) -> List[Dict[str, str]]:
    raw_values = msg.get_all(header, [])
    out: List[Dict[str, str]] = []
    for name, email in getaddresses(raw_values):
        email = (email or "").strip().lower()
        if not email:
            continue
        out.append({"email": email, "name": (name or "").strip()})
    return out


def _parse_date(msg: Message) -> Optional[datetime]:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _decode_part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "cp1252", "latin-1"):
        try:
            return payload.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _extract_bodies(msg: Message) -> Tuple[str, str]:
    """Return (plain_text, html). Picks the first non-attachment text parts."""
    plain, html = "", ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        if disp == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and not plain:
            plain = _decode_part_text(part)
        elif ctype == "text/html" and not html:
            html = _decode_part_text(part)
    return plain, html


def _is_signature_logo(
    *, filename: str, content_type: str, size: int, disposition: str, content_id: Optional[str]
) -> bool:
    if not content_type.lower().startswith("image/"):
        return False
    name = (filename or "").strip().lower()
    if _INLINE_IMAGE_NAME_RE.match(name):
        return True
    if (disposition == "inline" or content_id) and size < _SIGNATURE_LOGO_MAX_BYTES:
        return True
    return False


class _Att:
    __slots__ = ("filename", "content_type", "data", "is_inline", "content_id", "size_bytes")

    def __init__(self, filename, content_type, data, is_inline, content_id):
        self.filename = filename
        self.content_type = content_type
        self.data = data
        self.is_inline = is_inline
        self.content_id = content_id
        self.size_bytes = len(data or b"")


def _extract_attachments(msg: Message) -> Tuple[List[_Att], int]:
    """Return (kept_attachments, skipped_logo_count). Decodes MIME parts and
    drops inline signature logos."""
    kept: List[_Att] = []
    skipped_logos = 0
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        ctype = part.get_content_type()
        content_id = part.get("Content-ID")
        # A part is an attachment if it's marked as one, or it's an inline
        # file with a filename / Content-ID (not the body text/html).
        is_body_text = ctype in ("text/plain", "text/html") and disp != "attachment" and not filename
        if is_body_text:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        size = len(payload)
        if size <= 0:
            continue
        is_inline = disp == "inline" or (content_id is not None and disp != "attachment")
        if _is_signature_logo(
            filename=filename or "",
            content_type=ctype,
            size=size,
            disposition=disp,
            content_id=content_id,
        ):
            skipped_logos += 1
            continue
        kept.append(
            _Att(
                filename=filename or f"part.{ctype.replace('/', '.')}",
                content_type=ctype,
                data=payload,
                is_inline=is_inline,
                content_id=(content_id or None),
            )
        )
    return kept, skipped_logos


# ---------------------------------------------------------------------------
# Document construction (matches src/pipeline/ingestion.py _build_email_doc)
# ---------------------------------------------------------------------------

def parse_eml_file(path: Path, root: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    file_sha = sha256_bytes(raw)
    raw = _strip_mbox_from_line(raw)
    msg = message_from_bytes(raw)

    subject = (msg.get("Subject") or "").strip()
    subject_norm = _normalize_subject(subject)
    sender = _addr(msg.get("From"))
    to = _addr_list(msg, "To")
    cc = _addr_list(msg, "Cc")
    bcc = _addr_list(msg, "Bcc")
    reply_to = _addr_list(msg, "Reply-To")
    date = _parse_date(msg)

    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    in_reply_to = (msg.get("In-Reply-To") or "").strip() or None
    refs_raw = (msg.get("References") or "").strip()
    references = refs_raw.split() if refs_raw else []

    plain, html = _extract_bodies(msg)
    body_source = plain or (html_to_text(html) if html else "")
    body_clean = clean_email_body(body_source, strip_quotes=True)

    attachments, skipped_logos = _extract_attachments(msg)

    # Relative path under the root for provenance / stable id.
    try:
        rel_path = str(path.relative_to(root))
    except ValueError:
        rel_path = path.name
    # month folder = first path component, e.g. "2024-06"
    month = path.parent.name

    return {
        "file_sha": file_sha,
        "rel_path": rel_path,
        "month": month,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "subject": subject,
        "subject_normalized": subject_norm,
        "from": sender,
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "reply_to": reply_to,
        "date": date,
        "body_text_raw": plain,
        "body_html": html,
        "body_text": body_clean,
        "attachments": attachments,
        "skipped_logos": skipped_logos,
    }


def build_email_doc(parsed: Dict[str, Any], run_id, max_body_chars: int) -> dict:
    body_clean = parsed["body_text"]
    body_raw = parsed["body_text_raw"]
    body_html = parsed["body_html"]
    if len(body_clean) > max_body_chars:
        body_clean = body_clean[:max_body_chars] + "\n…[truncated]"
    if len(body_raw) > max_body_chars:
        body_raw = body_raw[:max_body_chars] + "\n…[truncated]"
    if len(body_html) > max_body_chars:
        body_html = body_html[:max_body_chars] + "\n<!-- truncated -->"

    content_hash = sha256_strings(
        [
            parsed["from"].get("email", "") or parsed["from"].get("name", ""),
            ",".join(a.get("email", "") for a in parsed["to"]),
            parsed["subject_normalized"],
            (parsed["date"].isoformat() if parsed["date"] else ""),
            body_clean[:5000],
        ]
    )

    # Namespaced, stable, unique idempotency key (analogous to pst_entry_id).
    base = parsed["message_id"] or ("path:" + parsed["rel_path"])
    pst_entry_id = "aafund:" + base

    thread_id: Optional[str] = None
    if parsed["references"]:
        thread_id = parsed["references"][0]
    elif parsed["in_reply_to"]:
        thread_id = parsed["in_reply_to"]
    elif parsed["subject_normalized"]:
        thread_id = "subj:" + parsed["subject_normalized"][:120]

    d = parsed["date"]
    date_year = d.year if d else None
    date_month = d.month if d else None
    date_day = d.day if d else None
    date_ym = f"{d.year:04d}-{d.month:02d}" if d else None
    date_ymd = f"{d.year:04d}-{d.month:02d}-{d.day:02d}" if d else None
    date_weekday = d.strftime("%A") if d else None

    folder_path = "AA_Fund/" + (parsed["month"] or "")

    # Evidentiary spine — David corpus = adverse-party admissions.
    evidence = ev.evidentiary_fields(
        corpus=ev.CORPUS_FRAUD_COMMUNICATIONS,
        source_file=parsed["rel_path"],
        sha256=parsed["file_sha"],
        ingest_run_id=str(run_id),
        custodian="AA_Fund mailbox export (Rakesh / David correspondence)",
    )

    doc = {
        "pst_entry_id": pst_entry_id,
        "internet_message_id": parsed["message_id"] or None,
        "content_hash": content_hash,

        "subject": parsed["subject"],
        "subject_normalized": parsed["subject_normalized"],

        "from": parsed["from"],
        "to": parsed["to"],
        "cc": parsed["cc"],
        "bcc": parsed["bcc"],
        "reply_to": parsed["reply_to"],

        "date": d,
        "date_sent": d,
        "date_received": d,
        "date_modified": None,
        "date_year": date_year,
        "date_month": date_month,
        "date_day": date_day,
        "date_ym": date_ym,
        "date_ymd": date_ymd,
        "date_weekday": date_weekday,

        "body_text": body_clean,
        "body_text_raw": body_raw,
        "body_html": body_html,
        "body_format": "plain" if parsed["body_text_raw"] else "html",

        "has_attachments": bool(parsed["attachments"]),
        "attachment_count": len(parsed["attachments"]),
        "attachment_ids": [],

        "in_reply_to": parsed["in_reply_to"],
        "references": parsed["references"],
        "thread_id": thread_id,
        "conversation_topic": parsed["subject_normalized"],

        "folder_path": folder_path,
        "importance": None,
        "size_bytes": (len(body_raw) + len(body_html)) if (body_raw or body_html) else 0,
        "headers_raw": {},

        "source": {
            "origin": "eml_folder",
            "root": "AA_Fund",
            "rel_path": parsed["rel_path"],
            "file_sha256": parsed["file_sha"],
        },
        "ingested_at": datetime.now(timezone.utc),
        "ingestion_run_id": run_id,

        # ---- Evidentiary spine (Phase 3) ----
        **evidence,
    }
    return doc


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _iter_eml_files(root: Path, month: Optional[str]) -> List[Path]:
    if month:
        base = root / month
        if not base.exists():
            logger.error(f"Month folder not found: {base}")
            return []
        return sorted(base.glob("*.eml"))
    return sorted(root.glob("*/*.eml"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest the AA_Fund .eml corpus into MongoDB.")
    ap.add_argument("--root", default=r"F:\AA_Fund", help="Root folder containing month subfolders")
    ap.add_argument("--month", default=None, help="Only ingest one YYYY-MM month folder")
    ap.add_argument("--limit", type=int, default=0, help="Max emails to process (0 = all)")
    ap.add_argument("--dry-run", action="store_true", help="Parse + report only; write NOTHING")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        logger.error(f"Root not found: {root}")
        return 2

    eml_files = _iter_eml_files(root, args.month)
    if args.limit > 0:
        eml_files = eml_files[: args.limit]
    if not eml_files:
        logger.error("No .eml files found.")
        return 2

    logger.info(
        f"Found {len(eml_files):,} .eml files "
        f"({'DRY RUN — no writes' if args.dry_run else 'LIVE — writing to MongoDB'})"
    )

    settings = Settings.load()
    mongo: Optional[MongoClientWrapper] = None
    repo: Optional[EmailRepository] = None
    run_id = "dry-run"
    existing_ids: set[str] = set()

    if not args.dry_run:
        mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
        mongo.ping()
        repo = EmailRepository(mongo)
        run_id = repo.start_run(
            pst_meta={
                "path": str(root),
                "name": "AA_Fund",
                "origin": "eml_folder",
                "corpus": ev.CORPUS_FRAUD_COMMUNICATIONS,
            }
        )
        # Resume: skip already-ingested aafund emails.
        existing_ids = {
            doc["pst_entry_id"]
            for doc in mongo.emails.find(
                {"pst_entry_id": {"$regex": "^aafund:"}},
                {"pst_entry_id": 1, "_id": 0},
            )
        }
        logger.info(f"{len(existing_ids):,} AA_Fund emails already ingested — will skip those")

    totals = {
        "seen": 0, "inserted": 0, "skipped_existing": 0,
        "attachments_kept": 0, "attachments_stored": 0, "logos_skipped": 0, "errors": 0,
    }
    folder_counts: Dict[str, int] = {}

    for path in tqdm(eml_files, desc="Ingesting .eml", unit="msg"):
        totals["seen"] += 1
        try:
            parsed = parse_eml_file(path, root)
        except Exception as exc:  # noqa: BLE001
            totals["errors"] += 1
            logger.warning(f"parse failed for {path.name}: {exc}")
            continue

        totals["logos_skipped"] += parsed["skipped_logos"]
        doc = build_email_doc(parsed, run_id, settings.max_body_chars)
        totals["attachments_kept"] += doc["attachment_count"]

        if args.dry_run:
            if totals["seen"] <= 3:
                logger.info(
                    f"[sample] {path.name}\n"
                    f"  from={doc['from']}  date={doc['date']}  subj={doc['subject'][:80]!r}\n"
                    f"  attachments_kept={doc['attachment_count']} logos_skipped={parsed['skipped_logos']}\n"
                    f"  clean_body[:280]={doc['body_text'][:280]!r}"
                )
            continue

        if doc["pst_entry_id"] in existing_ids:
            totals["skipped_existing"] += 1
            continue

        try:
            id_map = repo.upsert_emails([doc])  # type: ignore[union-attr]
            email_id = id_map.get(doc["pst_entry_id"])
            totals["inserted"] += 1
            folder_counts[doc["folder_path"]] = folder_counts.get(doc["folder_path"], 0) + 1

            att_ids = []
            for att in parsed["attachments"]:
                if att.size_bytes > settings.attachment_max_bytes:
                    logger.warning(f"skip oversize attachment {att.filename} ({att.size_bytes:,}B)")
                    continue
                sha = sha256_bytes(att.data)
                aid = repo.store_attachment(  # type: ignore[union-attr]
                    email_id=email_id,
                    email_pst_entry_id=doc["pst_entry_id"],
                    filename=att.filename,
                    display_name=att.filename,
                    content_type=att.content_type,
                    data=att.data,
                    sha256=sha,
                    is_inline=att.is_inline,
                    content_id=att.content_id,
                )
                att_ids.append(aid)
                totals["attachments_stored"] += 1
            if att_ids:
                repo.link_attachments_to_email(email_id, att_ids)  # type: ignore[union-attr]
            existing_ids.add(doc["pst_entry_id"])
        except Exception as exc:  # noqa: BLE001
            totals["errors"] += 1
            logger.error(f"write failed for {path.name}: {exc}")
            if repo is not None and mongo is not None:
                repo.log_error(run_id, doc["pst_entry_id"], "ingest_eml", str(exc), traceback.format_exc())

    if not args.dry_run and repo is not None:
        for fp in folder_counts:
            try:
                repo.upsert_folder(fp)
            except Exception:  # noqa: BLE001
                pass
        repo.finish_run(run_id, totals, status="completed")
        if mongo is not None:
            mongo.close()

    logger.info(f"DONE. totals={totals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
