"""Knowledge graph — who is who, and what they are connected to.

Requirement 11: vector data connected to full metadata, entity linkage and a
knowledge graph. This package supplies the entities and edges, and stamps
``entity_ids`` onto every chunk so retrieval can filter by person or organisation
the same way it filters by property.

The graph is built from what the corpus already proves rather than from a model:
registry membership, who actually appeared on which message, and which property
each message was decided to concern. That keeps every edge auditable back to a
list of artifacts, which is the property that makes a graph usable as evidence in
a lending file rather than merely suggestive.
"""
