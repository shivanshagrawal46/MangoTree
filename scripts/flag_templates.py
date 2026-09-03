"""Scan the corpus for blank templates and flag them, in place.

Templates stay retrievable (admin decision). This sets `is_template`,
`template_confidence` and `template_signals` on the artifact, and prefixes the
notice onto every one of its chunks' embedded context so the caveat travels with
the text into retrieval rather than sitting in a field nobody renders.

Run with --apply to write; without it, reports only.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.resolve.template_detector import (
    TEMPLATE_NOTICE, detect_template,
)
from mangotree.storage.mongo import get_mongo

APPLY = "--apply" in sys.argv


def main() -> None:
    db = get_mongo().db

    artifacts = list(db["artifacts"].find(
        {"text": {"$exists": True, "$ne": ""}},
        {"sha256": 1, "filename": 1, "text": 1, "doc_class": 1,
         "relative_path": 1, "property_ids": 1},
    ))
    print(f"scanning {len(artifacts)} artifacts with text\n")

    flagged = []
    for a in artifacts:
        verdict = detect_template(
            a.get("filename") or "",
            a.get("text") or "",
            doc_class=a.get("doc_class") or "",
        )
        if verdict.is_template:
            flagged.append((a, verdict))

    print("=" * 78)
    print(f"TEMPLATES DETECTED: {len(flagged)}")
    print("=" * 78)
    for a, verdict in sorted(flagged, key=lambda t: -t[1].confidence):
        print(f"\n  {verdict.confidence:.2f}  {a.get('filename')}")
        print(f"        path  : {a.get('relative_path')}")
        print(f"        props : {a.get('property_ids')}")
        print(f"        why   : {verdict.reason}")
        chunk_count = db["chunks"].count_documents({"artifact_sha": a.get("sha256")})
        print(f"        chunks: {chunk_count}")

    if not APPLY:
        print("\n(report only — pass --apply to write flags)")
        return

    print("\napplying flags...")
    for a, verdict in flagged:
        sha = a.get("sha256")
        db["artifacts"].update_one({"sha256": sha}, {"$set": {
            "is_template": True,
            "template_confidence": verdict.confidence,
            "template_signals": verdict.signals,
        }})

        # Prefix the notice onto the embedded context of every chunk. Prepended,
        # not appended: a truncated read still sees the warning.
        updated = 0
        for chunk in db["chunks"].find(
            {"artifact_sha": sha},
            {"_id": 1, "embed_text": 1, "context": 1, "tier1": 1},
        ):
            embed_text = chunk.get("embed_text") or ""
            if embed_text.startswith(TEMPLATE_NOTICE):
                continue
            db["chunks"].update_one({"_id": chunk["_id"]}, {"$set": {
                "is_template": True,
                "embed_text": f"{TEMPLATE_NOTICE}\n{embed_text}",
                "context": f"{TEMPLATE_NOTICE} {chunk.get('context') or ''}".strip(),
            }})
            updated += 1
        print(f"  {a.get('filename')}: artifact flagged, {updated} chunks annotated")

    print("\nNote: annotated chunks keep their existing vectors. Re-run "
          "`index --reindex` to re-embed with the notice included if you want "
          "the flag to influence similarity as well as display.")


if __name__ == "__main__":
    main()
