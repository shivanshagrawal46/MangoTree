"""Do email attachments inherit their parent's property, and do they have text?

`Tahona ALTA.pdf` and `Tahona Commitment.pdf` both came back with
``property_ids: None`` while the email that carried them resolved to `tahona`.
An attachment is the *evidence* the email refers to — a title commitment matters
far more than the sentence announcing it — so an attachment that inherits no
property is invisible to that property's analysis.

This measures the scale of it across the whole attachment set before anything is
changed, since a fix applied to 2 files and a fix applied to 700 are different
pieces of work.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db

    total = db["artifacts"].count_documents({"source_type": "attachment"})
    print("=" * 74)
    print(f"ATTACHMENTS: {total}")
    print("=" * 74)

    no_prop = db["artifacts"].count_documents({
        "source_type": "attachment",
        "$or": [{"property_ids": None}, {"property_ids": {"$size": 0}},
                {"property_ids": {"$exists": False}}],
    })
    with_prop = total - no_prop
    no_text = db["artifacts"].count_documents({
        "source_type": "attachment",
        "$or": [{"text": None}, {"text": ""}, {"text": {"$exists": False}}],
    })

    print(f"  with a property        {with_prop}")
    print(f"  WITHOUT a property     {no_prop}")
    print(f"  without extracted text {no_text}")

    print("\n--- by extension ---")
    ext_all: Counter = Counter()
    ext_noprop: Counter = Counter()
    ext_notext: Counter = Counter()
    for a in db["artifacts"].find(
        {"source_type": "attachment"},
        {"filename": 1, "property_ids": 1, "text": 1, "parent_sha": 1,
         "email_sha": 1, "parent_message_sha": 1},
    ):
        name = (a.get("filename") or "").lower()
        ext = name.rsplit(".", 1)[-1] if "." in name else "(none)"
        ext_all[ext] += 1
        if not (a.get("property_ids") or []):
            ext_noprop[ext] += 1
        if not (a.get("text") or "").strip():
            ext_notext[ext] += 1

    print(f"  {'ext':<10}{'total':>8}{'no prop':>10}{'no text':>10}")
    for ext, count in ext_all.most_common(20):
        print(f"  {ext:<10}{count:>8}{ext_noprop[ext]:>10}{ext_notext[ext]:>10}")

    print("\n--- which field links an attachment to its email? ---")
    sample = db["artifacts"].find_one({"source_type": "attachment"})
    for key, value in sorted((sample or {}).items()):
        preview = repr(value)
        if len(preview) > 110:
            preview = preview[:110] + "..."
        print(f"  {key:<26}{preview}")

    print("\n--- highest-value attachments currently without a property ---")
    keywords = ("alta", "commitment", "title", "payoff", "deed", "note",
                "invoice", "draw", "settlement", "policy", "appraisal", "budget")
    shown = 0
    for a in db["artifacts"].find(
        {"source_type": "attachment",
         "$or": [{"property_ids": None}, {"property_ids": {"$size": 0}},
                 {"property_ids": {"$exists": False}}]},
        {"filename": 1, "text": 1, "date": 1},
    ):
        name = (a.get("filename") or "").lower()
        if any(k in name for k in keywords):
            chars = len((a.get("text") or "").strip())
            when = a.get("date")
            stamp = when.strftime("%Y-%m-%d") if hasattr(when, "strftime") else "?"
            print(f"  {stamp}  {chars:>7} chars  {a.get('filename')}")
            shown += 1
            if shown >= 40:
                print("  ...")
                break


if __name__ == "__main__":
    main()
