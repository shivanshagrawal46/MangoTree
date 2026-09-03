"""Clear extraction state on artifacts whose failure a code fix has now addressed.

Scoped deliberately narrowly. `deferred` is a legitimate terminal state for video
and zip archives, so it is never reset in bulk — only the specific artifacts named
here, whose deferral was caused by unroutable extensions rather than by policy.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

APPLY = "--apply" in sys.argv


def main() -> None:
    db = get_mongo().db

    # Legacy .doc files that failed under python-docx, now handled by the OLE2
    # piece-table reader.
    legacy = list(db["artifacts"].find(
        {"source_type": "attachment",
         "filename": {"$regex": r"\.doc$", "$options": "i"},
         "extraction.status": "failed"},
        {"filename": 1, "sha256": 1},
    ))

    # Inline images with no extension and an application/octet-stream MIME type,
    # deferred as unroutable; magic-byte sniffing now identifies them.
    inline = list(db["artifacts"].find(
        {"source_type": "attachment",
         "extraction.status": "deferred",
         "filename": {"$regex": "^img-", "$options": "i"}},
        {"filename": 1, "sha256": 1},
    ))

    print(f"legacy .doc to retry : {len(legacy)}")
    for a in legacy:
        print(f"    {a.get('filename')}")
    print(f"inline images to retry: {len(inline)}")
    for a in inline:
        print(f"    {a.get('filename')}")

    if not APPLY:
        print("\n(dry run — pass --apply to reset)")
        return

    shas = [a["sha256"] for a in (legacy + inline)]
    if shas:
        result = db["artifacts"].update_many(
            {"sha256": {"$in": shas}},
            {"$unset": {"extraction": ""}},
        )
        print(f"\nreset {result.modified_count} artifacts; run "
              f"`python -m mangotree.cli extract --yes` to re-extract")


if __name__ == "__main__":
    main()
