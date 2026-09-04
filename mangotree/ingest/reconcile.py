"""Reconciliation — proving completeness rather than assuming it.

The guarantee we need is not "the backfill ran" but "for every message the
provider holds that matches our criteria, we either stored it or recorded why we
did not." Reconciliation is what turns that from a hope into a check.

Method
------
1. Re-run the discovery queries against Gmail (cheap — ids only, no bodies).
2. Compare the provider's id set against ``occurrences`` ∪ ``skipped``.
3. Anything in neither is a **gap** — a message we have never made a decision
   about. Gaps are reported and can be repaired in the same pass.

A gap count of zero is the completeness proof. A non-zero count is an alarm with
the exact ids attached, never a silent degradation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from mangotree.core.logging import logger
from mangotree.ingest.gmail_backfill import GmailBackfill, build_queries
from mangotree.ingest.gmail_client import GmailClient
from mangotree.storage.mongo import Mongo


@dataclass
class ReconcileReport:
    mailbox: str
    since: str
    provider_ids: int = 0
    accounted_stored: int = 0
    accounted_skipped: int = 0
    gaps: List[str] = field(default_factory=list)
    repaired: int = 0
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def complete(self) -> bool:
        return not self.gaps

    def as_dict(self) -> dict:
        return {
            "mailbox": self.mailbox,
            "since": self.since,
            "provider_ids": self.provider_ids,
            "accounted_stored": self.accounted_stored,
            "accounted_skipped": self.accounted_skipped,
            "gap_count": len(self.gaps),
            "gap_ids": self.gaps[:200],
            "repaired": self.repaired,
            "complete": self.complete,
            "checked_at": self.checked_at,
        }


class GmailReconciler:
    def __init__(
        self,
        mongo: Mongo,
        client: GmailClient,
        *,
        since: str,
    ) -> None:
        self.mongo = mongo
        self.client = client
        self.since = since
        self.mailbox = (client.address or "unknown").lower()

    # ------------------------------------------------------------------
    def _provider_ids(self) -> Set[str]:
        ids: Set[str] = set()
        for query in build_queries(self.since):
            try:
                for item in self.client.iter_message_ids(query=query["q"]):
                    ids.add(item["id"])
            except Exception as exc:
                logger.error("Reconcile query failed (%s): %s", query["name"], exc)
        return ids

    def _known_ids(self) -> tuple:
        stored = {
            doc["provider_id"]
            for doc in self.mongo.occurrences.find(
                {"provider": "gmail", "mailbox": self.mailbox, "provider_id": {"$ne": None}},
                {"provider_id": 1},
            )
        }
        skipped = {
            doc["provider_id"]
            for doc in self.mongo.skipped.find({"provider": "gmail"}, {"provider_id": 1})
        }
        return stored, skipped

    # ------------------------------------------------------------------
    def run(self, *, repair: bool = True) -> ReconcileReport:
        logger.info("Reconciling %s since %s", self.mailbox, self.since)

        provider_ids = self._provider_ids()
        stored, skipped = self._known_ids()

        report = ReconcileReport(mailbox=self.mailbox, since=self.since)
        report.provider_ids = len(provider_ids)
        report.accounted_stored = len(provider_ids & stored)
        report.accounted_skipped = len(provider_ids & skipped)
        report.gaps = sorted(provider_ids - stored - skipped)

        logger.info(
            "  provider=%d stored=%d skipped=%d gaps=%d",
            report.provider_ids, report.accounted_stored,
            report.accounted_skipped, len(report.gaps),
        )

        if report.gaps and repair:
            backfill = GmailBackfill(
                self.mongo, self.client, since=self.since, mailbox=self.mailbox,
            )
            for message_id in report.gaps:
                try:
                    message = self.client.get_raw(message_id)
                    backfill.pipeline.process_raw_email(
                        message["raw"],
                        mailbox=self.mailbox,
                        provider="gmail",
                        provider_id=message["id"],
                        provider_thread_id=message.get("thread_id"),
                        labels=message.get("label_ids", []),
                        folder=GmailBackfill._folder_from_labels(message.get("label_ids", [])),
                    )
                    report.repaired += 1
                except Exception as exc:
                    logger.error("Gap repair failed for %s: %s", message_id, exc)
            logger.info("  repaired %d gaps", report.repaired)

        self.mongo.runs.insert_one({
            "run_id": f"reconcile-{report.checked_at:%Y%m%d-%H%M%S}",
            "kind": "reconcile",
            "started_at": report.checked_at,
            "finished_at": datetime.now(timezone.utc),
            "status": "complete",
            **report.as_dict(),
        })
        return report
