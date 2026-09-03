"""
FREE re-parse of all stored title reports (no OCR, no API cost).

Why: Claude Vision OCR renders header tables with '|' pipes; the original
header regexes (written for born-digital text) missed fields on the OCR'd
text — Prowess updates typed as Full Search, owners missing on 145 docs.
The parsers are now pipe-aware; this re-runs them over the STORED
extracted_text and refreshes all derived fields + linkage + version chains.

Idempotent — safe to re-run any time (also used after new-folder ingests to
rebuild global version chains).

Usage:
  python -m scripts.reparse_titles            # re-parse + relink + rebuild chains
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

import config.settings  # noqa: F401  (loads .env)
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger
from scripts.ingest_title_reports import (
    parse_report, parse_prowess, detect_vendor, normalize_parcel, _parse_date,
    resolve_owner_entity, resolve_property_entity, derive_fraud_flags,
    norm_name, DOCS_COLLECTION, ENTITIES_COLLECTION,
)
from scripts.ingest_titles_full import norm_address

_STREET_SUFFIXES = {
    "rd", "road", "dr", "drive", "st", "street", "ave", "avenue", "ln", "lane",
    "ct", "court", "blvd", "boulevard", "pkwy", "parkway", "pl", "place",
    "way", "path", "cir", "circle", "ter", "terrace", "hwy",
}
_DIR_MAP = {"w": "west", "e": "east", "n": "north", "s": "south",
            "nw": "northwest", "ne": "northeast", "sw": "southwest", "se": "southeast"}


_DIRECTIONALS = {"west", "east", "north", "south", "northwest", "northeast",
                 "southwest", "southeast"}


def addr_core(addr_norm: str) -> str:
    """Property key = house number + directionals (anywhere, canonicalized) +
    FIRST real street word. Robust across filename vs full address, 'W'=='West',
    and directional placement ('83 S Ann Drive' == '83 Ann Drive S'). Parcel
    firewalls (union step) guard rare house#+street collisions."""
    toks = [_DIR_MAP.get(t, t) for t in (addr_norm or "").split() if t]
    if not toks:
        return ""
    house = toks[0]
    dirs = sorted({t for t in toks[1:] if t in _DIRECTIONALS})
    street = next((t for t in toks[1:] if t not in _DIRECTIONALS and t not in _STREET_SUFFIXES), "")
    return " ".join([house] + dirs + ([street] if street else []))


def parcel_digits(parcel: str) -> str:
    """Parcel/APN reduced to digits only — ProTitle '0100-015.00-07.00-021.000'
    and Prowess '0100-015-00-07-00-021-000' styles compare equal."""
    import re as _re
    return _re.sub(r"\D", "", parcel or "")


def main() -> int:
    s = Settings.load()
    now = datetime.now(timezone.utc)
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    docs = m.db[DOCS_COLLECTION]
    ents = m.db[ENTITIES_COLLECTION]

    q = {"source_type": "title_report"}
    total = docs.count_documents(q)
    logger.info(f"Re-parsing {total} stored title reports (free, from stored text)")

    stats = Counter()
    identities: Dict[tuple, List[str]] = defaultdict(list)
    rows: List[Dict[str, Any]] = []

    for d in docs.find(q, {"extracted_text": 1, "vendor": 1, "custody": 1}):
        text = d.get("extracted_text") or ""
        # RE-DETECT vendor from the text head (don't trust the stored value —
        # embedded ProTitle inside Prowess updates and unbranded Prowess
        # formats were misclassified at ingest).
        vendor = detect_vendor(text) or d.get("vendor") or "protitle"
        rep = parse_prowess(text) if vendor == "prowess" else parse_report(text)
        has_embedded = vendor == "prowess" and "protitleusa" in text.lower()

        # Embedded ORIGINAL inside an update: a 2nd 'Order Type: Full Search'
        # header later in the same PDF (e.g. 520 E 81st: update at p.1 +
        # original full search embedded at p.14). The original is then NOT
        # missing — it lives inside this document.
        embedded_original = None
        if vendor == "prowess" and rep.get("is_update"):
            import re as _re
            normt = _re.sub(r"[|*]", " ", text)
            normt = _re.sub(r"\s+", " ", normt)
            hits = list(_re.finditer(r"Order Type:\s*Full", normt, _re.I))
            if hits:
                seg = normt[hits[0].start(): hits[0].start() + 4000]
                er = parse_prowess(seg)
                embedded_original = {
                    "order_type": "Full Search",
                    "search_date": er.get("search_date"),
                    "owner_name": er.get("owner_name"),
                    "property_address": er.get("property_address"),
                }

        addr = rep.get("property_address")
        addr_key = norm_address(addr)
        parcel = rep.get("parcel_id")
        is_update = (bool(rep.get("is_update")) if vendor == "protitle"
                     else rep.get("order_type") == "Update Search")
        eff = (_parse_date(rep.get("completed_date")) if vendor == "protitle"
               else (_parse_date(rep.get("new_effective_date")) or _parse_date(rep.get("search_date"))))

        owner_res = resolve_owner_entity(ents, rep.get("owner_name") or "", now,
                                         address=addr or "")
        prop_id = resolve_property_entity(ents, rep, owner_res["is_david"], now)

        # David-network signal from 'Names Searched' too (the searched party is
        # the party of interest even when title vests in a bank/HOA).
        names_searched = rep.get("names_searched")
        david_in_names = False
        if names_searched:
            ns_norm = norm_name(names_searched)
            for dav in ents.find({"is_david": True}, {"name_norm": 1}):
                nn = dav.get("name_norm") or ""
                if nn and nn in ns_norm:
                    david_in_names = True
                    break

        if vendor == "protitle":
            ident = ("PT", addr_key, rep.get("order_number"),
                     (rep.get("completed_date") or "").strip(), (rep.get("index_date") or "").strip())
        else:
            ident = ("PW", addr_key, rep.get("order_type"), (rep.get("search_date") or "").strip(),
                     (rep.get("old_effective_date") or "").strip(), (rep.get("new_effective_date") or "").strip())
        identities[ident].append(d["_id"])

        docs.update_one({"_id": d["_id"]}, {"$set": {
            "vendor": vendor,
            "issuing_authority": ("ProTitle USA" if vendor == "protitle"
                                  else "Prowess Title Abstracts"),
            "names_searched": names_searched,
            "david_in_names_searched": david_in_names,
            "order_number": rep.get("order_number"),
            "order_type": rep.get("order_type") or ("Update Search" if is_update else "Full Search"),
            "instrument_subtype": "update_search" if is_update else "full_search",
            "is_update": is_update,
            "completed_date": _parse_date(rep.get("completed_date")),
            "index_date": _parse_date(rep.get("index_date")),
            "search_date": _parse_date(rep.get("search_date")),
            "old_effective_date": _parse_date(rep.get("old_effective_date")),
            "new_effective_date": _parse_date(rep.get("new_effective_date")),
            "effective_date": eff,
            "update_from_index_date": rep.get("update_from_index_date"),
            "property_address": addr, "address_norm": addr_key,
            "parcel_id": parcel, "county": rep.get("county"),
            "owner_name_raw": rep.get("owner_name"),
            "owner_entity_id": owner_res["entity_id"], "owner_is_david": owner_res["is_david"],
            "property_ids": [prop_id] if prop_id else [],
            "title_defect_category": rep.get("title_defect_category"),
            "fraud_flags": derive_fraud_flags(rep, text),
            "has_embedded_protitle": has_embedded,
            "has_embedded_original": bool(embedded_original or has_embedded),
            "embedded_original": embedded_original,
            "quality.has_parcel": bool(parcel), "quality.has_owner": bool(rep.get("owner_name")),
            "quality.needs_review": not rep.get("owner_name"),
            "reparsed_at": now, "updated_at": now,
        }})
        rows.append({"doc_id": d["_id"], "eff": eff, "is_update": is_update,
                     "parcel_digits": parcel_digits(parcel or ""),
                     "addr_core": addr_core(addr_key),
                     "addr": addr, "owner_eid": owner_res["entity_id"],
                     "prop_id": prop_id,
                     "has_embedded_orig": bool(embedded_original or has_embedded)})
        stats["docs"] += 1
        stats["owner_found"] += bool(rep.get("owner_name"))
        stats["owner_david"] += bool(owner_res["is_david"])
        stats["david_linked"] += bool(owner_res["is_david"] or david_in_names)
        stats["parcel_found"] += bool(parcel)
        stats["updates"] += bool(is_update)
        stats["embedded"] += bool(has_embedded)

    # identity collision check (post-fix duplicates would mean dedup gap)
    collisions = {k: v for k, v in identities.items() if len(v) > 1}
    logger.info(f"identity collisions after re-parse: {len(collisions)}")
    for k, v in list(collisions.items())[:10]:
        logger.warning(f"  COLLISION {k}: {v}")

    # ---- cross-vendor property clustering (union-find) ----
    # Same property when parcel digits match OR the address core matches —
    # vendor-proof: an original from ProTitle links to its Prowess update.
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_parcel: Dict[str, int] = {}
    by_addr: Dict[str, int] = {}
    for i, r in enumerate(rows):
        if r["parcel_digits"]:
            j = by_parcel.setdefault(r["parcel_digits"], i)
            union(i, j)
        if r["addr_core"]:
            j = by_addr.setdefault(r["addr_core"], i)
            # MUST-NOT-LINK firewall: never merge by address when both docs
            # carry parcels that DIFFER (two distinct properties can share a
            # house# + street word across towns).
            if (r["parcel_digits"] and rows[j]["parcel_digits"]
                    and r["parcel_digits"] != rows[j]["parcel_digits"]):
                continue
            union(i, j)

    clusters: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for i, r in enumerate(rows):
        clusters[find(i)].append(r)

    # rebuild version chains per property cluster (+ flag update-only groups)
    rels = m.db["relationships"]
    orphan_updates: List[Dict[str, Any]] = []
    for root, items in clusters.items():
        rep_row = rows[root]
        vg = ("parcel:" + rep_row["parcel_digits"]) if rep_row["parcel_digits"] else \
             ("addr:" + (rep_row["addr_core"] or rep_row["doc_id"]))
        ordered = sorted(items, key=lambda x: (x["eff"] or datetime.min.replace(tzinfo=timezone.utc)))
        ids = [it["doc_id"] for it in ordered]
        originals = [it["doc_id"] for it in ordered if not it["is_update"]]
        # original is "present" if a standalone original exists OR an update
        # in this group carries the original embedded inside it
        no_original = len(originals) == 0 and not any(it.get("has_embedded_orig") for it in ordered)
        for i, did in enumerate(ids):
            docs.update_one({"_id": did}, {"$set": {
                "version_group": vg,
                "is_latest": (i == len(ids) - 1),
                "supersedes": ids[i - 1] if i > 0 else None,
                "superseded_by": ids[i + 1] if i < len(ids) - 1 else None,
                "update_of": (originals[0] if (ordered[i]["is_update"] and originals) else None),
                "original_missing": no_original,
                "version_index": i + 1, "version_count": len(ids),
            }})
        if no_original:
            orphan_updates.extend(ordered)

        # relationship edges: doc ABOUT_PROPERTY prop; owner OWNS prop
        for it in ordered:
            if it.get("prop_id"):
                rels.update_one(
                    {"type": "ABOUT_PROPERTY", "src": it["doc_id"], "dst": it["prop_id"]},
                    {"$set": {"type": "ABOUT_PROPERTY", "src": it["doc_id"],
                              "dst": it["prop_id"], "source_doc_id": it["doc_id"],
                              "as_of": it["eff"], "updated_at": now}},
                    upsert=True)
                if it.get("owner_eid"):
                    rels.update_one(
                        {"type": "OWNS", "src": it["owner_eid"], "dst": it["prop_id"]},
                        {"$set": {"type": "OWNS", "src": it["owner_eid"],
                                  "dst": it["prop_id"], "source_doc_id": it["doc_id"],
                                  "as_of": it["eff"], "updated_at": now}},
                        upsert=True)

    multi = {k: v for k, v in clusters.items() if len(v) > 1}

    logger.info(f">>> UPDATE-ONLY properties (NO original full search in corpus): {len(orphan_updates)}")
    for it in orphan_updates:
        logger.info(f"   ORIGINAL MISSING: {it['doc_id']}  addr={it.get('addr')!r}")
    logger.info("================ RE-PARSE DONE ================")
    logger.info(f"docs={stats['docs']}  owner_found={stats['owner_found']}  "
                f"owner_david={stats['owner_david']}  david_linked(owner|names)={stats['david_linked']}  "
                f"parcel_found={stats['parcel_found']}")
    logger.info(f"updates={stats['updates']}  embedded_protitle={stats['embedded']}")
    logger.info(f"properties={len(clusters)}  multi_version={len(multi)}  collisions={len(collisions)}  "
                f"update_only_docs={len(orphan_updates)}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
