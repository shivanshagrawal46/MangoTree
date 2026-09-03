"""Find what the corpus already knows about a property name.

Registering a property from a bare name invites two errors: an alias set too
narrow to match how people actually write it, and a wrong street number that
then mis-files every document. The corpus already contains the answer, so this
pulls the real spellings, street numbers and counterparties out of subjects,
bodies and filenames before anything is added to the registry.
"""
from __future__ import annotations

import re
import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

TERM = sys.argv[1] if len(sys.argv) > 1 else "Tahona"


def main() -> None:
    db = get_mongo().db
    pattern = {"$regex": TERM, "$options": "i"}

    print("=" * 78)
    print(f"CORPUS MENTIONS OF: {TERM}")
    print("=" * 78)

    subjects = list(db["artifacts"].find(
        {"$or": [{"subject": pattern}, {"filename": pattern},
                 {"relative_path": pattern}]},
        {"subject": 1, "filename": 1, "relative_path": 1, "source_type": 1,
         "from": 1, "to": 1, "date": 1, "property_ids": 1},
    ).limit(80))

    print(f"\n--- {len(subjects)} artifacts with {TERM} in subject/filename/path ---")
    for a in subjects:
        label = a.get("subject") or a.get("filename") or a.get("relative_path")
        sender = a.get("from") or ""
        if isinstance(sender, dict):
            sender = sender.get("address", "")
        when = a.get("date")
        stamp = when.strftime("%Y-%m-%d") if hasattr(when, "strftime") else ""
        print(f"  [{a.get('source_type','?'):<10}] {stamp}  {str(label)[:110]}")
        if sender:
            print(f"                from: {sender}   props: {a.get('property_ids')}")

    # Body hits give the street number and the surrounding phrasing.
    body_hits = list(db["artifacts"].find(
        {"text": pattern},
        {"subject": 1, "filename": 1, "text": 1, "source_type": 1, "date": 1},
    ).limit(40))
    print(f"\n--- {len(body_hits)} artifacts with {TERM} in body text ---")

    context = re.compile(rf".{{0,110}}{re.escape(TERM)}.{{0,110}}", re.I)
    spellings: Counter = Counter()
    numbers: Counter = Counter()
    for a in body_hits:
        label = a.get("subject") or a.get("filename") or "(untitled)"
        text = a.get("text") or ""
        snippets = context.findall(text)[:3]
        print(f"\n  [{a.get('source_type','?')}] {str(label)[:90]}")
        for snippet in snippets:
            clean = re.sub(r"\s+", " ", snippet).strip()
            print(f"      ...{clean}...")
        for match in re.finditer(
            rf"(\d{{3,6}})\s+({re.escape(TERM)}[a-z]*)", text, re.I
        ):
            numbers[match.group(1)] += 1
        for match in re.finditer(
            rf"{re.escape(TERM)}[a-z]*(?:\s+(?:st|street|ct|court|dr|drive|rd|road|ln|lane|way|pl|place|ave|avenue|blvd|cir|circle|trl|trail))?",
            text, re.I,
        ):
            spellings[re.sub(r"\s+", " ", match.group(0)).strip().title()] += 1

    print("\n--- street numbers seen immediately before the name ---")
    for number, count in numbers.most_common(12):
        print(f"  {count:>5}x  {number} {TERM}")

    print("\n--- spellings / street-type variants seen ---")
    for spelling, count in spellings.most_common(20):
        print(f"  {count:>5}x  {spelling}")

    print("\n--- chunk hits (already indexed and searchable) ---")
    print(f"  {db['chunks'].count_documents({'text': pattern})} chunks contain the term")

    print("\n--- skipped messages mentioning it ---")
    for s in db["skipped"].find(
        {"$or": [{"subject": pattern}, {"reason": pattern}]},
        {"subject": 1, "reason": 1, "from": 1},
    ).limit(20):
        print(f"  {s.get('reason','?'):<26} {str(s.get('subject'))[:80]}")


if __name__ == "__main__":
    main()
