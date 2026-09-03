"""End-state verification for the five-phase pass.

Checks the things that were actually changed, so the summary rests on measured
values rather than on what the scripts reported at the time they ran.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.index.vector_index import VECTOR_INDEX_DEFINITION, vector_index_status
from mangotree.storage.mongo import get_mongo


def line(label: str, value, note: str = "") -> None:
    print(f"    {label:<34} {value:>9}   {note}")


def main() -> None:
    mongo = get_mongo()
    art, chunks = mongo.artifacts, mongo.chunks
    total_chunks = chunks.count_documents({})

    print("\n  PHASE 1 — .msg ingestion")
    line("emails also tagged disk_file", art.count_documents(
        {"source_types": {"$all": ["email", "disk_file"]}}), "the 7 .msg files")
    line("msg_parser_pending still open", mongo.review_queue.count_documents(
        {"kind": "msg_parser_pending", "resolved": {"$ne": True}}))

    print("\n  PHASE 2 — resolution")
    line("artifacts with a property", art.count_documents({"property_ids": {"$ne": []}}))
    line("resolved by thread context", art.count_documents(
        {"segregation.method": "thread_context", "property_ids": {"$ne": []}}))
    line("review queue open", mongo.review_queue.count_documents({"resolved": {"$ne": True}}))
    line("review queue closed (audit kept)", mongo.review_queue.count_documents({"resolved": True}))
    line("emails still needing a human", art.count_documents(
        {"source_type": "email", "resolution_status": "needs_review"}))

    print("\n  PHASE 3 — chunk metadata")
    for field in ("scope", "date_ym", "date_year", "latest_date", "from_email",
                  "extension", "folder_path", "artifact_sha", "parent_email_shas",
                  "occurrence_count"):
        n = chunks.count_documents({field: {"$exists": True, "$nin": [None, "", []]}})
        pct = f"{100 * n / max(1, total_chunks):.0f}%"
        line(field, f"{n:,}", pct)

    filters = [f["path"] for f in VECTOR_INDEX_DEFINITION["fields"] if f["type"] == "filter"]
    print(f"\n    vector index filter fields     {len(filters)}")
    for info in vector_index_status(mongo):
        print(f"    {info['name']:<34} {info['status']}  queryable={info['queryable']}")

    print("\n  PHASE 4 — timelines")
    events = mongo.db["timeline_events"]
    line("timeline events", f"{events.count_documents({}):,}")
    line("dated", f"{events.count_documents({'occurred_at': {'$ne': None}}):,}")
    line("model-extracted", f"{events.count_documents({'extracted_by': {'$ne': 'deterministic'}}):,}")
    line("properties covered", len(events.distinct("property_id")), "of 15")

    print("\n  PHASE 5 — index hygiene")
    line("chunks", f"{total_chunks:,}")
    line("artifacts marked inline image", art.count_documents({"is_inline_image": True}))
    line("chunks from inline images", chunks.count_documents(
        {"artifact_sha": {"$in": art.distinct("sha256", {"is_inline_image": True})}}), "must be 0")

    print("\n  INTEGRITY")
    line("artifacts", f"{art.count_documents({}):,}")
    line("distinct sha256", f"{len(art.distinct('sha256')):,}", "must match")
    line("chunks embedded", f"{chunks.count_documents({'embedding_model': 'voyage-4-large'}):,}")
    line("embedding models present", str(chunks.distinct("embedding_model")))
    print()


if __name__ == "__main__":
    main()
