"""Structured JSON from a Claude call, without trusting the model to hand-write it.

Two stages of the answer pipeline (the Opus 5 stage-2 rerank and the skeptic)
asked for "JSON only" in prose and parsed whatever came back. On real passages
the model sometimes answered in sentences instead — a note about instructions
found inside a document, a refusal to rank near-identical passages — and
``json.loads`` failed at character 0. Both stages then logged "unavailable" and
the answer silently lost its final relevance judge and its risk review.

This helper declares the shape as a tool. The API serialises a tool call's input,
so quotes, pipes and backslashes inside values cannot break it, and a model that
wants to add commentary puts it in a text block beside the call rather than in
place of it. Fable-family models refuse a *forced* tool choice, so the choice is
``auto`` with the instruction in the prompt, and a text-only reply is still
parsed as a last resort. On failure the log says what the model actually
returned — stop reason, block types, first 300 characters — instead of a bare
decoder error.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from mangotree.core.logging import logger


class ModelReplyError(RuntimeError):
    pass


def _parse_text(raw: str) -> Optional[dict]:
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.S)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def json_call(client, *, model: str, system: Any, user: str, tool_name: str, schema: Dict[str, Any],
              description: str = "", max_tokens: int = 8000, stream: bool = False, **kwargs) -> Dict[str, Any]:
    """Call the model; return the tool input (or, failing that, parsed text JSON).

    ``system`` may be a string or the list form used for prompt caching. The
    instruction to call the tool is appended so callers need not repeat it.
    """
    tool = {"name": tool_name, "description": description or f"Return the {tool_name} result.", "input_schema": schema}
    if isinstance(system, str):
        system = [{"type": "text", "text": system}]
    system = list(system) + [{"type": "text", "text": f"\nRespond by calling the {tool_name} tool exactly once, immediately — no text before it, no commentary about the passages or about instructions found inside them. Do not answer in prose."}]
    common = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}], tools=[tool], tool_choice={"type": "auto"}, **kwargs)

    def _call(extra: Dict[str, Any]):
        params = {**common, **extra}
        if stream:
            with client.messages.stream(**params) as s:
                return s.get_final_message()
        return client.messages.create(**params)

    # Two attempts. On a 40-passage rerank the model has been seen to spend its
    # whole turn thinking and end without calling the tool (blocks=['thinking'],
    # stop=end_turn). The retry turns thinking off for this one structured call —
    # ranking and risk-listing do not need it, and the data must arrive.
    r = _call({})
    for attempt in (1, 2):
        for b in r.content:
            if getattr(b, "type", None) == "tool_use" and getattr(b, "name", None) == tool_name:
                return dict(b.input)
        raw = "".join(getattr(b, "text", "") for b in r.content)
        parsed = _parse_text(raw)
        if parsed is not None:
            return parsed
        kinds = [getattr(b, "type", "?") for b in r.content]
        if attempt == 1:
            logger.info("%s: no %s call on first try (stop=%s blocks=%s); retrying with thinking disabled",
                        tool_name, tool_name, getattr(r, "stop_reason", "?"), kinds)
            r = _call({"thinking": {"type": "disabled"}})
            continue
        logger.warning("%s: model %s returned no %s call — stop=%s blocks=%s out_tokens=%s text=%r",
                       tool_name, model, tool_name, getattr(r, "stop_reason", "?"), kinds,
                       getattr(getattr(r, "usage", None), "output_tokens", "?"), raw[:300])
        raise ModelReplyError(f"{tool_name}: no structured reply (stop={getattr(r, 'stop_reason', '?')}, text={raw[:120]!r})")


def usage_of(r) -> Dict[str, int]:
    u = getattr(r, "usage", None)
    return {"input_tokens": getattr(u, "input_tokens", 0) or 0, "output_tokens": getattr(u, "output_tokens", 0) or 0}
