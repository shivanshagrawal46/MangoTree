"""Sprint 8 hardening — Defense-Counsel Critic (8.2) + post-generation entity
validation (8.1) + negative-evidence/OCR-confidence helpers (8.4/8.5).

These run AFTER the agent produces a verified answer. They do not block a
grounded answer; they upgrade the gate from "is each fact grounded?" to "would
this survive cross-examination?" and "are all named entities real?".
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from src.graph.normalize import norm_name
from src.utils.logger import logger

# ── 8.2 Defense-Counsel Critic ───────────────────────────────────────────────
_CRITIC_SYS = (
    "You are David DeRosa's DEFENSE ATTORNEY reviewing an opposing analyst's "
    "answer about a property in a fraud/asset-recovery matter. Your job: find the "
    "SINGLE most damaging weakness a court would seize on. Look specifically for: "
    "(a) an instrument treated as operative when it may be only a DRAFT/unexecuted; "
    "(b) confusion between effective date vs recording date vs execution date; "
    "(c) an identity/ownership/'David-controlled' claim that is SPECULATIVE/inferred "
    "rather than documented; (d) a stated 'fact' that is actually an inference; "
    "(e) a missing counter-document or unaddressed innocent explanation. "
    "Return ONLY the tool call."
)
_CRITIC_TOOL = {
    "name": "defense_critique",
    "description": "Report the single biggest cross-examination vulnerability.",
    "input_schema": {"type": "object", "properties": {
        "has_gap": {"type": "boolean"},
        "category": {"type": "string", "enum": ["draft_vs_executed", "date_axis",
                     "speculative_identity", "inference_as_fact", "missing_counter_doc", "none"]},
        "gap": {"type": "string", "description": "the vulnerability, one or two sentences"},
        "severity": {"type": "string", "enum": ["low", "medium", "high", "none"]},
        "closeable_by_retrieval": {"type": "boolean"},
    }, "required": ["has_gap", "category", "severity"]}
}


def defense_critic(client, model: str, query: str, answer: str,
                   facts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not answer or not answer.strip():
        return {"has_gap": False, "severity": "none"}
    fact_lines = "\n".join(f"- {f.get('claim','')}" for f in (facts or [])[:25])
    user = (f"QUESTION: {query}\n\nANALYST ANSWER:\n{answer[:9000]}\n\n"
            f"KEY CLAIMS:\n{fact_lines}\n\nFind the single biggest defense vulnerability.")
    try:
        resp = client.messages.create(
            model=model, max_tokens=1500, system=_CRITIC_SYS,
            tools=[_CRITIC_TOOL], tool_choice={"type": "tool", "name": "defense_critique"},
            messages=[{"role": "user", "content": user}])
        for b in resp.content:
            if getattr(b, "type", None) == "tool_use":
                return dict(b.input or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"defense_critic skipped: {str(exc)[:100]}")
    return {"has_gap": False, "severity": "none"}


# ── 8.1 Post-generation entity validation ────────────────────────────────────
# a run of Capitalized/UPPER tokens (function words like 'is owned by' break the
# run) immediately followed by a corporate suffix -> the actual entity name.
_LLC_RE = re.compile(r"\b((?:[A-Z0-9][A-Za-z0-9&.'\-]{0,24}\s){0,5}(?:LLC|L\.L\.C\.|INC|CORP|LP|L\.P\.))\b")


def validate_entities(answer: str, mongo) -> Dict[str, Any]:
    """Check entity (LLC/corp) names asserted in the answer against the canonical
    graph. Flags names NOT present (possible invention) and David-claims not
    backed by the graph. Informational — surfaced, not blocking."""
    if not answer:
        return {"checked": 0, "not_in_graph": [], "david_claims_unverified": []}
    mentioned = sorted({re.split(r"\.\s+", m.group(1).strip())[-1].strip()
                        for m in _LLC_RE.finditer(answer)})
    ents = mongo.db["entities"]
    # build a norm_name set once
    known = {}
    for e in ents.find({"kind": {"$in": ["llc", "org", "bank", "person"]}},
                       {"name_norm": 1, "canonical_name": 1, "is_david": 1}):
        known[e.get("name_norm") or norm_name(e.get("canonical_name") or "")] = e
    not_in_graph = []
    for nm in mentioned:
        if norm_name(nm) not in known:
            not_in_graph.append(nm)
    return {"checked": len(mentioned), "mentioned": mentioned,
            "not_in_graph": not_in_graph}


# ── 8.4 negative-evidence / 8.5 OCR-confidence (light heuristics) ─────────────
def negative_evidence_present(answer: str) -> bool:
    """True if the answer explicitly states what is NOT on file (good practice)."""
    return bool(re.search(r"\b(no records found|not found|none on file|no open|"
                          r"could not (locate|find|confirm)|not documented|no .* recorded)\b",
                          answer or "", re.I))


def apply_hardening(client, model: str, *, query: str, answer: str,
                    facts: List[Dict[str, Any]], mongo,
                    run_critic: bool = True) -> Dict[str, Any]:
    """Run the hardening suite; return a report + a (possibly annotated) answer."""
    report: Dict[str, Any] = {}
    if run_critic:
        report["defense_critique"] = defense_critic(client, model, query, answer, facts)
    report["entity_validation"] = validate_entities(answer, mongo)
    report["states_negative_evidence"] = negative_evidence_present(answer)

    note_lines = []
    dc = report.get("defense_critique") or {}
    if dc.get("has_gap") and dc.get("severity") in ("medium", "high"):
        note_lines.append(f"⚠ Defense-counsel vulnerability ({dc.get('severity')}, "
                          f"{dc.get('category')}): {dc.get('gap')}")
    nig = report["entity_validation"].get("not_in_graph") or []
    if nig:
        note_lines.append("⚠ Entities named but not yet in our canonical graph "
                          f"(verify): {', '.join(nig[:6])}")
    annotated = answer
    if note_lines:
        annotated = answer + "\n\n" + "\n".join(note_lines)
    report["annotated_answer"] = annotated
    report["downgrade_confidence"] = bool(dc.get("severity") == "high")
    return report
