"""Verify email-embedding coverage."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main():
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    total_emails = m.emails.count_documents({})
    emails_with_body = m.emails.count_documents({
        "body_text": {"$exists": True, "$nin": [None, ""]},
    })

    chunks_total = m.chunks.count_documents({"source_type": "email_body"})
    distinct_email_ids = m.chunks.distinct("email_id", {"source_type": "email_body"})
    n_distinct = len(distinct_email_ids)

    print(f"Emails in DB:                         {total_emails:,}")
    print(f"  with non-empty body_text:           {emails_with_body:,}")
    print(f"")
    print(f"Email-body chunks in email_chunks:    {chunks_total:,}")
    print(f"Unique emails covered by chunks:      {n_distinct:,}")
    print(f"")
    if n_distinct < emails_with_body:
        gap = emails_with_body - n_distinct
        print(f"MISSING: {gap:,} emails have a body but no chunks!")
    else:
        print(f"COMPLETE: every email with a body is embedded.")

    # Sanity check: how many email-chunk docs have non-empty embedding vector
    chunks_with_emb = m.chunks.count_documents({
        "source_type": "email_body",
        "embedding": {"$exists": True, "$type": "array"},
    })
    print(f"\nChunks with embedding vector populated: {chunks_with_emb:,}")

    m.close()


if __name__ == "__main__":
    main()
