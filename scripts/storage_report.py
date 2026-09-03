"""Where the bytes actually live.

Admin constraint: large binaries must not sit in MongoDB. Atlas storage is the
expensive tier and GridFS quietly turns a 40 MB scan into 160 chunk documents in
the same database we run every query against. Originals belong in the object
store; Mongo holds text, metadata and vectors.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db
    stats = db.command("dbstats")
    print("=" * 70)
    print("STORAGE REPORT")
    print("=" * 70)
    print(f"  data {stats['dataSize']/1e6:.1f} MB   "
          f"storage {stats['storageSize']/1e6:.1f} MB   "
          f"indexes {stats.get('indexSize', 0)/1e6:.1f} MB")
    print()
    for name in sorted(db.list_collection_names()):
        s = db.command("collstats", name)
        print(f"  {name:<22}{s['count']:>8} docs "
              f"{s['size']/1e6:>9.2f} MB data "
              f"{s.get('totalIndexSize', 0)/1e6:>7.2f} MB idx")

    print("\n--- GridFS (should be empty: originals belong in the object store) ---")
    files = db["originals.files"].count_documents({})
    fchunks = db["originals.chunks"].count_documents({})
    print(f"  originals.files  {files}   originals.chunks {fchunks}")
    if files:
        for f in db["originals.files"].find(
            {}, {"filename": 1, "length": 1}
        ).sort("length", -1).limit(8):
            print(f"      {f.get('length', 0)/1e6:>8.2f} MB  {f.get('filename')}")

    print("\n--- object store on disk ---")
    root = getattr(SETTINGS, "raw_store", None)
    if root:
        from pathlib import Path
        path = Path(root)
        if path.exists():
            total = 0
            count = 0
            for item in path.rglob("*"):
                if item.is_file():
                    total += item.stat().st_size
                    count += 1
            print(f"  {path}\n      {count} objects, {total/1e6:.1f} MB")
        else:
            print(f"  {path}  (does not exist yet)")
    else:
        print("  no object_store_root configured")


if __name__ == "__main__":
    main()
