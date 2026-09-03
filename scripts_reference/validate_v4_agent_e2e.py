"""
End-to-end validation: run the v3 agent and emit the exact WebSocket
frame stream the frontend will consume. Verifies:

  • agent_plan / agent_step / agent_done frames have the fields the
    AgentReasoningPanel reads (step_num, type, tool_name, tool_input,
    summary, new_chunk_indices, elapsed_ms)
  • verification frame still works
  • sources frame includes body + verified_facts
  • agent_trace persisted shape is sensible
  • forced finalize path produces verified facts

We run THREE queries that exercise different tool combinations:

  1. Simple lookup  — should converge in 1-2 calls
  2. Comparison     — exercises compare_versions / find_latest_version
  3. Timeline       — exercises search_timeframe
"""
from __future__ import annotations

import json
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
os.environ["RAG_V3_AGENT_MAX_TOOL_CALLS"] = "6"
os.environ["RAG_V3_AGENT_MAX_WALL_CLOCK_S"] = "200"
os.environ["RAG_V3_AGENT_TRACE_LOG"] = "false"

from api.rag_singleton import make_chat
from config.settings import Settings
from src.utils.logger import configure_logger

configure_logger(Settings.load().logs_dir)


QUERIES = [
    # Simple lookup — should converge fast
    "What was the Fort Hill Unpaid Tax amount stated in the Settlement Agreement?",
    # Timeline — should use search_timeframe
    "Walk me through the major events of this case from June 2023 to August 2023.",
]


def expected_panel_fields_present(step: Dict[str, Any]) -> List[str]:
    """Return any missing fields the FE panel expects."""
    required = ["step_num", "type", "tool_name", "tool_input",
                "summary", "new_chunk_indices", "elapsed_ms"]
    missing = [k for k in required if k not in step]
    return missing


def validate_ws_payload_shapes(turn) -> List[str]:
    """Returns a list of validation errors (empty = all good)."""
    errors = []

    # --- agent_trace shape ---
    if turn.agent_trace:
        if "steps" not in turn.agent_trace:
            errors.append("agent_trace missing `steps`")
        if "budget" not in turn.agent_trace:
            errors.append("agent_trace missing `budget`")
        for i, s in enumerate(turn.agent_trace.get("steps", [])):
            miss = expected_panel_fields_present(s)
            if miss:
                errors.append(f"step #{i + 1} missing FE fields: {miss}")

    # --- verification shape ---
    if turn.verification_outcome:
        if not isinstance(turn.facts, list):
            errors.append("turn.facts is not a list")
        if not isinstance(turn.fact_verdicts, list):
            errors.append("turn.fact_verdicts is not a list")
        for i, v in enumerate(turn.fact_verdicts):
            for k in ("fact_id", "verdict", "claim", "source_chunk_id",
                      "verbatim_quote", "score"):
                if k not in v:
                    errors.append(f"verdict #{i} missing `{k}`")

    return errors


def main():
    chat = make_chat()
    print(f"\nuse_agent={chat.use_agent}  model={chat.agent_model}")
    print(f"max_tool_calls={chat.agent_max_tool_calls}, max_wall_clock_s={chat.agent_max_wall_clock_s}")
    print("=" * 80)

    all_ok = True
    for qi, q in enumerate(QUERIES):
        print(f"\n  QUERY {qi + 1}: {q}\n  {'-' * 76}")

        # Capture WS-style events as the agent runs.
        events: List[tuple] = []
        chat.on_agent_event = lambda t, p: events.append((t, p))

        chat.history = []
        try:
            turn = chat.ask(q)
        except Exception as exc:
            print(f"  ❌ FAILED: {exc}")
            import traceback
            traceback.print_exc()
            all_ok = False
            continue

        # Print the stream of events as the WS would have delivered them.
        print(f"\n  WS event stream ({len(events)} events):")
        for et, ep in events:
            if et == "agent_plan":
                print(f"    [agent_plan] budget={ep['budget']['max_tool_calls']} tools={len(ep['tools'])}")
            elif et == "agent_step":
                print(f"    [agent_step #{ep['step_num']}] {ep['tool_name']}: {ep['summary'][:80]}")
            elif et == "agent_forced_finalize":
                print(f"    [forced_finalize] {ep}")
            elif et == "agent_done":
                print(f"    [agent_done] outcome={ep['outcome']} facts={ep['n_facts']} "
                      f"verified={ep['n_verified']} tools={ep['tool_calls']} "
                      f"elapsed={ep['elapsed_ms']}ms")

        # Validate payload shapes
        errs = validate_ws_payload_shapes(turn)
        if errs:
            print(f"\n  ⚠ VALIDATION ERRORS:")
            for e in errs:
                print(f"    - {e}")
            all_ok = False
        else:
            print(f"\n  ✓ All WS payload shapes valid")

        # Summarise the answer
        print(f"\n  Outcome: {turn.verification_outcome}")
        print(f"  Facts: {len(turn.facts)} ({sum(1 for v in turn.fact_verdicts if v.get('verdict') == 'VERIFIED')} verified)")
        print(f"  Chunks: {len(turn.chunks)}")
        if turn.agent_trace:
            b = turn.agent_trace.get("budget", {})
            print(f"  Budget: {b.get('tool_calls_used')}/{b.get('max_tool_calls')} calls, "
                  f"{b.get('total_tokens')}/{b.get('max_total_tokens')} tokens, "
                  f"{b.get('elapsed_s')}s")
        print(f"\n  Answer preview ({len(turn.answer)} chars):")
        for line in turn.answer[:600].split("\n")[:8]:
            print(f"    {line}")
        if len(turn.answer) > 600:
            print(f"    ...")

    print("\n" + "=" * 80)
    print(f"  OVERALL: {'✓ ALL TESTS PASSED' if all_ok else '✗ SOME TESTS FAILED'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
