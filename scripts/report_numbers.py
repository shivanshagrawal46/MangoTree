"""Exact corpus counts for the CEO report, so no figure in it is estimated."""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    artifacts = get_mongo().artifacts

    print("emails      ", artifacts.count_documents({"source_type": "email"}))
    print("attachments ", artifacts.count_documents({"source_type": "attachment"}))
    print("disk_file   ", artifacts.count_documents({"source_type": "disk_file"}))
    print("total       ", artifacts.count_documents({}))

    extractable = {"source_type": {"$in": ["attachment", "disk_file"]}}
    total = artifacts.count_documents(extractable)
    done = artifacts.count_documents({**extractable, "extraction": {"$exists": True}})
    print("extractable ", total)
    print("extracted   ", done)
    print("pending     ", total - done)

    print()
    for field in ("source", "provider", "origin"):
        values = artifacts.distinct(field, {"source_type": "email"})
        if values:
            print(f"email {field}:")
            for value in values:
                count = artifacts.count_documents({"source_type": "email", field: value})
                print(f"   {value:<24} {count:>6,}")
            break

    print()
    # Top-level ``text``: the extractor writes the body there, and ``extraction``
    # holds only status and metrics. Counting ``extraction.text`` reported zero
    # documents with text across the whole corpus.
    print("with text   ", artifacts.count_documents({**extractable, "text": {"$ne": ""}}))
    print("empty text  ", artifacts.count_documents({**extractable, "text": ""}))
    print("failed      ", artifacts.count_documents({**extractable, "extraction.method": "failed"}))

    print()
    print("pages blocked", mongo_pages_blocked(artifacts))


def mongo_pages_blocked(artifacts) -> int:
    """Pages an OCR engine refused and that no fallback has yet read."""
    return artifacts.count_documents(
        {"extraction.pages": {"$elemMatch": {"blocked": True, "text": ""}}}
    )


if __name__ == "__main__":
    main()
