"""Outlook backfill — enumerate, filter, then fetch.

Why this differs in shape from the Gmail backfill
-------------------------------------------------
Gmail can search by participant, so that backfill asks targeted questions and
fetches only the answers. Microsoft Graph has no equivalent — there is no way to
ask "every message involving any of these 37 addresses" without either 37 full
enumerations or a ``$search`` that behaves differently from our rule.

So this runs in two passes over the same folders:

1. **Enumerate envelopes.** Sender, recipients, subject and date only, 100 per
   request. Cheap, and it covers every message in scope rather than only the
   ones a query happened to match.
2. **Fetch full MIME for what qualifies.** Roughly 2,700 of 14,000, so the
   expensive call is only paid where it earns its keep.

The rule is applied twice and that is deliberate. The envelope pass uses it to
decide what to fetch; the pipeline applies it again to the real MIME headers,
which are authoritative. The two can only disagree by *widening* — a Bcc header
present in the raw message but absent from the Graph projection — and widening
is safe. A message the envelope pass wrongly rejected would be invisible, which
is why the projection deliberately includes ``bccRecipients``.

Folder scope is wider than "Inbox and Sent Items" because the folder census
proved that reading literally would drop 448 qualifying messages: Graph's
well-known ``sentitems`` excludes its own child folders, and one property has a
folder of its own at the mailbox root.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Sequence, Set, Tuple

from mangotree.core.logging import logger
from mangotree.ingest.graph_client import GraphClient
from mangotree.ingest.participants import build_participants, decide
from mangotree.ingest.pipeline import EmailPipeline
from mangotree.storage.mongo import Mongo

#: Every folder holding in-scope correspondence, measured 2026-09-02.
#: The four beyond Inbox/Sent Items carry 448 qualifying messages between them.
SCOPED_FOLDERS: Tuple[str, ...] = (
    "Inbox",
    "Sent Items",
    "Briardale Tampa",
    "Sent Items/Forwarded to JP Sir",
    "Sent Items/Forwarded to Neha",
    "Sent Items/RB Sir to guide",
)

#: Folder path -> the value stored on the occurrence record. Direction is
#: decided folder-first, so a sent subfolder must still read as SENT.
_DIRECTION_FOLDER = {
    "inbox": "INBOX",
    "sent items": "SENT",
    "briardale tampa": "INBOX",
}


def direction_folder_for(path: str) -> str:
    key = path.strip().lower()
    if key in _DIRECTION_FOLDER:
        return _DIRECTION_FOLDER[key]
    return "SENT" if key.startswith("sent") else "INBOX"


@dataclass
class OutlookReport:
    run_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    enumerated: int = 0
    qualified: int = 0
    fetched: int = 0
    fetch_failures: int = 0
    per_folder: Dict[str, dict] = field(default_factory=dict)
    stats: Optional[dict] = None


class OutlookBackfill:
    def __init__(
        self,
        mongo: Mongo,
        client: GraphClient,
        *,
        since: datetime,
        pipeline: Optional[EmailPipeline] = None,
    ) -> None:
        self.mongo = mongo
        self.client = client
        self.since = since
        self.mailbox = client.mailbox
        self.pipeline = pipeline or EmailPipeline(mongo)

    # ------------------------------------------------------------------
    def _checkpoint_key(self, folder: str) -> str:
        return f"outlook::{self.mailbox}::{folder}::since={self.since:%Y-%m-%d}"

    def _done_ids(self, folder: str) -> Set[str]:
        doc = self.mongo.get_checkpoint(self._checkpoint_key(folder))
        return set(doc.get("processed_ids", []) or [])

    def _save_done(self, folder: str, ids: Set[str], complete: bool) -> None:
        self.mongo.set_checkpoint(
            self._checkpoint_key(folder),
            processed_ids=sorted(ids),
            complete=complete,
            updated_at=datetime.now(timezone.utc),
            mailbox=self.mailbox,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _envelope_addresses(message: dict) -> List[str]:
        out: List[str] = []
        sender = (message.get("from") or {}).get("emailAddress", {}).get("address")
        if sender:
            out.append(sender.strip().lower())
        for key in ("toRecipients", "ccRecipients", "bccRecipients"):
            for entry in message.get(key) or []:
                addr = (entry.get("emailAddress") or {}).get("address")
                if addr:
                    out.append(addr.strip().lower())
        return out

    def _qualifying_envelopes(self, folder_id: str, date_field: str) -> Iterator[dict]:
        """Envelopes the rule admits. The expensive MIME fetch follows only these."""
        for message in self.client.survey_messages(
            folder_id, date_field=date_field, since=self.since
        ):
            sender = (message.get("from") or {}).get("emailAddress", {}).get("address", "")
            others = [a for a in self._envelope_addresses(message) if a != sender.lower()]
            # The rule classifies each address by side and never distinguishes
            # To from Cc from Bcc, so collapsing the recipients into one header
            # is faithful and avoids rebuilding Graph's structure as RFC822.
            participants = build_participants({
                "From": sender,
                "To": ", ".join(others),
            })
            if decide(participants, subject=message.get("subject") or "").ingest:
                yield message

    # ------------------------------------------------------------------
    def run(self, *, limit: Optional[int] = None, resume: bool = True) -> OutlookReport:
        report = OutlookReport(
            run_id=self.pipeline.run_id, started_at=datetime.now(timezone.utc)
        )
        self.mongo.runs.insert_one({
            "run_id": report.run_id,
            "kind": "outlook_backfill",
            "mailbox": self.mailbox,
            "since": self.since,
            "folders": list(SCOPED_FOLDERS),
            "started_at": report.started_at,
            "status": "running",
        })
        logger.info(
            "Outlook backfill starting: mailbox=%s since=%s folders=%d",
            self.mailbox, self.since.date(), len(SCOPED_FOLDERS),
        )

        census = {row["path"]: row for row in self.client.folder_census()}

        for path in SCOPED_FOLDERS:
            folder = census.get(path)
            if not folder:
                logger.error("Folder not found, skipping: %s", path)
                report.per_folder[path] = {"error": "not found"}
                continue

            date_field = (
                "sentDateTime" if path.lower().startswith("sent") else "receivedDateTime"
            )
            processed = self._done_ids(path) if resume else set()
            counts = {"enumerated": 0, "qualified": 0, "fetched": 0, "failed": 0}

            logger.info("  %-38s enumerating (%d already done)", path, len(processed))

            pending: List[dict] = []
            for envelope in self._qualifying_envelopes(folder["id"], date_field):
                counts["qualified"] += 1
                if envelope["id"] not in processed:
                    pending.append(envelope)
                if limit and len(pending) >= limit:
                    break
            counts["enumerated"] = folder.get("count", 0)

            logger.info(
                "  %-38s %d qualify, %d to fetch", path, counts["qualified"], len(pending)
            )

            folder_label = direction_folder_for(path)
            for index, (envelope, raw) in enumerate(
                self._fetch_concurrently(pending), start=1
            ):
                if raw is None:
                    counts["failed"] += 1
                    report.fetch_failures += 1
                    processed.add(envelope["id"])
                    continue
                try:
                    self.pipeline.process_raw_email(
                        raw,
                        mailbox=self.mailbox,
                        provider="outlook",
                        provider_id=envelope["id"],
                        provider_thread_id=envelope.get("conversationId"),
                        labels=[path],
                        folder=folder_label,
                    )
                    counts["fetched"] += 1
                except Exception as exc:
                    self.pipeline._record_error(
                        "outlook_process", envelope.get("id", "?"), exc
                    )
                processed.add(envelope["id"])

                if index % 25 == 0:
                    self._save_done(path, processed, complete=False)
                    logger.info(
                        "    %-36s %d/%d  ingested=%d skipped=%d",
                        path, index, len(pending),
                        self.pipeline.stats.ingested, self.pipeline.stats.total_skipped,
                    )

            self._save_done(path, processed, complete=True)
            report.per_folder[path] = counts
            report.qualified += counts["qualified"]
            report.fetched += counts["fetched"]
            logger.info("  %-38s done: %s", path, counts)

        report.finished_at = datetime.now(timezone.utc)
        report.stats = self.pipeline.stats.as_dict()
        self.mongo.runs.update_one(
            {"run_id": report.run_id},
            {"$set": {
                "status": "complete",
                "finished_at": report.finished_at,
                "qualified": report.qualified,
                "fetched": report.fetched,
                "fetch_failures": report.fetch_failures,
                "per_folder": report.per_folder,
                **report.stats,
            }},
        )
        return report

    # ------------------------------------------------------------------
    def _fetch_concurrently(self, envelopes: Sequence[dict], workers: int = 6):
        """Yield ``(envelope, raw_bytes|None)`` preserving input order.

        Graph throttles harder than Gmail, so the pool is smaller and the client's
        own Retry-After handling does the backing off.
        """
        if not envelopes:
            return
        batch_size = workers * 4
        for start in range(0, len(envelopes), batch_size):
            batch = list(envelopes[start:start + batch_size])
            results: Dict[str, Optional[bytes]] = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(self._safe_mime, env["id"]): env["id"] for env in batch
                }
                for future in as_completed(future_map):
                    message_id = future_map[future]
                    try:
                        results[message_id] = future.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        self.pipeline._record_error("outlook_fetch", message_id, exc)
                        results[message_id] = None
            for envelope in batch:
                yield envelope, results.get(envelope["id"])

    def _safe_mime(self, message_id: str) -> Optional[bytes]:
        try:
            return self.client.raw_mime(message_id)
        except Exception as exc:
            self.pipeline._record_error("outlook_fetch", message_id, exc)
            return None
