"""Gmail backfill — resumable, throttle-safe, and provably complete.

Completeness strategy
---------------------
The mailbox holds ~255k messages, of which only a few hundred are business
records. Scanning every message would be slow and pointless, so the backfill
runs **targeted queries** built from the registry: one pass per known external
counterparty, plus a pass over the ``rakesh@mtreh.com`` send-as alias traffic
that only exists inside Gmail.

Every candidate id is then re-checked by the participant filter, so a query that
over-matches cannot smuggle anything in. The queries decide what we *look at*;
the filter decides what we *keep*.

Checkpointing stores the set of processed provider ids per query, so an
interrupted run resumes without re-fetching, and a re-run is a no-op.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set

from mangotree.config.registry import (
    AMBIGUOUS_ALIASES,
    INGESTED_MAILBOXES,
    PEOPLE,
    PROPERTIES,
    Side,
    normalize_text,
)
from mangotree.core.logging import logger
from mangotree.ingest.gmail_client import GmailClient
from mangotree.ingest.pipeline import EmailPipeline
from mangotree.storage.mongo import Mongo


def _quote(addr: str) -> str:
    return addr.strip().lower()


#: Admin directive: ingest ONLY the Inbox and Sent folders. Archive, Trash,
#: Spam and label-only mail are out of scope — the record of record is what was
#: actually received and what was actually sent.
FOLDER_SCOPE = "(in:inbox OR in:sent)"

#: Folders whose messages may be stored. Enforced again at write time so a query
#: change can never quietly widen the corpus.
ALLOWED_FOLDERS = {"INBOX", "SENT"}


def build_queries(since: str) -> List[Dict[str, str]]:
    """Queries covering every way the admin rule can admit a message.

    There is one query family per rule, and the mapping is deliberate — a rule
    with no corresponding query is a rule that silently never fires, because
    Gmail holds 255k messages and the backfill only ever looks at what a query
    returns.

    * **rule 2** — one pass per external counterparty address.
    * **rule 3** — one pass per property, matching aliases in the subject. This
      is the only way a message with no registered participant can be found.
    * plus the ``rakesh@mtreh.com`` send-as sweep, whose traffic exists nowhere
      but this Gmail account.

    Queries decide what we *look at*; ``participants.decide`` decides what we
    *keep*, so an over-matching query cannot widen the corpus.
    """
    queries: List[Dict[str, str]] = []

    # -- rule 2: known counterparties ---------------------------------
    for person in PEOPLE:
        if person.side is not Side.EXTERNAL:
            continue
        for addr in person.all_addresses:
            a = _quote(addr)
            queries.append({
                "name": f"party:{a}",
                "q": f"after:{since} {FOLDER_SCOPE} "
                     f"(from:{a} OR to:{a} OR cc:{a} OR bcc:{a})",
            })

    # -- rule 3: property named in the subject -------------------------
    for prop in PROPERTIES:
        aliases = sorted(
            {
                alias.strip()
                for alias in (prop.canonical_address, *prop.aliases)
                if alias.strip()
                and normalize_text(alias) not in AMBIGUOUS_ALIASES
            },
            key=len,
            reverse=True,
        )
        if not aliases:
            continue
        # Quoted so multi-word aliases match as phrases rather than loose terms.
        terms = " OR ".join(f'"{alias}"' for alias in aliases)
        queries.append({
            "name": f"subject:{prop.property_id}",
            "q": f"after:{since} {FOLDER_SCOPE} subject:({terms})",
        })

    # The send-as alias: mail Rakesh Sir sent as rakesh@mtreh.com lives ONLY in
    # this Gmail account. Outlook can never return it.
    queries.append({
        "name": "alias:rakesh@mtreh.com",
        "q": f"after:{since} {FOLDER_SCOPE} "
             f"(from:rakesh@mtreh.com OR to:rakesh@mtreh.com "
             f"OR cc:rakesh@mtreh.com OR bcc:rakesh@mtreh.com)",
    })

    return queries


@dataclass
class BackfillReport:
    run_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    candidates_seen: int = 0
    unique_candidates: int = 0
    stats: Optional[dict] = None
    per_query: Dict[str, int] = None  # type: ignore[assignment]


class GmailBackfill:
    def __init__(
        self,
        mongo: Mongo,
        client: GmailClient,
        *,
        since: str,
        mailbox: Optional[str] = None,
    ) -> None:
        self.mongo = mongo
        self.client = client
        self.since = since
        self.mailbox = (mailbox or client.address or "unknown").lower()
        self.pipeline = EmailPipeline(mongo)

    # ------------------------------------------------------------------
    def _checkpoint_key(self, query_name: str) -> str:
        return f"gmail::{self.mailbox}::{query_name}::since={self.since}"

    def _done_ids(self, query_name: str) -> Set[str]:
        doc = self.mongo.get_checkpoint(self._checkpoint_key(query_name))
        return set(doc.get("processed_ids", []) or [])

    def _save_done(self, query_name: str, ids: Set[str], complete: bool) -> None:
        self.mongo.set_checkpoint(
            self._checkpoint_key(query_name),
            processed_ids=sorted(ids),
            complete=complete,
            updated_at=datetime.now(timezone.utc),
            mailbox=self.mailbox,
        )

    # ------------------------------------------------------------------
    def run(self, *, limit: Optional[int] = None, resume: bool = True) -> BackfillReport:
        report = BackfillReport(
            run_id=self.pipeline.run_id,
            started_at=datetime.now(timezone.utc),
            per_query={},
        )
        self.mongo.runs.insert_one({
            "run_id": report.run_id,
            "kind": "gmail_backfill",
            "mailbox": self.mailbox,
            "since": self.since,
            "started_at": report.started_at,
            "status": "running",
        })

        queries = build_queries(self.since)
        logger.info(
            "Gmail backfill starting: mailbox=%s since=%s queries=%d",
            self.mailbox, self.since, len(queries),
        )

        # Collect candidate ids across all queries first so the same message
        # matched by several parties is fetched exactly once.
        candidates: Dict[str, str] = {}   # message_id -> thread_id
        for query in queries:
            found = 0
            try:
                for item in self.client.iter_message_ids(query=query["q"]):
                    candidates.setdefault(item["id"], item.get("threadId"))
                    found += 1
                    if limit and len(candidates) >= limit:
                        break
            except Exception as exc:
                logger.error("Query failed (%s): %s", query["name"], exc)
                self.pipeline._record_error("gmail_list", query["name"], exc)
            report.per_query[query["name"]] = found
            report.candidates_seen += found
            logger.info("  %-45s %5d matches", query["name"], found)
            if limit and len(candidates) >= limit:
                logger.info("  limit=%s reached, stopping discovery", limit)
                break

        report.unique_candidates = len(candidates)
        logger.info(
            "Discovery complete: %d matches -> %d unique messages",
            report.candidates_seen, report.unique_candidates,
        )

        processed = self._done_ids("all") if resume else set()
        if processed:
            logger.info("Resuming: %d messages already processed", len(processed))

        pending = [mid for mid in candidates if mid not in processed]

        # Fetching is network-bound and independent per message, so it runs in a
        # small thread pool. Processing stays single-threaded and ordered: the
        # thread-stitching union-find and the Mongo upserts are not designed for
        # concurrent mutation, and ordering keeps runs reproducible.
        for index, message in enumerate(self._fetch_concurrently(pending), start=1):
            if message is None:
                continue

            labels = message.get("label_ids", [])
            folder = self._folder_from_labels(labels)
            if folder not in ALLOWED_FOLDERS:
                # Belt-and-braces: the query already scopes to Inbox/Sent, but a
                # message can be relabelled between listing and fetching.
                self.pipeline.stats.skip("skip_out_of_folder_scope")
                processed.add(message["id"])
                continue

            try:
                self.pipeline.process_raw_email(
                    message["raw"],
                    mailbox=self.mailbox,
                    provider="gmail",
                    provider_id=message["id"],
                    provider_thread_id=message.get("thread_id"),
                    labels=labels,
                    folder=folder,
                )
            except Exception as exc:
                self.pipeline._record_error("gmail_process", message.get("id", "?"), exc)

            processed.add(message["id"])
            if index % 25 == 0:
                self._save_done("all", processed, complete=False)
                logger.info(
                    "  progress %d/%d  ingested=%d skipped=%d review=%d",
                    index, len(pending), self.pipeline.stats.ingested,
                    self.pipeline.stats.total_skipped, self.pipeline.stats.review,
                )

        self._save_done("all", processed, complete=True)

        report.finished_at = datetime.now(timezone.utc)
        report.stats = self.pipeline.stats.as_dict()
        self.mongo.runs.update_one(
            {"run_id": report.run_id},
            {"$set": {
                "status": "complete",
                "finished_at": report.finished_at,
                "candidates_seen": report.candidates_seen,
                "unique_candidates": report.unique_candidates,
                "per_query": report.per_query,
                **report.stats,
            }},
        )
        return report

    # ------------------------------------------------------------------
    def _fetch_concurrently(self, message_ids: Sequence[str], workers: int = 8):
        """Yield fetched messages in batches, fetching in parallel.

        Order within a batch is preserved so the run stays reproducible. A fetch
        failure yields ``None`` for that id and is dead-lettered, never silently
        dropped.
        """
        if not message_ids:
            return

        batch_size = workers * 4
        for start in range(0, len(message_ids), batch_size):
            batch = list(message_ids[start:start + batch_size])
            results: Dict[str, Optional[dict]] = {}

            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(self._safe_get_raw, mid): mid for mid in batch
                }
                for future in as_completed(future_map):
                    mid = future_map[future]
                    try:
                        results[mid] = future.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        self.pipeline._record_error("gmail_fetch", mid, exc)
                        results[mid] = None

            for mid in batch:
                yield results.get(mid)

    def _safe_get_raw(self, message_id: str) -> Optional[dict]:
        try:
            return self.client.get_raw(message_id)
        except Exception as exc:
            self.pipeline._record_error("gmail_fetch", message_id, exc)
            return None

    # ------------------------------------------------------------------
    @staticmethod
    def _folder_from_labels(labels: Sequence[str]) -> str:
        upper = {str(l).upper() for l in labels}
        if "DRAFT" in upper:
            return "DRAFTS"
        if "SENT" in upper:
            return "SENT"
        if "TRASH" in upper:
            return "TRASH"
        if "SPAM" in upper:
            return "SPAM"
        if "INBOX" in upper:
            return "INBOX"
        return "ARCHIVE"
