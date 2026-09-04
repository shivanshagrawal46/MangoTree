"""Build and persist per-property timelines."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from mangotree.config.models import Seat, model_for
from mangotree.config.registry import PROPERTY_INDEX, deal_type_for
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo
from mangotree.timeline.events import TimelineEvent, document_events
from mangotree.timeline.extractor import EventExtractor, ExtractStats

#: Documents contextualised per model batch.
JOB_BATCH = 24


class TimelineBuilder:
    def __init__(
        self,
        mongo: Mongo,
        *,
        anthropic_api_key: Optional[str] = None,
        model: Optional[str] = None,
        run_id: Optional[str] = None,
        concurrency: int = 30,
    ) -> None:
        # The ANALYST seat, not the workhorse. Timeline extraction is the one
        # stage where a misread date is actively dangerous — it will be used to
        # compute interest, argue a default, or set a deadline — and the events
        # are read out of dense legal and financial prose. Sonnet was cheaper per
        # call and wrong more often: it paraphrased instead of quoting, and the
        # verbatim-quote guard threw away roughly seven of every ten events it
        # proposed. Paying for the better reader is cheaper than discarding 70%.
        model = model or model_for(Seat.ANALYST)
        self.mongo = mongo
        self.run_id = run_id or datetime.now(timezone.utc).strftime("timeline-%Y%m%d-%H%M%S")
        self.extract_stats = ExtractStats()
        self.extractor: Optional[EventExtractor] = None
        if anthropic_api_key:
            self.extractor = EventExtractor(
                anthropic_api_key, model=model, concurrency=concurrency
            )
        self.deterministic = 0
        self.extracted = 0

    # ------------------------------------------------------------------
    @property
    def events(self):
        return self.mongo.db["timeline_events"]

    def ensure_indexes(self) -> None:
        from pymongo import ASCENDING, DESCENDING

        events = self.events
        events.create_index([("event_id", ASCENDING)], name="ux_event_id", unique=True)
        events.create_index(
            [("property_id", ASCENDING), ("occurred_at", ASCENDING)],
            name="ix_property_time",
        )
        events.create_index([("event_type", ASCENDING)], name="ix_event_type")
        events.create_index([("source_sha", ASCENDING)], name="ix_event_source")
        events.create_index([("occurred_at", DESCENDING)], name="ix_event_recent")

    # ------------------------------------------------------------------
    def _write(self, events: Sequence[TimelineEvent]) -> int:
        if not events:
            return 0
        from pymongo import UpdateOne

        operations = []
        for event in events:
            record = event.as_dict()
            record["run_id"] = self.run_id
            operations.append(
                UpdateOne({"event_id": record["event_id"]},
                          {"$set": record}, upsert=True)
            )
        result = self.events.bulk_write(operations, ordered=False)
        return (result.upserted_count or 0) + (result.modified_count or 0)

    # ------------------------------------------------------------------
    def run(
        self,
        *,
        property_ids: Optional[Sequence[str]] = None,
        use_model: bool = True,
        limit: Optional[int] = None,
        force: bool = False,
    ) -> dict:
        self.ensure_indexes()

        # Every source, not just the disk corpus. Restricting this to
        # ``disk_file`` covered 353 artifacts and silently omitted 3,440 emails
        # and 1,161 attachments — which, for a deal that generated little paper,
        # is the entire record of what happened.
        from mangotree.core.sources import ALL_SOURCE_TYPES
        query: dict = {
            "source_type": {"$in": list(ALL_SOURCE_TYPES)},
            "property_ids.0": {"$exists": True},
            # Signature logos carry property ids like anything else, so without
            # this an inline image becomes a dated event — and one of them was
            # attached to twelve properties, so it landed on twelve timelines.
            "is_inline_image": {"$ne": True},
        }
        if property_ids:
            query["property_ids"] = {"$in": list(property_ids)}

        projection = {
            "sha256": 1, "filename": 1, "relative_path": 1, "doc_class": 1,
            "date": 1, "property_ids": 1, "text": 1, "privileged": 1,
            "source_type": 1, "subject": 1, "body_clean": 1, "participants": 1,
        }
        artifacts = list(self.mongo.artifacts.find(query, projection))
        # Emails carry their content in ``body_clean``; the extractor reads
        # ``text``, so it is normalised here rather than in every caller.
        for artifact in artifacts:
            if artifact.get("source_type") == "email" and not artifact.get("text"):
                artifact["text"] = artifact.get("body_clean") or ""
        if limit:
            artifacts = artifacts[:limit]

        logger.info(
            "Timeline: %d artifacts (run %s, model=%s)",
            len(artifacts), self.run_id,
            self.extractor.model if (self.extractor and use_model) else "off",
        )

        # Pass 1 — deterministic. Guarantees every held document appears.
        batch: List[TimelineEvent] = []
        for artifact in artifacts:
            batch.extend(document_events(artifact, deal_type_lookup=deal_type_for))
            if len(batch) >= 400:
                self.deterministic += self._write(batch)
                batch = []
        self.deterministic += self._write(batch)
        logger.info("Timeline: %d document-level events written", self.deterministic)

        # Pass 2 — model extraction of events described inside the text.
        if use_model and self.extractor is not None:
            # Resumable: a document whose text has already been read by the model
            # is skipped. Extraction is the expensive half of this stage, and an
            # interrupted run should not re-buy what it already paid for. Events
            # upsert on a content-derived id, so re-reading would be harmless but
            # not free.
            already_read = set()
            if not force:
                # Two sources of truth, because the first one alone was a bug: a
                # document Opus read and found *no* events in left no event row,
                # so it was re-read — and re-billed — on every run. Half the
                # corpus is such documents, so one property's timeline refresh
                # cost hundreds of calls that changed nothing. ``timeline_read``
                # on the artifact is the durable marker; the event-derived set
                # covers documents read before that marker existed.
                already_read = set(self.events.distinct(
                    "source_sha", {"extracted_by": {"$ne": "deterministic"}}
                ))
                already_read |= set(self.mongo.artifacts.distinct(
                    "sha256", {"timeline_read.model": self.extractor.model}
                ))

            jobs = [
                {
                    "text": a.get("text") or "",
                    "artifact": a,
                    "property_ids": a.get("property_ids") or [],
                    "deal_type_lookup": deal_type_for,
                }
                for a in artifacts
                if (a.get("text") or "").strip() and a["sha256"] not in already_read
            ]
            logger.info(
                "Timeline: extracting from %d documents with text (%d already read)",
                len(jobs), len(already_read),
            )

            for start in range(0, len(jobs), JOB_BATCH):
                group = jobs[start : start + JOB_BATCH]
                found = self.extractor.extract_many(group, stats=self.extract_stats)
                self.extracted += self._write(found)
                read = [j["artifact"]["sha256"] for j in group if j["artifact"].get("_timeline_read")]
                if read:
                    self.mongo.artifacts.update_many(
                        {"sha256": {"$in": read}},
                        {"$set": {"timeline_read": {
                            "model": self.extractor.model,
                            "at": datetime.now(timezone.utc),
                            "run_id": self.run_id,
                        }}},
                    )
                logger.info(
                    "  %d/%d docs  events kept=%d proposed=%d rejected(quote)=%d",
                    min(start + JOB_BATCH, len(jobs)), len(jobs),
                    self.extract_stats.events_kept,
                    self.extract_stats.events_proposed,
                    self.extract_stats.rejected_no_quote,
                )

        self.mongo.runs.insert_one({
            "run_id": self.run_id, "kind": "timeline", "status": "complete",
            "finished_at": datetime.now(timezone.utc),
            "document_events": self.deterministic,
            "extracted_events": self.extracted,
            "extract_stats": self.extract_stats.as_dict(),
        })

        return {
            "document_events": self.deterministic,
            "extracted_events": self.extracted,
            "extract_stats": self.extract_stats.as_dict(),
        }

    # ------------------------------------------------------------------
    def property_timeline(
        self, property_id: str, *, include_undated: bool = True
    ) -> List[dict]:
        """Chronological events for one property, dated first then undated."""
        dated = list(self.events.find(
            {"property_id": property_id, "occurred_at": {"$ne": None}},
            {"_id": 0, "embedding": 0},
        ).sort("occurred_at", 1))
        if not include_undated:
            return dated
        undated = list(self.events.find(
            {"property_id": property_id, "occurred_at": None},
            {"_id": 0, "embedding": 0},
        ))
        return dated + undated

    def coverage(self) -> List[dict]:
        """Per-property event counts and date span — the timeline's own audit."""
        rows = list(self.events.aggregate([
            {"$group": {
                "_id": "$property_id",
                "events": {"$sum": 1},
                "dated": {"$sum": {"$cond": [{"$ne": ["$occurred_at", None]}, 1, 0]}},
                "extracted": {"$sum": {
                    "$cond": [{"$eq": ["$extracted_by", "deterministic"]}, 0, 1]
                }},
                "first": {"$min": "$occurred_at"},
                "last": {"$max": "$occurred_at"},
            }},
            {"$sort": {"events": -1}},
        ]))
        for row in rows:
            prop = PROPERTY_INDEX.get(row["_id"])
            row["address"] = prop.canonical_address if prop else row["_id"]
            row["deal_type"] = prop.deal_type if prop else None
        return rows
