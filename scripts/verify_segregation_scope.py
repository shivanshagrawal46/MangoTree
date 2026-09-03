"""Confirm what the next Opus 5 run will and will not see, before it is billed.

Exercises the real ``SegregationRunner`` selectors rather than reimplementing
them, so this cannot drift from what the stage actually does. Three assertions:

* every email attachment reaches its parent's call, including the 122 that also
  live in the disk corpus;
* no disk-only file is ever sent — its folder already names the property;
* the folder-derived property on a collapsed artifact survives, because the
  attachment writer unions rather than overwrites.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.models import Seat, model_for
from mangotree.resolve.segregation_runner import CONCURRENCY, SegregationRunner
from mangotree.storage.mongo import get_mongo


class _Stub:
    def __init__(self, model: str) -> None:
        self.model = model


class _Selectors(SegregationRunner):
    """Selector methods only — constructing the real runner needs an API key."""

    def __init__(self, mongo):
        self.mongo = mongo
        self.segregator = _Stub(model_for(Seat.ANALYST))


def main() -> int:
    mongo = get_mongo()
    art = mongo.artifacts
    sel = _Selectors(mongo)

    collapsed = list(art.find(
        {"source_types": {"$all": ["attachment", "disk_file"]}},
        {"sha256": 1, "filename": 1, "property_ids": 1, "parent_email_shas": 1},
    ))
    print(f"\n  collapsed artifacts (disk + mail)   {len(collapsed):>6,}")

    parent_shas = {s for d in collapsed for s in (d.get("parent_email_shas") or [])}
    reached, missing_property = set(), []
    for sha in parent_shas:
        email = art.find_one({"sha256": sha}, {"sha256": 1})
        if not email:
            continue
        for att in sel._attachments_of(email):
            reached.add(att["sha256"])

    for doc in collapsed:
        if not doc.get("property_ids"):
            missing_property.append(doc.get("filename"))

    every = {d["sha256"] for d in collapsed}
    print(f"  reached by the attachment join     {len(every & reached):>6,} / {len(every):,}")
    print(f"  still carrying folder property     {len(collapsed) - len(missing_property):>6,} / {len(collapsed):,}")

    # Asks the join itself, so an attachment counts as carrying evidence only if
    # the text survives the exact projection the billed call will use.
    payloads = [
        att
        for sha in parent_shas
        for att in sel._attachments_of({"sha256": sha})
    ]
    with_text = sum(1 for att in payloads if (att.get("text") or "").strip())
    print(f"  attachment payloads built          {len(payloads):>6,}")
    print(f"  of those carrying real text        {with_text:>6,}")

    pending = sel._pending(None)
    print(f"\n  OPUS 5 NEXT RUN (concurrency {CONCURRENCY})")
    print(f"    emails queued (1 call each)      {len(pending):>6,}")
    disk_only = art.count_documents({"source_types": ["disk_file"]})
    print(f"    disk-only files excluded         {disk_only:>6,}")
    sent_types = {t for d in pending for t in (d.get("source_types") or [])}
    print(f"    origin tags in the queue         {sorted(sent_types)}")

    # Subset, not equality: once a run has decided every email the queue is
    # empty, and an empty queue is the success state. What must never happen is
    # a non-email origin appearing in it.
    ok = (
        (every & reached) == every
        and not missing_property
        and sent_types <= {"email"}
        and with_text > 0
    )
    print(f"\n  {'PASS' if ok else 'FAIL'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
