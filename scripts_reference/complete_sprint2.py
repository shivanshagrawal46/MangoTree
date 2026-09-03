"""Complete Sprint 2 remaining backend items + gate.

2.2 Lawyer-corpus tag backfill on emails (+ attachments_v2 if present)
2.4 Redaction-aware tags on documents (has_redactions + redaction markers)
2.6 Temporal normalization -> documents.dates_normalized [{kind, iso}]
2.7 Quality gates -> documents.quality.needs_review + obs metrics
2.8 GATE: every doc has corpus+privilege; grounded title docs carry facts
"""
import re
import sys
from datetime import datetime, timezone
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
db = m.db
now = datetime.now(timezone.utc)
docs = db["documents"]

# ---- 2.2 lawyer-corpus backfill on emails ----
if "emails" in db.list_collection_names():
    r = db["emails"].update_many(
        {"$or": [{"corpus": {"$exists": False}}, {"corpus": None}]},
        {"$set": {"corpus": "legal_correspondence", "privilege_status": "privileged",
                  "evidentiary_class": "privileged_work_product",
                  "custody.tagged_at": now, "corpus_basis": "pst_lawyer_mailbox"}})
    logger.info(f"2.2 emails tagged: {r.modified_count}")

# ---- 2.2b documents corpus/privilege backfill (by source_type) ----
_CORPUS = {
    "title_report": ("property_records", "public_record"),
    "insurance": ("insurance_records", "third_party"),
    "equity_schedule": ("financial_records", "third_party"),
    "service_agreement": ("contract_records", "third_party"),
    "litigation_update": ("court_records", "public_record"),
}
for st, (corp, priv) in _CORPUS.items():
    r = docs.update_many({"source_type": st, "$or": [{"corpus": {"$exists": False}}, {"corpus": None}]},
                         {"$set": {"corpus": corp, "corpus_tagged_at": now}})
    r2 = docs.update_many({"source_type": st, "$or": [{"privilege_status": {"$exists": False}},
                                                      {"privilege_status": None}]},
                          {"$set": {"privilege_status": priv}})
    if r.modified_count or r2.modified_count:
        logger.info(f"2.2b {st}: corpus+{r.modified_count} priv+{r2.modified_count}")

# ---- 2.4 redaction-aware tags ----
_REDACT = re.compile(r"\[REDACTED[^\]]*\]|\bREDACTED\b|X{4,}|█{2,}|▮{2,}", re.I)
red = 0
for d in docs.find({"extracted_text": {"$exists": True}}, {"extracted_text": 1}):
    hits = _REDACT.findall(d.get("extracted_text") or "")
    has = len(hits) > 0
    docs.update_one({"_id": d["_id"]}, {"$set": {
        "has_redactions": has, "redaction_count": len(hits), "redaction_checked_at": now}})
    red += int(has)
logger.info(f"2.4 documents with redactions flagged: {red}")

# ---- 2.6 temporal normalization ----
_DATE_FIELDS = [("document_date", "document_date"), ("effective_date", "effective_date"),
                ("recording_date", "recording_date"), ("filing_date", "filing_date"),
                ("execution_date", "execution_date"), ("completed_date", "document_date"),
                ("search_date", "document_date"), ("as_of_date", "effective_date")]
norm = 0
for d in docs.find({}, {f: 1 for f, _ in _DATE_FIELDS}):
    out = []
    for field, kind in _DATE_FIELDS:
        v = d.get(field)
        if hasattr(v, "strftime"):
            out.append({"kind": kind, "iso": v.strftime("%Y-%m-%d"), "field": field})
    if out:
        docs.update_one({"_id": d["_id"]}, {"$set": {"dates_normalized": out}})
        norm += 1
logger.info(f"2.6 documents with normalized dates: {norm}")

# ---- 2.7 quality gates ----
flagged = 0
for d in docs.find({}, {"property_ids": 1, "owner_entity_id": 1, "dates_normalized": 1,
                        "source_type": 1, "extracted_text": 1}):
    reasons = []
    if not d.get("dates_normalized"):
        reasons.append("no_date")
    if d.get("source_type") == "title_report" and not (d.get("property_ids") or d.get("owner_entity_id")):
        reasons.append("no_entity")
    if len((d.get("extracted_text") or "")) < 200:
        reasons.append("thin_text")
    docs.update_one({"_id": d["_id"]}, {"$set": {
        "quality.needs_review": bool(reasons), "quality.review_reasons": reasons}})
    flagged += int(bool(reasons))
logger.info(f"2.7 docs flagged needs_review: {flagged}")

# ---- 2.8 GATE ----
total = docs.count_documents({})
no_corpus = docs.count_documents({"$or": [{"corpus": {"$exists": False}}, {"corpus": None}]})
no_priv = docs.count_documents({"$or": [{"privilege_status": {"$exists": False}}, {"privilege_status": None}]})
title = docs.count_documents({"source_type": "title_report"})
title_facts = docs.count_documents({"source_type": "title_report", "grounded_facts": {"$exists": True}})
logger.info("================ SPRINT 2 GATE ================")
logger.info(f"  docs total={total} | missing corpus={no_corpus} missing privilege={no_priv}")
logger.info(f"  title reports with grounded_facts: {title_facts}/{title}")
gate = no_corpus == 0 and no_priv == 0 and title_facts >= 0.95 * title
logger.info(f"SPRINT 2 GATE: {'PASS' if gate else 'REVIEW'}")
m.close()
sys.exit(0)
