"""Print one property's timeline, to show the shape of what is being built.

Every event carries exactly one ``property_id``. A document concerning two
properties produces two events, one filed under each, rather than a single
shared event — so a property's timeline is complete on its own and never has to
be assembled by filtering someone else's.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo
from mangotree.timeline.runner import TimelineBuilder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--property", default="varnum")
    parser.add_argument("--limit", type=int, default=18)
    parser.add_argument("--extracted-only", action="store_true",
                        help="only events read out of document text")
    args = parser.parse_args()

    mongo = get_mongo()
    builder = TimelineBuilder(mongo)
    rows = builder.property_timeline(args.property)

    if args.extracted_only:
        rows = [r for r in rows if r.get("extracted_by") != "deterministic"]

    print(f"\n  TIMELINE — {args.property}   ({len(rows):,} events)\n")
    for row in rows[: args.limit]:
        when = row["occurred_at"].strftime("%Y-%m-%d") if row.get("occurred_at") else "undated  "
        amount = f"  ${row['amount']:,.0f}" if row.get("amount") else ""
        src = "model" if row.get("extracted_by") != "deterministic" else "doc"
        print(f"    {when}  {row['event_type']:<14} {row['title'][:58]}{amount}")
        print(f"                {src:<6} {(row.get('source_name') or row.get('source_ref') or '')[:62]}")
        if row.get("quote"):
            print(f"                quote: \"{row['quote'][:78]}\"")

    print(f"\n  EVENT TYPES FOR {args.property}")
    counts = {}
    for row in rows:
        counts[row["event_type"]] = counts.get(row["event_type"], 0) + 1
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {kind:<16} {n:>5,}")
    print()


if __name__ == "__main__":
    main()
