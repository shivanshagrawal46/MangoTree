"""Rolling chat summary — so month-6 questions still know week-2 context.

After every answer, Opus 5 folds the exchange into a short running summary of
the conversation: decisions made in chat, open questions, instructions the
admin gave, figures agreed. The summary rides with every subsequent message
alongside the Remember notes, so the agent never forgets what was discussed
even after the raw history is trimmed.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from mangotree.retrieve import config as cfg

_SYSTEM = """You maintain the running summary of one chat between RKB staff and their
document system. Given the PREVIOUS SUMMARY and the LATEST EXCHANGE (question,
final answer headline and points, any instructions in the question), return an
UPDATED summary — plain words, at most 220 words — with these headings only
when they have content:

Decisions made · Open questions · Instructions given · Figures agreed · Context

Each question is prefixed with who asked it, e.g. "[Rakesh Sir (CEO — final
authority)]". Under "Instructions given", name who gave each instruction. An
instruction from Rakesh Sir stands until he changes it and overrides any
conflicting instruction from anyone else — record the conflict and say his
prevails. Keep what is still true, drop what was superseded, never invent.
Return the summary text only."""


def update_summary(client, *, previous: str, question: str, answer: Dict[str, Any]) -> Optional[str]:
    points = "\n".join(f"- ({p.get('urgency')}) {p.get('text')}" for p in (answer.get("points") or [])[:7])
    latest = f"QUESTION:\n{question}\n\nANSWER HEADLINE:\n{answer.get('headline', '')}\n\nPOINTS:\n{points}"
    try:
        r = client.messages.create(model=cfg.AGENT_PLANNER_MODEL, max_tokens=900, system=_SYSTEM,
                                   messages=[{"role": "user", "content": f"PREVIOUS SUMMARY:\n{previous or '(none yet)'}\n\nLATEST EXCHANGE:\n{latest}"}])
        text = "".join(b.text for b in r.content if b.type == "text").strip()
        return text or None
    except Exception:
        return None
