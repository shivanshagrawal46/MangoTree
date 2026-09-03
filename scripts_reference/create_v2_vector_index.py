"""
Create the Atlas Vector Search index on `email_chunks_v2`.

This is the v2 equivalent of `scripts/print_vector_index.py` — but
PyMongo 4.5+ exposes `Collection.create_search_index()` for vector
indexes, so we can do the whole thing programmatically. No more
copy/pasting JSON into the Atlas UI.

Index design choices:

  • numDimensions = 1024  (voyage-4-large native output dim)
  • similarity     = cosine  (Voyage's recommendation for legal text)
  • Filter fields: source_type / date / date_ym / from_email /
    folder_path / filename / extension / sha256 / attachment_id
    — these power the `$vectorSearch.filter` clause used by the v2
    hybrid retriever for date-range narrowing, sender narrowing,
    filename direct lookup, etc.

Idempotency: if the index already exists with the same name we skip.
If it exists with a DIFFERENT spec, pass --force to drop & recreate.

Usage:
  python scripts/create_v2_vector_index.py
  python scripts/create_v2_vector_index.py --force
  python scripts/create_v2_vector_index.py --name email_chunks_v2_vector
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

from config.settings import Settings


COLLECTION_NAME = "email_chunks_v2"
DEFAULT_INDEX_NAME = "email_chunks_v2_vector"
EMBEDDING_DIM = 1024  # voyage-4-large

INDEX_DEFINITION = {
    "fields": [
        {
            "type": "vector",
            "path": "embedding",
            "numDimensions": EMBEDDING_DIM,
            "similarity": "cosine",
        },
        # Filterable fields — used in $vectorSearch.filter clauses by the
        # v2 hybrid retriever for date-range narrowing, sender narrowing,
        # filename direct lookup, source-type isolation, etc.
        #
        # Top-level (PRIMARY-occurrence) mirror fields. These power
        # "creation-verb" queries ("when was X drafted/signed/filed?")
        # where we want the earliest known appearance.
        {"type": "filter", "path": "source_type"},
        {"type": "filter", "path": "date"},
        {"type": "filter", "path": "date_ym"},
        {"type": "filter", "path": "from_email"},
        {"type": "filter", "path": "folder_path"},
        {"type": "filter", "path": "filename"},
        {"type": "filter", "path": "extension"},
        {"type": "filter", "path": "sha256"},
        {"type": "filter", "path": "attachment_id"},
        {"type": "filter", "path": "email_id"},
        {"type": "filter", "path": "latest_date"},
        # Evidentiary spine — REQUIRED for Clean/shareable mode (filters out
        # privileged at the vector layer) and corpus-scoped retrieval.
        {"type": "filter", "path": "privilege_status"},
        {"type": "filter", "path": "corpus"},

        # Option B fan-out filter paths — Atlas Vector Search supports
        # array path filters with "any-element-matches" semantics, which
        # is exactly what we want for "what was discussed in March 2024?"
        # and "what did Jane send / receive / forward?" queries.
        {"type": "filter", "path": "occurrences.date"},
        {"type": "filter", "path": "occurrences.date_ym"},
        {"type": "filter", "path": "occurrences.from_email"},
        {"type": "filter", "path": "occurrences.email_id"},
        {"type": "filter", "path": "occurrences.attachment_id"},
        {"type": "filter", "path": "occurrences.folder_path"},
        {"type": "filter", "path": "occurrences.filename"},
    ]
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", default=DEFAULT_INDEX_NAME,
                    help="Atlas vector-search index name")
    ap.add_argument("--force", action="store_true",
                    help="Drop and recreate if the index already exists")
    ap.add_argument("--wait", action="store_true",
                    help="Block until the index becomes READY (Atlas can take 1-2 min)")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Max seconds to wait for READY when --wait is set")
    args = ap.parse_args()

    settings = Settings.load()
    client = MongoClient(settings.mongo_uri)
    db = client[settings.mongo_db_name]
    col = db[COLLECTION_NAME]

    n_docs = col.estimated_document_count()
    print(f"target: {settings.mongo_db_name}.{COLLECTION_NAME} "
          f"(~{n_docs:,} docs)")
    print(f"index name: {args.name}")
    print("definition:")
    print(json.dumps(INDEX_DEFINITION, indent=2))
    print("-" * 60)

    # Check for existing index.
    existing = None
    try:
        for ix in col.list_search_indexes():
            if ix.get("name") == args.name:
                existing = ix
                break
    except Exception as exc:
        print(f"WARN: could not list search indexes (Atlas only?): {exc}")
        # Continue — create_search_index will tell us if it's not Atlas.

    if existing:
        print(f"Index '{args.name}' already exists "
              f"(status={existing.get('status')})")
        if args.force:
            print(f"--force: dropping {args.name} ...")
            col.drop_search_index(args.name)
            # Wait for drop to settle.
            for _ in range(60):
                still = [
                    ix for ix in col.list_search_indexes()
                    if ix.get("name") == args.name
                ]
                if not still:
                    break
                time.sleep(2)
        else:
            print("Pass --force to drop and recreate.")
            return 0

    model = SearchIndexModel(definition=INDEX_DEFINITION,
                             name=args.name, type="vectorSearch")
    print(f"Creating Atlas Vector Search index '{args.name}' ...")
    name = col.create_search_index(model=model)
    print(f"  submitted. server-returned name: {name}")
    print("  Atlas typically takes 60-120 seconds to mark the index READY.")

    if args.wait:
        print("Waiting for READY ...")
        deadline = time.time() + args.timeout
        last_status = None
        while time.time() < deadline:
            for ix in col.list_search_indexes(args.name):
                status = ix.get("status") or ix.get("queryable")
                if status != last_status:
                    print(f"  status={status}  queryable={ix.get('queryable')}")
                    last_status = status
                if ix.get("queryable") is True:
                    print("  READY ✓")
                    return 0
            time.sleep(5)
        print(f"WARN: not READY after {args.timeout}s; check Atlas UI.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
