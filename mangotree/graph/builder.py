"""Builds the knowledge graph from the ingested corpus.

Three passes, in order:

1. **Seed** the registry — the 15 properties, the people we have vouched for and
   the organisations they belong to. These exist whether or not any mail
   mentions them, so a property with no correspondence is still a node rather
   than a hole.
2. **Observe** every artifact — who was on it, which property it concerns — and
   accumulate weighted edges. An unregistered correspondent becomes a node keyed
   by address, and their domain becomes an organisation, so the graph reflects
   who is actually in the deal rather than only who we listed.
3. **Link** chunks to entities, so the vector store can filter by person or
   organisation exactly as it filters by property.

Every edge keeps a bounded sample of the artifacts that produced it. An edge you
cannot trace back to specific messages is an assertion, not evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set

from pymongo import UpdateOne

from mangotree.config.registry import (
    PEOPLE,
    PROPERTIES,
    PROPERTY_CONTACTS,
    Side,
)
from mangotree.core.logging import logger
from mangotree.graph.schema import (
    CORRESPONDED_WITH,
    Edge,
    Entity,
    INVOLVED_IN,
    MEMBER_OF,
    SERVICES,
    address_person_id,
    domain_org_id,
    org_id,
    person_id,
    property_entity_id,
)
from mangotree.storage.mongo import Mongo

#: Free-mail domains are not organisations. Treating gmail.com as an employer
#: would connect a homeowner, a realtor and a contractor into one fictional firm.
_GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "aol.com", "outlook.com", "hotmail.com",
    "icloud.com", "me.com", "msn.com", "comcast.net", "verizon.net",
    "live.com", "protonmail.com", "mac.com",
}

WRITE_BATCH = 500


class KnowledgeGraphBuilder:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.entities: Dict[str, Entity] = {}
        self.edges: Dict[str, Edge] = {}
        self.address_to_entity: Dict[str, str] = {}
        self.artifacts_seen = 0
        self.chunks_linked = 0

    # ------------------------------------------------------------ seeding
    def _seed_registry(self) -> None:
        for prop in PROPERTIES:
            entity = Entity(
                entity_id=property_entity_id(prop.property_id),
                entity_type="property",
                name=prop.canonical_address,
                registry_id=prop.property_id,
                aliases=list(prop.aliases),
                in_registry=True,
                property_ids={prop.property_id},
            )
            self.entities[entity.entity_id] = entity

        for person in PEOPLE:
            org_entity = self._ensure_org(person.org.value, in_registry=True)
            entity = Entity(
                entity_id=person_id(person.person_id),
                entity_type="person",
                name=person.display_name,
                registry_id=person.person_id,
                side=person.side.value,
                role=person.role,
                org_entity_id=org_entity.entity_id,
                addresses=list(person.all_addresses),
                in_registry=True,
            )
            self.entities[entity.entity_id] = entity
            for address in person.all_addresses:
                self.address_to_entity[address.lower()] = entity.entity_id
            self._edge(entity.entity_id, org_entity.entity_id, MEMBER_OF).observe(
                None, None, set()
            )

        # Roster edges: the admin told us who is on each deal. These hold even
        # for a property with no mail yet, which is precisely when a roster is
        # most useful.
        for property_id, registry_ids in PROPERTY_CONTACTS.items():
            target = property_entity_id(property_id)
            if target not in self.entities:
                continue
            for registry_id in registry_ids:
                source = person_id(registry_id)
                if source not in self.entities:
                    continue
                self._edge(source, target, INVOLVED_IN).observe(
                    None, None, {property_id}
                )
                self.entities[source].property_ids.add(property_id)

    def _ensure_org(self, name: str, *, in_registry: bool = False) -> Entity:
        entity_id = org_id(name)
        entity = self.entities.get(entity_id)
        if entity is None:
            entity = Entity(
                entity_id=entity_id, entity_type="org", name=name,
                in_registry=in_registry,
            )
            self.entities[entity_id] = entity
        return entity

    def _ensure_address_person(self, address: str) -> Optional[Entity]:
        address = (address or "").strip().lower()
        if not address or "@" not in address:
            return None

        known = self.address_to_entity.get(address)
        if known:
            return self.entities[known]

        entity_id = address_person_id(address)
        entity = self.entities.get(entity_id)
        if entity is None:
            domain = address.rsplit("@", 1)[-1]
            org_entity = None
            if domain and domain not in _GENERIC_DOMAINS:
                org_entity = self._ensure_org(domain)
                org_entity.entity_type = "org"
                org_entity.entity_id = domain_org_id(domain)
                self.entities[org_entity.entity_id] = org_entity
            entity = Entity(
                entity_id=entity_id,
                entity_type="person",
                name=address,
                side=Side.EXTERNAL.value,
                addresses=[address],
                org_entity_id=org_entity.entity_id if org_entity else None,
                in_registry=False,
            )
            self.entities[entity_id] = entity
            self.address_to_entity[address] = entity_id
            if org_entity:
                self._edge(entity_id, org_entity.entity_id, MEMBER_OF).observe(
                    None, None, set()
                )
        return entity

    def _edge(self, src: str, dst: str, edge_type: str) -> Edge:
        key = f"{src}|{edge_type}|{dst}"
        edge = self.edges.get(key)
        if edge is None:
            edge = Edge(src=src, dst=dst, edge_type=edge_type)
            self.edges[key] = edge
        return edge

    # ------------------------------------------------------------ observing
    def _observe_artifacts(self) -> None:
        projection = {
            "sha256": 1, "date": 1, "property_ids": 1, "participants": 1,
            "person_ids": 1, "source_type": 1,
        }
        cursor = self.mongo.artifacts.find({}, projection)

        for artifact in cursor:
            self.artifacts_seen += 1
            when = artifact.get("date")
            if isinstance(when, str):
                when = None
            properties = set(artifact.get("property_ids") or [])
            sha = artifact.get("sha256")

            participants = artifact.get("participants") or {}
            addresses: Set[str] = set()
            for key in ("from", "to", "cc", "bcc"):
                for address in participants.get(key) or []:
                    if address:
                        addresses.add(str(address).strip().lower())

            people: List[Entity] = []
            for address in addresses:
                entity = self._ensure_address_person(address)
                if entity is None:
                    continue
                entity.observe(when)
                entity.property_ids |= properties
                people.append(entity)

            for entity in people:
                for property_id in properties:
                    target = property_entity_id(property_id)
                    if target not in self.entities:
                        continue
                    self._edge(entity.entity_id, target, INVOLVED_IN).observe(
                        sha, when, {property_id}
                    )
                    if entity.org_entity_id:
                        self._edge(entity.org_entity_id, target, SERVICES).observe(
                            sha, when, {property_id}
                        )

            # Correspondence is symmetric, so it is stored once under a sorted
            # pair rather than twice in opposite directions.
            ids = sorted({e.entity_id for e in people})
            for i, src in enumerate(ids):
                for dst in ids[i + 1:]:
                    self._edge(src, dst, CORRESPONDED_WITH).observe(sha, when, properties)

            if self.artifacts_seen % 2000 == 0:
                logger.info(
                    "  graph: %d artifacts, %d entities, %d edges",
                    self.artifacts_seen, len(self.entities), len(self.edges),
                )

    # ------------------------------------------------------------ chunk linkage
    def _link_chunks(self) -> None:
        """Stamp entity ids onto chunks so vectors are filterable by person/org.

        A chunk inherits the people on its parent email and the properties the
        chunk itself concerns — not the parent's properties, which would undo the
        segmenter's work of keeping one property's text away from another's.
        """
        parents: Dict[str, List[str]] = {}
        for artifact in self.mongo.artifacts.find(
            {"source_type": {"$in": ["email", "attachment"]}},
            {"sha256": 1, "participants": 1},
        ):
            participants = artifact.get("participants") or {}
            ids: List[str] = []
            for key in ("from", "to", "cc", "bcc"):
                for address in participants.get(key) or []:
                    entity_id = self.address_to_entity.get(str(address).strip().lower())
                    if entity_id and entity_id not in ids:
                        ids.append(entity_id)
            if ids:
                parents[artifact["sha256"]] = ids

        operations: List[UpdateOne] = []
        for chunk in self.mongo.chunks.find(
            {}, {"chunk_id": 1, "artifact_sha": 1, "property_ids": 1}
        ):
            entity_ids = list(parents.get(chunk.get("artifact_sha"), []))
            for property_id in chunk.get("property_ids") or []:
                entity_ids.append(property_entity_id(property_id))
            if not entity_ids:
                continue
            operations.append(UpdateOne(
                {"chunk_id": chunk["chunk_id"]},
                {"$set": {"entity_ids": sorted(set(entity_ids))}},
            ))
            if len(operations) >= WRITE_BATCH:
                self.mongo.chunks.bulk_write(operations, ordered=False)
                self.chunks_linked += len(operations)
                operations = []
        if operations:
            self.mongo.chunks.bulk_write(operations, ordered=False)
            self.chunks_linked += len(operations)

    # ------------------------------------------------------------ persistence
    def _write(self) -> None:
        entities = self.mongo.db["entities"]
        edges = self.mongo.db["entity_edges"]

        entities.create_index("entity_id", unique=True, name="ux_entity_id")
        entities.create_index("entity_type", name="ix_entity_type")
        entities.create_index("property_ids", name="ix_entity_property")
        entities.create_index("addresses", name="ix_entity_addresses")
        edges.create_index("edge_id", unique=True, name="ux_edge_id")
        edges.create_index("src", name="ix_edge_src")
        edges.create_index("dst", name="ix_edge_dst")
        edges.create_index("edge_type", name="ix_edge_type")
        edges.create_index("property_ids", name="ix_edge_property")
        self.mongo.chunks.create_index("entity_ids", name="ix_chunk_entities")

        for collection, rows in (
            (entities, [e.as_dict() for e in self.entities.values()]),
            (edges, [e.as_dict() for e in self.edges.values()]),
        ):
            operations = [
                UpdateOne(
                    {"entity_id" if "entity_id" in row else "edge_id":
                     row.get("entity_id") or row.get("edge_id")},
                    {"$set": row},
                    upsert=True,
                )
                for row in rows
            ]
            for start in range(0, len(operations), WRITE_BATCH):
                collection.bulk_write(operations[start:start + WRITE_BATCH], ordered=False)

    # ------------------------------------------------------------ entry point
    def build(self, *, link_chunks: bool = True) -> dict:
        started = datetime.now(timezone.utc)
        logger.info("Knowledge graph: seeding registry")
        self._seed_registry()

        logger.info("Knowledge graph: observing artifacts")
        self._observe_artifacts()

        logger.info("Knowledge graph: writing %d entities, %d edges",
                    len(self.entities), len(self.edges))
        self._write()

        if link_chunks:
            logger.info("Knowledge graph: linking chunks to entities")
            self._link_chunks()

        by_type: Dict[str, int] = {}
        for entity in self.entities.values():
            by_type[entity.entity_type] = by_type.get(entity.entity_type, 0) + 1
        by_edge: Dict[str, int] = {}
        for edge in self.edges.values():
            by_edge[edge.edge_type] = by_edge.get(edge.edge_type, 0) + 1

        report = {
            "artifacts_seen": self.artifacts_seen,
            "entities": len(self.entities),
            "entities_by_type": by_type,
            "edges": len(self.edges),
            "edges_by_type": by_edge,
            "registry_entities": sum(1 for e in self.entities.values() if e.in_registry),
            "discovered_entities": sum(1 for e in self.entities.values() if not e.in_registry),
            "chunks_linked": self.chunks_linked,
            "seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        }
        logger.info("Knowledge graph complete: %s", report)
        return report
