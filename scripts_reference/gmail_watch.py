"""Arm / renew Gmail push notifications (users.watch) for a label.

Gmail's watch() must be re-called before it expires (~7 days), so this runs
daily (systemd timer / cron). It publishes change notifications for the
watched label to a Cloud Pub/Sub topic; the push worker consumes them.

Also holds the shared watch-state helpers (last_history_id per label) that the
worker imports.

Env / args:
    GMAIL_PUBSUB_TOPIC   projects/<project-id>/topics/<topic-id>   (or --topic)

Usage:
    python -m scripts.gmail_watch --label "__....Boris Lawsuit"
    python -m scripts.gmail_watch --label "__....Boris Lawsuit" --stop
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings                    # noqa: E402
from src.db.mongo import MongoClientWrapper             # noqa: E402
from src.ingest.gmail_client import GmailClient         # noqa: E402
from src.utils.logger import logger                     # noqa: E402

STATE_COLLECTION = "gmail_watch_state"


def get_state(mongo: MongoClientWrapper, label: str) -> Optional[Dict[str, Any]]:
    return mongo.db[STATE_COLLECTION].find_one({"_id": label})


def set_history_id(mongo: MongoClientWrapper, label: str, history_id: str) -> None:
    mongo.db[STATE_COLLECTION].update_one(
        {"_id": label},
        {"$set": {"last_history_id": str(history_id),
                  "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


def arm_watch(client: GmailClient, mongo: MongoClientWrapper, *,
              label: str, topic: str) -> Dict[str, Any]:
    """(Re)arm watch for `label`. Seeds last_history_id on first arm only,
    so the worker's low-water mark is never rewound."""
    label_id = client.resolve_labels([label])[label]
    resp = client.watch(topic_name=topic, label_ids=[label_id])
    hist = str(resp.get("historyId"))
    exp = resp.get("expiration")
    exp_dt = (datetime.fromtimestamp(int(exp) / 1000, tz=timezone.utc)
              if exp else None)
    mongo.db[STATE_COLLECTION].update_one(
        {"_id": label},
        {"$set": {"label_id": label_id,
                  "topic": topic,
                  "watch_expiration": exp_dt,
                  "armed_history_id": hist,
                  "updated_at": datetime.now(timezone.utc)},
         "$setOnInsert": {"last_history_id": hist}},
        upsert=True,
    )
    logger.info(f"[watch] armed label={label!r} label_id={label_id} "
                f"historyId={hist} expires={exp_dt}")
    return {"label_id": label_id, "history_id": hist, "expiration": exp_dt}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="__....Boris Lawsuit")
    ap.add_argument("--topic", default=os.getenv("GMAIL_PUBSUB_TOPIC"),
                    help="Full Pub/Sub topic: projects/<proj>/topics/<id>")
    ap.add_argument("--stop", action="store_true", help="Disable all push notifications.")
    args = ap.parse_args()

    settings = Settings.load()
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    mongo.ping()
    client = GmailClient().authenticate()

    if args.stop:
        client.stop_watch()
        logger.info("[watch] push notifications stopped.")
        mongo.close()
        return 0

    if not args.topic:
        logger.error("No Pub/Sub topic. Pass --topic or set GMAIL_PUBSUB_TOPIC.")
        return 2

    arm_watch(client, mongo, label=args.label, topic=args.topic)
    mongo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
