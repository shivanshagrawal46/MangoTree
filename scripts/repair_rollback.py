"""Finish the rollback of a run: re-queue its emails and clear orphaned objects.

The rollback deleted artifacts but could not re-queue the emails, because the
artifact record does not carry `provider_id` — it lives only in the object store
sidecar and in `occurrences`, and occurrences went with the rollback. The object
store was never touched, so the Gmail message ids are still on disk in the
`.meta.json` files and can be recovered from there.

That leaves two things to fix, both of which this handles:

  1. The messages are untracked. Nothing is lost (Gmail is the source of truth),
     but with no queue entry they would only reappear by luck on a future
     backfill. They get written back to `skipped` under a hold reason.

  2. The stored originals are orphaned — bytes on disk with no artifact pointing
     at them. Left alone they would make a re-ingest silently skip storing the
     original, since `put` short-circuits when the hash already exists.

    python scripts/repair_rollback.py run-20260831-183753 --dry-run
    python scripts/repair_rollback.py run-20260831-183753 --confirm
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import get_logger
from mangotree.storage.mongo import get_mongo

log = get_logger()

HOLD_REASON = "hold_pending_segregator"
HOLD_DETAIL = (
    "Ingested prematurely on 2026-08-31 and rolled back at admin instruction. "
    "Sender is registered; message awaits the Outlook-connected pipeline with "
    "Opus 5 property segregation."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not args.confirm:
        ap.error("pass --dry-run to inspect, or --confirm to apply")

    db = get_mongo().db
    root = Path(SETTINGS.raw_store)
    if not root.exists():
        raise SystemExit(f"object store root not found: {root}")

    emails, attachments, other = [], [], []
    for meta_path in root.rglob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("unreadable sidecar %s: %s", meta_path, exc)
            continue
        if meta.get("run_id") != args.run_id:
            continue
        sha = meta.get("sha256")
        # Only orphans are in scope. If an artifact still references the hash,
        # something else owns these bytes and they must not be removed.
        if db["artifacts"].count_documents({"sha256": sha}, limit=1):
            other.append((meta_path, meta))
            continue
        if meta.get("source_type") == "email":
            emails.append((meta_path, meta))
        else:
            attachments.append((meta_path, meta))

    with_id = [m for _, m in emails if m.get("provider_id") not in (None, "", "None")]

    print(f"\n=== repair {args.run_id} ===")
    print(f"  orphaned email objects        {len(emails):>5}")
    print(f"    of those, provider_id found {len(with_id):>5}")
    print(f"  orphaned attachment objects   {len(attachments):>5}")
    print(f"  still referenced, keeping     {len(other):>5}")

    already = db["skipped"].count_documents({"reason": HOLD_REASON})
    print(f"  existing hold records         {already:>5}")

    if with_id:
        print("\n  sample recovered ids:")
        for meta in with_id[:5]:
            print(f"    {meta['provider_id']}  {meta.get('original_filename', '')}")

    if args.dry_run:
        print("\ndry run, nothing written")
        return

    now = datetime.now(timezone.utc)
    inserted = 0
    for meta in with_id:
        result = db["skipped"].update_one(
            {"provider": "gmail", "provider_id": meta["provider_id"],
             "reason": HOLD_REASON},
            {"$setOnInsert": {
                "provider": "gmail",
                "provider_id": meta["provider_id"],
                "reason": HOLD_REASON,
                "detail": HOLD_DETAIL,
                "date": None,
                "discovery_candidates": [],
                "run_id": args.run_id,
                "created_at": now,
            }},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted += 1
    log.info("re-queued %d emails as %s", inserted, HOLD_REASON)

    removed = 0
    for meta_path, meta in emails + attachments:
        blob = meta_path.with_name(meta_path.name.replace(".meta.json", ""))
        for path in (blob, meta_path):
            try:
                if path.exists():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                log.warning("could not remove %s: %s", path, exc)
    log.info("removed %d orphaned files (blobs + sidecars)", removed)

    print(f"\n  re-queued            {inserted}")
    print(f"  files removed        {removed}")
    print(f"  hold records now     {db['skipped'].count_documents({'reason': HOLD_REASON})}")


if __name__ == "__main__":
    main()
