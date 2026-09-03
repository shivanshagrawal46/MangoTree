"""Sprint 4 · findings ledger — the investigation's persistent memory.

Every detector output (contradiction, anachronism, voidable-transfer candidate)
and, later, every agent discovery is written here with its evidence chain,
confidence, and a human confirm/reject status. Confirmed findings are surfaced
on future queries touching the same entity.

Deterministic _id (type + sorted entities + key) so re-running detectors is
idempotent (a finding updates in place, never duplicates) and a human's
confirmed/rejected status is preserved across re-runs.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

COLLECTION = "findings"

# severities
SEV_CRITICAL = "critical"
SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_INFO = "info"

# statuses (human review)
ST_PENDING = "pending"
ST_CONFIRMED = "confirmed"
ST_REJECTED = "rejected"


@dataclass
class Evidence:
    doc_id: Optional[str] = None
    chunk_id: Optional[str] = None
    quote: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"doc_id": self.doc_id, "chunk_id": self.chunk_id,
                "quote": self.quote, "note": self.note}


@dataclass
class Finding:
    finding_type: str                       # contradiction | anachronism | voidable_transfer | omission
    title: str
    detail: str
    entity_ids: List[str] = field(default_factory=list)
    property_id: Optional[str] = None
    severity: str = SEV_HIGH
    confidence: float = 0.8
    evidence: List[Evidence] = field(default_factory=list)
    detector: str = ""
    key: str = ""                           # extra disambiguator for the _id

    def finding_id(self) -> str:
        basis = "|".join([self.finding_type, *sorted(self.entity_ids),
                          self.property_id or "", self.key])
        return "find_" + hashlib.sha1(basis.encode()).hexdigest()[:16]

    def to_doc(self, now: datetime) -> Dict[str, Any]:
        return {
            "_id": self.finding_id(),
            "finding_type": self.finding_type,
            "title": self.title, "detail": self.detail,
            "entity_ids": self.entity_ids, "property_id": self.property_id,
            "severity": self.severity, "confidence": self.confidence,
            "evidence": [e.to_dict() for e in self.evidence],
            "detector": self.detector, "updated_at": now,
        }


def upsert_finding(coll, f: Finding, now: Optional[datetime] = None) -> str:
    """Idempotent write that PRESERVES human review status across re-runs."""
    now = now or datetime.now(timezone.utc)
    doc = f.to_doc(now)
    fid = doc["_id"]
    existing = coll.find_one({"_id": fid}, {"status": 1})
    coll.update_one(
        {"_id": fid},
        {"$set": doc,
         "$setOnInsert": {"status": ST_PENDING, "created_at": now}},
        upsert=True,
    )
    # never overwrite a human decision
    if existing and existing.get("status") in (ST_CONFIRMED, ST_REJECTED):
        pass
    return fid


def ensure_indexes(coll) -> None:
    from pymongo import ASCENDING
    for keys, nm in [([("finding_type", ASCENDING)], "ix_type"),
                     ([("entity_ids", ASCENDING)], "ix_entities"),
                     ([("property_id", ASCENDING)], "ix_property"),
                     ([("severity", ASCENDING)], "ix_severity"),
                     ([("status", ASCENDING)], "ix_status")]:
        try:
            coll.create_index(keys, name=nm)
        except Exception:  # noqa: BLE001
            pass
