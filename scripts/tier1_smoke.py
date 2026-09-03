"""Smoke test Tier 1 on real documents and show the before/after context."""
from mangotree.chunk.chunker import chunk_artifact
from mangotree.config.registry import PROPERTY_INDEX
from mangotree.config.settings import SETTINGS
from mangotree.context.tier1 import Tier1Stats, Tier1Writer
from mangotree.context.tier2 import build_embedded_context, build_tier2
from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db
    writer = Tier1Writer(SETTINGS.anthropic_api_key)
    stats = Tier1Stats()

    targets = list(
        db.artifacts.find(
            {
                "source_type": "disk_file",
                "extraction.status": "complete",
                "text": {"$exists": True},
                "property_ids.0": {"$exists": True},
            },
            {"sha256": 1, "filename": 1, "text": 1, "doc_class": 1,
             "property_ids": 1, "date": 1},
        ).sort("raw_size", -1).limit(2)
    )

    for doc in targets:
        pids = doc["property_ids"]
        prop = PROPERTY_INDEX.get(pids[0])
        chunks = chunk_artifact(
            doc["text"], artifact_sha=doc["sha256"],
            property_ids=pids, default_ref="document",
        )
        chunks = chunks[:4]
        print("\n" + "=" * 96)
        print(f"DOCUMENT: {doc['filename']}")
        print(f"  class={doc.get('doc_class')}  property={prop.canonical_address if prop else pids}"
              f"  deal={prop.deal_type if prop else '?'}")
        print(f"  doc chars={len(doc['text']):,}  chunks tested={len(chunks)}")

        summaries = writer.write_for_document(
            document_text=doc["text"],
            chunk_texts=[c.text for c in chunks],
            meta={
                "display_name": doc["filename"],
                "doc_class": doc.get("doc_class"),
                "property_label": prop.canonical_address if prop else None,
                "deal_type": prop.deal_type if prop else None,
                "date": doc.get("date"),
            },
            stats=stats,
        )

        for chunk, tier1 in zip(chunks, summaries):
            tier2 = build_tier2(
                property_ids=chunk.property_ids or pids,
                doc_class=doc.get("doc_class"),
                display_name=doc["filename"],
                date=doc.get("date"),
                source_ref=chunk.source_ref,
            )
            print("\n  " + "-" * 92)
            print(f"  CHUNK (raw, first 200 chars):\n    {chunk.text[:200]!r}")
            print(f"\n  TIER 1 (Sonnet 5):\n    {tier1 or '(none)'}")
            print(f"\n  TIER 2 (templated):\n    {tier2}")
            print(f"\n  EMBEDDED CONTEXT:\n    {build_embedded_context(tier1, tier2)[:400]}")

    print("\n" + "=" * 96)
    print("TIER 1 STATS:", stats.as_dict())
    print("calls:", writer.calls)


if __name__ == "__main__":
    main()
