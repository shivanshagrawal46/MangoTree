"""Re-drive dead-lettered Outlook messages through the pipeline.

The backfill records a failure and moves on so one bad message cannot stop a
multi-hour run. That is the right behaviour during the run and the wrong place
to leave things afterwards: a recorded loss is still a loss. This replays those
messages once the underlying defect is fixed, and clears the error only when the
message actually lands.

Safe to run repeatedly. Messages that still fail keep their error record, with
the new error text, so a second defect does not masquerade as the first.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.ingest.graph_auth import GraphDelegatedAuth
from mangotree.ingest.graph_client import GraphClient
from mangotree.ingest.outlook_backfill import SCOPED_FOLDERS, direction_folder_for
from mangotree.ingest.pipeline import EmailPipeline
from mangotree.storage.mongo import get_mongo

#: Stages whose failures are recoverable by replaying the message. A parse or
#: process failure means we hold the bytes but mishandled them; a fetch failure
#: means the download itself failed and is equally worth another attempt.
RETRYABLE_STAGES = ["parse", "outlook_process", "outlook_fetch"]


def _scoped_folder_ids(client: GraphClient) -> dict:
    """Graph folder id -> scoped folder path, for the six folders in scope."""
    out = {}
    for path in SCOPED_FOLDERS:
        try:
            folder_id = client.folder_id_for(path)
        except Exception:
            folder_id = None
        if folder_id:
            out[folder_id] = path
    return out


def _folder_label_for(client: GraphClient, message_id: str, folder_ids: dict) -> str:
    """INBOX or SENT for a replayed message.

    Direction is read back from Graph rather than assumed: it decides how the
    message is routed, and defaulting a sent message to INBOX would quietly
    misfile it.
    """
    try:
        response = client._request(
            "GET",
            f"{client._mailbox_root()}/messages/{message_id}",
            params={"$select": "parentFolderId"},
        )
        response.raise_for_status()
        parent = response.json().get("parentFolderId", "")
        path = folder_ids.get(parent)
        if path:
            return direction_folder_for(path)
    except Exception as exc:
        logger.warning("  folder lookup failed for %s: %s", str(message_id)[:24], exc)
    return "INBOX"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    mongo = get_mongo()

    query = {"stage": {"$in": RETRYABLE_STAGES}}
    failures = list(mongo.errors.find(query))
    if args.limit:
        failures = failures[: args.limit]

    if not failures:
        print("\n  no dead-lettered messages — nothing to replay\n")
        return 0

    print(f"\n  replaying {len(failures)} dead-lettered message(s)")
    for failure in failures:
        print(f"    {failure.get('error', '?')[:60]:<62} {str(failure.get('key'))[:30]}")

    if args.dry_run:
        print("\n  dry run — nothing was changed\n")
        return 0

    auth = GraphDelegatedAuth(
        tenant_id=SETTINGS.graph_tenant_id,
        client_id=SETTINGS.graph_client_id,
        mailbox=SETTINGS.graph_mailbox,
    )
    client = GraphClient(auth)
    pipeline = EmailPipeline(mongo, run_id="retry-dead-letters")
    folder_ids = _scoped_folder_ids(client)

    recovered = failed = 0
    for failure in failures:
        message_id = failure.get("key")
        if not message_id or message_id == "?":
            continue

        try:
            raw = client.raw_mime(message_id)
        except Exception as exc:
            logger.warning("  fetch still failing for %s: %s", str(message_id)[:24], exc)
            failed += 1
            continue

        folder_label = _folder_label_for(client, message_id, folder_ids)

        try:
            pipeline.process_raw_email(
                raw,
                mailbox=SETTINGS.graph_mailbox,
                provider="outlook",
                provider_id=message_id,
                folder=folder_label,
            )
            mongo.errors.delete_one({"_id": failure["_id"]})
            recovered += 1
            logger.info("  recovered %s (%s, %d bytes)",
                        str(message_id)[:24], folder_label, len(raw))
        except Exception as exc:
            mongo.errors.update_one(
                {"_id": failure["_id"]},
                {"$set": {"error": str(exc), "error_type": type(exc).__name__,
                          "retried": True}},
            )
            failed += 1
            logger.warning("  still failing %s: %s", str(message_id)[:24], exc)

    print(f"\n  recovered      {recovered}")
    print(f"  still failing  {failed}")
    print(f"  stats          {pipeline.stats.as_dict()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
