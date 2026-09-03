"""
Cross-family critique -> revise loop (Sprint 4++).

Flow requested by the client:

  1. Fable 5 investigates and writes the first answer (the agent loop).
  2. GPT-5.5 (a DIFFERENT model family) CRITIQUES that answer against the
     user's question and the case — what is missing, glossed over, or weak?
     It does NOT rewrite the answer.
  3. GPT-5.5's findings are handed BACK to Fable 5.
  4. Fable 5 writes the FINAL answer addressing those findings — Fable stays
     the sole author, and the revision is re-verified so grounding holds.

Gated by RAG_CROSS_CRITIC_ENABLED (default off). One critique + one revision
round (bounded cost/latency). If the critic finds no gaps, the original
answer ships unchanged.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence

_CRITIC_MODEL = os.getenv("ENTAILMENT_MODEL") or "gpt-5.5"

_CRITIC_SYSTEM = (
    "You are an INDEPENDENT senior forensic-legal reviewer, from a different "
    "team than the author. You are given the user's QUESTION, the ANSWER a "
    "colleague wrote, and the FACTS it cited. Your ONLY job is to find what "
    "the answer MISSES or gets weak on with respect to the question and the "
    "case:\n"
    "  - parts of the question left unaddressed\n"
    "  - missing parties, dates, amounts, documents, or angles\n"
    "  - overlooked contradictions, risks, or defenses\n"
    "  - claims that overreach beyond the evidence\n"
    "  - next steps a careful attorney would add\n"
    "Do NOT rewrite the answer. Be specific and actionable. If the answer is "
    "genuinely complete, say so. Respond with STRICT JSON: "
    '{"has_gaps": true|false, "findings": ["...", "..."]}.'
)


class GptCritic:
    """GPT-5.5-backed critic (dedicated entailment key; adaptive params)."""

    def __init__(self, *, model: Optional[str] = None, api_key: Optional[str] = None):
        self.model = model or _CRITIC_MODEL
        self._api_key = (api_key
                         or os.environ.get("ENTAILMENT_OPENAI_API_KEY")
                         or os.environ.get("OPENAI_API_KEY"))
        self._client = None
        self._lock = threading.Lock()

    def _client_lazy(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    from openai import OpenAI
                    self._client = OpenAI(api_key=self._api_key)
        return self._client

    def critique(self, question: str, answer: str,
                 facts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        client = self._client_lazy()
        facts_txt = "\n".join(f"- {f.get('claim', '')}" for f in list(facts)[:60])
        user = (f"QUESTION:\n{question}\n\nANSWER:\n{(answer or '')[:12000]}\n\n"
                f"FACTS CITED:\n{facts_txt}\n\nReturn strict JSON.")
        base = {"model": self.model, "max_completion_tokens": 3000,
                "messages": [{"role": "system", "content": _CRITIC_SYSTEM},
                             {"role": "user", "content": user}]}
        try:
            resp = client.chat.completions.create(reasoning_effort="minimal", **base)
        except Exception as exc:  # noqa: BLE001
            if "reasoning_effort" in str(exc):
                resp = client.chat.completions.create(**base)
            else:
                raise
        content = (resp.choices[0].message.content or "").strip()
        return _parse_critique(content)


def _parse_critique(content: str) -> Dict[str, Any]:
    if not content:
        return {"has_gaps": False, "findings": [], "raw": ""}
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            findings = [str(x) for x in (obj.get("findings") or []) if str(x).strip()]
            return {"has_gaps": bool(obj.get("has_gaps")) and bool(findings),
                    "findings": findings, "raw": content}
        except json.JSONDecodeError:
            pass
    return {"has_gaps": False, "findings": [], "raw": content}


def _render_sources(chunks: Sequence[Any]) -> str:
    """Numbered SOURCES block matching the [#N] = chunks[N-1] convention so
    the revision's citations line up with the verifier."""
    lines: List[str] = []
    for i, c in enumerate(chunks):
        get = (c.get if isinstance(c, dict) else lambda k, d=None: getattr(c, k, d))
        date = get("date") or ""
        frm = get("from_email") or ""
        body = (get("body") or get("text") or "")[:1500]
        lines.append(f"[#{i+1}] {date} · {frm}\n{body}")
    return "\n\n".join(lines)


def run_cross_critique(
    *,
    anthropic_client: Any,
    model: str,
    question: str,
    answer: str,
    facts: Sequence[Dict[str, Any]],
    chunks: Sequence[Any],
    critic: Optional[GptCritic] = None,
    max_tokens: int = 16000,
) -> Dict[str, Any]:
    """Critique with GPT-5.5, then (if gaps) have Fable revise + re-verify.

    Returns {revised: bool, critique: {...}, answer, facts, fact_verdicts}.
    On any failure, returns revised=False and leaves the original intact.
    """
    from src.rag.v3.prompts import build_agent_system_prompt
    from src.rag.v2.answer_pipeline import generate_verified_answer

    out: Dict[str, Any] = {"revised": False, "critique": None,
                           "answer": answer, "facts": list(facts), "fact_verdicts": None}
    if not answer or not facts:
        return out

    critic = critic or GptCritic()
    try:
        crit = critic.critique(question, answer, facts)
    except Exception:  # noqa: BLE001 — critique must never break answering
        return out
    out["critique"] = crit
    if not crit.get("has_gaps"):
        return out  # reviewer found nothing to add — ship original

    # ----- hand findings back to Fable for the FINAL answer -----
    findings_txt = "\n".join(f"  {i+1}. {f}" for i, f in enumerate(crit["findings"]))
    sources = _render_sources(chunks)
    user_message = (
        f"SOURCES:\n{sources}\n\n"
        f"QUESTION:\n{question}\n\n"
        f"YOUR PREVIOUS ANSWER:\n{answer}\n\n"
        f"An independent reviewer flagged these gaps in your previous answer:\n"
        f"{findings_txt}\n\n"
        f"Write your FINAL answer. Address every valid gap above using ONLY "
        f"the SOURCES (cite [#N]); keep everything that was correct; do not "
        f"invent facts. If a flagged gap cannot be supported by the sources, "
        f"say so explicitly rather than fabricating."
    )
    try:
        system = build_agent_system_prompt(max_calls=0)
        verified = generate_verified_answer(
            anthropic_client=anthropic_client, model=model,
            system_prompt=system, user_message=user_message,
            prior_messages=None, chunks=list(chunks), max_tokens=max_tokens,
        )
        if verified.answer and verified.facts:
            out["revised"] = True
            out["answer"] = verified.answer
            out["facts"] = verified.facts
            out["fact_verdicts"] = verified.fact_verdicts
    except Exception:  # noqa: BLE001 — keep the original on any revision failure
        return out
    return out


__all__ = ["GptCritic", "run_cross_critique", "_parse_critique"]
