"""Claude Opus 5.5 (effort high) on the valuation file.

Three jobs, all judgement, none arithmetic:

  discover_comps   with web search, find recently sold comparable homes near
                   the subject on public listing pages and report them in a
                   fixed shape with the page each came from
  analyze          read the whole file — subject, market, comps with every
                   adjustment line, the reconciled value, the rehab budget and
                   the deal math — and return a structured opinion: its own
                   ARV and range, what it would keep, drop or re-adjust and
                   why, the risks, the questions whose answers would move the
                   number, and a view on the maximum offer
  chat             answer a follow-up about this file, streamed

The structured calls use a tool schema (``_structured``), never hand-written
JSON. The model id and effort come from ``config``. Documents attached to the
file (``documents.py``) are read once at upload and their digests go to the
model with everything else.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Callable, Dict, List, Optional

import anthropic

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.core.usage import METER

from . import config as cfg
from . import documents as documents_mod
from .providers import new_comp

_CLIENT: Optional[anthropic.Anthropic] = None
_LOCK = threading.Lock()
#: Flipped off after the API rejects the fallbacks parameter once.
_FALLBACKS_OK = [cfg.ARV_FALLBACKS]


def client() -> anthropic.Anthropic:
    global _CLIENT
    with _LOCK:
        if _CLIENT is None:
            _CLIENT = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key, max_retries=3, timeout=600.0)
        return _CLIENT


def model_kwargs() -> Dict[str, Any]:
    # Opus 5.5: thinking is always on; effort is the only depth control, and the
    # model's default is medium, so it is set explicitly (admin ask: high).
    return {"output_config": {"effort": cfg.ARV_EFFORT}, "thinking": {"type": "adaptive"}}


SYSTEM = """You are the chief valuation analyst for RKB Consulting Group, a renovation lender in the United States. You review After Repair Value (ARV) files the way a senior residential appraiser reviews a Form 1004 / URAR sales-comparison grid, and the way a hard-money underwriter reads a flip: skeptically, with numbers, and in plain English.

Standards you hold the file to
- Sales comparison approach, subject valued AS REPAIRED (UAD condition C2 unless the file says the renovation is lighter, then C3). Comps that were themselves renovated resales are the best evidence of ARV; tired C4-C5 sales need a condition adjustment and are weaker evidence.
- Time / market-conditions adjustments are mandatory (Fannie Mae, since March 2025): each sale is walked forward from its sale date with the ZIP home-value index in the file. Say if the index looks wrong for this street.
- Adjustments must reflect the market, not rules of thumb. The 15% net / 25% gross lines are a heuristic, not a rule, but a comp above them is less comparable and deserves less weight.
- Bracketing: the subject should sit inside the comps' range of size, age and adjusted value. Extrapolation outside the range must be called out.
- The engine's arithmetic is correct by construction; your job is whether its INPUTS and CHOICES are right: comp selection, condition assumptions, the $/sqft rate, the market trend, whether a comp is really a sale or a listing, whether the rehab scope matches the condition described, and what the person has not told you that would move the number.
- Never invent a sale. If you doubt a comp, say what would verify it (listing page, MLS, county record).
- Dollar figures rounded to the nearest $1,000 in prose; ranges as low-high.
- Documents in the file (inspection, appraisal, contractor bid, listing, photos, title, tax) are evidence that outranks assumptions: a measured living area beats a typed one, a bid beats a tier estimate, an inspector's finding changes the scope. Cite the document by name when you lean on it, and say when two documents or a document and the person disagree.
- The subject is valued with its AS-REPAIRED room count and living area when the plan adds a bedroom, a bath or square footage (beds_after, baths_after, sqft_after); say so when that drives the number.

Write for the CEO who will decide whether to fund. Every sentence carries a number or a decision. The single most important caveat comes first."""

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence: the ARV opinion and the biggest caveat."},
        "arv_opinion": {"type": "number", "description": "Your point opinion of value as repaired, USD."},
        "arv_low": {"type": "number"}, "arv_high": {"type": "number"},
        "agrees_with_engine": {"type": "boolean", "description": "True if your opinion is within the engine's range."},
        "confidence": {"type": "string", "enum": ["high", "good", "moderate", "low", "very_low"]},
        "confidence_reason": {"type": "string"},
        "summary_points": {"type": "array", "minItems": 3, "maxItems": 7, "items": {"type": "string"},
                           "description": "The review as 3-7 bullets a CEO reads in twenty seconds. Each one sentence, at most 24 words, with a number or a named comp or document. Order: value, comps, condition and rehab, market, deal."},
        "summary": {"type": "string", "description": "The fuller reasoning behind the bullets, two to four short paragraphs (markdown allowed). Read only by someone who wants the detail."},
        "value_drivers": {"type": "array", "maxItems": 6, "description": "What would move the ARV most, each as a signed dollar effect on the point value, largest first.",
                          "items": {"type": "object", "properties": {"factor": {"type": "string", "description": "Short label, e.g. 'Comp 3 is a listing, not a sale'."}, "impact_usd": {"type": "number", "description": "Signed: negative lowers the ARV."}, "why": {"type": "string"}},
                                    "required": ["factor", "impact_usd", "why"]}},
        "verdict": {"type": "string", "enum": ["fund", "fund_with_conditions", "renegotiate", "pass"], "description": "Your lending verdict at the purchase price in the file (or the recommended MAO if none)."},
        "verdict_reason": {"type": "string", "description": "One sentence."},
        "comp_notes": {"type": "array", "items": {"type": "object", "properties": {
            "comp_id": {"type": "string"}, "verdict": {"type": "string", "enum": ["keep", "drop", "caution", "add"]},
            "note": {"type": "string"},
            "suggested_condition": {"type": "string", "description": "C1-C6 if you believe the comp's condition differs from the file's assumption."},
            "suggested_adjustments": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "amount": {"type": "number"}, "why": {"type": "string"}}, "required": ["label", "amount", "why"]}}
        }, "required": ["comp_id", "verdict", "note"]}},
        "adjustment_critique": {"type": "array", "items": {"type": "object", "properties": {"rate": {"type": "string"}, "engine_value": {"type": "string"}, "your_view": {"type": "string"}, "why": {"type": "string"}}, "required": ["rate", "your_view", "why"]}},
        "market_read": {"type": "string"},
        "rehab_read": {"type": "string", "description": "Does the scope and budget match the condition described? What is missing?"},
        "mao_opinion": {"type": "number", "description": "Your maximum allowable offer, USD."},
        "mao_reason": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "severity": {"type": "string", "enum": ["critical", "high", "watch"]}, "detail": {"type": "string"}}, "required": ["title", "severity", "detail"]}},
        "questions": {"type": "array", "description": "Questions for the person whose answers would change the number most. Ask only what matters.",
                      "items": {"type": "object", "properties": {"question": {"type": "string"}, "why_it_matters": {"type": "string"}, "impact": {"type": "string", "enum": ["high", "medium", "low"]}}, "required": ["question", "why_it_matters", "impact"]}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "arv_opinion", "arv_low", "arv_high", "agrees_with_engine", "confidence", "confidence_reason", "summary_points", "summary", "value_drivers", "verdict", "verdict_reason", "comp_notes", "market_read", "rehab_read", "mao_opinion", "mao_reason", "risks", "questions", "next_steps"],
}

_COMPS_SCHEMA = {
    "type": "object",
    "properties": {
        "comps": {"type": "array", "items": {"type": "object", "properties": {
            "address": {"type": "string"}, "sale_price": {"type": "number"}, "sale_date": {"type": "string", "description": "YYYY-MM-DD"},
            "price_basis": {"type": "string", "enum": ["closed", "listed", "estimated"]},
            "beds": {"type": "number"}, "baths": {"type": "number"}, "sqft": {"type": "number"}, "lot_sqft": {"type": "number"}, "year_built": {"type": "number"},
            "property_type": {"type": "string", "enum": ["single_family", "townhouse", "condo", "multi_2_4", "manufactured"]},
            "condition": {"type": "string", "description": "C1-C6 if the listing text or photos make it clear (renovated = C2/C3, dated = C4, distressed = C5/C6); omit if unknown."},
            "distance_mi": {"type": "number"}, "url": {"type": "string"}, "note": {"type": "string", "description": "What the page said: renovated, as-is, estate sale, days on market."},
        }, "required": ["address", "sale_price", "sale_date", "price_basis", "url"]}},
        "search_notes": {"type": "string", "description": "What you searched, what you could not find, what to verify."},
    },
    "required": ["comps", "search_notes"],
}


def _structured(*, system: Any, user: Any, tool_name: str, schema: Dict[str, Any], description: str, max_tokens: int) -> Dict[str, Any]:
    """One tool-shaped reply from Opus 5.5. ``core.llm_json.json_call`` is not
    used here because its retry path disables thinking, which this model
    rejects; the retry here nudges instead."""
    tool = {"name": tool_name, "description": description, "input_schema": schema}
    sys_blocks = list(system) + [{"type": "text", "text": "\nRespond by calling the %s tool exactly once. Do not answer in prose." % tool_name}]
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user}]
    last_text = ""
    for attempt in (1, 2):
        with client().messages.stream(model=cfg.ARV_MODEL, max_tokens=max_tokens, system=sys_blocks, messages=messages, tools=[tool],
                                      tool_choice={"type": "auto"}, **model_kwargs()) as s:
            r = s.get_final_message()
        METER.record_anthropic(cfg.ARV_MODEL, r)
        for b in r.content:
            if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == tool_name:
                return dict(b.input)
        last_text = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", None) == "text")
        if r.stop_reason == "refusal":
            raise RuntimeError("the model declined this analysis (%s)" % getattr(getattr(r, "stop_details", None), "category", "policy"))
        if r.stop_reason == "max_tokens":
            raise RuntimeError("the analysis was cut off at %d output tokens; raise the cap" % max_tokens)
        logger.info("%s: no tool call on attempt %d (stop=%s); nudging", tool_name, attempt, r.stop_reason)
        messages.append({"role": "assistant", "content": r.content})
        messages.append({"role": "user", "content": "Call the %s tool now with your full analysis." % tool_name})
    raise RuntimeError("%s: no structured reply (text=%r)" % (tool_name, last_text[:160]))


def _strip(d: Dict[str, Any]) -> Dict[str, Any]:
    """Remove bulky or redundant fields before the file goes to the model."""
    out = dict(d)
    out.pop("raw", None)
    return out


def _file_for_model(doc: Dict[str, Any]) -> str:
    comps = []
    for c in doc.get("comps") or []:
        row = {k: c.get(k) for k in ("comp_id", "source", "address", "distance_mi", "sale_price", "sale_date", "price_basis", "listing_price", "dom",
                                     "property_type", "beds", "baths", "sqft", "lot_sqft", "year_built", "garage_spaces", "basement", "pool", "condition",
                                     "url", "note", "included", "selected", "forced", "similarity", "weight", "fails")}
        if c.get("adjustment"):
            a = c["adjustment"]
            row["adjustment"] = {"lines": [{"label": l["label"], "amount": l["amount"], "why": l["why"]} for l in a["lines"]], "net": a["net"], "gross": a["gross"],
                                 "net_pct": a["net_pct"], "gross_pct": a["gross_pct"], "adjusted_price": a["adjusted_price"], "adjusted_ppsf": a.get("adjusted_ppsf"), "flags": a["flags"]}
        if c.get("deductions"):
            row["score_deductions"] = [d["reason"] for d in c["deductions"]]
        comps.append(row)
    market = dict(doc.get("market") or {})
    for k in ("zhvi", "mortgage"):
        if isinstance(market.get(k), dict):
            market[k] = {kk: vv for kk, vv in market[k].items() if kk != "history"}
    uw = dict(doc.get("underwriting") or {})
    uw.pop("sensitivity", None)
    payload = {
        "subject": _strip(doc.get("subject") or {}), "geocode": {k: v for k, v in (doc.get("geocode") or {}).items() if k in ("formatted", "county", "state", "zip", "precision")},
        "records": doc.get("records"), "market": market, "rehab_budget": {k: v for k, v in (doc.get("rehab_budget") or {}).items() if k != "lines"},
        "rehab_lines": [{"label": l["label"], "amount": l["amount"]} for l in (doc.get("rehab_budget") or {}).get("lines") or [] if l.get("amount")],
        "valuation": doc.get("valuation"), "comps": comps, "underwriting": uw,
        "documents": documents_mod.for_model(doc.get("documents") or []),
        "user_notes": (doc.get("subject") or {}).get("notes"), "user_answers": doc.get("answers") or [],
        "previous_analysis_questions": [q.get("question") for q in ((doc.get("analysis") or {}).get("questions") or [])],
    }
    return json.dumps(payload, indent=1, default=str)


def analyze(doc: Dict[str, Any], emit: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    if emit:
        emit("Claude Opus 5.5 is reading the file (effort %s)…" % cfg.ARV_EFFORT)
    user = ("Review this ARV file and return your analysis by calling the arv_analysis tool.\n\n"
            "FILE:\n" + _file_for_model(doc) +
            "\n\nNotes: comp_id values must be copied exactly. If the person already answered questions (user_answers), use the answers and do not ask them again. "
            "Your arv_opinion is your own number — agree with the engine only if the evidence supports it.")
    before = METER.snapshot()
    out = _structured(system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}], user=user,
                      tool_name="arv_analysis", schema=_ANALYSIS_SCHEMA, description="The structured ARV review.", max_tokens=24000)
    out["model"] = cfg.ARV_MODEL
    out["effort"] = cfg.ARV_EFFORT
    out["usage"] = METER.summarize(METER.diff(before, METER.snapshot()))
    return out


def discover_comps(subject: Dict[str, Any], geo: Dict[str, Any], *, radius_mi: float, months: int, emit: Optional[Callable[[str], None]] = None,
                   max_searches: int = 12) -> Dict[str, Any]:
    """Find recent sales near the subject with web search. Returns comps
    (source=web) and the model's notes on what it could and could not find."""
    addr = geo.get("formatted") or subject.get("address")
    desc = ", ".join(str(x) for x in [
        ("%s bed" % subject["beds"]) if subject.get("beds") is not None else None,
        ("%s bath" % subject["baths"]) if subject.get("baths") is not None else None,
        ("%s sqft" % int(subject["sqft"])) if subject.get("sqft") else None,
        ("built %s" % int(subject["year_built"])) if subject.get("year_built") else None,
        (subject.get("property_type") or "").replace("_", " ") or None] if x)
    location = {"type": "approximate", "country": "US", "timezone": "America/New_York"}
    for key, src in (("city", "city"), ("region", "state_name")):
        if (geo.get(src) or "").strip():
            location[key] = geo[src].strip()
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches, "user_location": location},
        {"name": "report_comps", "description": "Report the comparable sales found.", "input_schema": _COMPS_SCHEMA},
    ]
    prompt = (
        "Find recently SOLD comparable homes for an After Repair Value analysis.\n"
        "Subject: %s (%s).\n"
        "Look for closed sales within about %.1f miles and the last %d months, similar size and type, preferring renovated resales. "
        "Search public listing pages (Zillow, Redfin, Realtor.com, Homes.com, Trulia, Movoto, county records, local brokerage sold pages). "
        "Read the sold price, sold date, beds, baths, living area, lot, year built and whether the home was renovated. "
        "Report 5 to 10 comps by calling report_comps exactly once when done; mark price_basis 'closed' only when the page states a sold price, "
        "'listed' when you only saw an asking price, 'estimated' when a site estimate is all there is. Include the page URL for each. "
        "Do not report the subject itself. Do not fabricate: a comp you cannot source is left out and mentioned in search_notes."
    ) % (addr, desc or "details unknown", radius_mi, months)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]
    if emit:
        emit("Searching the web for recent sales near %s…" % addr)
    before = METER.snapshot()
    comps: List[Dict[str, Any]] = []
    notes = ""
    searches = 0
    for turn in range(6):
        r = client().messages.create(model=cfg.ARV_MODEL, max_tokens=16000, system=SYSTEM, messages=messages, tools=tools,
                                     tool_choice={"type": "auto"}, **model_kwargs())
        METER.record_anthropic(cfg.ARV_MODEL, r)
        for b in r.content:
            t = getattr(b, "type", None)
            if t == "server_tool_use" and getattr(b, "name", "") == "web_search":
                searches += 1
                if emit:
                    emit("Web search %d: %s" % (searches, (getattr(b, "input", {}) or {}).get("query", "")))
            if t == "tool_use" and getattr(b, "name", "") == "report_comps":
                data = dict(b.input)
                notes = str(data.get("search_notes") or "")
                for c in data.get("comps") or []:
                    comps.append(new_comp(source="web", address=c.get("address"), sale_price=c.get("sale_price"), sale_date=c.get("sale_date"),
                                          price_basis=c.get("price_basis") or "estimated", beds=c.get("beds"), baths=c.get("baths"), sqft=c.get("sqft"),
                                          lot_sqft=c.get("lot_sqft"), year_built=c.get("year_built"), property_type=c.get("property_type"),
                                          condition=c.get("condition"), distance_mi=c.get("distance_mi"), url=c.get("url"), note=c.get("note")))
        if r.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": r.content})
            continue
        if r.stop_reason == "tool_use" and comps:
            break
        if r.stop_reason == "tool_use":
            # a client tool other than report_comps is impossible here; close the turn
            messages.append({"role": "assistant", "content": r.content})
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": next(b.id for b in r.content if getattr(b, "type", None) == "tool_use"), "content": "noted"}]})
            continue
        if r.stop_reason == "refusal":
            notes = "The model declined the search (%s)." % getattr(getattr(r, "stop_details", None), "category", "policy")
            break
        if r.stop_reason in ("end_turn", "max_tokens") and not comps:
            text = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", None) == "text")
            if turn < 2:
                messages.append({"role": "assistant", "content": r.content})
                messages.append({"role": "user", "content": "Now call report_comps with what you found (an empty list with search_notes is acceptable)."})
                continue
            notes = notes or text[:600]
            break
        break
    usage = METER.summarize(METER.diff(before, METER.snapshot()))
    return {"comps": comps, "notes": notes, "searches": searches, "usage": usage}


def _geocode_comps(comps: List[Dict[str, Any]], subject: Dict[str, Any], emit=None) -> None:
    """Web comps come with an address only; place them so distance and the map work."""
    from .geocode import geocode, haversine_miles
    for c in comps:
        if c.get("lat") is not None or not c.get("address"):
            continue
        try:
            g = geocode(c["address"])
            c["lat"], c["lon"] = g["lat"], g["lon"]
            if subject.get("lat") is not None:
                c["distance_mi"] = round(haversine_miles(subject["lat"], subject["lon"], g["lat"], g["lon"]), 2)
        except Exception as exc:
            logger.info("comp geocode failed for %r: %s", c.get("address"), exc)


def chat(doc: Dict[str, Any], history: List[Dict[str, str]], question: str, on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    system = [{"type": "text", "text": SYSTEM + "\n\nYou are answering follow-up questions about ONE valuation file, given below. Answer from the file; "
               "if the answer needs a fact that is not in it, say what to check. Keep answers under 250 words unless a table is asked for.\n\nFILE:\n" + _file_for_model(doc),
               "cache_control": {"type": "ephemeral"}}]
    msgs: List[Dict[str, Any]] = []
    for m in history[-12:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": question})
    before = METER.snapshot()
    text_parts: List[str] = []
    with client().messages.stream(model=cfg.ARV_MODEL, max_tokens=8000, system=system, messages=msgs, **model_kwargs()) as stream:
        for ev in stream:
            if getattr(ev, "type", "") == "content_block_delta" and getattr(getattr(ev, "delta", None), "type", "") == "text_delta":
                text_parts.append(ev.delta.text)
                if on_delta:
                    on_delta(ev.delta.text)
        final = stream.get_final_message()
    METER.record_anthropic(cfg.ARV_MODEL, final)
    answer = "".join(text_parts) or "".join(getattr(b, "text", "") for b in final.content if getattr(b, "type", None) == "text")
    if final.stop_reason == "refusal":
        answer = answer or "The model declined to answer this question."
    return {"answer": answer, "model": cfg.ARV_MODEL, "usage": METER.summarize(METER.diff(before, METER.snapshot())), "stop_reason": final.stop_reason}
