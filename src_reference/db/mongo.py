"""MongoDB client + GridFS bucket wrapper."""
from __future__ import annotations

from typing import Optional

from gridfs import GridFSBucket
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from src.utils.logger import logger


class MongoClientWrapper:
    """Holds a Mongo connection plus references to the collections we use."""

    def __init__(self, uri: str, db_name: str) -> None:
        self.uri = uri
        self.db_name = db_name
        self.client: MongoClient = MongoClient(
            uri,
            tz_aware=True,
            uuidRepresentation="standard",
            appname="fraud-emails-ingestor",
        )
        self.db: Database = self.client[db_name]
        self.emails: Collection = self.db["emails"]
        self.attachments: Collection = self.db["attachments"]
        self.folders: Collection = self.db["folders"]
        self.runs: Collection = self.db["ingestion_runs"]
        self.errors: Collection = self.db["ingestion_errors"]
        # Phase 2 — RAG
        self.chunks: Collection = self.db["email_chunks"]
        self.gridfs: GridFSBucket = GridFSBucket(self.db, bucket_name="attachment_files")

    # ---- lifecycle ----
    def ping(self) -> None:
        self.client.admin.command("ping")
        logger.info(f"Connected to MongoDB database '{self.db_name}'")

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    # ---- index management ----
    def ensure_indexes(self) -> None:
        logger.info("Ensuring indexes on collections")

        self.emails.create_index(
            [("pst_entry_id", ASCENDING)], name="ux_pst_entry_id", unique=True
        )
        self.emails.create_index(
            [("internet_message_id", ASCENDING)], name="ix_internet_message_id", sparse=True
        )
        # Canonical date index (newest first) — primary sort field
        self.emails.create_index([("date", DESCENDING)], name="ix_date")
        self.emails.create_index([("date_sent", DESCENDING)], name="ix_date_sent")
        self.emails.create_index([("date_received", DESCENDING)], name="ix_date_received")
        # Compound: folder + date for "Inbox newest first" style queries
        self.emails.create_index(
            [("folder_path", ASCENDING), ("date", DESCENDING)],
            name="ix_folder_date",
        )
        # Compound: sender + date for "all emails from X newest first"
        self.emails.create_index(
            [("from.email", ASCENDING), ("date", DESCENDING)],
            name="ix_from_date",
        )
        self.emails.create_index([("date_year", ASCENDING)], name="ix_date_year")
        self.emails.create_index([("date_ym", ASCENDING)], name="ix_date_ym")
        self.emails.create_index([("from.email", ASCENDING)], name="ix_from_email")
        self.emails.create_index([("to.email", ASCENDING)], name="ix_to_email")
        self.emails.create_index([("folder_path", ASCENDING)], name="ix_folder_path")
        self.emails.create_index([("thread_id", ASCENDING)], name="ix_thread_id", sparse=True)
        self.emails.create_index([("subject_normalized", ASCENDING)], name="ix_subject_normalized")
        self.emails.create_index([("content_hash", ASCENDING)], name="ix_content_hash")
        self.emails.create_index(
            [("subject", "text"), ("body_text", "text")],
            name="tx_subject_body",
            default_language="english",
        )

        self.attachments.create_index(
            [("email_id", ASCENDING)], name="ix_email_id"
        )
        self.attachments.create_index(
            [("sha256", ASCENDING)], name="ix_sha256"
        )
        self.attachments.create_index(
            [("filename", ASCENDING)], name="ix_filename"
        )

        self.folders.create_index(
            [("path", ASCENDING)], name="ux_folder_path", unique=True
        )

        self.runs.create_index([("started_at", DESCENDING)], name="ix_started_at")
        self.errors.create_index([("run_id", ASCENDING)], name="ix_run_id")

        # Phase 2 — email_chunks (RAG)
        # Note: the Atlas Vector Search index on `embedding` is created via
        # the Atlas UI / CLI separately (see scripts/print_vector_index.py).
        self.chunks.create_index([("source_type", ASCENDING)], name="ix_source_type")
        self.chunks.create_index([("email_id", ASCENDING)], name="ix_chunk_email_id")
        self.chunks.create_index([("attachment_id", ASCENDING)], name="ix_chunk_attachment_id", sparse=True)
        self.chunks.create_index([("date", DESCENDING)], name="ix_chunk_date")
        self.chunks.create_index([("date_ym", ASCENDING)], name="ix_chunk_date_ym")
        self.chunks.create_index([("from_email", ASCENDING)], name="ix_chunk_from_email")
        self.chunks.create_index(
            [("source_type", ASCENDING), ("email_id", ASCENDING), ("chunk_index", ASCENDING)],
            name="ix_chunk_source_compound",
        )

        logger.info("Indexes ready")


_singleton: Optional[MongoClientWrapper] = None


def get_mongo(uri: str, db_name: str) -> MongoClientWrapper:
    global _singleton
    if _singleton is None:
        _singleton = MongoClientWrapper(uri, db_name)
    return _singleton
