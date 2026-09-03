"""
Verified-answer pipeline — Sprint 3 finish.

This is the orchestrator that wraps:

  1. STRUCTURED GENERATION  — Opus 4.6 emits {facts, answer} via the
                              submit_answer tool (src/rag/v2/structured_answer.py).
  2. CITATION VERIFICATION  — deterministic OCR-tolerant fuzzy match of
                              every fact's verbatim_quote against its
                              cited chunk (src/rag/v2/verifier.py).
  3. SELF-CORRECTION LOOP   — if any fact fails, ask Opus to re-extract
                              ONLY the failed claims; verify again.
  4. FINALIZATION           — if 2nd verification still fails, ship the
                              ORIGINAL Opus answer as-is (user choice;
                              evidence panel still surfaces source).

The pipeline is callable in two ways:

  generate_verified_answer(client, ...) -> VerifiedAnswer

      Stateless, single-shot. Use this from `LegalAdvisorChat.ask()`.

  log_verification(mongo, verified_answer, query, session_id)

      Persist verification metadata to the `verification_log` collection
      for later forensic review. Fire-and-forget; safe to swallow errors
      since logging never blocks the user's answer.

Cost / latency
--------------
  Best case (all facts verify):  1x Opus call + ~100ms verifier.
  Retry case (some facts fail):  2x Opus calls (2nd is narrow — only
                                  failed facts) + ~200ms verifier.
  Bad case (still failing):      2x Opus + we ship the ORIGINAL answer
                                  per user's instruction. ~~Same cost as
                                  retry case.

The verifier itself has no API cost — it's pure Python.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from src.rag.retriever import RetrievedChunk
from src.rag.v2.structured_answer import (
    SUBMIT_ANSWER_TOOL,
    REEXTRACT_TOOL,
    StructuredAnswer,
    ReextractionResult,
    build_reextract_prompt,
    parse_submit_answer,
    parse_reextract,
    get_structured_prompt_tail,
)
from src.rag.v2.verifier import (
    VerificationReport,
    VerificationItem,
    VERDICT_VERIFIED,
    verify_facts,
    DEFAULT_FUZZY_THRESHOLD,
)
from src.utils.logger import logger


# =====================================================================
# Result dataclasses (returned to the caller)
# =====================================================================

OUTCOME_VERIFIED_FIRST_PASS = "VERIFIED_FIRST_PASS"
OUTCOME_VERIFIED_AFTER_RETRY = "VERIFIED_AFTER_RETRY"
OUTCOME_KEPT_ORIGINAL = "KEPT_ORIGINAL"  # 2nd verification also failed
OUTCOME_NO_FACTS = "NO_FACTS"            # answer is pure scoping/expertise
OUTCOME_FALLBACK = "FALLBACK"            # something went wrong; legacy path


@dataclass
class VerifiedAnswer:
    """
    Final output of the pipeline. Carries everything the chat layer +
    frontend need to render an evidence-panel answer.
    """

    # Display
    answer: str = ""
    facts: List[Dict[str, Any]] = field(default_factory=list)
    outcome: str = OUTCOME_FALLBACK

    # Verification trail (audit + frontend evidence panel)
    first_pass: Optional[VerificationReport] = None
    second_pass: Optional[VerificationReport] = None

    # Per-fact merged verdicts (after retry logic). Same length as
    # `facts`. Frontend can use this directly to render green/yellow/red
    # badges.
    fact_verdicts: List[Dict[str, Any]] = field(default_factory=list)

    # Cost / latency accounting
    first_call_usage: Dict[str, int] = field(default_factory=dict)
    retry_call_usage: Dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0
    retries: int = 0

    # Raw model outputs (kept for debugging / audit, NOT shown to user)
    raw_first: Optional[StructuredAnswer] = None
    raw_reextract: Optional[ReextractionResult] = None

    # =================================================================
    # Convenience properties
    # =================================================================

    @property
    def n_facts(self) -> int:
        return len(self.facts)

    @property
    def n_verified(self) -> int:
        return sum(1 for v in self.fact_verdicts if v.get("verdict") == VERDICT_VERIFIED)

    @property
    def n_unverified(self) -> int:
        return self.n_facts - self.n_verified

    @property
    def all_verified(self) -> bool:
        return self.n_facts == 0 or self.n_verified == self.n_facts

    def to_log_dict(self) -> Dict[str, Any]:
        """Compact serialisation for `verification_log` collection."""
        return {
            "outcome": self.outcome,
            "n_facts": self.n_facts,
            "n_verified": self.n_verified,
            "n_unverified": self.n_unverified,
            "retries": self.retries,
            "elapsed_ms": self.elapsed_ms,
            "first_call_usage": self.first_call_usage,
            "retry_call_usage": self.retry_call_usage,
            "first_pass": self.first_pass.to_dict() if self.first_pass else None,
            "second_pass": self.second_pass.to_dict() if self.second_pass else None,
            "fact_verdicts": self.fact_verdicts,
            "generated_at": datetime.now(timezone.utc),
        }


# =====================================================================
# Public entry point
# =====================================================================

def generate_verified_answer(
    *,
    anthropic_client: Any,
    model: str,
    system_prompt: str,
    user_message: str,
    prior_messages: Optional[Sequence[Dict[str, Any]]],
    chunks: Sequence[RetrievedChunk],
    max_tokens: int = 8192,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
    enable_retry: bool = True,
) -> VerifiedAnswer:
    """
    Single-shot verified-answer generation.

    Parameters
    ----------
    anthropic_client
        An initialised `anthropic.Anthropic` instance. We re-use the
        same one that the existing chat layer holds.
    model
        Anthropic model id (e.g. "claude-opus-4-6").
    system_prompt
        The v2 system prompt. We will internally append the structured-
        output instructions (`get_structured_prompt_tail()`) so callers
        don't need to.
    user_message
        The fully-formed user turn (SOURCES + question + tail).
    prior_messages
        Optional conversation history. None / empty for first turn.
    chunks
        The retrieved chunks in [#1], [#2], ... order — same order the
        prompt builder used. Used both for the second-pass re-extraction
        AND for verifying the verbatim quotes.
    max_tokens
        Output cap for Opus.
    fuzzy_threshold
        Passed to the verifier (default 85, OCR-tolerant).
    enable_retry
        If False, skip the re-extraction retry pass. Useful for
        benchmarking / debugging.
    """
    t0 = time.time()
    result = VerifiedAnswer()
    chunks_list = list(chunks)

    # 1. Compose the fully-augmented system prompt.
    full_system = system_prompt.rstrip() + "\n" + get_structured_prompt_tail()

    # 2. Compose the messages list with prior history.
    messages: List[Dict[str, Any]] = list(prior_messages or [])
    messages.append({"role": "user", "content": user_message})

    # ----- First call: structured generation ------------------------
    first = _call_submit_answer(
        client=anthropic_client,
        model=model,
        system_prompt=full_system,
        messages=messages,
        max_tokens=max_tokens,
    )
    result.raw_first = first
    result.first_call_usage = _usage_dict(first)
    result.answer = first.answer
    result.facts = list(first.facts)

    if not first.has_facts():
        # Pure scoping / clarification / expertise answer. Ship as-is.
        result.outcome = OUTCOME_NO_FACTS
        result.elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(
            f"verified-answer: {result.outcome} (no facts emitted, "
            f"elapsed={result.elapsed_ms}ms)"
        )
        return result

    # ----- First verification ---------------------------------------
    first_report = verify_facts(
        first.facts, chunks_list, fuzzy_threshold=fuzzy_threshold
    )
    result.first_pass = first_report

    if first_report.all_passed or not enable_retry:
        # Done. Fast path.
        result.outcome = (
            OUTCOME_VERIFIED_FIRST_PASS if first_report.all_passed
            else OUTCOME_KEPT_ORIGINAL
        )
        result.fact_verdicts = _build_verdicts(result.facts, first_report, None)
        result.elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(
            f"verified-answer: {result.outcome} ({first_report.n_passed}/"
            f"{len(first_report.items)} verified, elapsed={result.elapsed_ms}ms)"
        )
        return result

    # ----- Second call: re-extract only the failed facts ------------
    failed = first_report.failed
    failed_fact_ids = {f.fact_id for f in failed}
    failed_facts = [
        {**f, "_verifier_reason": _reason_for_fact(first_report, f.get("id"))}
        for f in first.facts
        if f.get("id") in failed_fact_ids
    ]
    logger.info(
        f"verified-answer: retrying {len(failed_facts)} failed claims "
        f"({first_report.n_passed}/{len(first_report.items)} verified first pass)"
    )

    reextract = _call_reextract(
        client=anthropic_client,
        model=model,
        system_prompt=full_system,
        prior_messages=messages,
        first_assistant_block=first.raw_tool_input,
        failed_facts=failed_facts,
        chunks=chunks_list,
        max_tokens=max(2048, max_tokens // 2),
    )
    result.raw_reextract = reextract
    result.retry_call_usage = {
        "input_tokens": reextract.input_tokens,
        "output_tokens": reextract.output_tokens,
    }
    result.retries = 1

    if not reextract.by_fact_id:
        # Model didn't call the re-extract tool. Per user's instruction:
        # show the original answer as-is.
        result.outcome = OUTCOME_KEPT_ORIGINAL
        result.fact_verdicts = _build_verdicts(result.facts, first_report, None)
        result.elapsed_ms = int((time.time() - t0) * 1000)
        logger.warning(
            "verified-answer: re-extract returned no usable tool call — "
            "shipping original answer"
        )
        return result

    # Deterministic merge + second-pass verify + outcome decision.
    # Same helper is used by the Sprint-4 agent path so the policy is
    # identical regardless of which front-end produced the facts.
    merge = apply_retry_merge(
        facts=first.facts,
        answer=first.answer,
        first_report=first_report,
        reextract=reextract,
        chunks=chunks_list,
        fuzzy_threshold=fuzzy_threshold,
        failed_fact_ids=failed_fact_ids,
    )
    result.facts = merge["final_facts"]
    result.fact_verdicts = merge["final_verdicts"]
    result.second_pass = merge["second_pass"]
    result.answer = merge["final_answer"]
    result.outcome = merge["outcome"]
    result.elapsed_ms = int((time.time() - t0) * 1000)
    n_pass = sum(
        1 for v in result.fact_verdicts if v["verdict"] == VERDICT_VERIFIED
    )
    logger.info(
        f"verified-answer: {result.outcome}  "
        f"({n_pass}/{len(result.fact_verdicts)} verified after retry, "
        f"elapsed={result.elapsed_ms}ms)"
    )
    return result


# =====================================================================
# Internals
# =====================================================================

def _call_submit_answer(
    *,
    client: Any,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
) -> StructuredAnswer:
    # Streaming so max_tokens may exceed Anthropic's ~21k non-streaming
    # ceiling (production caps are now 32k+ for full-length legal memos).
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        tools=[SUBMIT_ANSWER_TOOL],
        tool_choice={"type": "tool", "name": "submit_answer"},
        messages=messages,
    ) as stream:
        response = stream.get_final_message()
    return parse_submit_answer(response)


def _call_reextract(
    *,
    client: Any,
    model: str,
    system_prompt: str,
    prior_messages: List[Dict[str, Any]],
    first_assistant_block: Dict[str, Any],
    failed_facts: List[Dict[str, Any]],
    chunks: Sequence[RetrievedChunk],
    max_tokens: int,
    prior_tool_name: str = "submit_answer",
) -> ReextractionResult:
    """
    Run the narrow re-extraction call. We continue the same conversation:
    user_turn -> assistant tool_use (submit_*) -> tool_result placeholder
    -> user_turn (reextract prompt) -> assistant tool_use (reextract).

    Anthropic's tool-use API requires us to send the prior assistant
    tool_use block in the messages list, followed by a synthetic
    tool_result block, BEFORE the next user turn. This is the cheapest
    way to give Opus continuity without re-shipping the SOURCES block.

    `prior_tool_name` defaults to "submit_answer" (Sprint-3 one-shot path).
    The Sprint-4 agent passes "submit_final_answer" so the synthetic
    history matches what the model actually called.
    """
    prior_assistant = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_prev_submit",  # synthetic id; not persisted
                "name": prior_tool_name,
                "input": first_assistant_block,
            }
        ],
    }
    tool_result_ack = {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_prev_submit",
                "content": (
                    "submit_answer received. Some claims need verification."
                ),
            },
            {
                "type": "text",
                "text": build_reextract_prompt(failed_facts, list(chunks)),
            },
        ],
    }

    new_messages = list(prior_messages) + [prior_assistant, tool_result_ack]

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        tools=[REEXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "reextract_failed_claims"},
        messages=new_messages,
    )
    return parse_reextract(response)


def apply_retry_merge(
    *,
    facts: List[Dict[str, Any]],
    answer: str,
    first_report: VerificationReport,
    reextract: ReextractionResult,
    chunks: Sequence[RetrievedChunk],
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
    failed_fact_ids: Optional[set] = None,
) -> Dict[str, Any]:
    """
    Deterministic merge of a reextract response into the original facts,
    plus the second-pass verification and outcome decision.

    Used by BOTH Sprint-3 (`generate_verified_answer`) and Sprint-4
    (`AgentRunner._finalize`) so the retry policy is identical: same
    NOT_PRESENT semantics, same OUTCOME_KEPT_ORIGINAL fallback, same
    string-patch of corrected numbers in the prose.

    Returns a dict with:
      final_facts     — facts list with corrected quotes / NOT_PRESENT flags
      final_verdicts  — per-fact verdict objects (frontend-ready)
      second_pass     — VerificationReport of the second pass
      final_answer    — original prose with corrected quotes patched in
      outcome         — VERIFIED_FIRST_PASS / VERIFIED_AFTER_RETRY / KEPT_ORIGINAL
      any_corrected   — bool flag for downstream logging
    """
    chunks_list = list(chunks)
    if failed_fact_ids is None:
        failed_fact_ids = {i.fact_id for i in first_report.items if not i.passed}

    # Merge re-extracted quotes / NOT_PRESENT flags into the original facts.
    merged_facts: List[Dict[str, Any]] = []
    for f in facts:
        fid = f.get("id")
        if fid in reextract.by_fact_id:
            patch = reextract.by_fact_id[fid]
            status = (patch.get("status") or "").upper()
            new = dict(f)
            if status == "REEXTRACTED":
                new_q = patch.get("verbatim_quote") or ""
                new_c = patch.get("corrected_claim") or f.get("claim")
                if new_q:
                    new["verbatim_quote"] = new_q
                if new_c:
                    new["claim"] = new_c
            elif status == "NOT_PRESENT":
                new["_not_present"] = True
            merged_facts.append(new)
        else:
            merged_facts.append(dict(f))

    # Second-pass verification — only over re-extracted, not NOT_PRESENT.
    to_reverify = [
        f for f in merged_facts
        if f.get("id") in failed_fact_ids and not f.get("_not_present")
    ]
    second_report = (
        verify_facts(to_reverify, chunks_list, fuzzy_threshold=fuzzy_threshold)
        if to_reverify
        else VerificationReport(threshold=fuzzy_threshold)
    )

    # Decide final state per fact.
    #   first pass passed              -> keep original (verified)
    #   re-extracted + 2nd pass passed -> keep merged (verified after retry)
    #   re-extracted + 2nd pass failed -> keep ORIGINAL (per user policy)
    #   NOT_PRESENT                    -> keep ORIGINAL (per user policy)
    final_facts: List[Dict[str, Any]] = []
    final_verdicts: List[Dict[str, Any]] = []
    second_by_id = {i.fact_id: i for i in second_report.items}

    for orig, merged in zip(facts, merged_facts):
        fid = orig.get("id")
        first_item = _item_for_fact(first_report, fid)

        if first_item and first_item.passed:
            final_facts.append(orig)
            final_verdicts.append(
                _verdict_from_item("first_pass_verified", first_item, orig)
            )
            continue

        if merged.get("_not_present"):
            final_facts.append(orig)
            final_verdicts.append({
                "fact_id": fid,
                "verdict": "UNVERIFIED",
                "stage": "second_pass_not_present",
                "claim": orig.get("claim"),
                "source_chunk_id": orig.get("source_chunk_id"),
                "verbatim_quote": orig.get("verbatim_quote"),
                "score": 0.0,
                "reason": (
                    "Re-extraction reported NOT_PRESENT. Original answer "
                    "preserved per configuration; lawyer should verify "
                    "via evidence panel."
                ),
            })
            continue

        second_item = second_by_id.get(fid)
        if second_item and second_item.passed:
            final_facts.append(merged)
            final_verdicts.append(
                _verdict_from_item("second_pass_verified", second_item, merged)
            )
            continue

        # Both passes failed — ship original.
        final_facts.append(orig)
        reason = (
            second_item.reason if second_item
            else first_item.reason if first_item
            else "verification failed twice"
        )
        final_verdicts.append({
            "fact_id": fid,
            "verdict": "UNVERIFIED",
            "stage": "second_pass_failed",
            "claim": orig.get("claim"),
            "source_chunk_id": orig.get("source_chunk_id"),
            "verbatim_quote": orig.get("verbatim_quote"),
            "score": (
                second_item.score if second_item
                else first_item.score if first_item
                else 0.0
            ),
            "reason": reason,
        })

    any_corrected = any(
        m.get("verbatim_quote") != o.get("verbatim_quote")
        or m.get("claim") != o.get("claim")
        for o, m in zip(facts, merged_facts)
        if not m.get("_not_present")
    )
    n_pass = sum(1 for v in final_verdicts if v["verdict"] == VERDICT_VERIFIED)
    if n_pass == len(final_verdicts):
        outcome = (
            OUTCOME_VERIFIED_AFTER_RETRY if any_corrected
            else OUTCOME_VERIFIED_FIRST_PASS
        )
    else:
        outcome = OUTCOME_KEPT_ORIGINAL

    final_answer = (
        _patch_prose(answer, facts, merged_facts) if any_corrected else answer
    )

    return {
        "final_facts": final_facts,
        "final_verdicts": final_verdicts,
        "second_pass": second_report,
        "final_answer": final_answer,
        "outcome": outcome,
        "any_corrected": any_corrected,
    }


def _usage_dict(s: StructuredAnswer) -> Dict[str, int]:
    return {
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "cache_read_tokens": s.cache_read_tokens,
        "cache_creation_tokens": s.cache_creation_tokens,
    }


def _item_for_fact(
    report: Optional[VerificationReport],
    fid: Optional[str],
) -> Optional[VerificationItem]:
    if not report or not fid:
        return None
    for i in report.items:
        if i.fact_id == fid:
            return i
    return None


def _reason_for_fact(report: VerificationReport, fid: Optional[str]) -> str:
    item = _item_for_fact(report, fid)
    return item.reason or "" if item else ""


def _build_verdicts(
    facts: List[Dict[str, Any]],
    first: VerificationReport,
    second: Optional[VerificationReport],
) -> List[Dict[str, Any]]:
    """Frontend-friendly per-fact verdict list."""
    by_id_first = {i.fact_id: i for i in first.items}
    by_id_second = {i.fact_id: i for i in (second.items if second else [])}
    out: List[Dict[str, Any]] = []
    for f in facts:
        fid = f.get("id")
        i1 = by_id_first.get(fid)
        i2 = by_id_second.get(fid)
        used = i2 or i1
        if not used:
            out.append({
                "fact_id": fid,
                "verdict": "UNVERIFIED",
                "stage": "missing",
                "claim": f.get("claim"),
                "source_chunk_id": f.get("source_chunk_id"),
                "verbatim_quote": f.get("verbatim_quote"),
                "score": 0.0,
                "reason": "no verifier item found",
            })
            continue
        out.append({
            "fact_id": fid,
            "verdict": used.verdict,
            "stage": "second_pass" if i2 else "first_pass",
            "claim": f.get("claim"),
            "source_chunk_id": f.get("source_chunk_id"),
            "verbatim_quote": f.get("verbatim_quote"),
            "matched_span": used.matched_span,
            "score": used.score,
            "reason": used.reason,
        })
    return out


def _verdict_from_item(
    stage: str,
    item: VerificationItem,
    fact: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "fact_id": item.fact_id,
        "verdict": item.verdict,
        "stage": stage,
        "claim": fact.get("claim"),
        "source_chunk_id": item.source_chunk_id,
        "verbatim_quote": fact.get("verbatim_quote"),
        "matched_span": item.matched_span,
        "score": item.score,
        "reason": item.reason,
    }


def _patch_prose(
    original_prose: str,
    original_facts: List[Dict[str, Any]],
    merged_facts: List[Dict[str, Any]],
) -> str:
    """
    Best-effort string-replace of stale verbatim quotes in the prose
    answer with their re-extracted versions. NOT a regenerate — we keep
    it deterministic so we don't pay for an extra Opus call.

    This handles the common case where Opus paraphrased a number in the
    prose ("$405,000") that's been corrected ("$450,000") by the
    re-extraction. We swap the most-distinctive surrogate (the number,
    date, or 5+ word phrase) of the old quote for the new one.
    """
    if not original_prose:
        return original_prose
    out = original_prose
    for orig, merged in zip(original_facts, merged_facts):
        if merged.get("_not_present"):
            continue
        old_q = (orig.get("verbatim_quote") or "").strip()
        new_q = (merged.get("verbatim_quote") or "").strip()
        if not old_q or not new_q or old_q == new_q:
            continue
        # If the prose contains the old verbatim, swap it out.
        if old_q in out:
            out = out.replace(old_q, new_q)
            continue
        # Otherwise try replacing distinctive sub-tokens (currency/date).
        import re as _re
        # Currency tokens that differ between old and new
        old_curs = _re.findall(r"\$[\d,]+(?:\.\d+)?", old_q)
        new_curs = _re.findall(r"\$[\d,]+(?:\.\d+)?", new_q)
        if len(old_curs) == 1 and len(new_curs) == 1 and old_curs[0] != new_curs[0]:
            if old_curs[0] in out:
                out = out.replace(old_curs[0], new_curs[0])
    return out


# =====================================================================
# Optional persistence
# =====================================================================

def log_verification(
    *,
    mongo_db: Any,
    verified: VerifiedAnswer,
    query: str,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    collection_name: str = "verification_log",
) -> None:
    """
    Persist a compact verification record to MongoDB. Fire-and-forget —
    silent on errors so a logging failure can never block an answer.
    """
    try:
        doc = verified.to_log_dict()
        doc.update({
            "query": query,
            "session_id": session_id,
            "model": model,
            "answer": verified.answer,
        })
        mongo_db[collection_name].insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"verification_log write failed (non-fatal): {exc}")


__all__ = [
    "VerifiedAnswer",
    "OUTCOME_VERIFIED_FIRST_PASS",
    "OUTCOME_VERIFIED_AFTER_RETRY",
    "OUTCOME_KEPT_ORIGINAL",
    "OUTCOME_NO_FACTS",
    "OUTCOME_FALLBACK",
    "generate_verified_answer",
    "log_verification",
    # Re-export helpers for Sprint-4 agent retry parity
    "apply_retry_merge",
    "_call_reextract",
    "_reason_for_fact",
]
