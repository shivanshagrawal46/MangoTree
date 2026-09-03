"""What format are the failing .doc attachments really in?

python-docx reported "content type is themeManager+xml", which means it opened an
OOXML zip and could not find the main document part. That points at a renamed or
unusually-structured .docx rather than a legacy binary .doc — and the two need
completely different handling, so guessing would waste the fix.
"""
from __future__ import annotations

import sys
import zipfile
from io import BytesIO

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo
from mangotree.storage.objectstore import get_object_store

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP = b"PK\x03\x04"


def main() -> None:
    db = get_mongo().db
    store = get_object_store()

    rows = list(db["artifacts"].find(
        {"source_type": "attachment",
         "$or": [{"text": None}, {"text": ""}, {"text": {"$exists": False}}]},
        {"filename": 1, "sha256": 1, "content_type": 1, "raw_size": 1},
    ))

    for a in rows:
        sha = a.get("sha256")
        print("-" * 74)
        print(f"{a.get('filename')}")
        print(f"  declared content_type: {a.get('content_type')}")
        print(f"  size: {a.get('raw_size'):,}" if a.get("raw_size") else "  size: ?")
        try:
            data = store.get(sha)
        except Exception as exc:
            print(f"  !! cannot fetch: {exc}")
            continue

        head = data[:8]
        print(f"  magic: {head!r}")
        if head.startswith(OLE2):
            print("  -> LEGACY BINARY .doc (OLE2 compound file)")
            print("     python-docx cannot read this; needs antiword/LibreOffice")
        elif head.startswith(ZIP):
            print("  -> OOXML zip")
            try:
                with zipfile.ZipFile(BytesIO(data)) as zf:
                    names = zf.namelist()
                    print(f"     {len(names)} entries")
                    interesting = [
                        n for n in names
                        if n.endswith(".xml") and ("document" in n or "content" in n.lower())
                    ]
                    print(f"     document-ish parts: {interesting[:8]}")
                    has_doc = "word/document.xml" in names
                    print(f"     word/document.xml present: {has_doc}")
                    if not has_doc:
                        print(f"     first 15 entries: {names[:15]}")
            except Exception as exc:
                print(f"     !! zip read failed: {exc}")
        else:
            print(f"  -> unknown container; first 60 bytes: {data[:60]!r}")


if __name__ == "__main__":
    main()
