"""Finish the pipeline, including the disk corpus ingested after the first runner started.

The original overnight runner fixed its stage list at launch, before the 231
files from ``E:\\LP Remodeling Projects\\Hold Properties`` existed in the
corpus. Those files must be extracted before Opus assigns properties, or the
segregation stage judges a PDF by its filename while its text sits unread.

Waits for any extraction still in flight so two extractors never share a queue —
that mistake billed the same pages twice earlier in this run.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

LOGS = Path("logs")
LOGS.mkdir(exist_ok=True)

CONSOLE = LOGS / "finish.log"
PROGRESS = LOGS / "finish_progress.json"


def say(message: str) -> None:
    line = f"{datetime.now():%H:%M:%S}  {message}"
    print(line, flush=True)
    with open(CONSOLE, "a", encoding="utf-8", errors="replace") as handle:
        handle.write(line + "\n")


#: wmic no longer ships with Windows and raised CommandNotFoundException, which
#: this function swallowed as "nothing running" — the exact wrong answer for a
#: guard whose job is to stop two extractors sharing one queue.
_PS_EXTRACTORS = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -like '*mangotree.cli extract*' } | "
    "ForEach-Object { $_.ProcessId }"
)


def extraction_in_flight() -> List[int]:
    """PIDs of any running extraction, so we never start a competing one."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_EXTRACTORS],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        # Refusing to guess is safer than assuming the queue is free.
        say(f"  could not check for running extractors ({exc}); assuming one is active")
        return [-1]

    if result.returncode != 0:
        say(f"  extractor check failed: {result.stderr.strip()[:120]}; assuming one is active")
        return [-1]

    pids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def wait_for_extraction(poll_seconds: int = 60) -> None:
    reported = 0.0
    while True:
        pids = extraction_in_flight()
        if not pids:
            say("no extraction in flight")
            return
        now = time.time()
        if now - reported > 600:
            say(f"  waiting on extraction {pids}")
            reported = now
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
        "extract-disk",
        ["extract", "--yes"],
        required=True,
        note="the 231 disk-corpus files, every PDF page through vision",
    ),
    Stage(
        "reocr",
        ["reocr"],
        note="recover pages blocked while the OpenAI account was out of credit",
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
    Stage("graph", ["graph"], note="entities and edges, stamped onto every chunk"),
    Stage("vector-index", ["vector-index", "--status"], note="confirm the index is queryable"),
]


def run_stage(stage: Stage) -> dict:
    log_path = LOGS / f"stage_{stage.name}.log"
    command = (
        [sys.executable, "-u", stage.script, *stage.args] if stage.script
        else [sys.executable, "-u", "-m", "mangotree.cli", *stage.args]
    )

    say(f"START {stage.name} — {stage.note}")
    started = time.time()
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as handle:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                stdin=subprocess.DEVNULL,
            )
            for line in process.stdout:
                handle.write(line)
                handle.flush()
            code = process.wait()
    except Exception as exc:
        say(f"  {stage.name} could not start: {exc}")
        code = -1

    minutes = (time.time() - started) / 60
    say(f"  {stage.name} exit={code} in {minutes:.1f} min")
    return {"stage": stage.name, "exit": code, "minutes": round(minutes, 1)}


def main() -> int:
    say("=" * 70)
    say("finish-remaining starting")
    wait_for_extraction()

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
            f"{row['stage']:<16} {row['minutes']:>7.1f} min")

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

    say("finish-remaining complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
