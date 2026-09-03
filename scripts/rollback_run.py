"""Undo one ingestion run, precisely.

Deletes only what a given run actually created. The distinction that matters:
`run_id` is overwritten every time an artifact is touched, so deleting on it
would destroy pre-existing artifacts that the run merely re-saw. `first_run_id`
is written once, at insert, so it identifies genuinely new records and nothing
else. This script keys on `first_run_id` only.

Emails that get deleted are put back into `skipped` under a hold reason rather
than dropped, so the messages remain accounted for and can be re-ingested later
through the proper pipeline. Nothing is silently lost.

Registry entries are code, not data, and are left alone.

    python scripts/rollback_run.py run-20260831-183753 --dry-run
    python scripts/rollback_run.py run-20260831-183753 --confirm
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from mangotree.core.logging import get_logger
from mangotree.storage.mongo import get_mongo

log = get_logger()

HOLD_REASON = "hold_pending_segregator"
HOLD_DETAIL = (
    "Ingested prematurely and rolled back. Sender is in the registry; this "
    "message is waiting for the Outlook-connected pipeline with Opus 5 property "
    "segregation, per admin instruction."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true",
                    help="actually delete (required for a real run)")
    args = ap.parse_args()

    if not args.dry_run and not args.confirm:
        ap.error("pass --dry-run to inspect, or --confirm to actually delete")

    db = get_mongo().db
    run = args.run_id

    emails = list(db["artifacts"].find(
        {"first_run_id": run, "source_type": "email"},
        {"sha256": 1, "provider_id": 1, "provider": 1, "subject": 1, "date": 1,
         "property_ids": 1},
    ))
    email_shas = [e["sha256"] for e in emails]

    # An attachment is only ours to delete if this run created it. One created by
    # an earlier run and merely re-linked here must survive.
    attachments = list(db["artifacts"].find(
        {"first_run_id": run, "source_type": "attachment"},
        {"sha256": 1, "filename": 1, "parent_email_shas": 1},
    ))
    att_shas = [a["sha256"] for a in attachments]

    # Attachments this run linked but did not create — these stay, though the
    # link back to a deleted parent has to be cleaned off them.
    relinked = list(db["artifacts"].find(
        {
            "source_type": "attachment",
            "first_run_id": {"$ne": run},
            "parent_email_shas": {"$in": email_shas},
        },
        {"sha256": 1, "filename": 1},
    ))

    all_shas = email_shas + att_shas

    counts = {
        "emails": len(emails),
        "attachments (created by run)": len(attachments),
        "attachments (pre-existing, kept)": len(relinked),
        "chunks": db["chunks"].count_documents({"artifact_sha": {"$in": all_shas}}),
        "extractions": db["extractions"].count_documents({"sha256": {"$in": all_shas}}),
        "occurrences": db["occurrences"].count_documents({"run_id": run}),
        "review_queue": db["review_queue"].count_documents({"run_id": run}),
        "errors": db["errors"].count_documents({"run_id": run}),
    }

    print(f"\n=== rollback of {run} ===")
    for key, value in counts.items():
        print(f"  {key:<36}{value:>6}")

    print("\n  emails to remove (first 20):")
    for e in emails[:20]:
        subject = (e.get("subject") or "(no subject)")[:64]
        props = ",".join(e.get("property_ids") or []) or "-"
        print(f"    {str(e.get('date'))[:10]}  {props:<14}{subject}")
    if len(emails) > 20:
        print(f"    ... and {len(emails) - 20} more")

    if counts["chunks"]:
        print(f"\n  NOTE: {counts['chunks']} chunks exist — embeddings were written.")
    else:
        print("\n  Confirmed: no chunks, so nothing reached the vector database.")

    if args.dry_run:
        print("\ndry run, nothing written")
        return

    now = datetime.now(timezone.utc)
    holds = [
        {
            "provider": e.get("provider", "gmail"),
            "provider_id": e.get("provider_id"),
            "reason": HOLD_REASON,
            "detail": HOLD_DETAIL,
            "date": e.get("date"),
            "discovery_candidates": [],
            "run_id": run,
            "created_at": now,
            "rolled_back_at": now,
        }
        for e in emails
        if e.get("provider_id")
    ]
    if holds:
        db["skipped"].insert_many(holds)
        log.info("re-queued %d emails as %s", len(holds), HOLD_REASON)

    deleted = {
        "artifacts": db["artifacts"].delete_many(
            {"sha256": {"$in": all_shas}}).deleted_count,
        "chunks": db["chunks"].delete_many(
            {"artifact_sha": {"$in": all_shas}}).deleted_count,
        "extractions": db["extractions"].delete_many(
            {"sha256": {"$in": all_shas}}).deleted_count,
        "occurrences": db["occurrences"].delete_many({"run_id": run}).deleted_count,
        "review_queue": db["review_queue"].delete_many({"run_id": run}).deleted_count,
        "errors": db["errors"].delete_many({"run_id": run}).deleted_count,
    }

    if relinked:
        db["artifacts"].update_many(
            {"sha256": {"$in": [r["sha256"] for r in relinked]}},
            {"$pull": {"parent_email_shas": {"$in": email_shas}}},
        )
        log.info("unlinked %d surviving attachments from deleted parents", len(relinked))

    print("\n--- deleted ---")
    for key, value in deleted.items():
        print(f"  {key:<20}{value:>6}")
    print(f"\n  re-queued for later ingest: {len(holds)}")


if __name__ == "__main__":
    main()
