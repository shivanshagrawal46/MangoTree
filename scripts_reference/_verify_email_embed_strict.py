"""Strict, multi-axis verification of email-embedding completeness."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main():
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    print("=" * 70)
    print("EMAIL EMBEDDING — STRICT COMPLETENESS AUDIT")
    print("=" * 70)

    # 1. Total emails
    total_emails = m.emails.count_documents({})
    print(f"\n[1] Emails in DB:                                    {total_emails:,}")

    # 2. Emails with usable body
    has_body = m.emails.count_documents({
        "body_text": {"$exists": True, "$nin": [None, ""]},
    })
    no_body = total_emails - has_body
    print(f"    Emails WITH body_text:                           {has_body:,}")
    print(f"    Emails WITHOUT body (calendar/forwards):         {no_body:,}")

    # 3. Email-body chunks
    total_chunks = m.chunks.count_documents({"source_type": "email_body"})
    print(f"\n[2] Total 'email_body' chunks:                       {total_chunks:,}")

    # 4. Chunks with proper 1024-d embedding
    chunks_with_emb = m.chunks.count_documents({
        "source_type": "email_body",
        "embedding": {"$exists": True, "$type": "array"},
    })
    print(f"    With non-empty 1024-d embedding vector:          {chunks_with_emb:,}")

    chunks_no_emb = total_chunks - chunks_with_emb
    if chunks_no_emb > 0:
        print(f"    !!! MISSING embedding vector: {chunks_no_emb:,} chunks")

    # 5. Sample dimension check
    sample = m.chunks.find_one({"source_type": "email_body", "embedding": {"$exists": True}})
    if sample:
        dim = len(sample["embedding"])
        print(f"    Sample chunk embedding dimensionality:           {dim}  (expected {s.embedding_dim})")

    # 6. Distinct emails covered
    distinct_email_ids = m.chunks.distinct("email_id", {"source_type": "email_body"})
    n_covered = len(distinct_email_ids)
    print(f"\n[3] Unique emails covered by chunks:                 {n_covered:,}")
    print(f"    Target (emails with body):                       {has_body:,}")

    if n_covered < has_body:
        gap = has_body - n_covered
        print(f"    !!! MISSING: {gap:,} emails with body have NO chunks")
        # Find a few examples
        covered_set = set(str(eid) for eid in distinct_email_ids)
        missing = []
        for e in m.emails.find(
            {"body_text": {"$exists": True, "$nin": [None, ""]}},
            {"_id": 1, "subject": 1, "from": 1, "date": 1, "body_text": 1},
        ).limit(5_000):
            if str(e["_id"]) not in covered_set:
                missing.append(e)
                if len(missing) >= 5:
                    break
        for ex in missing:
            body_len = len(ex.get("body_text") or "")
            subj = (ex.get("subject") or "")[:60]
            print(f"      - {ex['_id']} | body={body_len}c | {subj}")
    else:
        print("    OK — every email with a body has at least one chunk.")

    # 7. Cross-reference: any chunks pointing to a non-existent email_id?
    print(f"\n[4] Cross-reference integrity (chunks ↔ emails):")
    sample_ids = distinct_email_ids[:200]
    found = m.emails.count_documents({"_id": {"$in": sample_ids}})
    print(f"    Sampled {len(sample_ids)} email_ids from chunks; found in emails:   {found}")

    # 8. Atlas vector index status
    try:
        idxs = list(m.chunks.list_search_indexes())
        for ix in idxs:
            print(f"\n[5] Atlas index '{ix.get('name')}' — status: {ix.get('status')}")
            print(f"    Queryable: {ix.get('queryable')}")
            print(f"    LatestDef: {ix.get('latestDefinition', {}).get('fields', [{}])[0].get('numDimensions','?')} dims")
    except Exception as exc:
        print(f"\n[5] Could not list Atlas indexes via driver: {exc}")

    print("\n" + "=" * 70)
    if chunks_with_emb == total_chunks and n_covered == has_body:
        print("VERDICT: EMAIL EMBEDDINGS ARE 100% COMPLETE")
    else:
        print("VERDICT: GAP FOUND — see warnings above")
    print("=" * 70)
    m.close()


if __name__ == "__main__":
    main()
