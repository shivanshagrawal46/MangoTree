"""The unit of retrieval — one chunk, with everything a later stage needs to know.

A hit carries its provenance (which channels found it, at what rank), its scope
label (property file, portfolio-common, unplaced), and the metadata that
rescoring and the reranker read. Stages mutate scores in place; the chunk_id is
the identity throughout, which is what lets the agent's scratchpad say "[#7] is
the same chunk it was ten turns ago".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Fields pulled from a chunk document for a hit. ``embedding`` is deliberately
#: absent — 1024 floats per candidate would dominate the wire for nothing.
HIT_PROJECTION = {
    "_id": 0,
    "chunk_id": 1, "artifact_sha": 1, "ordinal": 1,
    "text": 1, "context": 1, "header": 1, "tier1": 1, "tier2": 1,
    "display_name": 1, "source_ref": 1, "attribution": 1, "filename": 1,
    "property_ids": 1, "scope": 1, "common_kind": 1, "common_topics": 1, "placement": 1,
    "source_type": 1, "doc_class": 1, "privileged": 1,
    "date": 1, "date_ym": 1, "latest_date": 1, "from_email": 1, "extension": 1,
    "folder_path": 1, "parent_email_shas": 1, "entity_ids": 1, "token_count": 1,
}


@dataclass
class Hit:
    chunk_id: str
    artifact_sha: str
    text: str = ""
    context: str = ""
    header: str = ""
    ordinal: int = 0
    display_name: str = ""
    source_ref: str = ""
    attribution: str = ""
    filename: str = ""
    property_ids: List[str] = field(default_factory=list)
    scope: str = ""
    placement: str = ""                 # property | portfolio | unplaced | business
    common_kind: Optional[str] = None
    common_topics: List[str] = field(default_factory=list)
    source_type: str = ""
    doc_class: Optional[str] = None
    privileged: bool = False
    date: Any = None
    date_ym: Optional[str] = None
    from_email: Optional[str] = None
    extension: Optional[str] = None
    parent_email_shas: List[str] = field(default_factory=list)
    entity_ids: List[str] = field(default_factory=list)
    token_count: int = 0

    # --- retrieval state -----------------------------------------------------
    #: channel name -> rank (1-based) in that channel's list
    channel_ranks: Dict[str, int] = field(default_factory=dict)
    fused_score: float = 0.0
    rescored: float = 0.0
    rerank1_score: Optional[float] = None
    rerank2_score: Optional[float] = None
    rerank2_reason: str = ""
    #: how this chunk entered the final set: retrieved | neighbor | parent | thread | fulldoc
    origin: str = "retrieved"
    #: label the reranker and answer model see beside the passage
    label: str = ""

    # ---------------------------------------------------------------------
    @classmethod
    def from_doc(cls, doc: dict) -> "Hit":
        return cls(
            chunk_id=doc["chunk_id"],
            artifact_sha=doc.get("artifact_sha", ""),
            text=doc.get("text") or "",
            context=doc.get("context") or "",
            header=doc.get("header") or "",
            ordinal=int(doc.get("ordinal") or 0),
            display_name=doc.get("display_name") or "",
            source_ref=doc.get("source_ref") or "",
            attribution=doc.get("attribution") or "",
            filename=doc.get("filename") or "",
            property_ids=list(doc.get("property_ids") or []),
            scope=doc.get("scope") or "",
            placement=doc.get("placement") or "",
            common_kind=doc.get("common_kind"),
            common_topics=list(doc.get("common_topics") or []),
            source_type=doc.get("source_type") or "",
            doc_class=doc.get("doc_class"),
            privileged=bool(doc.get("privileged")),
            date=doc.get("date"),
            date_ym=doc.get("date_ym"),
            from_email=doc.get("from_email"),
            extension=doc.get("extension"),
            parent_email_shas=list(doc.get("parent_email_shas") or []),
            entity_ids=list(doc.get("entity_ids") or []),
            token_count=int(doc.get("token_count") or 0),
        )

    # ---------------------------------------------------------------------
    @property
    def citation(self) -> str:
        parts = [self.display_name or self.filename or self.artifact_sha[:12]]
        if self.source_ref and self.source_ref not in {"document", "email body"}:
            parts.append(self.source_ref)
        return " — ".join(parts)

    @property
    def date_str(self) -> str:
        try:
            return self.date.strftime("%Y-%m-%d") if self.date else ""
        except Exception:
            return str(self.date or "")

    def passage_header(self) -> str:
        """One line the reranker and answer model read before the text."""
        bits = []
        if self.property_ids:
            bits.append("property: " + ", ".join(self.property_ids))
        if self.label:
            bits.append(self.label)
        if self.doc_class:
            bits.append(f"type: {self.doc_class}")
        if self.common_topics:
            bits.append("topics: " + ", ".join(self.common_topics))
        if self.date_str:
            bits.append(f"date: {self.date_str}")
        if self.from_email:
            bits.append(f"from: {self.from_email}")
        bits.append(f"source: {self.citation}")
        return " | ".join(bits)

    def passage(self, *, max_chars: Optional[int] = None) -> str:
        body = f"{self.context}\n\n{self.text}".strip() if self.context else self.text
        if max_chars and len(body) > max_chars:
            body = body[:max_chars] + " …"
        return f"[{self.passage_header()}]\n{body}"

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "artifact_sha": self.artifact_sha,
            "ordinal": self.ordinal,
            "citation": self.citation,
            "display_name": self.display_name,
            "property_ids": self.property_ids,
            "placement": self.placement,
            "label": self.label,
            "doc_class": self.doc_class,
            "date": self.date_str,
            "from_email": self.from_email,
            "channel_ranks": self.channel_ranks,
            "fused_score": round(self.fused_score, 5),
            "rescored": round(self.rescored, 5),
            "rerank1_score": self.rerank1_score,
            "rerank2_score": self.rerank2_score,
            "rerank2_reason": self.rerank2_reason,
            "origin": self.origin,
            "text": self.text,
            "context": self.context,
        }
