"""Sprint 8 · 8.5 — surface OCR confidence. Copy each document's ocr_confidence
+ dominant extraction method onto its chunks, so answers can mark facts that
rest on low-confidence OCR pages (never silently hardened). Idempotent."""
import sys
from collections import Counter
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
docs, ch = m.db["documents"], m.db["email_chunks_v2"]

n = 0
for d in docs.find({"ocr_confidence": {"$exists": True}},
                   {"ocr_confidence": 1, "pages": 1}):
    conf = d.get("ocr_confidence")
    methods = Counter((p.get("method") for p in (d.get("pages") or []) if p.get("method")))
    dom = methods.most_common(1)[0][0] if methods else None
    low = bool(isinstance(conf, (int, float)) and conf < 0.6)
    r = ch.update_many({"document_id": d["_id"]}, {"$set": {
        "ocr_confidence": conf, "ocr_method": dom, "ocr_low_confidence": low}})
    n += r.modified_count
logger.info(f"8.5 stamped ocr confidence on {n} chunks")
logger.info(f"  low-confidence chunks: {ch.count_documents({'ocr_low_confidence': True})}")
logger.info(f"  by method: {dict(Counter(c.get('ocr_method') for c in ch.find({'ocr_method': {'$exists': True}}, {'ocr_method': 1})))}")
m.close()
sys.exit(0)
