"""One-screen view of where the corpus stands. Safe to run mid-pipeline."""
import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

mongo = get_mongo()

emails = mongo.artifacts.count_documents({"source_type": "email"})
attachments = mongo.artifacts.count_documents({"source_type": "attachment"})
extracted = mongo.artifacts.count_documents({"extraction": {"$exists": True}})
pending = mongo.artifacts.count_documents(
    {"source_type": "attachment", "extraction": {"$exists": False}}
)
chunks = mongo.chunks.count_documents({})
review = mongo.review_queue.count_documents({})

print(f"\n  emails            {emails:>7,}")
print(f"  attachments       {attachments:>7,}")
print(f"  extracted         {extracted:>7,}")
print(f"  awaiting extract  {pending:>7,}")
print(f"  chunks            {chunks:>7,}")
print(f"  review queue      {review:>7,}")

errors = list(mongo.errors.find({}, {"stage": 1, "error": 1}))
print(f"\n  dead-lettered     {len(errors):>7,}")
if errors:
    for (stage, error), count in Counter(
        (e.get("stage", "?"), e.get("error", "")[:58]) for e in errors
    ).most_common(12):
        print(f"      {count:>4}  {stage:<18} {error}")

methods = Counter(
    (d.get("extraction") or {}).get("method", "?")
    for d in mongo.artifacts.find({"extraction": {"$exists": True}}, {"extraction.method": 1})
)
print("\n  extraction methods")
for method, count in methods.most_common():
    print(f"      {count:>5}  {method}")
print()
