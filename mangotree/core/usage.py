"""Process-wide token meter and price table.

Every model call the system makes records its usage here, keyed by model, so
the cost of any run can be read as the difference between two snapshots — the
agent's own planner turns *and* the rerank, rewrite, skeptic and verdict calls
made on its behalf, which the agent budget alone never saw.

Prices are Anthropic / OpenAI direct list prices per million tokens as of
September 2026 (checked 2026-09-07). Update here, nowhere else.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# (input, output, cache read, cache write) — USD per million tokens
PRICES: Dict[str, tuple] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.50),
    "claude-fable-5": (10.0, 50.0, 1.00, 12.50),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.50),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    "gpt-6-astra": (10.0, 50.0, 1.00, 12.50),
    "gpt-5.6": (4.0, 20.0, 0.40, 0.0),
}


def price_for(model: str) -> tuple:
    m = (model or "").lower()
    for key, p in PRICES.items():
        if m.startswith(key):
            return p
    return (10.0, 50.0, 1.0, 12.5)   # unknown model: assume flagship rates


def cost_usd(model: str, *, input_tokens: int = 0, output_tokens: int = 0,
             cache_read: int = 0, cache_write: int = 0) -> float:
    pin, pout, pread, pwrite = price_for(model)
    return (input_tokens * pin + output_tokens * pout + cache_read * pread + cache_write * pwrite) / 1_000_000


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0        # uncached input
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write

    def minus(self, other: "Usage") -> "Usage":
        return Usage(self.calls - other.calls, self.input_tokens - other.input_tokens, self.output_tokens - other.output_tokens,
                     self.cache_read - other.cache_read, self.cache_write - other.cache_write)

    def cost(self, model: str) -> float:
        return cost_usd(model, input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                        cache_read=self.cache_read, cache_write=self.cache_write)

    def as_dict(self, model: Optional[str] = None) -> Dict[str, Any]:
        d = {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
             "cache_read": self.cache_read, "cache_write": self.cache_write}
        if model:
            d["cost_usd"] = round(self.cost(model), 4)
        return d


class UsageMeter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_model: Dict[str, Usage] = {}

    def record(self, model: str, *, input_tokens: int = 0, output_tokens: int = 0,
               cache_read: int = 0, cache_write: int = 0) -> None:
        with self._lock:
            u = self._by_model.setdefault(model, Usage())
            u.add(Usage(1, int(input_tokens or 0), int(output_tokens or 0), int(cache_read or 0), int(cache_write or 0)))

    def record_anthropic(self, model: str, response: Any) -> None:
        """Anthropic usage: input_tokens is already the uncached part."""
        u = getattr(response, "usage", None)
        if u is None:
            return
        self.record(model, input_tokens=getattr(u, "input_tokens", 0) or 0, output_tokens=getattr(u, "output_tokens", 0) or 0,
                    cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                    cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0)

    def record_openai(self, model: str, usage: Any) -> None:
        """OpenAI usage: input_tokens INCLUDES cached tokens; split them out."""
        if usage is None:
            return
        total_in = getattr(usage, "input_tokens", None)
        if total_in is None:
            total_in = getattr(usage, "prompt_tokens", 0) or 0
        details = getattr(usage, "input_tokens_details", None) or getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", 0) or 0
        out = getattr(usage, "output_tokens", None)
        if out is None:
            out = getattr(usage, "completion_tokens", 0) or 0
        self.record(model, input_tokens=max(0, (total_in or 0) - cached), output_tokens=out or 0, cache_read=cached)

    def snapshot(self) -> Dict[str, Usage]:
        with self._lock:
            return {m: Usage(u.calls, u.input_tokens, u.output_tokens, u.cache_read, u.cache_write) for m, u in self._by_model.items()}

    @staticmethod
    def diff(before: Dict[str, Usage], after: Dict[str, Usage]) -> Dict[str, Usage]:
        out: Dict[str, Usage] = {}
        for m, u in after.items():
            d = u.minus(before.get(m, Usage()))
            if d.calls:
                out[m] = d
        return out

    @staticmethod
    def summarize(delta: Dict[str, Usage]) -> Dict[str, Any]:
        per = {m: u.as_dict(m) for m, u in delta.items()}
        return {"by_model": per, "total_cost_usd": round(sum(u.cost(m) for m, u in delta.items()), 4),
                "total_calls": sum(u.calls for u in delta.values())}


METER = UsageMeter()
