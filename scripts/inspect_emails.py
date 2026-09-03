"""Dump full detail for emails matching a term — participants, dates, body.

Used before registering a new property: the alias list and the counterparty set
should come from how people actually wrote about the deal, not from a guess.
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

TERM = sys.argv[1] if len(sys.argv) > 1 else "Tahona"


def addr(value) -> str:
    if isinstance(value, dict):
        return value.get("address") or value.get("email") or str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(addr(v) for v in value)
    return str(value or "")


def main() -> None:
    db = get_mongo().db
    pattern = {"$regex": TERM, "$options": "i"}

    rows = list(db["artifacts"].find(
        {"$or": [{"subject": pattern}, {"filename": pattern}]},
    ).sort("date", 1))

    print(f"=== {len(rows)} artifacts matching {TERM} ===\n")
    for a in rows:
        when = a.get("date")
        stamp = when.strftime("%Y-%m-%d %H:%M") if hasattr(when, "strftime") else "?"
        print("-" * 74)
        print(f"{stamp}  [{a.get('source_type')}]  {a.get('subject') or a.get('filename')}")
        print(f"  from : {addr(a.get('from'))}")
        print(f"  to   : {addr(a.get('to'))}")
        print(f"  cc   : {addr(a.get('cc'))}")
        print(f"  props: {a.get('property_ids')}   conf: {a.get('property_confidence')}")
        print(f"  class: {a.get('doc_class')}   folder: {a.get('folder')}")
        body = ""
        for key in ("body_clean", "text", "body", "clean_text", "body_text", "plain_text"):
            if (a.get(key) or "").strip():
                body = a[key]
                break
        if body:
            clean = re.sub(r"\n{3,}", "\n\n", body)
            print(f"  --- body ({len(body)} chars, field found) ---")
            print("  " + clean[:1600].replace("\n", "\n  "))
        else:
            present = [k for k in a.keys() if "text" in k.lower() or "body" in k.lower()]
            print(f"  (no body text; text-ish fields present: {present})")
        print()


if __name__ == "__main__":
    main()
