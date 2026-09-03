"""Unattended watcher for the finish-remaining run.

Prints one compact status line per poll plus sentinel lines on anything that
needs attention, so progress can be followed without a human reading a
full-screen dashboard:

    STAGE-CHANGE   a stage started or exited
    ALERT          errors climbing, or a stage making no progress
    PIPELINE-DONE  the runner finished
    PIPELINE-FAIL  a required stage failed and the runner stopped

Stall detection is per stage rather than global: segregation writes a decision
every few seconds, indexing writes chunks in bursts with long quiet gaps while a
document's Tier-1 batch is in flight, so one global timeout would either cry
wolf on indexing or sleep through a wedged segregation.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo

FINISH_LOG = Path("logs/finish.log")
POLL_SECONDS = 60

#: How long a stage may show no forward movement before it is called out.
STALL_MINUTES = {"segregate": 12, "index": 25, "graph": 30, "reocr": 25}
DEFAULT_STALL_MINUTES = 20

_START = re.compile(r"START (\S+)")
_EXIT = re.compile(r"^\s*(\S+) exit=(-?\d+)")


def stage_from_log() -> tuple[str, str]:
    """(current stage, last terminal event) as the runner itself recorded it."""
    if not FINISH_LOG.exists():
        return "?", ""
    stage, last_event = "?", ""
    for line in FINISH_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        body = line[10:] if len(line) > 10 else line
        start = _START.search(body)
        if start:
            stage = start.group(1)
            continue
        exited = _EXIT.match(body)
        if exited:
            last_event = f"{exited.group(1)} exit={exited.group(2)}"
            if exited.group(2) != "0":
                stage = f"{exited.group(1)}(failed)"
        if "STOPPING" in body:
            last_event = body.strip()
        if "finish-remaining complete" in body:
            stage = "complete"
    return stage, last_event


def snapshot(mongo) -> dict:
    art = mongo.artifacts
    return {
        "segregated": art.count_documents({"source_type": "email", "segregation": {"$exists": True}}),
        "emails": art.count_documents({"source_type": "email"}),
        "assigned": art.count_documents({"property_ids": {"$ne": []}}),
        "chunks": mongo.chunks.count_documents({}),
        "entities": mongo.db["entities"].count_documents({}),
        "edges": mongo.db["entity_edges"].count_documents({}),
        "seg_errors": mongo.review_queue.count_documents({"kind": "segregation_error"}),
    }


def main() -> int:
    mongo = get_mongo()
    previous_stage = None
    previous_progress = None
    last_movement = time.time()
    alerted_stall = False
    last_errors = 0

    print("WATCH-START  polling every 60s", flush=True)

    while True:
        stage, last_event = stage_from_log()
        snap = snapshot(mongo)
        now = datetime.now().strftime("%H:%M:%S")

        # Progress is whatever the *current* stage advances, so a busy stage is
        # never judged by a counter it does not touch.
        if stage.startswith("segregate"):
            progress = snap["segregated"]
            shown = f"segregated {snap['segregated']:,}/{snap['emails']:,}"
        elif stage.startswith("index"):
            progress = snap["chunks"]
            shown = f"chunks {snap['chunks']:,}"
        elif stage.startswith("graph"):
            progress = snap["entities"] + snap["edges"]
            shown = f"entities {snap['entities']:,} edges {snap['edges']:,}"
        else:
            progress = snap["chunks"] + snap["segregated"]
            shown = f"assigned {snap['assigned']:,}"

        if stage != previous_stage:
            print(f"STAGE-CHANGE {now}  {previous_stage} -> {stage}  {last_event}", flush=True)
            previous_stage, last_movement, alerted_stall = stage, time.time(), False
            previous_progress = progress

        if progress != previous_progress:
            last_movement, alerted_stall = time.time(), False
            previous_progress = progress

        if snap["seg_errors"] > last_errors + 25:
            print(
                f"ALERT {now}  segregation errors climbing: {snap['seg_errors']:,}",
                flush=True,
            )
            last_errors = snap["seg_errors"]

        idle_minutes = (time.time() - last_movement) / 60
        limit = STALL_MINUTES.get(stage.split("(")[0], DEFAULT_STALL_MINUTES)
        if idle_minutes > limit and not alerted_stall:
            print(
                f"ALERT {now}  '{stage}' has not advanced in {idle_minutes:.0f} min",
                flush=True,
            )
            alerted_stall = True

        print(
            f"  {now}  stage={stage:<18} {shown:<30} "
            f"errors={snap['seg_errors']}",
            flush=True,
        )

        if stage == "complete":
            print(
                f"PIPELINE-DONE {now}  chunks={snap['chunks']:,} "
                f"entities={snap['entities']:,} edges={snap['edges']:,}",
                flush=True,
            )
            return 0
        if "STOPPING" in last_event:
            print(f"PIPELINE-FAIL {now}  {last_event}", flush=True)
            return 1

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
