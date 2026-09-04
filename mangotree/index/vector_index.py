"""Atlas Vector Search index definition and management.

``property_ids`` is declared as a **filter** field, not merely stored. That
distinction is the entire per-property guarantee at the retrieval layer: Atlas
applies the filter *during* the approximate-nearest-neighbour search, so a query
scoped to Decatur never has Varnum vectors in its candidate set. Post-filtering
after a top-k search would be both wrong (the k slots get eaten by other
properties) and unsafe (a leak becomes a ranking accident away).

``privileged`` is a filter field for the same reason: attorney work product must
be excludable at the search layer rather than trimmed from results afterwards.
"""
from __future__ import annotations

import time
from typing import List, Optional

from mangotree.config.models import EMBEDDING_DIM, EMBEDDING_MODEL
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

VECTOR_INDEX_NAME = "chunks_vector"

VECTOR_INDEX_DEFINITION = {
    "fields": [
        {
            "type": "vector",
            "path": "embedding",
            "numDimensions": EMBEDDING_DIM,
            "similarity": "cosine",
        },
        # Filters applied during search, not after.
        {"type": "filter", "path": "property_ids"},
        {"type": "filter", "path": "privileged"},
        {"type": "filter", "path": "source_type"},
        {"type": "filter", "path": "doc_class"},
        {"type": "filter", "path": "embedding_model"},
        # Knowledge-graph linkage, so a search can be narrowed to a person or an
        # organisation during the ANN scan for the same reason property_ids is.
        {"type": "filter", "path": "entity_ids"},
        # Set once Opus 5 has ruled. Filtering on it is what keeps the common
        # store out of a per-property chat.
        {"type": "filter", "path": "scope"},
        # Splits the common store. A property chat searches scope=property plus
        # common_kind=portfolio; business traffic stays out of the scan entirely.
        {"type": "filter", "path": "common_kind"},
        {"type": "filter", "path": "common_topics"},
        # property | portfolio | unplaced | business. The one token a property
        # chat's scope filter needs; derived from property_ids + common_kind.
        {"type": "filter", "path": "placement"},
        # --- Timeline. A lending file is read chronologically far more often
        # than it is read by topic: "before the default", "the six months around
        # closing", "Q1 2025". Without these the date can be shown on a result
        # but cannot shape the search, so a period question has to retrieve the
        # whole corpus and discard most of it afterwards.
        {"type": "filter", "path": "date"},
        {"type": "filter", "path": "date_ym"},
        {"type": "filter", "path": "date_year"},
        {"type": "filter", "path": "latest_date"},
        # --- Provenance. "Everything the title company sent", "only the
        # spreadsheets", "what sits in the Chita Ct folder".
        {"type": "filter", "path": "from_email"},
        {"type": "filter", "path": "extension"},
        {"type": "filter", "path": "folder_path"},
        # --- Pivots. artifact_sha collapses ten chunks of one title policy into
        # a single result; parent_email_shas answers "which emails carried this".
        {"type": "filter", "path": "artifact_sha"},
        {"type": "filter", "path": "parent_email_shas"},
    ]
}


def create_vector_index(
    mongo: Mongo, *, wait: bool = True, timeout: int = 600, update: bool = False
) -> str:
    """Create, confirm, or update the vector search index. Idempotent.

    ``update`` re-applies the definition to an index that already exists, which
    is how filter fields are added. Worth being explicit that this does **not**
    re-embed: the stored vectors are untouched and only the searchable-field
    declaration changes, so widening the filter set costs a rebuild of the index
    structure rather than a pass over the corpus.
    """
    from pymongo.operations import SearchIndexModel

    existing = {i["name"] for i in mongo.chunks.list_search_indexes()}
    if VECTOR_INDEX_NAME in existing and update:
        mongo.chunks.update_search_index(VECTOR_INDEX_NAME, VECTOR_INDEX_DEFINITION)
        filters = [f["path"] for f in VECTOR_INDEX_DEFINITION["fields"] if f["type"] == "filter"]
        logger.info(
            "Vector index '%s' definition updated (%d filter fields: %s)",
            VECTOR_INDEX_NAME, len(filters), ", ".join(filters),
        )
    elif VECTOR_INDEX_NAME in existing:
        logger.info("Vector index '%s' already exists", VECTOR_INDEX_NAME)
    else:
        model = SearchIndexModel(
            definition=VECTOR_INDEX_DEFINITION,
            name=VECTOR_INDEX_NAME,
            type="vectorSearch",
        )
        mongo.chunks.create_search_index(model=model)
        logger.info("Vector index '%s' requested", VECTOR_INDEX_NAME)

    if not wait:
        return VECTOR_INDEX_NAME

    deadline = time.time() + timeout
    while time.time() < deadline:
        for info in mongo.chunks.list_search_indexes(VECTOR_INDEX_NAME):
            if info.get("queryable"):
                logger.info("Vector index '%s' is queryable", VECTOR_INDEX_NAME)
                return VECTOR_INDEX_NAME
            status = info.get("status", "unknown")
            logger.info("  index status: %s", status)
            if status == "FAILED":
                raise RuntimeError(f"Vector index build failed: {info}")
        time.sleep(10)

    raise TimeoutError(f"Vector index not queryable within {timeout}s")


def vector_index_status(mongo: Mongo) -> List[dict]:
    out = []
    for info in mongo.chunks.list_search_indexes():
        out.append({
            "name": info.get("name"),
            "type": info.get("type"),
            "status": info.get("status"),
            "queryable": info.get("queryable"),
        })
    return out


def index_health(mongo: Mongo) -> dict:
    """Whether the index can actually be trusted for retrieval.

    ``mixed_embedding_spaces`` is the check that matters: more than one model id
    among the chunks means similarity scores are being compared across
    incompatible spaces, and every ranking in the system is quietly wrong.
    """
    total = mongo.chunks.count_documents({})
    embedded = mongo.chunks.count_documents({"embedding_status": "ok"})
    failed = mongo.chunks.count_documents({"embedding_status": "failed"})
    models = mongo.chunks.distinct("embedding_model")
    unattributed = mongo.chunks.count_documents({"property_ids": []})

    return {
        "chunks_total": total,
        "chunks_embedded": embedded,
        "chunks_failed": failed,
        "chunks_unattributed": unattributed,
        "embedding_models": models,
        "mixed_embedding_spaces": len(models) > 1,
        "expected_model": EMBEDDING_MODEL,
        "search_indexes": vector_index_status(mongo),
    }
