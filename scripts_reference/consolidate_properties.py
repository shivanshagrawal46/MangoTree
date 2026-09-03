"""
Sprint 3 · Step 1 — CANONICAL PROPERTY CONSOLIDATION.

Build ONE canonical property node per real property from ALL sources (title
reports, insurance, equity schedule, litigation), so every record attaches to
the same hub and a property query returns everything.

Method (union-find over property signals):
  * Signal from each doc = (parcel_digits, address_core).
  * Union signals sharing parcel_digits OR address_core.
  * MUST-NOT-LINK firewall: two signals with DIFFERENT parcel_digits never merge.
  * Each cluster -> one canonical property entity:
        _id = ent_prop_<parcel_digits>  (parcel known) else ent_prop_a_<addr-slug>
    carrying canonical_address, parcel_id, county, address_variants[], owner,
    is_david, equity facts, and the attached doc-id lists by type.
  * Re-point every document.property_ids to canonicals; rebuild ABOUT_PROPERTY /
    HAS_INSURANCE / OWNS / LITIGATION_ABOUT edges; delete superseded property
    entities (kind=property) — emails + LLC/person entities untouched.

Usage:  python -m scripts.consolidate_properties --live   (default dry-run)
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.logger import logger
from scripts.build_entities_from_llc import slug, norm_addr
from scripts.ingest_titles_full import norm_address, addr_core
from scripts.reparse_titles import parcel_digits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    args = ap.parse_args()
    s = Settings.load()
    now = datetime.now(timezone.utc)
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs, ents, rels = m.db["documents"], m.db["entities"], m.db["relationships"]

    # ---- 1. gather property signals from every source ----
    # signal = dict(pdg, ac, kind, doc_id, address, parcel, county, owner_eid, is_david, equity?)
    signals: List[Dict[str, Any]] = []

    def add(pdg, ac, kind, doc_id, address=None, parcel=None, county=None,
            owner_eid=None, is_david=False, extra=None):
        if not (pdg or ac):
            return
        signals.append({"pdg": pdg or "", "ac": ac or "", "kind": kind, "doc_id": doc_id,
                        "address": address, "parcel": parcel, "county": county,
                        "owner_eid": owner_eid, "is_david": is_david, "extra": extra or {}})

    for d in docs.find({"source_type": "title_report"},
                       {"property_address": 1, "address_norm": 1, "parcel_id": 1, "county": 1,
                        "owner_entity_id": 1, "owner_is_david": 1}):
        add(parcel_digits(d.get("parcel_id") or ""), addr_core(d.get("address_norm") or ""),
            "title", d["_id"], d.get("property_address"), d.get("parcel_id"), d.get("county"),
            d.get("owner_entity_id"), d.get("owner_is_david"))

    for d in docs.find({"source_type": "insurance"},
                       {"covered_addresses": 1}):
        for a in (d.get("covered_addresses") or []):
            add(None, addr_core(norm_address(a)), "insurance", d["_id"], address=a)

    eq = docs.find_one({"source_type": "equity_schedule"}, {"equity_rows": 1})
    for row in (eq or {}).get("equity_rows", []) if eq else []:
        a = f"{row.get('street') or ''} {row.get('city') or ''}".strip()
        add(parcel_digits(row.get("parcel") or ""), addr_core(norm_address(a)),
            "equity", eq["_id"], address=row.get("street"), parcel=row.get("parcel"),
            owner_eid=row.get("owner_entity_id"), is_david=False,
            extra={k: row.get(k) for k in ("equity", "mortgage", "re_taxes_owed",
                   "mkt_value_john", "lender", "lis_pendens", "active_foreclosure", "fraudulent")})

    for d in docs.find({"source_type": "litigation_update", "property_ids": {"$ne": []}},
                       {"property_ids": 1}):
        for pid in d.get("property_ids", []):
            e = ents.find_one({"_id": pid}, {"parcel_id": 1, "canonical_address": 1})
            if e:
                add(parcel_digits(e.get("parcel_id") or ""),
                    addr_core(norm_address(e.get("canonical_address") or "")),
                    "litigation", d["_id"])

    logger.info(f"gathered {len(signals)} property signals "
                f"(title/insurance/equity/litigation)")

    # ---- 2. union-find with must-not-link (different parcel) firewall ----
    parent = list(range(len(signals)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_pdg, by_ac = defaultdict(list), defaultdict(list)
    for i, sg in enumerate(signals):
        if sg["pdg"]:
            by_pdg[sg["pdg"]].append(i)
        if sg["ac"]:
            by_ac[sg["ac"]].append(i)
    for grp in by_pdg.values():
        for j in grp[1:]:
            union(grp[0], j)
    for grp in by_ac.values():
        for j in grp[1:]:
            a, b = grp[0], j
            pa, pb = signals[a]["pdg"], signals[b]["pdg"]
            if pa and pb and pa != pb:
                continue  # firewall: different parcels never merge by address
            union(a, b)

    clusters: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(signals)):
        clusters[find(i)].append(i)
    logger.info(f"=> {len(clusters)} canonical properties")

    # ---- 3. build canonical entities + attachment maps ----
    canon: Dict[int, Dict[str, Any]] = {}
    doc_to_canon: Dict[str, set] = defaultdict(set)
    for root, members in clusters.items():
        sgs = [signals[i] for i in members]
        pdg = next((x["pdg"] for x in sgs if x["pdg"]), "")
        ac = next((x["ac"] for x in sgs if x["ac"]), "")
        cid = "ent_prop_" + pdg if pdg else "ent_prop_a_" + slug(ac or root)
        # best address = longest seen
        addrs = [x["address"] for x in sgs if x.get("address")]
        best_addr = max(addrs, key=len) if addrs else (ac or "")
        parcel = next((x["parcel"] for x in sgs if x.get("parcel")), None)
        county = next((x["county"] for x in sgs if x.get("county")), None)
        owner_eid = next((x["owner_eid"] for x in sgs if x.get("owner_eid")), None)
        is_david = any(x.get("is_david") for x in sgs)
        equity = next((x["extra"] for x in sgs if x["kind"] == "equity" and x["extra"]), {})
        title_ids = sorted({x["doc_id"] for x in sgs if x["kind"] == "title"})
        ins_ids = sorted({x["doc_id"] for x in sgs if x["kind"] == "insurance"})
        eq_ids = sorted({x["doc_id"] for x in sgs if x["kind"] == "equity"})
        lit_ids = sorted({x["doc_id"] for x in sgs if x["kind"] == "litigation"})
        canon[root] = {
            "_id": cid, "kind": "property", "matter_id": DEFAULT_MATTER_ID,
            "canonical_address": best_addr, "address_norm": norm_addr(best_addr),
            "address_core": ac, "parcel_id": parcel, "parcel_digits": pdg or None,
            "county": county, "address_variants": sorted(set(a for a in addrs if a)),
            "owner_entity_id": owner_eid, "is_david": is_david,
            "is_david_network": is_david, "david_linked": is_david,
            "title_doc_ids": title_ids, "insurance_doc_ids": ins_ids,
            "equity_doc_ids": eq_ids, "litigation_doc_ids": lit_ids,
            "has_title": bool(title_ids), "has_insurance": bool(ins_ids),
            "has_equity": bool(eq_ids), "has_litigation": bool(lit_ids),
            "equity": equity.get("equity"), "mkt_value": equity.get("mkt_value_john"),
            "mortgage_amount": equity.get("mortgage"), "re_taxes_owed": equity.get("re_taxes_owed"),
            "lender": equity.get("lender"), "lis_pendens": equity.get("lis_pendens"),
            "active_foreclosure": equity.get("active_foreclosure"),
            "fraudulent_flag": equity.get("fraudulent"),
            "source": "consolidation", "updated_at": now,
        }
        for x in sgs:
            doc_to_canon[x["doc_id"]].add(cid)

    # ---- report ----
    full = sum(1 for c in canon.values() if c["has_title"] and c["has_insurance"])
    title_only = sum(1 for c in canon.values() if c["has_title"] and not c["has_insurance"])
    ins_only = sum(1 for c in canon.values() if c["has_insurance"] and not c["has_title"])
    with_eq = sum(1 for c in canon.values() if c["has_equity"])
    david = sum(1 for c in canon.values() if c["is_david"])
    logger.info(f"canonical properties: {len(canon)}  | title+insurance={full}  "
                f"title_only={title_only}  insurance_only={ins_only}  with_equity={with_eq}  david={david}")
    sample = [c for c in canon.values() if c["has_title"] and c["has_insurance"] and c["has_equity"]][:5]
    for c in sample:
        logger.info(f"   {c['canonical_address'][:40]:42} title={len(c['title_doc_ids'])} "
                    f"ins={len(c['insurance_doc_ids'])} equity={c['equity']} david={c['is_david']}")

    if args.dry_run:
        logger.info("DRY RUN — no writes. Re-run --live to consolidate.")
        m.close()
        return 0

    # ---- 4. write canonicals, re-point docs, rebuild edges, drop stale ----
    keep_ids = set()
    for c in canon.values():
        ents.update_one({"_id": c["_id"]}, {"$set": c, "$setOnInsert": {"created_at": now}}, upsert=True)
        keep_ids.add(c["_id"])
    for doc_id, cids in doc_to_canon.items():
        docs.update_one({"_id": doc_id}, {"$set": {"property_ids": sorted(cids)}})
    # rebuild property edges
    rels.delete_many({"type": {"$in": ["ABOUT_PROPERTY", "HAS_INSURANCE", "LITIGATION_ABOUT", "OWNS"]}})
    for root, c in canon.items():
        for did in c["title_doc_ids"]:
            rels.update_one({"type": "ABOUT_PROPERTY", "src": did, "dst": c["_id"]},
                            {"$set": {"type": "ABOUT_PROPERTY", "src": did, "dst": c["_id"], "updated_at": now}}, upsert=True)
        for did in c["insurance_doc_ids"]:
            rels.update_one({"type": "HAS_INSURANCE", "src": c["_id"], "dst": did},
                            {"$set": {"type": "HAS_INSURANCE", "src": c["_id"], "dst": did, "updated_at": now}}, upsert=True)
        for did in c["litigation_doc_ids"]:
            rels.update_one({"type": "LITIGATION_ABOUT", "src": did, "dst": c["_id"]},
                            {"$set": {"type": "LITIGATION_ABOUT", "src": did, "dst": c["_id"], "updated_at": now}}, upsert=True)
        if c.get("owner_entity_id"):
            rels.update_one({"type": "OWNS", "src": c["owner_entity_id"], "dst": c["_id"]},
                            {"$set": {"type": "OWNS", "src": c["owner_entity_id"], "dst": c["_id"], "updated_at": now}}, upsert=True)
    # drop stale property entities (replaced by canonicals)
    stale = ents.delete_many({"kind": "property", "_id": {"$nin": list(keep_ids)}}).deleted_count
    logger.info(f"wrote {len(canon)} canonical properties; re-pointed {len(doc_to_canon)} docs; "
                f"removed {stale} stale property entities")
    logger.info(f"property entities now: {ents.count_documents({'kind':'property'})}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
