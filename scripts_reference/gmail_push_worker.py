"""Real-time Gmail push worker (Cloud Pub/Sub pull subscriber).

Runs forever on the DigitalOcean server. Gmail -> Pub/Sub delivers a tiny
notification whenever the watched label changes; this worker resolves exactly
which new message(s) arrived (via history.list) and ingests only those through
the scoped realtime pipeline. No polling, no public endpoint.

Auth:
    GOOGLE_APPLICATION_CREDENTIALS  -> service-account JSON with role
                                       roles/pubsub.subscriber
    PUBSUB_SUBSCRIPTION             -> projects/<proj>/subscriptions/<sub>
    GMAIL_PUBSUB_TOPIC             -> projects/<proj>/topics/<id> (for watch renew)

Usage:
    python -m scripts.gmail_push_worker --label "__....Boris Lawsuit"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings                    # noqa: E402
from src.db.mongo import MongoClientWrapper             # noqa: E402
from src.ingest.gmail_client import GmailClient         # noqa: E402
from src.ingest.realtime_ingest import process_gmail_ids  # noqa: E402
from src.utils.logger import logger                     # noqa: E402
from scripts.gmail_watch import arm_watch, get_state, set_history_id  # noqa: E402


def _require_pubsub():
    try:
        from google.cloud import pubsub_v1  # noqa: F401
        return True
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Real-time worker needs google-cloud-pubsub:\n"
            "    python -m pip install google-cloud-pubsub\n"
            f"(import error: {exc})") from exc


def _message_ids_from_history(client: GmailClient, *, start_history_id: str,
                              label_id: str) -> List[str]:
    ids: Set[str] = set()
    for h in client.list_history(start_history_id=start_history_id, label_id=label_id):
        for ma in h.get("messagesAdded", []) or []:
            m = ma.get("message", {}) or {}
            if label_id in (m.get("labelIds") or []):
                ids.add(m["id"])
        for la in h.get("labelsAdded", []) or []:
            m = la.get("message", {}) or {}
            if label_id in (la.get("labelIds") or []) and m.get("id"):
                ids.add(m["id"])
    return list(ids)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="__....Boris Lawsuit")
    ap.add_argument("--subscription", default=os.getenv("PUBSUB_SUBSCRIPTION"))
    ap.add_argument("--topic", default=os.getenv("GMAIL_PUBSUB_TOPIC"))
    args = ap.parse_args()

    if not args.subscription:
        logger.error("No subscription. Pass --subscription or set PUBSUB_SUBSCRIPTION.")
        return 2

    _require_pubsub()
    from google.cloud import pubsub_v1

    settings = Settings.load()
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    mongo.ping()
    client = GmailClient().authenticate()

    # Ensure watch is armed (also seeds baseline history id on first run).
    if args.topic:
        arm_watch(client, mongo, label=args.label, topic=args.topic)
    st = get_state(mongo, args.label)
    if not st or not st.get("label_id"):
        logger.error("Watch not armed and no --topic given. Run gmail_watch first.")
        return 2
    label_id = st["label_id"]

    lock = threading.Lock()

    def handle(message) -> None:
        try:
            payload = json.loads(message.data.decode("utf-8"))
            new_hist = str(payload.get("historyId"))
            logger.info(f"[worker] notification historyId={new_hist}")
            with lock:
                state = get_state(mongo, args.label) or {}
                start = state.get("last_history_id") or new_hist
                try:
                    ids = _message_ids_from_history(
                        client, start_history_id=start, label_id=label_id)
                except Exception as exc:  # noqa: BLE001
                    # History expired (>~1 week) — fall back to a 2-day label scan.
                    logger.warning(f"[worker] history.list failed ({exc}); "
                                   "falling back to recent-label scan.")
                    after = datetime.now(timezone.utc) - timedelta(days=2)
                    ids = list(client.iter_message_ids(
                        label_ids=[label_id], after=after))
                if ids:
                    logger.info(f"[worker] {len(ids)} new message(s): {ids}")
                    process_gmail_ids(ids, label_names=[args.label],
                                      settings=settings, mongo=mongo, client=client)
                else:
                    logger.info("[worker] no new labelled messages in delta.")
                set_history_id(mongo, args.label, new_hist)
            message.ack()
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[worker] handler error: {exc}")
            message.nack()

    subscriber = pubsub_v1.SubscriberClient()
    # One message at a time: heavy pipeline must not overlap.
    flow = pubsub_v1.types.FlowControl(max_messages=1)
    future = subscriber.subscribe(args.subscription, callback=handle, flow_control=flow)
    logger.info(f"[worker] listening on {args.subscription} for label {args.label!r}")
    try:
        future.result()
    except KeyboardInterrupt:
        future.cancel()
        future.result()
    finally:
        mongo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
