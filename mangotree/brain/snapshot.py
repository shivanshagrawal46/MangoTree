"""The top of the property page — the numbers, the people, and how far along the deal is.

The four numbers and the people are read straight from what the system already
holds, with no model involved:

    RKB invested, amount on the note, maturity, daily interest
        from RKB's own loan workbooks (``loan_terms``); if a property is not in
        the workbooks, from the property file's facts — and marked as such, so
        nobody mistakes a figure read out of an email for the firm's own book.
    homeowner, contractor, title company, counsel, realtor, insurer
        from the workbooks and the property file, with addresses added from the
        people registry where a name matches.

The progress bar (closing -> construction -> complete -> listed -> sold and
repaid) needs a judgement about where the deal stands and what dates the
records give. That is one small Opus 5.5 reading of the property file, stored on
the file and redone when the status changes. It may only use a date a record
states: when the records give no completion or sale date, it says so.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .context import day, render_activity
from .llm import BrainError, cost_of, structured
from .store import BrainStore, name_of, now

# (key, label, workbook kind, item pattern, fact-key pattern)
_NUMBERS = (
    ("invested", "RKB invested", "summary", r"^cash funded", r"rkb funding|cash funded|funded"),
    ("note_amount", "Amount on the note", "terms", r"^note\s*/\s*loan amount|^note amount", r"note amount|^note$|loan amount"),
    ("maturity", "Maturity (from the note)", "terms", r"^maturity$|^note maturity|^maturity date", r"maturity"),
    ("daily_interest", "Daily interest", "terms", r"^daily interest|^per diem interest \(360-day year\)$", r"daily interest|per diem"),
)
_EXTRA = (
    ("rate", "Rate", "terms", r"^rate of interest"),
    ("total_due", "Total due", "summary", r"^total due"),
    ("approved", "Initial loan approved", "summary", r"^initial loan approved"),
    ("closing", "Closing / date of investment", "summary", r"^date of investment"),
    ("contract_date", "Contract date", "terms", r"^contract date"),
    ("construction_status", "Construction status (workbook)", "summary", r"^construction status"),
    ("workbook_status", "Status (workbook)", "terms", r"^status$"),
)
# (role, workbook item pattern, fact-key pattern)
_PEOPLE = (
    ("Homeowner / borrower", r"^homeowner", r"homeowner|borrower|^owner"),
    ("Contractor", r"^under \(contractor\)", r"^contractor"),
    ("Title company", None, r"title"),
    ("Counsel", None, r"counsel|attorney"),
    ("Realtor", None, r"realtor|listing agent|^listing$"),
    ("Insurer", None, r"^insur"),
    ("Property manager", None, r"property manager"),
)
_ASOF = re.compile(r"\b(till|to|as of)\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", re.I)


def _rows(store: BrainStore, pid: str) -> List[Dict[str, Any]]:
    doc = store.loan_terms.find_one({"property_id": pid}, {"_id": 0}) or {}
    out = []
    for src in doc.get("sources") or []:
        for r in src.get("rows") or []:
            out.append({**r, "kind": src.get("kind"), "file": src.get("file"), "as_of": src.get("as_of")})
    return out


def _pick(rows: List[Dict[str, Any]], kind: Optional[str], pattern: str) -> Optional[Dict[str, Any]]:
    rx = re.compile(pattern, re.I)
    for r in rows:
        if (kind is None or r.get("kind") == kind) and rx.search(str(r.get("item") or "")):
            return r
    return None


def _fact(file: Dict[str, Any], pattern: str) -> Optional[Dict[str, Any]]:
    rx = re.compile(pattern, re.I)
    return next((f for f in (file.get("facts") or []) if rx.search(str(f.get("key") or ""))), None)


_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})"
_MONEY = re.compile(r"^\$[\d,]+(?:\.\d+)?")
_EXTENDED = re.compile(r"exten\w*\s+to\s+" + _DATE, re.I)
_MATURITY_IN_TEXT = re.compile(r"matur\w*\s*(?:date)?\s*:?\s*" + _DATE, re.I)


def _headline(key: str, value: Any) -> Dict[str, str]:
    """The short figure for the tile, and the rest of what the source says beneath it.

    Nothing is worked out: the headline is a piece of the source text itself — the
    leading amount, the leading date, or for a maturity the date it was extended to.
    """
    v = str(value or "").strip()
    if not v:
        return {"headline": "", "detail": ""}
    if key == "maturity":
        m = _EXTENDED.search(v)
        if m:
            return {"headline": m.group(1), "detail": v}
        m = re.match(r"^" + _DATE, v)
        if m:
            return {"headline": m.group(1), "detail": v[m.end():].lstrip(" ,;-—")}
    m = _MONEY.match(v)
    if m:
        return {"headline": m.group(0), "detail": v[m.end():].lstrip(" ,;-—")}
    return {"headline": v if len(v) <= 28 else "", "detail": "" if len(v) <= 28 else v}


def numbers(store: BrainStore, pid: str, file: Dict[str, Any], rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    rows = _rows(store, pid) if rows is None else rows
    main, extra = [], {}
    for key, label, kind, item_rx, fact_rx in _NUMBERS:
        r = _pick(rows, kind, item_rx) or _pick(rows, None, item_rx)
        if r:
            m = _ASOF.search(str(r.get("item") or ""))
            main.append({"key": key, "label": label, "value": r["value"], "note": (f"to {m.group(2)}" if m else (r.get("note") or "")[:160]),
                         "source": r.get("file"), "as_of": r.get("as_of"), "from": "workbook", **_headline(key, r["value"])})
            continue
        f = _fact(file, fact_rx)
        value = (f or {}).get("value")
        if not f and key == "maturity":
            # No fact of its own, but another fact may state it ("... dated 2026-10-20, maturity 2027-10-19").
            for other in file.get("facts") or []:
                hit = _MATURITY_IN_TEXT.search(str(other.get("value") or ""))
                if hit:
                    f, value = other, f"{hit.group(1)} (stated under “{other.get('key')}”)"
                    break
        main.append({"key": key, "label": label, "value": value, "note": "", "source": "the property file (read from the records — not RKB's workbook)" if f else None,
                     "as_of": None, "from": "file" if f else "missing", **_headline(key, value)})
    for key, label, kind, item_rx in _EXTRA:
        r = _pick(rows, kind, item_rx)
        if r and not (key == "total_due" and not str(r["value"]).lstrip().startswith("$")):
            extra[key] = {"label": label, "value": r["value"], "note": (r.get("note") or "")[:200], "source": r.get("file")}
    return {"main": main, "extra": extra, "in_workbooks": bool(rows)}


_REGISTRY: Dict[str, Any] = {"at": 0.0, "rows": []}


def _registry(mongo: Mongo) -> List[Dict[str, Any]]:
    """The people registry changes rarely; it is read once every ten minutes, not on every page."""
    import time
    if time.time() - _REGISTRY["at"] > 600:
        _REGISTRY["rows"] = [p for p in mongo.people.find({}, {"_id": 0, "display_name": 1, "addresses": 1, "org": 1, "role": 1}) if len(str(p.get("display_name") or "")) >= 5]
        _REGISTRY["at"] = time.time()
    return _REGISTRY["rows"]


_ARV: Dict[str, Any] = {"at": 0.0, "by_pid": {}}
_ARV_FIELDS = {"_id": 0, "analysis_id": 1, "property_id": 1, "label": 1, "subject.address": 1, "subject_input.address": 1, "arv": 1, "valuation.confidence": 1,
               "comps_count": 1, "rehab_budget.total": 1, "rehab_budget.source": 1, "created_by_name": 1, "created_side": 1, "created_org_name": 1, "updated_at": 1}


def _arv_rows(mongo: Mongo, pid: str) -> List[Dict[str, Any]]:
    """The finished ARV analyses for this property, oldest first. An analysis is run on an
    address, not on a property record, so it is matched the way a Buildertrend job is."""
    import time
    from .buildertrend import match_property
    if time.time() - _ARV["at"] > 300:
        by: Dict[str, List[Dict[str, Any]]] = {}
        for a in mongo.db["arv_analyses"].find({"status": "done", "arv.value": {"$ne": None}}, _ARV_FIELDS).sort("updated_at", 1):
            addr = (a.get("subject") or {}).get("address") or (a.get("subject_input") or {}).get("address") or a.get("label") or ""
            p = a.get("property_id") or match_property(str(addr))
            if p:
                by.setdefault(p, []).append(a)
        _ARV.update(at=time.time(), by_pid=by)
    return _ARV["by_pid"].get(pid) or []


def arv_of(mongo: Mongo, pid: str) -> Optional[Dict[str, Any]]:
    """The ARV on file for the property: RKB's own latest analysis if there is one,
    otherwise the contractor's — and it says which."""
    rows = _arv_rows(mongo, pid)
    if not rows:
        return None
    ours = [r for r in rows if r.get("created_side") != "contractor"]
    a = (ours or rows)[-1]
    v, conf = a.get("arv") or {}, ((a.get("valuation") or {}).get("confidence") or {})
    return {"analysis_id": a["analysis_id"], "value": v.get("value"), "low": v.get("low"), "high": v.get("high"), "basis": v.get("label") or v.get("basis"),
            "engine": v.get("engine"), "analyst": v.get("analyst"), "confidence": {"score": conf.get("score"), "grade": conf.get("grade"), "label": conf.get("label")},
            "comps": a.get("comps_count"), "rehab_total": (a.get("rehab_budget") or {}).get("total"), "rehab_source": (a.get("rehab_budget") or {}).get("source"),
            "by": a.get("created_by_name"), "from_contractor": a.get("created_side") == "contractor", "org": a.get("created_org_name"),
            "at": a.get("updated_at"), "analyses": len(rows)}


def arv_detail(mongo: Mongo, pid: str) -> Dict[str, Any]:
    """The ARV tab: the figure, how it was reached, and the rehab budget line by line."""
    head = arv_of(mongo, pid)
    if not head:
        return {"exists": False}
    a = mongo.db["arv_analyses"].find_one({"analysis_id": head["analysis_id"]}, {"_id": 0, "rehab_budget": 1, "assumptions": 1, "valuation.confidence": 1, "subject.address": 1}) or {}
    rb = a.get("rehab_budget") or {}
    others = [{"analysis_id": r["analysis_id"], "value": (r.get("arv") or {}).get("value"), "by": r.get("created_by_name"), "from_contractor": r.get("created_side") == "contractor",
               "org": r.get("created_org_name"), "at": r.get("updated_at")} for r in _arv_rows(mongo, pid) if r["analysis_id"] != head["analysis_id"]]
    return {"exists": True, **head, "address": (a.get("subject") or {}).get("address"), "reasons": ((a.get("valuation") or {}).get("confidence") or {}).get("reasons") or [],
            "assumptions": a.get("assumptions") or {},
            "rehab": {"basis": rb.get("basis"), "source": rb.get("source"), "subtotal": rb.get("subtotal"), "permit_cost": rb.get("permit_cost"), "oh_and_p": rb.get("oh_and_p"),
                      "contingency": rb.get("contingency"), "total": rb.get("total"), "per_sqft": rb.get("per_sqft"),
                      "lines": [{"label": l.get("label"), "amount": l.get("amount"), "estimated": bool(l.get("estimated"))} for l in rb.get("lines") or []]},
            "others": others[::-1]}


def people(mongo: Mongo, store: BrainStore, pid: str, file: Dict[str, Any], rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    rows = _rows(store, pid) if rows is None else rows
    registry = _registry(mongo)
    out: List[Dict[str, Any]] = []
    seen_values = set()
    for role, item_rx, fact_rx in _PEOPLE:
        found = []
        r = _pick(rows, "terms", item_rx) if item_rx else None
        if r:
            found.append((r["value"], "RKB workbook"))
        rx = re.compile(fact_rx, re.I)
        for f in file.get("facts") or []:
            if rx.search(str(f.get("key") or "")) and not (role == "Title company" and re.search(r"lien position", str(f.get("key")), re.I)):
                found.append((str(f.get("value") or ""), f"property file — {f.get('key')}"))
        for value, source in found[:2]:
            v = value.strip()
            if not v or v.lower() in seen_values:
                continue
            seen_values.add(v.lower())
            contacts = []
            low = v.lower()
            for p in registry:
                name = str(p["display_name"])
                first_last = re.sub(r"\s*\(.*?\)", "", name).strip().lower()
                # A full name only. A first name alone ("Jessica") matched a different
                # Jessica and would have shown the wrong address beside a title company.
                if len(first_last.split()) >= 2 and re.search(r"(?<![a-z])" + re.escape(first_last) + r"(?![a-z])", low):
                    contacts.append({"name": re.sub(r"\s*\(.*?\)", "", name).strip(), "emails": list(p.get("addresses") or [])[:2], "org": p.get("org")})
            out.append({"role": role, "value": v[:500], "source": source, "contacts": contacts[:3]})
    return out


# ----------------------------------------------------------------- progress
STAGES = ("closing", "construction", "construction_complete", "listed", "under_contract", "sold_repaid")
STAGE_LABEL = {"closing": "Closed / funded", "construction": "Construction", "construction_complete": "Construction complete",
               "listed": "Listed for sale", "under_contract": "Under contract", "sold_repaid": "Sold and repaid"}

BRIEF = """YOUR JOB NOW: WHERE IS THIS DEAL ON ITS PATH?
Every RKB deal runs the same road: RKB closes and funds -> the renovation is built -> it
is finished -> the house is listed -> it goes under contract -> it sells and RKB is repaid.
From the property file and RKB's workbook rows below, say where this deal stands today.

* stage — where the deal stands TODAY, not the furthest point it ever reached. A sale
  contract that fell through puts the house back to "listed"; a listing that was taken
  down with the work finished is "construction_complete". "construction" means work is
  still going on or was left unfinished; "construction_complete" means the work is done
  but the house is not on the market.
* recovery — true if RKB is collecting through lawyers or the courts (demand letter,
  foreclosure, litigation) rather than by an ordinary sale. Say what in recovery_note.
* construction_basis and sale_basis — two sentences at most, each: whose date it is and
  when they gave it, and whether the latest records put it at risk.
* closing_date — the date RKB closed or first funded, if a record gives it.
* construction_percent — only if a record states or clearly supports a figure (a draw
  sheet total, "framing done, drywall next" is NOT a percentage). Otherwise null.
* construction_expected — the date construction is expected to finish, ONLY if a record
  gives one (a schedule, a completion date somebody committed to). Say whose date it is
  and when they gave it in construction_basis. If no record gives a date, null — and say
  in construction_basis that no completion date is on record. Do not estimate.
* listed_date — the date the house went on the market, ONLY if a record gives it (a
  listing agreement, an MLS sheet, an email saying "listed today" or "went live on ..").
  If it was taken off the market and listed again, give the date of the listing that is
  live now. Null if the house is not on the market or no record gives the date. Say in
  listed_basis, in one sentence, which record gives the date (or that none does) and the
  asking price if a record states it. Do not work the date out from other dates.
* sale_expected — the same rule for the sale or payoff: a closing date, a contract
  settlement date, a court date. Otherwise null, with sale_basis saying so.
* one_line — where it stands in one plain sentence, including what is holding it up.
* site_now — what the latest Buildertrend daily logs say is being done on site: ONE short
  plain sentence, at most 40 words, starting with the date of the latest log ("11 Nov:
  upstairs framing being finished for plumbing and electrical rough-in; basement bath
  and bedroom still to frame."). If the latest log is more than 14 days old, start with
  "No site log since <date>." and then say in a few words where that last log left the
  work. Empty string if the job has no logs at all. Give only what the logs say. Do NOT
  add that it is the contractor's report or when Buildertrend was read — the page shows
  both beside this line. The logs tell you what trade is on site; they do not give you
  a percentage, and they do not give you a finish date unless one is written in them.

A date you supply will be shown to Rakesh Sir as "expected". A date that is not in the
records is worse than no date. Dates as YYYY-MM-DD.
"""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["stage", "recovery", "recovery_note", "closing_date", "construction_percent", "construction_expected", "construction_basis",
                 "sale_expected", "sale_basis", "listed_date", "listed_basis", "one_line", "site_now"],
    "properties": {
        "stage": {"type": "string", "enum": list(STAGES)}, "recovery": {"type": "boolean"}, "recovery_note": {"type": "string"},
        "closing_date": {"type": ["string", "null"]}, "construction_percent": {"type": ["integer", "null"]},
        "construction_expected": {"type": ["string", "null"]}, "construction_basis": {"type": "string"},
        "sale_expected": {"type": ["string", "null"]}, "sale_basis": {"type": "string"},
        "listed_date": {"type": ["string", "null"]}, "listed_basis": {"type": "string"}, "one_line": {"type": "string"}, "site_now": {"type": "string"},
    },
}


def _date(v: Any) -> Optional[datetime]:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def refresh_stage(mongo: Mongo, store: BrainStore, pid: str) -> Optional[Dict[str, Any]]:
    """Read where the deal stands off its file, and store it on the file."""
    if pid == bc.GENERAL_ID:
        return None
    file = store.get_file(pid)
    if not file:
        return None
    nums = numbers(store, pid, file)
    wb = "\n".join(f"- {v['label']}: {v['value']}" + (f" ({v['note']})" if v.get("note") else "") for v in nums["extra"].values()) or "(this property is not in the workbooks)"
    facts = "\n".join(f"- {f.get('key')}: {f.get('value')}" for f in file.get("facts") or [])
    acts = "\n".join(render_activity(store.recent_activity(pid, days=30, limit=40)))
    from .buildertrend import Buildertrend
    bt = Buildertrend(mongo)
    site = bt.site_block(pid, days=45)[:90_000] or "(This property has no Buildertrend job.)"
    last_log = bt.summary(pid).get("last_log")
    user = (f"TODAY: {datetime.now(timezone.utc):%Y-%m-%d}\nPROPERTY: {name_of(pid)}\n\nRKB WORKBOOK ROWS:\n{wb}\n\n"
            f"WHERE IT STANDS: {file.get('headline')}\n\nSTATUS:\n{file.get('status')}\n\nKEY FACTS:\n{facts}\n\n"
            f"KEEP IN VIEW:\n" + "\n".join(f"- {w}" for w in file.get("watch") or []) + f"\n\nLAST 30 DAYS:\n{acts}\n{site}")
    try:
        r = structured(system=prompts.CORE + "\n" + BRIEF, user=user, schema=SCHEMA, effort="medium", max_tokens=6000, label=f"stage {pid}")
    except BrainError as exc:
        logger.warning("stage for %s could not be read: %s", pid, exc)
        return None
    d = r["data"]
    closing = _date(d.get("closing_date"))
    if not closing:
        c = (nums["extra"].get("closing") or nums["extra"].get("contract_date") or {}).get("value")
        closing = _date(c)
    stage = {"stage": d["stage"] if d.get("stage") in STAGES else "construction", "recovery": bool(d.get("recovery")),
             "recovery_note": str(d.get("recovery_note") or "")[:300], "closing_date": closing,
             "construction_percent": d.get("construction_percent") if isinstance(d.get("construction_percent"), int) and 0 <= d["construction_percent"] <= 100 else None,
             "construction_expected": _date(d.get("construction_expected")), "construction_basis": str(d.get("construction_basis") or "")[:900],
             "sale_expected": _date(d.get("sale_expected")), "sale_basis": str(d.get("sale_basis") or "")[:900],
             "listed_date": _date(d.get("listed_date")), "listed_basis": str(d.get("listed_basis") or "")[:500],
             "one_line": str(d.get("one_line") or "")[:300], "site_now": str(d.get("site_now") or "")[:400], "last_log": last_log,
             "read_at": now(), "cost_usd": cost_of(r["usage"])}
    store.files.update_one({"property_id": pid}, {"$set": {"stage": stage}})
    return stage


#: The three lists the side panel sorts the properties into.
GROUPS = (("construction", "Construction"), ("market", "On market"), ("nonperforming", "Non-performing"), ("sold", "Sold"))


def group_of(stage: Optional[Dict[str, Any]]) -> str:
    """Which list a property belongs in. A deal RKB is collecting through lawyers or the
    courts is non-performing whatever its stage; otherwise a listed or under-contract house
    is on the market, and everything before that is construction."""
    s = stage or {}
    if s.get("recovery"):
        return "nonperforming"
    if s.get("stage") in ("listed", "under_contract"):
        return "market"
    return "sold" if s.get("stage") == "sold_repaid" else "construction"


def days_on_market(stage: Optional[Dict[str, Any]], today: Optional[datetime] = None) -> Optional[int]:
    """Days since the house was listed — only while it is on the market, and only when a
    record gave the listing date. Counted in US Eastern calendar days."""
    s = stage or {}
    listed = s.get("listed_date")
    if s.get("stage") not in ("listed", "under_contract") or not isinstance(listed, datetime):
        return None
    from mangotree.core import eastern
    day = eastern.to_et(today or now()).date()
    return max(0, (day - listed.date()).days)


def stage_view(file: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The stage as the page shows it: the six steps with the current one marked."""
    s = file.get("stage")
    if not s:
        return None
    at = STAGES.index(s["stage"]) if s.get("stage") in STAGES else 1
    # A calendar date, not a moment: sent at midday so it reads as the same day in India and in the US.
    s = {**s, **{k: s[k].replace(hour=12) for k in ("closing_date", "construction_expected", "sale_expected", "listed_date", "last_log") if isinstance(s.get(k), datetime)}}
    return {**s, "index": at, "group": group_of(s), "on_market": s.get("stage") in ("listed", "under_contract"), "days_on_market": days_on_market(file.get("stage")), "steps": [{"key": k, "label": STAGE_LABEL[k], "state": "done" if i < at else "current" if i == at else "ahead"}
                                        for i, k in enumerate(STAGES)]}
