"""Atlas Search (Lucene) index on chunks — the lexical channel's engine.

MongoDB's ``$text`` index is a word list with a crude score: no phrases, no
tolerance for spelling, no synonyms. That was acceptable for a first channel and
is not acceptable for a corpus that is one-third OCR output. OCR writes
"Bayshor", "Chlta Ct" and "$1,250,0OO", and ``$text`` misses every one.

Atlas Search is the other engine on the same cluster, built on Lucene: real BM25,
phrase queries, fuzzy matching (edit distance, so "Bayshor" finds "Bayshore"),
synonym sets, field boosts, and the same filter fields the vector index has —
so property scope is applied inside the query here too, never after.

The synonym set is domain vocabulary a lender uses interchangeably. It lives in
a small collection because that is how Atlas wants it; the rows are written by
``ensure_synonyms`` and the index references them by name.
"""
from __future__ import annotations

import time
from typing import List

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

SEARCH_INDEX_NAME = "chunks_lexical"
SYNONYMS_COLLECTION = "search_synonyms"
SYNONYM_SET = "lending"

#: Equivalence classes. Every term in a row matches every other.
LEGAL_SYNONYMS: List[List[str]] = [
    ["deed of trust", "dot", "trust deed", "mortgage", "security instrument"],
    ["promissory note", "note"],
    ["assignor", "assigning party"],
    ["assignee", "receiving party"],
    ["allonge", "endorsement", "note endorsement"],
    ["assignment", "assignment of deed of trust", "assignment of mortgage", "aodot"],
    ["borrower", "obligor", "mortgagor", "trustor", "grantor"],
    ["lender", "beneficiary", "mortgagee", "noteholder", "holder"],
    ["guarantor", "surety"],
    ["guaranty", "guarantee", "personal guaranty", "pg"],
    ["payoff", "pay-off", "payoff statement", "payoff letter", "payoff demand"],
    ["draw", "draw request", "advance", "disbursement", "funding request"],
    ["settlement statement", "alta statement", "alta", "hud-1", "hud", "closing statement", "closing disclosure", "cd"],
    ["title commitment", "commitment", "prelim", "preliminary title report", "title report"],
    ["title policy", "loan policy", "owner's policy", "ltp", "alta loan policy"],
    ["lis pendens", "notice of pendency"],
    ["notice of default", "nod", "default notice", "demand letter", "notice of intent"],
    ["foreclosure", "trustee sale", "trustee's sale", "sheriff sale"],
    ["extension", "loan extension", "modification", "loan modification", "forbearance"],
    ["appraisal", "valuation", "bpo", "broker price opinion", "arv"],
    ["inspection", "inspection report", "site visit", "progress inspection"],
    ["insurance", "hazard insurance", "builder's risk", "builders risk", "property insurance", "coi", "certificate of insurance"],
    ["escrow", "closing", "settlement"],
    ["wire", "wire instructions", "wiring instructions", "wire transfer"],
    ["invoice", "bill", "statement of charges"],
    ["contractor", "gc", "general contractor", "remodeler", "builder"],
    ["scope of work", "sow", "budget", "construction budget", "renovation budget"],
    ["operating agreement", "oa", "company agreement"],
    ["articles of organization", "certificate of formation", "formation documents"],
    ["apn", "parcel number", "parcel id", "tax id", "folio"],
    ["hoa", "homeowners association", "association"],
    ["llc", "limited liability company"],
    ["rkb", "rkb consulting", "rkb consulting group"],
]

SEARCH_INDEX_DEFINITION = {
    "analyzer": "lucene.english",
    "searchAnalyzer": "lucene.english",
    "mappings": {
        "dynamic": False,
        "fields": {
            # Body and context: English-analysed for BM25, phrase and fuzzy, plus
            # a standard-analysed view for synonyms. Atlas only lets a synonym
            # mapping query fields built with its own analyzer, and the English
            # analyzer drops stopwords — which makes "deed of trust" an invalid
            # synonym. The standard view keeps the stopwords, so the phrase
            # survives and the mapping validates.
            "text": {
                "type": "string", "analyzer": "lucene.english",
                "multi": {"standard": {"type": "string", "analyzer": "lucene.standard"}},
            },
            "context": {
                "type": "string", "analyzer": "lucene.english",
                "multi": {"standard": {"type": "string", "analyzer": "lucene.standard"}},
            },
            # Names: analysed for partial matches and kept as tokens for exact.
            "display_name": [
                {"type": "string", "analyzer": "lucene.standard"},
                {"type": "token", "normalizer": "lowercase"},
            ],
            "filename": [
                {"type": "string", "analyzer": "lucene.standard"},
                {"type": "token", "normalizer": "lowercase"},
            ],
            # Filters — the same set the vector index declares, so both channels
            # scope identically.
            "property_ids": {"type": "token"},
            "scope": {"type": "token"},
            "common_kind": {"type": "token"},
            "common_topics": {"type": "token"},
            "placement": {"type": "token"},
            "source_type": {"type": "token"},
            "doc_class": {"type": "token"},
            "privileged": {"type": "boolean"},
            "date": {"type": "date"},
            "date_ym": {"type": "token"},
            "date_year": {"type": "number"},
            "from_email": {"type": "token", "normalizer": "lowercase"},
            "extension": {"type": "token", "normalizer": "lowercase"},
            "folder_path": {"type": "token"},
            "artifact_sha": {"type": "token"},
            "parent_email_shas": {"type": "token"},
            "entity_ids": {"type": "token"},
            "chunk_id": {"type": "token"},
        },
    },
    "synonyms": [
        {
            "name": SYNONYM_SET,
            "analyzer": "lucene.standard",
            "source": {"collection": SYNONYMS_COLLECTION},
        }
    ],
}

#: Path spec the synonym clause must use — the standard-analysed view.
SYNONYM_PATHS = [{"value": "text", "multi": "standard"}, {"value": "context", "multi": "standard"}]


def _analyzed(term: str) -> str:
    """Approximate lucene.standard: lowercase, whitespace-split. Two terms that
    collapse to the same thing are one term to Atlas, and a duplicate inside or
    across groups invalidates the whole mapping."""
    return " ".join(term.lower().split())


def disjoint_synonyms(groups: List[List[str]]) -> List[List[str]]:
    """Drop any term already claimed by an earlier group; drop groups left with one."""
    claimed: set = set()
    out: List[List[str]] = []
    for group in groups:
        kept: List[str] = []
        for term in group:
            key = _analyzed(term)
            if not key or key in claimed:
                continue
            claimed.add(key)
            kept.append(term)
        if len(kept) >= 2:
            out.append(kept)
    return out


def ensure_synonyms(mongo: Mongo) -> int:
    """Write the synonym rows Atlas reads. Idempotent; returns row count."""
    coll = mongo.db[SYNONYMS_COLLECTION]
    coll.delete_many({})
    rows = [{"mappingType": "equivalent", "synonyms": group} for group in disjoint_synonyms(LEGAL_SYNONYMS)]
    if rows:
        coll.insert_many(rows)
    logger.info("Synonym set '%s': %d equivalence classes", SYNONYM_SET, len(rows))
    return len(rows)


def create_search_index(
    mongo: Mongo, *, wait: bool = True, timeout: int = 900, update: bool = False
) -> str:
    """Create, confirm, or update the lexical index. Idempotent.

    Like the vector index, ``update`` re-applies the definition to an existing
    index; that rebuilds the index structure but touches no chunk.
    """
    from pymongo.operations import SearchIndexModel

    ensure_synonyms(mongo)

    existing = {i["name"]: i for i in mongo.chunks.list_search_indexes()}
    if SEARCH_INDEX_NAME in existing and update:
        mongo.chunks.update_search_index(SEARCH_INDEX_NAME, SEARCH_INDEX_DEFINITION)
        logger.info("Search index '%s' definition updated", SEARCH_INDEX_NAME)
    elif SEARCH_INDEX_NAME in existing:
        logger.info("Search index '%s' already exists", SEARCH_INDEX_NAME)
    else:
        model = SearchIndexModel(
            definition=SEARCH_INDEX_DEFINITION, name=SEARCH_INDEX_NAME, type="search"
        )
        mongo.chunks.create_search_index(model=model)
        logger.info("Search index '%s' requested", SEARCH_INDEX_NAME)

    if not wait:
        return "REQUESTED"

    started = time.time()
    while time.time() - started < timeout:
        for info in mongo.chunks.list_search_indexes():
            if info["name"] == SEARCH_INDEX_NAME:
                status = info.get("status")
                if status == "READY" and info.get("queryable"):
                    logger.info("Search index '%s' READY", SEARCH_INDEX_NAME)
                    return "READY"
                if status == "FAILED":
                    raise RuntimeError(f"Search index build failed: {info}")
        time.sleep(10)
    return "BUILDING"


def search_index_status(mongo: Mongo) -> dict:
    for info in mongo.chunks.list_search_indexes():
        if info["name"] == SEARCH_INDEX_NAME:
            return {"status": info.get("status"), "queryable": info.get("queryable")}
    return {"status": "ABSENT", "queryable": False}
