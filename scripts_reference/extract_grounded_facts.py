"""Sprint 2 · 2.1 — run grounded field extraction over title (+litigation) docs.

Stores documents.grounded_facts = {chain_of_title, mortgages, liens, lis_pendens,
judgments, assignments} (each fact verified to appear verbatim in the source) and
stamps grounded_at. Resumable + idempotent: re-running skips docs already done.

  python -m scripts.extract_grounded_facts --limit 3   # sample
  python -m scripts.extract_grounded_facts             # all pending
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extract.grounded_facts import GroundedExtractor, _FACT_KEYS
from src.utils.logger import logger

TARGET_TYPES = ["title_report"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reextract", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    s = Settings.load()
    ex = GroundedExtractor(s.anthropic_api_key, model="claude-sonnet-4-6")
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs = m.db["documents"]

    q = {"source_type": {"$in": TARGET_TYPES}}
    if not args.reextract:
        q["grounded_at"] = {"$exists": False}
    pending = list(docs.find(q, {"_id": 1}).sort("_id", 1))
    if args.limit:
        pending = pending[: args.limit]
    total = docs.count_documents({"source_type": {"$in": TARGET_TYPES}})
    logger.info(f"{len(pending)} docs to extract ({total - len(pending)} done)")

    done = totals = 0
    agg = {k: 0 for k in _FACT_KEYS}
    for n, ref in enumerate(pending, 1):
        d = docs.find_one({"_id": ref["_id"]}, {"extracted_text": 1})
        text = d.get("extracted_text") or ""
        try:
            facts = ex.extract(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"  [{n}/{len(pending)}] {ref['_id']}: extract failed {str(exc)[:80]}")
            continue
        counts = {k: len(facts.get(k) or []) for k in _FACT_KEYS}
        for k in _FACT_KEYS:
            agg[k] += counts[k]
        totals += sum(counts.values())
        docs.update_one({"_id": ref["_id"]}, {"$set": {
            "grounded_facts": {k: facts.get(k, []) for k in _FACT_KEYS},
            "grounded_dropped_ungrounded": facts.get("_dropped_ungrounded", 0),
            "grounded_at": now}})
        done += 1
        logger.info(f"  [{n}/{len(pending)}] {ref['_id'][:34]} -> "
                    f"{counts} (dropped {facts.get('_dropped_ungrounded',0)})")

    logger.info("================ GROUNDED EXTRACTION DONE (run) ================")
    logger.info(f"docs={done} facts={totals} by_type={agg}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
