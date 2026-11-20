"""A state RKB has not lent in.

The four states RKB works in are written by hand in ``states.py``. When a deal
comes from any other state, the quick check still runs (on the slowest and
costliest foreclosure we know), but the file and the documents stay shut until
that state has its own sheet.

Claude (Opus 5.5, with web search on the state's own statute and regulator
pages) writes that sheet once: the security instrument and what it must
contain, how a foreclosure runs and how long it takes, rate and point limits,
the recording tax, the contractor licence register, the lender-licence rule,
the wording the loan papers need, and **the extra papers the file must hold in
that state**, each with its reason. A person at RKB reads the sheet and
approves it with RKB's confirmation password. From then on the state works like
the other four for every later deal, and nothing is decided again deal by deal.

A sheet is stored in ``uw_states``. Only an approved sheet is used; a proposed
one changes nothing. Every rule in a sheet carries the source it came from, and
what the model could not confirm is listed under ``unsure`` for the person
approving it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import checklist as C
from . import documents as D
from . import states as S

PARTIES = [C.OWNER, C.ROI, C.CONTRACTOR, C.TITLE, C.INSURANCE, C.RKB]

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "The state's name, e.g. Pennsylvania."},
        "instrument": {"type": "string", "enum": ["Deed of Trust", "Mortgage"], "description": "The instrument lenders actually use on residential property in this state."},
        "instrument_must_have": {"type": "array", "items": {"type": "string"}, "description": "What the instrument must contain to be enforceable and to foreclose by the fastest lawful route, each with its statute."},
        "trustee_rule": {"type": "string", "description": "Who may be trustee, if a deed of trust state; empty for a mortgage state."},
        "foreclosure": {"type": "object", "properties": {
            "route": {"type": "string", "description": "At most 14 words: judicial or not, and the route."},
            "months": {"type": "integer", "description": "Realistic months from default to sale on a vacant, non-owner-occupied house."},
            "months_owner_occupied": {"type": "integer"},
            "cost": {"type": "integer", "description": "Realistic lender's cost in dollars (attorney, trustee or court, advertising, title)."},
            "steps": {"type": "array", "items": {"type": "string"}}}, "required": ["route", "months", "months_owner_occupied", "cost", "steps"]},
        "redemption": {"type": "string", "description": "Any right of redemption after the sale, and for how long."},
        "licence_register": {"type": "string", "description": "The contractor licence a home renovation needs here and the register it is checked on."},
        "lender_licence": {"type": "string", "description": "Whether an out-of-state company needs a licence to make this loan to (a) an LLC for a business purpose and (b) an individual; the exemption; the consequence of lending unlicensed."},
        "rate_limits": {"type": "array", "items": {"type": "string"}, "description": "Usury and fee limits for a first lien and for a second lien on a 1-4 family house, for business-purpose and for individual borrowers. Say whether 18% a year plus 2 points for 12 months fits."},
        "recording_tax": {"type": "object", "properties": {
            "basis": {"type": "string", "description": "How the tax on recording this instrument is worked out, in a few words."},
            "percent_of_amount": {"type": "number", "description": "The total tax as a percent of the amount secured (0 if none), for a typical county."}}, "required": ["basis", "percent_of_amount"]},
        "mechanics_lien": {"type": "string", "description": "How a contractor's lien ranks against a renovation lender's instrument here, and the step that keeps the lender first."},
        "contract_must_have": {"type": "array", "items": {"type": "string"}, "description": "What a home-improvement contract must contain here (licence number, notices, cancellation right)."},
        "notes": {"type": "array", "items": {"type": "string"}, "description": "At most five other things a lender must know before its first loan in this state."},
        "text": {"type": "object", "description": "Wording for the loan papers.", "properties": {
            "law": {"type": "string", "description": "As written after 'governed by the laws of', e.g. 'the Commonwealth of Pennsylvania'."},
            "records": {"type": "string", "description": "Where the instrument is recorded, with {county} where the county's name goes, e.g. 'the Office of the Recorder of Deeds of {county}, Pennsylvania'."},
            "notary": {"type": "string", "description": "The venue line of a notary acknowledgment, e.g. 'COMMONWEALTH OF PENNSYLVANIA, COUNTY OF ____________________, ss:'."},
            "courts": {"type": "string", "description": "e.g. 'the state or federal courts located in Pennsylvania'."},
            "lien_law": {"type": "string", "description": "One or two sentences, for the contractor's no-lien covenant, naming this state's mechanics' lien statute and subordinating the contractor's lien to the Lender's instrument."},
            "remedy": {"type": "array", "description": "The remedies section of the security instrument after acceleration: one to three paragraphs in formal document language, naming the statutes.", "items": {
                "type": "object", "properties": {"lead": {"type": "string"}, "text": {"type": "string"}}, "required": ["lead", "text"]}},
            "advances": {"type": "string", "description": "One paragraph, formal document language: that the instrument secures the construction advances with priority from recording, naming the statute and using the words MAXIMUM_PRINCIPAL where the dollar amount goes."}},
            "required": ["law", "records", "notary", "courts", "lien_law", "remedy", "advances"]},
        "extra_lines": {"type": "array", "description": "Papers or facts the loan file must hold in THIS state that a Maryland file would not ask for. None if there are none. Do not repeat the standard lines.", "items": {
            "type": "object", "properties": {
                "name": {"type": "string", "description": "Short name, at most six words."},
                "label": {"type": "string", "description": "One sentence: exactly what is asked."},
                "why": {"type": "string", "description": "One sentence: why this state requires it, with the statute."},
                "party": {"type": "string", "enum": PARTIES}, "when": {"type": "string", "enum": [C.FILE, C.CLOSING, C.AFTER]},
                "needs_file": {"type": "boolean"}, "required": {"type": "boolean", "description": "True if the lien or the loan is at risk without it."},
                "foreclosure": {"type": "boolean", "description": "True if a foreclosure in this state will ask for it."},
                "checks": {"type": "array", "items": {"type": "string"}, "description": "What must be true on the paper, as checkable statements."}},
            "required": ["name", "label", "why", "party", "when", "needs_file", "required", "foreclosure", "checks"]}},
        "sources": {"type": "array", "items": {"type": "object", "properties": {"rule": {"type": "string"}, "citation": {"type": "string"}, "url": {"type": "string"}}, "required": ["rule", "citation", "url"]}},
        "unsure": {"type": "array", "items": {"type": "string"}, "description": "Every rule above you could not confirm on the state's own statute or regulator page."},
    },
    "required": ["name", "instrument", "instrument_must_have", "trustee_rule", "foreclosure", "redemption", "licence_register", "lender_licence", "rate_limits", "recording_tax", "mechanics_lien",
                 "contract_must_have", "notes", "text", "extra_lines", "sources", "unsure"],
}

STANDARD = ("owner's contact details; proof nobody lives in the house; ownership and authority papers (estate, trust, company); personal guarantor for a company; mortgage statement and "
            "payoff permission where a mortgage stays; listing agent; signed customer agreement; proposal and builder's cost; signed measurement; photos; comparable sales; completion date; "
            "contractor's licence, liability insurance, workers' compensation, signed scope, W-9, no-lien covenant; title commitment; judgment and bankruptcy search; tax and water "
            "certificate; payoff letters; settlement statement; closing protection letter; construction-loan endorsement; recording of the instrument; original note to the custodian; "
            "recorded instrument and loan policy; property insurance naming the lender; identity papers held by the lender's attorney.")

SYSTEM = """You write the state rule sheet a private lender needs before its first loan in a US state.

The lender, RKB Consulting Group, Inc. (a New York corporation), funds the renovation of a house whose owner wants to fix it and sell it. The owner signs a promissory note to RKB for the renovation contract price and a security instrument on the house. RKB pays the contractor in draws (90% of completed work, 10% held to the sale). There are no monthly payments; the note is due at the sale or in 12 months, at 18% a year with 2 points, sometimes interest-free for the first six months. RKB lends only on houses nobody lives in, in first position or behind one mortgage. If the house does not sell, RKB forecloses, so its one aim is to be fully secured in a foreclosure: a lien that is valid, first in line where it should be, and enforceable by the fastest lawful route.

Work from the state's own sources: its code, its court rules, its banking and contractor regulators, its revenue department, the county recorder. Search for them and read them. Give the statute section beside every rule. Never state a rule from memory alone: if you could not confirm it on a primary or clearly authoritative page, still give your best statement and list it under `unsure`. Foreclosure months and cost are estimates: say realistic figures, not best cases.

The file already asks for these standard lines in every state, so do not repeat them as extra lines: """ + STANDARD + """

`extra_lines` are what THIS state adds: for example a notice of commencement where the state requires one, a spouse's signature on homestead property, a statutory disclosure or notice, a specific affidavit a recorder requires, a waiver form the statute prescribes, a registration the lender needs. Each must name its statute. Add none that the law does not require. An extra line is something RKB can hold by the closing or within weeks after recording. A step that is taken only if RKB later forecloses (a pre-foreclosure notice, a deficiency petition) is NOT an extra line: put it in `foreclosure.steps`.

Write for a busy reader: every free-text rule is at most three sentences, the conclusion first.

The `text` fields go straight into loan documents: write them in formal document language, accurate to this state, with no explanation around them.

Report by calling the state_sheet tool exactly once."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def propose(code: str, mongo, user: Dict[str, Any], emit: Optional[Callable[[str], None]] = None, searches: int = 14) -> Dict[str, Any]:
    """Have the model write the sheet for one state. Stored as proposed; changes nothing until approved."""
    from mangotree.arv import config as arv_cfg
    from mangotree.arv.analyst import client, model_kwargs
    from mangotree.core.usage import METER
    code = (code or "").strip().upper()[:2]
    if len(code) != 2 or not code.isalpha():
        raise ValueError("a two-letter state is needed")
    if code in BUILT_IN:
        raise ValueError("%s is already set up" % S.STATES[code]["name"])
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": searches},
             {"name": "state_sheet", "description": "The rule sheet for this state.", "input_schema": SCHEMA}]
    messages: List[Dict[str, Any]] = [{"role": "user", "content": "Write the state sheet for %s." % code}]
    before = METER.snapshot()
    sheet: Optional[Dict[str, Any]] = None
    n = 0
    for _ in range(10):
        with client().messages.stream(model=arv_cfg.ARV_MODEL, max_tokens=32000, system=SYSTEM, messages=messages, tools=tools, tool_choice={"type": "auto"}, **model_kwargs()) as s:
            r = s.get_final_message()
        METER.record_anthropic(arv_cfg.ARV_MODEL, r)
        for b in r.content:
            t = getattr(b, "type", None)
            if t == "server_tool_use" and getattr(b, "name", "") == "web_search":
                n += 1
                if emit:
                    emit("Reading %s's rules: %s" % (code, (getattr(b, "input", {}) or {}).get("query", "")))
            if t == "tool_use" and getattr(b, "name", "") == "state_sheet":
                sheet = dict(b.input)
        if sheet:
            break
        if r.stop_reason == "refusal":
            raise RuntimeError("the model declined this request")
        messages.append({"role": "assistant", "content": r.content})
        if r.stop_reason != "pause_turn":
            messages.append({"role": "user", "content": "Call the state_sheet tool now with the full sheet."})
    if not sheet:
        raise RuntimeError("no sheet came back for %s" % code)
    usage = METER.summarize(METER.diff(before, METER.snapshot()))
    row = {"code": code, "sheet": tidy(sheet), "status": "proposed", "proposed_at": _now(), "proposed_by": user.get("user_id"), "proposed_by_name": user.get("name"),
           "searches": n, "cost_usd": round(float(usage.get("total_cost_usd") or 0), 4), "approved_at": None}
    mongo.db["uw_states"].replace_one({"code": code}, row, upsert=True)
    return row


def tidy(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """Make a sheet safe to use: bounded numbers, known parties, a key for each extra line."""
    s = dict(sheet)
    fc = dict(s.get("foreclosure") or {})
    fc["months"] = max(1, min(60, int(fc.get("months") or S.WORST_CASE["months"])))
    fc["months_owner_occupied"] = max(fc["months"], min(72, int(fc.get("months_owner_occupied") or fc["months"])))
    fc["cost"] = max(1000, min(100000, int(fc.get("cost") or S.WORST_CASE["cost"])))
    fc["estimate"] = True
    s["foreclosure"] = fc
    s["instrument"] = "Mortgage" if s.get("instrument") == "Mortgage" else "Deed of Trust"
    tax = dict(s.get("recording_tax") or {})
    tax["percent_of_amount"] = max(0.0, min(5.0, float(tax.get("percent_of_amount") or 0)))
    s["recording_tax"] = tax
    lines = []
    for n, l in enumerate(s.get("extra_lines") or [], start=1):
        if not (l.get("name") and l.get("label")):
            continue
        lines.append({"key": "state_%d" % n, "name": str(l["name"])[:80], "label": str(l["label"])[:400], "why": str(l.get("why") or "")[:400],
                      "party": l.get("party") if l.get("party") in PARTIES else C.TITLE, "when": l.get("when") if l.get("when") in (C.FILE, C.CLOSING, C.AFTER) else C.FILE,
                      "needs_file": bool(l.get("needs_file")), "required": bool(l.get("required", True)), "foreclosure": bool(l.get("foreclosure")),
                      "checks": [str(c)[:300] for c in (l.get("checks") or [])][:8]})
    s["extra_lines"] = lines[:12]
    return s


def approve(code: str, mongo, user: Dict[str, Any], drop: Optional[List[str]] = None) -> Dict[str, Any]:
    """Approve a sheet. ``drop`` names the extra lines the person approving it does not want in the file."""
    code = (code or "").upper()
    row = mongo.db["uw_states"].find_one({"code": code}, {"_id": 0})
    if not row:
        raise ValueError("no sheet has been written for %s" % code)
    gone = set(drop or [])
    if gone:
        lines = row["sheet"].get("extra_lines") or []
        row["dropped_lines"] = [l["name"] for l in lines if l["key"] in gone]
        row["sheet"]["extra_lines"] = [l for l in lines if l["key"] not in gone]
    row.update({"status": "approved", "approved_at": _now(), "approved_by": user.get("user_id"), "approved_by_name": user.get("name")})
    mongo.db["uw_states"].replace_one({"code": code}, row)
    register(code, row["sheet"])
    return row


BUILT_IN = tuple(S.STATES.keys())


def register(code: str, sheet: Dict[str, Any]) -> None:
    """Put an approved sheet to work: the engine, the list and the documents read it like a built-in state."""
    if code in BUILT_IN:
        return
    t = sheet.get("text") or {}
    S.STATES[code] = {"name": sheet.get("name") or code, "instrument": sheet["instrument"], "instrument_must_have": sheet.get("instrument_must_have") or [],
                      "foreclosure": sheet["foreclosure"], "licence_register": sheet.get("licence_register") or "the state contractor licence",
                      "lender_licence": sheet.get("lender_licence") or "", "contract_must_have": sheet.get("contract_must_have") or [], "mechanics_lien": sheet.get("mechanics_lien") or "",
                      "notes": (sheet.get("rate_limits") or []) + (sheet.get("notes") or []), "written_by_claude": True, "recording_tax": sheet.get("recording_tax") or {}}
    S.EXTRA_LINES[code] = sheet.get("extra_lines") or []
    D.STATE_TEXT[code] = {"law": t.get("law") or ("the State of " + (sheet.get("name") or code)), "records": t.get("records") or "the land records of {county}",
                          "notary": t.get("notary") or "STATE OF ____________________, COUNTY OF ____________________:", "courts": t.get("courts") or "the courts of that state",
                          "lien_law": t.get("lien_law") or "", "remedy": t.get("remedy") or [], "advances": t.get("advances") or ""}


def load(mongo) -> List[str]:
    """Register every approved sheet (called when the API starts)."""
    done = []
    for row in mongo.db["uw_states"].find({"status": "approved"}, {"_id": 0}):
        register(row["code"], row["sheet"])
        done.append(row["code"])
    return done


def listing(mongo) -> List[Dict[str, Any]]:
    out = [{"code": k, "name": S.STATES[k]["name"], "status": "built in"} for k in BUILT_IN]
    for row in mongo.db["uw_states"].find({}, {"_id": 0}):
        out.append({"code": row["code"], "name": (row.get("sheet") or {}).get("name"), "status": row.get("status"), "proposed_by_name": row.get("proposed_by_name"),
                    "proposed_at": row.get("proposed_at"), "approved_by_name": row.get("approved_by_name"), "approved_at": row.get("approved_at"), "cost_usd": row.get("cost_usd")})
    return out
