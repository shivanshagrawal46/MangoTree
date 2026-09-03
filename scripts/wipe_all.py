"""Full reset: drop every collection and every stored original.

Authorised by the admin on 2026-09-01 after two explicit confirmations, ahead of
the email-first rebuild described in docs/11-REBUILD-PLAN.md.

Two deliberate choices:

* Collections are **dropped**, not emptied. Dropping clears the indexes with
  them, so the rebuild recreates indexes from the current code rather than
  inheriting a stale definition that no longer matches what the code expects.

* The Atlas vector search index is dropped explicitly. It is not an ordinary
  index and does not go away with `drop()` in all Atlas configurations; leaving
  it behind pointed at a recreated collection is a subtle way to end up with a
  half-populated index that still answers queries.

Both sources of truth are external and untouched: Gmail holds every message and
the E: drive holds every file. Everything destroyed here is derived and
reproducible.

    python scripts/wipe_all.py --dry-run
    python scripts/wipe_all.py --confirm --i-understand-this-deletes-everything
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import get_logger
from mangotree.index.vector_index import VECTOR_INDEX_NAME
from mangotree.storage.mongo import get_mongo

log = get_logger()


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--i-understand-this-deletes-everything", action="store_true",
                    dest="understood")
    args = ap.parse_args()

    if not args.dry_run and not (args.confirm and args.understood):
        ap.error("a real run needs --confirm and "
                 "--i-understand-this-deletes-everything")

    mongo = get_mongo()
    db = mongo.db
    root = Path(SETTINGS.raw_store)

    collections = sorted(db.list_collection_names())
    counts = {c: db[c].count_documents({}) for c in collections}

    blobs = []
    if root.exists():
        blobs = [p for p in root.rglob("*") if p.is_file()]
    blob_bytes = sum(p.stat().st_size for p in blobs)

    print(f"database: {db.name}")
    for name, n in counts.items():
        print(f"  drop {name:<24}{n:>10} docs")
    print(f"\nobject store: {root}")
    print(f"  delete {len(blobs)} files, {human(blob_bytes)}")

    if args.dry_run:
        print("\ndry run, nothing deleted")
        return

    try:
        db["chunks"].drop_search_index(VECTOR_INDEX_NAME)
        log.info("dropped vector index '%s'", VECTOR_INDEX_NAME)
    except Exception as exc:
        log.info("vector index not dropped (%s) — it goes with the collection", exc)

    for name in collections:
        db[name].drop()
        log.info("dropped collection %s", name)

    if root.exists():
        shutil.rmtree(root)
        log.info("removed object store %s", root)
    root.mkdir(parents=True, exist_ok=True)

    remaining = db.list_collection_names()
    print("\n--- after wipe ---")
    print(f"  collections remaining  {len(remaining)}  {remaining}")
    print(f"  object store files     {len(list(root.rglob('*')))}")
    print("\nSources of truth are intact: Gmail and the E: drive are untouched.")


if __name__ == "__main__":
    main()
