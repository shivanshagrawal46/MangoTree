"""
Agent scratchpad — per-query mutable state for the v3 agent.

The scratchpad owns:
  • the running list of RetrievedChunk objects the agent has discovered
    (deduplicated and stably indexed as [#1], [#2], ... so citations
    stay consistent across tool calls)
  • the audit trail of agent steps (planner decisions, tool calls,
    tool results) for the WebSocket stream AND for the final
    `agent_trace` persisted to Mongo
  • the budget tracker (tool-call count, token usage, wall clock)
  • the interrupt flag (lawyer clicked "stop")

Design notes
------------

1. **Stable indexing.** When the agent fetches new chunks, we DON'T
   renumber. New chunks get the next unused index. This keeps any
   `[#N]` references in the planner's reasoning consistent across
   tool calls. The final answer's `source_chunk_id` therefore refers
   to the same chunk it referred to during planning.

2. **Dedup by chunk_id.** Tools may re-discover chunks we already have
   (e.g. `search` and `fetch_full_document` overlap). We keep one copy
   keyed by `chunk_id` (the Mongo `_id` string) and return the existing
   display index. This prevents the prompt from ballooning with
   duplicates.

3. **Budget enforcement is policy, not mechanism.** The scratchpad
   tracks usage; the agent loop checks `budget.exhausted()` before
   each iteration. We never raise from inside the scratchpad — we
   surface signals and let the loop decide what to do.

4. **No I/O.** This module deliberately does no Mongo, no Anthropic.
   It's pure state and easy to test.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.rag.retriever import RetrievedChunk


# =====================================================================
# Step record (one per planner decision + tool result)
# =====================================================================

STEP_TYPE_PLAN = "plan"            # planner reasoning step (no tool yet)
STEP_TYPE_TOOL = "tool_call"       # tool was called + executed
STEP_TYPE_FINAL = "submit_final_answer"
STEP_TYPE_FORCED = "forced_finalize"  # budget exhausted, we cut short
STEP_TYPE_INTERRUPT = "interrupt"     # user clicked stop


@dataclass
class AgentStep:
    step_num: int
    type: str
    tool_name: Optional[str] = None
    tool_input: Dict[str, Any] = field(default_factory=dict)
    tool_result_summary: str = ""          # one-line for display
    tool_result_full: Any = None           # full data (chunks etc.)
    new_chunk_indices: List[int] = field(default_factory=list)  # [#N] indices added
    error: Optional[str] = None
    elapsed_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # =================================================================
    # Serialisation
    # =================================================================

    def to_stream_event(self) -> Dict[str, Any]:
        """Frontend-friendly compact event (no heavy payload)."""
        return {
            "step_num": self.step_num,
            "type": self.type,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "summary": self.tool_result_summary,
            "new_chunk_indices": self.new_chunk_indices,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cache_read": self.cache_read_tokens,
            },
        }

    def to_log_dict(self) -> Dict[str, Any]:
        """Audit log dict — keeps tool_result_full for forensic replay."""
        d = self.to_stream_event()
        d["started_at"] = self.started_at
        # Don't store full chunk objects in tool_result_full — too heavy.
        # The result summary + new_chunk_indices is enough; the chunks
        # themselves are accessible via the scratchpad snapshot.
        return d


# =====================================================================
# Budget tracker
# =====================================================================

@dataclass
class BudgetTracker:
    """
    Per-query budget enforced by the agent loop.

    Three independent guards:
      • max_tool_calls — hard cap on iterations
      • max_total_tokens — sum of planner input+output across all steps
      • max_wall_clock_s — total time spent in the agent loop
    """
    # Worst-case defaults — designed never to trip on a legitimately
    # complex legal query. The .env file is the source of truth for
    # production deployments; these are only used when BudgetTracker
    # is constructed directly (smokes, isolated unit tests).
    max_tool_calls: int = 30
    max_total_tokens: int = 3_000_000
    max_wall_clock_s: float = 1200.0

    tool_calls_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    cache_read_tokens: int = 0
    started_at: float = field(default_factory=time.time)
    interrupt_requested: bool = False

    def record(self, *, input_tokens: int = 0, output_tokens: int = 0,
               cache_read: int = 0, was_tool_call: bool = True) -> None:
        if was_tool_call:
            self.tool_calls_used += 1
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens
        self.cache_read_tokens += cache_read

    @property
    def total_tokens(self) -> int:
        return self.input_tokens_used + self.output_tokens_used

    @property
    def elapsed_s(self) -> float:
        return time.time() - self.started_at

    def exhausted(self) -> Optional[str]:
        """Returns a reason string if any limit is hit, else None."""
        if self.interrupt_requested:
            return "interrupt"
        if self.tool_calls_used >= self.max_tool_calls:
            return f"max_tool_calls ({self.max_tool_calls}) reached"
        if self.total_tokens >= self.max_total_tokens:
            return f"max_total_tokens ({self.max_total_tokens}) reached"
        if self.elapsed_s >= self.max_wall_clock_s:
            return f"max_wall_clock_s ({self.max_wall_clock_s}) reached"
        return None

    def remaining_calls(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls_used)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_calls_used": self.tool_calls_used,
            "max_tool_calls": self.max_tool_calls,
            "input_tokens_used": self.input_tokens_used,
            "output_tokens_used": self.output_tokens_used,
            "total_tokens": self.total_tokens,
            "max_total_tokens": self.max_total_tokens,
            "elapsed_s": round(self.elapsed_s, 2),
            "max_wall_clock_s": self.max_wall_clock_s,
            "interrupt_requested": self.interrupt_requested,
            "remaining_calls": self.remaining_calls(),
        }


# =====================================================================
# Scratchpad — the heart of the agent loop
# =====================================================================

EventEmitter = Callable[[str, Dict[str, Any]], None]


class AgentScratchpad:
    """
    Mutable state for one agent run.

    Public surface:
      • query, started_at
      • steps            — read-only list of AgentStep
      • all_chunks       — read-only list (in stable [#1], [#2], ... order)
      • get_chunk(idx)   — by 1-based display index
      • get_chunk_by_id(id) — by Mongo _id string
      • add_chunks(chunks) -> List[int]  (returns the [#N] indices added)
      • record_step(step) -> None
      • budget
      • emit(event_type, payload) -> None   (calls the stream callback)
    """

    def __init__(
        self,
        query: str,
        *,
        budget: Optional[BudgetTracker] = None,
        on_event: Optional[EventEmitter] = None,
    ) -> None:
        self.query = query
        self.started_at = datetime.now(timezone.utc)
        self.budget = budget or BudgetTracker()
        self._on_event = on_event

        self._steps: List[AgentStep] = []
        self._chunks: List[RetrievedChunk] = []
        self._by_id: Dict[str, int] = {}   # chunk_id -> 1-based display index

    # -----------------------------------------------------------------
    # Read access
    # -----------------------------------------------------------------

    @property
    def steps(self) -> List[AgentStep]:
        return list(self._steps)

    @property
    def all_chunks(self) -> List[RetrievedChunk]:
        return list(self._chunks)

    @property
    def n_chunks(self) -> int:
        return len(self._chunks)

    def get_chunk(self, display_index: int) -> Optional[RetrievedChunk]:
        if display_index < 1 or display_index > len(self._chunks):
            return None
        return self._chunks[display_index - 1]

    def get_chunk_by_id(self, chunk_id: str) -> Optional[RetrievedChunk]:
        idx = self._by_id.get(chunk_id)
        return self._chunks[idx - 1] if idx else None

    # -----------------------------------------------------------------
    # Write — chunk merging
    # -----------------------------------------------------------------

    def add_chunks(self, chunks: Sequence[RetrievedChunk]) -> List[int]:
        """
        Merge `chunks` into the scratchpad. New unique chunks (by
        chunk_id) get appended in order and assigned the next [#N]
        display index. Already-present chunks are silently skipped.

        Returns the list of NEW display indices added (so the planner
        knows which sources are fresh).
        """
        added: List[int] = []
        for c in chunks:
            cid = c.chunk_id or ""
            if not cid:
                # Skip chunks without a stable id — they can't be
                # cited reliably.
                continue
            if cid in self._by_id:
                continue
            self._chunks.append(c)
            idx = len(self._chunks)
            self._by_id[cid] = idx
            added.append(idx)
        return added

    # -----------------------------------------------------------------
    # Write — step recording
    # -----------------------------------------------------------------

    def record_step(self, step: AgentStep) -> None:
        self._steps.append(step)
        self.emit("agent_step", step.to_stream_event())

    def next_step_num(self) -> int:
        return len(self._steps) + 1

    # -----------------------------------------------------------------
    # Streaming bridge
    # -----------------------------------------------------------------

    def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Pass-through to the optional stream callback. Safe if None."""
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, payload)
        except Exception:
            # A buggy callback must never crash the agent.
            pass

    # -----------------------------------------------------------------
    # Snapshot for audit / log
    # -----------------------------------------------------------------

    def to_audit_dict(self, *, final_answer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Compact representation suitable for the `agent_trace` Mongo doc.
        Chunks themselves are NOT included — they're already referenced
        by chunk_id in the steps. Saves Mongo doc size.
        """
        return {
            "query": self.query,
            "started_at": self.started_at,
            "completed_at": datetime.now(timezone.utc),
            "budget": self.budget.to_dict(),
            "n_steps": len(self._steps),
            "n_chunks_discovered": len(self._chunks),
            "chunk_ids": [c.chunk_id for c in self._chunks],
            "steps": [s.to_log_dict() for s in self._steps],
            "final_answer": final_answer,
        }
