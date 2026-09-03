"""Everything a human needs to decide what happens next, in one pass.

Written for the handover after the first full run: what landed, what is queued
for a person, and where the property coverage actually sits.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    art, chunks = mongo.artifacts, mongo.chunks

    out: dict = {}

    out["corpus"] = {
        "artifacts": art.count_documents({}),
        "distinct_sha": len(art.distinct("sha256")),
        "emails": art.count_documents({"source_type": "email"}),
        "attachments": art.count_documents({"source_type": "attachment"}),
        "disk_files": art.count_documents({"source_type": "disk_file"}),
        "collapsed_both_origins": art.count_documents(
            {"source_types": {"$all": ["attachment", "disk_file"]}}
        ),
    }

    out["decisions"] = {
        "emails_decided": art.count_documents(
            {"source_type": "email", "segregation": {"$exists": True}}
        ),
        "attachments_decided": art.count_documents(
            {"source_type": "attachment", "segregation": {"$exists": True}}
        ),
        "filed_to_property": art.count_documents({"property_ids": {"$ne": []}}),
        "common_store": art.count_documents({"property_ids": []}),
    }

    out["index"] = {
        "chunks": chunks.count_documents({}),
        "embedded": chunks.count_documents({"embedding_model": "voyage-4-large"}),
        "with_entities": chunks.count_documents({"entity_ids": {"$ne": []}}),
        "entities": mongo.db["entities"].count_documents({}),
        "edges": mongo.db["entity_edges"].count_documents({}),
    }

    queue = Counter(
        d.get("kind") or d.get("reason") or "?"
        for d in mongo.review_queue.find({}, {"kind": 1, "reason": 1})
    )
    out["review_queue"] = {"total": sum(queue.values()), "by_kind": dict(queue.most_common())}

    unresolved = art.count_documents({"resolution_status": "needs_review"})
    out["review_queue"]["artifacts_needing_review"] = unresolved

    # Extraction gaps: what never produced text, and why.
    gaps = Counter()
    for doc in art.find(
        {"source_type": {"$in": ["disk_file", "attachment"]},
         "extraction.status": {"$ne": "complete"}},
        {"extraction": 1, "filename": 1},
    ):
        extraction = doc.get("extraction") or {}
        gaps[f"{extraction.get('status')}: {(extraction.get('reason') or extraction.get('method') or '')[:40]}"] += 1
    out["extraction_gaps"] = dict(gaps.most_common())

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
