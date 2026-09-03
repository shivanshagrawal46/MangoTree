"""Retract the stale 11H LLC anachronism finding after the DOS-date fix.

The finding was created when 11H's formation date was wrongly 2029. With the
date corrected to 2019 the anachronism no longer holds, but the findings ledger
keeps prior findings (to preserve human status), so we explicitly mark it
rejected with an auditable reason rather than letting a false 'backdating'
finding linger.

  python -m scripts.retract_11h_finding            # DRY-RUN
  python -m scripts.retract_11h_finding --live
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger


def main() -> int:
    live = "--live" in sys.argv
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    findings = m.db["findings"]

    q = {"finding_type": "anachronism",
         "$or": [{"title": {"$regex": "11H", "$options": "i"}},
                 {"entity_ids": "ent_llc_11h_llc"},
                 {"entity_id": "ent_llc_11h_llc"}]}
    hits = list(findings.find(q))
    logger.info(f"{len(hits)} stale 11H anachronism finding(s) to retract")
    for f in hits:
        logger.info(f"  {f.get('_id')} | {f.get('title')} | status={f.get('status')}")
        if live:
            findings.update_one({"_id": f["_id"]}, {"$set": {
                "status": "rejected",
                "rejected_reason": "data_correction: 11H LLC DOS filing date "
                                   "corrected 2029-03-14 -> 2019-03-14; anachronism "
                                   "was a false positive from the date typo.",
                "rejected_at": now, "updated_at": now}})
    logger.info("APPLIED" if live else "DRY-RUN — re-run with --live")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
