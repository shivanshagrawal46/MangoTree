"""Exactly what a full wipe would destroy. Read-only.

Enumerates every collection actually present rather than a hard-coded list, so
a collection created at some point and since forgotten cannot survive the wipe
and quietly poison the rebuilt corpus with stale records.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> None:
    mongo = get_mongo()
    db = mongo.db

    print(f"database: {db.name}\n")
    print(f"{'collection':<26}{'docs':>10}")
    print("-" * 36)

    total = 0
    for name in sorted(db.list_collection_names()):
        n = db[name].count_documents({})
        total += n
        print(f"  {name:<24}{n:>10}")
    print("-" * 36)
    print(f"  {'TOTAL':<24}{total:>10}")

    print("\nsearch indexes on chunks (the vector database):")
    try:
        for info in db["chunks"].list_search_indexes():
            print(f"  {info.get('name')}  type={info.get('type')}  "
                  f"queryable={info.get('queryable')}")
    except Exception as exc:
        print(f"  could not list: {exc}")

    root = Path(SETTINGS.raw_store)
    if root.exists():
        blobs = [p for p in root.rglob("*")
                 if p.is_file() and not p.name.endswith(".meta.json")]
        size = sum(p.stat().st_size for p in blobs)
        print(f"\nobject store: {root}")
        print(f"  stored originals  {len(blobs)}")
        print(f"  on disk           {human(size)}")
    else:
        print(f"\nobject store: {root} (missing)")

    print("\n--- rebuild cost after a total wipe ---")
    pages = db["extractions"].count_documents({"engine": {"$regex": "vision|claude|gpt"}})
    print(f"  vision-OCR'd pages to redo   ~{pages}")
    print(f"  chunks to re-summarise       {db['chunks'].count_documents({})}")
    print(f"  chunks to re-embed           {db['chunks'].count_documents({})}")
    print("  E: drive corpus is the source of truth and is untouched on disk")


if __name__ == "__main__":
    main()
