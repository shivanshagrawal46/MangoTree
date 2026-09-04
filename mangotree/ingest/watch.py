"""Live mail intake — the backfill's rule and pipeline, applied to what is new.

Every ``MT_POLL_MINUTES`` (default 10) both mailboxes are asked for messages
newer than the last look, minus an overlap window so nothing falls between two
polls. Each candidate goes through exactly the code the backfill used —
``EmailPipeline.process_raw_email`` — which applies the participant rule
(internal-only skipped, registered contact or property-in-subject ingested),
deduplicates by Message-ID and SHA-256, decides direction folder-first, stores
originals and attachments, and stitches threads. Nothing here decides anything
the backfill did not.

Two mailboxes, two providers:

* Gmail rakesh.bhargava@gmail.com — INBOX and SENT (the SENT label is where
  mail sent *as* rakesh@mtreh.com lives; Outlook never sees it).
* Outlook rakesh@mtreh.com — the same scoped folders the backfill covered.

Both are read-only. Processed provider ids are kept per source in
``checkpoints`` so a restart resumes exactly, and every poll writes a row to
``intake_runs`` — seen, ingested, skipped by reason, errors — which the
dashboard shows as the Intake row. A nightly sweep re-asks for the last 72
hours so a missed poll can never mean a missed email.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.ingest.pipeline import EmailPipeline
from mangotree.storage.mongo import Mongo

POLL_MINUTES = int(os.environ.get("MT_POLL_MINUTES", "10"))
#: Each poll looks back this far past the last successful poll, so clock skew and
#: late-arriving mail cannot slip between two polls. Dedup makes overlap free.
OVERLAP_MINUTES = 90
SWEEP_HOURS = 72
GMAIL_LABELS = ("INBOX", "SENT")


@dataclass
class IntakeReport:
    started_at: datetime
    kind: str                       # poll | sweep
    window_start: datetime
    seen: int = 0
    fetched: int = 0
    ingested: int = 0
    skipped: Dict[str, int] = field(default_factory=dict)
    errors: int = 0
    new_email_shas: List[str] = field(default_factory=list)
    per_source: Dict[str, Dict[str, int]] = field(default_factory=dict)
    source_errors: Dict[str, str] = field(default_factory=dict)
    finished_at: Optional[datetime] = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "new_email_shas"} | {"new_emails": len(self.new_email_shas)}


class MailWatcher:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self._gmail = None
        self._graph = None
        self.intake = mongo.db["intake_runs"]
        self.intake.create_index([("started_at", -1)], name="ix_intake_started")

    # ---------------------------------------------------------------- clients
    def gmail(self):
        if self._gmail is None:
            from mangotree.ingest.gmail_client import GmailClient
            self._gmail = GmailClient(client_secret_path=SETTINGS.gmail_client_secret,
                                      token_path=SETTINGS.gmail_token_path).authenticate()
        return self._gmail

    def graph(self):
        if self._graph is None:
            from mangotree.ingest.graph_client import GraphClient
            self._graph = GraphClient.from_settings(SETTINGS)
        return self._graph

    # ------------------------------------------------------------ checkpoints
    def _key(self, source: str) -> str:
        return f"watch::{source}"

    def _done(self, source: str) -> Set[str]:
        doc = self.mongo.get_checkpoint(self._key(source))
        return set(doc.get("processed_ids", []) or [])

    def _save(self, source: str, ids: Set[str], last_ok: datetime) -> None:
        # Keep the id set bounded: anything older than the sweep window can never
        # be re-offered by a poll, so it need not be remembered.
        self.mongo.set_checkpoint(self._key(source), processed_ids=sorted(ids)[-5000:],
                                  last_ok=last_ok, updated_at=datetime.now(timezone.utc))

    def last_ok(self, source: str) -> Optional[datetime]:
        doc = self.mongo.get_checkpoint(self._key(source))
        return doc.get("last_ok")

    # ------------------------------------------------------------------ gmail
    def _poll_gmail(self, pipeline: EmailPipeline, window_start: datetime, report: IntakeReport) -> None:
        source = "gmail"
        client = self.gmail()
        done = self._done(source)
        counts = {"seen": 0, "fetched": 0, "ingested": 0}
        after = window_start.strftime("%Y/%m/%d")
        candidates: Dict[str, str] = {}
        for label in GMAIL_LABELS:
            for item in client.iter_message_ids(query=f"after:{after}", label_ids=[label]):
                candidates.setdefault(item["id"], label)
        counts["seen"] = len(candidates)
        before = pipeline.stats.ingested
        for mid, label in candidates.items():
            if mid in done:
                continue
            try:
                msg = client.get_raw(mid)
            except Exception as exc:
                report.errors += 1
                pipeline._record_error("watch_gmail_fetch", mid, exc)
                continue
            labels = msg.get("label_ids", [])
            from mangotree.ingest.gmail_backfill import GmailBackfill, ALLOWED_FOLDERS
            folder = GmailBackfill._folder_from_labels(labels)
            if folder not in ALLOWED_FOLDERS:
                done.add(mid)
                continue
            counts["fetched"] += 1
            try:
                sha = pipeline.process_raw_email(msg["raw"], mailbox=(client.address or "gmail").lower(), provider="gmail",
                                                 provider_id=mid, provider_thread_id=msg.get("thread_id"), labels=labels, folder=folder)
                if sha:
                    report.new_email_shas.append(sha)
            except Exception as exc:
                report.errors += 1
                pipeline._record_error("watch_gmail_process", mid, exc)
            done.add(mid)
        counts["ingested"] = pipeline.stats.ingested - before
        self._save(source, done, datetime.now(timezone.utc))
        report.per_source[source] = counts

    # ---------------------------------------------------------------- outlook
    def _poll_outlook(self, pipeline: EmailPipeline, window_start: datetime, report: IntakeReport) -> None:
        from mangotree.ingest.outlook_backfill import SCOPED_FOLDERS, OutlookBackfill, direction_folder_for

        source = "outlook"
        client = self.graph()
        helper = OutlookBackfill(self.mongo, client, since=window_start, pipeline=pipeline)
        census = {row["path"]: row for row in client.folder_census()}
        done = self._done(source)
        counts = {"seen": 0, "fetched": 0, "ingested": 0}
        before = pipeline.stats.ingested
        for path in SCOPED_FOLDERS:
            folder = census.get(path)
            if not folder:
                continue
            date_field = "sentDateTime" if path.lower().startswith("sent") else "receivedDateTime"
            for env in helper._qualifying_envelopes(folder["id"], date_field):
                counts["seen"] += 1
                if env["id"] in done:
                    continue
                raw = helper._safe_mime(env["id"])
                if raw is None:
                    report.errors += 1
                    done.add(env["id"])
                    continue
                counts["fetched"] += 1
                try:
                    sha = pipeline.process_raw_email(raw, mailbox=client.mailbox, provider="outlook", provider_id=env["id"],
                                                     provider_thread_id=env.get("conversationId"), labels=[path], folder=direction_folder_for(path))
                    if sha:
                        report.new_email_shas.append(sha)
                except Exception as exc:
                    report.errors += 1
                    pipeline._record_error("watch_outlook_process", env.get("id", "?"), exc)
                done.add(env["id"])
        counts["ingested"] = pipeline.stats.ingested - before
        self._save(source, done, datetime.now(timezone.utc))
        report.per_source[source] = counts

    # ------------------------------------------------------------------- run
    def run(self, *, kind: str = "poll", hours: Optional[float] = None) -> IntakeReport:
        now = datetime.now(timezone.utc)
        if hours is None:
            last = min([d for d in (self.last_ok("gmail"), self.last_ok("outlook")) if d] or [now - timedelta(hours=SWEEP_HOURS)])
            window_start = last - timedelta(minutes=OVERLAP_MINUTES)
        else:
            window_start = now - timedelta(hours=hours)
        report = IntakeReport(started_at=now, kind=kind, window_start=window_start)
        pipeline = EmailPipeline(self.mongo, run_id=f"watch-{now:%Y%m%d-%H%M%S}")
        logger.info("Mail intake (%s): window from %s", kind, window_start.strftime("%Y-%m-%d %H:%M"))

        for source, fn in (("gmail", self._poll_gmail), ("outlook", self._poll_outlook)):
            try:
                fn(pipeline, window_start, report)
            except Exception as exc:
                logger.exception("intake %s failed", source)
                report.source_errors[source] = f"{type(exc).__name__}: {exc}"[:300]
                report.errors += 1

        report.seen = sum(c.get("seen", 0) for c in report.per_source.values())
        report.fetched = sum(c.get("fetched", 0) for c in report.per_source.values())
        report.ingested = pipeline.stats.ingested
        report.skipped = dict(pipeline.stats.skipped)
        report.finished_at = datetime.now(timezone.utc)
        self.intake.insert_one(report.as_dict() | {"new_email_shas": report.new_email_shas[:200]})
        logger.info("Mail intake done: seen=%d fetched=%d ingested=%d errors=%d", report.seen, report.fetched, report.ingested, report.errors)
        return report

    def status(self) -> dict:
        last = self.intake.find_one({}, sort=[("started_at", -1)])
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        agg = list(self.intake.aggregate([{"$match": {"started_at": {"$gte": today}}},
                                          {"$group": {"_id": None, "seen": {"$sum": "$seen"}, "ingested": {"$sum": "$ingested"}, "errors": {"$sum": "$errors"}, "runs": {"$sum": 1}}}]))
        t = agg[0] if agg else {}
        return {
            "last_run": last, "poll_minutes": POLL_MINUTES,
            "gmail_last_ok": self.last_ok("gmail"), "outlook_last_ok": self.last_ok("outlook"),
            "today": {"runs": t.get("runs", 0), "seen": t.get("seen", 0), "ingested": t.get("ingested", 0), "errors": t.get("errors", 0)},
        }
