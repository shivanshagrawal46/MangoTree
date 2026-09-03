"""Three questions the admin asked, answered from the database rather than memory.

1. Did the E: drive corpus actually go through vision OCR?
2. Was it deduplicated against attachments already held from email?
3. Will Opus 5 see every attachment and disk file, or only emails?

The third is the one that can silently under-deliver: if segregation only walks
emails, then any document that never arrived as an attachment — the entire disk
corpus — never gets a property decision from the model.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    mongo = get_mongo()
    artifacts = mongo.artifacts

    print("\n=== 1. OCR coverage of the E: drive corpus ===")
    disk_methods = Counter(
        (d.get("extraction") or {}).get("method", "NOT EXTRACTED")
        for d in artifacts.find({"source_type": "disk_file"}, {"extraction.method": 1})
    )
    for method, count in disk_methods.most_common():
        print(f"    {method:<28} {count:>5}")

    disk_vision_pages = 0
    for doc in artifacts.find(
        {"source_type": "disk_file"}, {"extraction.detail.vision_pages": 1}
    ):
        pages = ((doc.get("extraction") or {}).get("detail") or {}).get("vision_pages") or []
        disk_vision_pages += len(pages)
    print(f"    vision pages read from disk files: {disk_vision_pages:,}")

    print("\n=== 2. Deduplication between disk and email ===")
    #: A file that arrived both by email and on disk shares one artifact, so the
    #: proof of dedup is an artifact carrying both provenances at once.
    both = artifacts.count_documents(
        {"source_paths": {"$exists": True, "$ne": []}, "parent_email_shas": {"$exists": True, "$ne": []}}
    )
    print(f"    artifacts holding BOTH a disk path and an email parent: {both:,}")
    print("    (each of these is one stored copy of a file that arrived twice)")

    total_sha = len(artifacts.distinct("sha256"))
    total_docs = artifacts.count_documents({})
    print(f"    distinct sha256 {total_sha:,} vs {total_docs:,} artifacts "
          f"— {'no duplicates stored' if total_sha == total_docs else 'DUPLICATES PRESENT'}")

    print("\n=== 3. What Opus 5 segregation will actually cover ===")
    emails = artifacts.count_documents({"source_type": "email"})
    attachments = artifacts.count_documents({"source_type": "attachment"})
    disk = artifacts.count_documents({"source_type": "disk_file"})

    attached_to_email = artifacts.count_documents(
        {"source_type": "attachment", "parent_email_shas": {"$exists": True, "$ne": []}}
    )
    orphan_attachments = attachments - attached_to_email

    disk_with_parent = artifacts.count_documents(
        {"source_type": "disk_file", "parent_email_shas": {"$exists": True, "$ne": []}}
    )

    print(f"    emails                                  {emails:>6}  -> Opus call each")
    print(f"    attachments reachable from an email     {attached_to_email:>6}  -> ride along in that call")
    print(f"    attachments with NO parent email        {orphan_attachments:>6}  <- would be missed")
    print(f"    disk files                              {disk:>6}")
    print(f"      of those, reachable via an email      {disk_with_parent:>6}")
    print(f"      NOT reachable from any email          {disk - disk_with_parent:>6}  <- would be missed")

    missed = orphan_attachments + (disk - disk_with_parent)
    print(f"\n    documents Opus would never see: {missed:,}")
    print()


if __name__ == "__main__":
    main()
