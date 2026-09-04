"""Scope — what a chat is allowed to see, expressed as filters applied *inside*
every search.

Two modes, one pipeline. Property mode restricts every channel to the property's
own file plus the portfolio-common store plus the unplaced items; each of those
is its own ranked list with its own weight, so the property's file wins ties.
Global mode restricts nothing but privilege and lets everything compete at full
weight.

The filter is applied in the ANN scan and in the Lucene query, never afterwards:
a post-filter would let a large property consume the top-k before a small one
was considered, and would turn any filtering bug into a silent cross-property
leak.

``placement`` is the single token that encodes where a chunk stands:

    property   — filed under one or more of the fifteen
    portfolio  — confident-common, bears on the book, visible in property chats
    unplaced   — Opus 5 could not place it; a human is deciding; visible, labelled
    business   — confident other-property deal or no deal content; global only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from mangotree.retrieve import config as cfg

PLACEMENTS = ("property", "portfolio", "unplaced", "business")


def placement_of(artifact_or_chunk: dict) -> str:
    """Derive placement from the fields the pipeline already writes."""
    if artifact_or_chunk.get("property_ids"):
        return "property"
    kind = artifact_or_chunk.get("common_kind")
    if kind in ("portfolio", "business"):
        return kind
    return "unplaced"


@dataclass
class RankedListSpec:
    """One ranked list to build: a name, a filter, a fusion weight, a label."""
    name: str
    filter: Dict[str, Any]
    weight: float
    label: str


@dataclass
class Scope:
    mode: str = "global"                       # "property" | "global"
    property_id: Optional[str] = None
    #: Global mode may be narrowed by the router to a set of properties without
    #: becoming property mode (labels and fan-out behave differently).
    property_ids: List[str] = field(default_factory=list)
    include_privileged: bool = False
    #: Role-based restriction hook; empty means no additional restriction.
    role_filter: Dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------------
    @classmethod
    def for_property(cls, property_id: str, **kw) -> "Scope":
        return cls(mode="property", property_id=property_id, property_ids=[property_id], **kw)

    @classmethod
    def global_(cls, **kw) -> "Scope":
        return cls(mode="global", **kw)

    # ---------------------------------------------------------------------
    def _base(self) -> List[dict]:
        clauses: List[dict] = []
        if not self.include_privileged:
            clauses.append({"privileged": {"$ne": True}})
        if self.role_filter:
            clauses.append(dict(self.role_filter))
        return clauses

    @staticmethod
    def _and(clauses: Sequence[dict]) -> Dict[str, Any]:
        clauses = [c for c in clauses if c]
        if not clauses:
            return {}
        return clauses[0] if len(clauses) == 1 else {"$and": list(clauses)}

    def base_filter(self) -> Dict[str, Any]:
        """The filter every tool applies regardless of list — the clean-mode floor."""
        clauses = self._base()
        if self.mode == "property" and self.property_id:
            allowed = ["property", "portfolio", "unplaced"]
            if cfg.INCLUDE_BUSINESS_COMMON_IN_PROPERTY_CHAT:
                allowed.append("business")
            clauses.append({
                "$or": [
                    {"property_ids": self.property_id},
                    {"placement": {"$in": [p for p in allowed if p != "property"]}},
                ]
            })
        elif self.property_ids:
            clauses.append({"property_ids": {"$in": list(self.property_ids)}})
        return self._and(clauses)

    def ranked_lists(self) -> List[RankedListSpec]:
        """The lists to build for this scope, each with its filter and weight."""
        base = self._base()
        if self.mode == "property" and self.property_id:
            lists = [
                RankedListSpec(
                    "own", self._and(base + [{"property_ids": self.property_id}]),
                    1.0, cfg.LABEL_PROPERTY,
                ),
                RankedListSpec(
                    "portfolio", self._and(base + [{"placement": "portfolio"}]),
                    cfg.COMMON_LIST_WEIGHT, cfg.LABEL_PORTFOLIO,
                ),
                RankedListSpec(
                    "unplaced", self._and(base + [{"placement": "unplaced"}]),
                    cfg.UNPLACED_LIST_WEIGHT, cfg.LABEL_UNPLACED,
                ),
            ]
            if cfg.INCLUDE_BUSINESS_COMMON_IN_PROPERTY_CHAT:
                lists.append(RankedListSpec(
                    "business", self._and(base + [{"placement": "business"}]),
                    cfg.COMMON_LIST_WEIGHT, cfg.LABEL_BUSINESS,
                ))
            return lists

        clauses = list(base)
        if self.property_ids:
            clauses.append({"property_ids": {"$in": list(self.property_ids)}})
        return [RankedListSpec("all", self._and(clauses), 1.0, "")]

    # ---------------------------------------------------------------------
    def label_for(self, hit) -> str:
        """What the AI is told about where this passage stands."""
        if self.mode == "property" and self.property_id in (hit.property_ids or []):
            return cfg.LABEL_PROPERTY
        placement = hit.placement or placement_of({"property_ids": hit.property_ids, "common_kind": hit.common_kind})
        return {
            "property": cfg.LABEL_PROPERTY if self.mode == "property" else "",
            "portfolio": cfg.LABEL_PORTFOLIO,
            "unplaced": cfg.LABEL_UNPLACED,
            "business": cfg.LABEL_BUSINESS,
        }.get(placement, "")

    def allows(self, hit) -> bool:
        """Defence in depth — the filter above should make a miss impossible."""
        if hit.privileged and not self.include_privileged:
            return False
        if self.mode == "property" and self.property_id:
            if self.property_id in (hit.property_ids or []):
                return True
            placement = hit.placement or placement_of({"property_ids": hit.property_ids, "common_kind": hit.common_kind})
            if placement == "business":
                return cfg.INCLUDE_BUSINESS_COMMON_IN_PROPERTY_CHAT
            return placement in ("portfolio", "unplaced")
        if self.property_ids:
            return bool(set(self.property_ids) & set(hit.property_ids or []))
        return True

    def describe(self) -> str:
        if self.mode == "property":
            return f"property chat: {self.property_id}"
        if self.property_ids:
            return "global chat narrowed to: " + ", ".join(self.property_ids)
        return "global chat"


# =============================================================================
# Filter translation — one MQL-shaped dict, two engines
# =============================================================================

def to_search_filters(filter_dict: Dict[str, Any]) -> tuple[List[dict], List[dict]]:
    """Translate a filter dict into Atlas Search ``compound.filter`` / ``mustNot``.

    Handles the shapes this module produces: ``{f: v}``, ``{f: {"$in": [...]}}``,
    ``{f: {"$ne": v}}``, ``{f: {"$gte"/"$lte"/...: date}}``, ``{"$and": [...]}``,
    ``{"$or": [...]}``. Returns (filter_clauses, must_not_clauses).
    """
    filters: List[dict] = []
    must_not: List[dict] = []

    def one(field: str, value: Any) -> None:
        if isinstance(value, dict):
            if "$in" in value:
                filters.append({"in": {"path": field, "value": list(value["$in"])}})
            if "$nin" in value:
                must_not.append({"in": {"path": field, "value": list(value["$nin"])}})
            if "$ne" in value:
                must_not.append({"equals": {"path": field, "value": value["$ne"]}})
            if "$eq" in value:
                filters.append({"equals": {"path": field, "value": value["$eq"]}})
            rng = {k[1:]: v for k, v in value.items() if k in ("$gte", "$gt", "$lte", "$lt")}
            if rng:
                filters.append({"range": {"path": field, **rng}})
        else:
            filters.append({"equals": {"path": field, "value": value}})

    def walk(node: Dict[str, Any]) -> None:
        for key, value in node.items():
            if key == "$and":
                for sub in value:
                    walk(sub)
            elif key == "$or":
                shoulds: List[dict] = []
                for sub in value:
                    f, mn = to_search_filters(sub)
                    clause: dict = {}
                    if f:
                        clause["filter"] = f
                    if mn:
                        clause["mustNot"] = mn
                    if clause:
                        shoulds.append({"compound": clause})
                if shoulds:
                    filters.append({"compound": {"should": shoulds, "minimumShouldMatch": 1}})
            else:
                one(key, value)

    walk(filter_dict or {})
    return filters, must_not
