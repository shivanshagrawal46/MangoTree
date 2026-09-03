"""Clear PDF extractions that used the embedded text layer.

Admin directive (2026-09-02): every PDF page goes through vision OCR. Anything
already extracted as ``native_text_layer`` or ``hybrid_native_vision`` was read
under the old rule and has pages that never reached a vision model, so those
artifacts are re-queued.

Spreadsheets, Word documents and images are untouched — they were never in scope
for the change, and re-running them would spend money to reproduce what we have.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

SUPERSEDED = ["native_text_layer", "hybrid_native_vision", "native_partial"]


def main() -> int:
    mongo = get_mongo()

    query = {
        "extraction.method": {"$in": SUPERSEDED},
        "$or": [
            {"extension": ".pdf"},
            {"filename": {"$regex": r"\.pdf$", "$options": "i"}},
            {"content_type": "application/pdf"},
        ],
    }

    affected = list(mongo.artifacts.find(query, {"filename": 1, "extraction.method": 1}))
    if not affected:
        print("\n  nothing to clear — no PDF was extracted from a text layer\n")
        return 0

    by_method = {}
    for doc in affected:
        method = (doc.get("extraction") or {}).get("method", "?")
        by_method[method] = by_method.get(method, 0) + 1

    print(f"\n  {len(affected)} PDF(s) to re-extract through vision OCR")
    for method, count in sorted(by_method.items()):
        print(f"    {method:<24} {count}")

    result = mongo.artifacts.update_many(query, {"$unset": {"extraction": ""}})
    print(f"\n  cleared {result.modified_count} extraction record(s)")
    print("  originals are untouched; only the derived text was removed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
