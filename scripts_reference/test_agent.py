"""
Smoke test the v3 agent end-to-end.

Runs three hard queries that should exercise different tools:

  1. Comparison    — "Compare the Settlement Agreement drafts; what changed?"
  2. Timeline      — "Walk me through the major events from June to August 2023."
  3. Contradiction — "Is there any contradiction about the Fort Hill Unpaid Tax amount?"

For each, we print:
  - The agent's plan + every step
  - The final answer (truncated)
  - Verification outcome
  - Budget usage (tool calls, tokens, elapsed)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("RAG_V2_ENABLED", "true")
os.environ.setdefault("RAG_V2_STRUCTURED_OUTPUT", "true")
os.environ.setdefault("RAG_V2_CITATION_VERIFIER", "true")
os.environ.setdefault("RAG_V2_VERIFIER_RETRY", "true")
os.environ["RAG_V3_AGENT_ENABLED"] = "true"
os.environ.setdefault("RAG_V3_AGENT_MAX_TOOL_CALLS", "8")
os.environ.setdefault("RAG_V3_AGENT_MAX_WALL_CLOCK_S", "120")
os.environ.setdefault("RAG_V3_AGENT_TRACE_LOG", "false")  # skip Mongo log during smoke

from api.rag_singleton import make_chat
from config.settings import Settings
from src.utils.logger import configure_logger

configure_logger(Settings.load().logs_dir)


QUERIES = [
    "Were there multiple drafts of the Settlement Agreement? If so, what materially changed between the earliest and the latest version?",
    "Walk me through the major events of this case from June 2023 through August 2023.",
    "Is there any contradiction in the record about the Fort Hill Unpaid Tax amount? If yes, surface both figures and identify which one is operative.",
]


def _print_step(s: Dict[str, Any]) -> None:
    n = s.get("step_num")
    typ = s.get("type")
    tool = s.get("tool_name") or ""
    summary = s.get("summary") or ""
    new_idx = s.get("new_chunk_indices") or []
    elapsed = s.get("elapsed_ms", 0)
    err = s.get("error")
    line = f"  step #{n} [{typ}] {tool}: {summary}"
    if new_idx:
        line += f"  (added {len(new_idx)} new chunks)"
    line += f"  ({elapsed}ms)"
    if err:
        line += f"  ERROR: {err}"
    print(line)


def run_query(chat, idx: int, q: str) -> None:
    print(f"\n{'=' * 80}\n  QUERY {idx + 1}: {q}\n{'=' * 80}")

    # We DON'T need to capture stream events here — chat.on_agent_event
    # is None unless the WS handler set it. The final Turn already has
    # the agent_trace.
    turn = chat.ask(q)

    print(f"\nOutcome: {turn.verification_outcome}")
    print(f"Facts: {len(turn.facts)} ({sum(1 for v in turn.fact_verdicts if v.get('verdict') == 'VERIFIED')} verified)")
    print(f"Chunks discovered: {len(turn.chunks)}")
    if turn.agent_trace:
        budget = turn.agent_trace.get("budget", {})
        print(f"Budget: tool_calls={budget.get('tool_calls_used')}/{budget.get('max_tool_calls')}, "
              f"tokens={budget.get('total_tokens')}/{budget.get('max_total_tokens')}, "
              f"elapsed={budget.get('elapsed_s')}s")
        steps = turn.agent_trace.get("steps", [])
        print(f"\nAgent steps ({len(steps)}):")
        for s in steps:
            _print_step(s)

    print(f"\n--- ANSWER ({len(turn.answer)} chars) ---")
    print(turn.answer[:1200])
    if len(turn.answer) > 1200:
        print("  ... [truncated]")

    print("\n--- FACT VERDICTS ---")
    for v in turn.fact_verdicts[:10]:
        emoji = "✓" if v.get("verdict") == "VERIFIED" else "⚠"
        print(f"  {emoji} [{v.get('verdict')}] {(v.get('claim') or '')[:90]}")
    if len(turn.fact_verdicts) > 10:
        print(f"  ... and {len(turn.fact_verdicts) - 10} more")


def main():
    chat = make_chat()
    print(f"\nuse_agent: {getattr(chat, 'use_agent', False)}")
    print(f"agent_v2_pipeline: {'attached' if getattr(chat, 'agent_v2_pipeline', None) else 'MISSING'}")
    print(f"agent_model: {getattr(chat, 'agent_model', None)}")
    print(f"max_tool_calls: {getattr(chat, 'agent_max_tool_calls', None)}")

    # Fresh history for each query so we test the agent in isolation.
    for i, q in enumerate(QUERIES):
        chat.history = []
        try:
            run_query(chat, i, q)
        except Exception as exc:
            print(f"\nQUERY FAILED: {exc}")
            import traceback; traceback.print_exc()


if __name__ == "__main__":
    main()
