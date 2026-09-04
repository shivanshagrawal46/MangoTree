"""Tier 2 — document-in-deal context. Templated, not generated.

Per docs/03-CONTEXT-AND-MEMORY.md this tier is built from the ledger and the
graph, not by a model, and that is the right call: every fact in it is already
structured. Asking a model to restate the property address and document class
would add cost, latency and a hallucination surface to information we hold
exactly.

It carries the **deal structure** (old deal vs new deal), because the same
document means different things under the two: an interest-reserve draw that is
routine in one structure can be unauthorised in the other, and an analysis that
cannot see which structure applies will read a breach as business as usual.

``tier2_version`` is stamped on every chunk. When a deal's shape changes, chunks
whose Tier 2 predates the change are detectable and refreshable without
re-reading or re-summarising anything — Tier 2 is cheap to rebuild, Tier 1 is
not.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Sequence

from mangotree.config.registry import PROPERTY_INDEX

#: Bump when the template changes so old and new Tier 2 lines never mix silently.
TIER2_VERSION = "tier2-v1"

_DEAL_LABEL = {
    "old_deal": "old deal structure",
    "new_deal": "new deal structure",
}

#: Plain-English role of each document class within a deal. Retrieval questions
#: are asked in these words ("what did the appraisal say", "is there a payoff
#: statement"), not in our internal class names, so the phrasing matters.
_ROLE = {
    "deed_of_trust": "the security instrument recording RKB's lien",
    "assignment_allonge": "the assignment transferring the note and lien to RKB",
    "guaranty": "the personal guaranty backing the loan",
    "draw_schedule": "the schedule governing how construction funds are released",
    "budget": "the renovation budget for the project",
    "estimate": "a contractor estimate of scope and cost",
    "change_order": "a change to the agreed scope or price",
    "inspection_report": "a third-party inspection of work completed",
    "construction_status": "a status report on construction progress",
    "daily_log": "a dated site log of work performed",
    "closing_instructions": "instructions governing disbursement at closing",
    "closing_letter": "the closing summary from the title company",
    "contract": "the governing agreement between the parties",
    "extension": "an extension or modification of the loan term",
    "legal_demand": "a demand or default notice from counsel",
    "hold_harmless": "a hold-harmless or indemnity undertaking",
    "consent": "a consent given by a party to the transaction",
    "accounting": "an accounting record of money moved",
    "investor_package": "a package prepared to present the deal",
    "cma": "a comparative market analysis of value",
    "title_report": "the title search disclosing liens and encumbrances",
    "title_policy": "the title insurance policy issued to RKB as lender",
    "payoff": "a statement of the balance required to retire the loan",
}


def _fmt_date(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if value:
        return str(value)[:10]
    return ""


def build_tier2(
    *,
    property_ids: Sequence[str],
    doc_class: Optional[str] = None,
    display_name: Optional[str] = None,
    date: object = None,
    source_ref: Optional[str] = None,
    inventory: Optional[Dict[str, int]] = None,
    ordinal: Optional[int] = None,
) -> str:
    """One sentence placing this document inside its deal.

    ``inventory`` is the coverage denominator — how many documents of this class
    exist for this property. It is what lets a later answer say "invoice 3 of 14"
    and, more importantly, lets the model notice that something is *missing*
    rather than only reporting what it was handed.
    """
    props: List[str] = []
    deals = set()
    for pid in property_ids or ():
        prop = PROPERTY_INDEX.get(pid)
        if prop:
            props.append(prop.canonical_address)
            deals.add(prop.deal_type)
        else:
            props.append(pid)

    parts: List[str] = []

    role = _ROLE.get((doc_class or "").lower())
    if role:
        parts.append(f"This document is {role}")
    elif doc_class and doc_class not in {"pdf", "document", "image", "text", "archive"}:
        parts.append(f"This document is classified as {doc_class.replace('_', ' ')}")
    elif display_name:
        parts.append(f"This is the document \"{display_name}\"")
    else:
        parts.append("This document")

    if props:
        if len(props) == 1:
            parts.append(f"for the property at {props[0]}")
        else:
            parts.append(f"covering {', '.join(props)}")

    if len(deals) == 1:
        label = _DEAL_LABEL.get(next(iter(deals)))
        if label:
            parts.append(f"which follows the {label}")

    when = _fmt_date(date)
    if when:
        parts.append(f"dated {when}")

    sentence = " ".join(parts).strip()
    if not sentence.endswith("."):
        sentence += "."

    extras: List[str] = []
    if inventory and doc_class:
        total = inventory.get(doc_class)
        if total and ordinal:
            extras.append(
                f"It is document {ordinal} of {total} of this type held for this property."
            )
        elif total:
            extras.append(f"{total} documents of this type are held for this property.")
    if source_ref:
        extras.append(f"Source: {source_ref}.")

    return " ".join([sentence] + extras)


def build_embedded_context(tier1: str, tier2: str) -> str:
    """The context block that is embedded with the chunk.

    Ordering is deliberate: Tier 1 first because it is the specific, discriminating
    sentence, Tier 2 after as the stable frame. Both precede the raw chunk text,
    and the raw text is always stored separately so a quote can still be verified
    byte-for-byte against the source.
    """
    blocks = [b.strip() for b in (tier1, tier2) if b and b.strip()]
    return "\n".join(blocks)
