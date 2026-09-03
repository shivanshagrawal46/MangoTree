"""
Sprint 2 — Ingest ProTitle USA title reports (originals + update searches).

Pipeline (per the agreed plan):
  1. Extract text (born-digital text-layer; Claude Vision OCR for scanned).
  2. Keep only genuine ProTitle reports (content contains 'protitleusa').
  3. Deterministic header parse: order#, completed/index dates, owner,
     property_address, parcel_id (THE join key), county, report_type,
     title_defect_category. Update detected via "Update from index date".
  4. DEDUP: same report can appear in many files — key on Order# (+ content
     sha + parcel/completed/index). Nothing ingested twice/thrice.
  5. ENTITY LINKAGE: parcel_id -> property entity; owner -> David LLC/person
     entity (matched against the stored entities/ collection; created+flagged
     if unseen). Uses the David LLC list already loaded into entities/.
  6. VERSION LINEAGE: version_group = parcel_id; original vs update ordered by
     completed_date; is_latest / supersedes set per property.
  7. Write documents/ rows (full structured header + fraud_flags + custody +
     stored extracted_text for downstream chunking — OCR paid once).
  Chunking + contextual summary + embedding is a SEPARATE later step.

Modes:
  --dry-run (default) : parse + dedup + version + entity-match + REPORT. No
                        writes. Add --no-ocr to skip OCR (free born-digital).
  --live              : write entities + documents/ rows (OCRs scanned once).

Usage:
  python -m scripts.ingest_title_reports --year 2021 --dry-run --no-ocr
  python -m scripts.ingest_title_reports --year 2021 --live
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.extractor import extract_from_bytes
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.hashing import sha256_bytes, sha256_strings
from src.utils.logger import logger
# Reuse the SAME normalizers as the entity store so owner/parcel lookups match.
from scripts.build_entities_from_llc import norm_name, norm_addr, slug

TITLE_ROOT = r"F:\Title reports"
DOCS_COLLECTION = "documents"
ENTITIES_COLLECTION = "entities"

# Owners known to be David's from the email corpus / reports but possibly not
# in the formed-LLC sheet (holding entities). Confirm/extend with the user.
KNOWN_DAVID_OWNERS = {
    norm_name("IPA ASSET MANAGEMENT LLC"),
    norm_name("31FO LLC"),
    norm_name("27 WASHINGTON REALTY LLC"),
}


# ---------------------------------------------------------------------------
# ProTitle parsing (deterministic header)
# ---------------------------------------------------------------------------

_PARCEL_RE = re.compile(r"\b(\d{3,4}-\d[\d.]*-\d[\d.]*-\d[\d.]*)\b")
_ORDER_RE = re.compile(r"Order#\s*([0-9]+)", re.IGNORECASE)
_REF_RE = re.compile(r"Reference No:\s*([^\n\t]+)", re.IGNORECASE)
_NAME_DATE_RE = re.compile(r"Name\s+(.+?)\s+Completed Date\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE | re.DOTALL)
_INDEX_RE = re.compile(r"Index Date\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_ADDR_TYPE_RE = re.compile(r"Property Address\s+(.+?)\s+Report Type\s+([^\n]+)", re.IGNORECASE | re.DOTALL)
_ADDR_ONLY_RE = re.compile(r"Property Address\s+(.+?)(?:\n|Report Type|Update from)", re.IGNORECASE | re.DOTALL)
_COUNTY_RE = re.compile(r"County\s+([A-Za-z]+)", re.IGNORECASE)
_DEFECT_RE = re.compile(r"Title Defect Category\s*(.*?)\s*(?:Alert Note|Vesting|\n)", re.IGNORECASE | re.DOTALL)
_UPDATE_RE = re.compile(r"Update from index date\s*([\d/]+)?", re.IGNORECASE)


def is_protitle(text: str) -> bool:
    return "protitleusa" in (text or "").lower()


def detect_vendor(text: str) -> Optional[str]:
    """Which title vendor produced this report (ProTitleUSA / Prowess).

    IMPORTANT: decide from the SUMMARY HEAD of the document, not the whole
    text. A Prowess Update Search can EMBED the original ProTitle report
    inside it — whole-text 'protitleusa first' would misclassify the whole
    doc as ProTitle (this happened: 1082 Connetquot, order 972737).
    Some Prowess updates also carry NO branding at all (132 West 130th) —
    those are recognized by their format markers (Order Type / Effective)."""
    low = (text or "").lower()
    # FIRST-BRAND-WINS: the vendor whose branding appears FIRST is the primary
    # document. A Prowess Update Search often EMBEDS a ProTitle report later in
    # the file — so 'protitleusa appears somewhere' must NOT make the whole doc
    # ProTitle (this mislabeled 91 Gordon / 1082 Connetquot / 45 Sarah Drive).
    pp = low.find("protitleusa")
    pw = low.find("prowess")
    if pp != -1 and pw != -1:
        return "protitle" if pp < pw else "prowess"
    if pp != -1:
        return "protitle"
    if pw != -1:
        return "prowess"
    # unbranded but Prowess-format (scanned updates, e.g. 132 West 130th)
    if "old effective date" in low or ("order type" in low and "names searched" in low):
        return "prowess"
    return None


def is_title_report(text: str) -> bool:
    return detect_vendor(text) is not None


def is_first_page_excerpt(name: str) -> bool:
    """True for files like 'Title Report 10 Heritage Lane - 1st page.pdf'.
    These are page-1-only excerpts of a full report that is ingested
    separately — never worth OCR'ing (pure cost, zero new content)."""
    return bool(re.search(r"1st\s*page", name or "", re.I))


_COMMON_WORDS = (
    "the", "and", "of", "to", "in", "for", "title", "property", "report",
    "date", "order", "county", "page", "insurance", "policy", "company",
    "premises", "mortgage", "deed", "search",
)


def looks_garbled(text: str) -> bool:
    """Detect a born-digital PDF whose embedded text layer is CORRUPT (bad font
    encoding / no ToUnicode map) so we route it to Claude Vision OCR instead of
    trusting junk text. Conservative — only flags clearly broken text so we
    never pay to OCR a clean report."""
    t = text or ""
    n = len(t)
    if n < 200:
        return False
    # >1% Unicode replacement chars = broken decode
    if t.count("\ufffd") / n > 0.01:
        return True
    # A real page of prose has spaces; near-zero spaces = ligature/encoding rot
    if t.count(" ") / n < 0.02:
        return True
    # Lots of letters but NONE of the most common English/legal words = gibberish
    low = t.lower()
    hits = sum(1 for w in _COMMON_WORDS if w in low)
    letters = sum(1 for c in t if c.isalpha())
    if hits == 0 and letters / n > 0.5:
        return True
    return False


def normalize_parcel(p: str) -> str:
    return re.sub(r"\s+", "", p).upper() if p else ""


def filename_property_key(name: str) -> str:
    """Normalized property key from a title-report filename, used to detect
    that a SCANNED file is a redundant twin of a born-digital report we
    already have (so we don't pay to OCR it). Strips the 'Title Report'
    prefix, trailing version numbers, and '- 1st page'."""
    n = name.lower()
    n = re.sub(r"\.pdf$", "", n)
    n = re.sub(r"^title report\s*", "", n)
    n = re.sub(r"\s*-\s*1st page$", "", n)
    n = re.sub(r"\s+\d+$", "", n)        # trailing version number (1/2/3)
    n = n.rstrip(" .")
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return n


def _clean(s: Optional[str]) -> Optional[str]:
    return re.sub(r"\s+", " ", s).strip() if s else s


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip().replace("-", "/")  # Prowess uses MM-DD-YYYY; ProTitle uses MM/DD/YYYY
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Prowess Title Abstracts parsing (Full Search = original, Update Search = update)
# ---------------------------------------------------------------------------

_DATE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"
_PW_ORDER_TYPE_RE = re.compile(r"Order Type:\s*(Full|Update)", re.IGNORECASE)
_PW_SEARCH_DATE_RE = re.compile(r"Search Date:\s*" + _DATE, re.IGNORECASE)
_PW_OLD_EFF_RE = re.compile(r"Old Effective Date:\s*" + _DATE, re.IGNORECASE)
_PW_NEW_EFF_RE = re.compile(r"New Effective Date:\s*" + _DATE, re.IGNORECASE)
# Variant header used by some updates: 'Searched From: <date>' + 'Effective Date: <date>'
_PW_SEARCHED_FROM_RE = re.compile(r"Searched From:\s*" + _DATE, re.IGNORECASE)
_PW_EFF_ONLY_RE = re.compile(r"(?<!Old )(?<!New )Effective Date:\s*" + _DATE, re.IGNORECASE)
_PW_FIELD_END = r"(?:Address Searched|Address Given|County|State|#APN|Parcel ID|Block/Lot|Old Effective|New Effective|Searched From|Effective Date|Names Searched|Taxes|Tax Year|\n)"
_PW_OWNER_RE = re.compile(r"Vesting Owner:\s*(.+?)\s*" + _PW_FIELD_END, re.IGNORECASE | re.DOTALL)
_PW_NAMES_SEARCHED_RE = re.compile(r"Names Searched:\s*(.+?)\s*(?:Taxes|Tax Year|New Findings|Deed Chain|\n\n)", re.IGNORECASE | re.DOTALL)
_PW_NAME_GIVEN_RE = re.compile(r"Name Given:\s*(.+?)\s*(?:Address Given|Address Searched|Vesting|\n)", re.IGNORECASE | re.DOTALL)
_PW_ADDR_SEARCHED_RE = re.compile(r"Address Searched:\s*(.+?)\s*(?:County|State|Vesting|#APN|Parcel ID|Block/Lot|Old Effective|\n)", re.IGNORECASE | re.DOTALL)
_PW_ADDR_GIVEN_RE = re.compile(r"Address Given:\s*(.+?)\s*(?:Vesting|Address Searched|County|\n)", re.IGNORECASE | re.DOTALL)
_PW_APN_RE = re.compile(r"#APN\s*#Parcel\s*#PIN:\s*([0-9][0-9\-.\s]{6,})", re.IGNORECASE)
_PW_PARCEL_ID_RE = re.compile(r"Parcel ID:\s*([0-9][0-9\-.\s]{6,})", re.IGNORECASE)
_PW_BLOCK_LOT_RE = re.compile(r"Block/Lot/Legal:\s*BLOCK\s*(\d+)\s*,?\s*LOT\s*(\d+)", re.IGNORECASE)
_PW_COUNTY_RE = re.compile(r"County:\s*([A-Za-z ]+?)(?:\s*State|\n)", re.IGNORECASE)


def parse_prowess(text: str) -> Dict[str, Any]:
    text = _normalize_for_parse(text)

    def g(rx, grp=1):
        m = rx.search(text)
        return m.group(grp).strip() if m else None

    ot = g(_PW_ORDER_TYPE_RE)
    order_type = None
    if ot:
        order_type = "Update Search" if ot.lower().startswith("update") else "Full Search"
    # Owner: Vesting Owner (cap raised — bank-trustee names run long), then
    # first entity from Names Searched, then Name Given (when not '-').
    owner = _clean(g(_PW_OWNER_RE))
    if owner and len(owner) > 250:
        owner = None
    names_searched = _clean(g(_PW_NAMES_SEARCHED_RE)) or None
    if not owner and names_searched:
        owner = names_searched[:250]
    if not owner:
        ng = _clean(g(_PW_NAME_GIVEN_RE))
        if ng and ng not in ("-", "--"):
            owner = ng[:250]
    addr = _clean(g(_PW_ADDR_SEARCHED_RE) or g(_PW_ADDR_GIVEN_RE))
    if addr and len(addr) > 160:
        addr = addr[:160]
    apn_raw = g(_PW_APN_RE) or g(_PW_PARCEL_ID_RE)
    apn = normalize_parcel(re.sub(r"\s+", "", apn_raw)) if apn_raw else None
    if not apn:
        bl = _PW_BLOCK_LOT_RE.search(text)
        if bl:  # NYC-style identity: borough block/lot
            apn = f"BLK{bl.group(1)}-LOT{bl.group(2)}"
    # date variants: 'Searched From/Effective Date' = old/new effective
    old_eff = g(_PW_OLD_EFF_RE) or g(_PW_SEARCHED_FROM_RE)
    new_eff = g(_PW_NEW_EFF_RE) or g(_PW_EFF_ONLY_RE)
    return {
        "vendor": "prowess",
        "order_type": order_type,
        "is_update": order_type == "Update Search",
        "search_date": g(_PW_SEARCH_DATE_RE),
        "old_effective_date": old_eff,
        "new_effective_date": new_eff,
        "owner_name": owner,
        "names_searched": names_searched,
        "property_address": addr,
        "parcel_id": apn,
        "county": _clean(g(_PW_COUNTY_RE)),
        # ProTitle-shaped fields kept None for a uniform record
        "order_number": None, "reference_no": None,
        "completed_date": None, "index_date": None,
        "report_type": order_type, "title_defect_category": None,
        "update_from_index_date": None,
    }


def parse_any(text: str, vendor: str) -> Dict[str, Any]:
    """Dispatch to the right parser and stamp the vendor."""
    if vendor == "prowess":
        return parse_prowess(text)
    rep = parse_report(text)
    rep["vendor"] = "protitle"
    rep["order_type"] = "Update Search" if rep.get("is_update") else "Full Search"
    rep["search_date"] = None
    rep["old_effective_date"] = None
    rep["new_effective_date"] = None
    return rep


def _normalize_for_parse(text: str) -> str:
    """Claude Vision OCR renders tables as markdown with '|' separators
    (e.g. '| Order Type: | Full Search |'). Strip pipes + markdown bold so the
    header regexes work on BOTH born-digital and OCR'd text."""
    t = (text or "").replace("|", " ")
    t = t.replace("**", " ")
    return re.sub(r"[ \t]{2,}", " ", t)


def parse_report(text: str) -> Dict[str, Any]:
    text = _normalize_for_parse(text)

    def _g(rx, grp=1):
        m = rx.search(text)
        return m.group(grp).strip() if m else None

    nm = _NAME_DATE_RE.search(text)
    owner = _clean(nm.group(1)) if nm else None
    if owner and len(owner) > 120:
        owner = None
    completed = nm.group(2).strip() if nm else None
    at = _ADDR_TYPE_RE.search(text)
    if at and len(at.group(1)) < 160:
        address, report_type = _clean(at.group(1)), _clean(at.group(2))
    else:
        address, report_type = _clean(_g(_ADDR_ONLY_RE)), None
    parcel_m = _PARCEL_RE.search(text)
    upd = _UPDATE_RE.search(text)
    return {
        "order_number": _g(_ORDER_RE),
        "reference_no": _g(_REF_RE),
        "owner_name": owner,
        "completed_date": completed,
        "index_date": _g(_INDEX_RE),
        "property_address": address,
        "report_type": report_type,
        "county": _g(_COUNTY_RE),
        "title_defect_category": (_g(_DEFECT_RE) or "").strip() or None,
        "parcel_id": normalize_parcel(parcel_m.group(1)) if parcel_m else None,
        "is_update": bool(upd),
        "update_from_index_date": upd.group(1) if (upd and upd.group(1)) else None,
    }


def derive_fraud_flags(rep: Dict[str, Any], text: str) -> List[str]:
    flags: List[str] = []
    t = (text or "").lower()
    if rep.get("title_defect_category"):
        flags.append("title_defect")
    if "lis pendens" in t or "notice of pendency" in t:
        flags.append("lis_pendens")
    if "federal tax lien" in t or "tax lien" in t:
        flags.append("tax_lien")
    if ("rpapl" in t and ("1501" in t or "1504" in t)) or ("cancel" in t and "mortgage" in t):
        flags.append("mortgage_cancellation_action")
    return flags


# ---------------------------------------------------------------------------
# Entity resolution (against the stored entities/ collection)
# ---------------------------------------------------------------------------

_SUFFIX_RE = re.compile(r"\b(llc|l\s*l\s*c|inc|incorporated|corp|corporation|co|company|ltd)\b\.?", re.IGNORECASE)


def _strip_suffixes(name_norm: str) -> str:
    return re.sub(r"\s+", " ", _SUFFIX_RE.sub(" ", name_norm or "")).strip()


_ENT_CACHE: Dict[str, Any] = {}


def _entity_list(col) -> List[Dict[str, Any]]:
    """All llc/person entities (cached per run) for fuzzy owner matching."""
    if "rows" not in _ENT_CACHE:
        _ENT_CACHE["rows"] = list(col.find({"kind": {"$in": ["llc", "person"]}},
                                           {"_id": 1, "name_norm": 1, "is_david": 1}))
    return _ENT_CACHE["rows"]


def llc_matches_address(owner_name: str, address: str) -> bool:
    """David's rule: an LLC named after its own property (house number +
    street-initial letters) is David's. E.g. '132W130 LLC' owns 132 West 130th;
    '9RO LLC' owns 9 Roda; '182LA LLC' owns 182 Laurelton; '1091G' owns 1091
    Gardiner. Strict: a token must START with the exact house number, then the
    next letter must match the street's first letter (avoids false positives
    like 'RH PHILLIPS'/'JDK COVE' which do NOT lead with the house number)."""
    if not owner_name or not address:
        return False
    am = re.match(r"\s*(\d+)\s+([A-Za-z])", address.strip())
    if not am:
        return False
    house, street0 = am.group(1), am.group(2).lower()
    for tok in re.findall(r"[0-9A-Za-z]+", owner_name):
        tm = re.match(r"^(\d+)([A-Za-z])", tok)
        if tm and tm.group(1) == house and tm.group(2).lower() == street0:
            return True
    return False


def resolve_owner_entity(col, owner_name: str, now: datetime, address: str = "") -> Dict[str, Any]:
    n = norm_name(owner_name)
    if not n:
        return {"entity_id": None, "is_david": False, "created": False}
    addr_david = llc_matches_address(owner_name, address)
    found = col.find_one({"name_norm": n, "kind": {"$in": ["llc", "person"]}}, {"_id": 1, "is_david": 1})
    if found:
        is_d = bool(found.get("is_david"))
        if addr_david and not is_d:  # promote: LLC named after its property
            col.update_one({"_id": found["_id"]}, {"$set": {
                "is_david": True, "is_david_network": True, "needs_review": False,
                "david_flag_reason": "address_coded_llc", "updated_at": now}})
            is_d = True
            for e in _ENT_CACHE.get("rows", []):
                if e["_id"] == found["_id"]:
                    e["is_david"] = True
        return {"entity_id": found["_id"], "is_david": is_d, "created": False}
    # Fuzzy pass: suffix-stripped exact, then high-confidence fuzzy (>=92) —
    # so 'IPA Asset Management, L.L.C.' still hits the David LLC master row.
    stripped = _strip_suffixes(n)
    if stripped:
        try:
            from rapidfuzz import fuzz
        except ImportError:
            fuzz = None
        best, best_score = None, 0.0
        for e in _entity_list(col):
            cand = _strip_suffixes(e.get("name_norm") or "")
            if not cand:
                continue
            if cand == stripped:
                best, best_score = e, 100.0
                break
            if fuzz is not None:
                sc = fuzz.ratio(stripped, cand)
                if sc > best_score:
                    best, best_score = e, sc
        if best is not None and best_score >= 92.0:
            return {"entity_id": best["_id"], "is_david": bool(best.get("is_david")),
                    "created": False}
    # Not in store — create (flagged) so the graph grows from reports too.
    kind = "llc" if re.search(r"\bLLC\b|L\.L\.C", owner_name.upper()) else "person"
    is_david = (n in KNOWN_DAVID_OWNERS) or addr_david
    eid = ("ent_llc_" if kind == "llc" else "ent_per_") + slug(owner_name)
    col.update_one({"_id": eid}, {"$set": {
        "_id": eid, "kind": kind, "matter_id": DEFAULT_MATTER_ID,
        "canonical_name": owner_name, "name_norm": n, "aliases": [owner_name],
        "is_david": is_david, "is_david_network": is_david,
        "needs_review": not is_david, "source": "title_report", "updated_at": now,
    }, "$setOnInsert": {"created_at": now}}, upsert=True)
    if "rows" in _ENT_CACHE:  # keep fuzzy cache aware of the new entity
        _ENT_CACHE["rows"].append({"_id": eid, "name_norm": n, "is_david": is_david})
    return {"entity_id": eid, "is_david": is_david, "created": True}


def resolve_property_entity(col, rep: Dict[str, Any], owner_is_david: bool, now: datetime) -> Optional[str]:
    parcel = rep.get("parcel_id")
    if not parcel:
        return None
    pid = "ent_prop_" + slug(parcel)
    col.update_one({"_id": pid}, {
        "$set": {
            "_id": pid, "kind": "property", "matter_id": DEFAULT_MATTER_ID,
            "parcel_id": parcel, "canonical_address": rep.get("property_address"),
            "address_norm": norm_addr(rep.get("property_address") or ""),
            "county": rep.get("county"), "updated_at": now,
        },
        "$setOnInsert": {"created_at": now},
        "$addToSet": {"address_variants": rep.get("property_address")},
    }, upsert=True)
    if owner_is_david:
        col.update_one({"_id": pid}, {"$set": {"david_linked": True}})
    return pid


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest ProTitle title reports.")
    ap.add_argument("--year", default="2021")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false", help="Write entities + documents/")
    ap.add_argument("--no-ocr", action="store_true", help="Skip OCR (born-digital only) — free logic validation")
    ap.add_argument("--clean", action="store_true",
                    help="Wipe prior title_report docs + property/report-owner entities before ingest (clean start)")
    args = ap.parse_args()

    folder = Path(TITLE_ROOT) / args.year
    if not folder.exists():
        logger.error(f"Folder not found: {folder}")
        return 2
    pdfs = sorted(folder.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]

    settings = Settings.load()
    now = datetime.now(timezone.utc)
    # Arm the Claude Vision spend guard so OCR can't exceed the budget.
    if not args.no_ocr:
        from src.extractor.claude_ocr import init_spend_guard
        init_spend_guard(settings.ocr_vision_budget_usd)
    mongo: Optional[MongoClientWrapper] = None
    ents = docs = None
    if not args.dry_run:
        mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
        mongo.ping()
        ents = mongo.db[ENTITIES_COLLECTION]
        docs = mongo.db[DOCS_COLLECTION]
        from pymongo import ASCENDING
        for keys, name in [
            ([("source_type", ASCENDING)], "ix_source_type"),
            ([("parcel_id", ASCENDING)], "ix_parcel"),
            ([("order_number", ASCENDING)], "ix_order"),
            ([("property_ids", ASCENDING)], "ix_property_ids"),
            ([("owner_entity_id", ASCENDING)], "ix_owner"),
            ([("version_group", ASCENDING)], "ix_vgroup"),
            ([("corpus", ASCENDING)], "ix_corpus"),
        ]:
            try:
                docs.create_index(keys, name=name)
            except Exception:  # noqa: BLE001
                pass
        if args.clean:
            d1 = docs.delete_many({"source_type": "title_report"}).deleted_count
            d2 = ents.delete_many({"kind": "property"}).deleted_count
            d3 = ents.delete_many({"source": "title_report"}).deleted_count
            logger.info(f"--clean: removed {d1} title_report docs, {d2} property + {d3} "
                        f"report-owner entities (LLC store from Excel preserved)")

    # Resume: skip reports already ingested (don't re-extract or re-OCR).
    existing_ids: set = set()
    existing_keys: set = set()
    if not args.dry_run and not args.clean:
        for d in docs.find({"source_type": "title_report"},
                           {"_id": 1, "filename_key": 1}):
            existing_ids.add(d["_id"])
            if d.get("filename_key"):
                existing_keys.add(d["filename_key"])
        if existing_ids or existing_keys:
            logger.info(f"resume: {len(existing_ids)} reports already in documents/ "
                        f"({len(existing_keys)} property-keys) — will skip re-extract/OCR")

    logger.info(f"Scanning {len(pdfs)} PDFs in {folder} "
                f"({'LIVE — writing' if not args.dry_run else 'DRY RUN'}"
                f"{', no-OCR' if args.no_ocr else ''})")

    stats = {"pdfs": len(pdfs), "protitle": 0, "non_protitle": 0, "errors": 0,
             "ocr": 0, "skipped_redundant_scans": 0}
    dup_files = 0
    candidates: List[Dict[str, Any]] = []   # ProTitle reports (text + sha + meta)
    covered_keys: set = set(existing_keys)   # seed with already-ingested keys (resume → skip re-OCR)
    scanned: List[Path] = []                 # files with no text layer (need OCR)

    def _extract(data: bytes, name: str, ocr: bool):
        return extract_from_bytes(
            data, name,
            ocr_lang=settings.ocr_lang, ocr_min_chars=settings.ocr_text_layer_min_chars,
            ocr_dpi=settings.ocr_dpi, enable_ocr=ocr,
            vision_enabled=(settings.ocr_vision_enabled and ocr),
            vision_model=settings.ocr_vision_model,
            vision_min_pages=settings.ocr_vision_min_pages, vision_dpi=settings.ocr_vision_dpi,
            vision_concurrency=settings.ocr_vision_max_concurrency,
        )

    # ---- PASS A: free text-layer extraction (NO OCR) ----
    # '1st page' excerpts are dropped up-front: they are page-1-only copies of
    # a full report that is ingested separately, so OCR'ing them is pure waste.
    excerpts: List[Path] = [p for p in pdfs if is_first_page_excerpt(p.name)]
    work = [p for p in pdfs if not is_first_page_excerpt(p.name)]
    if excerpts:
        logger.info(f"Skipping {len(excerpts)} '1st page' excerpt file(s) — never OCR'd (cost = $0)")
    logger.info("Pass A: text-layer (free) — finding born-digital ProTitle reports")
    for idx, p in enumerate(work, 1):
        try:
            data = p.read_bytes()
            res = _extract(data, p.name, ocr=False)
            text = res.text or ""
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning(f"extract failed {p.name}: {exc}")
            continue
        if is_protitle(text) and not looks_garbled(text):
            candidates.append({"text": text, "sha256": sha256_bytes(data),
                               "file": p.name, "pages": len(res.pages)})
            covered_keys.add(filename_property_key(p.name))
        else:
            if is_protitle(text):  # readable header but corrupt body → OCR it
                logger.warning(f"  garbled text layer — routing to Claude Vision OCR: {p.name}")
            scanned.append(p)
        if idx % 50 == 0:
            logger.info(f"  Pass A {idx}/{len(work)} | born_digital={len(candidates)} scanned_pending={len(scanned)}")

    # ---- PASS B: OCR ONLY scanned files with no born-digital twin ----
    if not args.no_ocr:
        logger.info(f"Pass B: {len(scanned)} scanned files — OCR only those NOT already covered born-digital")
        for i, p in enumerate(scanned, 1):
            if filename_property_key(p.name) in covered_keys:
                stats["skipped_redundant_scans"] += 1
                continue
            try:
                data = p.read_bytes()
                res = _extract(data, p.name, ocr=True)
                text = res.text or ""
                stats["ocr"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                logger.warning(f"OCR failed {p.name}: {exc}")
                continue
            if is_protitle(text):
                candidates.append({"text": text, "sha256": sha256_bytes(data),
                                   "file": p.name, "pages": len(res.pages)})
                covered_keys.add(filename_property_key(p.name))
            else:
                stats["non_protitle"] += 1
            try:
                from src.extractor.claude_ocr import get_spend_guard
                g = get_spend_guard()
                spent = f"${g.spent:.2f}" if g else "n/a"
            except Exception:  # noqa: BLE001
                spent = "n/a"
            logger.info(f"  Pass B {i}/{len(scanned)} | ocr'd={stats['ocr']} "
                        f"skipped_redundant={stats['skipped_redundant_scans']} vision_spend={spent}")
    else:
        logger.info(f"--no-ocr: skipping OCR of {len(scanned)} scanned files")
        stats["non_protitle"] = len(scanned)

    # Safety net: confirm every skipped '1st page' excerpt has a full report
    # (token overlap on the property name). Warn — never silently lose — if a
    # standalone excerpt has no matching full report.
    stats["skipped_1st_page"] = len(excerpts)
    cand_fkeys = [set(filename_property_key(c["file"]).split()) for c in candidates]
    for p in excerpts:
        ek = set(filename_property_key(p.name).split())
        if not ek:
            continue
        need = max(1, (len(ek) + 1) // 2)
        if not any(len(ek & ck) >= need for ck in cand_fkeys):
            logger.warning(f"  '1st page' excerpt with NO matching full report — REVIEW: {p.name}")

    stats["protitle"] = len(candidates)

    # ---- PARSE all candidates ----
    reps: List[Dict[str, Any]] = []
    for c in candidates:
        rep = parse_report(c["text"])
        rep["file"] = c["file"]
        rep["sha256"] = c["sha256"]
        rep["page_count"] = c["pages"]
        rep["fraud_flags"] = derive_fraud_flags(rep, c["text"])
        rep["_text"] = c["text"]
        rep["filename_key"] = filename_property_key(c["file"])
        rep["_completed"] = (rep.get("completed_date") or "").strip()
        rep["_index"] = (rep.get("index_date") or "").strip()
        reps.append(rep)

    # ---- DEDUP by IDENTITY = (Order#, completed_date, index_date) ----
    # Two files are the SAME document only when Order# AND BOTH dates match.
    # A different completed OR index date = a DISTINCT report (update search,
    # or — as seen with Order# 687682 — an order# reused for another property).
    # Order# alone is NOT safe.
    def _ident(r: Dict[str, Any]) -> tuple:
        order = r.get("order_number") or ("sha:" + r["sha256"][:16])
        return (order, r["_completed"], r["_index"])

    by_ident: Dict[tuple, Dict[str, Any]] = {}
    source_files: Dict[tuple, List[str]] = defaultdict(list)
    for r in reps:
        k = _ident(r)
        source_files[k].append(r["file"])
        if k not in by_ident:
            by_ident[k] = r
        else:  # exact identity dup → keep the richer copy (more pages / text)
            cur = by_ident[k]
            if (r["page_count"], len(r["_text"])) > (cur["page_count"], len(cur["_text"])):
                by_ident[k] = r
            dup_files += 1

    # ---- Subsume "first-page" excerpts (both dates empty) into the FULL
    #      report sharing the same Order#. Page-1 excerpts add no new content;
    #      only dropped when a dated sibling exists, else KEPT (never lost).
    #      When an order# has several dated docs (reused order#), pick the one
    #      whose filename tokens overlap the excerpt most. ----
    dated_by_order: Dict[str, List[tuple]] = defaultdict(list)
    for k, r in by_ident.items():
        if r["_completed"] or r["_index"]:
            dated_by_order[r.get("order_number") or ""].append(k)

    def _overlap(a: str, b: str) -> int:
        return len(set(a.split()) & set(b.split()))

    excerpt_drop: List[tuple] = []
    for k, r in by_ident.items():
        if r["_completed"] or r["_index"]:
            continue
        cands = dated_by_order.get(r.get("order_number") or "", [])
        if not cands:
            continue  # only the excerpt exists for this order → keep it
        host = max(cands, key=lambda hk: _overlap(by_ident[hk]["filename_key"], r["filename_key"]))
        source_files[host].extend(source_files[k])
        excerpt_drop.append(k)
        dup_files += 1
    for k in excerpt_drop:
        by_ident.pop(k, None)

    for k, r in by_ident.items():
        r["_ident"] = k
        r["_source_files"] = sorted(set(source_files[k]))

    uniq = list(by_ident.values())

    # Version group: parcel if present, else normalized address, else filename
    # key — so an original and its update-search link even when parcel is blank
    # (these ProTitle reports frequently parse parcel=None).
    def _vgroup(r: Dict[str, Any]) -> str:
        if r.get("parcel_id"):
            return "parcel:" + normalize_parcel(r["parcel_id"])
        if r.get("property_address"):
            return "addr:" + re.sub(r"[^a-z0-9]+", " ", r["property_address"].lower()).strip()
        return "fkey:" + r["filename_key"]

    by_parcel: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in uniq:
        by_parcel[_vgroup(r)].append(r)

    def _doc_id(r: Dict[str, Any]) -> str:
        order = r.get("order_number")
        if order and not (r["_completed"] or r["_index"]):
            return "doc_tr_" + order
        if order:  # date-stamped so a reused order# never collides
            return "doc_tr_" + order + "_" + sha256_strings([r["_completed"], r["_index"]])[:8]
        return "doc_tr_cmp_" + sha256_strings(
            [r["filename_key"], r["_completed"], r["_index"], r["sha256"][:8]])[:16]

    # ---- LIVE: write entities + documents/ + version lineage ----
    written = 0
    skipped_existing = 0
    if not args.dry_run:
        for r in uniq:
            doc_id = _doc_id(r)
            if doc_id in existing_ids:
                skipped_existing += 1
                continue  # resume: already ingested, don't re-write
            owner_res = resolve_owner_entity(ents, r.get("owner_name") or "", now)
            prop_id = resolve_property_entity(ents, r, owner_res["is_david"], now)
            doc = {
                "_id": doc_id, "source_type": "title_report",
                "instrument_subtype": (r.get("report_type") or "").lower().replace(" ", "_") or None,
                "issuing_authority": "ProTitle USA",
                "matter_id": DEFAULT_MATTER_ID,
                "corpus": "property_records", "privilege_status": "public_record",
                "evidentiary_class": "third_party_business_record",
                "order_number": r.get("order_number"), "reference_no": r.get("reference_no"),
                "report_type": r.get("report_type"),
                "completed_date": _parse_date(r.get("completed_date")),
                "index_date": _parse_date(r.get("index_date")),
                "is_update": r.get("is_update"), "update_from_index_date": r.get("update_from_index_date"),
                "owner_name_raw": r.get("owner_name"), "owner_entity_id": owner_res["entity_id"],
                "owner_is_david": owner_res["is_david"],
                "property_address": r.get("property_address"), "parcel_id": r.get("parcel_id"),
                "county": r.get("county"), "property_ids": [prop_id] if prop_id else [],
                "title_defect_category": r.get("title_defect_category"),
                "fraud_flags": r.get("fraud_flags", []),
                "version_group": _vgroup(r),
                "filename_key": r.get("filename_key"),
                "page_count": r.get("page_count"),
                "extracted_text": r["_text"],
                "custody": {"source_file": r["file"], "sha256": r["sha256"],
                            "source_files": r.get("_source_files", [r["file"]]),
                            "origin": "title_reports/" + args.year},
                "quality": {"has_parcel": bool(r.get("parcel_id")),
                            "has_owner": bool(r.get("owner_name")),
                            "needs_review": not (r.get("parcel_id") and r.get("owner_name"))},
                "updated_at": now, "created_at": now,
            }
            docs.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
            written += 1

        # Version lineage per property (parcel): order by completed_date.
        for pid, rs in by_parcel.items():
            ordered = sorted(rs, key=lambda x: (_parse_date(x.get("completed_date")) or datetime.min.replace(tzinfo=timezone.utc)))
            ids = [_doc_id(x) for x in ordered]
            for i, did in enumerate(ids):
                docs.update_one({"_id": did}, {"$set": {
                    "is_latest": (i == len(ids) - 1),
                    "supersedes": ids[i - 1] if i > 0 else None,
                    "superseded_by": ids[i + 1] if i < len(ids) - 1 else None,
                    "version_index": i + 1, "version_count": len(ids),
                }})

    # ---- Report ----
    originals = [r for r in uniq if not r["is_update"]]
    updates = [r for r in uniq if r["is_update"]]
    multi = {pid: rs for pid, rs in by_parcel.items() if len(rs) > 1}
    logger.info("================ REPORT ================")
    logger.info(f"PDFs={stats['pdfs']} protitle={stats['protitle']} non_protitle={stats['non_protitle']} "
                f"ocr={stats['ocr']} skipped_1st_page={stats.get('skipped_1st_page', 0)} errors={stats['errors']}")
    logger.info(f"dup_files_merged={dup_files}  UNIQUE={len(uniq)} (orig={len(originals)} update={len(updates)})")
    logger.info(f"properties(parcel)={len(by_parcel)}  multi_version={len(multi)}")
    try:
        from src.extractor.claude_ocr import get_spend_guard
        g = get_spend_guard()
        if g:
            logger.info(f"Claude Vision spend (Anthropic): ${g.spent:.2f} / ${g.budget:.2f} budget")
    except Exception:  # noqa: BLE001
        pass
    if not args.dry_run:
        logger.info(f"documents/ written={written}  skipped_existing(resume)={skipped_existing}")
        logger.info(f"entities/ now: llc={ents.count_documents({'kind':'llc'})} "
                    f"person={ents.count_documents({'kind':'person'})} "
                    f"property={ents.count_documents({'kind':'property'})}")
        logger.info(f"documents/ now: title_report={docs.count_documents({'source_type':'title_report'})}")
        mongo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
