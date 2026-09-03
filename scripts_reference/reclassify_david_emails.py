"""Reclassify David-sender emails from the safe 'privileged' default to their
true posture: adverse-party admissions (usable in Clean/shareable output).

Sprint 2.3 defaulted ALL email/attachment chunks to corpus=legal_correspondence
+ privilege_status=privileged (over-protective, pending confirmed David sender
addresses). Per user (2026-06-17): any sender @ipellc.net is David's side.

So chunks whose sender domain is a confirmed David domain are reclassified:
  corpus            -> fraud_communications
  privilege_status  -> adverse_party        (NOT privileged -> shareable)
  evidentiary_class -> party_admission
  privilege_basis records the confirmation so it's auditable + reversible.

  python -m scripts.reclassify_david_emails            # DRY-RUN (counts + sample)
  python -m scripts.reclassify_david_emails --live     # apply
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

# Confirmed David sender domains (extend as more are confirmed).
DAVID_DOMAINS = ["ipellc.net"]


def _domain_regex(domains):
    # match from_email ending with @<domain> (case-insensitive)
    alt = "|".join(re.escape(d) for d in domains)
    return re.compile(rf"@(?:{alt})\s*$", re.I)


def main() -> int:
    live = "--live" in sys.argv
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    chunks = m.db["email_chunks_v2"]

    rx = _domain_regex(DAVID_DOMAINS)
    dom_clause = [{"from_email": {"$regex": f"@{re.escape(d)}$", "$options": "i"}}
                  for d in DAVID_DOMAINS]
    dom_clause += [{"occurrences.from_email": {"$regex": f"@{re.escape(d)}$", "$options": "i"}}
                   for d in DAVID_DOMAINS]
    q = {"$or": dom_clause}

    total = chunks.count_documents(q)
    already = chunks.count_documents({**q, "privilege_status": "adverse_party"})
    logger.info(f"David-sender domains: {DAVID_DOMAINS}")
    logger.info(f"matched chunks: {total} (already adverse_party: {already})")

    # sample senders for sanity
    seen = set()
    for c in chunks.find(q, {"from_email": 1, "subject": 1}).limit(800):
        fe = (c.get("from_email") or "").lower()
        if rx.search(fe) and fe not in seen:
            seen.add(fe)
            if len(seen) <= 12:
                logger.info(f"   sender: {c.get('from_email')}  |  {str(c.get('subject'))[:50]}")
    logger.info(f"distinct David senders (sample): {len(seen)}")

    if live:
        res = chunks.update_many(q, {"$set": {
            "corpus": "fraud_communications",
            "privilege_status": "adverse_party",
            "evidentiary_class": "party_admission",
            "privilege_basis": "david_sender_confirmed_domain_2026_06_17",
            "privilege_reclassified_at": now}})
        logger.info(f"APPLIED: reclassified {res.modified_count} chunks -> "
                    f"fraud_communications / adverse_party")
        import collections
        dist = collections.Counter(c.get("privilege_status")
                                   for c in chunks.find({}, {"privilege_status": 1}))
        logger.info(f"privilege distribution now: {dict(dist)}")
        shareable = chunks.count_documents({"privilege_status": {"$ne": "privileged"}})
        logger.info(f"Clean-mode shareable chunks: {shareable}/{chunks.estimated_document_count()}")
    else:
        logger.info("DRY-RUN — re-run with --live to apply.")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
