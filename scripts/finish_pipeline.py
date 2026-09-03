"""Runs the remaining stages in order, unattended.

Order is not arbitrary and the stages are not independent:

* extraction must precede segregation, or Opus 5 judges attachments by filename
  alone when their text was available all along;
* segregation must precede indexing, because a chunk inherits the property
  decision and re-chunking afterwards would mean embedding everything twice;
* the graph runs last because it links chunks that indexing has to have created.

Per the admin's instruction for unattended running, a failing stage is logged and
the run continues. The exception is a stage whose output the next one depends on
having *some* of — those are marked ``required`` and stop the chain, because
continuing would produce a corpus that looks finished and is not.
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

LOGS = Path("logs")
LOGS.mkdir(exist_ok=True)


@dataclass
class Stage:
    name: str
    args: List[str]
    required: bool = False
    note: str = ""


STAGES = [
    Stage(
        "extract",
        ["extract", "--yes"],
        required=True,
        note="native for Excel/Word, Claude vision OCR for PDFs, GPT-5 fallback",
    ),
    Stage(
        "reocr",
        ["reocr", "--yes"],
        note="GPT-5 re-reads pages Claude refused or read poorly",
    ),
    Stage(
        "segregate",
        ["segregate", "--yes"],
        required=True,
        note="Opus 5 decides the property for every email and attachment",
    ),
    Stage(
        "index",
        ["index", "--yes"],
        required=True,
        note="1000/200 chunking, Tier 1+2 context, voyage-4-large embeddings",
    ),
    Stage(
        "graph",
        ["graph"],
        note="entities, edges, and entity_ids stamped onto every chunk",
    ),
    Stage(
        "vector-index",
        ["vector-index", "--status"],
        note="confirm the index is queryable and one embedding space",
    ),
]


def run(stage: Stage) -> dict:
    log_path = LOGS / f"stage_{stage.name}.log"
    started = time.time()
    print(f"\n{'=' * 78}")
    print(f"  {datetime.now():%H:%M:%S}  {stage.name}")
    if stage.note:
        print(f"  {stage.note}")
    print(f"  log: {log_path}")
    print("=" * 78, flush=True)

    with open(log_path, "w", encoding="utf-8", errors="replace") as handle:
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "mangotree.cli", *stage.args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        for line in process.stdout:
            handle.write(line)
            handle.flush()
            # Progress lines only, so the console stays readable overnight.
            if any(k in line for k in ("progress", "complete", "ERROR", "eta", "/")):
                print(f"    {line.rstrip()[:150]}", flush=True)
        code = process.wait()

    elapsed = time.time() - started
    print(f"  -> exit {code} in {elapsed / 60:.1f} min", flush=True)
    return {"stage": stage.name, "exit": code, "minutes": round(elapsed / 60, 1)}


def main() -> int:
    print(f"\n  pipeline starting {datetime.now():%Y-%m-%d %H:%M:%S}")
    summary = []
    for stage in STAGES:
        result = run(stage)
        summary.append(result)
        if result["exit"] != 0:
            if stage.required:
                print(f"\n  STOPPING: required stage '{stage.name}' failed.\n")
                break
            print(f"  continuing past optional stage '{stage.name}'")

    print(f"\n{'=' * 78}\n  SUMMARY\n{'=' * 78}")
    for row in summary:
        flag = "ok " if row["exit"] == 0 else "FAIL"
        print(f"  [{flag}] {row['stage']:<16} {row['minutes']:>7.1f} min")

    print("\n  running verification...\n")
    verify = subprocess.run(
        [sys.executable, "scripts/verify_ingestion.py"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    (LOGS / "verify.log").write_text(verify.stdout + verify.stderr, encoding="utf-8")
    print(verify.stdout[-6000:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
