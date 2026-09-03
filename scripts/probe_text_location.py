"""Where does extracted text actually live on an artifact?

``_attachments_of`` reads ``extraction.text``. If the extractor writes somewhere
else, every attachment rides along with an empty body and Opus 5 is handed a
filename instead of a document.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def describe(doc: dict) -> None:
    print(f"\n    {doc.get('filename')}")
    print(f"      source_types      {doc.get('source_types')}")
    print(f"      extraction.status {(doc.get('extraction') or {}).get('status')}")
    for field in ("text", "text_len", "body_clean"):
        value = doc.get(field)
        if value is not None:
            shown = len(value) if isinstance(value, str) else value
            print(f"      {field:<17} {shown}")
    extraction = doc.get("extraction") or {}
    for key, value in extraction.items():
        shown = f"<{len(value)} chars>" if isinstance(value, str) and len(value) > 60 else value
        print(f"      extraction.{key:<7} {shown}")


def main() -> None:
    art = get_mongo().artifacts

    print("  COLLAPSED (disk + mail)")
    for doc in art.find({"source_types": {"$all": ["attachment", "disk_file"]}}).limit(2):
        describe(doc)

    print("\n  PURE ATTACHMENT (for comparison)")
    for doc in art.find({"source_types": ["attachment"], "extraction.status": "complete"}).limit(2):
        describe(doc)

    print("\n  COUNTS")
    for label, query in (
        ("extraction.text non-empty", {"extraction.text": {"$exists": True, "$ne": ""}}),
        ("top-level text non-empty", {"text": {"$exists": True, "$ne": ""}}),
    ):
        total = art.count_documents(query)
        collapsed = art.count_documents({**query, "source_types": {"$all": ["attachment", "disk_file"]}})
        print(f"    {label:<28} all={total:>6,}  collapsed={collapsed:>4,}")
    print()


if __name__ == "__main__":
    main()
