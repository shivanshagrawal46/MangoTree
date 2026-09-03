"""Sprint 6 · 6.4 — admin/observability dashboard data -> `dashboard_stats`.

Aggregates the numbers a reviewer/CEO/trustee wants at a glance: corpus sizes,
entity graph, findings, events, review queues, eval scorecard, grounded
coverage. UI renders this in Sprint 8 (no live agent needed)."""
import sys
from datetime import datetime, timezone
from collections import Counter
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
db = m.db
now = datetime.now(timezone.utc)


def by(coll, field, q=None):
    # stringify keys (Mongo cannot store a None/non-string dict key)
    return {str(k) if k is not None else "none": v
            for k, v in Counter(d.get(field) for d in db[coll].find(q or {}, {field: 1})).items()}


docs = db["documents"]
ents = db["entities"]
stats = {
    "_id": "current", "generated_at": now,
    "documents": {"total": docs.count_documents({}), "by_type": by("documents", "source_type"),
                  "with_grounded_facts": docs.count_documents({"grounded_facts": {"$exists": True}}),
                  "needs_review": docs.count_documents({"quality.needs_review": True}),
                  "with_redactions": docs.count_documents({"has_redactions": True})},
    "chunks": {"total": db["email_chunks_v2"].estimated_document_count(),
               "linked": db["email_chunks_v2"].count_documents({"entity_ids.0": {"$exists": True}}),
               "privileged": db["email_chunks_v2"].count_documents({"privilege_status": "privileged"}),
               "by_corpus": by("email_chunks_v2", "corpus")},
    "entities": {"total": ents.count_documents({"is_active": {"$ne": False}}),
                 "by_kind": by("entities", "kind", {"is_active": {"$ne": False}}),
                 "by_side": by("entities", "side", {"is_active": {"$ne": False}}),
                 "david": ents.count_documents({"is_david": True, "is_active": {"$ne": False}}),
                 "needs_review": ents.count_documents({"needs_review": True})},
    "relationships": {"total": db["relationships"].estimated_document_count(),
                      "by_type": by("relationships", "type")},
    "events": {"total": db["events"].estimated_document_count(), "by_type": by("events", "event_type")},
    "findings": {"total": db["findings"].count_documents({}),
                 "by_severity": by("findings", "severity"),
                 "by_type": by("findings", "finding_type"),
                 "by_status": by("findings", "status")},
    "review_queues": {"entity_merge_pending": db["entity_review"].count_documents({"status": "pending"}),
                      "entity_needs_review": ents.count_documents({"needs_review": True}),
                      "findings_pending": db["findings"].count_documents({"status": "pending"})},
    "property_dossiers": db["property_dossier"].count_documents({}),
}
ev = db["eval_results"].find_one(sort=[("run_at", -1)])
stats["latest_eval"] = ev.get("metrics") if ev else None

db["dashboard_stats"].update_one({"_id": "current"}, {"$set": stats}, upsert=True)
logger.info("================ DASHBOARD ================")
for k, v in stats.items():
    if k not in ("_id", "generated_at"):
        logger.info(f"  {k}: {v}")
m.close()
sys.exit(0)
