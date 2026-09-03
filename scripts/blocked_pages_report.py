"""How many pages are awaiting recovery, and why they failed.

Distinguishes the two cases that look alike in the logs but are not:

* ``blocked`` — Anthropic refused and GPT-5 never got a turn, so the page is
  genuinely blank and its content is missing from the corpus;
* low confidence — Claude's reading was kept, GPT-5 simply never improved it, so
  the text is present and merely unverified.

Only the first is data loss.
"""
import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

mongo = get_mongo()

blank_pages = 0
low_conf_pages = 0
docs_with_blanks = []

cursor = mongo.artifacts.find(
    {"extraction.detail.vision_pages": {"$exists": True}},
    {"filename": 1, "sha256": 1, "extraction.detail.vision_pages": 1},
)

for doc in cursor:
    pages = ((doc.get("extraction") or {}).get("detail") or {}).get("vision_pages") or []
    blanks = [p for p in pages if p.get("needs_human") or (p.get("blocked") and not p.get("text_len", 1))]
    blocked = [p for p in pages if p.get("blocked")]
    low = [p for p in pages if not p.get("blocked") and (p.get("confidence") or 1) < 0.75]

    hard = [p for p in blocked if p.get("needs_human")]
    if hard:
        docs_with_blanks.append((doc.get("filename", "?"), len(hard)))
    blank_pages += len(hard)
    low_conf_pages += len(low)

print(f"\n  pages blank (need recovery)   {blank_pages:>6}")
print(f"  pages low-confidence only     {low_conf_pages:>6}   (text present, unverified)")
print(f"  documents with blank pages    {len(docs_with_blanks):>6}")

if docs_with_blanks:
    print("\n  worst affected documents:")
    for name, count in sorted(docs_with_blanks, key=lambda x: -x[1])[:15]:
        print(f"      {count:>3} page(s)  {str(name)[:64]}")
print()
