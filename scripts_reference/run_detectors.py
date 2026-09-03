"""Sprint 4 — run fraud detectors over grounded facts, write to findings ledger.

Safe to run on partial grounded data (re-run after extraction completes; findings
are idempotent and human review status is preserved)."""
import sys
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.detectors import run_all
from src.utils.logger import logger

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
counts = run_all(m)
fc = m.db["findings"]
logger.info(f"detectors run: {counts}")
logger.info(f"findings ledger: total={fc.count_documents({})} "
            f"critical={fc.count_documents({'severity': 'critical'})} "
            f"high={fc.count_documents({'severity': 'high'})} "
            f"pending={fc.count_documents({'status': 'pending'})}")
# show a few examples
for f in fc.find({}).sort("severity", 1).limit(8):
    logger.info(f"  [{f['severity']}] {f['finding_type']}: {f['title']}")
m.close()
sys.exit(0)
