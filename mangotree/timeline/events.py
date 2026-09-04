"""Timeline event model and the deterministic document-level pass."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

#: Event categories. Deliberately lender-shaped: these are the things that move
#: RKB's exposure, not generic project milestones.
EVENT_TYPES = (
    "origination",       # loan made, note signed, deed of trust recorded
    "assignment",        # note/lien assigned in or out
    "funding",           # a draw or advance released
    "payment",           # money received from borrower
    "payoff",            # loan retired or payoff quoted
    "extension",         # term extended or loan modified
    "default",           # notice of default, NOI, demand letter
    "legal",             # suit filed, lis pendens, judgment, hearing
    "construction",      # work performed, inspection, permit, completion
    "listing_sale",      # listed, under contract, closed, appraisal
    "title",             # title commitment, policy, exception cleared
    "tax_insurance",     # tax bill/payment, insurance bound or lapsed
    "communication",     # a substantive commitment or instruction given
    "other",
)

#: How certain we are that the date is the date the event *occurred*, rather than
#: the date a document about it was produced. Keeping these apart matters: a
#: payoff statement dated July describing a February payment is two different
#: dates, and conflating them silently corrupts any interest calculation.
DATE_BASIS = ("stated_in_text", "document_date", "file_mtime", "inferred")


@dataclass
class TimelineEvent:
    property_id: str
    occurred_at: Optional[datetime]
    event_type: str
    title: str
    detail: str = ""
    amount: Optional[float] = None
    currency: str = "USD"
    #: Provenance — always populated. An event with no source cannot be trusted
    #: and must not be displayed as fact.
    source_sha: str = ""
    source_ref: str = ""
    source_name: str = ""
    quote: str = ""
    date_basis: str = "document_date"
    confidence: float = 1.0
    extracted_by: str = "deterministic"
    deal_type: Optional[str] = None
    needs_review: bool = False
    notes: str = ""

    def event_id(self) -> str:
        """Content-derived id, so re-running is idempotent rather than duplicating.

        Built from the property, source document, date and title — the tuple that
        makes an event *the same event*. A second run over unchanged input
        reproduces the identical id and upserts onto itself.
        """
        stamp = self.occurred_at.strftime("%Y-%m-%d") if self.occurred_at else "undated"
        raw = "|".join([
            self.property_id, self.source_sha, self.source_ref,
            stamp, self.event_type, self.title.strip().lower()[:120],
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id(),
            "property_id": self.property_id,
            "occurred_at": self.occurred_at,
            "event_type": self.event_type,
            "title": self.title.strip(),
            "detail": self.detail.strip(),
            "amount": self.amount,
            "currency": self.currency,
            "source_sha": self.source_sha,
            "source_ref": self.source_ref,
            "source_name": self.source_name,
            "quote": self.quote.strip()[:600],
            "date_basis": self.date_basis,
            "confidence": round(float(self.confidence), 3),
            "extracted_by": self.extracted_by,
            "deal_type": self.deal_type,
            "needs_review": bool(self.needs_review),
            "notes": self.notes.strip(),
            "updated_at": datetime.now(timezone.utc),
        }


#: doc_class -> (event_type, human phrasing). Only classes whose existence is
#: itself an event are listed; anything else falls through to a generic entry.
_DOC_EVENT = {
    "deed_of_trust": ("origination", "Deed of trust"),
    "assignment_allonge": ("assignment", "Assignment of deed of trust / allonge"),
    "guaranty": ("origination", "Personal guaranty executed"),
    "draw_schedule": ("funding", "Draw schedule"),
    "budget": ("construction", "Renovation budget"),
    "estimate": ("construction", "Contractor estimate"),
    "change_order": ("construction", "Change order"),
    "inspection_report": ("construction", "Inspection report"),
    "construction_status": ("construction", "Construction status report"),
    "daily_log": ("construction", "Site daily log"),
    "closing_instructions": ("origination", "Closing instructions"),
    "closing_letter": ("origination", "Closing letter / settlement statement"),
    "contract": ("origination", "Contract executed"),
    "extension": ("extension", "Loan extension / modification"),
    "legal_demand": ("default", "Legal demand / default notice"),
    "hold_harmless": ("legal", "Hold harmless / indemnity"),
    "consent": ("legal", "Consent given"),
    "accounting": ("payment", "Accounting record"),
    "investor_package": ("other", "Investor package prepared"),
    "cma": ("listing_sale", "Comparative market analysis"),
    "title_report": ("title", "Title report / search"),
    "title_policy": ("title", "Title insurance policy"),
    "payoff": ("payoff", "Payoff statement"),
}


def document_events(
    artifact: Dict[str, Any], *, deal_type_lookup=None
) -> List[TimelineEvent]:
    """One event per (artifact, property) — the document's own existence.

    This pass guarantees timeline completeness: because it is derived rather than
    extracted, a property's timeline can never silently omit a document we hold.
    The model pass adds richness on top; it never has to carry coverage.
    """
    property_ids = artifact.get("property_ids") or []
    if not property_ids:
        return []

    source_type = artifact.get("source_type")
    date = artifact.get("date")

    if source_type == "email":
        # An email's own event is that it was sent. Its date is when the thing
        # happened, not when a document about it was typed up, so it is a
        # stronger timeline anchor than a document date — and correspondence is
        # most of the record for a deal that never generated much paper.
        subject = (artifact.get("subject") or "").strip() or "(no subject)"
        senders = (artifact.get("participants") or {}).get("from") or []
        recipients = (artifact.get("participants") or {}).get("to") or []
        detail = f"Email from {', '.join(senders) or '?'}"
        if recipients:
            detail += f" to {', '.join(recipients[:3])}"
        event_type, phrasing = "communication", subject[:160]
        source_ref = "email"
        source_name = subject[:160]
        confidence_dated = 0.9
    else:
        doc_class = (artifact.get("doc_class") or "").lower()
        event_type, phrasing = _DOC_EVENT.get(
            doc_class, ("other", artifact.get("filename") or "Document")
        )
        detail = f"Document on file: {artifact.get('filename') or '(unnamed)'}"
        source_ref = artifact.get("relative_path") or "document"
        source_name = artifact.get("filename") or ""
        confidence_dated = 0.75

    basis = "document_date" if date else "inferred"

    out: List[TimelineEvent] = []
    for pid in property_ids:
        out.append(TimelineEvent(
            property_id=pid,
            occurred_at=date,
            event_type=event_type,
            title=phrasing,
            detail=detail,
            source_sha=artifact.get("sha256", ""),
            source_ref=source_ref,
            source_name=source_name,
            date_basis=basis,
            # A document date is when the paper was produced, which is usually
            # but not always when the thing happened. Flagged accordingly rather
            # than presented as certain.
            confidence=confidence_dated if date else 0.3,
            extracted_by="deterministic",
            deal_type=deal_type_lookup(pid) if deal_type_lookup else None,
            needs_review=not bool(date),
            notes="" if date else "no date on the artifact; position on the timeline unknown",
        ))
    return out
