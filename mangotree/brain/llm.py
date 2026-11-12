"""Opus 5.5 calls for the brain.

Two shapes only:

* ``structured`` — one call that must return an object of a given shape. Uses
  the API's structured output (``output_config.format``), so the reply is valid
  JSON by construction. Forced tool choice is not available on this model, and
  thinking cannot be switched off; effort is the dial.
* the chat/draft tool loop lives in ``agent.py``.

Every call is streamed (long inputs, long outputs) and metered.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional, Sequence, Union

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.core.usage import METER

from . import config as bc

_client = None
_lock = threading.Lock()


class BrainError(RuntimeError):
    pass


class BrainRefusal(BrainError):
    """The model declined (safety classifier). Never retried blindly."""


def client():
    global _client
    with _lock:
        if _client is None:
            import anthropic
            _client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key, max_retries=3, timeout=900.0)
        return _client


def system_blocks(*parts: str) -> List[dict]:
    """System prompt as blocks, each a cache breakpoint. Order them stable-first:
    the core instructions never change; the property context changes when the
    property does; anything volatile belongs in the user turn."""
    blocks = [{"type": "text", "text": p} for p in parts if p]
    # Up to three breakpoints here (the request-level marker is the fourth):
    # the record changes when an email arrives, the file when anything changes.
    for b in blocks[-3:]:
        b["cache_control"] = {"type": "ephemeral"}
    # The record of the last 14 days is by far the largest block and is the same
    # for every question, draft, update and check on a property until new mail
    # arrives. It is kept for an hour rather than five minutes, so a second
    # question ten minutes later reads it instead of paying to load it again.
    for b in blocks:
        if b["text"].startswith(RECORD_MARK) and "cache_control" in b:
            b["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return blocks


#: How the record block begins (see context.Records.record_block).
RECORD_MARK = "=== THE MAIL"


def usage_dict(r) -> Dict[str, int]:
    u = getattr(r, "usage", None)
    return {"input": getattr(u, "input_tokens", 0) or 0, "output": getattr(u, "output_tokens", 0) or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0}


def cost_of(usage: Dict[str, int]) -> float:
    from mangotree.core.usage import cost_usd
    return round(cost_usd(bc.MODEL, input_tokens=usage.get("input", 0), output_tokens=usage.get("output", 0),
                          cache_read=usage.get("cache_read", 0), cache_write=usage.get("cache_write", 0)), 4)


def add_usage(total: Dict[str, int], more: Dict[str, int]) -> Dict[str, int]:
    for k, v in more.items():
        total[k] = total.get(k, 0) + int(v or 0)
    return total


def structured(*, system: Union[str, Sequence[dict]], user: Union[str, List[dict]], schema: Dict[str, Any],
               effort: str, max_tokens: int = 24_000, label: str = "call", tools: Optional[Sequence[dict]] = None) -> Dict[str, Any]:
    """One call, one object. Returns ``{"data": {...}, "usage": {...}}``.

    ``tools`` is passed only so the request begins with the same bytes as the
    writer's request: the cache is keyed on tools, then system, then messages, and
    a check that omitted the tools would pay to load the whole record again. The
    tools are switched off for this call (``tool_choice: none``)."""
    sys_blocks = system_blocks(system) if isinstance(system, str) else list(system)
    params = dict(model=bc.MODEL, max_tokens=max_tokens, system=sys_blocks,
                  messages=[{"role": "user", "content": user}],
                  thinking={"type": "adaptive"},
                  output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    if tools:
        params["tools"] = list(tools)
        params["tool_choice"] = {"type": "none"}
    last: Optional[Exception] = None
    for attempt in (1, 2):
        with client().messages.stream(**params) as stream:
            r = stream.get_final_message()
        METER.record_anthropic(bc.MODEL, r)
        if r.stop_reason == "refusal":
            cat = getattr(getattr(r, "stop_details", None), "category", None)
            raise BrainRefusal(f"{label}: the model declined this request (category: {cat})")
        if r.stop_reason == "max_tokens":
            # Thinking counts against the cap; give it room once before giving up.
            last = BrainError(f"{label}: reply cut off at max_tokens={params['max_tokens']}")
            params["max_tokens"] = min(64_000, params["max_tokens"] * 2)
            logger.warning("%s: truncated; retrying with max_tokens=%s", label, params["max_tokens"])
            continue
        text = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
        try:
            return {"data": json.loads(text), "usage": usage_dict(r)}
        except json.JSONDecodeError as exc:
            last = BrainError(f"{label}: reply was not valid JSON ({exc})")
            logger.warning("%s: invalid JSON on attempt %d: %r", label, attempt, text[:200])
    raise last or BrainError(f"{label}: no reply")
