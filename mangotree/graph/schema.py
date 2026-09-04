"""Entity and edge shapes, and the id scheme that keeps them stable.

Ids are derived, never generated. ``person:rakesh`` is the same node on every
run and in every collection, so a rebuild updates the graph in place instead of
duplicating it, and a chunk's ``entity_ids`` stay valid across rebuilds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set

#: Edge types. Deliberately few — an edge nobody queries is a maintenance cost.
MEMBER_OF = "member_of"              # person  -> org
INVOLVED_IN = "involved_in"          # person/org -> property
CORRESPONDED_WITH = "corresponded_with"  # person <-> person
SERVICES = "services"                # org -> property (via its people)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def person_id(registry_id: str) -> str:
    return f"person:{registry_id}"


def address_person_id(address: str) -> str:
    """A correspondent we know only by their address.

    Kept distinct from registry people so the two never merge by accident: an
    unregistered address is a real node in the graph but is not a person we have
    vouched for, and analysis should be able to tell the difference.
    """
    return f"person:addr:{(address or '').strip().lower()}"


def org_id(name: str) -> str:
    return f"org:{slug(name)}"


def domain_org_id(domain: str) -> str:
    return f"org:domain:{(domain or '').strip().lower()}"


def property_entity_id(property_id: str) -> str:
    return f"property:{property_id}"


@dataclass
class Entity:
    entity_id: str
    entity_type: str                 # person | org | property
    name: str
    registry_id: Optional[str] = None
    side: Optional[str] = None       # rkb | external
    role: Optional[str] = None
    org_entity_id: Optional[str] = None
    addresses: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    in_registry: bool = False
    mention_count: int = 0
    property_ids: Set[str] = field(default_factory=set)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    def observe(self, when: Optional[datetime]) -> None:
        self.mention_count += 1
        if when is None:
            return
        if self.first_seen is None or when < self.first_seen:
            self.first_seen = when
        if self.last_seen is None or when > self.last_seen:
            self.last_seen = when

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "name": self.name,
            "registry_id": self.registry_id,
            "side": self.side,
            "role": self.role,
            "org_entity_id": self.org_entity_id,
            "addresses": sorted(self.addresses),
            "aliases": sorted(self.aliases),
            "in_registry": self.in_registry,
            "mention_count": self.mention_count,
            "property_ids": sorted(self.property_ids),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class Edge:
    src: str
    dst: str
    edge_type: str
    weight: int = 0
    property_ids: Set[str] = field(default_factory=set)
    #: A bounded sample of the artifacts that produced this edge. Bounded because
    #: an edge between Rakesh and Wes would otherwise carry thousands of shas and
    #: bloat the document; a sample is enough to audit the edge is real.
    evidence: List[str] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    EVIDENCE_CAP = 25

    @property
    def edge_id(self) -> str:
        return f"{self.src}|{self.edge_type}|{self.dst}"

    def observe(
        self, sha: Optional[str], when: Optional[datetime], properties: Set[str]
    ) -> None:
        self.weight += 1
        self.property_ids |= properties
        if sha and len(self.evidence) < self.EVIDENCE_CAP:
            self.evidence.append(sha)
        if when is None:
            return
        if self.first_seen is None or when < self.first_seen:
            self.first_seen = when
        if self.last_seen is None or when > self.last_seen:
            self.last_seen = when

    def as_dict(self) -> dict:
        return {
            "edge_id": self.edge_id,
            "src": self.src,
            "dst": self.dst,
            "edge_type": self.edge_type,
            "weight": self.weight,
            "property_ids": sorted(self.property_ids),
            "evidence": self.evidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }
