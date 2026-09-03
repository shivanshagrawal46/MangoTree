"""Re-queue artifacts whose extraction failed or produced nothing.

A failed extraction still writes a record, which makes the next run skip it —
the failure would otherwise be permanent and invisible.
"""
import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

mongo = get_mongo()

query = {
    "extraction": {"$exists": True},
    "$or": [
        {"extraction.detail.engine": "failed"},
        {"extraction.status": "failed"},
        {"extraction.method": "failed"},
    ],
}

docs = list(mongo.artifacts.find(query, {"filename": 1, "extraction": 1}))
print(f"\n  {len(docs)} failed extraction(s) to re-queue")
for name, count in Counter(
    str(d.get("filename", "?")).rsplit(".", 1)[-1].lower() for d in docs
).most_common():
    print(f"      .{name:<10} {count}")

if docs:
    result = mongo.artifacts.update_many(query, {"$unset": {"extraction": ""}})
    print(f"\n  cleared {result.modified_count} record(s)\n")
else:
    print()
