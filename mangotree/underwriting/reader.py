"""Reading an uploaded paper against its item's checks.

One Opus 5.5 call per upload. The model is given the paper, the facts of the
file and the item's checks, and answers each check: met, not met, or not shown —
with the words from the paper that decided it. It also reports the figures the
paper states (a payoff amount, a balance, an insured amount) so the file's
numbers can be set from the paper rather than typed.

The model judges nothing about the deal. A check that is not met sends the item
back with the reason in the paper's own words; the three tests are arithmetic
elsewhere.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

MET, NOT_MET, NOT_SHOWN = "met", "not_met", "not_shown"

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "what_it_is": {"type": "string", "description": "What this paper is, in at most 12 words."},
        "right_paper": {"type": "boolean", "description": "False if this is not the paper the item asks for."},
        "checks": {"type": "array", "description": "One entry per check, in the order given.", "items": {"type": "object", "properties": {
            "n": {"type": "integer", "description": "The check's number as given."},
            "result": {"type": "string", "enum": [MET, NOT_MET, NOT_SHOWN]},
            "quote": {"type": "string", "description": "The exact words or figure from the paper that decide it. Empty when not shown."},
            "note": {"type": "string", "description": "At most 25 words: what is wrong and what would fix it. Empty when met."}},
            "required": ["n", "result", "quote", "note"]}},
        "figures": {"type": "array", "description": "Dollar amounts, dates and names the paper states that a lender's file needs.", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "value": {"type": "string"}}, "required": ["label", "value"]}},
        "also_noticed": {"type": "array", "description": "At most three things in the paper a lender must know that no check asked about (a second mortgage, a judgment, a different owner's name).",
                         "items": {"type": "string"}},
        "legibility": {"type": "string", "enum": ["clear", "partial", "poor"]},
    },
    "required": ["what_it_is", "right_paper", "checks", "figures", "also_noticed", "legibility"],
}

SYSTEM = """You check closing papers for RKB Consulting Group, Inc., a lender that funds house renovations and is repaid when the house sells. If a house does not sell, RKB forecloses, so every paper must be exactly right before closing.

You are given one paper, the facts of the loan file, and a numbered list of checks for that paper. For each check answer met, not_met or not_shown.
- met: the paper itself shows it. Quote the words or the figure.
- not_met: the paper shows something different. Quote what it shows and say in a few plain words what must change.
- not_shown: the paper does not say. Do not guess, and do not treat silence as met.
Names must match exactly: "RKB Consulting Group, Inc." is the lender; "RKB Consulting LLC", "RKB Consulting", "RKB Consultants Inc." are not met. An address with a different street suffix, unit or zip is not met.
Never copy a Social Security number, a bank account number or a full card number into your answer. If the paper shows one, say only that it does.
Report by calling the read_paper tool exactly once."""

#: Digit runs that could be an identity or account number are removed from whatever the model returns.
import re as _re
_SSN = _re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")
_LONG = _re.compile(r"\b\d{9,}\b")


def scrub(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    return _LONG.sub("[number removed]", _SSN.sub("[number removed]", text))


def _facts_block(facts: Dict[str, Any], principal: Optional[float]) -> str:
    lines = ["Property: %s" % (facts.get("address") or "not entered"),
             "State: %s" % (facts.get("state") or "not entered"),
             "Owner (%s): %s" % (facts.get("owner_kind") or "kind not entered", facts.get("owner_name") or "name not entered")]
    if principal:
        lines.append("Note amount: $%s" % format(round(principal, 2), ",.2f"))
    if facts.get("closing_date"):
        lines.append("Closing date: %s. The note matures one year later, less a day." % facts["closing_date"])
    else:
        lines.append("Closing date: not set yet. Where a check compares a date with the closing or the maturity date, assume closing within 30 days of today and maturity twelve months after it.")
    if facts.get("completion_date"):
        lines.append("Work complete by: %s" % facts["completion_date"])
    for k, label in (("builder_cost", "Builder's cost"), ("contingency", "Contingency"), ("arv", "After-repair value"), ("sqft_above", "Square feet above grade"), ("sqft_below", "Square feet below grade")):
        if facts.get(k) not in (None, ""):
            lines.append("%s: %s" % (label, facts[k]))
    for l in facts.get("liens") or []:
        lines.append("Mortgage on the house: %s, balance %s, %s" % (l.get("holder") or "lender not named", l.get("balance"), "to be paid off at closing" if l.get("payoff_at_closing") else "stays"))
    return "\n".join(lines)


def read(*, item: Dict[str, Any], facts: Dict[str, Any], principal: Optional[float], filename: str, text: str, images: List[bytes]) -> Dict[str, Any]:
    """Returns {what_it_is, right_paper, checks:[{n, check, result, quote, note}], figures, also_noticed, legibility, passed, usage}."""
    from mangotree.arv.analyst import _structured
    from mangotree.core.usage import METER
    checks = list(item.get("checks") or [])
    if not text.strip() and not images:
        raise RuntimeError("nothing readable in this file")
    blocks: List[Dict[str, Any]] = [{"type": "text", "text": "THE FILE\n%s\n\nTHE ITEM\n%s\nWhy RKB needs it: %s\n\nCHECKS\n%s\n\nPAPER: %s" % (
        _facts_block(facts, principal), item.get("label"), item.get("why"), "\n".join("%d. %s" % (i, c) for i, c in enumerate(checks, start=1)) or "(none: say what the paper is)", filename)}]
    if text.strip():
        blocks.append({"type": "text", "text": "TEXT OF THE PAPER:\n" + text})
    for i, img in enumerate(images, start=1):
        blocks.append({"type": "text", "text": "[page image %d of %d]" % (i, len(images))})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(img).decode("ascii")}})
    before = METER.snapshot()
    raw = _structured(system=[{"type": "text", "text": SYSTEM}], user=blocks, tool_name="read_paper", schema=SCHEMA,
                      description="The check-by-check reading of one closing paper.", max_tokens=12000)
    usage = METER.summarize(METER.diff(before, METER.snapshot()))
    return tidy(raw, checks, usage=usage)


def tidy(raw: Dict[str, Any], checks: List[str], *, usage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Line the model's answers up with the checks asked. A check the model skipped is
    'not shown': silence never passes."""
    by_n = {}
    for c in raw.get("checks") or []:
        try:
            by_n[int(c.get("n"))] = c
        except (TypeError, ValueError):
            continue
    rows = []
    for i, check in enumerate(checks, start=1):
        c = by_n.get(i) or {}
        result = c.get("result") if c.get("result") in (MET, NOT_MET, NOT_SHOWN) else NOT_SHOWN
        rows.append({"n": i, "check": check, "result": result, "quote": scrub(c.get("quote") or ""), "note": scrub(c.get("note") or "")})
    right = bool(raw.get("right_paper", True))
    passed = right and all(r["result"] == MET for r in rows)
    out = {"what_it_is": scrub(raw.get("what_it_is") or ""), "right_paper": right, "checks": rows,
           "figures": [{"label": scrub(f.get("label") or ""), "value": scrub(str(f.get("value") or ""))} for f in (raw.get("figures") or []) if f][:20],
           "also_noticed": [scrub(x) for x in (raw.get("also_noticed") or []) if x][:3],
           "legibility": raw.get("legibility") or "clear", "passed": passed}
    if usage is not None:
        out["usage"] = usage
        out["cost_usd"] = round(float(usage.get("total_cost_usd") or 0), 4)
    return out


def reason_for(reading: Dict[str, Any]) -> str:
    """The one sentence shown when an upload is sent back."""
    if not reading.get("right_paper", True):
        return "This is not the paper asked for: it is %s." % (reading.get("what_it_is") or "something else")
    bad = [r for r in reading.get("checks") or [] if r["result"] != MET]
    parts = []
    for r in bad[:4]:
        parts.append("%s: %s" % (r["check"], r["note"] or ("the paper does not show this" if r["result"] == NOT_SHOWN else "not as required")))
    more = len(bad) - len(parts)
    return "; ".join(parts) + ("; and %d more" % more if more > 0 else "")


def extract(payload: bytes, filename: str) -> Dict[str, Any]:
    """Text and page images of an upload, through the same extractor the ARV documents use."""
    from mangotree.arv import config as arv_cfg, documents as arv_docs
    suffix = Path(filename or "").suffix.lower()
    work = arv_cfg.CACHE_DIR / "uw"
    work.mkdir(parents=True, exist_ok=True)
    tmp = work / (arv_docs.sha256(payload) + suffix)
    tmp.write_bytes(payload)
    try:
        return arv_docs.extract(tmp)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
