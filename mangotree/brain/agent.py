"""The tool loop shared by chat and draft-writing.

One model, the real conversation as messages, tools called only when the model
decides it needs them, text streamed as it is written.

History is append-only inside a turn: the assistant's content blocks (thinking
included) go back exactly as they came, and nothing earlier is edited — that is
what keeps the prompt cache warm and the model's own reasoning valid from one
tool round to the next. Caching uses the request-level marker, so no message
block is ever rewritten to move a breakpoint.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.core.usage import METER

from . import config as bc
from .llm import add_usage, client, usage_dict

OnText = Callable[[str], None]
OnTool = Callable[[Dict[str, Any]], None]

BUDGET_NOTE = ("You have done the lookups this question allows. Answer now from what you have, and say plainly "
               "what you could not check.")


@dataclass
class LoopResult:
    text: str = ""
    stop_reason: str = ""
    refused: bool = False
    refusal_category: Optional[str] = None
    rounds: int = 0
    usage: Dict[str, int] = field(default_factory=dict)
    tool_log: List[Dict[str, Any]] = field(default_factory=list)
    terminal: Optional[Dict[str, Any]] = None
    truncated: bool = False
    cancelled: bool = False


def _blocks(content) -> List[dict]:
    """Assistant content as request params, unchanged."""
    out = []
    for b in content or []:
        try:
            out.append(b.model_dump(exclude_none=True))
        except Exception:
            out.append(dict(b))
    return out


def run_loop(*, system: Sequence[dict], messages: List[dict], toolkit, effort: str, max_rounds: int,
             max_tokens: int = bc.CHAT_MAX_TOKENS, on_text: Optional[OnText] = None, on_round: Optional[Callable[[], None]] = None,
             on_tool: Optional[OnTool] = None, terminal_tool: Optional[Dict[str, Any]] = None,
             should_stop: Optional[Callable[[], bool]] = None) -> LoopResult:
    """Run until the model answers in text (or calls ``terminal_tool``).

    ``messages`` is extended in place with this turn's assistant/tool messages.
    ``terminal_tool`` is a tool definition; calling it ends the loop and its
    input is returned as ``result.terminal``.
    """
    res = LoopResult()
    tools = list(toolkit.specs()) + ([terminal_tool] if terminal_tool else [])
    reminded = False
    for round_no in range(1, max_rounds + 4):
        if should_stop and should_stop():
            res.cancelled = True
            return res
        res.rounds = round_no
        if on_round:
            on_round()
        params: Dict[str, Any] = dict(model=bc.MODEL, max_tokens=max_tokens, system=list(system), messages=messages,
                                      thinking={"type": "adaptive"}, output_config={"effort": effort},
                                      # Request-level cache marker: the API caches up to the last block
                                      # without any message being edited to carry a breakpoint.
                                      extra_body={"cache_control": {"type": "ephemeral"}})
        if tools:
            params["tools"] = tools
        round_text: List[str] = []
        with client().messages.stream(**params) as stream:
            for event in stream:
                if event.type == "text":
                    round_text.append(event.text)
                    if on_text:
                        on_text(event.text)
            r = stream.get_final_message()
        METER.record_anthropic(bc.MODEL, r)
        add_usage(res.usage, usage_dict(r))
        res.stop_reason = r.stop_reason or ""

        if r.stop_reason == "refusal":
            res.refused = True
            res.refusal_category = getattr(getattr(r, "stop_details", None), "category", None)
            res.text = "".join(round_text)
            return res

        tool_uses = [b for b in r.content if getattr(b, "type", None) == "tool_use"]
        if r.stop_reason == "max_tokens":
            res.truncated = True
            if tool_uses:
                # A half-written tool call must never run.
                raise RuntimeError("the reply was cut off in the middle of a tool call; raise max_tokens")
            res.text = "".join(round_text)
            messages.append({"role": "assistant", "content": _blocks(r.content)})
            return res

        messages.append({"role": "assistant", "content": _blocks(r.content)})
        if not tool_uses:
            if terminal_tool and not reminded:
                # `auto` does not guarantee the call; ask once, plainly.
                reminded = True
                messages.append({"role": "user", "content": f"Finish by calling the {terminal_tool['name']} tool now."})
                continue
            res.text = "".join(round_text).strip()
            return res

        results: List[dict] = []
        for tu in tool_uses:
            t0 = time.time()
            args = tu.input if isinstance(tu.input, dict) else {}
            if terminal_tool and tu.name == terminal_tool["name"]:
                res.terminal = dict(args)
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": "received"})
                continue
            content, is_error, label = toolkit.run(tu.name, args)
            entry = {"name": tu.name, "label": label, "error": bool(is_error), "ms": int((time.time() - t0) * 1000)}
            res.tool_log.append(entry)
            if on_tool:
                on_tool(entry)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": content, **({"is_error": True} if is_error else {})})
        if res.terminal is not None:
            messages.append({"role": "user", "content": results})
            res.text = "".join(round_text).strip()
            return res
        u = getattr(r, "usage", None)
        size = (getattr(u, "input_tokens", 0) or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        if round_no >= max_rounds or size > bc.CONTEXT_TOKEN_LIMIT:
            results.append({"type": "text", "text": BUDGET_NOTE})
        messages.append({"role": "user", "content": results})
        if round_no >= max_rounds + 2:
            logger.warning("brain loop: no final answer after %d rounds", round_no)
            res.text = "".join(round_text).strip()
            return res
    return res
