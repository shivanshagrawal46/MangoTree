"""Documents attached to an ARV file — inspection reports, appraisals,
contractor bids, listing sheets, photos, tax bills, title work.

Each upload is stored once by content hash (the same object store as every
other document in MangoTree), read natively where a text layer exists, shown
to the model as images where it does not (scans, photos), and then read by
Claude Opus 5.5 into a fixed shape: what kind of document it is, the facts it
states about the house, the dollar figures in it, the repair items and their
prices, the comparable sales in an appraisal grid, the defects an inspector
found, and what it leaves unanswered.

The structured read is what the rest of the system uses: the intake wizard
offers the facts as prefills (the person accepts each one), the rehab budget
can take a contractor's bid line by line, an appraisal's comps can be added to
the grid, and the analyst sees every document's digest beside the file.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.core.usage import METER

from . import config as cfg

SUPPORTED = {".pdf", ".docx", ".doc", ".xlsx", ".xlsm", ".xls", ".csv", ".txt", ".md", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif"}
MAX_BYTES = 40 * 1024 * 1024
MAX_VISION_PAGES = 20
MAX_TEXT_CHARS = 140_000
EXCERPT_CHARS = 9_000

DOC_TYPES: Dict[str, str] = {
    "appraisal": "Appraisal report", "inspection": "Inspection report", "contractor_bid": "Contractor bid / scope of work",
    "listing": "Listing sheet / MLS", "photos": "Photos", "title": "Title, survey or deed", "tax": "Tax bill / assessment",
    "insurance": "Insurance", "permit": "Permit or violation", "lease": "Lease / rent roll", "purchase_contract": "Purchase contract",
    "bpo": "Broker price opinion / CMA", "floor_plan": "Floor plan / survey", "other": "Other",
}

#: Facts a document may state about the subject; the wizard offers each as a prefill.
FACT_KEYS = ("property_type", "beds", "baths", "sqft", "lot_sqft", "year_built", "stories", "garage_spaces", "basement", "pool", "condition_current",
             "roof_age_years", "heating", "cooling", "foundation", "exterior", "occupancy", "hoa_monthly", "property_taxes_annual", "zoning", "waterfront")

_REHAB_CATEGORIES = ["general_conditions", "demolition", "structural_concrete", "concrete_flatwork", "kitchen", "siding", "decking_patios", "roofing",
                     "exterior_doors_windows", "garage_doors", "landscaping", "floors_stairs", "framing_drywall", "cabinets_countertops", "doors_trim",
                     "carpet_resilient", "hardwood", "tiling", "painting", "appliances", "plumbing", "hvac", "electrical", "doors_locks", "other"]

DOC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": list(DOC_TYPES.keys())},
        "title": {"type": "string", "description": "Short title: what this document is, e.g. 'Home inspection, 12 Mar 2026, ABC Inspections'."},
        "summary": {"type": "string", "description": "At most 60 words. What the document is and the three facts in it that matter most to value or cost."},
        "document_date": {"type": "string", "description": "YYYY-MM-DD if stated."},
        "prepared_by": {"type": "string", "description": "Firm or person who produced it, if stated."},
        "subject_address": {"type": "string", "description": "The property address as written in the document."},
        "subject_facts": {"type": "object", "description": "Only facts the document states. Omit anything not stated.", "properties": {
            "property_type": {"type": "string", "enum": ["single_family", "townhouse", "condo", "multi_2_4", "manufactured"]},
            "beds": {"type": "number"}, "baths": {"type": "number"}, "sqft": {"type": "number", "description": "Gross living area, finished above grade."},
            "lot_sqft": {"type": "number"}, "year_built": {"type": "number"}, "stories": {"type": "number"}, "garage_spaces": {"type": "number"},
            "basement": {"type": "string", "enum": ["none", "unfinished", "finished"]}, "pool": {"type": "boolean"},
            "condition_current": {"type": "string", "enum": ["C1", "C2", "C3", "C4", "C5", "C6"], "description": "UAD condition as the document describes the house today."},
            "roof_age_years": {"type": "number"}, "heating": {"type": "string"}, "cooling": {"type": "string"}, "foundation": {"type": "string"},
            "exterior": {"type": "string"}, "occupancy": {"type": "string", "enum": ["vacant", "owner", "tenant"]}, "hoa_monthly": {"type": "number"},
            "property_taxes_annual": {"type": "number"}, "zoning": {"type": "string"}, "waterfront": {"type": "boolean"},
        }},
        "values": {"type": "array", "description": "Every dollar opinion or price in the document.", "items": {"type": "object", "properties": {
            "label": {"type": "string", "description": "e.g. 'Appraised value as-is', 'Appraised value subject to repairs', 'List price', 'Contract price', 'Assessed value', 'Estimated repair cost'."},
            "amount": {"type": "number"}, "as_of": {"type": "string"}}, "required": ["label", "amount"]}},
        "rehab_items": {"type": "array", "description": "Repair or renovation line items with a price, from a bid, estimate or inspector's cost notes.", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "amount": {"type": "number"}, "category": {"type": "string", "enum": _REHAB_CATEGORIES}, "note": {"type": "string"}}, "required": ["label", "amount", "category"]}},
        "rehab_total": {"type": "number", "description": "The document's own total for the work, if it states one."},
        "findings": {"type": "array", "description": "Defects, hazards, deferred maintenance, title or permit problems. Severity: critical = safety / structural / stops a loan; high = costly; watch = minor; info = neutral fact.",
                     "items": {"type": "object", "properties": {"title": {"type": "string"}, "severity": {"type": "string", "enum": ["critical", "high", "watch", "info"]},
                                                                 "detail": {"type": "string"}, "cost_hint": {"type": "string", "description": "Rough cost if the document or common experience gives one."}},
                               "required": ["title", "severity", "detail"]}},
        "comps": {"type": "array", "description": "Comparable sales listed in the document (appraisal grid, CMA, BPO).", "items": {"type": "object", "properties": {
            "address": {"type": "string"}, "sale_price": {"type": "number"}, "sale_date": {"type": "string", "description": "YYYY-MM-DD or YYYY-MM"}, "beds": {"type": "number"}, "baths": {"type": "number"},
            "sqft": {"type": "number"}, "lot_sqft": {"type": "number"}, "year_built": {"type": "number"}, "distance_mi": {"type": "number"},
            "condition": {"type": "string", "enum": ["C1", "C2", "C3", "C4", "C5", "C6"]}, "adjusted_price": {"type": "number"}, "note": {"type": "string"}},
            "required": ["address", "sale_price"]}},
        "photo_notes": {"type": "array", "description": "For photos only: each room or area seen and its condition in a few words.", "items": {"type": "object", "properties": {
            "area": {"type": "string"}, "condition": {"type": "string", "enum": ["C1", "C2", "C3", "C4", "C5", "C6"]}, "note": {"type": "string"}}, "required": ["area", "note"]}},
        "open_questions": {"type": "array", "items": {"type": "string"}, "description": "What this document leaves unanswered that matters for ARV or rehab cost."},
        "legibility": {"type": "string", "enum": ["clear", "partial", "poor"], "description": "How much of the document you could actually read."},
    },
    "required": ["doc_type", "title", "summary", "subject_facts", "values", "rehab_items", "findings", "comps", "open_questions", "legibility"],
}

_READER_SYSTEM = """You read real-estate documents for an After Repair Value (ARV) and renovation-loan file at RKB Consulting Group, a US renovation lender.
Extract only what the document states. Never infer a number that is not on the page; leave the field out instead. Where a figure appears several times with different values (an inspector's estimate versus a contractor's bid), report each as its own entry.
Appraisal grids: report the comparables with their sale prices and the appraiser's adjusted prices. Inspection reports: every defect with the severity an underwriter would give it. Contractor bids: each priced line, mapped to the closest category. Photos: each area seen, its UAD condition and what you noticed (dated kitchen, water staining, new roof).
Report by calling the read_document tool exactly once."""


def new_id() -> str:
    return "doc_" + uuid.uuid4().hex[:12]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- extraction
def _spreadsheet_text(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        rows = []
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            for row in csv.reader(fh):
                if any(c.strip() for c in row):
                    rows.append(" | ".join(c.strip() for c in row))
        return "\n".join(rows)
    try:
        import openpyxl
    except ImportError:
        return ""
    out: List[str] = []
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    try:
        for ws in wb.worksheets:
            out.append("[sheet %s]" % ws.title)
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v).strip() for v in row]
                if any(cells):
                    out.append(" | ".join(cells))
    finally:
        wb.close()
    return "\n".join(out)


def extract(path: Path) -> Dict[str, Any]:
    """Native text plus the page images the model needs to see.
    Returns {text, images:[jpeg bytes], pages, vision_pages, kind, errors}."""
    from mangotree.extract import documents as native
    suffix = path.suffix.lower()
    out: Dict[str, Any] = {"text": "", "images": [], "pages": 0, "vision_pages": 0, "kind": suffix.lstrip("."), "errors": []}
    if suffix in IMAGE_SUFFIXES:
        from mangotree.extract.ocr import load_image_bytes
        out["images"] = [load_image_bytes(path, max_edge=1600)]
        out["pages"] = 1
        out["vision_pages"] = 1
        return out
    if suffix in (".xlsx", ".xlsm", ".xls", ".csv"):
        try:
            out["text"] = _spreadsheet_text(path)
        except Exception as exc:
            out["errors"].append("%s: %s" % (type(exc).__name__, exc))
        out["pages"] = 1
        return out
    ext = native.extract_native(path)
    out["text"] = ext.text or ""
    out["pages"] = len(ext.pages) or (1 if ext.text else 0)
    out["errors"].extend(ext.errors)
    if suffix == ".pdf":
        need = ext.pages_needing_vision
        if need or not ext.has_usable_text:
            try:
                from mangotree.extract.ocr import render_pdf_pages
                rendered = render_pdf_pages(path, dpi=110, max_edge=1600)
                wanted = need or list(range(1, len(rendered) + 1))
                picked = [rendered[p - 1] for p in wanted if 0 < p <= len(rendered)][:MAX_VISION_PAGES]
                out["images"] = picked
                out["vision_pages"] = len(picked)
                if len(wanted) > MAX_VISION_PAGES:
                    out["errors"].append("only the first %d scanned pages were shown to the model (of %d)" % (MAX_VISION_PAGES, len(wanted)))
            except Exception as exc:
                out["errors"].append("page render failed: %s" % exc)
    if len(out["text"]) > MAX_TEXT_CHARS:
        out["text"] = out["text"][:MAX_TEXT_CHARS] + "\n[... truncated ...]"
        out["errors"].append("text truncated to %d characters" % MAX_TEXT_CHARS)
    return out


# ------------------------------------------------------------------ the read
def read_with_model(filename: str, text: str, images: List[bytes], *, hint: Optional[str] = None, subject_address: Optional[str] = None) -> Dict[str, Any]:
    import base64
    from .analyst import _structured
    blocks: List[Dict[str, Any]] = []
    intro = "Document: %s" % filename
    if hint:
        intro += "\nThe person says this is: %s" % hint
    if subject_address:
        intro += "\nThe ARV file is for: %s (say in subject_address what the document itself says, so a mismatch is visible)." % subject_address
    blocks.append({"type": "text", "text": intro})
    if text.strip():
        blocks.append({"type": "text", "text": "TEXT LAYER:\n" + text})
    for i, img in enumerate(images, start=1):
        blocks.append({"type": "text", "text": "[image %d of %d]" % (i, len(images))})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(img).decode("ascii")}})
    if not text.strip() and not images:
        raise RuntimeError("nothing readable in this file")
    before = METER.snapshot()
    out = _structured(system=[{"type": "text", "text": _READER_SYSTEM}], user=blocks, tool_name="read_document", schema=DOC_SCHEMA,
                      description="The structured reading of one document.", max_tokens=16000)
    out["usage"] = METER.summarize(METER.diff(before, METER.snapshot()))
    return out


# ------------------------------------------------------------------- record
def _tidy(extracted: Dict[str, Any]) -> Dict[str, Any]:
    e = dict(extracted or {})
    e["doc_type"] = e.get("doc_type") if e.get("doc_type") in DOC_TYPES else "other"
    e["doc_type_label"] = DOC_TYPES[e["doc_type"]]
    facts = {k: v for k, v in (e.get("subject_facts") or {}).items() if k in FACT_KEYS and v not in (None, "", [])}
    e["subject_facts"] = facts
    for k in ("values", "rehab_items", "findings", "comps", "photo_notes", "open_questions"):
        e[k] = [x for x in (e.get(k) or []) if x]
    if e.get("rehab_items") and not e.get("rehab_total"):
        e["rehab_total"] = round(sum(float(x.get("amount") or 0) for x in e["rehab_items"]))
    return e


def ingest(payload: bytes, *, filename: str, content_type: Optional[str], user: Dict[str, Any], hint: Optional[str] = None,
           subject_address: Optional[str] = None, emit: Optional[Callable[[str], None]] = None, record: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Store, read and digest one file. ``record`` (the row inserted at upload
    time) is updated in place so the caller can save it; a fresh one is made
    otherwise. Never raises for a bad file: status becomes 'failed'."""
    rec = record if record is not None else new_record(filename=filename, size=len(payload), content_type=content_type, user=user, hint=hint)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in SUPPORTED:
        rec.update({"status": "failed", "error": "unsupported file type %s" % (suffix or "(none)")})
        return rec
    if len(payload) > MAX_BYTES:
        rec.update({"status": "failed", "error": "file is larger than %d MB" % (MAX_BYTES // (1024 * 1024))})
        return rec
    try:
        from mangotree.storage.objectstore import get_object_store
        sha = sha256(payload)
        rec["sha256"] = sha
        get_object_store().put(sha, payload, filename, {"kind": "arv_document", "uploaded_by": user.get("user_id") or ""})
        work = cfg.CACHE_DIR / "docs"
        work.mkdir(parents=True, exist_ok=True)
        tmp = work / (sha + suffix)
        tmp.write_bytes(payload)
        try:
            if emit:
                emit("Reading %s…" % filename)
            ext = extract(tmp)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        rec.update({"pages": ext["pages"], "vision_pages": ext["vision_pages"], "chars": len(ext["text"]), "extract_errors": ext["errors"], "text": ext["text"]})
        if emit:
            what = []
            if ext["text"]:
                what.append("%s characters of text" % format(len(ext["text"]), ","))
            if ext["images"]:
                what.append("%d page image(s)" % len(ext["images"]))
            emit("%s: %s — asking the AI to read it" % (filename, " and ".join(what) or "nothing readable"))
        read = read_with_model(filename, ext["text"], ext["images"], hint=hint, subject_address=subject_address)
        usage = read.pop("usage", None) or {}
        rec["extracted"] = _tidy(read)
        rec["usage"] = usage
        rec["cost_usd"] = round(float(usage.get("total_cost_usd") or 0), 4)
        rec["status"] = "done"
        rec["read_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        logger.exception("arv document %s failed", filename)
        rec.update({"status": "failed", "error": ("%s: %s" % (type(exc).__name__, exc))[:300]})
    return rec


def new_record(*, filename: str, size: int, content_type: Optional[str], user: Dict[str, Any], hint: Optional[str] = None, analysis_id: Optional[str] = None) -> Dict[str, Any]:
    return {"doc_id": new_id(), "analysis_id": analysis_id, "filename": filename, "size": size, "content_type": content_type, "hint": hint,
            "uploaded_by": user.get("user_id"), "uploaded_by_name": user.get("name"), "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "status": "reading", "error": None, "extracted": None}


def digest(rec: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a record that lives inside the analysis document (and goes to the analyst)."""
    d = {k: rec.get(k) for k in ("doc_id", "analysis_id", "sha256", "filename", "size", "content_type", "hint", "uploaded_by", "uploaded_by_name", "uploaded_at",
                                  "status", "error", "pages", "vision_pages", "chars", "extract_errors", "extracted", "cost_usd", "read_at")}
    text = rec.get("text") or ""
    d["text_excerpt"] = text[:EXCERPT_CHARS] + ("…" if len(text) > EXCERPT_CHARS else "")
    return d


def for_model(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What the analyst sees: the structured read plus a text excerpt."""
    out = []
    for d in docs or []:
        if d.get("status") != "done" or not d.get("extracted"):
            out.append({"doc_id": d.get("doc_id"), "filename": d.get("filename"), "status": d.get("status"), "error": d.get("error")})
            continue
        e = d["extracted"]
        out.append({"doc_id": d.get("doc_id"), "filename": d.get("filename"), "doc_type": e.get("doc_type"), "title": e.get("title"), "document_date": e.get("document_date"),
                    "prepared_by": e.get("prepared_by"), "subject_address_in_document": e.get("subject_address"), "summary": e.get("summary"),
                    "subject_facts": e.get("subject_facts"), "values": e.get("values"), "rehab_items": e.get("rehab_items"), "rehab_total": e.get("rehab_total"),
                    "findings": e.get("findings"), "comps": e.get("comps"), "photo_notes": e.get("photo_notes"), "open_questions": e.get("open_questions"),
                    "legibility": e.get("legibility"), "text_excerpt": (d.get("text_excerpt") or "")[:4000]})
    return out


def facts_from(docs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Merge the facts across documents: {key: {value, from: filename}} — the
    most recent document wins when two disagree; both are listed under
    ``conflicts`` so the person can see it."""
    merged: Dict[str, Dict[str, Any]] = {}
    for d in sorted([x for x in docs or [] if x.get("status") == "done"], key=lambda x: ((x.get("extracted") or {}).get("document_date") or "", x.get("uploaded_at") or "")):
        for k, v in ((d.get("extracted") or {}).get("subject_facts") or {}).items():
            prev = merged.get(k)
            if prev and prev["value"] != v:
                prev.setdefault("conflicts", []).append({"value": prev["value"], "from": prev["from"]})
            merged[k] = {"value": v, "from": d.get("filename"), "doc_id": d.get("doc_id"), "conflicts": (prev or {}).get("conflicts", [])}
    return merged


def rehab_lines_from(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A contractor bid's items as rehab lines, summed per category."""
    totals: Dict[str, float] = {}
    labels: Dict[str, str] = {}
    for it in ((doc.get("extracted") or {}).get("rehab_items") or []):
        cat = it.get("category") if it.get("category") in _REHAB_CATEGORIES else "other"
        totals[cat] = totals.get(cat, 0.0) + float(it.get("amount") or 0)
        labels.setdefault(cat, re.sub(r"_", " ", cat).capitalize())
    return [{"key": k, "label": labels[k], "amount": round(v)} for k, v in totals.items() if v]
