"""Money from RKB's own workbooks — imported, never re-derived by a model.

The figures the team already keeps (loan terms per property, the hold-properties
summary) are read cell by cell into ``loan_terms`` and shown on the property
file with the workbook's name and date. The model may quote them with that
as-of date; anything newer has to come from a record. No model call here, so a
number on the screen is exactly the number in the workbook.

    python -m mangotree.cli brain money "Loan terms by property - ….xlsx" "LP Remodeling Hold Properties Summary.xlsx"
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mangotree.config.registry import PROPERTIES
from mangotree.core.logging import logger
from mangotree.retrieve import config as rcfg

from .store import BrainStore, now

#: Fields that are money, rates or dates get formatted; everything else is text.
_RATE = re.compile(r"rate|interest \(|%", re.I)
_DAILY = re.compile(r"daily|per diem", re.I)


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def match_property(label: Any) -> Optional[str]:
    """A sheet name or an address cell -> property id, by the registry's aliases."""
    raw = str(label or "").strip()
    if not raw:
        return None
    n = _norm(raw)
    best, best_len = None, 0
    for p in PROPERTIES:
        names = [p.canonical_address, p.property_id.replace("_", " "), *list(p.aliases)]
        for name in names:
            k = _norm(name)
            if len(k) >= 4 and (k in n or n in k) and len(k) > best_len:
                best, best_len = p.property_id, len(k)
    return best


def fmt(item: str, value: Any) -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if _RATE.search(item) and not _DAILY.search(item) and 0 < abs(value) <= 1:
            return f"{value * 100:.2f}%".replace(".00%", "%")
        if abs(value) >= 1000 or _DAILY.search(item) or isinstance(value, float):
            return f"${value:,.2f}"
        return str(value)
    return str(value).strip()[:500]


def read_terms_workbook(path: Path) -> Dict[str, List[Dict[str, str]]]:
    """One sheet per property with rows of (Item, Value, Where it comes from)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    out: Dict[str, List[Dict[str, str]]] = {}
    for ws in wb.worksheets:
        pid = match_property(ws.title)
        if not pid:
            continue
        rows: List[Dict[str, str]] = []
        started = False
        for r in ws.iter_rows(values_only=True):
            cells = list(r) + [None, None, None]
            item, value, note = cells[0], cells[1], cells[2]
            if not started:
                started = str(item or "").strip().lower() == "item"
                continue
            v = fmt(str(item or ""), value)
            if item and v:
                rows.append({"item": str(item).strip()[:120], "value": v, "note": str(note or "").strip()[:300]})
        if rows:
            out[pid] = rows
    return out


def read_summary_workbook(path: Path) -> Dict[str, List[Dict[str, str]]]:
    """One row per property under a header row; the first column is the address."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {}
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    out: Dict[str, List[Dict[str, str]]] = {}
    for r in rows[1:]:
        pid = match_property(r[0]) if r and r[0] else None
        if not pid:
            continue
        items = []
        for h, v in zip(header[1:], r[1:]):
            if not h or v in (None, "", "!"):
                continue
            f = fmt(h, v)
            if f and f != "!":
                items.append({"item": h[:120], "value": f, "note": ""})
        if items:
            out[pid] = items
    return out


def import_workbooks(store: BrainStore, paths: List[str], *, by: str = "import") -> Dict[str, Any]:
    per: Dict[str, List[Dict[str, Any]]] = {}
    report: Dict[str, Any] = {"files": [], "properties": {}}
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            report["files"].append({"file": raw, "error": "not found"})
            continue
        stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name)
        as_of = f"dated {m.group(1)}" if m else f"file saved {stamp:%Y-%m-%d}"
        try:
            data = read_terms_workbook(path)
            kind = "terms"
            if not data:
                data, kind = read_summary_workbook(path), "summary"
        except Exception as exc:
            logger.exception("money import failed for %s", path)
            report["files"].append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        for pid, rows in data.items():
            per.setdefault(pid, []).append({"file": path.name, "kind": kind, "as_of": as_of, "rows": rows})
        report["files"].append({"file": path.name, "kind": kind, "properties": len(data)})
    for pid, sources in per.items():
        existing = store.loan_terms.find_one({"property_id": pid}, {"_id": 0}) or {}
        keep = [s for s in (existing.get("sources") or []) if s.get("file") not in {x["file"] for x in sources}]
        store.loan_terms.update_one({"property_id": pid}, {"$set": {"property_id": pid, "sources": keep + sources,
                                                                    "imported_at": now(), "imported_by": by}}, upsert=True)
        report["properties"][pid] = sum(len(s["rows"]) for s in sources)
    report["excluded_note"] = [p for p in per if p in rcfg.ANALYSIS_EXCLUDED_PROPERTIES]
    return report
