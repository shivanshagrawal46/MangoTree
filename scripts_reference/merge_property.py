"""Merge a duplicate PROPERTY entity into a survivor and re-point everything.

Two records can describe the same parcel when the address spelling differs
("Pkwy" vs "Parkway") or the parcel is written in different notations
(SCTM 0400-095.00-03.00-068.000 vs tax-map 472689 95.-3.-68.). This merges
the loser into the survivor: re-points chunks, relationships, documents,
events, findings; folds the loser's address/parcel in as aliases; retires the
loser. Idempotent. Dry-run by default.

  python -m scripts.merge_property --survivor <id> --loser <id>
  python -m scripts.merge_property --survivor <id> --loser <id> --live
Default pair = the 60 Central Pkwy / Parkway duplicate.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger

DEFAULT_SURVIVOR = "ent_prop_0400095000300068000"   # 60 Central Pkwy (richer)
DEFAULT_LOSER = "ent_prop_47268995368"               # 60 Central Parkway


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--survivor", default=DEFAULT_SURVIVOR)
    ap.add_argument("--loser", default=DEFAULT_LOSER)
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    surv, lose = args.survivor, args.loser
    now = datetime.now(timezone.utc)

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents = m.db["entities"]
    chunks, rels, docs = m.db["email_chunks_v2"], m.db["relationships"], m.db["documents"]
    doss, events, findings = m.db["property_dossier"], m.db["events"], m.db["findings"]

    se, le = ents.find_one({"_id": surv}), ents.find_one({"_id": lose})
    if not se or not le:
        logger.error(f"survivor or loser not found (surv={bool(se)} lose={bool(le)})")
        m.close(); return 1

    counts = {
        "chunks_entity_ids": chunks.count_documents({"entity_ids": lose}),
        "chunks_refs": chunks.count_documents({"entity_refs.properties": lose}),
        "chunks_primary": chunks.count_documents({"primary_property_id": lose}),
        "edges": rels.count_documents({"$or": [{"src": lose}, {"dst": lose}]}),
        "docs": docs.count_documents({"property_ids": lose}),
        "events": events.count_documents({"property_id": lose}),
        "findings": findings.count_documents({"property_id": lose}),
    }
    logger.info(f"=== merge property ({'LIVE' if args.live else 'DRY-RUN'}) ===")
    logger.info(f"SURVIVOR {surv}  '{se.get('canonical_address')}'  parcel={se.get('parcel_id')}")
    logger.info(f"LOSER    {lose}  '{le.get('canonical_address')}'  parcel={le.get('parcel_id')}")
    logger.info(f"to re-point: {counts}")

    if not args.live:
        logger.info("DRY-RUN — re-run with --live to apply.")
        m.close(); return 0

    # 1. chunks: add survivor, drop loser (entity_ids + entity_refs.properties)
    chunks.update_many({"entity_ids": lose}, {"$addToSet": {"entity_ids": surv}})
    chunks.update_many({"entity_ids": lose}, {"$pull": {"entity_ids": lose}})
    chunks.update_many({"entity_refs.properties": lose},
                       {"$addToSet": {"entity_refs.properties": surv}})
    chunks.update_many({"entity_refs.properties": lose},
                       {"$pull": {"entity_refs.properties": lose}})
    chunks.update_many({"primary_property_id": lose},
                       {"$set": {"primary_property_id": surv}})
    chunks.update_many({"property_ids": lose}, {"$addToSet": {"property_ids": surv}})
    chunks.update_many({"property_ids": lose}, {"$pull": {"property_ids": lose}})

    # 2. relationships: re-point src/dst (then drop any self/dupe edges)
    rels.update_many({"src": lose}, {"$set": {"src": surv}})
    rels.update_many({"dst": lose}, {"$set": {"dst": surv}})
    rels.delete_many({"$expr": {"$eq": ["$src", "$dst"]}})

    # 3. documents: property_ids loser -> survivor
    docs.update_many({"property_ids": lose}, {"$addToSet": {"property_ids": surv}})
    docs.update_many({"property_ids": lose}, {"$pull": {"property_ids": lose}})

    # 4. events + findings
    events.update_many({"property_id": lose}, {"$set": {"property_id": surv}})
    findings.update_many({"property_id": lose}, {"$set": {"property_id": surv}})

    # 4b. copy property-level financial / status fields the survivor is
    # MISSING (these come from the equity Excel + title and are stored on the
    # entity, not in chunks — so re-pointing chunks doesn't move them).
    FIN_FIELDS = ["equity", "mkt_value", "mkt_value_zillow", "mortgage_amount",
                  "re_taxes_owed", "lender", "lis_pendens", "active_foreclosure",
                  "fraudulent_flag", "judgement", "property_tax", "equity_source",
                  "equity_as_of", "parcel_id", "county",
                  "title_doc_ids", "insurance_doc_ids", "equity_doc_ids",
                  "litigation_doc_ids"]
    fin_set = {}
    for f in FIN_FIELDS:
        sv, lv = se.get(f), le.get(f)
        if (sv in (None, "", [])) and lv not in (None, "", []):
            fin_set[f] = lv
        elif f.endswith("_doc_ids"):  # union doc-id lists so nothing is lost
            merged = sorted(set(sv or []) | set(lv or []))
            if merged != (sv or []):
                fin_set[f] = merged
    if fin_set:
        ents.update_one({"_id": surv}, {"$set": {**fin_set, "updated_at": now}})
        logger.info(f"copied {len(fin_set)} financial/doc fields to survivor: {list(fin_set)}")

    # 5. fold loser's address + parcel into survivor as aliases
    variants = set(se.get("address_variants") or [])
    variants.update(le.get("address_variants") or [])
    if le.get("canonical_address"):
        variants.add(le["canonical_address"])
    parcel_aliases = set(se.get("parcel_aliases") or [])
    if le.get("parcel_id"):
        parcel_aliases.add(le["parcel_id"])
    ents.update_one({"_id": surv}, {"$set": {
        "address_variants": sorted(variants),
        "parcel_aliases": sorted(parcel_aliases),
        "updated_at": now}})

    # 6. retire loser + drop its now-stale dossier
    doss.delete_one({"_id": lose})
    ents.update_one({"_id": lose}, {"$set": {
        "is_active": False, "merged_into": surv, "superseded_by": [surv],
        "merge_reason": "duplicate_property_address_parcel_notation", "updated_at": now}})

    logger.info("APPLIED. Re-run `python -m scripts.build_dossier` to refresh the "
                "survivor's dossier (now aggregates the loser's docs).")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
