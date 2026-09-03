"""Describe the images that OCR left empty, and index the descriptions.

Only touches images with no extracted text — an image whose text OCR already read
(a W-9, a wiring instruction, a receipt) has real evidence attached and does not
need a model's impression of it.

Descriptions are written to `vision_description`, never to `text`, and indexed as
chunks that announce themselves as model-generated.

    python scripts/describe_images.py            # report what would be done
    python scripts/describe_images.py --apply    # describe and store
    python scripts/describe_images.py --apply --index   # also index as chunks
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.core.hashing import sha256_text
from mangotree.extract.image_describe import (
    DESCRIPTION_NOTICE, DescribeStats, ImageDescriber, description_record,
    retrievable_description,
)
from mangotree.extract.ocr import load_image_bytes
from mangotree.extract.runner import IMAGE_EXT
from mangotree.storage.mongo import get_mongo
from mangotree.storage.objectstore import get_object_store

APPLY = "--apply" in sys.argv
DO_INDEX = "--index" in sys.argv


def main() -> None:
    mongo = get_mongo()
    db = mongo.db
    store = get_object_store()

    candidates = []
    for a in db["artifacts"].find(
        {"source_type": "disk_file"},
        {"sha256": 1, "filename": 1, "relative_path": 1, "text": 1,
         "property_ids": 1, "object_path": 1, "vision_description": 1,
         "source_paths": 1, "date": 1, "doc_class": 1},
    ):
        rel = a.get("relative_path") or ""
        if Path(rel).suffix.lower() not in IMAGE_EXT:
            continue
        if (a.get("text") or "").strip():
            continue
        if (a.get("vision_description") or "").strip():
            continue
        candidates.append(a)

    print(f"{len(candidates)} images with no text and no description\n")
    for a in candidates:
        print(f"  {a.get('property_ids')}  {a.get('relative_path')}")

    if not APPLY:
        print("\n(report only — pass --apply to describe)")
        return

    describer = ImageDescriber(SETTINGS.anthropic_api_key)
    stats = DescribeStats()

    jobs = []
    for a in candidates:
        sha = a.get("sha256")
        data = None
        # Prefer the object store; fall back to the source path if an original
        # predates the store migration.
        try:
            data = store.get(sha)
        except Exception:
            for candidate_path in (a.get("source_paths") or []):
                path = Path(candidate_path)
                if path.exists():
                    data = path.read_bytes()
                    break
        if data is None:
            print(f"  !! original unreachable: {a.get('relative_path')}")
            continue

        # Normalise to JPEG. HEIC in particular cannot be sent to the API raw,
        # and this is the same helper the OCR path uses.
        try:
            image_bytes = load_image_bytes(Path(a.get("source_paths", [""])[0])) \
                if Path(a.get("relative_path") or "").suffix.lower() in {".heic", ".heif"} \
                else data
        except Exception:
            image_bytes = data

        jobs.append({
            "sha": sha,
            "bytes": image_bytes,
            "hint": a.get("filename") or "",
        })

    print(f"\ndescribing {len(jobs)} images on {describer.model}...")
    results = describer.describe_many(jobs, stats=stats)

    lookup = {a["sha256"]: a for a in candidates}
    for sha, text in results.items():
        db["artifacts"].update_one(
            {"sha256": sha},
            {"$set": description_record(text, describer.model)},
        )
        a = lookup.get(sha, {})
        print(f"\n  {a.get('relative_path')}")
        print(f"      {text[:300]}")

    print("\n=== description stats ===")
    for key, value in stats.as_dict().items():
        print(f"  {key:<18}{value}")

    if not DO_INDEX:
        print("\n(descriptions stored; pass --index to also make them searchable)")
        return

    # Index descriptions as their own chunks so a photo becomes findable, while
    # the notice keeps its status unambiguous in any answer that cites it.
    from mangotree.embed.embedder import Embedder

    embedder = Embedder(SETTINGS.voyage_api_key)

    # Pick up every stored description that has no chunk yet, not only the ones
    # written in this run — an indexing failure mid-run must not leave
    # descriptions permanently unsearchable.
    already = set(db["chunks"].distinct(
        "artifact_sha", {"chunk_kind": "image_description"}
    ))
    to_index: dict[str, dict] = {}
    for a in db["artifacts"].find(
        {"source_type": "disk_file",
         "vision_description": {"$exists": True, "$ne": ""}},
        {"sha256": 1, "filename": 1, "relative_path": 1, "property_ids": 1,
         "vision_description": 1, "description_model": 1, "date": 1,
         "doc_class": 1},
    ):
        if a["sha256"] in already:
            continue
        to_index[a["sha256"]] = a

    print(f"\n{len(to_index)} descriptions awaiting indexing")

    records = []
    for sha, a in to_index.items():
        text = a.get("vision_description") or ""
        body = retrievable_description(
            text, a.get("description_model") or describer.model
        )
        # chunk_id must follow the indexer's own convention, both to satisfy the
        # unique index and so a re-run upserts onto itself instead of duplicating.
        records.append({
            "chunk_id": sha256_text(f"{sha}:desc:0:{body[:200]}")[:24],
            "artifact_sha": sha,
            "ordinal": 0,
            "text": body,
            "embed_text": body,
            "context": DESCRIPTION_NOTICE,
            "property_ids": a.get("property_ids") or [],
            "source_ref": a.get("relative_path") or "",
            "source_name": a.get("filename") or "",
            "display_name": a.get("filename") or "",
            "doc_class": a.get("doc_class") or "site_photo",
            "source_type": "disk_file",
            "date": a.get("date"),
            "privileged": False,
            "is_model_generated": True,
            "chunk_kind": "image_description",
        })

    if records:
        vectors = embedder.embed_documents([r["embed_text"] for r in records])
        kept = []
        for record, vector in zip(records, vectors):
            if vector is None:
                print(f"  embed failed, skipping: {record['source_name']}")
                continue
            record["embedding"] = vector
            kept.append(record)
        if kept:
            from pymongo import UpdateOne

            db["chunks"].bulk_write(
                [UpdateOne({"chunk_id": r["chunk_id"]}, {"$set": r}, upsert=True)
                 for r in kept],
                ordered=False,
            )
        print(f"\nindexed {len(kept)} image-description chunks")


if __name__ == "__main__":
    main()
