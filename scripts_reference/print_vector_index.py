"""
Print the Atlas Vector Search index definition you need to create.

MongoDB Atlas does NOT yet expose `$vectorSearch` index creation through
PyMongo (it's an Atlas-only feature). Run this script to print the JSON
definition, then paste it into Atlas → your cluster → Atlas Search → Create
Search Index → JSON Editor, on the `email_chunks` collection.

You can also create it from the `mongosh` shell with:

    db.email_chunks.createSearchIndex({
      name: "email_chunks_vector",
      type: "vectorSearch",
      definition: { ... output of this script ... }
    })

Or via the Atlas CLI:

    atlas clusters search indexes create --clusterName <cluster> \
        --collection email_chunks --db fraud_emails -f vector_index.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings


def main() -> int:
    settings = Settings.load()

    definition = {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": settings.embedding_dim,
                "similarity": "cosine",
            },
            # Filterable fields — used in $vectorSearch.filter for
            # date-range / sender / source-type narrowing.
            {"type": "filter", "path": "source_type"},
            {"type": "filter", "path": "date"},
            {"type": "filter", "path": "date_ym"},
            {"type": "filter", "path": "from_email"},
            {"type": "filter", "path": "folder_path"},
            {"type": "filter", "path": "filename"},
        ]
    }

    print("=" * 72)
    print(f"Atlas Vector Search index for: {settings.mongo_db_name}.email_chunks")
    print(f"Index name (from .env): {settings.vector_index_name}")
    print("=" * 72)
    print()
    print("Paste the following JSON into Atlas → Search → Create Index")
    print("(Vector Search type, JSON editor):")
    print()
    print(json.dumps(definition, indent=2))
    print()
    print("After creation, wait ~30-60 seconds for the index to become ACTIVE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
