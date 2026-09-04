"""MongoDB layer — collections, indexes, and the raw-original store.

Collection map
--------------
``artifacts``        one row per unique ingested item (email, attachment, disk file)
``occurrences``      artifact x (mailbox, folder, property) fan-out + direction
``threads``          stitched conversations, cross-provider
``properties``       the property registry, materialised for joins/UI
``people``           identity registry, materialised
``review_queue``     anything below the confidence bar — never dropped, always visible
``ingestion_runs``   one row per backfill/sweep run, with counters
``ingestion_errors`` dead letters
``skipped``          messages deliberately NOT stored (counted for reconciliation)
``checkpoints``      resumable cursors per source
"""
from __future__ import annotations

from typing import Optional

from gridfs import GridFSBucket
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from mangotree.core.logging import logger


class Mongo:
    def __init__(self, uri: str, db_name: str) -> None:
        self.client: MongoClient = MongoClient(
            uri, tz_aware=True, uuidRepresentation="standard", appname="MangoTree"
        )
        self.db: Database = self.client[db_name]
        self.db_name = db_name

        self.artifacts: Collection = self.db["artifacts"]
        self.occurrences: Collection = self.db["occurrences"]
        self.threads: Collection = self.db["threads"]
        self.properties: Collection = self.db["properties"]
        self.people: Collection = self.db["people"]
        self.review_queue: Collection = self.db["review_queue"]
        self.runs: Collection = self.db["ingestion_runs"]
        self.errors: Collection = self.db["ingestion_errors"]
        self.skipped: Collection = self.db["skipped"]
        self.checkpoints: Collection = self.db["checkpoints"]
        self.chunks: Collection = self.db["chunks"]

        self.files: GridFSBucket = GridFSBucket(self.db, bucket_name="originals")

    # ------------------------------------------------------------------
    def ping(self) -> None:
        self.client.admin.command("ping")
        logger.info("MongoDB connected: db=%s", self.db_name)

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # pragma: no cover - best effort
            pass

    # ------------------------------------------------------------------
    def ensure_indexes(self) -> None:
        a = self.artifacts
        # sha256 is the identity of an artifact — one row per unique content.
        a.create_index([("sha256", ASCENDING)], name="ux_sha256", unique=True)
        a.create_index(
            [("internet_message_id", ASCENDING)],
            name="ix_imid",
            sparse=True,
        )
        a.create_index([("content_fingerprint", ASCENDING)], name="ix_fingerprint", sparse=True)
        a.create_index([("source_type", ASCENDING)], name="ix_source_type")
        a.create_index([("date", DESCENDING)], name="ix_date")
        a.create_index([("thread_key", ASCENDING)], name="ix_thread_key", sparse=True)
        a.create_index([("property_ids", ASCENDING)], name="ix_property_ids")
        a.create_index([("person_ids", ASCENDING)], name="ix_person_ids")
        a.create_index([("doc_class", ASCENDING)], name="ix_doc_class")
        a.create_index([("resolution.status", ASCENDING)], name="ix_resolution_status")
        a.create_index(
            [("property_ids", ASCENDING), ("date", DESCENDING)], name="ix_property_date"
        )
        a.create_index(
            [("subject", "text"), ("body_clean", "text"), ("filename", "text")],
            name="tx_fulltext",
            default_language="english",
        )

        o = self.occurrences
        o.create_index(
            [("artifact_sha", ASCENDING), ("mailbox", ASCENDING), ("folder", ASCENDING)],
            name="ux_occurrence",
            unique=True,
        )
        o.create_index([("artifact_sha", ASCENDING)], name="ix_occ_artifact")
        o.create_index([("mailbox", ASCENDING), ("direction", ASCENDING)], name="ix_occ_mailbox_dir")
        o.create_index([("property_id", ASCENDING)], name="ix_occ_property", sparse=True)
        o.create_index([("provider_id", ASCENDING)], name="ix_occ_provider_id", sparse=True)

        self.threads.create_index([("thread_key", ASCENDING)], name="ux_thread_key", unique=True)
        self.threads.create_index([("property_ids", ASCENDING)], name="ix_thread_property")
        self.threads.create_index([("last_date", DESCENDING)], name="ix_thread_last_date")

        self.properties.create_index([("property_id", ASCENDING)], name="ux_property_id", unique=True)
        self.people.create_index([("person_id", ASCENDING)], name="ux_person_id", unique=True)
        self.people.create_index([("addresses", ASCENDING)], name="ix_person_addresses")

        self.review_queue.create_index([("status", ASCENDING), ("created_at", DESCENDING)], name="ix_review")
        self.review_queue.create_index([("kind", ASCENDING)], name="ix_review_kind")
        self.review_queue.create_index(
            [("artifact_sha", ASCENDING), ("kind", ASCENDING)], name="ux_review_target", unique=True
        )

        self.runs.create_index([("started_at", DESCENDING)], name="ix_run_started")
        self.errors.create_index([("run_id", ASCENDING)], name="ix_err_run")

        # Skipped mail is counted, never stored: keep only the provider id + reason
        # so reconciliation can prove "seen and deliberately excluded".
        self.skipped.create_index(
            [("provider", ASCENDING), ("provider_id", ASCENDING)],
            name="ux_skipped",
            unique=True,
        )
        self.skipped.create_index([("reason", ASCENDING)], name="ix_skipped_reason")

        self.checkpoints.create_index([("key", ASCENDING)], name="ux_checkpoint", unique=True)

        c = self.chunks
        c.create_index([("chunk_id", ASCENDING)], name="ux_chunk_id", unique=True)
        c.create_index([("artifact_sha", ASCENDING), ("ordinal", ASCENDING)], name="ix_chunk_artifact")
        # The index that makes per-property retrieval possible: chunks are
        # filtered to one property *before* similarity is ever considered.
        c.create_index([("property_ids", ASCENDING)], name="ix_chunk_property")
        c.create_index([("property_ids", ASCENDING), ("date", DESCENDING)], name="ix_chunk_prop_date")
        c.create_index([("embedding_model", ASCENDING)], name="ix_chunk_model")
        c.create_index([("indexed_at", DESCENDING)], name="ix_chunk_indexed")
        # Lexical half of hybrid retrieval — exact terms ("draw 3", a dollar
        # figure, a lien number) that vectors reliably blur.
        try:
            c.create_index(
                [("text", "text"), ("context", "text")],
                name="tx_chunk_text",
                weights={"text": 10, "context": 3},
            )
        except Exception:  # a text index may already exist with other fields
            pass

        logger.info("MongoDB indexes ready")

    # ------------------------------------------------------------------
    # raw original store
    #
    # Bytes go to the object store (local now, S3 later); MongoDB keeps only the
    # pointer. Keeping originals in GridFS charges cluster storage for data that
    # is never queried — see storage/objectstore.py.
    # ------------------------------------------------------------------
    def put_original(self, sha256: str, data: bytes, filename: str, metadata: dict) -> None:
        """Store bytes exactly as received, addressed by SHA-256. Idempotent."""
        from mangotree.storage.objectstore import get_object_store

        get_object_store().put(sha256, data, filename, metadata)

    def get_original(self, sha256: str) -> Optional[bytes]:
        from mangotree.storage.objectstore import get_object_store

        data = get_object_store().get(sha256)
        if data is not None:
            return data
        # Fall back to GridFS for anything stored before the move.
        for grid_out in self.files.find({"filename": sha256}):
            return grid_out.read()
        return None

    # ------------------------------------------------------------------
    def get_checkpoint(self, key: str) -> dict:
        return self.checkpoints.find_one({"key": key}) or {}

    def set_checkpoint(self, key: str, **fields) -> None:
        self.checkpoints.update_one(
            {"key": key}, {"$set": {"key": key, **fields}}, upsert=True
        )


_singleton: Optional[Mongo] = None


def get_mongo(uri: Optional[str] = None, db_name: Optional[str] = None) -> Mongo:
    global _singleton
    if _singleton is None:
        from mangotree.config.settings import SETTINGS

        _singleton = Mongo(uri or SETTINGS.mongo_uri, db_name or SETTINGS.mongo_db)
    return _singleton
