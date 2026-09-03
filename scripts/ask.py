"""Ask the agent a question and watch it work.

Usage:
    python scripts/ask.py "who guaranteed the Chita Ct loan" --property chita_ct
    python scripts/ask.py "which properties have had a notice of default"       # global
    python scripts/ask.py "..." --json run.json --no-critique
"""
from __future__ import annotations

import argparse
import json
import sys
import time

sys.path.insert(0, ".")

from mangotree.agent.agent import Agent
from mangotree.config.settings import SETTINGS
from mangotree.retrieve.scope import Scope
from mangotree.storage.mongo import get_mongo


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question")
    parser.add_argument("--property", default=None)
    parser.add_argument("--privileged", action="store_true")
    parser.add_argument("--no-critique", action="store_true")
    parser.add_argument("--no-skeptic", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    scope = Scope.for_property(args.property, include_privileged=args.privileged) if args.property \
        else Scope.global_(include_privileged=args.privileged)

    t0 = time.time()

    def on_event(kind: str, payload: dict) -> None:
        el = f"{time.time() - t0:6.1f}s"
        if kind == "agent_step":
            s = payload
            extra = f" +{len(s.get('new_indices') or [])} new" if s.get("new_indices") else ""
            err = f"  ERROR {s['error']}" if s.get("error") else ""
            print(f"  {el}  [{s['step_num']:>2}] {s['type']:<16} {s.get('tool_name') or '':<24} {str(s.get('summary'))[:90]}{extra}{err}")
        elif kind == "agent_sufficiency_gate":
            print(f"  {el}  ---- sufficiency gate: first submission held, checklist returned ----")
        elif kind == "agent_budget":
            print(f"  {el}  ---- budget: {payload.get('reason')} — forcing finalisation ----")
        elif kind == "agent_start":
            print(f"\n  {el}  START {payload['scope']}  budget={payload['budget']['max_tool_calls']} calls / "
                  f"{payload['budget']['max_total_tokens']:,} tokens / {int(payload['budget']['max_wall_clock_s'] // 60)} min")

    agent = Agent(get_mongo(), anthropic_api_key=SETTINGS.anthropic_api_key,
                  voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key)
    res = agent.run(args.question, scope, on_event=on_event,
                    critique=not args.no_critique, skeptic=not args.no_skeptic)

    print(f"\n{'=' * 100}\n  ANSWER  ({res.outcome}{', ' + res.forced_reason if res.forced_reason else ''}, {res.elapsed_ms / 1000:.0f}s)\n{'=' * 100}")
    print(res.answer)
    if res.risks:
        print("\n  OPEN RISKS (skeptic, each cited)")
        for r in res.risks:
            print(f"   - {r}")
    if res.open_items:
        print("\n  OPEN ITEMS")
        for o in res.open_items:
            print(f"   - {o}")
    v = res.verification or {}
    print(f"\n  VERIFICATION  {v.get('verified')}/{v.get('facts')} facts verified byte-for-byte")
    for u in (v.get("unverified") or [])[:6]:
        print(f"   ✗ {u['verdict']:<16} {str(u['claim'])[:90]}  {u.get('missing_tokens') or ''}")
    c = res.critique or {}
    if c:
        print(f"  CRITIQUE  provider={c.get('provider')} model={c.get('model')} same_provider_fallback={c.get('same_provider_fallback')} "
              f"gaps={len(c.get('gaps') or [])} unsupported={len(c.get('unsupported') or [])} contradictions={len(c.get('contradictions') or [])} "
              f"rewritten={c.get('rewritten', False)}")
    print(f"\n  COVERAGE  {res.coverage}")
    print(f"  BUDGET    {res.budget}")
    print(f"\n  SOURCES CITED ({len(res.cited())})")
    for h in res.cited()[:25]:
        print(f"   [#{res.chunks.index(h) + 1}] {h.date_str:<10} {h.citation[:70]:<70} {h.property_ids} {h.placement}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res.as_dict(), f, indent=2, default=str)
        print(f"\n  written {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
