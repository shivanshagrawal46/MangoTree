"""Sprint 4 · 4.1 — stamp doc_authority_score on every chunk from its source
type, so the reranker/rescorer can weight by the legal authority hierarchy
(court order > recorded deed/mortgage > lien/DA > title > insurance > contract
> bank > llc > email_attachment > email_body > draft). Idempotent update_many."""
import sys
from collections import Counter
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.schema import authority_for, DEFAULT_AUTHORITY
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
ch = m.db["email_chunks_v2"]

types = ch.distinct("source_type")
dtypes = ch.distinct("doc_source_type")
all_types = set(t for t in types + dtypes if t)
logger.info(f"source types present: {sorted(all_types)}")

n = 0
for st in sorted(all_types):
    score = authority_for(st)
    # doc_source_type takes priority when present
    r = ch.update_many({"doc_source_type": st}, {"$set": {"doc_authority_score": score}})
    r2 = ch.update_many({"source_type": st, "doc_source_type": {"$exists": False}},
                        {"$set": {"doc_authority_score": score}})
    n += r.modified_count + r2.modified_count
    logger.info(f"  {st}: authority={score} ({r.modified_count + r2.modified_count} chunks)")

# default for any remaining
r = ch.update_many({"doc_authority_score": {"$exists": False}},
                   {"$set": {"doc_authority_score": DEFAULT_AUTHORITY}})
logger.info(f"stamped doc_authority_score on {n + r.modified_count} chunks")
try:
    from pymongo import ASCENDING
    ch.create_index([("doc_authority_score", ASCENDING)], name="ix_authority")
except Exception:  # noqa: BLE001
    pass
m.close()
sys.exit(0)
