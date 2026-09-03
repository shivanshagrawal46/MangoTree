"""
Sprint 2 — STEP 1: full extraction + cross-folder dedup + linkage for the
ENTIRE title-report corpus (ProTitle USA + Prowess Title Abstracts), 2021-2026
including subfolders (2025\\Updated…, 2026\\Sent to Dhibin).

What this does (Step 1 only — NOT chunk/embed, NOT grounded field extraction):
  1. Enumerate every PDF across all year folders + subfolders (skip '1st page'
     excerpts by name).
  2. PRE-DEDUP (free born-digital text-layer) so we OCR each unique report ONCE:
       ProTitle : Order# + completed_date + index_date   (+ address confirmed post-OCR)
       Prowess  : address + order_type + search_date + old_eff + new_eff
     Applied GLOBALLY across folders (same report in 2022 & 2024 & 2026 = one).
  3. FULL word-by-word OCR of every page of each unique report via Claude Vision
     (Sonnet 4.6) -> GPT-5 vision -> RapidOCR fallback. Watermarks stripped.
  4. Re-parse clean header from OCR text; AUTHORITATIVE dedup key uses the OCR'd
     normalized address (different address => different report, kept).
  5. Page-type tagging; detect embedded ProTitle inside Prowess updates and flag
     (so Step 2 won't re-store that content).
  6. LINKAGE: owner -> David LLC/person entity; property -> parcel/address entity;
     original <-> update version lineage per property (is_latest / supersedes).
  7. Write documents/ rows with full text + full metadata + extraction_method.

Modes:
  --dry-run (default): enumerate + PRE-DEDUP + REPORT (free, no OCR, no writes).
  --live             : OCR + write documents/ + entities/ (+ version lineage).
  --clean            : wipe ONLY title_report docs + property/report-owner entities
                       (PRESERVES emails + the David LLC store).
  --limit N          : cap unique reports processed (smoke test).

Usage:
  python -m scripts.ingest_titles_full --dry-run
  python -m scripts.ingest_titles_full --live --clean
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.extractor import extract_from_bytes
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.hashing import sha256_bytes, sha256_strings
from src.utils.logger import logger
from scripts.build_entities_from_llc import norm_addr, slug
from scripts.ingest_title_reports import (
    detect_vendor, parse_report, parse_prowess, is_first_page_excerpt,
    normalize_parcel, _parse_date, resolve_owner_entity, resolve_property_entity,
    derive_fraud_flags, DOCS_COLLECTION, ENTITIES_COLLECTION,
)

TITLE_ROOT = Path(r"F:\Title reports")
YEARS = ["2021", "2022", "2023", "2024", "2025", "2026"]

_WATERMARK_RES = [
    re.compile(r"^\s*not for legal use\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"not for legal use", re.IGNORECASE),
]


def strip_watermarks(text: str) -> str:
    """Remove the repeated 'Not For Legal Use' overlay stamped on recorded
    instruments. (Filing/NYSCEF stamps are KEPT — they are real provenance.)"""
    t = text or ""
    for rx in _WATERMARK_RES:
        t = rx.sub(" ", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def addr_from_filename(name: str) -> str:
    """Property key from filename — reliable across vendors/folders for grouping."""
    n = name.lower()
    n = re.sub(r"\.pdf$", "", n)
    n = re.sub(r"^title report\s*", "", n)
    for suff in ("_update search", "_full search", "_search package",
                 "- update search", "- full search", "_update", "- 1st page", "_1st page"):
        n = n.replace(suff, "")
    n = re.sub(r"['\u2019]", "", n)
    n = re.sub(r"\s+\d+$", "", n)
    n = re.sub(r"[^a-z0-9]+", " ", n).strip()
    return n


def norm_address(addr: Optional[str]) -> str:
    """Normalize an address string for dedup identity (house# + street core)."""
    if not addr:
        return ""
    a = addr.lower()
    a = re.sub(r"['\u2019]", "", a)
    a = re.sub(r"[^a-z0-9]+", " ", a).strip()
    return a


_STREET_SFX = {"rd", "road", "dr", "drive", "st", "street", "ave", "avenue", "ln", "lane",
               "ct", "court", "blvd", "pkwy", "pl", "place", "way", "path", "cir", "ter",
               "tri", "trail"}
# Directionals normalized to ONE canonical token so 'W' == 'West', 'N' == 'North'
# (this is the bug that missed '227 W Neck' vs '227 West Neck').
_DIR_MAP = {"w": "west", "e": "east", "n": "north", "s": "south",
            "nw": "northwest", "ne": "northeast", "sw": "southwest", "se": "southeast"}


_DIRECTIONALS = {"west", "east", "north", "south", "northwest", "northeast",
                 "southwest", "southeast"}


def addr_core(addr_norm: str) -> str:
    """Property key = house number + directionals (anywhere, canonicalized) +
    FIRST real street word. Robust across filename vs full address, 'W'=='West',
    and directional placement ('83 S Ann Drive' == '83 Ann Drive S'). City,
    street-type, and 'NEW' never enter the key."""
    toks = [_DIR_MAP.get(t, t) for t in (addr_norm or "").split() if t]
    if not toks:
        return ""
    house = toks[0]
    dirs = sorted({t for t in toks[1:] if t in _DIRECTIONALS})
    street = next((t for t in toks[1:] if t not in _DIRECTIONALS and t not in _STREET_SFX), "")
    return " ".join([house] + dirs + ([street] if street else []))


def name_says_update(name: str) -> bool:
    """Filename signals an update search ('- NEW', '_NEW', 'update')."""
    n = (name or "").lower()
    return ("update" in n) or bool(re.search(r"[-_ ]new\b", n))


def page_type(text: str) -> str:
    t = (text or "").lower()
    if "protitleusa" in t:
        return "protitle_summary"
    if "prowess" in t:
        return "prowess_summary"
    if "schedule a" in t and ("beginning at" in t or "parcel" in t or "lot" in t):
        return "schedule_a_legal_description"
    if "this indenture" in t or ("deed" in t and "grantor" in t) or "recording and endorsement" in t:
        return "deed"
    if "mortgage" in t and ("borrower" in t or "lender" in t or "principal" in t):
        return "mortgage"
    if "lis pendens" in t or "notice of pendency" in t:
        return "lis_pendens"
    if "lien" in t or ("tax" in t and ("parcel" in t or "bill" in t)):
        return "lien_or_tax"
    return "document"


def text_layer(p: Path, s: Settings) -> str:
    try:
        return (extract_from_bytes(p.read_bytes(), p.name, ocr_lang=s.ocr_lang,
                ocr_min_chars=s.ocr_text_layer_min_chars, ocr_dpi=s.ocr_dpi,
                enable_ocr=False, vision_enabled=False).text or "")
    except Exception:
        return ""


def full_ocr(p: Path, s: Settings, force: bool = True):
    """Extract every page.
      force=True  -> Claude Vision OCR EVERY page (text threshold set impossibly
                     high). Used for Prowess (unreliable two-column text layer).
      force=False -> hybrid: exact born-digital text for clean pages, Claude
                     Vision OCR for scanned pages (the recorded instruments).
                     Used for ProTitle (clean single-column summary).
    Claude Vision -> GPT-5 vision -> RapidOCR fallback is wired in the engine."""
    return extract_from_bytes(
        p.read_bytes(), p.name,
        ocr_lang=s.ocr_lang,
        ocr_min_chars=(10_000_000 if force else s.ocr_text_layer_min_chars),
        ocr_dpi=s.ocr_dpi, enable_ocr=True, vision_enabled=True,
        vision_model=s.ocr_vision_model, vision_min_pages=1,
        vision_dpi=s.ocr_vision_dpi, vision_concurrency=s.ocr_vision_max_concurrency,
    )


def prelim_meta(text: str, fname: str) -> Optional[Dict[str, Any]]:
    """Parse the cheap born-digital summary to drive pre-dedup. Returns None if
    not a recognizable title report (caller will still OCR to be safe)."""
    vendor = detect_vendor(text)
    fkey = addr_from_filename(fname)
    if vendor == "protitle":
        r = parse_report(text)
        oid = r.get("order_number") or ("ADDR:" + fkey)
        key = ("PT", oid, (r.get("completed_date") or "").strip(), (r.get("index_date") or "").strip())
        return {"vendor": "protitle", "key": key, "is_update": bool(r.get("is_update")), "fkey": fkey}
    if vendor == "prowess":
        r = parse_prowess(text)
        key = ("PW", fkey, r.get("order_type"), (r.get("search_date") or "").strip(),
               (r.get("old_effective_date") or "").strip(), (r.get("new_effective_date") or "").strip())
        return {"vendor": "prowess", "key": key,
                "is_update": r.get("order_type") == "Update Search", "fkey": fkey}
    # Unknown from text layer (scanned/no text) — OCR will classify it.
    # Infer original/update from the filename so address-dedup can match it.
    return {"vendor": None, "key": ("UNK", fkey, sha256_strings([fname])[:8]),
            "is_update": name_says_update(fname), "fkey": fkey}


def main() -> int:
    ap = argparse.ArgumentParser(description="Full title-report extraction + dedup + linkage.")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--folder", default=None,
                    help="Ingest a specific folder (instead of the year folders) and "
                         "dedup its reports against what is already in documents/")
    args = ap.parse_args()

    s = Settings.load()
    now = datetime.now(timezone.utc)

    # ---- 1. Enumerate all PDFs across all folders + subfolders ----
    files: List[Path] = []
    excerpts = 0
    roots = [Path(args.folder)] if args.folder else [TITLE_ROOT / y for y in YEARS]
    for folder in roots:
        if not folder.exists():
            continue
        for p in folder.rglob("*"):
            if p.suffix.lower() != ".pdf":
                continue
            if is_first_page_excerpt(p.name):
                excerpts += 1
                continue
            files.append(p)
    logger.info(f"Enumerated {len(files)} PDFs ( + {excerpts} '1st page' excerpts skipped )")

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(TITLE_ROOT))
        except ValueError:
            return str(p.relative_to(p.anchor))   # e.g. 'Title reports till .../x.pdf'

    # ---- 2. PRE-DEDUP (free born-digital text layer) ----
    groups: Dict[tuple, Dict[str, Any]] = {}   # key -> {rep file, vendor, is_update, source_files[], prelim}
    for i, p in enumerate(files, 1):
        text0 = text_layer(p, s)
        meta = prelim_meta(text0, p.name)
        meta["prelim"] = (parse_prowess(text0) if meta["vendor"] == "prowess"
                          else parse_report(text0)) if meta["vendor"] else {}
        k = meta["key"]
        rel = _rel(p)
        g = groups.get(k)
        if g is None:
            groups[k] = {"file": p, "vendor": meta["vendor"], "is_update": meta["is_update"],
                         "fkey": meta["fkey"], "prelim": meta["prelim"], "source_files": [rel]}
        else:
            g["source_files"].append(rel)
        if i % 50 == 0:
            logger.info(f"  pre-dedup {i}/{len(files)} | unique-so-far={len(groups)}")

    uniques = list(groups.values())
    # Process ProTitle first so embedded-ProTitle refs in Prowess can resolve.
    uniques.sort(key=lambda g: (g["vendor"] != "protitle",))
    pt = sum(1 for g in uniques if g["vendor"] == "protitle")
    pw = sum(1 for g in uniques if g["vendor"] == "prowess")
    unk = sum(1 for g in uniques if g["vendor"] is None)
    upd = sum(1 for g in uniques if g["is_update"])
    logger.info("================ PRE-DEDUP REPORT ================")
    logger.info(f"files={len(files)}  UNIQUE reports={len(uniques)}  "
                f"(ProTitle={pt} Prowess={pw} unknown/scanned={unk})")
    logger.info(f"originals={len(uniques) - upd}  updates={upd}  "
                f"duplicate files merged={len(files) - len(uniques)}")

    # ---- 2b. DEDUP AGAINST THE DATABASE (skip reports already extracted) ----
    # ProTitle : address + order# + completed date + index date all same
    # Prowess  : address + order type + search date + old eff + new eff all same
    mongo = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    mongo.ping()
    ents = mongo.db[ENTITIES_COLLECTION]
    docs = mongo.db[DOCS_COLLECTION]

    def _addr_match(fkey: str, addr_norm: str) -> bool:
        """House number must match + at least one street token shared."""
        a, b = (fkey or "").split(), (addr_norm or "").split()
        if not a or not b:
            return False
        if a[0].isdigit() or b[0].isdigit():
            if a[0] != b[0]:
                return False
        return len(set(a) & set(b)) >= 2 if (a[0].isdigit()) else len(set(a) & set(b)) >= 1

    # Address-core index of the DB (catches SCANNED duplicates the order/date
    # match can't see pre-OCR): (addr_core, is_update) -> doc_id.
    db_addr_index: Dict[tuple, str] = {}
    for hit in docs.find({"source_type": "title_report"},
                         {"address_norm": 1, "is_update": 1}):
        ac = addr_core(hit.get("address_norm") or "")
        if ac:
            db_addr_index.setdefault((ac, bool(hit.get("is_update"))), hit["_id"])

    def _in_db(g: Dict[str, Any]) -> Optional[str]:
        pr = g.get("prelim") or {}
        if g["vendor"] == "protitle" and pr.get("order_number"):
            for hit in docs.find({"source_type": "title_report", "vendor": "protitle",
                                  "order_number": pr["order_number"]},
                                 {"completed_date": 1, "index_date": 1, "address_norm": 1}):
                if (hit.get("completed_date") == _parse_date(pr.get("completed_date"))
                        and hit.get("index_date") == _parse_date(pr.get("index_date"))
                        and _addr_match(g["fkey"], hit.get("address_norm") or "")):
                    return hit["_id"]
        elif g["vendor"] == "prowess" and pr.get("order_type"):
            for hit in docs.find({"source_type": "title_report", "vendor": "prowess",
                                  "order_type": pr["order_type"]},
                                 {"search_date": 1, "old_effective_date": 1,
                                  "new_effective_date": 1, "address_norm": 1}):
                if (hit.get("search_date") == _parse_date(pr.get("search_date"))
                        and hit.get("old_effective_date") == _parse_date(pr.get("old_effective_date"))
                        and hit.get("new_effective_date") == _parse_date(pr.get("new_effective_date"))
                        and _addr_match(g["fkey"], hit.get("address_norm") or "")):
                    return hit["_id"]
        # Address-core fallback (esp. SCANNED/unknown): same property + same
        # original/update type already in DB -> duplicate, don't re-OCR.
        ac = addr_core(g.get("fkey") or "")
        if ac:
            hit = db_addr_index.get((ac, bool(g.get("is_update"))))
            if hit:
                return hit
        return None

    to_extract: List[Dict[str, Any]] = []
    dup_in_db = 0
    for g in uniques:
        existing = _in_db(g)
        if existing:
            dup_in_db += 1
            logger.info(f"  ALREADY IN DB (skip): {g['file'].name}  -> {existing}")
            # record the new file path on the existing doc (provenance)
            docs.update_one({"_id": existing},
                            {"$addToSet": {"custody.source_files": {"$each": g["source_files"]}}})
        else:
            to_extract.append(g)
    logger.info(f"vs DATABASE: already-extracted={dup_in_db}  NEW to extract={len(to_extract)}")
    for g in to_extract:
        logger.info(f"  WILL EXTRACT: [{g['vendor'] or 'scanned?'}"
                    f"{' UPD' if g['is_update'] else ' ORIG'}] {g['file'].name}")
    uniques = to_extract

    if args.dry_run:
        logger.info("DRY RUN — no OCR, no writes. Re-run with --live to extract.")
        mongo.close()
        return 0

    # ---- LIVE: OCR + write ----
    from src.extractor.claude_ocr import init_spend_guard, get_spend_guard
    init_spend_guard(s.ocr_vision_budget_usd)
    if args.clean:
        d1 = docs.delete_many({"source_type": "title_report"}).deleted_count
        d2 = ents.delete_many({"kind": "property"}).deleted_count
        d3 = ents.delete_many({"source": "title_report"}).deleted_count
        logger.info(f"--clean: removed {d1} title_report docs, {d2} property + {d3} report-owner "
                    f"entities (emails + David LLC store preserved)")

    if args.limit:
        uniques = uniques[: args.limit]

    # identity index for cross-checks (ProTitle stored first)
    pt_index: Dict[tuple, str] = {}     # (addr_norm, order#, completed, index) -> doc_id
    by_property: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    written = 0
    resumed = 0
    for n, g in enumerate(uniques, 1):
        p: Path = g["file"]
        rel0 = _rel(p)
        if not args.clean and docs.find_one(
                {"source_type": "title_report", "custody.source_files": rel0}, {"_id": 1}):
            resumed += 1
            continue  # resume: already extracted in a prior run
        try:
            res = full_ocr(p, s, force=True)   # FULL Claude Vision OCR every page (no born-digital)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OCR failed {p.name}: {exc}")
            continue
        raw_pages = res.pages or []
        page_texts = [strip_watermarks(pg.text or "") for pg in raw_pages]
        methods = [pg.method for pg in raw_pages]
        full_text = "\n\n".join(t for t in page_texts if t).strip()
        vendor = detect_vendor(full_text) or g["vendor"] or "protitle"
        rep = parse_prowess(full_text) if vendor == "prowess" else parse_report(full_text)

        addr = rep.get("property_address")
        addr_key = norm_address(addr) or g.get("fkey") or ""
        parcel = rep.get("parcel_id")

        # embedded ProTitle inside a Prowess doc?
        embedded_pages = [i for i, t in enumerate(page_texts) if "protitleusa" in t.lower()]
        has_embedded = vendor == "prowess" and len(embedded_pages) > 0

        # authoritative identity
        if vendor == "protitle":
            ident = ("PT", addr_key, rep.get("order_number"),
                     (rep.get("completed_date") or "").strip(), (rep.get("index_date") or "").strip())
            doc_id = "doc_tr_" + (rep.get("order_number") or "na") + "_" + sha256_strings(
                [addr_key, rep.get("completed_date") or "", rep.get("index_date") or ""])[:8]
        else:
            ident = ("PW", addr_key, rep.get("order_type"), (rep.get("search_date") or "").strip(),
                     (rep.get("old_effective_date") or "").strip(), (rep.get("new_effective_date") or "").strip())
            doc_id = "doc_pw_" + sha256_strings([addr_key, rep.get("order_type") or "",
                     rep.get("search_date") or "", rep.get("old_effective_date") or "",
                     rep.get("new_effective_date") or ""])[:16]

        owner_res = resolve_owner_entity(ents, rep.get("owner_name") or "", now)
        prop_id = resolve_property_entity(ents, rep, owner_res["is_david"], now)

        is_update = (rep.get("is_update") if vendor == "protitle"
                     else rep.get("order_type") == "Update Search")
        vgroup = ("parcel:" + normalize_parcel(parcel)) if parcel else ("addr:" + addr_key)
        eff = (_parse_date(rep.get("completed_date")) if vendor == "protitle"
               else (_parse_date(rep.get("new_effective_date")) or _parse_date(rep.get("search_date"))))

        embedded_refs = []
        if has_embedded:
            er = parse_report("\n\n".join(page_texts[i] for i in embedded_pages))
            cand = ("PT", addr_key, er.get("order_number"),
                    (er.get("completed_date") or "").strip(), (er.get("index_date") or "").strip())
            if cand in pt_index:
                embedded_refs.append(pt_index[cand])

        page_meta = [{"page": i + 1, "type": page_type(page_texts[i]), "method": methods[i] if i < len(methods) else "?",
                      "embedded_protitle": (i in embedded_pages)} for i in range(len(page_texts))]
        method_counts: Dict[str, int] = defaultdict(int)
        for mth in methods:
            method_counts[mth] += 1

        doc = {
            "_id": doc_id, "source_type": "title_report", "vendor": vendor,
            "issuing_authority": "ProTitle USA" if vendor == "protitle" else "Prowess Title Abstracts",
            "matter_id": DEFAULT_MATTER_ID,
            "instrument_subtype": "update_search" if is_update else "full_search",
            "corpus": "property_records", "privilege_status": "public_record",
            "evidentiary_class": "third_party_business_record", "authority_score": 1.15,
            # dedup identity
            "order_number": rep.get("order_number"),
            "order_type": rep.get("order_type") or ("Update Search" if is_update else "Full Search"),
            "completed_date": _parse_date(rep.get("completed_date")),
            "index_date": _parse_date(rep.get("index_date")),
            "search_date": _parse_date(rep.get("search_date")),
            "old_effective_date": _parse_date(rep.get("old_effective_date")),
            "new_effective_date": _parse_date(rep.get("new_effective_date")),
            "effective_date": eff, "is_update": is_update,
            # property + owner linkage
            "property_address": addr, "address_norm": addr_key, "parcel_id": parcel,
            "county": rep.get("county"), "property_ids": [prop_id] if prop_id else [],
            "owner_name_raw": rep.get("owner_name"), "owner_entity_id": owner_res["entity_id"],
            "owner_is_david": owner_res["is_david"],
            # structure / quality
            "version_group": vgroup, "fraud_flags": derive_fraud_flags(rep, full_text),
            "title_defect_category": rep.get("title_defect_category"),
            "page_count": len(page_texts), "pages": page_meta,
            "has_embedded_protitle": has_embedded, "embedded_protitle_refs": embedded_refs,
            "extraction_method": dict(method_counts),
            "ocr_confidence": res.avg_ocr_confidence,
            "extracted_text": full_text,
            "custody": {"source_files": g["source_files"], "sha256": sha256_bytes(p.read_bytes()),
                        "origin": "title_reports", "ingested_at": now},
            "quality": {"has_parcel": bool(parcel), "has_owner": bool(rep.get("owner_name")),
                        "has_embedded_protitle": has_embedded,
                        "needs_review": not (rep.get("owner_name")) or (has_embedded and not embedded_refs)},
            "updated_at": now, "created_at": now,
        }
        docs.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
        if vendor == "protitle":
            pt_index[ident] = doc_id
        by_property[vgroup].append({"doc_id": doc_id, "eff": eff, "is_update": is_update})
        written += 1
        try:
            g2 = get_spend_guard()
            spent = f"${g2.spent:.2f}" if g2 else "n/a"
        except Exception:  # noqa: BLE001
            spent = "n/a"
        logger.info(f"  [{n}/{len(uniques)}] {vendor} {'UPD' if is_update else 'ORIG'} "
                    f"pages={len(page_texts)} embedded={has_embedded} vision_spend={spent}  {p.name[:46]}")

    # ---- version lineage per property ----
    for vg, items in by_property.items():
        ordered = sorted(items, key=lambda x: (x["eff"] or datetime.min.replace(tzinfo=timezone.utc)))
        ids = [it["doc_id"] for it in ordered]
        for i, did in enumerate(ids):
            docs.update_one({"_id": did}, {"$set": {
                "is_latest": (i == len(ids) - 1),
                "supersedes": ids[i - 1] if i > 0 else None,
                "superseded_by": ids[i + 1] if i < len(ids) - 1 else None,
                "update_of": ids[0] if (i > 0 and ordered[i]["is_update"]) else None,
                "version_index": i + 1, "version_count": len(ids),
            }})

    g2 = get_spend_guard()
    logger.info("================ DONE ================")
    logger.info(f"documents/ written={written}  resumed(skipped already-done)={resumed}")
    if g2:
        logger.info(f"Claude Vision spend: ${g2.spent:.2f} / ${g2.budget:.2f} budget")
    logger.info(f"documents/ now: title_report={docs.count_documents({'source_type':'title_report'})}")
    logger.info(f"properties={ents.count_documents({'kind':'property'})}")
    mongo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
