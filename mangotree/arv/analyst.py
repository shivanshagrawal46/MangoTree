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
- Give an AS-IS value as well (as_is_opinion, as_is_low, as_is_high, as_is_basis): what the house would sell for today, in its current condition (condition_current, before any work), to an investor or an as-is buyer. It is a separate number from the ARV and never changes the ARV. Build it from the same comps adjusted down to the current condition, any as-is, shell or distressed sales in the file, the subject's own last sale and listing history, the price under contract, the AVM, and any as-is appraisal among the documents; say in as_is_basis which of these you leaned on. Cross-check: ARV minus the rehab budget minus an investor's margin should land near it.

Write for the CEO who will decide whether to fund. Every sentence carries a number or a decision. The single most important caveat comes first."""

_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence: the ARV opinion and the biggest caveat."},
        "arv_opinion": {"type": "number", "description": "Your point opinion of value as repaired, USD."},
        "arv_low": {"type": "number"}, "arv_high": {"type": "number"},
        "as_is_opinion": {"type": "number", "description": "Your point opinion of the house's value TODAY, in its current condition before any work, USD. Separate from the ARV."},
        "as_is_low": {"type": "number"}, "as_is_high": {"type": "number"},
        "as_is_basis": {"type": "string", "description": "One or two sentences: what the as-is number rests on (comps adjusted to current condition, as-is sales, last sale, contract price, AVM, as-is appraisal) and how it squares with ARV minus rehab."},
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
    "required": ["headline", "arv_opinion", "arv_low", "arv_high", "as_is_opinion", "as_is_low", "as_is_high", "as_is_basis", "agrees_with_engine", "confidence", "confidence_reason", "summary_points", "summary", "value_drivers", "verdict", "verdict_reason", "comp_notes", "market_read", "rehab_read", "mao_opinion", "mao_reason", "risks", "questions", "next_steps"],
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


# ------------------------------------------------------------ as-is files
#: An as-is file (pipeline.is_as_is, 2026-11-12) asks for one number: the value
#: of the house today, in its current condition. It has its own instructions and
#: its own reply shape; the ARV review above is not touched by any of it.
SYSTEM_AS_IS = """You are the chief valuation analyst for RKB Consulting Group, a renovation lender in the United States. This file asks for ONE number: the AS-IS market value of the house, meaning what it would sell for today, in the condition it is in now, with no work done. There is no renovation plan, no rehab budget and no deal in this file. Do not give an After Repair Value, a maximum offer or a lending verdict.

Standards you hold the file to
- Sales comparison approach, subject valued AS IT STANDS at its current condition (as_is_condition in the file, UAD C1-C6: stated by the person, or graded by HouseCanary, or assumed C4 when nobody said). The current condition is the reference point for the whole valuation: every comp is read against it.
- The best evidence is closed, arm's-length sales of similar houses nearby that sold in a similar condition: dated or tired houses sold as they stood, investor purchases at a market price, estate sales that were exposed to the market. A renovated resale is evidence of the ceiling, not of this house: bring it down by what the market actually pays for the difference (the cost of the work, plus an investor's margin, plus the months it takes), and say what you used. A shell, auction or bank sale is the floor unless the subject is itself a shell.
- Name the buyer. A C1-C3 house sells to an owner-occupant with a mortgage. A C5-C6 house usually sells to an investor for cash, and often cannot be financed at all, which lowers the price beyond the repair cost. Say which pool this house sells to and what that does to the number.
- The engine's condition adjustment is a flat percentage per C grade. That usually understates the discount for a house that needs heavy work. The engine's arithmetic is correct by construction; your job is whether its INPUTS and CHOICES are right: comp selection, each comp's condition, the $/sqft rate, the market trend, whether a comp is really a sale or a listing.
- Time / market-conditions adjustments are mandatory: each sale is walked forward from its sale date with the ZIP home-value index in the file. Say if the index looks wrong for this street.
- Other evidence, each with its weight: the subject's own last sale and listing history, an as-is appraisal or a contract price among the documents (strong), the AVM (a model that assumes average condition; discount it for a house in worse shape), the tax assessment (weak).
- Known issues (structure, water, roof, systems, title, tenant in place) each lower what a buyer pays today. Price the large ones in dollars.
- Bracketing: the subject should sit inside the comps' range of size, age and adjusted value. Extrapolation outside the range must be called out.
- Never invent a sale. If you doubt a comp, say what would verify it (listing page, MLS, county record).
- Dollar figures rounded to the nearest $1,000 in prose; ranges as low-high.
- Documents in the file (inspection, appraisal, listing, photos, title, tax) are evidence that outranks assumptions: a measured living area beats a typed one, an inspector's finding changes the condition. Cite the document by name when you lean on it, and say when the stated condition and the documents disagree.

Write for the CEO who will act on the number. Every sentence carries a number or a decision. The single most important caveat comes first."""

_AS_IS_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence: the as-is value and the biggest caveat."},
        "as_is_opinion": {"type": "number", "description": "Your point opinion of the house's market value TODAY, in its current condition with no work done, USD."},
        "as_is_low": {"type": "number"}, "as_is_high": {"type": "number"},
        "as_is_basis": {"type": "string", "description": "One or two sentences: what the number rests on (which sales in similar condition, the last sale, a contract price, the AVM, an as-is appraisal)."},
        "buyer_pool": {"type": "string", "description": "One sentence: who buys this house as it stands (owner-occupant with a mortgage, investor for cash) and what that does to the price."},
        "agrees_with_engine": {"type": "boolean", "description": "True if your opinion is within the engine's range."},
        "confidence": {"type": "string", "enum": ["high", "good", "moderate", "low", "very_low"]},
        "confidence_reason": {"type": "string"},
        "summary_points": {"type": "array", "minItems": 3, "maxItems": 7, "items": {"type": "string"},
                           "description": "The review as 3-7 bullets a CEO reads in twenty seconds. Each one sentence, at most 24 words, with a number or a named comp or document. Order: value, comps, condition, market."},
        "summary": {"type": "string", "description": "The fuller reasoning behind the bullets, two to four short paragraphs (markdown allowed). Read only by someone who wants the detail."},
        "value_drivers": {"type": "array", "maxItems": 6, "description": "What would move the as-is value most, each as a signed dollar effect on the point value, largest first.",
                          "items": {"type": "object", "properties": {"factor": {"type": "string", "description": "Short label, e.g. 'Roof at end of life'."}, "impact_usd": {"type": "number", "description": "Signed: negative lowers the value."}, "why": {"type": "string"}},
                                    "required": ["factor", "impact_usd", "why"]}},
        "comp_notes": _ANALYSIS_SCHEMA["properties"]["comp_notes"],
        "adjustment_critique": _ANALYSIS_SCHEMA["properties"]["adjustment_critique"],
        "market_read": {"type": "string"},
        "condition_read": {"type": "string", "description": "Does the stated condition match the evidence (documents, known issues, the HouseCanary grade, the listing)? What would a buyer's inspector find, and what does each large item cost the price?"},
        "risks": _ANALYSIS_SCHEMA["properties"]["risks"],
        "questions": _ANALYSIS_SCHEMA["properties"]["questions"],
        "next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "as_is_opinion", "as_is_low", "as_is_high", "as_is_basis", "buyer_pool", "agrees_with_engine", "confidence", "confidence_reason", "summary_points", "summary",
                 "value_drivers", "comp_notes", "market_read", "condition_read", "risks", "questions", "next_steps"],
}


def _is_as_is(doc: Dict[str, Any]) -> bool:
    return (doc or {}).get("mode") == "as_is"


def _as_is_file(payload: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
    """The file as an as-is review reads it: no plan, budget or deal blocks, the
    subject without its as-repaired fields, and the engine's figure named for
    what it is on this file (the as-is indication, not an ARV)."""
    out = {"file_type": "AS-IS VALUE ONLY. Value the house as it stands today. There is no renovation plan, rehab budget or deal in this file.",
           "as_is_condition": (doc.get("as_is") or {}).get("condition"),
           "as_is_condition_source": "stated by the person or graded by a feed" if (doc.get("as_is") or {}).get("condition_stated") else "nobody stated it; C4 assumed, so say what the evidence suggests",
           **payload}
    for k in ("rehab_budget", "rehab_lines", "underwriting"):
        out.pop(k, None)
    subj = dict(out.get("subject") or {})
    for k in ("condition_after", "beds_after", "baths_after", "sqft_after", "planned_work"):
        subj.pop(k, None)
    out["subject"] = subj
    v = dict(out.get("valuation") or {})
    for k in ("arv", "arv_low", "arv_high"):
        if k in v:
            v["engine_as_is" + k[3:]] = v.pop(k)
    out["valuation"] = v
    return out


def _analyze_as_is(doc: Dict[str, Any], emit: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    if emit:
        emit("Claude Opus 5.5 is reading the file for its as-is value (effort %s)…" % cfg.ARV_EFFORT)
    user = ("Review this file and return the house's AS-IS value by calling the as_is_analysis tool.\n\n"
            "FILE:\n" + _file_for_model(doc) +
            "\n\nNotes: comp_id values must be copied exactly. If the person already answered questions (user_answers), use the answers and do not ask them again. "
            "Your as_is_opinion is your own number: agree with the engine only if the evidence supports it. Do not give an after-repair value.")
    before = METER.snapshot()
    out = _structured(system=[{"type": "text", "text": SYSTEM_AS_IS, "cache_control": {"type": "ephemeral"}}], user=user,
                      tool_name="as_is_analysis", schema=_AS_IS_SCHEMA, description="The structured as-is value review.", max_tokens=24000)
    out["model"] = cfg.ARV_MODEL
    out["effort"] = cfg.ARV_EFFORT
    out["usage"] = METER.summarize(METER.diff(before, METER.snapshot()))
    return out


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
    if _is_as_is(doc):
        payload = _as_is_file(payload, doc)
    return json.dumps(payload, indent=1, default=str)


def analyze(doc: Dict[str, Any], emit: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    if _is_as_is(doc):
        return _analyze_as_is(doc, emit)
    if emit:
        emit("Claude Opus 5.5 is reading the file (effort %s)…" % cfg.ARV_EFFORT)
    user = ("Review this ARV file and return your analysis by calling the arv_analysis tool.\n\n"
            "FILE:\n" + _file_for_model(doc) +
            "\n\nNotes: comp_id values must be copied exactly. If the person already answered questions (user_answers), use the answers and do not ask them again. "
            "Your arv_opinion is your own number — agree with the engine only if the evidence supports it. "
            "Also give as_is_opinion with its range: the house's value today, before any work; it is separate from the ARV.")
    before = METER.snapshot()
    out = _structured(system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}], user=user,
                      tool_name="arv_analysis", schema=_ANALYSIS_SCHEMA, description="The structured ARV review.", max_tokens=24000)
    out["model"] = cfg.ARV_MODEL
    out["effort"] = cfg.ARV_EFFORT
    out["usage"] = METER.summarize(METER.diff(before, METER.snapshot()))
    return out


def discover_comps(subject: Dict[str, Any], geo: Dict[str, Any], *, radius_mi: float, months: int, emit: Optional[Callable[[str], None]] = None,
                   max_searches: int = 12, as_is: bool = False) -> Dict[str, Any]:
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
    if as_is:
        cond = subject.get("condition_after") or "C4"   # on an as-is subject this is the condition today
        prompt = (
            "Find recently SOLD comparable homes for an AS-IS market value analysis: what the subject would sell for today, as it stands, with no work done.\n"
            "Subject: %s (%s), current condition %s on the UAD scale (C1 new, C3 well kept, C4 dated, C5 needs major work, C6 uninhabitable).\n"
            "Look for closed sales within about %.1f miles and the last %d months, similar size and type, preferring homes that sold in a condition like the subject's "
            "(for a house needing work: as-is, dated, estate and investor purchases; for a well-kept house: ordinary owner-occupant sales). Add one or two renovated resales as the ceiling. "
            "Search public listing pages (Zillow, Redfin, Realtor.com, Homes.com, Trulia, Movoto, county records, local brokerage sold pages). "
            "Read the sold price, sold date, beds, baths, living area, lot, year built and the condition it sold in (renovated, dated, as-is, needs work). "
            "Report 5 to 10 comps by calling report_comps exactly once when done; mark price_basis 'closed' only when the page states a sold price, "
            "'listed' when you only saw an asking price, 'estimated' when a site estimate is all there is. Include the page URL for each. "
            "Do not report the subject itself. Do not fabricate: a comp you cannot source is left out and mentioned in search_notes."
        ) % (addr, desc or "details unknown", cond, radius_mi, months)
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
    system = [{"type": "text", "text": (SYSTEM_AS_IS if _is_as_is(doc) else SYSTEM) + "\n\nYou are answering follow-up questions about ONE valuation file, given below. Answer from the file; "
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


# ------------------------------------------------------------- shortlist
_SHORTLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {"type": "array", "items": {"type": "object", "properties": {
            "comp_id": {"type": "string"}, "rank": {"type": "integer", "description": "1 = the single best comp."},
            "reason": {"type": "string", "description": "One sentence, with the number or fact that decided it."},
            "condition_guess": {"type": "string", "description": "C1-C6 if the evidence says so (a resale after a purchase at a third of the price is a renovation; an auction or estate sale is as-is). Omit if unknown."}},
            "required": ["comp_id", "rank", "reason"]}},
        "rejected": {"type": "array", "items": {"type": "object", "properties": {"comp_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["comp_id", "reason"]}},
        "notes": {"type": "string", "description": "Two or three sentences for the CEO: what kind of sales these are, what is missing, what would change the pick."},
    },
    "required": ["picks", "rejected", "notes"],
}


def shortlist(subject: Dict[str, Any], candidates: List[Dict[str, Any]], *, size: int, emit: Optional[Callable[[str], None]] = None,
              as_is: bool = False) -> Dict[str, Any]:
    """Opus 5.5 chooses ``size`` comps from the scored candidates. Rules decided
    who is eligible (distance, recency, size, type, arm's length); the model
    decides between them from the deed and MLS evidence: a renovated resale,
    an auction, an estate transfer, a shell. It cannot add an address or
    change a number. Writes ai_pick / ai_rank / ai_reason onto the comps."""
    if emit:
        emit("Opus 5.5 is choosing the %d best comparable sales from %d candidates…" % (size, len(candidates)))
    rows = []
    for c in candidates:
        rows.append({k: c.get(k) for k in ("comp_id", "source", "address", "distance_mi", "sale_price", "sale_date", "price_basis", "listing_price", "dom",
                                           "property_type", "beds", "baths", "sqft", "lot_sqft", "year_built", "basement", "condition", "arms_length", "note", "url", "similarity")}
                    | {"score_deductions": [d["reason"] for d in (c.get("deductions") or [])], "evidence": c.get("evidence")})
    subj = {k: subject.get(k) for k in ("address", "property_type", "beds", "baths", "sqft", "lot_sqft", "year_built", "condition_current", "condition_after", "basement", "stories", "known_issues", "planned_work")}
    user = ("Choose the %d comparable sales that best support an After Repair Value for this subject, from the candidates below. "
            "Every candidate already passed the distance, recency, size and type filters and carries a similarity score from the engine. "
            "Your job is judgement the engine cannot do: read each candidate's deed and MLS evidence and prefer closed, arm's-length, market sales of a house in the condition the subject will be in after the work "
            "(a resale months after a cheap purchase is a renovated comp; an auction, estate, bank or $10,000 transfer is not a market price; a vacant shell sold to an investor is an as-is comp). "
            "The grid values the subject AS REPAIRED, so renovated resales are the comps that matter; keep at most one as-is or shell sale, and only as a stated floor. "
            "Bracket the subject: at least one comp larger and one smaller, one newer and one older where possible. "
            "Pick exactly %d unless fewer are defensible; list every other candidate under rejected with one reason each. Copy comp_id values exactly."
            "\n\nSUBJECT (as repaired to %s):\n%s\n\nCANDIDATES:\n%s") % (
        size, size, subject.get("condition_after") or "C2", json.dumps(subj, default=str), json.dumps(rows, indent=1, default=str))
    if as_is:
        cond = subject.get("condition_after") or "C4"   # on an as-is subject this is the condition today
        subj_today = {k: v for k, v in subj.items() if k not in ("condition_after", "planned_work")}
        user = ("Choose the %d comparable sales that best support an AS-IS market value for this subject (what it would sell for today, as it stands, with no work done), from the candidates below. "
                "Every candidate already passed the distance, recency, size and type filters and carries a similarity score from the engine. "
                "Your job is judgement the engine cannot do: read each candidate's deed and MLS evidence and prefer closed, arm's-length, market sales of a house that sold in a condition like the subject's today "
                "(a cash sale to an investor or LLC at a price well under the renovated sales is an as-is comp; a resale months after a cheap purchase is a renovated comp; an auction, bank or $10,000 transfer is not a market price). "
                "The grid values the subject AS IT STANDS at condition %s, so sales in similar condition are the comps that matter; keep at most one renovated resale, and only as a stated ceiling. "
                "Bracket the subject: at least one comp larger and one smaller, one newer and one older where possible. "
                "Pick exactly %d unless fewer are defensible; list every other candidate under rejected with one reason each. Copy comp_id values exactly."
                "\n\nSUBJECT (as it stands today, condition %s):\n%s\n\nCANDIDATES:\n%s") % (
            size, cond, size, cond, json.dumps(subj_today, default=str), json.dumps(rows, indent=1, default=str))
    before = METER.snapshot()
    out = _structured(system=[{"type": "text", "text": SYSTEM_AS_IS if as_is else SYSTEM, "cache_control": {"type": "ephemeral"}}], user=user, tool_name="shortlist_comps",
                      schema=_SHORTLIST_SCHEMA, description="The chosen comparable sales with reasons, and the rejected ones with reasons.", max_tokens=8000)
    ids = {c["comp_id"]: c for c in candidates}
    picks = [p for p in (out.get("picks") or []) if p.get("comp_id") in ids][:size]
    for c in candidates:
        c["ai_pick"] = False
        c.pop("ai_rank", None)
        c["ai_reason"] = None
    for p in sorted(picks, key=lambda p: p.get("rank") or 99):
        c = ids[p["comp_id"]]
        c["ai_pick"] = True
        c["ai_rank"] = p.get("rank")
        c["ai_reason"] = p.get("reason")
        g = str(p.get("condition_guess") or "").upper()
        if g in ("C1", "C2", "C3", "C4", "C5", "C6") and not c.get("condition"):
            c["condition"] = g
            c["condition_source"] = "analyst"
    for r in out.get("rejected") or []:
        c = ids.get(r.get("comp_id"))
        if c is not None and not c.get("ai_pick"):
            c["ai_reason"] = r.get("reason")
    return {"picks": [{"comp_id": p["comp_id"], "rank": p.get("rank"), "reason": p.get("reason"), "address": ids[p["comp_id"]].get("address")} for p in picks],
            "rejected": [{"comp_id": r.get("comp_id"), "reason": r.get("reason"), "address": (ids.get(r.get("comp_id")) or {}).get("address")} for r in (out.get("rejected") or []) if r.get("comp_id") in ids],
            "notes": out.get("notes"), "model": cfg.ARV_MODEL, "effort": cfg.ARV_EFFORT, "size": size, "candidates": len(candidates),
            "usage": METER.summarize(METER.diff(before, METER.snapshot()))}
