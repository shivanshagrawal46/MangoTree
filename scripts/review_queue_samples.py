"""Show what a person would actually be looking at in the review queue.

Counts alone do not tell anyone whether the queue is an afternoon of work or a
week of it. Real examples do, so this prints a handful of each kind together
with the state of the artifact behind it.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

KINDS = [
    "property_resolution",
    "property_unresolved",
    "property_low_confidence",
    "msg_parser_pending",
]


def short(text: str, width: int = 92) -> str:
    text = " ".join(str(text or "").split())
    return text[: width - 1] + "\u2026" if len(text) > width else text


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    for kind in KINDS:
        total = mongo.review_queue.count_documents({"kind": kind})
        print(f"\n{'=' * 96}")
        print(f"  {kind}   —   {total:,} entries")
        print("=" * 96)

        for entry in mongo.review_queue.find({"kind": kind}).limit(4):
            sha = entry.get("artifact_sha") or ""
            doc = art.find_one({"sha256": sha}) or {}
            seg = doc.get("segregation") or {}

            label = doc.get("subject") or doc.get("filename") or entry.get("subject") or "(none)"
            print(f"\n  item      {short(label)}")
            print(f"    type    {doc.get('source_type') or 'no artifact (disk path key)'}")
            print(f"    now has {doc.get('property_ids')}")
            if entry.get("candidates"):
                guesses = [
                    f"{c.get('property_id')} @ {c.get('confidence')}"
                    for c in entry["candidates"][:3]
                ]
                print(f"    matcher guessed {guesses}")
            if entry.get("note"):
                print(f"    note    {short(entry['note'])}")
            if seg.get("reasoning"):
                print(f"    opus    {short(seg['reasoning'])}")
            if seg.get("confidence") is not None:
                print(f"    conf    {seg.get('confidence')}   fallback={seg.get('fallback_used')}")

    # The headline question: how many legacy entries are already answered?
    legacy = list(mongo.review_queue.find({"kind": "property_resolution"}, {"artifact_sha": 1}))
    shas = [e.get("artifact_sha") for e in legacy if e.get("artifact_sha")]
    answered = art.count_documents(
        {"sha256": {"$in": shas}, "property_ids": {"$ne": []}, "segregation": {"$exists": True}}
    )
    still_blank = art.count_documents(
        {"sha256": {"$in": shas}, "property_ids": [], "segregation": {"$exists": True}}
    )
    print(f"\n{'=' * 96}")
    print("  LEGACY property_resolution ENTRIES — did Opus 5 already answer them?")
    print("=" * 96)
    print(f"    entries with an artifact           {len(shas):>7,}")
    print(f"    Opus 5 gave it a property          {answered:>7,}   <- queue entry is answered")
    print(f"    Opus 5 saw it and said 'no property'{still_blank:>7,}   <- deliberate, not pending")
    print()


if __name__ == "__main__":
    main()
