"""Cheap end-to-end check of the Opus 5 segregator before the full run.

Runs a handful of real emails through and prints what the model decided and why,
then rolls the decisions back so the production run re-does them with attachment
text present. Catches prompt and parsing faults for a few cents rather than
discovering them 3,000 calls in.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.resolve.segregation_runner import SegregationRunner
from mangotree.storage.mongo import get_mongo

LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 4


def main() -> int:
    mongo = get_mongo()
    runner = SegregationRunner(mongo, SETTINGS.anthropic_api_key)

    emails = runner._pending(LIMIT)
    if not emails:
        print("nothing pending")
        return 1

    print(f"\n  model: {runner.segregator.model}\n")
    touched = []

    for email in emails:
        attachments = runner._attachments_of(email)
        payload = runner._email_payload(email, attachments)
        result = runner.segregator.segregate(payload, attachments)

        print("=" * 78)
        print(f"  subject : {(payload['subject'] or '(none)')[:70]}")
        print(f"  from    : {payload['from'][:70]}")
        print(f"  hints   : {payload['hints']}")
        print(f"  attach  : {len(attachments)}")
        if result.error:
            print(f"  ERROR   : {result.error}")
            continue
        decision = result.email
        print(f"  -> properties {decision.properties}  conf={decision.confidence:.2f}"
              f"  unresolved={decision.unresolved}  scope={decision.scope}")
        if decision.out_of_scope:
            print(f"  -> out of scope: {decision.out_of_scope}")
        print(f"  -> {decision.reasoning[:300]}")
        for attachment in attachments:
            att = result.attachments.get(attachment["sha256"])
            if att:
                print(f"     [{attachment['filename']}] {att.properties} "
                      f"conf={att.confidence:.2f} :: {att.reasoning[:140]}")
        print(f"  tokens  : in={result.input_tokens} out={result.output_tokens}")
        touched.append(email["sha256"])

    # Roll back so the real run redoes these with attachment text extracted.
    if touched:
        mongo.artifacts.update_many(
            {"sha256": {"$in": touched}}, {"$unset": {"segregation": ""}}
        )
        print(f"\n  rolled back {len(touched)} decision(s) for the real run")

    print(f"\n  totals: {runner.segregator.calls} calls, "
          f"{runner.segregator.input_tokens} in / {runner.segregator.output_tokens} out\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
