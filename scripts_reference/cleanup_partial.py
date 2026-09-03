"""
Clean up emails that were upserted but whose attachments did not finish
uploading (likely cause: previous run hit the Atlas free-tier quota and the
last batch was interrupted).

Detection rule:
    has_attachments == True AND attachment_ids == [] AND attachment_count > 0

These emails will be deleted (along with any orphaned attachments / GridFS
files referencing their pst_entry_id), so the next ingestion run can re-
process them cleanly.

Also marks any prior `running` ingestion run as `failed` so the run list
is accurate.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger


def main() -> int:
    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    try:
        mongo.ping()

        # 1) Mark stale "running" runs as failed
        stale = mongo.runs.update_many(
            {"status": "running"},
            {
                "$set": {
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc),
                    "failure_reason": "interrupted (resumed by cleanup script)",
                }
            },
        )
        if stale.modified_count:
            logger.info(f"Marked {stale.modified_count} stale 'running' run(s) as 'failed'")

        # 2) Find emails with incomplete attachment uploads
        query = {
            "has_attachments": True,
            "attachment_ids": {"$size": 0},
            "attachment_count": {"$gt": 0},
        }
        suspect_count = mongo.emails.count_documents(query)
        logger.info(
            f"Found {suspect_count} emails with has_attachments=true but no "
            f"attachment_ids — these may have been interrupted mid-upload"
        )
        if suspect_count == 0:
            logger.info("Nothing to clean up.")
            return 0

        suspects = list(
            mongo.emails.find(query, {"_id": 1, "pst_entry_id": 1, "subject": 1})
        )

        # 3) Delete linked attachments + GridFS files for these emails
        suspect_ids = [s["_id"] for s in suspects]
        suspect_pst_ids = [s["pst_entry_id"] for s in suspects]

        # Find any attachments that reference these emails (rare — they would
        # not have been linked, but safety net)
        attach_docs = list(
            mongo.attachments.find(
                {"email_pst_entry_id": {"$in": suspect_pst_ids}},
                {"_id": 1, "gridfs_id": 1},
            )
        )
        if attach_docs:
            for d in attach_docs:
                if d.get("gridfs_id"):
                    try:
                        mongo.gridfs.delete(d["gridfs_id"])
                    except Exception as exc:
                        logger.warning(f"Failed to delete GridFS file {d['gridfs_id']}: {exc}")
            mongo.attachments.delete_many(
                {"_id": {"$in": [d["_id"] for d in attach_docs]}}
            )
            logger.info(f"Deleted {len(attach_docs)} orphan attachment records + GridFS blobs")

        # 4) Delete the suspect emails so re-ingest will pick them up again
        result = mongo.emails.delete_many({"_id": {"$in": suspect_ids}})
        logger.info(
            f"Deleted {result.deleted_count} email(s) — they will be "
            f"re-ingested on the next run"
        )

        # Print a tiny sample for sanity
        for s in suspects[:5]:
            subj = (s.get("subject") or "")[:80]
            logger.info(f"  removed: pst_entry_id={s['pst_entry_id']} subject={subj!r}")

        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
