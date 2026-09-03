"""
Verification augmentation (Sprint 4 wiring) — flag-gated, DEFAULT OFF.

Bolts the three built-but-inert verification modules onto the finalized
answer WITHOUT changing behavior unless explicitly enabled:

  RAG_ENTAILMENT_ENABLED       -> cross-family (GPT-5) claim-entailment judge
  RAG_COVERAGE_ENABLED         -> uncited-number/date coverage checker
  RAG_INJECTION_SCAN_ENABLED   -> prompt-injection scan of the evidence

When a flag is off, that check is skipped. When all are off (the default),
`augment_answer` returns the answer unchanged and an empty report — i.e.
zero behavior change on the production path until you flip a flag.

The entailment judge is dependency-injected (`judge_fn`) so it is fully
unit-testable with no API cost.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.rag.v2.entailment import judge_facts, ENTAIL_NOT_SUPPORTED, ENTAIL_PARTIAL
from src.rag.v2.coverage import check_coverage
from src.rag.v3.injection_guard import scan_for_injection


def _flag(name: str) -> bool:
    return os.getenv(name, "false").lower() in ("1", "true", "yes")


def augment_answer(
    *,
    answer: str,
    facts: Sequence[Dict[str, Any]],
    fact_verdicts: List[Dict[str, Any]],
    chunks: Sequence[Any],
    judge_fn: Optional[Callable[[str, str], Any]] = None,
) -> Dict[str, Any]:
    """Return {answer, verdicts, report, notes}. No-op when all flags off."""
    notes: List[str] = []
    report: Dict[str, Any] = {}

    # --- entailment (cross-family) ---
    if _flag("RAG_ENTAILMENT_ENABLED") and facts:
        jf = judge_fn
        if jf is None:
            from src.rag.v2.entailment import OpenAIEntailmentJudge
            jf = OpenAIEntailmentJudge()
        erep = judge_facts(facts, judge_fn=jf)
        report["entailment"] = erep.to_dict()
        by_id = {(v.get("fact_id") or v.get("id")): v for v in fact_verdicts}
        for it in erep.items:
            v = by_id.get(it.fact_id)
            if v is not None:
                v["entailment"] = it.label
        bad = [i for i in erep.items if i.label == ENTAIL_NOT_SUPPORTED]
        partial = [i for i in erep.items if i.label == ENTAIL_PARTIAL]
        if bad:
            notes.append(
                f"Entailment: {len(bad)} claim(s) NOT supported by their quote "
                f"({', '.join(i.fact_id for i in bad[:6])}) — treat as unverified.")
        elif partial:
            notes.append(f"Entailment: {len(partial)} claim(s) only partially supported.")

    # --- coverage (uncited hard tokens) ---
    if _flag("RAG_COVERAGE_ENABLED") and answer:
        crep = check_coverage(answer, facts)
        report["coverage"] = crep.to_dict()
        if not crep.ok:
            toks = ", ".join(sorted({g.token for g in crep.gaps})[:8])
            notes.append(
                f"Coverage: {len(crep.gaps)} figure(s)/date(s) in the prose are not "
                f"tied to a verified fact ({toks}). Verify before relying on them.")

    # --- prompt-injection scan of evidence ---
    if _flag("RAG_INJECTION_SCAN_ENABLED") and chunks:
        hits = []
        for c in chunks:
            body = getattr(c, "body", None) or getattr(c, "text", "") or ""
            hits.extend(scan_for_injection(body))
        report["injection_hits"] = len(hits)
        if hits:
            labels = ", ".join(sorted({h.label for h in hits}))
            notes.append(
                f"Prompt-injection scan: {len(hits)} instruction-like span(s) found in "
                f"the evidence ({labels}); treated as DATA, not obeyed.")

    # Do NOT append notes to the answer prose — they belong in structured
    # metadata (report/notes) that the UI can render in a verification panel,
    # not inline in the message. Answer text is returned unchanged.
    return {"answer": answer, "verdicts": fact_verdicts, "report": report, "notes": notes}


__all__ = ["augment_answer"]
