"""Gmail message ingestion — Phase 4 Sprint 1.

`ingest_one_email()` is the single idempotent entry point the plan calls for
(step 1.2): given the raw RFC822 bytes of ONE Gmail message, it runs the same
ingest stages the rest of the corpus uses —

    parse (shared .eml parser)  ->  dedup (3-way)  ->  write email doc
    ->  store + link attachments (GridFS, SHA-256)  ->  stamp evidentiary spine

Chunking + contextual summary + embedding remain the existing resumable batch
step (`build_email_chunks_v2`), which SHA-dedups attachment content and fans
out occurrences[] — so we do NOT duplicate that work per-message here.

THREE-WAY DEDUP (the "nothing re-ingested, nothing double-counted" guarantee):
  1. pst_entry_id == 'gmail:<id>' already present  -> we already pulled this
     exact Gmail message (resume/idempotency).
  2. internet_message_id already present (from PST/.eml/another label) -> the
     SAME logical email is already held; we don't re-insert, we just record the
     Gmail provenance (id + labels) on the existing doc.
  3. content_hash already present -> same content, missing/rewritten Message-ID;
     same treatment as (2).

Anything that passes all three is genuinely new and gets inserted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email import message_from_bytes
from typing import Any, Dict, List, Optional

from src.db.mongo import MongoClientWrapper
from src.db.repository import EmailRepository
from src.rag import evidence_schema as ev
from src.utils.hashing import sha256_bytes, sha256_strings
from src.utils.logger import logger

# Reuse the EXACT helpers the .eml ingestion uses, so a Gmail-pulled message is
# parsed byte-for-byte the same way as the AA_Fund .eml corpus (no divergence).
from scripts.ingest_eml_folder import (
    _strip_mbox_from_line, _addr, _addr_list, _parse_date,
    _extract_bodies, _extract_attachments, _normalize_subject,
)
from src.cleaner import clean_email_body, html_to_text


# Outcome codes returned by ingest_one_email.
OUT_INSERTED = "inserted"
OUT_SKIP_EXISTING = "skipped_existing_gmail"      # dedup (1)
OUT_SKIP_MSGID = "skipped_dup_message_id"          # dedup (2)
OUT_SKIP_HASH = "skipped_dup_content_hash"         # dedup (3)
OUT_ERROR = "error"


def parse_raw_email(raw: bytes) -> Dict[str, Any]:
    """Parse RFC822 bytes into the same structured dict the .eml path produces."""
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

    # Full header map (lower-cased keys) — parity with the PST pipeline, which
    # stores headers_raw and derives importance from it.
    headers_raw: Dict[str, str] = {}
    for k, v in msg.items():
        headers_raw[k.lower()] = v

    return {
        "file_sha": file_sha,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "subject": subject,
        "subject_normalized": subject_norm,
        "from": sender, "to": to, "cc": cc, "bcc": bcc, "reply_to": reply_to,
        "date": date,
        "body_text_raw": plain, "body_html": html, "body_text": body_clean,
        "attachments": attachments, "skipped_logos": skipped_logos,
        "headers_raw": headers_raw,
    }


def _derive_importance(headers_raw: Dict[str, str]) -> Optional[str]:
    """Same importance derivation the PST pipeline uses (Importance / X-Priority)."""
    imp_raw = (headers_raw or {}).get("importance") or (headers_raw or {}).get("x-priority")
    if not imp_raw:
        return None
    v = imp_raw.lower()
    if "high" in v or v.strip() in ("1", "2"):
        return "High"
    if "low" in v or v.strip() in ("4", "5"):
        return "Low"
    return "Normal"


def _content_hash(parsed: Dict[str, Any], body_clean: str) -> str:
    return sha256_strings([
        parsed["from"].get("email", "") or parsed["from"].get("name", ""),
        ",".join(a.get("email", "") for a in parsed["to"]),
        parsed["subject_normalized"],
        (parsed["date"].isoformat() if parsed["date"] else ""),
        body_clean[:5000],
    ])


def build_gmail_email_doc(
    parsed: Dict[str, Any], *, run_id, gmail_id: str, thread_id: Optional[str],
    label_names: List[str], max_body_chars: int,
    corpus: str, privilege_status: Optional[str], custodian: str,
) -> dict:
    """Build the `emails` document for a Gmail-pulled message (mirrors the .eml
    builder, with Gmail provenance + a configurable evidentiary corpus)."""
    body_clean = parsed["body_text"]
    body_raw = parsed["body_text_raw"]
    body_html = parsed["body_html"]
    if len(body_clean) > max_body_chars:
        body_clean = body_clean[:max_body_chars] + "\n…[truncated]"
    if len(body_raw) > max_body_chars:
        body_raw = body_raw[:max_body_chars] + "\n…[truncated]"
    if len(body_html) > max_body_chars:
        body_html = body_html[:max_body_chars] + "\n<!-- truncated -->"

    content_hash = _content_hash(parsed, body_clean)
    pst_entry_id = "gmail:" + gmail_id

    tid = thread_id
    if not tid:
        if parsed["references"]:
            tid = parsed["references"][0]
        elif parsed["in_reply_to"]:
            tid = parsed["in_reply_to"]
        elif parsed["subject_normalized"]:
            tid = "subj:" + parsed["subject_normalized"][:120]

    d = parsed["date"]
    date_ym = f"{d.year:04d}-{d.month:02d}" if d else None
    date_ymd = f"{d.year:04d}-{d.month:02d}-{d.day:02d}" if d else None

    folder_path = "gmail/" + (label_names[0] if label_names else "INBOX")

    evidence = ev.evidentiary_fields(
        corpus=corpus,
        source_file=pst_entry_id,
        sha256=parsed["file_sha"],
        ingest_run_id=str(run_id),
        custodian=custodian,
        privilege_status=privilege_status,
    )

    return {
        "pst_entry_id": pst_entry_id,
        "internet_message_id": parsed["message_id"] or None,
        "content_hash": content_hash,

        "subject": parsed["subject"],
        "subject_normalized": parsed["subject_normalized"],
        "from": parsed["from"], "to": parsed["to"], "cc": parsed["cc"],
        "bcc": parsed["bcc"], "reply_to": parsed["reply_to"],

        "date": d, "date_sent": d, "date_received": d, "date_modified": None,
        "date_year": d.year if d else None, "date_month": d.month if d else None,
        "date_day": d.day if d else None, "date_ym": date_ym, "date_ymd": date_ymd,
        "date_weekday": d.strftime("%A") if d else None,

        "body_text": body_clean, "body_text_raw": body_raw, "body_html": body_html,
        "body_format": "plain" if parsed["body_text_raw"] else "html",

        "has_attachments": bool(parsed["attachments"]),
        "attachment_count": len(parsed["attachments"]),
        "attachment_ids": [],

        "in_reply_to": parsed["in_reply_to"], "references": parsed["references"],
        "thread_id": tid, "conversation_topic": parsed["subject_normalized"],

        "folder_path": folder_path,
        "importance": _derive_importance(parsed.get("headers_raw")),
        "size_bytes": (len(body_raw) + len(body_html)) if (body_raw or body_html) else 0,
        "headers_raw": parsed.get("headers_raw") or {},

        "source": {
            "origin": "gmail_api",
            "gmail_id": gmail_id,
            "gmail_thread_id": thread_id,
            "gmail_labels": label_names,
            "file_sha256": parsed["file_sha"],
        },
        "gmail_id": gmail_id,
        "gmail_labels": label_names,
        "ingested_at": datetime.now(timezone.utc),
        "ingestion_run_id": run_id,

        **evidence,
    }


def _record_gmail_provenance(mongo: MongoClientWrapper, existing_id, *,
                             gmail_id: str, label_names: List[str]) -> None:
    """When a message already exists from another source, don't re-insert —
    just note that it was ALSO seen in Gmail (for the completeness audit)."""
    mongo.emails.update_one(
        {"_id": existing_id},
        {"$addToSet": {"also_seen_gmail_ids": gmail_id,
                       "also_seen_gmail_labels": {"$each": label_names or []}},
         "$set": {"present_in_gmail": True}},
    )


def ingest_one_email(
    raw: bytes,
    *,
    gmail_id: str,
    thread_id: Optional[str],
    label_names: List[str],
    mongo: MongoClientWrapper,
    repo: EmailRepository,
    run_id,
    settings,
    corpus: str = ev.CORPUS_LEGAL_CORRESPONDENCE,
    privilege_status: Optional[str] = None,
    custodian: str = "Gmail mailbox (read-only API pull)",
) -> Dict[str, Any]:
    """Idempotently ingest ONE Gmail message. Returns
    {outcome, gmail_id, attachments_stored, skipped_logos, message_id}.

    No writes happen for any of the three dedup hits — the corpus is never
    duplicated, and a message already held from the PST/.eml set is recognised
    and merely tagged with its Gmail provenance."""
    parsed = parse_raw_email(raw)
    body_clean = parsed["body_text"][: settings.max_body_chars]
    content_hash = _content_hash(parsed, body_clean)
    pst_entry_id = "gmail:" + gmail_id

    # ---- dedup (1): already pulled this exact Gmail message ----
    if mongo.emails.find_one({"pst_entry_id": pst_entry_id}, {"_id": 1}):
        return {"outcome": OUT_SKIP_EXISTING, "gmail_id": gmail_id,
                "attachments_stored": 0, "skipped_logos": parsed["skipped_logos"],
                "message_id": parsed["message_id"]}

    # ---- dedup (2): same Message-ID already held from another source ----
    if parsed["message_id"]:
        ex = mongo.emails.find_one(
            {"internet_message_id": parsed["message_id"]}, {"_id": 1})
        if ex:
            _record_gmail_provenance(mongo, ex["_id"], gmail_id=gmail_id,
                                     label_names=label_names)
            return {"outcome": OUT_SKIP_MSGID, "gmail_id": gmail_id,
                    "attachments_stored": 0,
                    "skipped_logos": parsed["skipped_logos"],
                    "message_id": parsed["message_id"]}

    # ---- dedup (3): same content hash already held ----
    ex = mongo.emails.find_one({"content_hash": content_hash}, {"_id": 1})
    if ex:
        _record_gmail_provenance(mongo, ex["_id"], gmail_id=gmail_id,
                                 label_names=label_names)
        return {"outcome": OUT_SKIP_HASH, "gmail_id": gmail_id,
                "attachments_stored": 0,
                "skipped_logos": parsed["skipped_logos"],
                "message_id": parsed["message_id"]}

    # ---- genuinely new — insert ----
    doc = build_gmail_email_doc(
        parsed, run_id=run_id, gmail_id=gmail_id, thread_id=thread_id,
        label_names=label_names, max_body_chars=settings.max_body_chars,
        corpus=corpus, privilege_status=privilege_status, custodian=custodian)

    id_map = repo.upsert_emails([doc])
    email_id = id_map.get(doc["pst_entry_id"])

    att_ids = []
    for att in parsed["attachments"]:
        if att.size_bytes > settings.attachment_max_bytes:
            logger.warning(f"skip oversize attachment {att.filename} ({att.size_bytes:,}B)")
            continue
        sha = sha256_bytes(att.data)
        aid = repo.store_attachment(
            email_id=email_id, email_pst_entry_id=doc["pst_entry_id"],
            filename=att.filename, display_name=att.filename,
            content_type=att.content_type, data=att.data, sha256=sha,
            is_inline=att.is_inline, content_id=att.content_id)
        att_ids.append(aid)
    if att_ids:
        repo.link_attachments_to_email(email_id, att_ids)

    return {"outcome": OUT_INSERTED, "gmail_id": gmail_id,
            "attachments_stored": len(att_ids),
            "skipped_logos": parsed["skipped_logos"],
            "message_id": parsed["message_id"]}


__all__ = [
    "ingest_one_email", "parse_raw_email", "build_gmail_email_doc",
    "OUT_INSERTED", "OUT_SKIP_EXISTING", "OUT_SKIP_MSGID", "OUT_SKIP_HASH",
    "OUT_ERROR",
]
