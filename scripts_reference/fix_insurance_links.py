"""
FREE linkage repair for insurance docs (uses stored extracted_text — no OCR).
For every insurance document:
  * extract ALL candidate property addresses (filename + OCR text),
  * match each to an existing property entity (addr_core exact, then
    typo-tolerant fuzzy on the street word — handles 'Rasberry'->'Raspberry'),
  * for a genuinely-new insured property (no title report), CREATE a property
    entity from the address so the insurance is still queryable,
  * relink HAS_INSURANCE edges + per-property latest flag.
Goal: every insured property is a node carrying ALL its insurance records.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.logger import logger
from scripts.build_entities_from_llc import slug, norm_addr
from scripts.ingest_titles_full import norm_address, addr_core
from scripts.ingest_insurance import addrs_from_filename

# ONLY the insured-property signals — NOT every address in the document
# (a policy also lists the insurer, the mortgagee, and the mailing address).
_LOC_RE = re.compile(r"Insured Location[s]?:?\s*([0-9][0-9A-Za-z .'/\-]{4,45})", re.IGNORECASE)
_PROP_ADDR_RE = re.compile(r"Property Address:?\s*([0-9][0-9A-Za-z .'/\-]{4,45})", re.IGNORECASE)


_FN_NOISE = re.compile(
    r"\.pdf$|\(.*?\)|\b20\d{2}\b|[-_]\s*\d{1,4}$|"
    r"\b(evidence|insurance|coverage|innovative|notice|of|cancellation|placed|master|policy|"
    r"copy|ipa|mangotree|mt|removed|mortgagee|mortgage|additional|insured|for|with|the|"
    r"amityville|amerstdam|amsterdam|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|properties)\b",
    re.IGNORECASE)
# GREEDY street capture (non-greedy truncated 'Shore Road' -> 'W', creating
# bogus '91 west' fragment nodes). addr_core trims to house+dir+street anyway.
_HOUSE_ST = re.compile(r"\b(\d{1,5}(?:-\d{1,5})?)\s+([A-Za-z][A-Za-z .']{2,40})")


def _addrs_in(s: str) -> List[str]:
    """Find 'house# street' phrases in a noise-stripped string."""
    cleaned = _FN_NOISE.sub(" ", s)
    cleaned = re.sub(r"[|*]", " ", cleaned)
    out = []
    for mt in _HOUSE_ST.finditer(cleaned):
        street = mt.group(2).strip(" .")
        if street and not street.isdigit():
            out.append(f"{mt.group(1)} {street}")
    return out


def candidate_addresses(text: str, fname: str) -> List[str]:
    out = _addrs_in(fname)                       # address anywhere in the filename
    for rx in (_LOC_RE, _PROP_ADDR_RE):          # + explicit insured-location fields in OCR
        for mt in rx.finditer(text or ""):
            out.extend(_addrs_in(mt.group(1)))
    seen, res = set(), []
    for a in out:
        ac = addr_core(norm_address(a))
        if ac and ac not in seen:
            seen.add(ac)
            res.append(a)
    return res


def build_prop_list(ents):
    rows = []
    for e in ents.find({"kind": "property"}, {"canonical_address": 1, "address_variants": 1}):
        cores = set()
        for a in [e.get("canonical_address")] + list(e.get("address_variants") or []):
            ac = addr_core(norm_address(a or ""))
            if ac:
                cores.add(ac)
        rows.append((e["_id"], cores))
    return rows


def fuzzy_match(ac: str, rows) -> Optional[str]:
    try:
        from rapidfuzz import fuzz
    except ImportError:
        fuzz = None
    parts = ac.split()
    if not parts:
        return None
    house = parts[0]
    for eid, cores in rows:
        if ac in cores:
            return eid
    if fuzz is None:
        return None
    best, bscore = None, 0.0
    for eid, cores in rows:
        for c in cores:
            cp = c.split()
            if cp and cp[0] == house:  # same house number
                sc = fuzz.ratio(ac, c)
                if sc > bscore:
                    best, bscore = eid, sc
    return best if bscore >= 88 else None


def main() -> int:
    live = "--live" in sys.argv
    s = Settings.load()
    now = datetime.now(timezone.utc)
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs, ents, rels = m.db["documents"], m.db["entities"], m.db["relationships"]
    if live:   # clean slate: remove prior insurance-only nodes + reset links
        d = ents.delete_many({"from_insurance_only": True}).deleted_count
        docs.update_many({"source_type": "insurance"}, {"$set": {"property_ids": []}})
        rels.delete_many({"type": "HAS_INSURANCE"})
        logger.info(f"reset: removed {d} prior insurance-only nodes + cleared insurance links")
    rows = build_prop_list(ents)
    logger.info(f"property entities: {len(rows)}")

    by_prop = defaultdict(list)
    relinked = created = still_unmatched = 0
    unmatched_files = []
    for d in docs.find({"source_type": "insurance"},
                       {"extracted_text": 1, "custody.source_files": 1, "effective_date": 1,
                        "is_cancellation": 1}):
        fn = (d.get("custody") or {}).get("source_files", ["?"])[0]
        cands = candidate_addresses(d.get("extracted_text") or "", fn)
        pids = []
        for a in cands:
            ac = addr_core(norm_address(a))
            eid = fuzzy_match(ac, rows)
            if not eid:  # create a property node for this insured-only property
                eid = "ent_prop_ins_" + slug(ac)
                if live:
                    ents.update_one({"_id": eid}, {"$set": {
                        "_id": eid, "kind": "property", "matter_id": DEFAULT_MATTER_ID,
                        "canonical_address": a, "address_norm": norm_addr(a),
                        "source": "insurance", "from_insurance_only": True, "updated_at": now,
                    }, "$setOnInsert": {"created_at": now}}, upsert=True)
                    rows.append((eid, {ac}))
                created += 1
            if eid not in pids:
                pids.append(eid)
        if not pids:
            still_unmatched += 1
            unmatched_files.append(fn)
        else:
            relinked += 1
        if live:
            docs.update_one({"_id": d["_id"]}, {"$set": {"property_ids": pids,
                            "quality.needs_review": len(pids) == 0}})
            for pid in pids:
                by_prop[pid].append({"doc_id": d["_id"], "eff": d.get("effective_date")})
                rels.update_one({"type": "HAS_INSURANCE", "src": pid, "dst": d["_id"]},
                                {"$set": {"type": "HAS_INSURANCE", "src": pid, "dst": d["_id"],
                                          "as_of": d.get("effective_date"), "updated_at": now}}, upsert=True)

    if live:
        for pid, recs in by_prop.items():
            ordered = sorted(recs, key=lambda x: (x["eff"] or datetime.min.replace(tzinfo=timezone.utc)))
            ents.update_one({"_id": pid}, {"$set": {
                "insurance_doc_ids": [r["doc_id"] for r in ordered],
                "insurance_latest_id": ordered[-1]["doc_id"], "insurance_count": len(ordered),
                "updated_at": now}})

    logger.info(f"{'LIVE' if live else 'DRY'} : docs linked={relinked}  new property nodes created={created}  "
                f"still unmatched={still_unmatched}")
    for u in unmatched_files:
        logger.info(f"   STILL UNMATCHED (no address): {u}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
