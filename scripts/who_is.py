"""Everything the corpus knows about an email domain or address.

Used before adding someone to the registry: the role recorded there drives how
their mail is weighted, so it should reflect what they actually send rather than
a guess from their domain name.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

TERM = sys.argv[1] if len(sys.argv) > 1 else "conduitbankers"


def main() -> None:
    db = get_mongo().db
    pattern = {"$regex": TERM, "$options": "i"}

    print("=" * 78)
    print(f"WHO IS: {TERM}")
    print("=" * 78)

    print("\n--- skipped messages involving them ---")
    count = 0
    for s in db["skipped"].find():
        blob = repr(s).lower()
        if TERM.lower() not in blob:
            continue
        count += 1
        if count <= 30:
            print(f"  {str(s.get('reason')):<24} {str(s.get('subject'))[:95]}")
            cands = s.get("discovery_candidates") or []
            if cands:
                print(f"        candidates: {cands}")
    print(f"  total: {count}")

    print("\n--- ingested artifacts involving them ---")
    found = 0
    for a in db["artifacts"].find(
        {}, {"subject": 1, "filename": 1, "participants": 1, "from": 1,
             "to": 1, "cc": 1, "property_ids": 1, "date": 1, "source_type": 1},
    ):
        if TERM.lower() not in repr(a).lower():
            continue
        found += 1
        when = a.get("date")
        stamp = when.strftime("%Y-%m-%d") if hasattr(when, "strftime") else "?"
        if found <= 30:
            label = a.get("subject") or a.get("filename")
            print(f"  {stamp}  [{a.get('source_type')}]  {str(label)[:88]}")
            print(f"        props: {a.get('property_ids')}")
    print(f"  total: {found}")

    print("\n--- text mentions in document bodies ---")
    hits = 0
    for a in db["artifacts"].find(
        {"$or": [{"text": pattern}, {"body_clean": pattern}]},
        {"subject": 1, "filename": 1, "text": 1, "body_clean": 1},
    ).limit(12):
        hits += 1
        body = (a.get("text") or a.get("body_clean") or "")
        low = body.lower()
        idx = low.find(TERM.lower())
        window = body[max(0, idx - 200): idx + 260].replace("\n", " ")
        print(f"\n  {a.get('subject') or a.get('filename')}")
        print(f"      ...{window}...")
    print(f"\n  documents mentioning it in text: {hits}")

    print("\n--- discovery candidates ranked (all unknown parties) ---")
    tally: dict = {}
    for s in db["skipped"].find({}, {"discovery_candidates": 1}):
        for c in (s.get("discovery_candidates") or []):
            key = c if isinstance(c, str) else str(c)
            tally[key] = tally.get(key, 0) + 1
    for key, n in sorted(tally.items(), key=lambda kv: -kv[1])[:25]:
        print(f"  {n:>5}x  {key}")


if __name__ == "__main__":
    main()
