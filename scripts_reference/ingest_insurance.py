"""
Ingest 'Evidence of Insurance Coverage' (F:\\Evidence of Insurance Coverage).

  * Full Claude Vision OCR every page (GPT-5 -> RapidOCR fallback only for
    pages Claude's filter blocks — nothing lost).
  * One file can cover MULTIPLE properties (e.g. '4 Cal, 59 Beecher, 145
    Hunters') -> linked to ALL of them.
  * Cancellation notices are tagged as a distinct subtype.
  * Per property there are MANY yearly records -> all linked; the most recent
    (by effective date) flagged is_latest_insurance. Goal: a property query
    surfaces EVERY insurance record for that property to the AI.

Usage: python -m scripts.ingest_insurance --live   (default dry-run)
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.hashing import sha256_bytes
from src.utils.logger import logger
from scripts.ingest_titles_full import full_ocr, strip_watermarks, norm_address, addr_core
from scripts.reparse_titles import parcel_digits

INS_ROOT = Path(r"F:\Evidence of Insurance Coverage")

_EFF_RE = re.compile(r"Effective:?\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
_EXP_RE = re.compile(r"Expiration:?\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I)
_NAMED_RE = re.compile(r"Named Insured:?\s*(.+?)\s*(?:Master|Policy|Date:|Effective|\n)", re.I | re.S)
_MASTER_RE = re.compile(r"Master [Pp]olicy(?: Number)?:?\s*([A-Z0-9 /\-]+)", re.I)
_CERT_RE = re.compile(r"Certificate Number:?\s*([A-Z0-9\-]+)", re.I)
_LOC_RE = re.compile(r"Insured Location:?\s*(.+?)\s*(?:Property Type|Property Code|Effective|Coverage|\n)", re.I | re.S)
_INSURERS = ["lloyd", "unitas", "lexington", "scottsdale", "amguard", "great american",
             "nationwide", "obie", "steadily", "foremost", "american modern"]


def _date(sv: Optional[str]) -> Optional[datetime]:
    if not sv:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(sv.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def addrs_from_filename(name: str) -> List[str]:
    """Property addresses in the filename (a file may list several)."""
    n = re.sub(r"\.pdf$", "", name, flags=re.I)
    # cut everything from the document-type phrase onward
    n = re.split(r"\s*-\s*(?:evidence|insurance)", n, flags=re.I)[0]
    n = re.sub(r"\bNY\b", "", n)
    parts = re.split(r"\s*[,&]\s*|\s+and\s+", n)
    out = []
    for p in parts:
        p = p.strip()
        if re.match(r"^\d+\s+\w", p) or re.search(r"\d", p):
            out.append(p)
    return out or ([n.strip()] if n.strip() else [])


def insurer_of(text: str) -> Optional[str]:
    low = text.lower()
    for ins in _INSURERS:
        if ins in low:
            return "Lloyds of London" if ins == "lloyd" else ins.title()
    return None


def parse_insurance(text: str, fname: str) -> Dict[str, Any]:
    def g(rx):
        m = rx.search(text)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    locs = [g(_LOC_RE)] if _LOC_RE.search(text) else []
    return {
        "named_insured": g(_NAMED_RE),
        "master_policy": g(_MASTER_RE),
        "certificate_number": g(_CERT_RE),
        "effective_date": _date(g(_EFF_RE)),
        "expiration_date": _date(g(_EXP_RE)),
        "insurer": insurer_of(text),
        "insured_locations_text": [x for x in locs if x],
        "is_cancellation": ("cancellation" in fname.lower() or "cancellation" in text.lower()
                            or "cancelled" in text.lower()),
    }


def build_prop_index(ents) -> Dict[str, str]:
    """addr_core / parcel_digits -> property entity id."""
    idx: Dict[str, str] = {}
    for e in ents.find({"kind": "property"},
                       {"canonical_address": 1, "address_variants": 1, "parcel_id": 1}):
        addrs = [e.get("canonical_address")] + list(e.get("address_variants") or [])
        for a in addrs:
            ac = addr_core(norm_address(a or ""))   # SAME normalizer as the lookup
            if ac:
                idx.setdefault(ac, e["_id"])
        pdg = parcel_digits(e.get("parcel_id") or "")
        if pdg:
            idx.setdefault("p:" + pdg, e["_id"])
    return idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    s = Settings.load()
    now = datetime.now(timezone.utc)
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    docs, ents = m.db["documents"], m.db["entities"]
    rels = m.db["relationships"]
    prop_idx = build_prop_index(ents)

    pdfs = sorted(INS_ROOT.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    logger.info(f"{len(pdfs)} insurance PDFs; property index has {len(prop_idx)} keys")

    if args.dry_run:
        for p in pdfs:
            fa = addrs_from_filename(p.name)
            matched = [prop_idx.get(addr_core(norm_address(a))) for a in fa]
            logger.info(f"  {p.name[:55]:57} addrs={fa} -> matched={[x for x in matched if x]}")
        logger.info("DRY RUN (filename match preview). Re-run --live to OCR + store.")
        m.close()
        return 0

    from src.extractor.claude_ocr import init_spend_guard, get_spend_guard
    init_spend_guard(s.ocr_vision_budget_usd)
    by_property: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    written = 0
    for n, p in enumerate(pdfs, 1):
        try:
            res = full_ocr(p, s, force=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OCR failed {p.name}: {exc}")
            continue
        text = "\n\n".join(strip_watermarks(pg.text or "") for pg in (res.pages or []) if pg.text).strip()
        meta = parse_insurance(text, p.name)
        methods: Dict[str, int] = {}
        for pg in (res.pages or []):
            methods[pg.method] = methods.get(pg.method, 0) + 1

        # property linkage: filename addresses + insured-location text
        cands = addrs_from_filename(p.name) + meta["insured_locations_text"]
        prop_ids, addrs_norm = [], []
        for a in cands:
            an = norm_address(a)
            pid = prop_idx.get(addr_core(an))
            if pid and pid not in prop_ids:
                prop_ids.append(pid)
            addrs_norm.append(an)

        doc_id = "doc_ins_" + sha256_bytes(p.read_bytes())[:16]
        doc = {
            "_id": doc_id, "source_type": "insurance",
            "instrument_subtype": "cancellation_notice" if meta["is_cancellation"] else "evidence_of_coverage",
            "matter_id": DEFAULT_MATTER_ID,
            "corpus": "insurance_records", "privilege_status": "third_party",
            "evidentiary_class": "third_party_business_record", "authority_score": 1.10,
            "insurer": meta["insurer"], "named_insured": meta["named_insured"],
            "master_policy": meta["master_policy"], "certificate_number": meta["certificate_number"],
            "effective_date": meta["effective_date"], "expiration_date": meta["expiration_date"],
            "policy_year": (meta["effective_date"].year if meta["effective_date"] else None),
            "is_cancellation": meta["is_cancellation"],
            "property_ids": prop_ids, "covered_addresses": cands,
            "page_count": len(res.pages or []), "extraction_method": methods,
            "extracted_text": text,
            "custody": {"source_files": [p.name], "sha256": sha256_bytes(p.read_bytes()),
                        "origin": "insurance", "ingested_at": now},
            "quality": {"needs_review": len(prop_ids) == 0 or len(text) < 200,
                        "review_reasons": ([] if prop_ids else ["no_property_match"])},
            "updated_at": now, "created_at": now,
        }
        docs.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
        written += 1
        for pid in prop_ids:
            by_property[pid].append({"doc_id": doc_id, "eff": meta["effective_date"],
                                     "cancel": meta["is_cancellation"]})
            rels.update_one({"type": "HAS_INSURANCE", "src": pid, "dst": doc_id},
                            {"$set": {"type": "HAS_INSURANCE", "src": pid, "dst": doc_id,
                                      "as_of": meta["effective_date"], "until": meta["expiration_date"],
                                      "source_doc_id": doc_id, "updated_at": now}}, upsert=True)
        try:
            g = get_spend_guard(); spent = f"${g.spent:.2f}" if g else "n/a"
        except Exception:  # noqa: BLE001
            spent = "n/a"
        logger.info(f"  [{n}/{len(pdfs)}] {'CANCEL' if meta['is_cancellation'] else 'COV'} "
                    f"pages={len(res.pages or [])} props={len(prop_ids)} eff={meta['effective_date']} "
                    f"spend={spent}  {p.name[:42]}")

    # per-property: flag latest insurance + write history onto the property entity
    for pid, recs in by_property.items():
        ordered = sorted(recs, key=lambda x: (x["eff"] or datetime.min.replace(tzinfo=timezone.utc)))
        latest = ordered[-1]["doc_id"]
        for i, r in enumerate(ordered):
            docs.update_one({"_id": r["doc_id"]}, {"$set": {
                f"insurance_latest_for.{pid}": (r["doc_id"] == latest)}})
        ents.update_one({"_id": pid}, {"$set": {
            "insurance_doc_ids": [r["doc_id"] for r in ordered],
            "insurance_latest_id": latest,
            "insurance_count": len(ordered), "updated_at": now}})

    g = get_spend_guard()
    logger.info("================ INSURANCE DONE ================")
    logger.info(f"insurance docs written={written}  properties with insurance={len(by_property)}")
    no_match = docs.count_documents({"source_type": "insurance", "property_ids": []})
    logger.info(f"docs with NO property match (review): {no_match}")
    if g:
        logger.info(f"Claude Vision spend: ${g.spent:.2f}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
