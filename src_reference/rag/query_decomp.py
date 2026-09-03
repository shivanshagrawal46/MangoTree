"""Sprint 7 · 7.4 query decomposition + 7.5 sufficiency / self-reflection.

decompose_query(): split a compound question into focused sub-questions so each
is retrieved separately (Hebbia ISD style) — raises recall on multi-part asks
like "list David's LLCs, the properties they own, and the latest title status".
Deterministic first (clause/conjunction/enumeration split); the agent can also
call this as a tool and run search per sub-question.

sufficiency_prompt(): the self-reflection guard text the agent applies before
finalizing ("what would make this incomplete? have I checked every linked
source / entity / date?").
"""
from __future__ import annotations

import re
from typing import List

_SPLIT_HINTS = re.compile(
    r"\s+(?:and then|and also|as well as|along with|;|\band\b|,\s*(?:and\s+)?)\s+", re.I)
_QWORDS = ("what", "which", "who", "when", "where", "how", "list", "show", "find",
           "is", "are", "did", "does", "was", "were", "give")


def decompose_query(query: str, *, max_parts: int = 6) -> List[str]:
    """Best-effort deterministic decomposition. Returns [query] when it's already
    a single ask (so callers can always iterate the result)."""
    q = (query or "").strip()
    if not q:
        return []
    # explicit enumerations: "1) ... 2) ..." or "a. ... b. ..."
    enum = re.split(r"(?:^|\s)(?:\d+[\).]|[a-d][\).])\s+", q)
    enum = [p.strip() for p in enum if p.strip()]
    if len(enum) >= 2:
        return enum[:max_parts]
    # multiple question marks
    if q.count("?") >= 2:
        parts = [p.strip() + "?" for p in q.split("?") if p.strip()]
        if len(parts) >= 2:
            return parts[:max_parts]
    # conjunction split — only if it yields parts that look like sub-questions
    rough = [p.strip(" ,.") for p in _SPLIT_HINTS.split(q) if p.strip(" ,.")]
    if len(rough) >= 2:
        # keep parts of reasonable length; merge tiny fragments back
        parts = [p for p in rough if len(p.split()) >= 2]
        if len(parts) >= 2:
            return parts[:max_parts]
    return [q]


def is_compound(query: str) -> bool:
    return len(decompose_query(query)) > 1


_SUFFICIENCY = (
    "Before finalizing, self-check for COMPLETENESS (recall is sacred):\n"
    "1. Did I resolve EVERY entity named (property, person, LLC, amount, date)?\n"
    "2. Did I fan out to EVERY linked source type (David email, title, insurance, "
    "equity, deed, mortgage, lien, litigation) — not just the obvious one?\n"
    "3. For a multi-part question, did I answer EACH part with its own evidence?\n"
    "4. Is there a recorded fact (deed/lien/judgment/transfer) I haven't cited?\n"
    "5. If something is NOT in the corpus, did I say so explicitly (negative evidence)?\n"
    "If any answer is 'no', retrieve more before answering."
)


def sufficiency_prompt() -> str:
    return _SUFFICIENCY
