"""
v3 Agent — main controller loop.

Top-level flow
==============

  1. SEED        — Run one initial v2 retrieve(query) so the planner has
                   a starting set of chunks. (Skipped if `seed=False`.)
  2. LOOP        — Repeatedly:
                     a) ask Opus to pick one tool with tool-use
                     b) execute the tool through ToolBox
                     c) record the step into the scratchpad
                     d) feed the result back to Opus as a tool_result
                   until Opus calls `submit_final_answer` or budget runs out
  3. VERIFY      — Run the Sprint-3 verifier on the agent's facts[].
                   If anything fails, attempt ONE re-extraction pass.
  4. FINALIZE    — Return AgentResult (answer, facts, verdicts, trace).

Output shape is byte-compatible with Sprint 3's `VerifiedAnswer`, so
chat.py and websocket_chat.py just see the same fields they already
render (plus an `agent_trace` for the reasoning panel).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.rag.retriever import RetrievedChunk
from src.rag.v2.orchestrator import V2Pipeline
from src.rag.v2.verifier import (
    verify_facts,
    VerificationReport,
    VERDICT_VERIFIED,
    DEFAULT_FUZZY_THRESHOLD,
)
from src.rag.v2.answer_pipeline import (
    OUTCOME_VERIFIED_FIRST_PASS,
    OUTCOME_VERIFIED_AFTER_RETRY,
    OUTCOME_KEPT_ORIGINAL,
    OUTCOME_NO_FACTS,
    OUTCOME_FALLBACK,
    apply_retry_merge,
    _call_reextract,
    _reason_for_fact,
)
from src.rag.v3.scratchpad import (
    AgentScratchpad,
    AgentStep,
    BudgetTracker,
    STEP_TYPE_TOOL,
    STEP_TYPE_FINAL,
    STEP_TYPE_FORCED,
    STEP_TYPE_INTERRUPT,
)
from src.rag.v3.tools import ToolBox, ToolResult, build_tool_specs
from src.rag.v3.prompts import build_agent_system_prompt, build_retry_system_prompt
from src.rag.query_decomp import sufficiency_prompt
from src.utils.logger import logger

# Completeness gate injected once before the first final answer is accepted.
_SUFFICIENCY_GATE = (
    "HOLD — completeness self-check before I accept this as final (recall is "
    "sacred in this matter).\n\n" + sufficiency_prompt() + "\n\n"
    "If you have ALREADY satisfied every point above with cited evidence, call "
    "submit_final_answer again now and it will be accepted immediately. If ANY "
    "gap exists (an unchecked linked source, an unresolved entity/amount/date, "
    "an unanswered sub-question, or a recorded fact not yet cited), use your "
    "retrieval tools to close it FIRST, then submit."
)


# =====================================================================
# Result dataclass
# =====================================================================

@dataclass
class AgentResult:
    """Final output of an agent run. Same shape as Sprint 3 + agent_trace."""

    answer: str = ""
    facts: List[Dict[str, Any]] = field(default_factory=list)
    fact_verdicts: List[Dict[str, Any]] = field(default_factory=list)
    chunks: List[RetrievedChunk] = field(default_factory=list)
    outcome: str = OUTCOME_FALLBACK

    # Verification trail (carries first + second pass for audit)
    first_pass: Optional[Any] = None   # VerificationReport
    second_pass: Optional[Any] = None  # VerificationReport (if retry ran)
    retries: int = 0                   # 0 or 1 — single retry policy
    raw_reextract: Optional[Any] = None  # ReextractionResult for audit

    # Per-step audit trail (for `agent_trace` Mongo + frontend panel)
    agent_trace: Dict[str, Any] = field(default_factory=dict)

    # Cost / latency accounting
    elapsed_ms: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def n_facts(self) -> int:
        return len(self.facts)

    @property
    def n_verified(self) -> int:
        return sum(1 for v in self.fact_verdicts if v.get("verdict") == VERDICT_VERIFIED)


# =====================================================================
# Public runner
# =====================================================================

class AgentRunner:
    """
    One-shot runner. Build once per process (it's stateless across
    runs) and call `.run(query)` for each user question.
    """

    def __init__(
        self,
        *,
        anthropic_client: Any,
        v2_pipeline: V2Pipeline,
        retriever: Any,
        model: str = "claude-opus-4-6",
        max_tokens_per_call: int = 16384,
        fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
        enforce_sufficiency: bool = True,
        effort: Optional[str] = None,
    ) -> None:
        self.client = anthropic_client
        self.v2 = v2_pipeline
        self.retriever = retriever
        self.model = model
        self.max_tokens_per_call = max_tokens_per_call
        self.fuzzy_threshold = fuzzy_threshold
        # Adaptive-thinking effort level ("low"|"medium"|"high"|...). Passed
        # to the API via extra_body when set; on models/SDKs that reject the
        # parameter we transparently retry without it (see _stream_planner).
        self.effort = (effort or "").strip() or None
        # Set to False after the first API rejection so we don't pay a
        # failed-call round-trip on every planner iteration.
        self._effort_supported = True
        # When True, the FIRST submit_final_answer is intercepted once with a
        # completeness self-check (recall guard) before being accepted. Bounded
        # to a single reflection pass so it can't loop or balloon cost.
        self.enforce_sufficiency = enforce_sufficiency

    # ----------------------------------------------------------------
    # The main loop
    # ----------------------------------------------------------------

    def run(
        self,
        query: str,
        *,
        budget: Optional[BudgetTracker] = None,
        seed_with_initial_search: bool = True,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        prior_messages: Optional[List[Dict[str, Any]]] = None,
        base_filter: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        """Run the agent loop and return a verified answer.

        `prior_messages` carries the conversation history (verbatim recent
        turns + optional rolling summary) so follow-up questions keep context.
        Same alternating user/assistant shape the verified path uses; it is
        prepended before the seed user turn.
        """
        t0 = time.time()
        budget = budget or BudgetTracker()
        pad = AgentScratchpad(query=query, budget=budget, on_event=on_event)
        result = AgentResult()

        # Tool wiring. base_filter (e.g. Clean-mode privilege exclusion) is
        # enforced on EVERY tool retrieval so the agent can't pull privileged
        # chunks into a shareable answer.
        toolbox = ToolBox(v2_pipeline=self.v2, retriever=self.retriever,
                          base_filter=base_filter)
        toolbox.attach_scratchpad(pad)
        tool_specs = build_tool_specs(toolbox)
        tool_descriptions = [
            {
                "name": ts.name,
                "description": ts.description,
                "input_schema": ts.input_schema,
            }
            for ts in tool_specs.values()
        ]
        # ── Prompt caching ──────────────────────────────────────────────
        # The tool definitions are the same on every Opus call in this
        # query's loop. Marking the LAST tool as a cache breakpoint tells
        # Anthropic to cache every tool spec up to and including this one.
        # Subsequent calls in the same conversation read the cache at
        # ~0.1× the input price AND ~10× faster (the bottleneck is
        # tokenisation, not network). This is the single biggest latency
        # win for the agent loop — without it, a 150K-token context is
        # re-shipped every round-trip and Opus takes 4–5 min per step.
        if tool_descriptions:
            tool_descriptions[-1] = {
                **tool_descriptions[-1],
                "cache_control": {"type": "ephemeral"},
            }

        # Stream a high-level plan event so the UI knows the agent started
        pad.emit("agent_plan", {
            "query": query,
            "budget": budget.to_dict(),
            "tools": [ts.name for ts in tool_specs.values()],
        })

        # ----- step 0: seed with one initial v2 retrieve --------------
        if seed_with_initial_search:
            try:
                seed = self.retriever.retrieve(query, atlas_filter=(base_filter or None))
                pad.add_chunks(seed)
                # Record a synthetic step so the UI shows the seed
                seed_step = AgentStep(
                    step_num=pad.next_step_num(),
                    type=STEP_TYPE_TOOL,
                    tool_name="(seed) search",
                    tool_input={"query": query},
                    tool_result_summary=f"Initial v2 retrieve returned {len(seed)} chunks",
                    new_chunk_indices=list(range(1, len(seed) + 1)),
                    elapsed_ms=int((time.time() - t0) * 1000),
                )
                pad.record_step(seed_step)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"agent seed retrieval failed (non-fatal): {exc}")

        # ----- conversation state for the planner ---------------------
        system_prompt_text = build_agent_system_prompt(
            today=datetime.now(timezone.utc),
            max_calls=budget.max_tool_calls,
        )
        # The system prompt never changes during the loop. Send it as a
        # cacheable block so Anthropic reuses it across every Opus call.
        system_prompt: List[Dict[str, Any]] = [
            {
                "type": "text",
                "text": system_prompt_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        # The conversation is a synthetic dialogue between the agent
        # (assistant turns containing tool_use blocks) and us (user
        # turns containing tool_result blocks). We seed it with the
        # initial user question + a textual summary of the seed chunks.
        # The seed summary is the HEAVIEST part of the prompt (80 chunks
        # × ~500-char snippets) and never changes during the loop —
        # mark it as a cache breakpoint so the bulk of the input tokens
        # only get processed once and are reused on subsequent turns.
        seed_summary = self._render_seed_chunks(pad)
        seed_text = (
            f"USER QUESTION:\n{query}\n\n"
            f"INITIAL SEED ({pad.n_chunks} chunks already in your "
            f"scratchpad, indexed [#1]..[#{pad.n_chunks}]):\n"
            f"{seed_summary}\n\n"
            "Plan your next step. Call a tool, or call "
            "submit_final_answer when ready."
        )
        # Prepend conversation history (if any) so follow-up turns keep
        # context. Mirrors the verified path's `prior_messages + [user turn]`
        # shape; _build_prior_messages yields alternating turns ending on an
        # assistant message, so the seed user turn that follows is valid.
        messages: List[Dict[str, Any]] = list(prior_messages or [])
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": seed_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        )

        # ----- main loop ---------------------------------------------
        terminal_payload: Optional[Dict[str, Any]] = None
        forced_reason: Optional[str] = None
        reflected = False  # sufficiency self-check fires at most once
        no_tool_streak = 0  # consecutive text-only (reasoning) turns

        while True:
            # Pre-step budget check. When exhausted, we DON'T just fall
            # back to a templated stub — we give Opus one last LLM call
            # forcing it to call submit_final_answer with what it has.
            exhausted = budget.exhausted()
            if exhausted:
                forced_reason = exhausted
                logger.info(f"agent: budget exhausted ({exhausted}) — forcing finalize")
                terminal_payload = self._force_finalize_via_llm(
                    pad=pad,
                    messages=messages,
                    system_prompt=system_prompt,
                    tool_descriptions=tool_descriptions,
                    tool_specs=tool_specs,
                    reason=exhausted,
                )
                break

            # Ask the planner to think and pick a tool. tool_choice=auto so
            # the model may interleave visible reasoning (and, on adaptive-
            # thinking models, native thinking blocks) between tool calls —
            # extended thinking is API-incompatible with forced tool_choice.
            try:
                response = self._planner_call(
                    model=self.model,
                    max_tokens=self.max_tokens_per_call,
                    system=system_prompt,
                    tools=tool_descriptions,
                    tool_choice={"type": "auto"},
                    messages=messages,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"agent planner call failed: {exc}")
                forced_reason = f"planner_error: {exc}"
                terminal_payload = self._force_finalize_stub(pad, reason=forced_reason)
                break

            # Token accounting
            usage = getattr(response, "usage", None)
            if usage is not None:
                budget.record(
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    was_tool_call=False,  # accounted below per actual tool execution
                )

            # Parse the tool_use block(s). Anthropic permits multiple
            # tool_use blocks in one assistant turn; we execute them in
            # order to satisfy the API requirement that every tool_use
            # gets a matching tool_result before the next user turn.
            tool_uses: List[Any] = []
            assistant_blocks: List[Dict[str, Any]] = []
            for block in (response.content or []):
                btype = getattr(block, "type", None)
                if btype == "tool_use":
                    tool_uses.append(block)
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif btype == "text":
                    text = getattr(block, "text", "") or ""
                    assistant_blocks.append({"type": "text", "text": text})
                elif btype in ("thinking", "redacted_thinking"):
                    # Adaptive/extended-thinking models require thinking
                    # blocks to be preserved verbatim in the replayed
                    # assistant turns of a tool-use conversation.
                    try:
                        assistant_blocks.append(block.model_dump(exclude_none=True))
                    except Exception:  # noqa: BLE001
                        pass

            if not tool_uses:
                # With tool_choice=auto the model may spend a turn on pure
                # reasoning (text/thinking, no tool call). That's healthy
                # investigative behaviour — keep the reasoning in context
                # and nudge it to act. Only a stuck streak forces finalize.
                no_tool_streak += 1
                if no_tool_streak >= 3:
                    logger.warning(
                        "agent planner produced 3 consecutive turns without "
                        "a tool call; forcing finalize"
                    )
                    forced_reason = "no_tool_use_returned"
                    terminal_payload = self._force_finalize_via_llm(
                        pad=pad,
                        messages=messages,
                        system_prompt=system_prompt,
                        tool_descriptions=tool_descriptions,
                        tool_specs=tool_specs,
                        reason=forced_reason,
                    )
                    break
                if assistant_blocks:
                    messages.append({"role": "assistant", "content": assistant_blocks})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Understood. Continue the investigation: call the "
                            "next tool now, or call submit_final_answer if "
                            "your analysis is complete."
                        ),
                    })
                # (empty response content: leave messages unchanged and
                # retry — the streak counter bounds this at 3 attempts)
                continue
            no_tool_streak = 0

            # Append the assistant turn (must come BEFORE any tool_result)
            messages.append({"role": "assistant", "content": assistant_blocks})

            # Execute each tool_use in order
            tool_results_for_user_turn: List[Dict[str, Any]] = []
            saw_terminal = False
            for tu in tool_uses:
                step_t = time.time()
                name = tu.name
                inp = tu.input or {}
                spec = tool_specs.get(name)
                if not spec:
                    msg = f"unknown tool: {name}"
                    logger.warning(msg)
                    tool_results_for_user_turn.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": msg,
                        "is_error": True,
                    })
                    step = AgentStep(
                        step_num=pad.next_step_num(),
                        type=STEP_TYPE_TOOL,
                        tool_name=name,
                        tool_input=inp,
                        tool_result_summary=msg,
                        error=msg,
                        elapsed_ms=int((time.time() - step_t) * 1000),
                    )
                    pad.record_step(step)
                    budget.record(was_tool_call=True)
                    continue

                try:
                    result_obj: ToolResult = spec.fn(**inp)
                except TypeError as exc:
                    err = f"bad tool args for {name}: {exc}"
                    logger.warning(err)
                    tool_results_for_user_turn.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": err,
                        "is_error": True,
                    })
                    step = AgentStep(
                        step_num=pad.next_step_num(),
                        type=STEP_TYPE_TOOL,
                        tool_name=name,
                        tool_input=inp,
                        tool_result_summary=err,
                        error=err,
                        elapsed_ms=int((time.time() - step_t) * 1000),
                    )
                    pad.record_step(step)
                    budget.record(was_tool_call=True)
                    continue
                except Exception as exc:  # noqa: BLE001
                    err = f"{name} crashed: {exc}"
                    logger.exception(err)
                    tool_results_for_user_turn.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": err,
                        "is_error": True,
                    })
                    step = AgentStep(
                        step_num=pad.next_step_num(),
                        type=STEP_TYPE_TOOL,
                        tool_name=name,
                        tool_input=inp,
                        tool_result_summary=err,
                        error=err,
                        elapsed_ms=int((time.time() - step_t) * 1000),
                    )
                    pad.record_step(step)
                    budget.record(was_tool_call=True)
                    continue

                # Terminal tool — but first, enforce ONE completeness
                # self-check (recall guard). On the first submit we bounce a
                # sufficiency prompt back to the planner instead of accepting;
                # it must re-confirm (or retrieve more, then re-submit). This
                # closes the "answered too early / missed a linked source" gap.
                if result_obj.is_terminal and self.enforce_sufficiency \
                        and not reflected and not budget.exhausted():
                    reflected = True
                    pad.emit("agent_sufficiency_check",
                             {"note": "completeness self-check before finalize"})
                    tool_results_for_user_turn.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": _SUFFICIENCY_GATE,
                    })
                    step = AgentStep(
                        step_num=pad.next_step_num(),
                        type=STEP_TYPE_TOOL,
                        tool_name=name,
                        tool_input={},
                        tool_result_summary="sufficiency self-check requested "
                                            "before accepting final answer",
                        elapsed_ms=int((time.time() - step_t) * 1000),
                    )
                    pad.record_step(step)
                    budget.record(was_tool_call=True)
                    continue  # do NOT set saw_terminal — loop continues once

                # Terminal tool — stash the payload, stop after this iter.
                if result_obj.is_terminal:
                    saw_terminal = True
                    terminal_payload = result_obj.payload
                    tool_results_for_user_turn.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": "(received submit_final_answer; finalising)",
                    })
                    step = AgentStep(
                        step_num=pad.next_step_num(),
                        type=STEP_TYPE_FINAL,
                        tool_name=name,
                        tool_input={"n_facts": len(terminal_payload.get("facts", []))},
                        tool_result_summary=result_obj.summary,
                        elapsed_ms=int((time.time() - step_t) * 1000),
                    )
                    pad.record_step(step)
                    budget.record(was_tool_call=True)
                    continue

                # Non-terminal — build a compact tool_result for the next
                # planner turn (we serialise the payload to a JSON string).
                content_for_planner = self._render_tool_result(name, result_obj)
                tool_results_for_user_turn.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": content_for_planner,
                })
                step = AgentStep(
                    step_num=pad.next_step_num(),
                    type=STEP_TYPE_TOOL,
                    tool_name=name,
                    tool_input=inp,
                    tool_result_summary=result_obj.summary,
                    new_chunk_indices=[
                        i for i in result_obj.payload.get("new_chunk_indices", [])
                    ] if isinstance(result_obj.payload, dict) else [],
                    elapsed_ms=int((time.time() - step_t) * 1000),
                )
                pad.record_step(step)
                budget.record(was_tool_call=True)

            if saw_terminal:
                break

            # Continue the conversation with tool_result back to the planner
            messages.append({
                "role": "user",
                "content": tool_results_for_user_turn,
            })

        # ===== finalize + verify =====================================
        return self._finalize(
            pad=pad,
            terminal_payload=terminal_payload or {},
            forced_reason=forced_reason,
            result=result,
            started_at=t0,
        )

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _planner_call(self, **kwargs: Any) -> Any:
        """
        One planner LLM call. Uses the STREAMING API so `max_tokens` can
        exceed Anthropic's ~21k non-streaming ceiling, and passes the
        adaptive-thinking effort level via `output_config` when set.
        If the model/SDK rejects the parameter we retry once without it
        and remember the rejection for the rest of the run.
        """
        if self.effort and self._effort_supported:
            try:
                with self.client.messages.stream(
                    **kwargs, output_config={"effort": self.effort}
                ) as stream:
                    return stream.get_final_message()
            except TypeError:
                # SDK too old for the output_config parameter.
                self._effort_supported = False
                logger.warning("output_config unsupported by SDK — effort disabled")
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "effort" in msg or "output_config" in msg or "thinking" in msg:
                    self._effort_supported = False
                    logger.warning(
                        f"effort={self.effort} rejected by API — proceeding without it"
                    )
                else:
                    raise
        with self.client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    # Per-chunk and total character budgets for the seed evidence block.
    # ~4,500 chars covers essentially the whole of a 1000-token chunk, so
    # the planner reads FULL evidence — not previews. The total cap keeps
    # a worst-case seed (170 chunks post-expansion) inside ~200K tokens
    # of the model's 1M context. The block is prompt-cached, so the cost
    # is paid once per query, then read at ~0.1x on every loop iteration.
    SEED_CHUNK_CHAR_CAP = 4_500
    SEED_TOTAL_CHAR_CAP = 800_000

    def _render_seed_chunks(self, pad: AgentScratchpad) -> str:
        """
        Render ALL seed chunks with (effectively) FULL bodies so the
        planner reasons over the complete evidence pack upfront — the
        depth of the final analysis is bounded by what the planner can
        actually read. A total budget guard keeps pathological seeds
        bounded; if it trips, later chunks degrade to 600-char briefs
        (the planner can still fetch_full_document them on demand).
        """
        import re as _re

        if pad.n_chunks == 0:
            return "(no seed chunks)"

        lines: List[str] = []
        spent = 0
        for i, c in enumerate(pad.all_chunks):
            idx = i + 1
            if c.source_type == "attachment":
                title = c.filename or "(unknown file)"
            else:
                title = c.subject or "(no subject)"
            date = ""
            if c.date and hasattr(c.date, "strftime"):
                try:
                    date = c.date.strftime("%Y-%m-%d")
                except Exception:
                    pass
            meta = []
            if getattr(c, "from_email", None):
                meta.append(f"from {c.from_email}")
            if getattr(c, "doc_source_type", None):
                meta.append(str(c.doc_source_type))
            meta_s = f" · {' · '.join(meta)}" if meta else ""
            body = c.body or c.text or ""
            body = _re.sub(r"\s+", " ", body).strip()
            cap = self.SEED_CHUNK_CHAR_CAP if spent < self.SEED_TOTAL_CHAR_CAP else 600
            body = body[:cap]
            spent += len(body)
            lines.append(f"[#{idx}] {date} · {title}{meta_s}\n     {body}")
        return "\n".join(lines)

    def _render_tool_result(self, tool_name: str, result: ToolResult) -> str:
        """
        Produce a textual tool_result content for the planner. Generous
        cap (60K chars ≈ 15K tokens) so full-document fetches and rich
        search results reach the planner intact instead of as stubs.
        """
        import json
        # Strip heavy fields before serialising.
        payload = dict(result.payload or {})
        # Already-chunk-brief structures are fine.
        payload["_summary"] = result.summary
        if result.error:
            payload["_error"] = result.error
        try:
            return json.dumps(payload, indent=2, default=str)[:60000]
        except Exception:
            return result.summary

    def _force_finalize_via_llm(
        self,
        *,
        pad: AgentScratchpad,
        messages: List[Dict[str, Any]],
        system_prompt: Any,  # list[dict] of cached blocks (or legacy str)
        tool_descriptions: List[Dict[str, Any]],
        tool_specs: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        """
        Budget hit. Ask Opus ONE more time, restricted to the
        `submit_final_answer` tool, so we get a real synthesised
        answer from the evidence the agent already gathered (instead
        of a templated "couldn't finish" stub).

        This call doesn't count against the agent's tool_call budget
        (it's a closing-out step) but DOES count against tokens.
        """
        pad.emit("agent_forced_finalize", {"reason": reason, "phase": "summarising"})

        # User-turn message that overrides the conversation's prior
        # planning incentives and forces a submit. We EXPLICITLY require
        # the prose `answer` to be non-empty — otherwise Opus has been
        # observed (with large fact lists) burning all its output tokens
        # on the facts JSON and emitting an empty answer string.
        force_msg = (
            f"BUDGET EXHAUSTED ({reason}). Stop searching and submit your "
            f"BEST answer NOW using ONLY the {pad.n_chunks} chunks already "
            f"in your scratchpad ([#1]..[#{pad.n_chunks}]).\n\n"
            f"CRITICAL — submit_final_answer requires BOTH fields:\n"
            f"  1. `facts`: a structured array of every claim you can "
            f"support with a verbatim quote.\n"
            f"  2. `answer`: a SYNTHESISED prose answer that fully "
            f"addresses the user's actual question, citing facts via "
            f"[#N]. Do NOT artificially shorten it — a complex forensic "
            f"question deserves a complete memo (often 800-2,500 words). "
            f"THIS MUST BE A NON-EMPTY STRING. Do NOT leave it blank — "
            f"the user reads this prose, not the JSON.\n\n"
            f"If the evidence is incomplete, say so honestly in the prose "
            f"and flag the gaps. Call submit_final_answer NOW."
        )
        force_messages = messages + [{"role": "user", "content": force_msg}]

        # Output budget for the forced-finalize call: 64K so a large
        # facts[] array AND a full forensic memo both fit. Streaming is
        # mandatory at this size (Anthropic requires it above ~21k) and
        # note: extended/adaptive thinking is API-incompatible with a
        # forced tool_choice, so no effort/thinking param on this call.
        force_max_tokens = 64000

        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=force_max_tokens,
                system=system_prompt,
                tools=tool_descriptions,
                tool_choice={"type": "tool", "name": "submit_final_answer"},
                messages=force_messages,
            ) as stream:
                # Drain the stream — get_final_message blocks until the
                # full response is assembled (including tool_use blocks).
                response = stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"force-finalize LLM call failed: {exc}")
            return self._force_finalize_stub(pad, reason=reason)

        # Pull the submit_final_answer block.
        for block in (response.content or []):
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_final_answer":
                inp = dict(block.input or {})
                logger.info(
                    f"agent forced finalize: submitted {len(inp.get('facts', []))} facts"
                )
                return inp

        logger.warning("force-finalize: planner didn't emit submit_final_answer; using stub")
        return self._force_finalize_stub(pad, reason=reason)

    def _force_finalize_stub(
        self,
        pad: AgentScratchpad,
        *,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Build a degraded `submit_final_answer`-shaped payload when the
        budget is exhausted before the agent submitted on its own.

        We DON'T fabricate facts. We return a transparent message
        acknowledging the limit and listing what chunks were discovered.
        """
        pad.emit("agent_forced_finalize", {"reason": reason})

        seed_lines = []
        for i, c in enumerate(pad.all_chunks[:8]):
            idx = i + 1
            title = c.filename if c.source_type == "attachment" else (c.subject or "(no subject)")
            seed_lines.append(f"- [#{idx}] {title}")
        seed_block = "\n".join(seed_lines) if seed_lines else "  (no chunks were retrieved)"

        if "interrupt" in (reason or ""):
            preamble = "The investigation was stopped by the user before completion."
        else:
            preamble = (
                f"The investigation budget was reached ({reason}) before I "
                f"could compile a fully verified answer."
            )

        return {
            "facts": [],
            "answer": (
                f"{preamble}\n\n"
                f"During the investigation I examined the following sources:\n\n"
                f"{seed_block}\n\n"
                "Because no verified facts were synthesised, no claims are "
                "shown. Re-ask the question with more specificity, or raise "
                "the per-query budget for deeper investigation."
            ),
            "reasoning_summary": f"Forced finalize: {reason}",
        }

    def _finalize(
        self,
        *,
        pad: AgentScratchpad,
        terminal_payload: Dict[str, Any],
        forced_reason: Optional[str],
        result: AgentResult,
        started_at: float,
    ) -> AgentResult:
        facts: List[Dict[str, Any]] = list(terminal_payload.get("facts") or [])
        answer: str = str(terminal_payload.get("answer") or "")
        chunks = pad.all_chunks

        if not facts:
            # Either no-facts answer, or forced finalize, or pure scoping
            result.outcome = (
                OUTCOME_FALLBACK if forced_reason else OUTCOME_NO_FACTS
            )
            result.answer = answer
            result.facts = facts
        else:
            # ----- First verifier pass --------------------------------
            first_report = verify_facts(
                facts, chunks, fuzzy_threshold=self.fuzzy_threshold
            )
            result.first_pass = first_report

            if first_report.all_passed:
                # Fast path — every quote verified on the first try.
                result.outcome = OUTCOME_VERIFIED_FIRST_PASS
                result.facts = facts
                result.fact_verdicts = self._verdicts_from_report(
                    facts, first_report, stage="agent_first_pass"
                )
                result.answer = answer
            else:
                # ----- Retry pass: same policy as Sprint 3 ------------
                # Identical contract: ask Opus to re-extract ONLY the
                # failed claims via REEXTRACT_TOOL, then re-verify, then
                # merge. If still failing → KEPT_ORIGINAL (we ship the
                # agent's original answer with amber verdicts).
                self._run_retry_pass(
                    pad=pad,
                    facts=facts,
                    answer=answer,
                    first_report=first_report,
                    result=result,
                )

        # ----- Sprint 8 hardening: Defense-Counsel Critic + entity validation
        hardening_report: Dict[str, Any] = {}
        try:
            import os
            if os.getenv("RAG_V3_DEFENSE_CRITIC", "true").lower() in ("1", "true", "yes"):
                from src.rag.v3.hardening import apply_hardening
                hardening_report = apply_hardening(
                    self.client, self.model, query=getattr(pad, "query", ""),
                    answer=result.answer or "", facts=result.facts or [],
                    mongo=self.retriever.mongo)
                if hardening_report.get("annotated_answer"):
                    result.answer = hardening_report["annotated_answer"]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Sprint-8 hardening skipped: {str(exc)[:120]}")

        # ----- Cross-family critique -> revise loop (flag-gated, default OFF)
        # Fable wrote the answer; GPT-5.5 critiques it for gaps vs the question
        # & case; Fable then writes the FINAL answer addressing those gaps
        # (re-verified). Enable with RAG_CROSS_CRITIC_ENABLED=true.
        try:
            import os
            if (os.getenv("RAG_CROSS_CRITIC_ENABLED", "false").lower() in ("1", "true", "yes")
                    and result.facts and result.answer):
                from src.rag.v3.cross_critic import run_cross_critique
                pad.emit("agent_cross_critique_start", {})
                cc = run_cross_critique(
                    anthropic_client=self.client, model=self.model,
                    question=getattr(pad, "query", ""), answer=result.answer,
                    facts=result.facts, chunks=chunks,
                )
                hardening_report["cross_critique"] = {
                    "revised": cc.get("revised"),
                    "critique": cc.get("critique"),
                }
                if cc.get("revised"):
                    result.answer = cc["answer"]
                    result.facts = cc["facts"]
                    if cc.get("fact_verdicts"):
                        result.fact_verdicts = cc["fact_verdicts"]
                    result.outcome = OUTCOME_VERIFIED_AFTER_RETRY
                pad.emit("agent_cross_critique_done", {"revised": bool(cc.get("revised"))})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"cross-critique skipped: {str(exc)[:120]}")

        # ----- Verification augmentation (entailment / coverage / injection)
        # Flag-gated, DEFAULT OFF — zero behavior change until enabled via
        # RAG_ENTAILMENT_ENABLED / RAG_COVERAGE_ENABLED / RAG_INJECTION_SCAN_ENABLED.
        try:
            from src.rag.v2.verification_augment import augment_answer
            aug = augment_answer(
                answer=result.answer or "", facts=result.facts or [],
                fact_verdicts=result.fact_verdicts or [], chunks=chunks)
            result.answer = aug["answer"]
            if aug.get("report"):
                hardening_report["verification_augment"] = aug["report"]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"verification augmentation skipped: {str(exc)[:120]}")

        # ----- Common bookkeeping -------------------------------------
        result.chunks = chunks
        result.elapsed_ms = int((time.time() - started_at) * 1000)
        result.tool_calls = pad.budget.tool_calls_used
        result.input_tokens = pad.budget.input_tokens_used
        result.output_tokens = pad.budget.output_tokens_used
        result.cache_read_tokens = pad.budget.cache_read_tokens

        # Build the agent_trace blob
        result.agent_trace = pad.to_audit_dict(final_answer={
            "facts": result.facts,
            "answer": result.answer,
            "outcome": result.outcome,
            "reasoning_summary": terminal_payload.get("reasoning_summary"),
            "forced_reason": forced_reason,
            "retries": result.retries,
            "hardening": hardening_report,
        })

        pad.emit("agent_done", {
            "outcome": result.outcome,
            "n_facts": result.n_facts,
            "n_verified": result.n_verified,
            "tool_calls": result.tool_calls,
            "retries": result.retries,
            "elapsed_ms": result.elapsed_ms,
        })

        logger.info(
            f"agent done: outcome={result.outcome} "
            f"facts={result.n_facts} verified={result.n_verified} "
            f"retries={result.retries} "
            f"tools={result.tool_calls} elapsed={result.elapsed_ms}ms"
        )
        return result

    # ----------------------------------------------------------------
    # Retry pass (parity with Sprint 3 `_ask_verified`)
    # ----------------------------------------------------------------

    def _verdicts_from_report(
        self,
        facts: List[Dict[str, Any]],
        report: VerificationReport,
        *,
        stage: str,
    ) -> List[Dict[str, Any]]:
        """Build frontend-friendly verdict objects from a single report."""
        by_id = {i.fact_id: i for i in report.items}
        verdicts: List[Dict[str, Any]] = []
        for fact in facts:
            fid = fact.get("id")
            item = by_id.get(fid)
            if item is None:
                verdicts.append({
                    "fact_id": fid,
                    "verdict": "UNVERIFIED",
                    "stage": stage,
                    "claim": fact.get("claim"),
                    "source_chunk_id": fact.get("source_chunk_id"),
                    "verbatim_quote": fact.get("verbatim_quote"),
                    "score": 0.0,
                    "reason": "no verifier item found",
                })
                continue
            verdicts.append({
                "fact_id": fid,
                "verdict": item.verdict,
                "stage": stage,
                "claim": fact.get("claim"),
                "source_chunk_id": fact.get("source_chunk_id"),
                "verbatim_quote": fact.get("verbatim_quote"),
                "matched_span": item.matched_span,
                "score": item.score,
                "reason": item.reason,
            })
        return verdicts

    def _run_retry_pass(
        self,
        *,
        pad: AgentScratchpad,
        facts: List[Dict[str, Any]],
        answer: str,
        first_report: VerificationReport,
        result: AgentResult,
    ) -> None:
        """
        One re-extraction round when the agent's submit_final_answer
        fails the verifier. Same policy as Sprint 3: Opus gets a single
        chance to either provide a corrected verbatim quote or honestly
        mark the claim NOT_PRESENT. After merge + second verify, if any
        fact still fails we ship the ORIGINAL answer (KEPT_ORIGINAL).

        This routes through `_call_reextract` from Sprint 3 with a
        compact synthetic conversation (we don't re-ship the full agent
        loop history — the reextract prompt re-shows the failed chunks).
        """
        failed_items = first_report.failed
        failed_fact_ids = {f.fact_id for f in failed_items}
        failed_facts = [
            {
                **f,
                "_verifier_reason": _reason_for_fact(first_report, f.get("id")),
            }
            for f in facts
            if f.get("id") in failed_fact_ids
        ]

        if not failed_facts:
            # Shouldn't happen (we only enter here if !all_passed) but
            # guard against it — fall back to first-pass verdicts.
            result.outcome = OUTCOME_KEPT_ORIGINAL
            result.facts = facts
            result.answer = answer
            result.fact_verdicts = self._verdicts_from_report(
                facts, first_report, stage="agent_first_pass"
            )
            return

        logger.info(
            f"agent retry: re-extracting {len(failed_facts)} failed claim(s) "
            f"({first_report.n_passed}/{len(first_report.items)} verified "
            f"first pass)"
        )
        pad.emit("agent_retry_start", {
            "n_failed": len(failed_facts),
            "first_pass_passed": first_report.n_passed,
            "first_pass_total": len(first_report.items),
        })

        # Synthetic prior conversation. We do NOT re-ship the full agent
        # loop — the reextract prompt re-shows the chunk bodies anyway.
        synthetic_prior: List[Dict[str, Any]] = [{
            "role": "user",
            "content": (
                f"USER QUESTION:\n{pad.query}\n\n"
                "(Agent retrieval and reasoning produced the following "
                "structured answer; the verifier rejected one or more "
                "verbatim quotes. Please correct ONLY the failed claims.)"
            ),
        }]
        first_assistant_block = {"facts": facts, "answer": answer}
        retry_system = build_retry_system_prompt()

        reextract = None
        try:
            reextract = _call_reextract(
                client=self.client,
                model=self.model,
                system_prompt=retry_system,
                prior_messages=synthetic_prior,
                first_assistant_block=first_assistant_block,
                failed_facts=failed_facts,
                chunks=pad.all_chunks,
                max_tokens=max(2048, self.max_tokens_per_call // 2),
                prior_tool_name="submit_final_answer",
            )
            result.retries = 1
            result.raw_reextract = reextract
            # Charge the retry to the budget so audit logs are accurate.
            pad.budget.record(
                input_tokens=reextract.input_tokens,
                output_tokens=reextract.output_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"agent retry: reextract call failed ({exc}) — "
                f"keeping original answer"
            )
            result.outcome = OUTCOME_KEPT_ORIGINAL
            result.facts = facts
            result.answer = answer
            result.fact_verdicts = self._verdicts_from_report(
                facts, first_report, stage="agent_first_pass"
            )
            return

        if not reextract or not reextract.by_fact_id:
            logger.warning(
                "agent retry: reextract returned no usable tool call — "
                "keeping original answer"
            )
            result.outcome = OUTCOME_KEPT_ORIGINAL
            result.facts = facts
            result.answer = answer
            result.fact_verdicts = self._verdicts_from_report(
                facts, first_report, stage="agent_first_pass"
            )
            return

        # Deterministic merge + second verification (shared helper).
        merge = apply_retry_merge(
            facts=facts,
            answer=answer,
            first_report=first_report,
            reextract=reextract,
            chunks=pad.all_chunks,
            fuzzy_threshold=self.fuzzy_threshold,
            failed_fact_ids=failed_fact_ids,
        )
        result.facts = merge["final_facts"]
        result.fact_verdicts = merge["final_verdicts"]
        result.second_pass = merge["second_pass"]
        result.answer = merge["final_answer"]
        result.outcome = merge["outcome"]

        n_pass = sum(
            1 for v in result.fact_verdicts if v["verdict"] == VERDICT_VERIFIED
        )
        pad.emit("agent_retry_done", {
            "outcome": result.outcome,
            "n_verified": n_pass,
            "n_total": len(result.fact_verdicts),
            "any_corrected": merge["any_corrected"],
        })
        logger.info(
            f"agent retry done: outcome={result.outcome} "
            f"({n_pass}/{len(result.fact_verdicts)} verified after retry)"
        )


__all__ = ["AgentRunner", "AgentResult"]
