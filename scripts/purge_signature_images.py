"""Remove email signature images that got past the logo filter.

``image001.jpg``, ``Outlook-<hash>.png`` and ``img-<uuid>`` files are inline
decoration from mail signatures, not evidence. The ingest filter catches most of
them, but a set slipped through — one appears in 22 emails and another was even
assigned to a property.

They matter because each one produced a chunk, and those chunks sit in the same
vector space as real documents, competing for the top-k slots on every search.
A handful of nothing is still a handful of nothing ranked against a title policy.

The artifacts are marked rather than deleted: their SHA-256 links are how we
know an email had an inline image at all, and dropping the row would make the
attachment counts on those emails wrong. Only the chunks are removed, since
those are what pollutes retrieval.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

#: Named patterns only, no size or length heuristics.
#:
#: An earlier version also treated "any image with a short filename" as noise.
#: That deleted ``IMG_3072.jpeg`` and ``IMG_3328.png`` — camera filenames, which
#: in a renovation corpus are site photos, and one of them had four chunks of
#: vision-described content across three properties. Losing real evidence is far
#: worse than leaving a few logos behind, so the rule now only fires on names
#: mail clients generate.
_NOISE_PATTERNS = (
    r"^image\d+\.(png|jpe?g|gif|bmp)$",        # image001.png — Outlook/Gmail inline
    r"^image\.(png|jpe?g|gif|bmp)$",           # bare image.png
    r"^Outlook-[a-z0-9]{6,}\.(png|jpe?g)$",    # Outlook-uy14zwfl.png
    r"^img-[0-9a-f]{8}-[0-9a-f-]{12,}",        # img-<uuid>, with or without suffix
    r"^(oledata|ole\d+|logo|banner|spacer)",
)
_NOISE = tuple(re.compile(p, re.I) for p in _NOISE_PATTERNS)

#: A camera filename is evidence, never decoration. Checked first so no pattern
#: below can claim it.
_CAMERA = re.compile(r"^(IMG|DSC|PXL|DJI|GOPR)[-_]?\d{3,}", re.I)


def is_noise(filename: str) -> bool:
    name = (filename or "").strip()
    if not name or _CAMERA.match(name):
        return False
    return any(pattern.match(name) for pattern in _NOISE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    mongo = get_mongo()
    art = mongo.artifacts

    candidates = [
        d for d in art.find(
            {"source_types": "attachment"},
            {"sha256": 1, "filename": 1, "property_ids": 1, "raw_size": 1},
        )
        if is_noise(d.get("filename"))
    ]
    shas = [d["sha256"] for d in candidates]
    chunk_count = mongo.chunks.count_documents({"artifact_sha": {"$in": shas}})

    print(f"\n  signature/inline images found  {len(candidates):>5,}")
    print(f"  chunks they produced           {chunk_count:>5,}")
    print(f"  of those carrying a property   {sum(1 for d in candidates if d.get('property_ids')):>5,}")

    print("\n  sample")
    for doc in candidates[:12]:
        n = mongo.chunks.count_documents({"artifact_sha": doc["sha256"]})
        props = ",".join(doc.get("property_ids") or []) or "-"
        print(f"    {n:>2} chunks  {(doc.get('filename') or '?')[:44]:<46} [{props}]")

    if not args.apply:
        print("\n  DRY RUN — pass --apply to remove the chunks\n")
        return 0

    removed = mongo.chunks.delete_many({"artifact_sha": {"$in": shas}})
    art.update_many(
        {"sha256": {"$in": shas}},
        {"$set": {
            "is_inline_image": True,
            "indexing": {
                "excluded": True,
                "reason": "email signature / inline image, not evidence",
                "excluded_at": datetime.now(timezone.utc),
            },
        }},
    )

    print(f"\n  chunks removed                 {removed.deleted_count:>5,}")
    print(f"  artifacts marked inline        {len(shas):>5,}")
    print(f"  chunks remaining               {mongo.chunks.count_documents({}):>5,}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
