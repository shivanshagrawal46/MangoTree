"""Indexing — chunk, contextualise, embed, store.

Sources
-------
* **Emails** — the cleaned body, quoted replies already split off so a forwarded
  thread is not re-indexed as new activity every time someone hits reply.
* **Disk artifacts** — whatever extraction produced (native text, OCR, or
  spreadsheet rows), each carrying its page or cell reference.

Every chunk written here carries:

* ``property_ids`` — from **its own segments**, not the parent document, so a
  Decatur query can never reach a Varnum sentence that happened to share an email.
* ``embedding_model`` — because one embedding space must never mix with another.
* ``source_ref`` — the page or cell the text came from, so the answer UI can put
  a human on the exact line in ≤2 clicks.

Idempotent: chunk ids are content-derived and writes are upserts, so re-running
after an interruption reproduces the same index rather than duplicating it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence

from mangotree.config.models import CONTEXT as CTX
from mangotree.config.models import EMBEDDING_MODEL
from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core.sources import DOCUMENT_SOURCE_TYPES
from mangotree.chunk.chunker import chunk_artifact
from mangotree.context.tier1 import Tier1Stats, Tier1Writer, write_many
from mangotree.context.tier2 import TIER2_VERSION, build_embedded_context, build_tier2
from mangotree.core.logging import logger
from mangotree.embed.embedder import Embedder, build_header_line
from mangotree.storage.mongo import Mongo

#: Chunks written per Mongo bulk operation.
WRITE_BATCH = 200

#: Documents contextualised together before embedding. Tier 1 parallelises across
#: documents, so this is the unit of concurrency; keeping it modest bounds how
#: much work is lost if the run is interrupted.
DOC_BATCH = 24


@dataclass
class IndexStats:
    artifacts: int = 0
    skipped_no_text: int = 0
    skipped_done: int = 0
    chunks: int = 0
    embedded: int = 0
    embed_failures: int = 0
    unattributed_chunks: int = 0
    tier1_written: int = 0
    tier1_empty: int = 0
    by_property: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "artifacts_indexed": self.artifacts,
            "skipped_no_text": self.skipped_no_text,
            "skipped_already_indexed": self.skipped_done,
            "chunks_written": self.chunks,
            "chunks_embedded": self.embedded,
            "embed_failures": self.embed_failures,
            "unattributed_chunks": self.unattributed_chunks,
            "tier1_written": self.tier1_written,
            "tier1_empty": self.tier1_empty,
            "chunks_per_property": dict(
                sorted(self.by_property.items(), key=lambda kv: -kv[1])
            ),
        }


def _property_label(property_ids: Sequence[str]) -> Optional[str]:
    """Human label for the context line. Multi-property chunks say so plainly."""
    if not property_ids:
        return None
    if len(property_ids) == 1:
        prop = PROPERTY_INDEX.get(property_ids[0])
        return prop.canonical_address if prop else property_ids[0]
    labels = []
    for pid in property_ids[:3]:
        prop = PROPERTY_INDEX.get(pid)
        labels.append(prop.canonical_address if prop else pid)
    return " + ".join(labels)


class Indexer:
    def __init__(
        self,
        mongo: Mongo,
        *,
        api_key: str,
        run_id: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        tier1: bool = True,
    ):
        self.mongo = mongo
        self.embedder = Embedder(api_key)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("index-%Y%m%d-%H%M%S")
        self.stats = IndexStats()
        self.tier1_stats = Tier1Stats()

        # Tier 1 is the retrieval quality lever and is on by default. It is
        # switchable rather than mandatory so an index rebuild is still possible
        # if Anthropic is down — but when it is skipped the chunks are stamped as
        # tier1-less, so a degraded index can never be mistaken for a full one.
        self.tier1_writer: Optional[Tier1Writer] = None
        if tier1 and anthropic_api_key:
            self.tier1_writer = Tier1Writer(anthropic_api_key)
        elif tier1:
            logger.warning(
                "Tier-1 context requested but no Anthropic key supplied; "
                "chunks will carry Tier 2 + header only"
            )

        self._inventory: Dict[str, Dict[str, int]] = {}
        self._ordinals: Dict[str, int] = {}

    # ------------------------------------------------------------------
    def _targets(self, *, source: str, reindex: bool, limit: Optional[int]) -> List[dict]:
        # Attachments are indexed alongside the other two. They were absent from
        # every branch here until 2026-08-31, so even once their text existed it
        # was unreachable: an ALTA settlement statement extracted but not indexed
        # is no more findable than one never read at all.
        extracted = {"extraction.status": {"$in": ["complete", "partial"]}}
        query: dict = {}
        if source == "email":
            query["source_type"] = "email"
            query["body_clean"] = {"$exists": True, "$ne": ""}
        elif source == "disk":
            query["source_type"] = "disk_file"
            query.update(extracted)
        elif source == "attachment":
            query["source_type"] = "attachment"
            query.update(extracted)
        else:
            query["$or"] = [
                {"source_type": "email", "body_clean": {"$exists": True, "$ne": ""}},
                *({"source_type": st, **extracted} for st in DOCUMENT_SOURCE_TYPES),
            ]

        if not reindex:
            query["indexing.model"] = {"$ne": EMBEDDING_MODEL}

        projection = {
            "sha256": 1, "source_type": 1, "filename": 1, "subject": 1,
            "body_clean": 1, "text": 1, "property_ids": 1, "doc_class": 1,
            "date": 1, "privileged": 1,
            # Retrieval metadata: carried onto every chunk so a query can narrow
            # by sender, date, folder or scope during the search itself.
            "participants": 1, "scope": 1, "common_kind": 1, "common_topics": 1, "placement": 1, "relative_path": 1,
            "parent_email_shas": 1,
        }
        cursor = self.mongo.artifacts.find(query, projection)
        if limit:
            cursor = cursor.limit(limit)
        return list(cursor)

    # ------------------------------------------------------------------
    @staticmethod
    def _text_and_ref(doc: dict) -> tuple[str, str, str]:
        """(text, default source ref, display name) for an artifact."""
        if doc.get("source_type") == "email":
            subject = (doc.get("subject") or "").strip()
            body = doc.get("body_clean") or ""
            # The subject is a strong property signal and belongs to the first
            # segment, so it is prepended rather than indexed separately.
            text = f"Subject: {subject}\n\n{body}" if subject else body
            return text, "email body", subject or "(no subject)"
        return doc.get("text") or "", "document", doc.get("filename") or "(unnamed)"

    def _load_inventory(self) -> None:
        """Per-property counts of each document class — the coverage denominator
        that Tier 2 carries. Without it an analysis can only describe what it was
        given; with it, the model knows 14 invoices exist and can notice that it
        has seen 11."""
        pipeline = [
            # Attachments count toward coverage too — a title commitment that
            # arrived by email is the same kind of evidence as one found on disk,
            # and excluding it understates the denominator.
            {"$match": {"source_type": {"$in": list(DOCUMENT_SOURCE_TYPES)}}},
            {"$unwind": "$property_ids"},
            {"$group": {
                # $ifNull is required, not defensive: attachments carry no
                # doc_class, and a $group key that resolves to missing is dropped
                # from the _id document entirely rather than set to null.
                "_id": {
                    "p": "$property_ids",
                    "c": {"$ifNull": ["$doc_class", "unclassified"]},
                },
                "n": {"$sum": 1},
            }},
        ]
        for row in self.mongo.artifacts.aggregate(pipeline):
            pid = row["_id"]["p"]
            cls = row["_id"].get("c") or "unclassified"
            self._inventory.setdefault(pid, {})[cls] = row["n"]

    def _ordinal_for(self, doc: dict) -> Optional[int]:
        """Stable position of this document among its class for its property."""
        pids = doc.get("property_ids") or []
        if not pids:
            return None
        key = f"{pids[0]}::{doc.get('doc_class') or 'unclassified'}"
        self._ordinals[key] = self._ordinals.get(key, 0) + 1
        return self._ordinals[key]

    # ------------------------------------------------------------------
    def _build_chunks(self, doc: dict) -> List[dict]:
        """Chunk an artifact and attach everything except Tier 1, which is filled
        in for a whole document at once so the prompt cache pays off."""
        text, default_ref, display = self._text_and_ref(doc)
        if not text.strip():
            self.stats.skipped_no_text += 1
            return []

        property_ids = doc.get("property_ids") or []
        chunks = chunk_artifact(
            text,
            artifact_sha=doc["sha256"],
            property_ids=property_ids,
            default_ref=default_ref,
        )
        if not chunks:
            self.stats.skipped_no_text += 1
            return []

        date = doc.get("date")
        date_hint = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else None
        doc_class = doc.get("doc_class")
        inventory = self._inventory.get(property_ids[0]) if property_ids else None
        ordinal = self._ordinal_for(doc)
        retrieval_meta = self._retrieval_meta(doc)

        records: List[dict] = []
        for chunk in chunks:
            header = build_header_line(
                filename=display,
                property_label=_property_label(chunk.property_ids),
                doc_class=doc_class,
                source_ref=chunk.source_ref,
                date_hint=date_hint,
            )
            # Tier 2 is scoped to the *chunk's* properties, not the document's,
            # so a chunk that only concerns 904 Bayshore is never framed as
            # belonging to a document that also covered 910.
            tier2 = build_tier2(
                property_ids=chunk.property_ids or property_ids,
                doc_class=doc_class,
                display_name=display,
                date=date,
                source_ref=chunk.source_ref,
                inventory=inventory,
                ordinal=ordinal,
            )

            record = chunk.as_dict()
            record.update({
                "source_type": doc.get("source_type"),
                "doc_class": doc_class,
                "date": date,
                "privileged": bool(doc.get("privileged")),
                "display_name": display,
                "embedding_model": EMBEDDING_MODEL,
                "run_id": self.run_id,
                "header": header,
                "tier1": "",
                "tier2": tier2,
                "tier2_version": TIER2_VERSION,
                "tier1_model": None,
                "tier1_prompt_version": None,
                **retrieval_meta,
            })
            records.append(record)

            if not chunk.property_ids:
                self.stats.unattributed_chunks += 1
            for pid in chunk.property_ids:
                self.stats.by_property[pid] = self.stats.by_property.get(pid, 0) + 1

        return records

    # ------------------------------------------------------------------
    def _retrieval_meta(self, doc: dict) -> dict:
        """Sender, dates, folder and scope, resolved for this document.

        Attachments have none of their own — a PDF carries no sender — so the
        emails that delivered it are looked up and supply them.
        """
        from mangotree.index.metadata import chunk_metadata

        parents = []
        parent_shas = doc.get("parent_email_shas") or []
        if parent_shas:
            parents = list(self.mongo.artifacts.find(
                {"sha256": {"$in": parent_shas}},
                {"sha256": 1, "date": 1, "participants": 1},
            ))

        occurrences = list(self.mongo.occurrences.find(
            {"artifact_sha": doc["sha256"]},
            {"folder": 1, "mailbox": 1, "date": 1},
        ))
        return chunk_metadata(doc, occurrences=occurrences, parent_emails=parents)

    # ------------------------------------------------------------------
    def _apply_tier1(self, batch: List[tuple]) -> None:
        """Fill Tier 1 across a batch of documents, then finalise embed text.

        ``batch`` is a list of ``(artifact, records)``. Tier 1 needs the whole
        document as its cached prefix, so it is invoked per document rather than
        per chunk.
        """
        if self.tier1_writer is not None:
            payload = []
            for doc, records in batch:
                document_text, _, display = self._text_and_ref(doc)
                pids = doc.get("property_ids") or []
                prop = PROPERTY_INDEX.get(pids[0]) if pids else None
                payload.append({
                    "key": doc["sha256"],
                    "document_text": document_text,
                    "chunk_texts": [r["text"] for r in records],
                    "meta": {
                        "display_name": display,
                        "doc_class": doc.get("doc_class"),
                        "property_label": _property_label(pids),
                        "deal_type": prop.deal_type if prop else None,
                        "date": doc.get("date"),
                    },
                })

            try:
                summaries = write_many(
                    self.tier1_writer, payload, stats=self.tier1_stats
                )
            except Exception as exc:
                logger.error("Tier-1 batch failed wholesale: %s", exc)
                summaries = {}

            for doc, records in batch:
                lines = summaries.get(doc["sha256"]) or []
                for record, line in zip(records, lines):
                    if line:
                        record["tier1"] = line
                        record["tier1_model"] = self.tier1_writer.model
                        record["tier1_prompt_version"] = self.tier1_writer.prompt_version

        for _, records in batch:
            for record in records:
                if record.get("tier1"):
                    self.stats.tier1_written += 1
                else:
                    self.stats.tier1_empty += 1
                context = build_embedded_context(record["tier1"], record["tier2"])
                blocks = [b for b in (context, record["header"]) if b]
                record["context"] = "\n".join(blocks)
                record["embed_text"] = (
                    f"{record['context']}\n\n{record['text']}".strip()
                )

    # ------------------------------------------------------------------
    def _flush(self, records: List[dict]) -> None:
        """Embed a batch and upsert it. Chunks whose embedding failed are stored
        without a vector and marked, so the gap is visible and repairable rather
        than silently absent from every future search."""
        if not records:
            return

        from pymongo import UpdateOne

        vectors = self.embedder.embed_documents([r["embed_text"] for r in records])
        operations: List[UpdateOne] = []
        now = datetime.now(timezone.utc)

        for record, vector in zip(records, vectors):
            record.pop("embed_text", None)
            if vector is None:
                record["embedding_status"] = "failed"
                self.stats.embed_failures += 1
            else:
                record["embedding"] = vector
                record["embedding_status"] = "ok"
                self.stats.embedded += 1
            record["indexed_at"] = now
            operations.append(
                UpdateOne({"chunk_id": record["chunk_id"]}, {"$set": record}, upsert=True)
            )

        if operations:
            self.mongo.chunks.bulk_write(operations, ordered=False)
            self.stats.chunks += len(operations)

    # ------------------------------------------------------------------
    def run(
        self, *, source: str = "all", reindex: bool = False, limit: Optional[int] = None
    ) -> IndexStats:
        targets = self._targets(source=source, reindex=reindex, limit=limit)
        self._load_inventory()
        logger.info(
            "Indexing %d artifacts (run %s, tier1=%s)",
            len(targets), self.run_id,
            self.tier1_writer.model if self.tier1_writer else "off",
        )

        self.mongo.runs.insert_one({
            "run_id": self.run_id, "kind": "indexing", "status": "running",
            "started_at": datetime.now(timezone.utc), "target_count": len(targets),
            "embedding_model": EMBEDDING_MODEL,
            "tier1_model": self.tier1_writer.model if self.tier1_writer else None,
            "tier2_version": TIER2_VERSION,
        })

        pending: List[dict] = []
        indexed_shas: List[str] = []
        done = 0

        for start in range(0, len(targets), DOC_BATCH):
            group = targets[start : start + DOC_BATCH]
            batch: List[tuple] = []
            for doc in group:
                records = self._build_chunks(doc)
                if records:
                    batch.append((doc, records))

            if batch:
                self._apply_tier1(batch)
                for doc, records in batch:
                    pending.extend(records)
                    indexed_shas.append(doc["sha256"])
                    self.stats.artifacts += 1

            while len(pending) >= WRITE_BATCH:
                self._flush(pending[:WRITE_BATCH])
                pending = pending[WRITE_BATCH:]
            if not pending:
                self._mark_indexed(indexed_shas)
                indexed_shas = []

            done += len(group)
            logger.info(
                "  %d/%d docs  chunks=%d embedded=%d tier1=%d/%d calls=%d",
                done, len(targets), self.stats.chunks, self.stats.embedded,
                self.stats.tier1_written,
                self.stats.tier1_written + self.stats.tier1_empty,
                self.tier1_writer.calls if self.tier1_writer else 0,
            )

        self._flush(pending)
        self._mark_indexed(indexed_shas)

        self.mongo.runs.update_one(
            {"run_id": self.run_id},
            {"$set": {"status": "complete", "finished_at": datetime.now(timezone.utc),
                      **self.stats.as_dict(),
                      "tier1_stats": self.tier1_stats.as_dict(),
                      "embed_stats": self.embedder.stats.as_dict()}},
        )
        return self.stats

    def _mark_indexed(self, shas: Sequence[str]) -> None:
        if not shas:
            return
        self.mongo.artifacts.update_many(
            {"sha256": {"$in": list(shas)}},
            {"$set": {"indexing": {
                "model": EMBEDDING_MODEL,
                "run_id": self.run_id,
                "indexed_at": datetime.now(timezone.utc),
            }}},
        )
