"""Finish the entire pipeline unattended, from wherever it currently stands.

Written to run while nobody is watching, which drives three choices:

* it waits for the ingestion and extraction already in flight instead of racing
  them — two extractors on one queue bill twice for the same page, which has
  already happened once tonight;
* no stage may block on a prompt, so every billed command is passed --yes, the
  cost having been approved in advance;
* a stage that fails is logged and the chain continues, unless the next stage
  needs its output, in which case stopping is better than producing a corpus
  that looks complete and is not.

Every stage writes its own log under logs/, and the run ends with the twelve-point
verification so the morning starts with evidence rather than a claim.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

LOGS = Path("logs")
LOGS.mkdir(exist_ok=True)

PROGRESS = LOGS / "overnight_progress.json"
CONSOLE = LOGS / "overnight.log"

#: The jobs already running when this was launched. Everything below depends on
#: both being finished: extraction must see Outlook's attachments, and property
#: resolution must see Outlook's mail.
WAIT_FOR_PIDS = [29612, 27904]


def say(message: str) -> None:
    line = f"{datetime.now():%H:%M:%S}  {message}"
    print(line, flush=True)
    with open(CONSOLE, "a", encoding="utf-8", errors="replace") as handle:
        handle.write(line + "\n")


def is_running(pid: int) -> bool:
    """True while the process is alive. Uses tasklist so no dependency is needed."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception:
        return False
    return str(pid) in out


def wait_for_running_jobs(poll_seconds: int = 60) -> None:
    if not WAIT_FOR_PIDS:
        return
    say(f"waiting for in-flight jobs {WAIT_FOR_PIDS}")
    last_report = 0.0
    while True:
        alive = [pid for pid in WAIT_FOR_PIDS if is_running(pid)]
        if not alive:
            say("in-flight jobs finished")
            return
        now = time.time()
        if now - last_report > 900:  # a line every 15 minutes, not every minute
            say(f"  still running: {alive}")
            last_report = now
        time.sleep(poll_seconds)


@dataclass
class Stage:
    name: str
    args: Sequence[str]
    script: Optional[str] = None
    required: bool = False
    note: str = ""


STAGES: List[Stage] = [
    Stage(
        "clear-text-layer",
        [],
        script="scripts/clear_text_layer_extractions.py",
        note="drop any text-layer extractions the stale processes wrote",
    ),
    Stage(
        "retry-dead-letters",
        [],
        script="scripts/retry_failed_messages.py",
        note="replay messages the old MIME parser dropped",
    ),
    Stage(
        "reresolve",
        ["reresolve"],
        note="re-run property resolution so all mail gets the bare-alias fix",
    ),
    Stage(
        "extract",
        ["extract", "--yes"],
        required=True,
        note="every PDF page through Claude vision, GPT-5 fallback; Excel/Word native",
    ),
    Stage(
        "reocr",
        ["reocr"],
        note="GPT-5 re-reads pages Claude refused or read poorly",
    ),
    Stage(
        "segregate",
        ["segregate", "--yes"],
        required=True,
        note="Opus 5 assigns the property for every email and attachment",
    ),
    Stage(
        "index",
        ["index"],
        required=True,
        note="1000/200 token chunks, Tier-1 context, voyage-4-large embeddings",
    ),
    Stage(
        "graph",
        ["graph"],
        note="entities and edges, stamped onto every chunk",
    ),
    Stage(
        "vector-index",
        ["vector-index", "--status"],
        note="confirm the index is queryable",
    ),
]


def run_stage(stage: Stage) -> dict:
    log_path = LOGS / f"stage_{stage.name}.log"
    if stage.script:
        command = [sys.executable, "-u", stage.script, *stage.args]
    else:
        command = [sys.executable, "-u", "-m", "mangotree.cli", *stage.args]

    say(f"START {stage.name} — {stage.note}")
    started = time.time()
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as handle:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                stdin=subprocess.DEVNULL,  # a prompt must fail fast, never hang
            )
            for line in process.stdout:
                handle.write(line)
                handle.flush()
            code = process.wait()
    except Exception as exc:
        say(f"  {stage.name} could not start: {exc}")
        code = -1

    minutes = (time.time() - started) / 60
    say(f"  {stage.name} exit={code} in {minutes:.1f} min  (log: {log_path.name})")
    return {"stage": stage.name, "exit": code, "minutes": round(minutes, 1)}


def main() -> int:
    say("=" * 70)
    say("overnight run starting")
    say("=" * 70)

    wait_for_running_jobs()

    summary: List[dict] = []
    for stage in STAGES:
        result = run_stage(stage)
        summary.append(result)
        PROGRESS.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        if result["exit"] != 0 and stage.required:
            say(f"STOPPING — required stage '{stage.name}' failed")
            break

    say("=" * 70)
    for row in summary:
        say(f"  [{'ok  ' if row['exit'] == 0 else 'FAIL'}] "
            f"{row['stage']:<20} {row['minutes']:>7.1f} min")

    say("running verification")
    try:
        verify = subprocess.run(
            [sys.executable, "scripts/verify_ingestion.py"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=3600,
        )
        (LOGS / "verify.log").write_text(
            verify.stdout + verify.stderr, encoding="utf-8", errors="replace"
        )
        say("verification written to logs/verify.log")
    except Exception as exc:
        say(f"verification failed to run: {exc}")

    say("overnight run complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
