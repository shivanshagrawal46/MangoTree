"""Drives Opus 5 segregation across the corpus and records what it decided.

Resumable by construction: an artifact carries ``segregation.model`` once it has
been decided, and the selector skips anything already stamped with the current
model. An interrupted run resumes where it stopped and a re-run is a no-op, which
matters when the work is billed per call.

Every decision is written in full — the properties, the confidence, the model's
own reasoning and whether a fallback supplied the answer. A property assignment
that cannot be explained months later is not worth much in a lending file.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.resolve.segregator import (
    ItemDecision,
    PropertySegregator,
    SegregationResult,
    apply_fallback,
)
from mangotree.storage.mongo import Mongo

#: Emails in flight. Opus 5 is the slowest and most expensive model in the stack,
#: so this is the main throughput lever. Anthropic's per-minute limits, not our
#: machine, set the ceiling.
#:
#: Raised from 6 once the segregator retried rate limits instead of dropping the
#: email: over-asking now costs a backoff sleep rather than a missing property
#: decision, so the ceiling can be probed safely.
#:
#: Raised again to 30 on 2026-09-02: this key is on the top Anthropic tier, where
#: 10 in flight left the limit largely unused and made a 3,440-email run an
#: 80-minute one. The stage is latency-bound — each call waits ~14s on Opus — so
#: this is wall-clock time bought directly.
CONCURRENCY = 30

#: Emails queued per window before results are drained and written. Deep enough
#: to keep every worker busy across a window boundary, shallow enough that a
#: crash costs seconds of billed work rather than the whole submission phase.
WINDOW = CONCURRENCY * 4


@dataclass
class SegregationStats:
    considered: int = 0
    called: int = 0
    emails_assigned: int = 0
    attachments_assigned: int = 0
    common_store: int = 0
    unresolved: int = 0
    fallback_applied: int = 0
    out_of_scope_named: int = 0
    review_queued: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class SegregationRunner:
    def __init__(self, mongo: Mongo, api_key: str, *, model: Optional[str] = None):
        self.mongo = mongo
        self.segregator = PropertySegregator(api_key, model=model)
        self.stats = SegregationStats()
        self.run_id = f"seg-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"

    # ------------------------------------------------------------------
    def _pending(self, limit: Optional[int]) -> List[dict]:
        query = {
            "source_type": "email",
            "segregation.model": {"$ne": self.segregator.model},
        }
        cursor = self.mongo.artifacts.find(query).sort("date", 1)
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    def _attachments_of(self, email: dict) -> List[dict]:
        """Attachments point up at their parent, so the join runs that way.

        An attachment can have several parents — the same PDF forwarded twice is
        one artifact by SHA-256 — which is exactly why the link is a list on the
        attachment rather than a list on the email.

        Matched on ``source_types`` rather than ``source_type``: 122 attachments
        also live in the disk corpus, and the disk pass wrote ``source_type``
        last. Keyed on the singular field they were invisible here, so their
        parent email was judged without the ALTA statement or title search it
        was carrying.
        """
        cursor = self.mongo.artifacts.find(
            {"source_types": "attachment", "parent_email_shas": email["sha256"]},
            # Projected, not whole documents: ``extraction.detail`` carries the
            # per-page vision output, which is megabytes on a long title package
            # and none of it is needed here.
            {"sha256": 1, "filename": 1, "content_type": 1, "text": 1},
        ).sort("filename", 1)
        return [
            {
                "sha256": doc["sha256"],
                "filename": doc.get("filename"),
                "content_type": doc.get("content_type"),
                # The extractor writes the document body to the top-level
                # ``text`` field; ``extraction`` holds only status and metrics.
                # Reading ``extraction.text`` here returned "" for every
                # attachment in the corpus, which would have handed Opus 5 a
                # list of filenames and no documents.
                "text": doc.get("text") or "",
            }
            for doc in cursor
        ]

    def _attachments_for(self, emails: List[dict]) -> Dict[str, List[dict]]:
        """Attachments for a whole window of emails, in one query.

        The per-email version costs a round trip each — 284ms against Atlas,
        which is 16 minutes across this corpus and dwarfs the work it feeds. One
        ``$in`` returns the same rows in a single trip.
        """
        shas = [email["sha256"] for email in emails]
        by_parent: Dict[str, List[dict]] = {sha: [] for sha in shas}
        wanted = set(shas)

        cursor = self.mongo.artifacts.find(
            {"source_types": "attachment", "parent_email_shas": {"$in": shas}},
            {"sha256": 1, "filename": 1, "content_type": 1, "text": 1,
             "parent_email_shas": 1},
        ).sort("filename", 1)

        for doc in cursor:
            item = {
                "sha256": doc["sha256"],
                "filename": doc.get("filename"),
                "content_type": doc.get("content_type"),
                "text": doc.get("text") or "",
            }
            # One attachment can hang off several emails, and the $in matched on
            # any of them — so it is added to each parent present in this window.
            for parent in doc.get("parent_email_shas") or []:
                if parent in wanted:
                    by_parent[parent].append(item)
        return by_parent

    @staticmethod
    def _email_payload(email: dict, attachments: List[dict]) -> dict:
        people = email.get("participants") or {}

        def joined(key: str) -> str:
            return ", ".join(people.get(key) or [])

        return {
            "sha256": email.get("sha256"),
            "subject": email.get("subject"),
            "date": str(email.get("date") or ""),
            "from": joined("from"),
            "to": joined("to"),
            "cc": joined("cc"),
            "body": email.get("body_clean") or "",
            # Deterministic matches travel as hints only; the model is told so.
            # Candidates are included as well as accepted ids, because a match
            # that fell below the auto-assign bar is still worth the model
            # seeing — it just is not worth acting on unreviewed.
            "hints": sorted(
                set(email.get("property_ids") or [])
                | {
                    c.get("property_id")
                    for c in (email.get("property_candidates") or [])
                    if c.get("property_id")
                }
            ),
        }

    # ------------------------------------------------------------------
    def run(self, *, limit: Optional[int] = None) -> SegregationStats:
        pending = self._pending(limit)
        self.stats.considered = len(pending)
        logger.info(
            "Segregation starting: %d email(s) with %s (concurrency=%d)",
            len(pending), self.segregator.model, CONCURRENCY,
        )
        if not pending:
            return self.stats

        self.mongo.runs.insert_one({
            "run_id": self.run_id,
            "kind": "segregation",
            "model": self.segregator.model,
            "pending": len(pending),
            "started_at": datetime.now(timezone.utc),
            "status": "running",
        })

        started = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            # Submitted in windows rather than all at once. Submitting the whole
            # corpus first meant no decision was written until every email had
            # been queued — 16 minutes on this corpus — so a crash in that window
            # threw away every Opus call it had already paid for. A window also
            # bounds how many full email bodies and attachment texts are held in
            # memory at once.
            for start in range(0, len(pending), WINDOW):
                window = pending[start:start + WINDOW]
                attachments_by_email = self._attachments_for(window)

                futures = {}
                for email in window:
                    attachments = attachments_by_email.get(email["sha256"], [])
                    payload = self._email_payload(email, attachments)
                    future = pool.submit(self.segregator.segregate, payload, attachments)
                    futures[future] = (email, attachments, payload)

                for future in as_completed(futures):
                    email, attachments, payload = futures[future]
                    done += 1
                    try:
                        result = future.result()
                    except Exception as exc:
                        self.stats.errors += 1
                        logger.error("Segregation failed for %s: %s", email.get("sha256", "?")[:12], exc)
                        continue

                    try:
                        self._persist(email, attachments, payload, result)
                    except Exception as exc:
                        self.stats.errors += 1
                        logger.error("Persisting segregation failed: %s", exc)

                    if done % 25 == 0:
                        rate = done / max(1e-6, time.time() - started)
                        remaining = (len(pending) - done) / max(1e-6, rate)
                        logger.info(
                            "  segregated %d/%d  %.1f/s  eta %.0f min  "
                            "assigned=%d common=%d review=%d errors=%d",
                            done, len(pending), rate, remaining / 60,
                            self.stats.emails_assigned, self.stats.common_store,
                            self.stats.review_queued, self.stats.errors,
                        )

        self.stats.input_tokens = self.segregator.input_tokens
        self.stats.output_tokens = self.segregator.output_tokens
        self.mongo.runs.update_one(
            {"run_id": self.run_id},
            {"$set": {
                "status": "complete",
                "finished_at": datetime.now(timezone.utc),
                **self.stats.as_dict(),
            }},
        )
        logger.info("Segregation complete: %s", self.stats.as_dict())
        return self.stats

    # ------------------------------------------------------------------
    def _persist(
        self, email: dict, attachments: List[dict], payload: dict, result: SegregationResult
    ) -> None:
        if result.error and result.parse_failed is False and not result.email.properties:
            # A transport failure is not a decision. Leave the artifact unstamped
            # so a later run retries it rather than treating silence as "common".
            self.stats.errors += 1
            self._queue_review(
                email["sha256"], "segregation_error",
                f"Opus 5 call failed: {result.error}",
            )
            return

        self.stats.called += 1

        decision = apply_fallback(
            result.email, subject=payload.get("subject") or "", body=payload.get("body") or ""
        )
        self._write_decision(email["sha256"], decision, result, is_email=True)

        for attachment in attachments:
            sha = attachment["sha256"]
            att_decision = result.attachments.get(sha) or ItemDecision(
                unresolved=True, reasoning="model returned no entry for this attachment"
            )
            # An attachment the model could not place inherits its email's
            # properties. The covering email is the strongest available evidence
            # for a document that names no address of its own.
            if not att_decision.properties and decision.properties:
                att_decision.properties = list(decision.properties)
                att_decision.fallback_used = "inherited_from_email"
            self._write_decision(sha, att_decision, result, is_email=False)

    def _write_decision(
        self, sha: str, decision: ItemDecision, result: SegregationResult, *, is_email: bool
    ) -> None:
        record = decision.as_dict()
        record.update({
            "model": result.model,
            "run_id": self.run_id,
            "decided_at": datetime.now(timezone.utc),
        })
        if result.parse_failed:
            record["parse_failed"] = True

        properties = list(decision.properties)
        if not is_email:
            # One attachment can hang off several emails — the same PDF forwarded
            # twice is a single artifact by SHA-256 — and each parent produces its
            # own decision. Union rather than overwrite, or whichever email
            # happened to be processed last would erase the other's answer.
            existing = self.mongo.artifacts.find_one(
                {"sha256": sha}, {"property_ids": 1}
            ) or {}
            for pid in existing.get("property_ids") or []:
                if pid not in properties:
                    properties.append(pid)

        self.mongo.artifacts.update_one(
            {"sha256": sha},
            {"$set": {
                "property_ids": properties,
                "scope": "property" if properties else "common",
                "segregation": record,
                "resolution_status": (
                    "needs_review" if decision.needs_review
                    else "resolved" if properties
                    else "no_property"
                ),
            }},
        )

        if decision.properties:
            if is_email:
                self.stats.emails_assigned += 1
            else:
                self.stats.attachments_assigned += 1
        else:
            self.stats.common_store += 1
        if decision.unresolved:
            self.stats.unresolved += 1
        if decision.fallback_used and decision.fallback_used != "none_available":
            self.stats.fallback_applied += 1
        if decision.out_of_scope:
            self.stats.out_of_scope_named += 1

        if decision.needs_review:
            self._queue_review(
                sha,
                "property_unresolved" if decision.unresolved else "property_low_confidence",
                decision.reasoning or "Opus 5 could not place this item.",
                properties=decision.properties,
                confidence=decision.confidence,
                out_of_scope=decision.out_of_scope,
            )

    def _queue_review(
        self, sha: str, kind: str, note: str, **extra
    ) -> None:
        self.mongo.review_queue.update_one(
            {"artifact_sha": sha, "kind": kind},
            {"$set": {
                "artifact_sha": sha,
                "kind": kind,
                "note": note[:1000],
                "run_id": self.run_id,
                "queued_at": datetime.now(timezone.utc),
                "resolved": False,
                **extra,
            }},
            upsert=True,
        )
        self.stats.review_queued += 1
