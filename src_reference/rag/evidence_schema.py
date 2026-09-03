"""
Evidentiary metadata spine (Phase 3 — Sprint 0, shape only).

This module defines the corpus / privilege / custody / evidentiary-class
vocabulary and a per-document field template that every ingested document
(emails, attachments, title reports, insurance, deeds, LLC docs, court
filings, bank records, …) will carry. Nothing imports this yet — it is the
schema foundation the Sprint-1/2 ingestion pipelines will fill in.

Why this exists (legal, not just technical):
  • We hold TWO corpora that must never be confused —
      - the lawyer correspondence (attorney-client PRIVILEGED + work product), and
      - the David / adverse-party communications (party admissions, fully usable).
  • Every document needs chain-of-custody (FRE 901/902) and an evidentiary
    weight hint so the agent can reason about admissions vs records vs drafts.

These are plain string constants (not Enums) so they serialize cleanly into
MongoDB and JSON without conversion, matching the rest of the codebase.
"""
from __future__ import annotations

from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Corpus — which body of evidence a document belongs to
# ---------------------------------------------------------------------------
CORPUS_LEGAL_CORRESPONDENCE = "legal_correspondence"   # our emails w/ attorneys
CORPUS_FRAUD_COMMUNICATIONS = "fraud_communications"   # David & team (AA_Fund)
CORPUS_PROPERTY_RECORDS     = "property_records"        # title/deed/mortgage/lien
CORPUS_INSURANCE_RECORDS    = "insurance_records"       # binders/policies/claims
CORPUS_CORPORATE_RECORDS    = "corporate_records"       # LLC formation/operating
CORPUS_COURT_RECORDS        = "court_records"           # DA filings/orders/judgments
CORPUS_FINANCIAL_RECORDS    = "financial_records"       # bank/wire/equity/tax

CORPORA: List[str] = [
    CORPUS_LEGAL_CORRESPONDENCE,
    CORPUS_FRAUD_COMMUNICATIONS,
    CORPUS_PROPERTY_RECORDS,
    CORPUS_INSURANCE_RECORDS,
    CORPUS_CORPORATE_RECORDS,
    CORPUS_COURT_RECORDS,
    CORPUS_FINANCIAL_RECORDS,
]


# ---------------------------------------------------------------------------
# Privilege status — drives the Clean-mode retrieval guard (Sprint 5)
# ---------------------------------------------------------------------------
PRIVILEGE_PRIVILEGED   = "privileged"      # attorney-client / work product
PRIVILEGE_ADVERSE_PARTY = "adverse_party"  # David & team — admissions
PRIVILEGE_THIRD_PARTY  = "third_party"     # banks, title cos, insurers
PRIVILEGE_PUBLIC_RECORD = "public_record"  # recorded deeds, court/LLC filings
PRIVILEGE_NOT_PRIVILEGED = "not_privileged"

PRIVILEGE_STATUSES: List[str] = [
    PRIVILEGE_PRIVILEGED,
    PRIVILEGE_ADVERSE_PARTY,
    PRIVILEGE_THIRD_PARTY,
    PRIVILEGE_PUBLIC_RECORD,
    PRIVILEGE_NOT_PRIVILEGED,
]

# Privilege statuses that Clean mode must EXCLUDE from retrieval so a
# shareable/trustee/expert-facing answer can never contain privileged text.
CLEAN_MODE_EXCLUDED_PRIVILEGE: List[str] = [PRIVILEGE_PRIVILEGED]


# ---------------------------------------------------------------------------
# Evidentiary class — weight hint for the agent + authority ranking
# ---------------------------------------------------------------------------
EVID_PARTY_ADMISSION          = "party_admission"            # David's own words
EVID_RECORDED_INSTRUMENT      = "recorded_instrument"        # deed/mortgage/lien
EVID_THIRD_PARTY_BUSINESS_REC = "third_party_business_record"  # bank/insurer/title
EVID_COURT_RECORD             = "court_record"               # filings/orders
EVID_PRIVILEGED_WORK_PRODUCT  = "privileged_work_product"    # our counsel
EVID_CORRESPONDENCE           = "correspondence"             # generic

EVIDENTIARY_CLASSES: List[str] = [
    EVID_PARTY_ADMISSION,
    EVID_RECORDED_INSTRUMENT,
    EVID_THIRD_PARTY_BUSINESS_REC,
    EVID_COURT_RECORD,
    EVID_PRIVILEGED_WORK_PRODUCT,
    EVID_CORRESPONDENCE,
]


# Default corpus → privilege / evidentiary-class mapping (a safe starting
# point at ingestion; the classifier/human can override per document).
CORPUS_DEFAULTS: Dict[str, Dict[str, str]] = {
    CORPUS_LEGAL_CORRESPONDENCE: {
        "privilege_status": PRIVILEGE_PRIVILEGED,
        "evidentiary_class": EVID_PRIVILEGED_WORK_PRODUCT,
    },
    CORPUS_FRAUD_COMMUNICATIONS: {
        "privilege_status": PRIVILEGE_ADVERSE_PARTY,
        "evidentiary_class": EVID_PARTY_ADMISSION,
    },
    CORPUS_PROPERTY_RECORDS: {
        "privilege_status": PRIVILEGE_PUBLIC_RECORD,
        "evidentiary_class": EVID_RECORDED_INSTRUMENT,
    },
    CORPUS_INSURANCE_RECORDS: {
        "privilege_status": PRIVILEGE_THIRD_PARTY,
        "evidentiary_class": EVID_THIRD_PARTY_BUSINESS_REC,
    },
    CORPUS_CORPORATE_RECORDS: {
        "privilege_status": PRIVILEGE_PUBLIC_RECORD,
        "evidentiary_class": EVID_RECORDED_INSTRUMENT,
    },
    CORPUS_COURT_RECORDS: {
        "privilege_status": PRIVILEGE_PUBLIC_RECORD,
        "evidentiary_class": EVID_COURT_RECORD,
    },
    CORPUS_FINANCIAL_RECORDS: {
        "privilege_status": PRIVILEGE_THIRD_PARTY,
        "evidentiary_class": EVID_THIRD_PARTY_BUSINESS_REC,
    },
}

# Single matter for now (the David fraud investigation). Extension point for
# multi-matter later.
DEFAULT_MATTER_ID = "matter_001"


def evidentiary_fields(
    *,
    corpus: str,
    source_file: str,
    sha256: str,
    ingest_run_id: str,
    custodian: str = "",
    matter_id: str = DEFAULT_MATTER_ID,
    privilege_status: str | None = None,
    evidentiary_class: str | None = None,
    bates_start: str | None = None,
    bates_end: str | None = None,
) -> Dict[str, Any]:
    """Build the evidentiary metadata block stamped onto a document/chunk.

    `privilege_status` / `evidentiary_class` default from the corpus mapping
    unless explicitly overridden (e.g. a lawyer email forwarded to a third
    party that is no longer privileged).
    """
    defaults = CORPUS_DEFAULTS.get(corpus, {})
    return {
        "matter_id": matter_id,
        "corpus": corpus,
        "privilege_status": privilege_status or defaults.get(
            "privilege_status", PRIVILEGE_NOT_PRIVILEGED
        ),
        "evidentiary_class": evidentiary_class or defaults.get(
            "evidentiary_class", EVID_CORRESPONDENCE
        ),
        "custody": {
            "custodian": custodian,
            "source_file": source_file,
            "sha256": sha256,
            "ingest_run_id": ingest_run_id,
            # ingested_at is set by the writer at insert time.
        },
        "bates_range": (
            {"start": bates_start, "end": bates_end}
            if (bates_start or bates_end)
            else None
        ),
    }


# Source-type → corpus map (covers the doc_source_type / source_type values
# the ingestion + extraction pipelines stamp). Used as a fallback when a chunk
# has no explicit `corpus` field.
_SOURCE_TYPE_CORPUS: Dict[str, str] = {
    "title_report": CORPUS_PROPERTY_RECORDS, "deed": CORPUS_PROPERTY_RECORDS,
    "mortgage": CORPUS_PROPERTY_RECORDS, "lien": CORPUS_PROPERTY_RECORDS,
    "satisfaction": CORPUS_PROPERTY_RECORDS, "lis_pendens": CORPUS_PROPERTY_RECORDS,
    "closing_statement": CORPUS_PROPERTY_RECORDS,
    "insurance": CORPUS_INSURANCE_RECORDS, "binder": CORPUS_INSURANCE_RECORDS,
    "policy": CORPUS_INSURANCE_RECORDS, "claim": CORPUS_INSURANCE_RECORDS,
    "litigation": CORPUS_COURT_RECORDS, "litigation_update": CORPUS_COURT_RECORDS,
    "court_order": CORPUS_COURT_RECORDS, "judgment": CORPUS_COURT_RECORDS,
    "da_filing": CORPUS_COURT_RECORDS, "indictment": CORPUS_COURT_RECORDS,
    "equity_schedule": CORPUS_FINANCIAL_RECORDS, "bank_record": CORPUS_FINANCIAL_RECORDS,
    "wire_confirmation": CORPUS_FINANCIAL_RECORDS, "tax_record": CORPUS_FINANCIAL_RECORDS,
    "llc_formation": CORPUS_CORPORATE_RECORDS, "operating_agreement": CORPUS_CORPORATE_RECORDS,
    "certificate_of_good_standing": CORPUS_CORPORATE_RECORDS,
}

# Privilege-status → corpus map (privilege_status is stamped on every chunk in
# Sprint 2.3, so this is the most reliable signal when `corpus` is absent).
_PRIVILEGE_CORPUS: Dict[str, str] = {
    PRIVILEGE_PRIVILEGED: CORPUS_LEGAL_CORRESPONDENCE,
    PRIVILEGE_ADVERSE_PARTY: CORPUS_FRAUD_COMMUNICATIONS,
}


def corpus_for(chunk: Any) -> str:
    """Best-effort corpus label for a chunk (dict or RetrievedChunk).

    Priority: explicit `corpus` field → source-type mapping (most specific) →
    privilege-status mapping → "unknown". Source-type is preferred over
    privilege because an attachment that is a recorded deed should read as
    `property_records` even though its privilege posture is public_record.
    This is what removes the cosmetic `corpus: unknown` footer on agent-path
    answers without needing a data migration.
    """
    get = (chunk.get if isinstance(chunk, dict)
           else lambda k, d=None: getattr(chunk, k, d))
    explicit = get("corpus")
    if explicit:
        return explicit
    st = (get("doc_source_type") or get("source_type") or "").lower()
    if st in _SOURCE_TYPE_CORPUS:
        return _SOURCE_TYPE_CORPUS[st]
    priv = (get("privilege_status") or "").lower()
    if priv in _PRIVILEGE_CORPUS:
        return _PRIVILEGE_CORPUS[priv]
    # bare email chunks with no privilege signal: correspondence, side unknown
    if st in ("email_body", "email", "correspondence"):
        return CORPUS_FRAUD_COMMUNICATIONS if priv == PRIVILEGE_ADVERSE_PARTY \
            else "correspondence"
    return "unknown"


def ensure_evidence_indexes(collection) -> None:
    """Create idempotent indexes for the evidentiary spine on a chunks/docs
    collection. Safe to call repeatedly; tolerates pre-existing specs.

    Called by the Sprint-1/2 ingestion writers once data starts flowing.
    """
    from pymongo import ASCENDING

    specs = [
        ([("matter_id", ASCENDING)], "ix_matter"),
        ([("corpus", ASCENDING)], "ix_corpus"),
        ([("privilege_status", ASCENDING)], "ix_privilege_status"),
        ([("evidentiary_class", ASCENDING)], "ix_evidentiary_class"),
        ([("custody.sha256", ASCENDING)], "ix_custody_sha256"),
        ([("matter_id", ASCENDING), ("corpus", ASCENDING)], "ix_matter_corpus"),
    ]
    for keys, name in specs:
        try:
            collection.create_index(keys, name=name)
        except Exception:  # noqa: BLE001 — tolerate pre-existing/conflicting specs
            pass
