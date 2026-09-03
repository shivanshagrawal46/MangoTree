"""Merge emails stored twice because they arrived in both mailboxes.

Same Message-ID, different bytes (each mailbox's delivery headers differ), so the
SHA-256 key kept both. The pipeline now folds the second copy into an
*occurrence* of the first; this does the same for the pairs the backfill created.

Per Message-ID group: the winner is the copy that carries a property (or, on a
tie, the earlier one). Every loser is folded in:

* its occurrences move to the winner (so both mailboxes stay recorded)
* attachments that pointed at it point at the winner
* its chunks, doc summary, timeline events, review-queue rows and cards go —
  the winner has its own of each
* the thread loses one message from its count
* the loser artifact itself is copied to ``artifacts_merged`` for audit, then removed

Graph edges to the deleted chunks are left for the nightly rebuild, which
recomputes them from the chunk collection.

Run with ``--dry`` first.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def pick_winner(docs):
    def score(d):
        seg = d.get("segregation") or {}
        return (
            1 if d.get("property_ids") else 0,
            1 if seg else 0,
            1 if d.get("indexing") else 0,
            -(d.get("created_at") or datetime.max.replace(tzinfo=timezone.utc)).timestamp(),
        )
    return max(docs, key=score)


def main() -> int:
    dry = "--dry" in sys.argv
    m = get_mongo()
    art = m.artifacts
    groups = list(art.aggregate([
        {"$match": {"source_type": "email", "internet_message_id": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$internet_message_id", "n": {"$sum": 1}, "shas": {"$push": "$sha256"}}},
        {"$match": {"n": {"$gt": 1}}},
    ]))
    print(f"  {len(groups)} Message-IDs stored more than once")
    merged = 0
    for g in groups:
        docs = list(art.find({"sha256": {"$in": g["shas"]}}))
        winner = pick_winner(docs)
        losers = [d for d in docs if d["sha256"] != winner["sha256"]]
        w = winner["sha256"]
        print(f"\n  {g['_id'][:60]}")
        print(f"    keep  {w[:12]}  {winner.get('provider'):<8} props={winner.get('property_ids')}  {(winner.get('subject') or '')[:50]}")
        for d in losers:
            l = d["sha256"]
            print(f"    fold  {l[:12]}  {d.get('provider'):<8} props={d.get('property_ids')}")
            if dry:
                continue
            # occurrences -> winner (drop any that would collide on the unique key)
            for occ in m.occurrences.find({"artifact_sha": l}):
                exists = m.occurrences.find_one({"artifact_sha": w, "mailbox": occ.get("mailbox"), "folder": occ.get("folder")})
                if exists:
                    m.occurrences.delete_one({"_id": occ["_id"]})
                else:
                    m.occurrences.update_one({"_id": occ["_id"]}, {"$set": {"artifact_sha": w, "merged_from": l}})
            # attachments re-parented
            art.update_many({"parent_email_shas": l}, {"$addToSet": {"parent_email_shas": w}})
            art.update_many({"parent_email_shas": l}, {"$pull": {"parent_email_shas": l}})
            # derived rows of the loser
            m.chunks.delete_many({"artifact_sha": l})
            m.db["doc_summaries"].delete_many({"artifact_sha": l})
            m.db["timeline_events"].delete_many({"source_sha": l})
            m.review_queue.delete_many({"artifact_sha": l})
            m.db["cards"].update_many({"source_sha": l, "status": {"$in": ["new", "seen"]}},
                                      {"$set": {"status": "superseded", "superseded_reason": f"duplicate of {w}"}})
            if d.get("thread_key"):
                m.threads.update_one({"thread_key": d["thread_key"]}, {"$pull": {"artifact_shas": l}, "$inc": {"message_count": -1}})
            # winner remembers where else it was seen; loser archived then removed
            art.update_one({"sha256": w}, {"$addToSet": {"also_seen_in": {"mailbox": d.get("mailbox"), "provider": d.get("provider"), "sha256": l}}})
            m.db["artifacts_merged"].update_one({"sha256": l}, {"$set": {**{k: v for k, v in d.items() if k != "_id"}, "merged_into": w, "merged_at": datetime.now(timezone.utc)}}, upsert=True)
            art.delete_one({"sha256": l})
            merged += 1
    print(f"\n  {'would fold' if dry else 'folded'} {sum(g['n'] - 1 for g in groups) if dry else merged} duplicate copies")
    if not dry:
        left = list(art.aggregate([
            {"$match": {"source_type": "email", "internet_message_id": {"$nin": [None, ""]}}},
            {"$group": {"_id": "$internet_message_id", "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}}, {"$count": "n"}]))
        print(f"  Message-IDs still duplicated: {left[0]['n'] if left else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
