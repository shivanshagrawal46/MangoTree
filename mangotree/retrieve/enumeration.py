"""Enumeration — exhaustive questions bypass similarity.

"List every draw request on Varnum." "How many invoices did the title company
send in 2025?" "Is there any notice of default on Chita Ct?" Similarity search
answers these badly by construction: it returns the *most similar* twenty, and
the question wants *all* of them, or a count, or a confident "none".

So an enumeration is a structured query over artifacts — the scope filter plus
whatever the question fixed (type, sender, period, extension, topic) — and it
returns the complete set with its denominator: how many documents were in scope,
how many matched. A negative answer carries the same denominator ("no notice of
default in 312 documents of Chita Ct"), which is what turns "we found nothing"
into evidence of absence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from mangotree.retrieve.query_understanding import QueryUnderstanding
from mangotree.retrieve.scope import Scope
from mangotree.storage.mongo import Mongo

#: doc_class values and the words that select them in an enumeration.
_CLASS_WORDS = {
    "draw_request": ("draw", "draw request", "advance"),
    "invoice": ("invoice", "bill"),
    "payoff_statement": ("payoff",),
    "settlement_statement": ("settlement statement", "alta", "hud"),
    "deed_of_trust": ("deed of trust", "mortgage", "dot"),
    "assignment": ("assignment", "allonge"),
    "guaranty": ("guaranty", "guarantee"),
    "title_commitment": ("title commitment", "prelim"),
    "title_policy": ("title policy", "loan policy"),
    "inspection_report": ("inspection",),
    "appraisal": ("appraisal", "bpo"),
    "insurance": ("insurance", "coi"),
    "default_notice": ("notice of default", "default notice", "demand letter", "nod"),
    "wire": ("wire",),
    "extension": ("extension", "modification"),
    "loan_agreement": ("loan agreement", "lending agreement"),
}

ARTIFACT_PROJECTION = {
    "_id": 0, "sha256": 1, "filename": 1, "subject": 1, "source_type": 1, "doc_class": 1,
    "date": 1, "property_ids": 1, "placement": 1, "common_kind": 1, "common_topics": 1,
    "participants.from": 1, "extension": 1, "parent_email_shas": 1, "deal_address": 1,
}


@dataclass
class EnumerationResult:
    question: str
    scope: str
    criteria: Dict[str, Any]
    criteria_text: str
    in_scope: int
    matched: int
    items: List[dict] = field(default_factory=list)
    truncated: bool = False

    @property
    def denominator(self) -> str:
        return f"{self.matched} of {self.in_scope} documents in scope ({self.scope})"

    def as_dict(self) -> dict:
        return {
            "question": self.question, "scope": self.scope, "criteria": self.criteria_text,
            "in_scope": self.in_scope, "matched": self.matched, "truncated": self.truncated,
            "items": self.items,
        }


def _artifact_scope_filter(scope: Scope) -> Dict[str, Any]:
    """Artifact-level equivalent of the chunk scope filter."""
    clauses: List[dict] = []
    if not scope.include_privileged:
        clauses.append({"privileged": {"$ne": True}})
    if scope.mode == "property" and scope.property_id:
        allowed = ["portfolio", "unplaced"]
        clauses.append({"$or": [{"property_ids": scope.property_id}, {"placement": {"$in": allowed}}]})
    elif scope.property_ids:
        clauses.append({"property_ids": {"$in": list(scope.property_ids)}})
    # Inline signature images are never documents anyone wants enumerated.
    clauses.append({"is_inline_image": {"$ne": True}})
    if not clauses:
        return {}
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def is_enumeration(understanding: QueryUnderstanding, rewrite_intent: Optional[str]) -> bool:
    return (rewrite_intent or understanding.intent) in ("enumeration", "negative") or \
        "enumeration" in understanding.intents


def build_criteria(
    understanding: QueryUnderstanding,
    *,
    filters: Dict[str, Any],
) -> tuple[Dict[str, Any], str]:
    """Translate what the question fixed into an artifact query."""
    crit: Dict[str, Any] = {}
    text: List[str] = []
    low = understanding.normalized.lower()

    classes = list(dict.fromkeys(list(understanding.doc_classes) + list(filters.get("doc_classes") or [])))
    if not classes:
        for cls, words in _CLASS_WORDS.items():
            if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
                classes.append(cls)
    # Free-text topic hints from the rewrite widen the word list, never filter.
    hint_words = [h for h in (filters.get("topic_hints") or []) if len(h) > 3]
    if classes or hint_words:
        # doc_class is partially populated; fall back to topic and name words.
        words = [w for c in classes for w in _CLASS_WORDS.get(c, (c.replace("_", " "),))] + hint_words
        regex = "|".join(re.escape(w) for w in words)
        ors: List[dict] = [
            {"filename": {"$regex": regex, "$options": "i"}},
            {"subject": {"$regex": regex, "$options": "i"}},
        ]
        if classes:
            ors.insert(0, {"doc_class": {"$in": classes}})
            ors.insert(1, {"common_topics": {"$in": classes}})
        crit["$or"] = ors
        text.append("type: " + ", ".join(classes or hint_words))

    sender = filters.get("from_email") or (understanding.emails[0] if understanding.emails else None)
    if sender:
        if "@" in sender:
            crit["participants.from"] = {"$regex": re.escape(sender), "$options": "i"}
        else:
            crit["participants.from"] = {"$regex": re.escape(sender.split()[0]), "$options": "i"}
        text.append(f"from: {sender}")

    start = filters.get("date_from") or (understanding.date_range.start if understanding.date_range else None)
    end = filters.get("date_to") or (understanding.date_range.end if understanding.date_range else None)
    rng: Dict[str, Any] = {}
    if isinstance(start, datetime):
        rng["$gte"] = start
    if isinstance(end, datetime):
        rng["$lte"] = end
    if rng:
        crit["date"] = rng
        text.append("period: " + (understanding.date_range.describe() if understanding.date_range else "stated"))

    exts = list(understanding.extensions) + list(filters.get("extensions") or [])
    if exts:
        crit["filename"] = {"$regex": "(" + "|".join(re.escape(e) for e in exts) + ")$", "$options": "i"}
        text.append("extension: " + ", ".join(exts))

    if filters.get("topics"):
        # Vocabulary topics only (validated upstream). Joined with the type
        # clause as an alternative, not an intersection: a "wire" question wants
        # documents typed as wires OR tagged wire_instructions.
        topic_clause = {"common_topics": {"$in": list(filters["topics"])}}
        if "$or" in crit:
            crit["$or"].append(topic_clause)
        else:
            crit.update(topic_clause)
        text.append("topics: " + ", ".join(filters["topics"]))

    return crit, "; ".join(text) if text else "all documents"


def enumerate_set(
    mongo: Mongo,
    question: str,
    understanding: QueryUnderstanding,
    scope: Scope,
    *,
    filters: Dict[str, Any],
    limit: int = 300,
) -> EnumerationResult:
    base = _artifact_scope_filter(scope)
    crit, crit_text = build_criteria(understanding, filters=filters)
    query = {"$and": [c for c in (base, crit) if c]} if crit else base

    in_scope = mongo.artifacts.count_documents(base)
    matched = mongo.artifacts.count_documents(query)
    rows = list(mongo.artifacts.find(query, ARTIFACT_PROJECTION).sort("date", 1).limit(limit))
    items = []
    for r in rows:
        items.append({
            "sha256": r.get("sha256"),
            "name": r.get("filename") or r.get("subject") or r.get("sha256", "")[:12],
            "source_type": r.get("source_type"),
            "doc_class": r.get("doc_class"),
            "date": r["date"].strftime("%Y-%m-%d") if hasattr(r.get("date"), "strftime") else None,
            "property_ids": r.get("property_ids") or [],
            "placement": r.get("placement"),
            "topics": r.get("common_topics") or [],
            "from": ((r.get("participants") or {}).get("from") or [None])[0],
            "deal_address": r.get("deal_address"),
        })
    return EnumerationResult(
        question=question, scope=scope.describe(), criteria=query, criteria_text=crit_text,
        in_scope=in_scope, matched=matched, items=items, truncated=matched > len(items),
    )
