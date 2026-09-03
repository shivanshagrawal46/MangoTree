"""
Conversation Summary Memory.

Why this exists:
  v1 sends EVERY prior turn (Q + A) to Claude on every new question. After
  20-30 turns this means hundreds of thousands of tokens of history per
  call. Two failures emerge:

    1. Cost & latency explode (linear in turn count).
    2. "Lost in the middle" — Claude attends less to messages buried deep
       in a long context, so instructions / facts the user established
       early in the conversation get progressively forgotten.

  Solution: after `keep_recent` turns, fold older turns into a compact
  running summary that Claude maintains itself. Send: [summary] + [last N
  full turns]. Costs become bounded; the user's early instructions stay
  alive in the summary.

LLM choice: Sonnet 4.6 (configurable, never Haiku) so summaries are
high-fidelity. The summary is updated incrementally — each call only
processes the NEW turns since the last summary.

Design notes:
  • Pure dataclass state — no I/O beyond the Claude call.
  • Fail-safe: if Claude errors during summarisation, we fall back to
    sending raw history (degrades to v1 behaviour for that turn).
  • Each `Turn` is treated as the canonical record; we never modify it.
  • Token budgeting is a hard cap on prompt size — if even with summary
    the context would overflow, we drop the oldest "kept" turns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import anthropic

from src.utils.logger import logger


# Approximate token counter (avoids hard dependency on tiktoken here).
def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    """Lightweight turn record (decoupled from src.rag.chat.Turn)."""
    question: str
    answer: str


@dataclass
class MemoryState:
    """Mutable state of the conversation memory for one chat session."""
    summary: str = ""
    summarised_through: int = 0  # last index of `turns` covered by `summary`


# ---------------------------------------------------------------------------
# SummaryMemory
# ---------------------------------------------------------------------------

class SummaryMemory:
    """
    Maintains a running summary of older turns and exposes a list of
    messages to send to Claude.

    Usage pattern (in chat.py):

        memory = SummaryMemory(client, ...)
        # before generating a turn:
        prior = memory.build_prior_messages(turns_so_far)
        # after appending a new (q, a) to turns_so_far:
        memory.maybe_update_summary(turns_so_far)

    The instance is owned by the chat session — one instance per session.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        *,
        model: str = "claude-sonnet-4-6",
        summary_after_turns: int = 8,
        keep_recent: int = 5,
        max_summary_tokens: int = 1500,
        prompt_token_budget: int = 60_000,
    ) -> None:
        self.client = client
        self.model = model
        self.summary_after_turns = max(2, summary_after_turns)
        self.keep_recent = max(1, keep_recent)
        self.max_summary_tokens = max(256, max_summary_tokens)
        self.prompt_token_budget = max(8_000, prompt_token_budget)
        self.state = MemoryState()

    # ------------------------------------------------------------------
    # Public — build the messages list to send to Claude
    # ------------------------------------------------------------------

    def build_prior_messages(
        self,
        turns: Sequence[Turn],
    ) -> List[Dict[str, str]]:
        """
        Returns a list of {role, content} messages representing all turns
        BEFORE the current new question.

        Strategy:
          • If total turns <= summary_after_turns → return all verbatim.
          • Otherwise: prepend summary as a system-style assistant message,
            followed by the last `keep_recent` turns verbatim.
        """
        if not turns:
            return []

        if len(turns) <= self.summary_after_turns:
            return _flatten_turns(turns)

        # We have a summary OR we'll generate one shortly. The summary
        # covers turns[: summarised_through]. Emit summary + tail.
        summary = self.state.summary or ""
        kept_start = max(self.state.summarised_through, len(turns) - self.keep_recent)
        kept = turns[kept_start:]

        msgs: List[Dict[str, str]] = []
        if summary:
            msgs.append(_summary_message(summary))
        msgs.extend(_flatten_turns(kept))

        # Hard cap: if even this is too big, drop oldest kept turns.
        msgs = _enforce_token_budget(msgs, self.prompt_token_budget)
        return msgs

    # ------------------------------------------------------------------
    # Public — refresh summary if needed
    # ------------------------------------------------------------------

    def maybe_update_summary(self, turns: Sequence[Turn]) -> None:
        """
        After a new turn is appended, decide if we need to roll the summary
        forward and (best-effort) regenerate it.

        Trigger condition: there exist turns NOT yet covered by the
        summary, AND total turns > summary_after_turns. We always leave
        `keep_recent` recent turns OUTSIDE the summary so the model sees
        them verbatim.
        """
        n = len(turns)
        if n <= self.summary_after_turns:
            return

        # Turns to summarise = everything except the last keep_recent.
        target_through = max(0, n - self.keep_recent)
        if target_through <= self.state.summarised_through:
            return  # already covered

        to_summarise = turns[: target_through]
        try:
            new_summary = self._generate_summary(
                prior_summary=self.state.summary,
                turns=to_summarise,
                already_covered_through=self.state.summarised_through,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Summary update failed (keeping old summary): {exc}")
            return

        if new_summary:
            self.state.summary = new_summary
            self.state.summarised_through = target_through
            logger.debug(
                f"Conversation summary updated through turn {target_through} "
                f"({len(new_summary)} chars)"
            )

    # ------------------------------------------------------------------
    # Internal — call Claude to generate / extend the summary
    # ------------------------------------------------------------------

    def _generate_summary(
        self,
        *,
        prior_summary: str,
        turns: Sequence[Turn],
        already_covered_through: int,
    ) -> str:
        """
        Generate a fresh summary covering all `turns`. We always pass the
        prior summary so Claude can incorporate it (compounding memory).
        """
        # Build a compact transcript of the new portion (turns that are
        # past `already_covered_through`). Older ones are already in the
        # prior summary so we don't need to re-feed them.
        new_portion = turns[already_covered_through:]
        if not new_portion:
            return prior_summary  # nothing to do

        transcript = "\n\n".join(
            f"[Turn {already_covered_through + i + 1}]\n"
            f"USER: {t.question.strip()}\n\n"
            f"ASSISTANT: {t.answer.strip()}"
            for i, t in enumerate(new_portion)
        )

        system = (
            "You are a memory compaction assistant for a senior legal advisor "
            "AI. Maintain a faithful, dense, fact-centric summary of an "
            "ongoing conversation between an investigator and the AI. "
            "Focus on: established facts, named entities (parties, dollar "
            "amounts, dates, document names, case/docket numbers), "
            "instructions or preferences the user has given the AI, and "
            "open questions. Drop pleasantries. Bullet-list style is fine. "
            "Aim for ~400 words; never exceed ~600."
        )

        if prior_summary:
            user_msg = (
                "PRIOR SUMMARY (covers turns 1..N):\n"
                f"{prior_summary}\n\n"
                "NEW TURNS TO INCORPORATE INTO THE SUMMARY:\n"
                f"{transcript}\n\n"
                "Produce an UPDATED summary that incorporates the new turns "
                "while preserving everything from the prior summary that "
                "remains relevant. Output ONLY the updated summary text — "
                "no preamble, no JSON, no markdown headers."
            )
        else:
            user_msg = (
                "FULL CONVERSATION SO FAR:\n"
                f"{transcript}\n\n"
                "Produce a dense factual summary as described. Output only "
                "the summary text."
            )

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_summary_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        parts: List[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _flatten_turns(turns: Sequence[Turn]) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    for t in turns:
        msgs.append({"role": "user", "content": t.question})
        msgs.append({"role": "assistant", "content": t.answer})
    return msgs


def _summary_message(summary: str) -> Dict[str, str]:
    """Encode the summary as an assistant message — Claude sees it as 'recap'."""
    body = (
        "[Conversation memory — running summary of earlier turns]\n\n"
        f"{summary}\n\n"
        "[End of memory. The recent turns follow verbatim.]"
    )
    return {"role": "assistant", "content": body}


def _enforce_token_budget(
    messages: List[Dict[str, str]],
    budget: int,
) -> List[Dict[str, str]]:
    """
    If `messages` exceed `budget` tokens, drop the OLDEST verbatim turns
    until we fit. Always keep the leading summary message (if any) and
    the last user/assistant pair.
    """
    if not messages:
        return messages

    def _total(msgs: List[Dict[str, str]]) -> int:
        return sum(_approx_tokens(m["content"]) + 8 for m in msgs)

    if _total(messages) <= budget:
        return messages

    has_summary = (
        messages
        and messages[0]["role"] == "assistant"
        and messages[0]["content"].startswith("[Conversation memory")
    )

    summary = [messages[0]] if has_summary else []
    rest = messages[1:] if has_summary else messages[:]

    # Drop oldest pairs (user+assistant) from `rest`.
    while rest and _total(summary + rest) > budget and len(rest) > 2:
        # Pop the oldest pair: index 0 (user) and 1 (assistant) if present.
        rest = rest[2:] if len(rest) >= 2 else rest[1:]

    return summary + rest
