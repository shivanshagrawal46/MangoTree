"""Prove the legacy .doc reader works before wiring it into the pipeline.

A promissory note extracted in the wrong order, with deleted revisions mixed in,
would be worse than one that failed — so this prints enough of the real output to
judge whether the text is coherent and the amounts are right.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.extract.legacy_doc import extract_legacy_doc
from mangotree.storage.mongo import get_mongo
from mangotree.storage.objectstore import get_object_store


def main() -> None:
    db = get_mongo().db
    store = get_object_store()

    rows = list(db["artifacts"].find(
        {"source_type": "attachment", "filename": {"$regex": r"\.doc$", "$options": "i"}},
        {"filename": 1, "sha256": 1, "text": 1},
    ))
    print(f"{len(rows)} legacy .doc attachments\n")

    tmp = Path(tempfile.mkdtemp(prefix="legacydoc-"))
    for a in rows:
        sha = a.get("sha256")
        name = a.get("filename")
        try:
            data = store.get(sha)
        except Exception as exc:
            print(f"!! {name}: cannot fetch ({exc})")
            continue

        path = tmp / f"{sha[:16]}.doc"
        path.write_bytes(data)

        result = extract_legacy_doc(path)
        print("=" * 74)
        print(f"{name}")
        print(f"  method     : {result.method}")
        print(f"  pieces     : {result.pieces}")
        print(f"  confidence : {result.confidence}")
        print(f"  chars      : {len(result.text):,}")
        for warning in result.warnings:
            print(f"  WARNING    : {warning}")
        if result.text:
            money = re.findall(r"\$\s?[\d,]+\.?\d{0,2}", result.text)
            dates = re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", result.text)
            print(f"  money      : {len(money)} e.g. {money[:6]}")
            print(f"  dates      : {len(dates)} e.g. {dates[:5]}")
            print("  --- first 700 chars ---")
            print("  " + re.sub(r"\n", "\n  ", result.text[:700]))
        print()


if __name__ == "__main__":
    main()
