"""Sprint 2 · 2.3 + Sprint 5 · 5.5 prep — corpus/privilege tagging on chunks.

Doc chunks already carry corpus+privilege (set at chunk_embed). Email/attachment
chunks get tagged here so Clean mode can filter at the retrieval layer.

SAFE DEFAULT: email/attachment chunks -> corpus=legal_correspondence,
privilege_status=privileged. This OVER-protects: Clean-mode outputs exclude them,
so a privileged strategy can never leak. David's emails are admissions we WANT
in clean output — but that requires confirmed David sender addresses; until then
we default to privileged and reclassify on confirmation (privilege_basis records
this so it's auditable + reversible). Idempotent.
"""
import sys
from datetime import datetime, timezone
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
ch = m.db["email_chunks_v2"]
now = datetime.now(timezone.utc)

# email/attachment chunks lacking a corpus -> safe privileged default
r1 = ch.update_many(
    {"source_type": {"$in": ["email_body", "attachment"]},
     "$or": [{"corpus": {"$exists": False}}, {"corpus": None}]},
    {"$set": {"corpus": "legal_correspondence", "privilege_status": "privileged",
              "privilege_basis": "default_safe_pending_sender_confirmation",
              "evidentiary_class": "privileged_work_product", "corpus_tagged_at": now}})
logger.info(f"email/attachment chunks tagged privileged (safe default): {r1.modified_count}")

# ensure doc chunks have an explicit privilege (public_record / third_party are shareable)
r2 = ch.update_many(
    {"corpus": {"$in": ["property_records", "insurance_records", "court_records",
                        "financial_records", "contract_records", "corporate_records"]},
     "privilege_status": {"$exists": False}},
    {"$set": {"privilege_status": "public_record", "corpus_tagged_at": now}})
logger.info(f"doc chunks privilege backfilled: {r2.modified_count}")

from pymongo import ASCENDING
for p in ["corpus", "privilege_status"]:
    try:
        ch.create_index([(p, ASCENDING)], name="ix_" + p)
    except Exception:  # noqa: BLE001
        pass

import collections
logger.info("privilege distribution: " +
            str(dict(collections.Counter(c.get("privilege_status")
                                         for c in ch.find({}, {"privilege_status": 1})))))
clean_visible = ch.count_documents({"privilege_status": {"$ne": "privileged"}})
logger.info(f"Clean-mode visible chunks (non-privileged): {clean_visible}/{ch.estimated_document_count()}")
m.close()
sys.exit(0)
