"""Scratchpad and budget — the agent's memory and its leash.

The scratchpad is the one place evidence lives during a run. Chunks are keyed by
``chunk_id``; adding an already-present chunk is a no-op, and the display index
``[#N]`` a chunk receives on first arrival is the index it keeps. That is what
lets a citation written on turn two still mean the same passage on turn twenty,
and what keeps the context from ballooning when tools return overlapping sets.

The budget is one profile — 30 tool calls, 10M tokens, 15 minutes — plus a
manual interrupt. Hitting a ceiling does not produce a stub; the loop makes one
more planner call that forces a full answer from what has been gathered.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from mangotree.retrieve import config as cfg
from mangotree.retrieve.hits import Hit

EventEmitter = Callable[[str, Dict[str, Any]], None]

STEP_SEED = "seed"
STEP_TOOL = "tool"
STEP_REASONING = "reasoning"
STEP_GATE = "sufficiency_gate"
STEP_FINAL = "final"


@dataclass
class BudgetTracker:
    max_tool_calls: int = cfg.AGENT_MAX_TOOL_CALLS
    max_total_tokens: int = cfg.AGENT_MAX_TOTAL_TOKENS
    max_wall_clock_s: float = float(cfg.AGENT_MAX_WALL_CLOCK_S)

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
        if self.interrupt_requested:
            return "interrupted by user"
        if self.tool_calls_used >= self.max_tool_calls:
            return f"tool-call budget reached ({self.max_tool_calls})"
        if self.total_tokens >= self.max_total_tokens:
            return f"token budget reached ({self.max_total_tokens:,})"
        if self.elapsed_s >= self.max_wall_clock_s:
            return f"time budget reached ({int(self.max_wall_clock_s // 60)} min)"
        return None

    def remaining_calls(self) -> int:
        return max(0, self.max_tool_calls - self.tool_calls_used)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "tool_calls_used": self.tool_calls_used, "max_tool_calls": self.max_tool_calls,
            "input_tokens": self.input_tokens_used, "output_tokens": self.output_tokens_used,
            "cache_read_tokens": self.cache_read_tokens, "total_tokens": self.total_tokens,
            "max_total_tokens": self.max_total_tokens,
            "elapsed_s": round(self.elapsed_s, 1), "max_wall_clock_s": self.max_wall_clock_s,
            "interrupted": self.interrupt_requested,
        }


@dataclass
class AgentStep:
    step_num: int
    type: str
    tool_name: str = ""
    tool_input: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    new_indices: List[int] = field(default_factory=list)
    error: str = ""
    elapsed_ms: int = 0
    reasoning: str = ""

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["tool_input"] = {k: (v if not isinstance(v, str) or len(v) < 400 else v[:400] + "…")
                           for k, v in (self.tool_input or {}).items()}
        return d


class AgentScratchpad:
    def __init__(self, question: str, *, budget: Optional[BudgetTracker] = None,
                 on_event: Optional[EventEmitter] = None) -> None:
        self.question = question
        self.started_at = datetime.now(timezone.utc)
        self.budget = budget or BudgetTracker()
        self._on_event = on_event
        self._steps: List[AgentStep] = []
        self._chunks: List[Hit] = []
        self._by_id: Dict[str, int] = {}
        self.notes: List[str] = []          # planner's own scratch notes, if it leaves any
        self.searches: List[str] = []       # every query string run, for the coverage statement
        self.lists_seen: Dict[str, int] = {}
        self.degrades: List[str] = []
        self.enumerations: List[Dict[str, Any]] = []
        self.scopes_touched: set = set()

    # ------------------------------------------------------------------ read
    @property
    def steps(self) -> List[AgentStep]:
        return list(self._steps)

    @property
    def chunks(self) -> List[Hit]:
        return list(self._chunks)

    @property
    def n_chunks(self) -> int:
        return len(self._chunks)

    @property
    def seen_ids(self) -> set:
        return set(self._by_id)

    def get(self, index: int) -> Optional[Hit]:
        if 1 <= index <= len(self._chunks):
            return self._chunks[index - 1]
        return None

    def index_of(self, chunk_id: str) -> Optional[int]:
        return self._by_id.get(chunk_id)

    # ----------------------------------------------------------------- write
    def add_chunks(self, chunks: Sequence[Hit]) -> List[int]:
        """Merge; return only the display indices that are genuinely new."""
        added: List[int] = []
        for c in chunks:
            cid = c.chunk_id
            if not cid or cid in self._by_id:
                continue
            self._chunks.append(c)
            idx = len(self._chunks)
            self._by_id[cid] = idx
            added.append(idx)
        return added

    def record_step(self, step: AgentStep) -> None:
        self._steps.append(step)
        self.emit("agent_step", step.as_dict())

    def next_step_num(self) -> int:
        return len(self._steps) + 1

    def emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, payload)
        except Exception:
            pass

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "started_at": self.started_at.isoformat(),
            "n_chunks": self.n_chunks,
            "steps": [s.as_dict() for s in self._steps],
            "budget": self.budget.as_dict(),
            "searches": self.searches,
            "degrades": self.degrades,
            "enumerations": self.enumerations,
            "scopes_touched": sorted(self.scopes_touched),
        }
