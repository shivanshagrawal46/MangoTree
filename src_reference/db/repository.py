"""High-level write API around MongoDB collections."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from bson import ObjectId
from pymongo import UpdateOne
from pymongo.errors import BulkWriteError

from src.utils.logger import logger

from .mongo import MongoClientWrapper


class EmailRepository:
    def __init__(self, mongo: MongoClientWrapper) -> None:
        self.mongo = mongo

    # ------------------------------------------------------------------
    # Ingestion run lifecycle
    # ------------------------------------------------------------------
    def start_run(self, pst_meta: dict) -> ObjectId:
        doc = {
            "status": "running",
            "started_at": datetime.now(timezone.utc),
            "completed_at": None,
            "pst_file": pst_meta,
            "totals": {
                "messages_seen": 0,
                "messages_inserted": 0,
                "messages_skipped": 0,
                "attachments_inserted": 0,
                "errors": 0,
            },
        }
        result = self.mongo.runs.insert_one(doc)
        logger.info(f"Started ingestion run {result.inserted_id}")
        return result.inserted_id

    def update_run_totals(self, run_id: ObjectId, totals: dict) -> None:
        self.mongo.runs.update_one(
            {"_id": run_id},
            {"$set": {"totals": totals}},
        )

    def finish_run(self, run_id: ObjectId, totals: dict, status: str = "completed") -> None:
        self.mongo.runs.update_one(
            {"_id": run_id},
            {
                "$set": {
                    "status": status,
                    "completed_at": datetime.now(timezone.utc),
                    "totals": totals,
                }
            },
        )
        logger.info(f"Run {run_id} finished with status='{status}': {totals}")

    def log_error(self, run_id: ObjectId, pst_entry_id: str, stage: str, error: str, tb: str = "") -> None:
        try:
            self.mongo.errors.insert_one(
                {
                    "run_id": run_id,
                    "pst_entry_id": pst_entry_id,
                    "stage": stage,
                    "error": error[:5000],
                    "traceback": tb[:20000],
                    "created_at": datetime.now(timezone.utc),
                }
            )
        except Exception as exc:
            logger.error(f"Failed to log ingestion error: {exc}")

    # ------------------------------------------------------------------
    # Folder tracking
    # ------------------------------------------------------------------
    def upsert_folder(self, path: str) -> None:
        self.mongo.folders.update_one(
            {"path": path},
            {
                "$setOnInsert": {
                    "path": path,
                    "name": path.rsplit("/", 1)[-1] if path else "",
                },
                "$inc": {"email_count": 1},
            },
            upsert=True,
        )

    # ------------------------------------------------------------------
    # Existence checks (idempotency)
    # ------------------------------------------------------------------
    def existing_pst_entry_ids(self, ids: Iterable[str]) -> set[str]:
        cursor = self.mongo.emails.find(
            {"pst_entry_id": {"$in": list(ids)}},
            {"pst_entry_id": 1, "_id": 0},
        )
        return {doc["pst_entry_id"] for doc in cursor}

    # ------------------------------------------------------------------
    # Email writes
    # ------------------------------------------------------------------
    def upsert_emails(self, docs: list[dict]) -> dict[str, ObjectId]:
        """Upsert emails by pst_entry_id; return mapping pst_entry_id -> _id."""
        if not docs:
            return {}

        ops = [
            UpdateOne(
                {"pst_entry_id": d["pst_entry_id"]},
                {"$set": d, "$setOnInsert": {"_id": ObjectId()}},
                upsert=True,
            )
            for d in docs
        ]
        try:
            self.mongo.emails.bulk_write(ops, ordered=False)
        except BulkWriteError as bwe:
            logger.error(f"Bulk write error on emails: {bwe.details}")
            raise

        cursor = self.mongo.emails.find(
            {"pst_entry_id": {"$in": [d["pst_entry_id"] for d in docs]}},
            {"pst_entry_id": 1},
        )
        return {doc["pst_entry_id"]: doc["_id"] for doc in cursor}

    # ------------------------------------------------------------------
    # Attachment writes (binary -> GridFS, metadata -> attachments)
    # ------------------------------------------------------------------
    def store_attachment(
        self,
        *,
        email_id: ObjectId,
        email_pst_entry_id: str,
        filename: str,
        display_name: str | None,
        content_type: str | None,
        data: bytes,
        sha256: str,
        is_inline: bool,
        content_id: str | None,
    ) -> ObjectId:
        # Store binary in GridFS
        gridfs_id = self.mongo.gridfs.upload_from_stream(
            filename or "unknown",
            data,
            metadata={
                "email_id": email_id,
                "email_pst_entry_id": email_pst_entry_id,
                "sha256": sha256,
                "content_type": content_type,
            },
        )

        ext = ""
        if filename and "." in filename:
            ext = "." + filename.rsplit(".", 1)[-1].lower()

        attachment_doc = {
            "email_id": email_id,
            "email_pst_entry_id": email_pst_entry_id,
            "filename": filename or "unknown",
            "display_name": display_name,
            "extension": ext,
            "content_type": content_type,
            "size_bytes": len(data),
            "sha256": sha256,
            "is_inline": is_inline,
            "content_id": content_id,
            "gridfs_id": gridfs_id,
            "ingested_at": datetime.now(timezone.utc),
        }
        result = self.mongo.attachments.insert_one(attachment_doc)
        return result.inserted_id

    def link_attachments_to_email(self, email_id: ObjectId, attachment_ids: list[ObjectId]) -> None:
        if not attachment_ids:
            return
        self.mongo.emails.update_one(
            {"_id": email_id},
            {
                "$set": {
                    "attachment_ids": attachment_ids,
                    "attachment_count": len(attachment_ids),
                    "has_attachments": True,
                }
            },
        )
