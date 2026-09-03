"""Field-level shape of one image artifact, and where its original actually lives.

`stored_path` came back empty on all 44 images while the object store holds 1,960
objects, so the pointer is recorded under a different name. Worth knowing exactly
which, because "the original is preserved" is a claim the dashboard will depend on.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo
from mangotree.storage.objectstore import get_object_store


def main() -> None:
    db = get_mongo().db

    doc = db["artifacts"].find_one({"relative_path": {"$regex": r"\.HEIC$"}})
    if not doc:
        doc = db["artifacts"].find_one({"relative_path": {"$regex": r"\.jpeg$"}})
    if not doc:
        print("no image artifact found")
        return

    print("=== every field on one image artifact ===")
    for key, value in sorted(doc.items()):
        preview = repr(value)
        if len(preview) > 130:
            preview = preview[:130] + f"... ({len(preview)} chars)"
        print(f"  {key:<24} {preview}")

    sha = doc.get("sha256")
    print(f"\n=== can the original be fetched back by sha? ===")
    store = get_object_store()
    try:
        data = store.get(sha)
        print(f"  YES — {len(data):,} bytes retrieved for {sha[:16]}")
        head = data[:12]
        print(f"  magic bytes: {head!r}")
    except Exception as exc:
        print(f"  NO — {type(exc).__name__}: {exc}")

    print("\n=== images with no extracted text (candidates for description) ===")
    from mangotree.extract.runner import IMAGE_EXT

    blanks = []
    for a in db["artifacts"].find(
        {"source_type": "disk_file"},
        {"sha256": 1, "relative_path": 1, "text": 1, "property_ids": 1},
    ):
        rel = a.get("relative_path") or ""
        if Path(rel).suffix.lower() in IMAGE_EXT and not (a.get("text") or "").strip():
            blanks.append(a)
    print(f"  {len(blanks)} blank images")
    for a in blanks:
        print(f"      {a.get('property_ids')}  {a.get('relative_path')}")


if __name__ == "__main__":
    main()
