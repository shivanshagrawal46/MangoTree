"""Show the recorded ingestion errors exactly as stored."""
import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

mongo = get_mongo()
for doc in mongo.errors.find().limit(10):
    doc.pop("_id", None)
    print()
    for key, value in doc.items():
        print(f"  {key:<16} {str(value)[:150]}")
print()
