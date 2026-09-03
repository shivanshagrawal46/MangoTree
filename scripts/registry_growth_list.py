"""Deals outside the registry — grouped by address, for a human to decide.

Reads the ``deal_address`` the common-store classifier extracted from every
business item that is about a specific non-registry property, normalises the
address enough to group spellings, and writes one row per address with the
document count, the topics seen, the date span, and the strongest signals
(executed instruments, payoffs, assignments to RKB).

Output goes to ``review_queue`` as ``kind: "registry_candidate"`` — one entry per
address — so the same review screen that decides the 365 unplaced items can
decide these: register as a new property, or leave as an other deal. It is also
printed, so the main user can read it tonight.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

_SUFFIX = {
    "street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "court": "ct", "lane": "ln",
    "place": "pl", "boulevard": "blvd", "circle": "cir", "terrace": "ter", "parkway": "pkwy",
    "highway": "hwy", "way": "way",
}
_STRONG = ("assignment_allonge", "note_assignment", "deed_of_trust", "payoff", "wire_instructions", "draw_request")


def normalise(address: str) -> str:
    a = address.lower()
    a = re.sub(r"[.,#]", " ", a)
    a = re.sub(r"\b(unit|apt|suite|ste)\s*\w+", "", a)
    words = []
    for w in a.split():
        words.append(_SUFFIX.get(w, w))
    a = " ".join(words)
    # Drop city/state/zip tail: keep number + up to 4 words + suffix.
    m = re.match(r"^(\d+[a-z]?\s+(?:[a-z0-9']+\s+){0,3}(?:st|ave|rd|dr|ct|ln|pl|blvd|cir|ter|pkwy|hwy|way)\b(?:\s+(?:nw|ne|sw|se))?)", a)
    return m.group(1).strip() if m else a.strip()


def main() -> int:
    mongo = get_mongo()
    art = mongo.artifacts
    rows = list(art.find(
        {"deal_address": {"$exists": True, "$ne": None}},
        {"sha256": 1, "deal_address": 1, "common_topics": 1, "common_kind": 1, "date": 1,
         "filename": 1, "subject": 1, "common_classification.reasoning": 1},
    ))
    groups = defaultdict(list)
    for r in rows:
        groups[normalise(r["deal_address"])].append(r)

    now = datetime.now(timezone.utc)
    entries = []
    for key, docs in groups.items():
        topics = defaultdict(int)
        for d in docs:
            for t in d.get("common_topics") or []:
                topics[t] += 1
        dates = sorted(d["date"] for d in docs if hasattr(d.get("date"), "strftime"))
        strong = [t for t in _STRONG if topics.get(t)]
        rkb = sum(1 for d in docs if re.search(r"\brkb\b", (d.get("filename") or "") + " " + (d.get("subject") or ""), re.I))
        spellings = sorted({d["deal_address"] for d in docs})
        entries.append({
            "address_key": key,
            "spellings": spellings[:6],
            "documents": len(docs),
            "topics": dict(sorted(topics.items(), key=lambda kv: -kv[1])),
            "strong_signals": strong,
            "rkb_named_in_title": rkb,
            "first_seen": dates[0] if dates else None,
            "last_seen": dates[-1] if dates else None,
            "sample_names": [d.get("filename") or d.get("subject") for d in docs[:5]],
            "artifact_shas": [d["sha256"] for d in docs],
        })
    entries.sort(key=lambda e: (-len(e["strong_signals"]), -e["rkb_named_in_title"], -e["documents"]))

    rq = mongo.review_queue
    written = 0
    for e in entries:
        rq.update_one(
            {"artifact_sha": f"address::{e['address_key']}", "kind": "registry_candidate"},
            {"$set": {
                "artifact_sha": f"address::{e['address_key']}", "kind": "registry_candidate",
                "status": "open", "created_at": now,
                "note": f"{e['documents']} documents about {e['spellings'][0]}; "
                        f"signals: {', '.join(e['strong_signals']) or 'none'}",
                "payload": e,
            }},
            upsert=True,
        )
        written += 1

    print(f"\n  DEALS OUTSIDE THE REGISTRY   {len(entries)} addresses from {len(rows)} documents")
    print(f"  review_queue entries written: {written} (kind=registry_candidate)\n")
    print(f"  {'address':<34} {'docs':>4}  {'span':<23} {'RKB':>3}  signals")
    for e in entries[:60]:
        span = ""
        if e["first_seen"]:
            span = f"{e['first_seen']:%Y-%m} → {e['last_seen']:%Y-%m}"
        print(f"  {e['spellings'][0][:34]:<34} {e['documents']:>4}  {span:<23} {e['rkb_named_in_title']:>3}  "
              f"{', '.join(e['strong_signals'])}")
    if len(entries) > 60:
        print(f"  … and {len(entries) - 60} more addresses (all in review_queue)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
