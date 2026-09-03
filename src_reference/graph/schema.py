"""Canonical graph vocabulary — sides, entity kinds, edge types, authority,
date kinds. Single source of truth so ingest, resolution, retrieval, and the
detectors all speak the same language.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# Entity "side" — who an entity is relative to our matter (user-locked).
# --------------------------------------------------------------------------
SIDE_OUR = "our_side"          # Mango Tree (Rakesh Sir's team) + our people/attorneys
SIDE_DAVID = "david_network"   # David + his shells/agents (IPA, Island Properties, address-coded LLCs)
SIDE_THIRD = "third_party"     # neutral third parties (GMR investor, title vendors, insurers, lenders)
SIDE_COVICTIM = "co_victim"    # fellow fraud victims like us (Brian Detmer / his entities)
SIDE_UNKNOWN = "unknown"
SIDES = {SIDE_OUR, SIDE_DAVID, SIDE_THIRD, SIDE_COVICTIM, SIDE_UNKNOWN}

# --------------------------------------------------------------------------
# Entity kinds
# --------------------------------------------------------------------------
KIND_PERSON = "person"
KIND_PROPERTY = "property"
KIND_LLC = "llc"
KIND_CASE = "case"
KIND_BANK = "bank"
KIND_ORG = "org"
ENTITY_KINDS = {KIND_PERSON, KIND_PROPERTY, KIND_LLC, KIND_CASE, KIND_BANK, KIND_ORG}

# --------------------------------------------------------------------------
# Relationship edge types (relationships/ collection). Every edge carries
# {type, src, dst, as_of?, until?, source_doc_id?, source_chunk_id?,
#  confidence?, updated_at}.
# --------------------------------------------------------------------------
REL_GRANTOR_OF = "GRANTOR_OF"
REL_GRANTEE_OF = "GRANTEE_OF"
REL_OWNS = "OWNS"
REL_MEMBER_OF = "MEMBER_OF"
REL_BORROWER_OF = "BORROWER_OF"
REL_HAS_LIEN = "HAS_LIEN"
REL_HAS_MORTGAGE = "HAS_MORTGAGE"
REL_HAS_INSURANCE = "HAS_INSURANCE"
REL_ABOUT_PROPERTY = "ABOUT_PROPERTY"
REL_REFERENCES = "REFERENCES"
REL_SATISFIES = "SATISFIES"
REL_ATTACHED_TO = "ATTACHED_TO"
REL_FILED_IN = "FILED_IN"
REL_SENT_EMAIL = "SENT_EMAIL"
REL_LITIGATION_ABOUT = "LITIGATION_ABOUT"
REL_LENT_TO = "LENT_TO"
EDGE_TYPES = {
    REL_GRANTOR_OF, REL_GRANTEE_OF, REL_OWNS, REL_MEMBER_OF, REL_BORROWER_OF,
    REL_HAS_LIEN, REL_HAS_MORTGAGE, REL_HAS_INSURANCE, REL_ABOUT_PROPERTY,
    REL_REFERENCES, REL_SATISFIES, REL_ATTACHED_TO, REL_FILED_IN, REL_SENT_EMAIL,
    REL_LITIGATION_ABOUT, REL_LENT_TO,
}

# --------------------------------------------------------------------------
# Authority scores (feed the reranker). §3.7 of the plan.
# --------------------------------------------------------------------------
AUTHORITY_SCORES = {
    "court_order": 1.25, "judgment": 1.25,
    "deed": 1.20, "mortgage": 1.20, "satisfaction": 1.20,
    "lien": 1.18, "lis_pendens": 1.18, "da_filing": 1.18, "indictment": 1.18,
    "title_report": 1.15, "closing_statement": 1.15,
    "insurance": 1.10, "binder": 1.10, "policy": 1.10, "claim": 1.10,
    "contract": 1.08, "operating_agreement": 1.08, "service_agreement": 1.08,
    "bank_record": 1.06, "wire_confirmation": 1.06, "tax_record": 1.06,
    "equity_schedule": 1.06,
    "llc_formation": 1.05, "certificate_of_good_standing": 1.05,
    "email_attachment": 1.00,
    "email_body": 0.95, "email": 0.95, "correspondence": 0.95,
    "litigation_update": 1.18,
    "draft": 0.85, "attorney_notes": 0.85,
}
DEFAULT_AUTHORITY = 1.00


def authority_for(source_type: str | None, instrument_subtype: str | None = None) -> float:
    if instrument_subtype and instrument_subtype in AUTHORITY_SCORES:
        return AUTHORITY_SCORES[instrument_subtype]
    return AUTHORITY_SCORES.get(source_type or "", DEFAULT_AUTHORITY)


# --------------------------------------------------------------------------
# Date kinds (multi-axis temporal model). Never conflate these.
# --------------------------------------------------------------------------
DATE_DOCUMENT = "document_date"
DATE_EFFECTIVE = "effective_date"
DATE_RECORDING = "recording_date"
DATE_FILING = "filing_date"
DATE_EXECUTION = "execution_date"
DATE_KINDS = {DATE_DOCUMENT, DATE_EFFECTIVE, DATE_RECORDING, DATE_FILING, DATE_EXECUTION}
