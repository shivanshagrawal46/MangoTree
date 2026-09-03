"""Candidate properties, ranked by how often they head an email SUBJECT.

Subjects are where a real deal announces itself; document bodies are full of
notary, lender and legal-description addresses that are not properties of ours.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from mangotree.config.registry import PROPERTIES
from mangotree.storage.mongo import get_mongo

SKIP_DUMP = (
    r"C:\Users\SHIVANSH AGRAWAL\.cursor\projects"
    r"\c-Users-SHIVANSH-AGRAWAL-Desktop-MangoTree\agent-tools"
    r"\acf540c9-b6f9-4a9c-ab22-6b51889adc5d.txt"
)

SUFFIX = (
    r"St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Ln|Lane|Ct|Court|Pl|Place|"
    r"Blvd|Boulevard|Way|Ter|Terrace|Cir|Circle|Hwy|Pkwy|Sq|Trail"
)
# Allow ordinals (9th, 50th) and single letters (K Street) in the street name.
ADDR = re.compile(
    rf"\b(\d{{1,6}}[A-Z]?)\s+((?:(?:[A-Z][\w'\-\.]*|\d+(?:st|nd|rd|th))\s+){{1,4}}?)"
    rf"({SUFFIX})\b\.?(\s+(?:NW|NE|SW|SE))?",
    re.IGNORECASE,
)

OURS = {}
for p in PROPERTIES:
    for a in (p.canonical_address,) + tuple(p.aliases):
        OURS[a.lower()] = p.property_id


def canon(m) -> str:
    num, name, suf, quad = m.group(1), m.group(2) or "", m.group(3), m.group(4) or ""
    name = " ".join(name.split()).title()
    return f"{num} {name} {suf.title()}" + (f" {quad.strip().upper()}" if quad else "")


def owner(text: str):
    t = text.lower()
    for alias, pid in OURS.items():
        if alias in t:
            return pid
    return None


def main() -> None:
    db = get_mongo().db

    subjects: list[str] = []
    for a in db.artifacts.find({"source_type": "email"}, {"subject": 1}):
        if a.get("subject"):
            subjects.append(a["subject"])

    skipped_subjects: list[str] = []
    try:
        for line in open(SKIP_DUMP, encoding="utf-8", errors="replace"):
            line = line.strip()
            if re.match(r"^20\d\d-\d\d-\d\d", line):
                skipped_subjects.append(line)
    except OSError:
        pass

    counts: Counter = Counter()
    origin: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list[str]] = defaultdict(list)

    for tag, pool in (("ingested", subjects), ("skipped", skipped_subjects)):
        for s in pool:
            for m in ADDR.finditer(s):
                key = canon(m)
                if len(key) > 46:
                    continue
                counts[key] += 1
                origin[key][tag] += 1
                if len(examples[key]) < 3:
                    examples[key].append(s[:88])

    known, unknown = {}, {}
    for k, n in counts.items():
        pid = owner(k)
        (known if pid else unknown)[k] = (n, pid)

    print("=" * 98)
    print("A) SUBJECT-LINE MENTIONS THAT MAP TO YOUR 14 REGISTERED PROPERTIES")
    print("=" * 98)
    per_prop = Counter()
    for k, (n, pid) in known.items():
        per_prop[pid] += n
    for p in PROPERTIES:
        print(f"  {p.property_id:14s} {p.canonical_address:28s} {per_prop.get(p.property_id,0):>5} subject mentions")

    print("\n" + "=" * 98)
    print("B) ADDRESSES IN EMAIL SUBJECTS THAT ARE **NOT** IN YOUR REGISTRY")
    print("   These are the real candidates for extra properties.")
    print("=" * 98)
    for k, (n, _) in sorted(unknown.items(), key=lambda kv: -kv[1][0]):
        o = origin[k]
        tags = f"ingested:{o.get('ingested',0)} skipped:{o.get('skipped',0)}"
        print(f"\n  {n:>4}x  {k:38s}  [{tags}]")
        for ex in examples[k]:
            print(f"           e.g. {ex}")


if __name__ == "__main__":
    main()
