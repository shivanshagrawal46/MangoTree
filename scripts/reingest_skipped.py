"""Re-run skipped mail through the pipeline after the registry has grown.

When a counterparty is approved, their old mail is still sitting in the `skipped`
collection — approving them does nothing on its own. This re-fetches those
messages and puts them back through the normal pipeline, which re-evaluates the
participant policy against the current registry.

Nothing here decides anything: the pipeline is the sole judge of whether a
message now qualifies. This only reopens the question. A skip record is deleted
only once the message has actually landed as an artifact, so a crash mid-run
leaves the remaining skips intact and the job can simply be run again.

Gmail only. Disk `.eml` skips are handled by re-running the disk ingest, and
Outlook skips do not exist yet.

    python scripts/reingest_skipped.py --dry-run
    python scripts/reingest_skipped.py
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")

from mangotree.config.registry import ADDRESS_INDEX
from mangotree.config.settings import SETTINGS
from mangotree.ingest.gmail_client import GmailClient
from mangotree.ingest.pipeline import EmailPipeline
from mangotree.storage.mongo import get_mongo
from mangotree.core.logging import get_logger

log = get_logger()

REOPENABLE = ["skip_unknown_external", "skip_no_rkb"]

#: Gmail's own folders. The admin scoped ingestion to Inbox and Sent, so a
#: message that now has a known participant but lives in neither stays skipped.
IN_SCOPE_LABELS = {"INBOX", "SENT"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be reopened without writing")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    mongo = get_mongo()
    db = mongo.db

    skips = list(db["skipped"].find({
        "provider": "gmail",
        "reason": {"$in": REOPENABLE},
    }))
    log.info("reopenable gmail skips: %d", len(skips))

    # Only bother fetching messages whose recorded discovery candidates are now
    # in the registry. The rest would just be skipped again, and re-fetching all
    # 345 to prove that wastes API calls.
    now_known, still_unknown = [], []
    for s in skips:
        candidates = [str(a).strip().lower() for a in (s.get("discovery_candidates") or [])]
        if any(a in ADDRESS_INDEX for a in candidates):
            now_known.append(s)
        else:
            still_unknown.append(s)

    log.info("now have a registered participant: %d", len(now_known))
    log.info("still unknown, leaving skipped:    %d", len(still_unknown))

    if args.dry_run:
        matched: Counter = Counter()
        for s in now_known:
            for a in (s.get("discovery_candidates") or []):
                key = str(a).strip().lower()
                if key in ADDRESS_INDEX:
                    matched[ADDRESS_INDEX[key].display_name] += 1
        print("\nwould reopen, by newly-registered party:")
        for name, count in matched.most_common():
            print(f"  {count:>4}  {name}")
        return

    if not now_known:
        log.info("nothing to reopen")
        return

    gc = GmailClient(
        client_secret_path=SETTINGS.gmail_client_secret,
        token_path=SETTINGS.gmail_token_path,
    ).authenticate()

    pipeline = EmailPipeline(mongo)
    log.info("run_id=%s", pipeline.run_id)

    def fetch(skip):
        try:
            return skip, gc.get_raw(skip["provider_id"]), None
        except Exception as exc:
            return skip, None, exc

    outcome: Counter = Counter()
    ingested_ids = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for skip, msg, exc in ex.map(fetch, now_known):
            if exc is not None:
                log.warning("fetch failed %s: %s", skip.get("provider_id"), exc)
                outcome["fetch_error"] += 1
                continue

            labels = msg.get("label_ids") or []
            if not (set(labels) & IN_SCOPE_LABELS):
                outcome["out_of_scope_folder"] += 1
                continue

            folder = "SENT" if "SENT" in labels else "INBOX"
            sha = pipeline.process_raw_email(
                msg["raw"],
                mailbox=gc.address or "rakesh.bhargava@gmail.com",
                provider="gmail",
                provider_id=msg["id"],
                provider_thread_id=msg.get("thread_id"),
                labels=labels,
                folder=folder,
            )
            if sha:
                outcome["ingested"] += 1
                ingested_ids.append(skip["_id"])
            else:
                outcome["skipped_again"] += 1

    # Clear skip records only for messages that actually landed.
    if ingested_ids:
        deleted = db["skipped"].delete_many({"_id": {"$in": ingested_ids}}).deleted_count
        log.info("cleared %d skip records", deleted)

    print("\n--- outcome ---")
    for key, count in outcome.most_common():
        print(f"  {key:<24}{count:>5}")
    print("\n--- pipeline stats ---")
    for key, value in pipeline.stats.as_dict().items():
        if key != "discovery_candidates":
            print(f"  {key:<24}{value}")


if __name__ == "__main__":
    main()
