"""Knowledge-graph layer for the legal evidence platform.

Consolidates entity normalization, resolution, relationship edges, and
entity-anchored fan-out that previously lived (duplicated) inside ingestion
scripts. Everything here is pure + idempotent so it can be reused by ingest,
re-parse, consolidation, and live retrieval without drift.
"""
