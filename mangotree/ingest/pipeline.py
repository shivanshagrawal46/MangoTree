"""The ingestion pipeline — one artifact's journey, end to end.

Stages (each idempotent, keyed by ``sha256`` so a re-run never duplicates):

    raw bytes -> parse -> participant filter -> store original -> clean
              -> dedup -> direction -> thread -> property resolve
              -> artifact upsert -> occurrence upsert -> review queue

Skipped mail is *counted* in ``skipped`` (provider id + reason only, never
content), which is what lets the reconciliation sweep prove the difference
between "deliberately excluded" and "missed".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.clean.cleaner import clean_body
from mangotree.config.registry import person_for_address
from mangotree.core.hashing import content_fingerprint
from mangotree.core.logging import logger
from mangotree.ingest.direction import Direction, resolve_direction
from mangotree.ingest.mime_parser import ParsedEmail, parse_rfc822
from mangotree.ingest.participants import Decision, build_participants, decide
from mangotree.ingest.threading import ThreadIndex, thread_key_for
from mangotree.resolve.property_resolver import ResolutionStatus, resolve_property
from mangotree.storage.mongo import Mongo


@dataclass
class IngestStats:
    seen: int = 0
    ingested: int = 0
    duplicates: int = 0
    skipped: Dict[str, int] = field(default_factory=dict)
    review: int = 0
    attachments: int = 0
    errors: int = 0
    discovery_candidates: Dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def candidate(self, address: str) -> None:
        self.discovery_candidates[address] = self.discovery_candidates.get(address, 0) + 1

    @property
    def total_skipped(self) -> int:
        return sum(self.skipped.values())

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seen": self.seen,
            "ingested": self.ingested,
            "duplicates": self.duplicates,
            "skipped_total": self.total_skipped,
            "skipped_by_reason": dict(self.skipped),
            "review": self.review,
            "attachments": self.attachments,
            "errors": self.errors,
            "discovery_candidates": dict(sorted(
                self.discovery_candidates.items(), key=lambda kv: kv[1], reverse=True
            )[:50]),
        }


class EmailPipeline:
    def __init__(
        self,
        mongo: Mongo,
        *,
        run_id: Optional[str] = None,
    ) -> None:
        self.mongo = mongo
        self.run_id = run_id or datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
        self.threads = ThreadIndex()
        self.stats = IngestStats()

    # ------------------------------------------------------------------
    def process_raw_email(
        self,
        raw_bytes: bytes,
        *,
        mailbox: str,
        provider: str,
        provider_id: Optional[str] = None,
        provider_thread_id: Optional[str] = None,
        labels: Sequence[str] = (),
        folder: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> Optional[str]:
        """Run one message through the pipeline. Returns the artifact sha, or None."""
        self.stats.seen += 1
        try:
            parsed = parse_rfc822(raw_bytes)
        except Exception as exc:
            self._record_error("parse", provider_id or "?", exc)
            return None

        participants = build_participants(parsed.headers)
        verdict = decide(participants, subject=parsed.subject)

        for candidate in verdict.discovery_candidates:
            self.stats.candidate(candidate)

        if not verdict.ingest:
            self._record_skip(provider, provider_id, verdict, parsed)
            self.stats.skip(verdict.decision.value)
            return None

        return self._store(
            parsed,
            raw_bytes,
            participants=participants,
            verdict_reason=verdict.reason,
            mailbox=mailbox,
            provider=provider,
            provider_id=provider_id,
            provider_thread_id=provider_thread_id,
            labels=labels,
            folder=folder,
            source_path=source_path,
        )

    # ------------------------------------------------------------------
    def _store(
        self,
        parsed: ParsedEmail,
        raw_bytes: bytes,
        *,
        participants,
        verdict_reason: str,
        mailbox: str,
        provider: str,
        provider_id: Optional[str],
        provider_thread_id: Optional[str],
        labels: Sequence[str],
        folder: Optional[str],
        source_path: Optional[str],
    ) -> str:
        sha = parsed.raw_sha256
        cleaned = clean_body(parsed.body_text, parsed.body_html)

        direction = resolve_direction(
            mailbox=mailbox,
            from_addrs=participants.from_addrs,
            labels=labels,
            folder=folder,
        )

        # Same message, second mailbox. A mail addressed to both rakesh@mtreh.com
        # and the Gmail account is delivered twice with the same Message-ID but
        # different bytes (each hop adds its own Received/Delivered-To headers),
        # so the SHA-256 key alone stores it twice — 19 times in the backfill. The
        # Message-ID is the identity that survives delivery; when it is already
        # here, the new copy becomes an *occurrence* of the existing artifact (so
        # "seen in both mailboxes" is recorded) and nothing downstream runs twice.
        same_message = None
        if parsed.message_id:
            same_message = self.mongo.artifacts.find_one(
                {"internet_message_id": parsed.message_id, "source_type": "email",
                 "sha256": {"$ne": sha}},
                {"sha256": 1},
            )
        if same_message is not None:
            self._upsert_occurrence(
                sha=same_message["sha256"], mailbox=mailbox, provider=provider,
                provider_id=provider_id,
                folder=folder or ("SENT" if direction.direction is Direction.SENT else "INBOX"),
                labels=labels, direction=direction, date=parsed.date, source_path=source_path,
            )
            self.mongo.artifacts.update_one(
                {"sha256": same_message["sha256"]},
                {"$addToSet": {"also_seen_in": {"mailbox": mailbox, "provider": provider, "sha256": sha}}},
            )
            self.stats.duplicates += 1
            return same_message["sha256"]

        thread_key = thread_key_for(
            self.threads,
            message_id=parsed.message_id,
            references=parsed.references,
            in_reply_to=parsed.in_reply_to,
            subject=parsed.subject,
            participants=participants.all_addrs,
            provider_thread_id=provider_thread_id,
        )

        existing = self.mongo.artifacts.find_one({"sha256": sha}, {"property_ids": 1})
        is_duplicate = existing is not None

        # Thread inheritance: a reply with no property signal of its own adopts
        # the resolution its conversation already has.
        thread_props: List[str] = []
        if thread_key:
            thread_doc = self.mongo.threads.find_one(
                {"thread_key": thread_key}, {"property_ids": 1}
            )
            if thread_doc:
                thread_props = thread_doc.get("property_ids", []) or []

        attachment_names = [a.filename for a in parsed.attachments if not a.likely_logo]

        resolution = resolve_property(
            subject=parsed.subject,
            body=cleaned.full_text,
            filenames=attachment_names,
            thread_property_ids=thread_props,
            person_ids=participants.person_ids,
        )

        # Store the original bytes exactly as received.
        self.mongo.put_original(
            sha,
            raw_bytes,
            filename=f"{sha}.eml",
            metadata={
                "source_type": "email",
                "provider": provider,
                "mailbox": mailbox,
                "provider_id": provider_id,
                "run_id": self.run_id,
            },
        )

        now = datetime.now(timezone.utc)
        artifact = {
            "sha256": sha,
            "source_type": "email",
            "provider": provider,
            "subject": parsed.subject,
            "date": parsed.date,
            "internet_message_id": parsed.message_id,
            "in_reply_to": parsed.in_reply_to,
            "references": parsed.references,
            "thread_key": thread_key,
            "content_fingerprint": content_fingerprint(
                parsed.subject, cleaned.body_clean, ",".join(participants.from_addrs)
            ),
            "participants": {
                "from": participants.from_addrs,
                "to": participants.to_addrs,
                "cc": participants.cc_addrs,
                "bcc": participants.bcc_addrs,
                "rkb": participants.rkb_addrs,
                "external": participants.known_external_addrs,
                "unknown": participants.unknown_addrs,
            },
            "person_ids": participants.person_ids,
            "author_person_id": direction.author_person_id,
            "body_clean": cleaned.body_clean,
            "body_quoted": cleaned.quoted,
            "signature": cleaned.signature,
            "was_html": cleaned.was_html,
            "raw_size": parsed.raw_size,
            "attachment_count": len(parsed.attachments),
            "attachment_names": attachment_names,
            "property_ids": resolution.property_ids if not resolution.needs_review else [],
            "property_candidates": [
                {"property_id": h.property_id, "confidence": round(h.confidence, 3), "signals": h.signals}
                for h in resolution.hits
            ],
            "resolution": {
                "status": resolution.status.value,
                "notes": resolution.notes,
            },
            "ingest_reason": verdict_reason,
            "updated_at": now,
        }

        self.mongo.artifacts.update_one(
            {"sha256": sha},
            {
                "$set": artifact,
                # ``source_type`` is last-writer-wins across ingest passes, so it
                # cannot answer "where did this come from" for bytes that arrived
                # from more than one place. ``source_types`` accumulates instead.
                "$addToSet": {"source_types": "email"},
                "$setOnInsert": {"created_at": now, "first_run_id": self.run_id},
            },
            upsert=True,
        )

        self._store_attachments(parsed, sha, resolution.property_ids)
        self._upsert_occurrence(
            sha=sha,
            mailbox=mailbox,
            provider=provider,
            provider_id=provider_id,
            folder=folder or ("SENT" if direction.direction is Direction.SENT else "INBOX"),
            labels=labels,
            direction=direction,
            date=parsed.date,
            source_path=source_path,
        )
        self._upsert_thread(thread_key, parsed, resolution.property_ids, participants)

        if resolution.needs_review:
            self._queue_review(sha, parsed, resolution)

        if is_duplicate:
            self.stats.duplicates += 1
        else:
            self.stats.ingested += 1
        return sha

    # ------------------------------------------------------------------
    def _store_attachments(
        self, parsed: ParsedEmail, parent_sha: str, property_ids: Sequence[str]
    ) -> None:
        now = datetime.now(timezone.utc)
        for att in parsed.attachments:
            if att.likely_logo:
                continue  # signature logos / tracking pixels never enter the corpus
            if not att.data:
                continue

            self.mongo.put_original(
                att.sha256,
                att.data,
                filename=att.filename,
                metadata={
                    "source_type": "attachment",
                    "parent_email_sha": parent_sha,
                    "content_type": att.content_type,
                    "run_id": self.run_id,
                },
            )
            # Tagged additively: the same PDF can sit in the disk corpus *and*
            # arrive as an attachment, and a later disk pass would otherwise
            # overwrite ``source_type`` and hide it from the attachment join.
            add_to_set: Dict[str, Any] = {
                "parent_email_shas": parent_sha,
                "source_types": "attachment",
            }
            if property_ids:
                add_to_set["property_ids"] = {"$each": list(property_ids)}

            self.mongo.artifacts.update_one(
                {"sha256": att.sha256},
                {
                    "$set": {
                        "sha256": att.sha256,
                        "source_type": "attachment",
                        "filename": att.filename,
                        "content_type": att.content_type,
                        "raw_size": att.size,
                        "date": parsed.date,
                        "updated_at": now,
                    },
                    "$addToSet": add_to_set,
                    "$setOnInsert": {
                        "created_at": now,
                        "first_run_id": self.run_id,
                        # Always present so array queries never hit a missing field.
                        **({} if property_ids else {"property_ids": []}),
                    },
                },
                upsert=True,
            )
            self.stats.attachments += 1

    # ------------------------------------------------------------------
    def _upsert_occurrence(
        self,
        *,
        sha: str,
        mailbox: str,
        provider: str,
        provider_id: Optional[str],
        folder: str,
        labels: Sequence[str],
        direction,
        date: Optional[datetime],
        source_path: Optional[str],
    ) -> None:
        self.mongo.occurrences.update_one(
            {"artifact_sha": sha, "mailbox": mailbox, "folder": folder},
            {
                "$set": {
                    "artifact_sha": sha,
                    "mailbox": mailbox,
                    "provider": provider,
                    "provider_id": provider_id,
                    "folder": folder,
                    "labels": list(labels),
                    "direction": direction.direction.value,
                    "direction_basis": direction.basis,
                    "via_send_as_alias": direction.via_alias,
                    "alias_used": direction.alias_used,
                    "author_person_id": direction.author_person_id,
                    "date": date,
                    "source_path": source_path,
                    "run_id": self.run_id,
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )

    # ------------------------------------------------------------------
    def _upsert_thread(
        self,
        thread_key: str,
        parsed: ParsedEmail,
        property_ids: Sequence[str],
        participants,
    ) -> None:
        if not thread_key:
            return
        update: Dict[str, Any] = {
            "$set": {"thread_key": thread_key, "last_subject": parsed.subject},
            "$addToSet": {
                "artifact_shas": parsed.raw_sha256,
                "participants": {"$each": participants.all_addrs},
                "person_ids": {"$each": participants.person_ids},
            },
            "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            "$inc": {"message_count": 1},
        }
        if property_ids:
            update["$addToSet"]["property_ids"] = {"$each": list(property_ids)}
        if parsed.date:
            update["$max"] = {"last_date": parsed.date}
            update["$min"] = {"first_date": parsed.date}
        self.mongo.threads.update_one({"thread_key": thread_key}, update, upsert=True)

    # ------------------------------------------------------------------
    def _queue_review(self, sha: str, parsed: ParsedEmail, resolution) -> None:
        self.mongo.review_queue.update_one(
            {"artifact_sha": sha, "kind": "property_resolution"},
            {
                "$set": {
                    "artifact_sha": sha,
                    "kind": "property_resolution",
                    "status": "open",
                    "subject": parsed.subject,
                    "date": parsed.date,
                    "resolution_status": resolution.status.value,
                    "candidates": [
                        {
                            "property_id": h.property_id,
                            "canonical": h.canonical,
                            "confidence": round(h.confidence, 3),
                            "signals": h.signals,
                        }
                        for h in resolution.hits
                    ],
                    "notes": resolution.notes,
                    "run_id": self.run_id,
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
        self.stats.review += 1

    # ------------------------------------------------------------------
    def _record_skip(self, provider: str, provider_id: Optional[str], verdict, parsed) -> None:
        """Count a skipped message. Content is never stored — only the fact."""
        if not provider_id:
            return
        self.mongo.skipped.update_one(
            {"provider": provider, "provider_id": provider_id},
            {
                "$set": {
                    "provider": provider,
                    "provider_id": provider_id,
                    "reason": verdict.decision.value,
                    "detail": verdict.reason,
                    "date": parsed.date,
                    "run_id": self.run_id,
                    # Deliberately no subject/body: skipped mail (incl. personal)
                    # never enters the system.
                    "discovery_candidates": verdict.discovery_candidates[:5],
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )

    def _record_error(self, stage: str, key: str, exc: Exception) -> None:
        self.stats.errors += 1
        logger.error("Pipeline error [%s] %s: %s", stage, key, exc)
        self.mongo.errors.insert_one({
            "run_id": self.run_id,
            "stage": stage,
            "key": key,
            "error_type": type(exc).__name__,
            "error": str(exc)[:2000],
            "created_at": datetime.now(timezone.utc),
        })
