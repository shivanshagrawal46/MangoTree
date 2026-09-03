"""
Claim-entailment judge (Sprint 4 — cross-family verification).

The deterministic verifier proves a quote EXISTS in the cited chunk. It
does NOT prove the CLAIM follows from the quote (its own docstring says so).
That gap lets a claim subtly misstate what its quote says and still read as
"verified".

This module closes the gap with an INDEPENDENT, CROSS-FAMILY judge: the
answer is written by Claude/Fable; the judge is OpenAI (gpt-5). Different
model families fail differently, so the judge catches the author's blind
spots — the honest technical reason to run two models (not a reasoning/
answering split).

Design:
  * Pure orchestration here; the model call is injected as `judge_fn` so
    the logic is unit-testable with zero API cost.
  * `OpenAIEntailmentJudge` is the production `judge_fn` (gpt-5 via the
    same OPENAI_API_KEY the OCR fallback uses).
  * Verdicts mirror the verifier's item shape so failed facts flow through
    the SAME retry pipeline (`apply_retry_merge`).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Verdict labels
ENTAIL_SUPPORTED = "SUPPORTED"
ENTAIL_PARTIAL = "PARTIAL"
ENTAIL_NOT_SUPPORTED = "NOT_SUPPORTED"
ENTAIL_SKIPPED = "SKIPPED"          # no quote / not applicable
ENTAIL_ERROR = "ERROR"              # judge call failed -> treat as non-blocking

_PASS_LABELS = {ENTAIL_SUPPORTED}
# PARTIAL is surfaced (amber) but does not hard-fail by default; configurable.

# JudgeFn: (claim, quote) -> (label, reason)
JudgeFn = Callable[[str, str], Tuple[str, str]]


@dataclass(frozen=True)
class EntailmentItem:
    fact_id: str
    label: str
    reason: str = ""

    @property
    def supported(self) -> bool:
        return self.label in _PASS_LABELS

    @property
    def failed(self) -> bool:
        return self.label == ENTAIL_NOT_SUPPORTED


@dataclass
class EntailmentReport:
    items: List[EntailmentItem] = field(default_factory=list)
    fail_on_partial: bool = False

    @property
    def failed(self) -> List[EntailmentItem]:
        bad = {ENTAIL_NOT_SUPPORTED}
        if self.fail_on_partial:
            bad.add(ENTAIL_PARTIAL)
        return [i for i in self.items if i.label in bad]

    @property
    def all_ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "all_ok": self.all_ok,
            "n_total": len(self.items),
            "n_failed": len(self.failed),
            "items": [{"fact_id": i.fact_id, "label": i.label, "reason": i.reason}
                      for i in self.items],
        }


def judge_facts(
    facts: Sequence[Dict[str, Any]],
    *,
    judge_fn: JudgeFn,
    fail_on_partial: bool = False,
    max_workers: Optional[int] = None,
) -> EntailmentReport:
    """Run the entailment judge over each fact's (claim, verbatim_quote).

    Facts are judged CONCURRENTLY (one independent model call each), so a
    30-fact answer costs ~one call's latency instead of 30 sequential ones.
    Output order matches input order. `max_workers` defaults to
    ENTAILMENT_WORKERS (env) or 6.
    """
    from concurrent.futures import ThreadPoolExecutor

    report = EntailmentReport(fail_on_partial=fail_on_partial)
    facts = list(facts)
    results: List[Optional[EntailmentItem]] = [None] * len(facts)
    to_run: List[Tuple[int, str, str, str]] = []

    for i, f in enumerate(facts):
        fid = str(f.get("id") or f.get("fact_id") or "?")
        claim = str(f.get("claim") or "").strip()
        quote = str(f.get("verbatim_quote") or "").strip()
        if not claim or not quote:
            results[i] = EntailmentItem(fid, ENTAIL_SKIPPED, "no claim/quote")
        else:
            to_run.append((i, fid, claim, quote))

    def _one(task: Tuple[int, str, str, str]) -> Tuple[int, EntailmentItem]:
        idx, fid, claim, quote = task
        try:
            label, reason = judge_fn(claim, quote)
        except Exception as exc:  # noqa: BLE001 — judge must never crash answering
            return idx, EntailmentItem(fid, ENTAIL_ERROR, str(exc)[:200])
        return idx, EntailmentItem(fid, _normalize_label(label), (reason or "")[:300])

    if to_run:
        if max_workers is None:
            try:
                max_workers = int(os.getenv("ENTAILMENT_WORKERS", "6"))
            except ValueError:
                max_workers = 6
        workers = max(1, min(max_workers, len(to_run)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for idx, item in ex.map(_one, to_run):
                results[idx] = item

    report.items = [r for r in results if r is not None]
    return report


def _normalize_label(raw: str) -> str:
    r = (raw or "").strip().upper()
    if "NOT" in r or "UNSUPPORT" in r or "CONTRADICT" in r:
        return ENTAIL_NOT_SUPPORTED
    if "PARTIAL" in r:
        return ENTAIL_PARTIAL
    if "SUPPORT" in r or r in ("YES", "TRUE"):
        return ENTAIL_SUPPORTED
    return ENTAIL_PARTIAL  # unknown -> conservative middle


# ---------------------------------------------------------------------------
# Production judge: OpenAI gpt-5 (cross-family from the Claude/Fable author)
# ---------------------------------------------------------------------------
_JUDGE_SYSTEM = (
    "You are an INDEPENDENT verification auditor in a forensic legal system. "
    "You are given a CLAIM and a VERBATIM QUOTE taken from a source document. "
    "Decide strictly whether the QUOTE, on its own, supports the factual "
    "assertion in the CLAIM.\n"
    "Labels:\n"
    "  SUPPORTED     - the quote substantiates the claim's factual assertion.\n"
    "  PARTIAL       - the quote supports part of the claim but not all, or "
    "requires an assumption.\n"
    "  NOT_SUPPORTED - the quote does not support, or contradicts, the claim.\n"
    "Judge ONLY the quote-to-claim relationship; do not use outside knowledge. "
    "Numbers, dates, and names in the claim must match the quote to be "
    "SUPPORTED. Respond with STRICT JSON: {\"label\": \"...\", \"reason\": \"...\"}."
)

_ENTAILMENT_MODEL = os.environ.get("ENTAILMENT_MODEL") or "gpt-5.5"


class OpenAIEntailmentJudge:
    """Callable judge_fn backed by OpenAI. Reasoning-model friendly.

    Uses a DEDICATED key (ENTAILMENT_OPENAI_API_KEY) kept separate from the
    OCR key, falling back to OPENAI_API_KEY only if the dedicated one is
    unset. Model defaults to gpt-5.5 (cross-family from the Claude author)."""

    def __init__(self, *, model: Optional[str] = None, api_key: Optional[str] = None):
        self.model = model or _ENTAILMENT_MODEL
        self._api_key = (api_key
                         or os.environ.get("ENTAILMENT_OPENAI_API_KEY")
                         or os.environ.get("OPENAI_API_KEY"))
        self._client = None
        import threading
        self._client_lock = threading.Lock()

    def _client_lazy(self):
        # Double-checked lock so concurrent judge_facts workers don't each
        # build their own client on the first call.
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    from openai import OpenAI
                    self._client = OpenAI(api_key=self._api_key)
        return self._client

    def __call__(self, claim: str, quote: str) -> Tuple[str, str]:
        client = self._client_lazy()
        user = f"CLAIM:\n{claim}\n\nVERBATIM QUOTE:\n{quote}\n\nReturn strict JSON."
        messages = [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ]
        # Models differ on which knobs they accept: gpt-5 needs
        # reasoning_effort=minimal (else it burns the budget on reasoning and
        # returns empty); gpt-5.5 REJECTS reasoning_effort. So we try with the
        # reasoning knob and transparently retry without it on a 400.
        base = {"model": self.model, "max_completion_tokens": 2000, "messages": messages}
        try:
            resp = client.chat.completions.create(reasoning_effort="minimal", **base)
        except Exception as exc:  # noqa: BLE001
            if "reasoning_effort" in str(exc):
                resp = client.chat.completions.create(**base)
            else:
                raise
        content = (resp.choices[0].message.content or "").strip()
        return _parse_judge_json(content)


def _parse_judge_json(content: str) -> Tuple[str, str]:
    """Robustly extract {label, reason} from a model response."""
    if not content:
        return ENTAIL_ERROR, "empty response"
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return str(obj.get("label", "")), str(obj.get("reason", ""))
        except json.JSONDecodeError:
            pass
    # Fallback: scan for a label keyword in free text.
    return content, content[:200]


__all__ = [
    "EntailmentItem", "EntailmentReport", "judge_facts",
    "OpenAIEntailmentJudge",
    "ENTAIL_SUPPORTED", "ENTAIL_PARTIAL", "ENTAIL_NOT_SUPPORTED",
    "ENTAIL_SKIPPED", "ENTAIL_ERROR",
]
