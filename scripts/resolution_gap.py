"""How many emails have no property, and would AI resolution actually help?

Attachment inheritance stalled because 44 of 46 unattributed attachments hang off
emails that are themselves unresolved. So the binding constraint is email
resolution, not attachment inheritance — and the admin already specified the fix
(Opus 4.6 reading subject, body and attachments). This measures the size of that
job and, importantly, whether the unresolved mail is even in scope: an email
about a property we do not track should stay unresolved, not be forced into one.
"""
from __future__ import annotations

import re
import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.config.registry import PROPERTIES
from mangotree.storage.mongo import get_mongo

UNRESOLVED = [
    {"property_ids": None},
    {"property_ids": {"$size": 0}},
    {"property_ids": {"$exists": False}},
]

ADDRESS = re.compile(
    r"\b\d{2,6}\s+[A-Z][A-Za-z0-9'\.\-]*(?:\s+[A-Z][A-Za-z0-9'\.\-]*){0,3}"
    r"\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place|"
    r"Blvd|Boulevard|Way|Cir|Circle|Ter|Terrace|Trl|Trail|Pkwy|Hwy)\b"
)


def main() -> None:
    db = get_mongo().db

    print("=" * 78)
    print("PROPERTY RESOLUTION GAP")
    print("=" * 78)

    for source_type in ("email", "attachment", "disk_file"):
        total = db["artifacts"].count_documents({"source_type": source_type})
        unresolved = db["artifacts"].count_documents(
            {"source_type": source_type, "$or": UNRESOLVED}
        )
        pct = unresolved / total if total else 0
        print(f"  {source_type:<12}{total:>6} total  {unresolved:>6} unresolved  ({pct:.0%})")

    print("\n--- unresolved emails: what do their subjects say? ---")
    known_aliases = []
    for prop in PROPERTIES:
        for alias in (prop.canonical_address, *prop.aliases):
            known_aliases.append((alias.lower(), prop.property_id))

    rows = list(db["artifacts"].find(
        {"source_type": "email", "$or": UNRESOLVED},
        {"subject": 1, "body_clean": 1, "date": 1},
    ))
    print(f"  {len(rows)} unresolved emails\n")

    names_known: Counter = Counter()
    addresses_unknown: Counter = Counter()
    no_signal = 0

    for row in rows:
        subject = row.get("subject") or ""
        body = (row.get("body_clean") or "")[:4000]
        blob = f"{subject} {body}"
        low = blob.lower()

        hit = None
        for alias, pid in known_aliases:
            if len(alias) >= 4 and alias in low:
                hit = pid
                break
        if hit:
            names_known[hit] += 1
            continue

        found = ADDRESS.findall(blob)
        if found:
            for match in found[:2]:
                addresses_unknown[match.strip()] += 1
        else:
            no_signal += 1

    print("  MENTION A REGISTERED PROPERTY but were not resolved to it:")
    for pid, count in names_known.most_common():
        print(f"      {count:>5}  {pid}")
    print(f"      total: {sum(names_known.values())}  <-- AI resolution should recover these")

    print("\n  MENTION AN ADDRESS WE DO NOT TRACK (out of scope, correctly unresolved):")
    for addr, count in addresses_unknown.most_common(25):
        print(f"      {count:>5}  {addr}")
    print(f"      distinct unknown addresses: {len(addresses_unknown)}")

    print(f"\n  NO property signal at all: {no_signal}")

    print("\n--- sample unresolved subjects that name a known property ---")
    shown = 0
    for row in rows:
        subject = row.get("subject") or ""
        low = f"{subject} {(row.get('body_clean') or '')[:2000]}".lower()
        for alias, pid in known_aliases:
            if len(alias) >= 4 and alias in low:
                when = row.get("date")
                stamp = when.strftime("%Y-%m-%d") if hasattr(when, "strftime") else "?"
                print(f"  {stamp}  [{pid}]  {subject[:88]}")
                shown += 1
                break
        if shown >= 30:
            break


if __name__ == "__main__":
    main()
