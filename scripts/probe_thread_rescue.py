"""Could the unresolved emails be answered by their own conversation?

Opus 5 judges one email at a time. A two-word reply ("Received.", "What time?")
carries no property signal on its own, so it lands in the review queue — but the
thread it belongs to often resolved cleanly on an earlier message. If so, these
are machine-answerable and should never reach a person.

Also sizes the unparsed .msg files, since names like "Briardale emails.msg"
suggest whole archives rather than single messages.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

DISK_ROOT = Path(r"E:\LP Remodeling Projects\Hold Properties")


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    unresolved = list(art.find(
        {"source_type": "email", "resolution_status": "needs_review", "property_ids": []},
        {"sha256": 1, "thread_key": 1, "subject": 1},
    ))
    print(f"\n  unresolved emails                    {len(unresolved):>6,}")

    keys = {d.get("thread_key") for d in unresolved if d.get("thread_key")}
    print(f"    of those, in a known thread        {len([d for d in unresolved if d.get('thread_key')]):>6,}")

    # Which of those threads have a property from some *other* message?
    resolved_threads = {}
    for key in keys:
        siblings = art.find(
            {"thread_key": key, "property_ids": {"$ne": []}},
            {"property_ids": 1},
        ).limit(5)
        props = {p for s in siblings for p in (s.get("property_ids") or [])}
        if props:
            resolved_threads[key] = props

    rescuable = [d for d in unresolved if d.get("thread_key") in resolved_threads]
    single = sum(1 for k in resolved_threads if len(resolved_threads[k]) == 1)

    print(f"\n  RESCUABLE FROM THREAD CONTEXT")
    print(f"    threads that resolved elsewhere    {len(resolved_threads):>6,}")
    print(f"      of those naming exactly one      {single:>6,}")
    print(f"    unresolved emails they cover       {len(rescuable):>6,}")
    if unresolved:
        print(f"    share of the review backlog        {100 * len(rescuable) / len(unresolved):>5.0f}%")

    print("\n    examples")
    for doc in rescuable[:6]:
        props = sorted(resolved_threads[doc["thread_key"]])
        subject = " ".join(str(doc.get("subject") or "").split())[:58]
        print(f"      {subject:<60} -> {props}")

    print("\n  UNPARSED .msg FILES")
    total = 0
    for entry in mongo.review_queue.find({"kind": "msg_parser_pending"}, {"subject": 1}):
        rel = entry.get("subject") or ""
        path = DISK_ROOT / rel
        size = path.stat().st_size if path.exists() else 0
        total += size
        print(f"    {size / 1024:>9,.0f} KB  {Path(rel).name}")
    print(f"    {total / 1024:>9,.0f} KB  total unread")
    print()


if __name__ == "__main__":
    main()
