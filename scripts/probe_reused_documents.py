"""Separate genuinely reused *evidence* from signature-image noise.

The reuse leaderboard is topped by ``image001.jpg`` and ``img-<uuid>`` files
appearing in dozens of emails — those are inline signature logos that got past
the logo filter, not documents. The design question ("how do we link one
document to every email that carried it") should be answered against real
evidence, so this splits the two and sizes each.
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

#: Generic names mail clients assign to inline/signature images.
NOISE_NAME = re.compile(
    r"^(image\d+|img-[0-9a-f-]{16,}|oledata|ole\d+|logo|banner|spacer)",
    re.I,
)
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")


def is_noise(filename: str, content_type: str) -> bool:
    name = (filename or "").strip()
    if NOISE_NAME.match(name):
        return True
    # Tiny images with no meaningful name are decoration regardless of pattern.
    return name.lower().endswith(IMAGE_EXT) and len(name) <= 16


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    reused_docs, reused_noise = [], []
    for doc in art.find(
        {"source_types": "attachment", "parent_email_shas.1": {"$exists": True}},
        {"sha256": 1, "filename": 1, "content_type": 1,
         "parent_email_shas": 1, "raw_size": 1, "property_ids": 1},
    ):
        target = reused_noise if is_noise(doc.get("filename"), doc.get("content_type")) else reused_docs
        target.append(doc)

    print(f"\n  ATTACHMENTS APPEARING IN 2+ EMAILS   {len(reused_docs) + len(reused_noise):,}")
    print(f"    real documents                     {len(reused_docs):>5,}")
    print(f"    signature / inline image noise     {len(reused_noise):>5,}")

    print("\n  REAL DOCUMENTS REUSED ACROSS EMAILS")
    for doc in sorted(reused_docs, key=lambda d: -len(d["parent_email_shas"]))[:14]:
        n = len(doc["parent_email_shas"])
        kb = (doc.get("raw_size") or 0) / 1024
        name = (doc.get("filename") or "(unnamed)")[:52]
        props = ",".join(doc.get("property_ids") or []) or "-"
        print(f"    {n:>3} emails  {kb:>8,.0f} KB  {name:<54} [{props}]")

    total_noise_links = sum(len(d["parent_email_shas"]) for d in reused_noise)
    print(f"\n  NOISE STILL IN THE CORPUS")
    print(f"    reused image artifacts             {len(reused_noise):>5,}")
    print(f"    email links they account for       {total_noise_links:>5,}")
    chunks = mongo.chunks.count_documents(
        {"artifact_sha": {"$in": [d["sha256"] for d in reused_noise]}}
    )
    print(f"    chunks they produced               {chunks:>5,}")
    print()


if __name__ == "__main__":
    main()
