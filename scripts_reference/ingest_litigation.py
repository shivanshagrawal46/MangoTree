"""
Ingest MangoTree Litigation Updates (F:\\Litigation updates) — a chronological
case-status series (16 docs, Jun 2021 -> Mar 2026; .docx + born-digital .pdf).

These are typed narrative reports -> exact text layer extraction (NO OCR; OCR
does not apply to digital Word/PDF text and would only add errors).

  * Stored as documents/ source_type=litigation_update (court_records corpus),
    privilege_status=work_product (review — these are our counsel's updates).
  * Sequenced chronologically by date; the most recent flagged is_latest.
  * Linked to the case entity + David + every property the update mentions
    (so a property query surfaces its litigation status).

Usage: python -m scripts.ingest_litigation --live   (default dry-run)
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import config.settings  # noqa: F401
from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.extractor.extractor import extract_from_bytes
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.hashing import sha256_bytes
from src.utils.logger import logger
from scripts.ingest_titles_full import norm_address, addr_core
from scripts.ingest_insurance import build_prop_index

LIT_ROOT = Path(r"F:\Litigation updates")
CASE_ID = "ent_case_mangotree_david"
_HOUSE_ST = re.compile(r"\b(\d{1,5}(?:-\d{1,5})?)\s+([A-Za-z][A-Za-z .']{2,40})")
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["", "January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], )}


def parse_date_from_name(name: str) -> datetime:
    m = re.search(r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
                  r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                  r"[ .]+(\d{1,2}),?\s*(\d{4})", name, re.I)
    if not m:
        return datetime(1900, 1, 1, tzinfo=timezone.utc)
    mon = next((v for k, v in _MONTHS.items() if k and m.group(1).lower().startswith(k.lower()[:3])), 1)
    return datetime(int(m.group(3)), mon, int(m.group(2)), tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    args = ap.parse_args()
    s = Settings.load()
    now = datetime.now(timezone.utc)
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    docs, ents, rels = m.db["documents"], m.db["entities"], m.db["relationships"]
    prop_idx = build_prop_index(ents)

    files = sorted([p for p in LIT_ROOT.rglob("*") if p.suffix.lower() in (".pdf", ".docx")])
    logger.info(f"{len(files)} litigation files")

    if not args.dry_run:
        ents.update_one({"_id": CASE_ID}, {"$set": {
            "_id": CASE_ID, "kind": "case", "matter_id": DEFAULT_MATTER_ID,
            "canonical_name": "MangoTree v. David / Island Properties (litigation)",
            "is_ours": True, "source": "litigation_update", "updated_at": now,
        }, "$setOnInsert": {"created_at": now}}, upsert=True)

    records = []
    for p in files:
        try:
            res = extract_from_bytes(p.read_bytes(), p.name, enable_ocr=False, vision_enabled=False)
            text = (res.text or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"extract failed {p.name}: {exc}")
            continue
        dt = parse_date_from_name(p.name)
        seqm = re.match(r"\s*(\d+)", p.name)
        seq = int(seqm.group(1)) if seqm else 0
        # mentioned properties
        pids = []
        for mt in _HOUSE_ST.finditer(text):
            ac = addr_core(norm_address(f"{mt.group(1)} {mt.group(2)}"))
            pid = prop_idx.get(ac)
            if pid and pid not in pids:
                pids.append(pid)
        records.append({"path": p, "text": text, "date": dt, "seq": seq, "pids": pids,
                        "pages": len(res.pages or [1])})
        logger.info(f"  [{seq:02d}] {dt.date()} chars={len(text)} props_mentioned={len(pids)}  {p.name[:45]}")

    records.sort(key=lambda r: (r["date"], r["seq"]))
    if args.dry_run:
        logger.info("DRY RUN — re-run --live to store.")
        m.close()
        return 0

    for i, r in enumerate(records):
        p = r["path"]
        doc_id = "doc_lit_" + sha256_bytes(p.read_bytes())[:16]
        doc = {
            "_id": doc_id, "source_type": "litigation_update",
            "instrument_subtype": "litigation_update", "matter_id": DEFAULT_MATTER_ID,
            "corpus": "court_records", "privilege_status": "work_product",
            "evidentiary_class": "attorney_work_product", "authority_score": 1.05,
            "sequence_no": r["seq"], "document_date": r["date"],
            "is_latest": (i == len(records) - 1),
            "supersedes": ("doc_lit_" + sha256_bytes(records[i - 1]["path"].read_bytes())[:16]) if i > 0 else None,
            "case_ids": [CASE_ID], "property_ids": r["pids"],
            "page_count": r["pages"], "extracted_text": r["text"],
            "custody": {"source_files": [p.name], "sha256": sha256_bytes(p.read_bytes()),
                        "origin": "litigation_updates", "ingested_at": now},
            "quality": {"needs_review": len(r["text"]) < 200},
            "updated_at": now, "created_at": now,
        }
        docs.update_one({"_id": doc_id}, {"$set": doc}, upsert=True)
        rels.update_one({"type": "FILED_IN", "src": doc_id, "dst": CASE_ID},
                        {"$set": {"type": "FILED_IN", "src": doc_id, "dst": CASE_ID,
                                  "as_of": r["date"], "updated_at": now}}, upsert=True)
        for pid in r["pids"]:
            rels.update_one({"type": "LITIGATION_ABOUT", "src": doc_id, "dst": pid},
                            {"$set": {"type": "LITIGATION_ABOUT", "src": doc_id, "dst": pid,
                                      "as_of": r["date"], "source_doc_id": doc_id, "updated_at": now}},
                            upsert=True)
    logger.info(f"================ LITIGATION DONE ================")
    logger.info(f"stored {len(records)} litigation updates; latest = {records[-1]['date'].date()}")
    logger.info(f"documents/ litigation_update now: {docs.count_documents({'source_type':'litigation_update'})}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
