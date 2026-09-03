"""
Scorecard — the single-command quality gate (Sprint 1).

Runs the deterministic, no-API quality checks and prints one report with a
pass/fail verdict and a nonzero exit code on any failure. This is the
"measurement is the boss" artefact: every sprint must leave it green.

What it runs today (all offline, no DB / no API):
  * verifier mutation suite      — corrupted facts must be caught
  * quoted-text three-bucket     — recall-recovery classification logic
  * header/mojibake utilities    — encoding correctness
  * answer coverage checker      — no uncited hard tokens in prose

Extends cleanly: drop a new (name, module_path) into SUITES and it joins
the gate. The live-corpus checks (planted-fact recall, self-consistency,
health counters) attach here once their DB/model runs are wired — they
are intentionally NOT invoked from this offline gate.

Usage:
    python scripts/scorecard.py
    python scripts/scorecard.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (label, test file relative to repo root)
SUITES = [
    ("verifier_mutations", "tests/test_verifier_mutations.py"),
    ("quoted_text_buckets", "tests/test_quoted_text.py"),
    ("header_encoding", "tests/test_headers.py"),
    ("answer_coverage", "tests/test_coverage.py"),
    ("injection_guard", "tests/test_injection_guard.py"),
    ("deadline_radar", "tests/test_deadline_radar.py"),
    ("entailment_judge", "tests/test_entailment.py"),
    ("verification_augment", "tests/test_verification_augment.py"),
    ("cross_critic", "tests/test_cross_critic.py"),
    ("verifier_money", "tests/test_verifier_money.py"),
]


def _run_suite(path: str) -> dict:
    t0 = time.time()
    # Ensure `src` is importable even for tests that lack a path bootstrap
    # (some pre-existing tests rely on pytest's rootdir injection).
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(ROOT / path)],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )
    elapsed = time.time() - t0
    # The test runners print "N/M test functions passed" on the last line.
    tail = (proc.stdout.strip().splitlines() or [""])[-1]
    return {
        "path": path,
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "summary": tail,
        "elapsed_s": round(elapsed, 2),
        "stderr_tail": (proc.stderr.strip().splitlines() or [""])[-1] if proc.stderr.strip() else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=str, default="", help="write full report to this path")
    args = ap.parse_args()

    results = [dict(name=name, **_run_suite(path)) for (name, path) in SUITES]
    n_pass = sum(1 for r in results if r["passed"])
    all_ok = n_pass == len(results)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "all_passed": all_ok,
        "n_suites": len(results),
        "n_passed": n_pass,
        "suites": results,
    }

    # ---- console table ----
    print("=" * 68)
    print(f" SCORECARD - {report['generated_at']}")
    print("=" * 68)
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['name']:<22} {r['summary']:<30} {r['elapsed_s']:>5}s")
        if not r["passed"] and r["stderr_tail"]:
            print(f"         -> {r['stderr_tail']}")
    print("-" * 68)
    print(f"  {n_pass}/{len(results)} suites passed  ->  "
          f"{'GREEN' if all_ok else 'RED'}")
    print("=" * 68)

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"  full report written to {args.json}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
