"""Document-level vectors for the hierarchical channel (C9).

One vector per artifact, built from what the document *is* rather than any one
passage: its display name, the Tier-2 document-in-deal context, its type, and
the opening of its text. Searching these finds the right document first; the
pipeline then pulls that document's best chunks. It catches the case chunk
search misses — a question about a document as a whole, where no single chunk
is a strong match but the document plainly is.

Cheap: a few thousand short texts through voyage-4-large, one small vector
index. Re-runnable; artifacts already summarised with this version are skipped.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, ".")

from pymongo import UpdateOne
from pymongo.operations import SearchIndexModel

from mangotree.config.models import EMBEDDING_DIM, EMBEDDING_MODEL
from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.embed.embedder import Embedder
from mangotree.retrieve.channels import DOC_SUMMARY_COLLECTION, DOC_SUMMARY_INDEX
from mangotree.storage.mongo import get_mongo

VERSION = "docsum-v1"
BATCH = 64

INDEX_DEF = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": EMBEDDING_DIM, "similarity": "cosine"},
        {"type": "filter", "path": "property_ids"},
        {"type": "filter", "path": "placement"},
        {"type": "filter", "path": "privileged"},
        {"type": "filter", "path": "source_type"},
        {"type": "filter", "path": "doc_class"},
        {"type": "filter", "path": "date"},
        {"type": "filter", "path": "extension"},
        {"type": "filter", "path": "from_email"},
    ]
}


def main() -> int:
    mongo = get_mongo()
    coll = mongo.db[DOC_SUMMARY_COLLECTION]
    coll.create_index("artifact_sha", unique=True, name="ux_docsum_sha")
    done = {d["artifact_sha"] for d in coll.find({"version": VERSION}, {"artifact_sha": 1})}

    # One representative row per artifact: its first two chunks.
    pipeline = [
        {"$sort": {"artifact_sha": 1, "ordinal": 1}},
        {"$group": {
            "_id": "$artifact_sha",
            "display_name": {"$first": "$display_name"},
            "tier2": {"$first": "$tier2"},
            "doc_class": {"$first": "$doc_class"},
            "property_ids": {"$first": "$property_ids"},
            "placement": {"$first": "$placement"},
            "privileged": {"$first": "$privileged"},
            "source_type": {"$first": "$source_type"},
            "date": {"$first": "$date"},
            "extension": {"$first": "$extension"},
            "from_email": {"$first": "$from_email"},
            "texts": {"$push": "$text"},
            "n": {"$sum": 1},
        }},
    ]
    rows = [r for r in mongo.chunks.aggregate(pipeline, allowDiskUse=True) if r["_id"] not in done]
    logger.info("doc summaries: %d artifacts to embed (%d already done)", len(rows), len(done))

    embedder = Embedder(SETTINGS.voyage_api_key)
    started = time.time()
    written = 0
    for start in range(0, len(rows), BATCH):
        batch = rows[start:start + BATCH]
        texts = []
        for r in batch:
            opening = " ".join(t for t in (r.get("texts") or [])[:2] if t)[:2000]
            parts = [r.get("display_name") or "", r.get("tier2") or ""]
            if r.get("doc_class"):
                parts.append(f"type: {r['doc_class']}")
            parts.append(opening)
            texts.append("\n".join(p for p in parts if p).strip() or (r.get("display_name") or "document"))
        vectors = embedder.embed_documents(texts)
        ops = []
        now = datetime.now(timezone.utc)
        for r, vec in zip(batch, vectors):
            if vec is None:
                continue
            ops.append(UpdateOne(
                {"artifact_sha": r["_id"]},
                {"$set": {
                    "artifact_sha": r["_id"], "display_name": r.get("display_name"),
                    "doc_class": r.get("doc_class"), "property_ids": r.get("property_ids") or [],
                    "placement": r.get("placement"), "privileged": bool(r.get("privileged")),
                    "source_type": r.get("source_type"), "date": r.get("date"),
                    "extension": r.get("extension"), "from_email": r.get("from_email"),
                    "chunks": r.get("n"), "embedding": vec, "embedding_model": EMBEDDING_MODEL,
                    "version": VERSION, "updated_at": now,
                }},
                upsert=True,
            ))
        if ops:
            coll.bulk_write(ops, ordered=False)
            written += len(ops)
        if (start // BATCH) % 10 == 0:
            rate = written / max(1e-6, time.time() - started)
            logger.info("  %d/%d  %.0f/s", written, len(rows), rate)

    existing = {i["name"] for i in coll.list_search_indexes()}
    if DOC_SUMMARY_INDEX not in existing:
        coll.create_search_index(SearchIndexModel(definition=INDEX_DEF, name=DOC_SUMMARY_INDEX, type="vectorSearch"))
        logger.info("vector index '%s' requested", DOC_SUMMARY_INDEX)
    else:
        coll.update_search_index(DOC_SUMMARY_INDEX, INDEX_DEF)
        logger.info("vector index '%s' updated", DOC_SUMMARY_INDEX)

    while True:
        info = next((i for i in coll.list_search_indexes() if i["name"] == DOC_SUMMARY_INDEX), None)
        if info and info.get("status") == "READY" and info.get("queryable"):
            break
        time.sleep(10)

    print(f"\n  DOC SUMMARIES  written={written:,}  total={coll.count_documents({}):,}  index READY")
    print(f"  tokens={embedder.stats.total_tokens:,}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
