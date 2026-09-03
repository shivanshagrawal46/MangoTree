"""
Strict final verification for the RAG corpus.

Confirms:
  1. Every non-empty email body has at least one email_body chunk.
  2. Every attachment with non-empty extracted_text has at least one
     attachment chunk (matched by sha256 source_hash).
  3. Reports counts and any gaps so we can targeted-fix them.

Usage:
    python scripts/_verify_embed_final.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main() -> int:
    s = Settings.load()
    mongo = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    mongo.ping()

    # --- 1. Emails ---
    total_emails = mongo.emails.count_documents({})
    emails_with_body = mongo.emails.count_documents(
        {"body_text": {"$type": "string", "$ne": ""}}
    )

    email_chunks = mongo.chunks.count_documents({"source_type": "email_body"})
    emailed_ids = set(
        mongo.chunks.distinct("email_id", {"source_type": "email_body"})
    )

    body_email_ids = set(
        d["_id"]
        for d in mongo.emails.find(
            {"body_text": {"$type": "string", "$ne": ""}}, {"_id": 1}
        )
    )
    missing_email_ids = body_email_ids - emailed_ids

    # --- 2. Attachments ---
    total_att_docs = mongo.attachments.count_documents({})
    extracted_att = mongo.attachments.count_documents(
        {"extracted_text": {"$type": "string", "$ne": ""}}
    )

    pipeline = [
        {"$match": {"extracted_text": {"$type": "string", "$ne": ""}}},
        {"$group": {"_id": "$sha256"}},
    ]
    unique_text_sha = {d["_id"] for d in mongo.attachments.aggregate(pipeline)}

    att_chunks_total = mongo.chunks.count_documents({"source_type": "attachment"})
    embedded_sha = set(
        mongo.chunks.distinct("sha256", {"source_type": "attachment"})
    )
    missing_sha = unique_text_sha - embedded_sha

    # --- 3. Report ---
    print("=" * 70)
    print("FINAL RAG CORPUS VERIFICATION")
    print("=" * 70)

    print("\nEMAILS")
    print(f"  total emails in DB:           {total_emails:,}")
    print(f"  emails w/ non-empty body:     {emails_with_body:,}")
    print(f"  email_body chunks:            {email_chunks:,}")
    print(f"  unique emails embedded:       {len(emailed_ids):,}")
    if missing_email_ids:
        print(f"  MISSING (non-empty body, no chunk): {len(missing_email_ids):,}")
        for eid in list(missing_email_ids)[:5]:
            print(f"    - {eid}")
    else:
        print(f"  status:                       FULLY EMBEDDED")

    print("\nATTACHMENTS")
    print(f"  total attachment docs:        {total_att_docs:,}")
    print(f"  docs w/ non-empty text:       {extracted_att:,}")
    print(f"  unique sha256 w/ text:        {len(unique_text_sha):,}")
    print(f"  attachment chunks:            {att_chunks_total:,}")
    print(f"  unique sha256 embedded:       {len(embedded_sha):,}")
    if missing_sha:
        print(f"  MISSING (text but no chunk):  {len(missing_sha):,}")
        # Print sample filenames for the missing ones
        for sha in list(missing_sha)[:8]:
            doc = mongo.attachments.find_one(
                {"sha256": sha},
                {"filename": 1, "size_bytes": 1, "extraction.method": 1},
            )
            if doc:
                fn = doc.get("filename", "?")
                size_kb = (doc.get("size_bytes") or 0) // 1024
                method = (doc.get("extraction") or {}).get("method", "?")
                print(f"    - {sha[:8]}  {size_kb:>6}KB  [{method}]  {fn[:60]}")
    else:
        print(f"  status:                       FULLY EMBEDDED")

    print("\nTOTAL chunks ready for retrieval:", email_chunks + att_chunks_total)
    print("=" * 70)

    gaps = len(missing_email_ids) + len(missing_sha)
    return 0 if gaps == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
