"""Live pipeline dashboard. Read-only; safe to run alongside everything else.

Redraws a single screen on an interval rather than appending, so the window
can be left open and glanced at. Every number here is read from the logs or
from Mongo — this process never writes anything.

Run it in a window you can see:

    python scripts/monitor.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, ".")

LOGS = Path("logs")
INTERVAL = 20

#: Stages the orchestrator runs, in order, so the dashboard can show what has
#: finished and what is still ahead rather than only the current one.
STAGE_ORDER = [
    ("extract-disk", "OCR re-read"),
    ("reocr", "Blocked-page recovery"),
    ("segregate", "Opus 5 property assignment"),
    ("index", "Chunks and embeddings"),
    ("graph", "Knowledge graph"),
    ("vector-index", "Vector index"),
]


def read(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


#: wmic is absent from current Windows builds and fails silently in a
#: try/except, which made this report "nothing running" while the pipeline was
#: healthy. CIM via PowerShell is the supported route.
_PS_COMMAND_LINES = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "ForEach-Object { $_.CommandLine }"
)


def powershell(command: str, timeout: int = 45) -> str:
    try:
        return subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
        ).stdout.strip()
    except Exception:
        return ""


def live_processes() -> List[str]:
    names = []
    for line in powershell(_PS_COMMAND_LINES).splitlines():
        if "mangotree.cli" in line:
            match = re.search(r"mangotree\.cli\s+([a-z-]+)", line)
            if match:
                names.append(match.group(1))
        elif "finish_remaining" in line:
            names.append("orchestrator")
    return names


def progress_with_eta(text: str) -> Optional[Tuple[int, int, str]]:
    """Current position plus a finish time projected from observed throughput.

    The rate is measured across the whole run rather than the last two lines:
    per-document times swing by an order of magnitude between a one-page
    invoice and a sixty-page lien package, and a two-point estimate inherits
    that swing. A whole-run average is the only stable signal available.
    """
    points = re.findall(r"^(\d{2}):(\d{2}):(\d{2}).*?\s(\d+)/(\d+)\s", text, re.M)
    if not points:
        return None

    def seconds(point) -> int:
        return int(point[0]) * 3600 + int(point[1]) * 60 + int(point[2])

    first, last = points[0], points[-1]
    done, total = int(last[3]), int(last[4])

    elapsed = seconds(last) - seconds(first)
    if elapsed < 0:  # run crossed midnight
        elapsed += 86400
    advanced = done - int(first[3])

    if elapsed <= 0 or advanced <= 0 or done >= total:
        return done, total, ""

    per_doc = elapsed / advanced
    remaining = timedelta(seconds=int((total - done) * per_doc))
    finish = datetime.now() + remaining
    hours, rest = divmod(int(remaining.total_seconds()), 3600)
    minutes = rest // 60
    span = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
    return done, total, f"~{span} left, done by {finish:%H:%M}"


def last_timestamp(text: str, needle: str) -> str:
    """Clock time of the most recent line containing ``needle``."""
    stamps = [
        line[:8]
        for line in text.splitlines()
        if needle in line and re.match(r"^\d{2}:\d{2}:\d{2}", line)
    ]
    return stamps[-1] if stamps else ""


def add_fallback_health(add, log: str) -> None:
    """Report the GPT-5 tier's state, not its lifetime error count.

    A running total is the wrong signal here: the credit outage earlier today
    left 153 failures in this log permanently, so a bare count keeps reporting
    an emergency hours after it was resolved. What matters is whether the most
    recent credit failure is older than the most recent success.
    """
    failed_at = last_timestamp(log, "no credits remaining")
    if not failed_at:
        return
    worked_at = last_timestamp(log, "read by gpt-5")
    if worked_at > failed_at:
        add(f"             gpt-5 healthy (last read {worked_at}; outage ended {failed_at})")
    else:
        add(f"             GPT-5 OUT OF CREDIT since {failed_at} — pages are being deferred")


def bar(done: int, total: int, width: int = 34) -> str:
    if total <= 0:
        return ""
    filled = max(0, min(width, round(width * done / total)))
    return "[" + "#" * filled + "." * (width - filled) + f"] {100 * done // total:>3}%"


def mongo_counts() -> dict:
    try:
        from mangotree.storage.mongo import get_mongo

        mongo = get_mongo()
        artifacts = mongo.artifacts
        return {
            "emails": artifacts.count_documents({"source_type": "email"}),
            "attachments": artifacts.count_documents({"source_type": "attachment"}),
            "archive files": artifacts.count_documents({"source_type": "disk_file"}),
            "extracted": artifacts.count_documents({"extraction": {"$exists": True}}),
            "property-assigned": artifacts.count_documents({"segregation": {"$exists": True}}),
            "chunks": mongo.chunks.count_documents({}),
        }
    except Exception as exc:
        return {"mongo unreachable": str(exc)[:50]}


def draw() -> None:
    lines: List[str] = []
    add = lines.append

    add("=" * 68)
    add(f"  MANGOTREE PIPELINE            {datetime.now():%H:%M:%S}")
    add("=" * 68)

    running = live_processes()
    add(f"  running   {', '.join(running) if running else 'nothing'}")
    add("")

    finish_log = read(LOGS / "finish.log")
    started = re.findall(r"START ([a-z-]+)", finish_log)
    #: The orchestrator records completion as "<stage> exit=<code>". Matching on
    #: words like OK or DONE showed a finished stage as still running, so the
    #: dashboard claimed two stages were live at once.
    finished = {
        stage: int(code) for stage, code in re.findall(r"([a-z-]+) exit=(\d+)", finish_log)
    }

    add("  STAGES")
    for stage, label in STAGE_ORDER:
        log = read(LOGS / f"stage_{stage}.log")
        if stage in finished:
            code = finished[stage]
            add(f"    {'done    ' if code == 0 else 'FAILED  '} {label}")
            continue
        if not log and stage not in started:
            add(f"    queued   {label}")
            continue

        detail = progress_with_eta(log)
        if detail:
            done, total, eta = detail
            add(f"    running  {label}")
            add(f"             {bar(done, total)}  {done}/{total}")
            if eta:
                add(f"             {eta}")
        else:
            add(f"    running  {label}")

        blank = log.count("unreadable: Anthropic refused")
        if blank:
            add(f"             {blank} pages queued for recovery")
        add_fallback_health(add, log)
    add("")

    #: Recovery ran twice: once manually this afternoon and again as an
    #: orchestrator stage. Each writes its own log, so reading only one would
    #: freeze the tally at the other run's total while work was still happening.
    recover = read(LOGS / "reocr_recover.log") + read(LOGS / "stage_reocr.log")
    if recover:
        restored = recover.count("refused by Anthropic; read by gpt")
        improved = recover.count("improved by gpt")
        docs = recover.count("Re-reading")
        state = "running" if "reocr" in running else "finished"
        add(f"  BLOCKED-PAGE RECOVERY ({state}, both runs)")
        add(f"    {restored} pages restored, {improved} improved, across {docs} documents")
        add("")

    add("  CORPUS")
    for key, value in mongo_counts().items():
        shown = f"{value:,}" if isinstance(value, int) else value
        add(f"    {key:<20} {shown:>9}")
    add("")

    free = powershell("[math]::Round((Get-PSDrive C).Free/1GB,1)", timeout=30)
    if free:
        add(f"  C: {free} GB free")
    add("")
    add(f"  refreshes every {INTERVAL}s   Ctrl+C to close")

    os.system("cls" if os.name == "nt" else "clear")
    print("\n".join(lines), flush=True)


def main() -> int:
    while True:
        try:
            draw()
        except Exception as exc:  # a dashboard must never die on a bad read
            print(f"  dashboard error: {exc}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nmonitor stopped")
