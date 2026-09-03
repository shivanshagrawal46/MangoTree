"""Close the .msg-parser-pending notes now that the parser exists.

These were raised by disk ingestion as a deliberate marker — "this file was seen
but cannot be read yet" — so they were never a human decision, just a visible
placeholder. All seven files have since been converted and matched against the
corpus, so the placeholder is answered.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    result = mongo.review_queue.update_many(
        {"kind": "msg_parser_pending"},
        {"$set": {
            "resolved": True,
            "status": "closed",
            "resolved_at": datetime.now(timezone.utc),
            "resolved_by": "msg_parser",
            "resolution": "parsed and matched to an email already in the corpus",
        }},
    )
    print(f"\n  msg_parser_pending closed  {result.modified_count}")
    print(f"  review queue still open    {mongo.review_queue.count_documents({'resolved': {'$ne': True}}):,}")
    print(f"  review queue total (audit) {mongo.review_queue.count_documents({}):,}\n")


if __name__ == "__main__":
    main()
