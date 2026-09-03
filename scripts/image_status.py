"""What state are the image artifacts actually in?

Image support already exists — IMAGE_EXT covers HEIC, load_image_bytes decodes it
via pillow-heif, and _do_image runs vision OCR. So empty text means the images
were deferred or budgeted out, not unsupported. This says which.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.extract.runner import IMAGE_EXT
from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db

    rows = list(db["artifacts"].find(
        {"source_type": "disk_file"},
        {"sha256": 1, "filename": 1, "relative_path": 1, "text": 1,
         "extract_status": 1, "extract_reason": 1, "property_ids": 1,
         "pages": 1, "kind": 1, "stored_path": 1},
    ))

    images = [
        a for a in rows
        if Path(a.get("relative_path") or a.get("filename") or "").suffix.lower()
        in IMAGE_EXT
    ]

    print("=" * 74)
    print(f"IMAGE ARTIFACTS: {len(images)}")
    print("=" * 74)

    status: Counter = Counter()
    reasons: Counter = Counter()
    by_ext: Counter = Counter()
    with_text = 0
    for a in images:
        status[a.get("extract_status") or "(none)"] += 1
        reasons[a.get("extract_reason") or "(none)"] += 1
        by_ext[Path(a.get("relative_path") or "").suffix.lower()] += 1
        if (a.get("text") or "").strip():
            with_text += 1

    print(f"\n  with extracted text     {with_text} / {len(images)}")
    print("\n  by extract_status:")
    for key, count in status.most_common():
        print(f"      {key:<26}{count}")
    print("\n  by extract_reason:")
    for key, count in reasons.most_common():
        print(f"      {key:<40}{count}")
    print("\n  by extension:")
    for key, count in by_ext.most_common():
        print(f"      {key:<10}{count}")

    print("\n  originals present in the object store?")
    have = sum(1 for a in images if a.get("stored_path"))
    print(f"      {have} / {len(images)} have a stored_path")

    print("\n--- sample ---")
    for a in images[:15]:
        text_len = len((a.get("text") or "").strip())
        print(f"  {text_len:>6} chars  [{a.get('extract_status')}] "
              f"{a.get('relative_path')}")
        if a.get("extract_reason"):
            print(f"                reason: {a.get('extract_reason')}")

    print("\n--- is pillow-heif available? ---")
    try:
        import pillow_heif  # noqa: F401
        print("      yes")
    except ImportError as exc:
        print(f"      NO -- {exc}. HEIC files cannot be decoded without it.")

    print("\n--- likely receipts/documents among the images ---")
    keywords = ("receipt", "invoice", "permit", "bill", "estimate", "check",
                "statement", "license", "insurance")
    for a in images:
        name = (a.get("filename") or "").lower()
        if any(k in name for k in keywords):
            print(f"  {a.get('relative_path')}")


if __name__ == "__main__":
    main()
