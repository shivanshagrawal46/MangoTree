"""Build per-property timelines across the whole corpus.

Two passes, deliberately separated:

* deterministic — one event per (artifact, property), so a property's timeline
  can never silently omit something we hold. Coverage is guaranteed by
  construction rather than by a model behaving well.
* model — events *described inside* the text ("payoff received 12 Feb",
  "extension granted through June"), which is where the actual chronology of a
  deal lives. Every extracted event must carry a verbatim quote or it is
  rejected.

Run with ``--no-model`` to get coverage quickly, then again with the model pass.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, ".")

from dotenv import load_dotenv

from mangotree.storage.mongo import get_mongo
from mangotree.timeline.runner import TimelineBuilder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-model", action="store_true", help="deterministic pass only")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--property", dest="properties", action="append")
    parser.add_argument(
        "--concurrency", type=int, default=30,
        help="documents read in parallel; the stage is latency-bound, not compute-bound",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="re-read documents the model has already extracted from",
    )
    args = parser.parse_args()

    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    use_model = not args.no_model
    if use_model and not key:
        print("ANTHROPIC_API_KEY not set — running deterministic pass only")
        use_model = False

    mongo = get_mongo()
    builder = TimelineBuilder(
        mongo,
        anthropic_api_key=key if use_model else None,
        concurrency=args.concurrency,
    )

    result = builder.run(
        property_ids=args.properties,
        use_model=use_model,
        limit=args.limit,
        force=args.force,
    )

    print("\n  TIMELINE BUILD")
    print(f"    document-level events   {result['document_events']:>7,}")
    print(f"    model-extracted events  {result['extracted_events']:>7,}")
    for key_, value in (result.get("extract_stats") or {}).items():
        if isinstance(value, (int, float)):
            print(f"    {key_:<24}{value:>7,}")

    print("\n  COVERAGE PER PROPERTY")
    print(f"    {'property':<18} {'events':>7} {'dated':>7} {'extracted':>10}  span")
    for row in builder.coverage():
        first = row["first"].strftime("%Y-%m") if row.get("first") else "?"
        last = row["last"].strftime("%Y-%m") if row.get("last") else "?"
        print(
            f"    {str(row['_id']):<18} {row['events']:>7,} {row['dated']:>7,} "
            f"{row['extracted']:>10,}  {first} -> {last}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
