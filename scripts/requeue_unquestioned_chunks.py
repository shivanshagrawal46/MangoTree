"""Make chunks that were embedded without questions eligible for the night job again.

The first night-job run marked a chunk done even when the question call failed
to parse. This clears that mark on any chunk carrying an empty question list, so
``night_job_questions.py`` picks them up and retries.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.index.questions import EMBED_VERSION
from mangotree.storage.mongo import get_mongo


def main() -> int:
    chunks = get_mongo().chunks
    query = {"embed_version": EMBED_VERSION, "$or": [{"questions": []}, {"questions": {"$exists": False}}]}
    n = chunks.count_documents(query)
    r = chunks.update_many(query, {"$set": {"embed_version": f"{EMBED_VERSION}-noq"}})
    print(f"\n  requeued {r.modified_count} of {n} chunks embedded without questions\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
