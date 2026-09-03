"""
End-to-end ingestion pipeline.

Pipeline stages:
  1. Open PST + record run start (with PST sha256 for traceability)
  2. For each message:
        parse with libpff -> ParsedEmail (incl. attachments)
        clean bodies (HTML -> text + signature/quote/footer removal)
        compose Mongo document
        store attachments in GridFS, link to email
  3. Bulk upsert emails (idempotent on pst_entry_id)
  4. Update folder counters
  5. Mark run completed with totals
"""
from __future__ import annotations

import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from bson import ObjectId
from tqdm import tqdm

from config.settings import Settings
from src.cleaner import clean_email_body, html_to_text
from src.db.mongo import MongoClientWrapper
from src.db.repository import EmailRepository
from src.parser.pst_parser import ParsedEmail, PSTParser
from src.utils.hashing import sha256_bytes, sha256_file, sha256_strings
from src.utils.logger import logger


class IngestionPipeline:
    # Parallel uploads to GridFS. 8 workers is a sweet spot for slow networks
    # without blowing past Atlas connection limits (M0 free tier = 100).
    ATTACHMENT_UPLOAD_WORKERS = 8

    def __init__(self, settings: Settings, mongo: MongoClientWrapper) -> None:
        self.settings = settings
        self.mongo = mongo
        self.repo = EmailRepository(mongo)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self) -> dict:
        pst_path: Path = self.settings.pst_file_path
        if not pst_path.exists():
            raise FileNotFoundError(f"PST not found: {pst_path}")

        logger.info(f"Computing SHA-256 of PST file ({pst_path.stat().st_size:,} bytes)…")
        pst_sha = sha256_file(pst_path)
        logger.info(f"PST sha256 = {pst_sha}")

        run_id = self.repo.start_run(
            pst_meta={
                "path": str(pst_path),
                "name": pst_path.name,
                "size_bytes": pst_path.stat().st_size,
                "sha256": pst_sha,
            }
        )

        totals = {
            "messages_seen": 0,
            "messages_inserted": 0,
            "messages_skipped": 0,
            "attachments_inserted": 0,
            "errors": 0,
        }

        # Pre-fetch existing pst_entry_ids so we can SKIP parsing those
        # messages entirely (much faster resume).
        logger.info("Pre-fetching already-ingested pst_entry_ids for resume…")
        existing_ids: set[str] = {
            d["pst_entry_id"]
            for d in self.mongo.emails.find({}, {"pst_entry_id": 1, "_id": 0})
        }
        logger.info(f"Found {len(existing_ids):,} already-ingested messages — these will be skipped")

        status = "completed"
        try:
            with PSTParser(pst_path) as parser:
                total_msgs = parser.message_count or 0
                progress = tqdm(total=total_msgs, desc="Ingesting", unit="msg")

                batch: list[tuple[ParsedEmail, dict]] = []
                folder_counts: dict[str, int] = {}

                for pst_entry_id, msg, folder_path in parser.iter_message_ids():
                    totals["messages_seen"] += 1
                    progress.update(1)

                    if pst_entry_id in existing_ids:
                        totals["messages_skipped"] += 1
                        if totals["messages_seen"] % 200 == 0:
                            logger.info(
                                f"Skip-scan: {totals['messages_seen']}/{total_msgs} "
                                f"({totals['messages_skipped']} already ingested)"
                            )
                        continue

                    # Only fully parse messages we actually need to insert
                    try:
                        parsed = parser.parse_message(
                            msg,
                            folder_path,
                            attachment_max_bytes=self.settings.attachment_max_bytes,
                        )
                    except Exception as exc:
                        totals["errors"] += 1
                        self.repo.log_error(
                            run_id, pst_entry_id, "parse_message",
                            str(exc), traceback.format_exc(),
                        )
                        continue

                    if totals["messages_seen"] % 100 == 0:
                        logger.info(
                            f"Progress: {totals['messages_seen']}/{total_msgs} "
                            f"seen, "
                            f"{totals['messages_inserted']} inserted, "
                            f"{totals['messages_skipped']} skipped, "
                            f"{totals['attachments_inserted']} attachments, "
                            f"{totals['errors']} errors"
                        )

                    try:
                        doc = self._build_email_doc(parsed, run_id, pst_path, pst_sha)
                    except Exception as exc:
                        totals["errors"] += 1
                        self.repo.log_error(
                            run_id, parsed.pst_entry_id, "build_doc",
                            str(exc), traceback.format_exc(),
                        )
                        continue

                    folder_counts[parsed.folder_path] = (
                        folder_counts.get(parsed.folder_path, 0) + 1
                    )
                    batch.append((parsed, doc))

                    if len(batch) >= self.settings.batch_size:
                        self._flush_batch(batch, run_id, totals)
                        batch.clear()
                        self.repo.update_run_totals(run_id, totals)

                if batch:
                    self._flush_batch(batch, run_id, totals)
                    batch.clear()

                progress.close()

                # Update folder counts (set_to_zero then increment cleanly)
                for path, _count in folder_counts.items():
                    if path:
                        self.repo.upsert_folder(path)

        except Exception as exc:
            status = "failed"
            logger.exception(f"Pipeline failed: {exc}")
            self.repo.log_error(run_id, "", "pipeline", str(exc), traceback.format_exc())
        finally:
            self.repo.finish_run(run_id, totals, status=status)

        return {"run_id": str(run_id), "status": status, "totals": totals}

    # ------------------------------------------------------------------
    # Batch writes
    # ------------------------------------------------------------------
    def _flush_batch(
        self,
        batch: list[tuple[ParsedEmail, dict]],
        run_id: ObjectId,
        totals: dict,
    ) -> None:
        t0 = time.time()
        # 1) Skip already-ingested emails (idempotency)
        ids_in_batch = [doc["pst_entry_id"] for _p, doc in batch]
        existing = self.repo.existing_pst_entry_ids(ids_in_batch)
        new_pairs = [(p, d) for (p, d) in batch if d["pst_entry_id"] not in existing]
        totals["messages_skipped"] += len(batch) - len(new_pairs)

        if not new_pairs:
            return

        # 2) Upsert emails
        docs = [d for _p, d in new_pairs]
        id_map = self.repo.upsert_emails(docs)
        totals["messages_inserted"] += len(new_pairs)
        t_emails = time.time() - t0

        # 3) Build flat list of (parsed_email, email_id, attachment) tuples to upload
        upload_jobs: list[tuple[ParsedEmail, ObjectId, Any]] = []
        per_email_ids: dict[ObjectId, list[ObjectId]] = {}
        skipped_attachments = 0

        for parsed, doc in new_pairs:
            email_id = id_map.get(doc["pst_entry_id"])
            if email_id is None:
                continue
            per_email_ids[email_id] = []
            if not parsed.attachments:
                continue

            for att in parsed.attachments:
                if att.size_bytes <= 0 or not att.data:
                    skipped_attachments += 1
                    continue
                if att.size_bytes > self.settings.attachment_max_bytes:
                    logger.warning(
                        f"Skip oversize attachment '{att.filename}' "
                        f"({att.size_bytes:,} bytes)"
                    )
                    skipped_attachments += 1
                    continue
                upload_jobs.append((parsed, email_id, att))

        # 4) Upload attachments in parallel — this is the big speedup
        bytes_uploaded = 0
        if upload_jobs:
            t_att_start = time.time()

            def _do_upload(job):
                parsed, email_id, att = job
                sha = sha256_bytes(att.data)
                attachment_id = self.repo.store_attachment(
                    email_id=email_id,
                    email_pst_entry_id=parsed.pst_entry_id,
                    filename=att.filename,
                    display_name=att.display_name,
                    content_type=att.content_type,
                    data=att.data,
                    sha256=sha,
                    is_inline=att.is_inline,
                    content_id=att.content_id,
                )
                return parsed.pst_entry_id, email_id, attachment_id, len(att.data)

            with ThreadPoolExecutor(max_workers=self.ATTACHMENT_UPLOAD_WORKERS) as ex:
                futures = {ex.submit(_do_upload, job): job for job in upload_jobs}
                for fut in as_completed(futures):
                    job = futures[fut]
                    parsed = job[0]
                    try:
                        _pst, email_id, attachment_id, n_bytes = fut.result()
                        per_email_ids[email_id].append(attachment_id)
                        totals["attachments_inserted"] += 1
                        bytes_uploaded += n_bytes
                    except Exception as exc:
                        totals["errors"] += 1
                        self.repo.log_error(
                            run_id, parsed.pst_entry_id, "store_attachment",
                            str(exc), traceback.format_exc(),
                        )

            t_attach = time.time() - t_att_start
            mbps = (bytes_uploaded / 1024 / 1024) / t_attach if t_attach > 0 else 0
            logger.info(
                f"Batch flushed: {len(new_pairs)} emails ({t_emails:.1f}s) + "
                f"{len(upload_jobs)} attachments "
                f"({bytes_uploaded / 1024 / 1024:.1f} MB in {t_attach:.1f}s "
                f"= {mbps:.2f} MB/s, {skipped_attachments} skipped)"
            )
        else:
            logger.info(
                f"Batch flushed: {len(new_pairs)} emails ({t_emails:.1f}s), "
                f"no attachments"
            )

        # 5) Link attachment ids to their parent emails (one bulk-ish update)
        for email_id, att_ids in per_email_ids.items():
            if att_ids:
                self.repo.link_attachments_to_email(email_id, att_ids)

    # ------------------------------------------------------------------
    # Document construction
    # ------------------------------------------------------------------
    def _build_email_doc(
        self,
        parsed: ParsedEmail,
        run_id: ObjectId,
        pst_path: Path,
        pst_sha: str,
    ) -> dict:
        # ---- Body cleaning ----
        # Prefer plain text if present; fall back to HTML -> text
        plain_source = parsed.body_text_raw or ""
        if not plain_source and parsed.body_html:
            plain_source = html_to_text(parsed.body_html)

        body_text_clean = clean_email_body(plain_source, strip_quotes=True)

        # Truncate giant bodies (keeps Mongo doc < 16MB safety margin)
        max_chars = self.settings.max_body_chars
        if len(body_text_clean) > max_chars:
            body_text_clean = body_text_clean[:max_chars] + "\n…[truncated]"
        if len(parsed.body_text_raw) > max_chars:
            parsed.body_text_raw = parsed.body_text_raw[:max_chars] + "\n…[truncated]"
        if len(parsed.body_html) > max_chars:
            parsed.body_html = parsed.body_html[:max_chars] + "\n<!-- truncated -->"

        # ---- Content hash for cross-PST dedup ----
        content_hash = sha256_strings(
            [
                parsed.sender.get("email", "") or parsed.sender.get("name", ""),
                ",".join(a.get("email", "") for a in parsed.to),
                parsed.subject_normalized,
                (parsed.date_sent.isoformat() if parsed.date_sent else ""),
                body_text_clean[:5000],
            ]
        )

        # ---- Thread id (heuristic) ----
        thread_id: str | None = None
        if parsed.references:
            thread_id = parsed.references[0]
        elif parsed.in_reply_to:
            thread_id = parsed.in_reply_to
        elif parsed.subject_normalized:
            thread_id = "subj:" + parsed.subject_normalized[:120]

        importance = None
        imp_raw = parsed.headers_raw.get("importance") or parsed.headers_raw.get("x-priority")
        if imp_raw:
            v = imp_raw.lower()
            if "high" in v or v.strip() in ("1", "2"):
                importance = "High"
            elif "low" in v or v.strip() in ("4", "5"):
                importance = "Low"
            else:
                importance = "Normal"

        # Canonical date for sorting/filtering (sent > received > modified)
        canonical_date = parsed.date_sent or parsed.date_received or parsed.date_modified
        date_year = canonical_date.year if canonical_date else None
        date_month = canonical_date.month if canonical_date else None
        date_day = canonical_date.day if canonical_date else None
        date_ym = f"{canonical_date.year:04d}-{canonical_date.month:02d}" if canonical_date else None
        date_ymd = (
            f"{canonical_date.year:04d}-{canonical_date.month:02d}-{canonical_date.day:02d}"
            if canonical_date else None
        )
        date_weekday = canonical_date.strftime("%A") if canonical_date else None

        return {
            "pst_entry_id": parsed.pst_entry_id,
            "internet_message_id": parsed.internet_message_id,
            "content_hash": content_hash,

            "subject": parsed.subject,
            "subject_normalized": parsed.subject_normalized,

            "from": parsed.sender,
            "to": parsed.to,
            "cc": parsed.cc,
            "bcc": parsed.bcc,
            "reply_to": parsed.reply_to,

            "date": canonical_date,
            "date_sent": parsed.date_sent,
            "date_received": parsed.date_received,
            "date_modified": parsed.date_modified,
            "date_year": date_year,
            "date_month": date_month,
            "date_day": date_day,
            "date_ym": date_ym,
            "date_ymd": date_ymd,
            "date_weekday": date_weekday,

            "body_text": body_text_clean,
            "body_text_raw": parsed.body_text_raw,
            "body_html": parsed.body_html,
            "body_format": parsed.body_format,

            "has_attachments": bool(parsed.attachments),
            "attachment_count": len(parsed.attachments),
            "attachment_ids": [],

            "in_reply_to": parsed.in_reply_to,
            "references": parsed.references,
            "thread_id": thread_id,
            "conversation_topic": parsed.conversation_topic,

            "folder_path": parsed.folder_path,
            "importance": importance,
            "size_bytes": (
                len(parsed.body_text_raw) + len(parsed.body_html)
                if (parsed.body_text_raw or parsed.body_html)
                else 0
            ),
            "headers_raw": parsed.headers_raw,

            "pst_source": {
                "file_name": pst_path.name,
                "file_sha256": pst_sha,
            },
            "ingested_at": datetime.now(timezone.utc),
            "ingestion_run_id": run_id,
        }
