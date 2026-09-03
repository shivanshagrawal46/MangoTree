"""Do the pages still flagged as blank actually contain text?

The blank-page report keys off the ``blocked``/``needs_human`` flags alone. If
recovery writes the recovered text but leaves those flags set, the report
overstates the loss forever. If the flags are accurate, the text really is
missing. Only the stored page records can tell the two apart.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()

    flagged_with_text = 0
    flagged_empty = 0
    samples = []

    cursor = mongo.artifacts.find(
        {"extraction.detail.vision_pages": {"$exists": True}},
        {"filename": 1, "extraction.detail.vision_pages": 1},
    )

    for doc in cursor:
        pages = ((doc.get("extraction") or {}).get("detail") or {}).get("vision_pages") or []
        for page in pages:
            if not (page.get("blocked") and page.get("needs_human")):
                continue
            length = page.get("text_len") or len(page.get("text") or "")
            if length > 0:
                flagged_with_text += 1
                if len(samples) < 8:
                    samples.append(
                        (doc.get("filename", "?"), page.get("page"), length, page.get("engine"))
                    )
            else:
                flagged_empty += 1

    print(f"\n  flagged pages that DO contain text  {flagged_with_text:>6}")
    print(f"  flagged pages genuinely empty       {flagged_empty:>6}")

    if samples:
        print("\n  examples of flagged-but-recovered pages:")
        for name, page, length, engine in samples:
            print(f"      p{page:<4} {length:>6} chars  {str(engine or '?'):<10} {str(name)[:48]}")
    print()


if __name__ == "__main__":
    main()
