"""Size the stages that have not run yet, so the finish estimate is measured.

Segregation calls Opus once per email (attachments ride along in the same
call). Indexing contextualises once per document and embeds per chunk. Knowing
both counts turns "a few more hours" into an actual arrival time.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.resolve.segregation_runner import CONCURRENCY as SEGREGATE_CONCURRENCY
from mangotree.storage.mongo import get_mongo

#: Imported, not copied: a local duplicate of the concurrency silently reports
#: the old finish time the moment the runner's is tuned.
INDEX_DOC_BATCH = 24

#: Observed per-call latencies. Opus on a full email with attachments is the
#: slow one; Sonnet Tier-1 summaries are short and cached against the document.
OPUS_SECONDS = 14.0
TIER1_SECONDS = 6.0


def main() -> None:
    mongo = get_mongo()
    artifacts = mongo.artifacts

    emails = artifacts.count_documents({"source_type": "email"})
    segregated = artifacts.count_documents(
        {"source_type": "email", "segregation": {"$exists": True}}
    )
    pending_segregation = emails - segregated

    #: Mirrors Indexer._targets exactly. Emails carry their body in
    #: ``body_clean``, not ``text``, so querying ``text`` undercounts the work by
    #: the entire mail corpus.
    extracted = {"extraction.status": {"$in": ["complete", "partial"]}}
    target_query = {
        "$or": [
            {"source_type": "email", "body_clean": {"$exists": True, "$ne": ""}},
            {"source_type": "disk_file", **extracted},
            {"source_type": "attachment", **extracted},
        ]
    }
    has_text = artifacts.count_documents(target_query)
    chunks = mongo.chunks.count_documents({})

    print(f"\n  SEGREGATION (Opus 5, concurrency {SEGREGATE_CONCURRENCY})")
    print(f"    emails total            {emails:>7,}")
    print(f"    already assigned        {segregated:>7,}")
    print(f"    pending                 {pending_segregation:>7,}")
    seg_minutes = pending_segregation / SEGREGATE_CONCURRENCY * OPUS_SECONDS / 60
    print(f"    estimated               {seg_minutes:>7.0f} min")

    print(f"\n  INDEXING (Tier-1 per document, batches of {INDEX_DOC_BATCH})")
    print(f"    documents with text     {has_text:>7,}")
    print(f"    chunks written so far   {chunks:>7,}")
    idx_minutes = has_text / INDEX_DOC_BATCH * TIER1_SECONDS * 2 / 60
    print(f"    estimated               {idx_minutes:>7.0f} min")

    print(f"\n  TOTAL for both          {seg_minutes + idx_minutes:>7.0f} min")
    print()


if __name__ == "__main__":
    main()
