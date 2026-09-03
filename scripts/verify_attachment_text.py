"""Spot-check that attachment extraction produced real, usable text.

A char count proves the pipeline ran, not that it worked. A settlement statement
that OCR'd into 4,000 characters of nothing is worse than one that failed loudly,
because it will be retrieved and cited. So this prints actual content from the
documents that matter most, and flags the ones still empty.
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

KEY_TERMS = ("alta", "commitment", "title", "payoff", "deed", "note",
             "settlement", "policy", "budget", "invoice", "draw")


def main() -> None:
    db = get_mongo().db

    print("=" * 78)
    print("KEY ATTACHMENTS — CONTENT CHECK")
    print("=" * 78)

    rows = list(db["artifacts"].find(
        {"source_type": "attachment"},
        {"filename": 1, "text": 1, "property_ids": 1, "extraction": 1,
         "date": 1, "sha256": 1},
    ))

    interesting = [
        a for a in rows
        if any(term in (a.get("filename") or "").lower() for term in KEY_TERMS)
    ]
    print(f"\n{len(interesting)} attachments matching key financial/legal terms\n")

    for a in sorted(interesting, key=lambda r: -(len(r.get("text") or "")))[:14]:
        text = (a.get("text") or "").strip()
        extraction = a.get("extraction") or {}
        print("-" * 74)
        print(f"{a.get('filename')}")
        print(f"  props   : {a.get('property_ids')}")
        print(f"  chars   : {len(text):,}   method: {extraction.get('method')}   "
              f"status: {extraction.get('status')}   conf: {extraction.get('confidence')}")
        if text:
            # Money and dates are the load-bearing content in these documents;
            # if they are absent the read is suspect regardless of length.
            money = re.findall(r"\$\s?[\d,]+\.?\d{0,2}", text)
            dates = re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", text)
            print(f"  money   : {len(money)} amounts, e.g. {money[:6]}")
            print(f"  dates   : {len(dates)} dates, e.g. {dates[:5]}")
            snippet = re.sub(r"\s+", " ", text[:420])
            print(f"  opening : {snippet}")
        else:
            print("  EMPTY")

    print("\n" + "=" * 78)
    print("STILL WITHOUT TEXT")
    print("=" * 78)
    for a in rows:
        if (a.get("text") or "").strip():
            continue
        extraction = a.get("extraction") or {}
        print(f"  {a.get('filename')}")
        print(f"      status={extraction.get('status')} "
              f"method={extraction.get('method')} "
              f"reason={str(extraction.get('reason'))[:90]}")


if __name__ == "__main__":
    main()
