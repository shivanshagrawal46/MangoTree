"""
Claude Sonnet 4.5 chat layer with structured citations.

Architecture:

  - We treat Claude as a senior legal advisor.
  - For every user question we (a) retrieve top-K chunks via Atlas
    Vector Search + Voyage rerank, and (b) inject them as numbered
    sources into a structured prompt.
  - Claude is instructed to cite sources inline using `[#N]` markers
    referencing the numbered list, and to clearly mark when something is
    from its own legal expertise (not the corpus).
  - We support conversation continuity (multi-turn) by keeping a list of
    {role, content} messages.

The prompt is intentionally explicit about:
  - hallucination guardrails (don't invent facts about the corpus)
  - citation format ([#1], [#2], …)
  - separation of corpus-grounded vs. expertise-grounded statements
  - awareness of OCR fallibility (low-confidence pages are flagged)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import anthropic

from src.rag.retriever import RetrievedChunk, Retriever
from src.utils.logger import logger


# Heuristic detection of timeline-style questions.
_TIMELINE_KEYWORDS = re.compile(
    r"\b(timeline|chronolog(?:y|ical)|chrono|sequence of events|over time|"
    r"from\s+\d{4}\s+to\s+\d{4}|between\s+\d{4}\s+and\s+\d{4}|"
    r"summari[sz]e\s+(?:everything|all)|how\s+did\s+\w+\s+evolve|"
    r"what\s+happened\s+(?:between|from|during))\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def detect_timeline_intent(question: str) -> Tuple[bool, Optional[datetime], Optional[datetime]]:
    """
    Returns (is_timeline_query, date_from, date_to).

    If the user asks "summarize from 2021 to 2024" we extract the years and
    pass them as Atlas date filters.
    """
    if not _TIMELINE_KEYWORDS.search(question):
        return False, None, None
    years = [int(y) for y in _YEAR_RE.findall(question)]
    date_from = date_to = None
    if len(years) >= 2:
        ys, ye = sorted([years[0], years[1]])
        date_from = datetime(ys, 1, 1)
        date_to = datetime(ye, 12, 31, 23, 59, 59)
    elif len(years) == 1:
        y = years[0]
        date_from = datetime(y, 1, 1)
        date_to = datetime(y, 12, 31, 23, 59, 59)
    return True, date_from, date_to


SYSTEM_PROMPT = """You are a senior legal advisor reviewing a body of email correspondence and attached documents that constitute evidence in a fraud investigation. Your role is to help the investigator understand the facts, identify suspicious patterns, locate corroborating documents, and reason about legal implications.

## Operating principles

1. **Two sources of knowledge.** You have:
   - **Corpus knowledge** — the numbered SOURCES block provided with each user question. This is the ground truth for what was said, when, and by whom in this case.
   - **Legal expertise** — your general legal, financial, and investigative knowledge. Use this to interpret the corpus, identify relevant doctrines, suggest follow-up questions, and explain implications.

2. **Cite the corpus inline.** When you state any factual claim drawn from the SOURCES, append a citation marker `[#N]` referring to the numbered source. If a claim draws on multiple sources, cite all of them, e.g. `[#1][#3]`. Do not cite legal-expertise statements; those flow naturally and uncited.

3. **Never invent corpus facts.** If the SOURCES do not contain information needed to answer the question, say so plainly ("The provided emails do not show…") and then offer your professional perspective on what *would* be relevant to look for.

4. **Dates are first-class.** Every SOURCE block carries a date in its header. When reasoning, ALWAYS anchor claims to dates. If the same fact appears at different dates with different values (e.g., a verdict in 2023 reversed in 2024), surface BOTH versions and identify which is most recent / operative. Never collapse a time-evolved fact into a single statement without dates.

5. **Skip unreadable text silently.** Some attachments were extracted via OCR and may contain garbled passages. If a piece of text is clearly unreadable or contains obvious recognition errors, simply ignore it and use only the cleanly extracted text. DO NOT add notes like "OCR impaired", "OCR error", "garbled", or similar caveats in your answer. If you cannot read a figure or fact cleanly, just leave it out entirely — never call attention to the imperfection.

6. **Be direct and structured.** Investigators are time-pressed. Lead with the bottom line, follow with the evidence chain, then add nuance. Use short paragraphs and bullet lists when listing facts.

7. **Flag suspicious patterns proactively.** When you notice red flags (round-number transfers, unusual urgency, off-domain emails, contradictions across emails, signature mismatches, gaps in timeline), call them out even if the user didn't ask.

8. **Maintain attorney-client style discretion.** Treat the corpus as confidential. Don't speculate about parties not in the corpus."""


TIMELINE_INSTRUCTION = """\
The user has asked for a chronological / timeline-style summary. The SOURCES below have been pre-sorted by date ascending. Produce a CHRONOLOGICAL summary:

  - Group events by year (## 2021, ## 2022, …)
  - Within each year, list significant events as bullets, each starting with the date `**YYYY-MM-DD** —` followed by a one-sentence factual summary, followed by the citation `[#N]`
  - At the end, add a short "Key inflection points" subsection calling out events where positions changed, decisions were reversed, or major payments/transfers happened
  - If the corpus has gaps (long silent periods, missing months), call them out explicitly
"""


def _format_chunk_for_prompt(idx: int, chunk: RetrievedChunk) -> str:
    """Render a single retrieved chunk as a numbered SOURCE block (plain).

    Option B: when an attachment was sent in multiple emails, surface the
    fan-out compactly so Claude can attribute the same fact to multiple
    senders / dates. We cap the visible fan-out at 3 entries (with a
    summary line for the rest) to keep the prompt bounded.
    """
    head_parts: List[str] = [f"#{idx}"]
    if chunk.source_type == "email_body":
        head_parts.append("Email")
    elif chunk.source_type == "attachment":
        head_parts.append(f"Attachment: {chunk.filename or 'unknown'}")
        if chunk.page_start is not None:
            if chunk.page_end and chunk.page_end != chunk.page_start:
                head_parts.append(f"pp. {chunk.page_start}-{chunk.page_end}")
            else:
                head_parts.append(f"p. {chunk.page_start}")
    if chunk.date is not None:
        try:
            head_parts.append(chunk.date.strftime("%Y-%m-%d %H:%M"))
        except AttributeError:
            head_parts.append(str(chunk.date))
    if chunk.from_email:
        head_parts.append(f"from {chunk.from_email}")
    if chunk.to_emails:
        head_parts.append(f"to {', '.join(chunk.to_emails[:3])}")
    if chunk.subject:
        head_parts.append(f"subject: {chunk.subject}")

    header = " | ".join(head_parts)
    fan_out = _format_occurrences_fanout(chunk)
    body = chunk.body or chunk.text
    if fan_out:
        return f"[{header}]\n{fan_out}\n{body}"
    return f"[{header}]\n{body}"


def _format_occurrences_fanout(chunk: RetrievedChunk) -> str:
    """Return a one-line summary of additional occurrences for this content.

    The PRIMARY occurrence is already rendered in the chunk header (date,
    from, subject). This method renders the OTHER occurrences — i.e. all
    additional parent emails that carried this same byte-identical file —
    so Claude knows the document was also sent on dates X / Y / Z by
    different senders.

    Empty string if the chunk has 0 or 1 occurrences (nothing to add).
    """
    occs = chunk.occurrences or []
    if len(occs) <= 1:
        return ""

    # The primary (earliest) is already in the header → render the rest.
    extra = occs[1:]
    visible = extra[:3]
    parts: List[str] = []
    for o in visible:
        bits: List[str] = []
        d = o.get("date")
        if d is not None:
            try:
                bits.append(d.strftime("%Y-%m-%d"))
            except AttributeError:
                bits.append(str(d))
        if o.get("from_email"):
            bits.append(f"from {o['from_email']}")
        subj = (o.get("subject") or "").strip()
        if subj:
            bits.append(f"subject: {subj[:80]}")
        if bits:
            parts.append("(" + " | ".join(bits) + ")")
    extra_more = len(extra) - len(visible)
    summary = "Also sent in: " + "; ".join(parts) if parts else ""
    if extra_more > 0:
        if summary:
            summary += f"; +{extra_more} more email(s)"
        else:
            summary = f"Also sent in {extra_more} other email(s)"
    return summary


def _xml_escape(s: str) -> str:
    """Minimal XML attribute escape — Claude tolerates ampersand-only escape."""
    if not s:
        return ""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _format_chunk_xml(idx: int, chunk: RetrievedChunk) -> str:
    """
    Render a chunk as `<doc id="N" ...>body</doc>`.

    Anthropic best-practice for Claude in long-context retrieval: wrap
    each evidence document in an XML tag with meaningful attributes.
    Claude's attention pattern on tagged docs is measurably better than
    on plain bracketed headers.
    """
    attrs: List[Tuple[str, str]] = [("id", str(idx))]
    if chunk.source_type == "email_body":
        attrs.append(("type", "email"))
    elif chunk.source_type == "attachment":
        attrs.append(("type", "attachment"))
        if chunk.filename:
            attrs.append(("filename", _xml_escape(chunk.filename)))
        if chunk.page_start is not None:
            if chunk.page_end and chunk.page_end != chunk.page_start:
                attrs.append(("pages", f"{chunk.page_start}-{chunk.page_end}"))
            else:
                attrs.append(("page", str(chunk.page_start)))
    if chunk.date is not None:
        try:
            attrs.append(("date", chunk.date.strftime("%Y-%m-%d %H:%M")))
        except AttributeError:
            attrs.append(("date", _xml_escape(str(chunk.date))))
    if chunk.from_email:
        attrs.append(("from", _xml_escape(chunk.from_email)))
    if chunk.to_emails:
        attrs.append(("to", _xml_escape(", ".join(chunk.to_emails[:3]))))
    if chunk.subject:
        attrs.append(("subject", _xml_escape(chunk.subject)))
    # Option B fan-out — render as a single-attribute summary so Claude
    # can attribute the same file across multiple parent emails without
    # bloating the XML tag list.
    occ_count = len(chunk.occurrences or [])
    if occ_count > 1:
        attrs.append(("appeared_in_n_emails", str(occ_count)))
        latest = chunk.latest_date
        if latest is not None:
            try:
                attrs.append(("latest_appearance", latest.strftime("%Y-%m-%d")))
            except AttributeError:
                attrs.append(("latest_appearance", _xml_escape(str(latest))))

    attrs_str = " ".join(f'{k}="{v}"' for k, v in attrs)
    body = chunk.body or chunk.text or ""
    fan_out = _format_occurrences_fanout(chunk)
    body_with_fanout = f"{fan_out}\n{body}" if fan_out else body
    return f"<doc {attrs_str}>\n{body_with_fanout}\n</doc>"


# Tail reminder appended AFTER the question. Best-practice instruction
# placement for long context (Anthropic's own guidance). Reminds Claude
# of the highest-leverage rules right where it starts generating.
_TAIL_REMINDER = (
    "REMINDER (apply strictly):\n"
    "  • Cite EVERY factual claim with [#N] referring to the source above. "
    "Uncited claims will be treated as expertise commentary, not corpus facts.\n"
    "  • If the corpus does not support a claim, say so plainly — never fabricate.\n"
    "  • When the same fact appears at different dates, surface ALL versions "
    "with their dates and flag which is most recent / operative.\n"
    "  • If a quoted dollar amount, name, or filename appears anywhere in the "
    "sources above, USE IT. Don't say it isn't present.\n"
)


def _build_user_message(
    question: str,
    chunks: List[RetrievedChunk],
    *,
    timeline: bool = False,
    xml_sources: bool = False,
) -> str:
    """
    Compose the user-turn message: SOURCES block + question + tail reminder.

    When `xml_sources=True`, each chunk is wrapped in `<doc>...</doc>` and the
    full block in `<sources>...</sources>`. Otherwise we use the legacy
    bracketed-header format (kept for backward compatibility / older tests).
    """
    if not chunks:
        return (
            f"QUESTION:\n{question}\n\n"
            "(No corpus sources were retrieved for this question. "
            "Answer from your legal expertise alone, and explicitly note the "
            "absence of supporting corpus evidence.)"
        )

    if xml_sources:
        inner = "\n".join(
            _format_chunk_xml(i + 1, c) for i, c in enumerate(chunks)
        )
        order_note = (
            "  (sorted by date ascending)"
            if timeline
            else "  (best signals at start AND end of block; cite as [#N])"
        )
        sources_block = f"<sources count=\"{len(chunks)}\">{order_note}\n{inner}\n</sources>"
        body = (
            f"{sources_block}\n\n"
            f"<question>\n{question}\n</question>\n\n"
            f"{_TAIL_REMINDER}"
        )
        if timeline:
            body = f"{TIMELINE_INSTRUCTION}\n\n{body}"
        return body

    # ---- legacy plain format ----
    sources_block = "\n\n".join(
        _format_chunk_for_prompt(i + 1, c) for i, c in enumerate(chunks)
    )
    instr = (
        "SOURCES (sorted by date ascending; cite as [#N]):"
        if timeline else
        "SOURCES (numbered, cite as [#N]):"
    )
    body = f"{instr}\n\n{sources_block}\n\n---\n\nQUESTION:\n{question}"
    if timeline:
        body = f"{TIMELINE_INSTRUCTION}\n\n{body}"
    return body


@dataclass
class Turn:
    question: str
    answer: str
    chunks: List[RetrievedChunk] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    # Sprint-3-finish verification metadata. Empty for v1 / legacy path.
    facts: List[Dict[str, Any]] = field(default_factory=list)
    fact_verdicts: List[Dict[str, Any]] = field(default_factory=list)
    verification_outcome: Optional[str] = None     # e.g. VERIFIED_FIRST_PASS
    # Sprint-4 agent trace. None for non-agent turns.
    agent_trace: Optional[Dict[str, Any]] = None
    # Sprint-5: privilege mode ("analysis" | "clean") + provenance footer.
    mode: str = "analysis"
    provenance: Optional[Dict[str, Any]] = None


class LegalAdvisorChat:
    """Stateful multi-turn conversation manager over the email corpus."""

    def __init__(
        self,
        anthropic_api_key: str,
        retriever: Retriever,
        *,
        model: str = "claude-opus-4-6",
        max_tokens: int = 8192,
        # ---- v2 add-ons (all optional; OFF by default) -------------------
        use_enhanced_prompt: bool = False,
        summary_memory: Optional[Any] = None,    # SummaryMemory instance
        xml_sources: bool = False,
        anthropic_client: Optional[anthropic.Anthropic] = None,
        # ---- Sprint 3 finish: verified-answer pipeline -------------------
        use_structured_output: bool = False,
        use_citation_verifier: bool = False,
        use_verifier_retry: bool = False,
        verifier_threshold: float = 85.0,
        verifier_log_db: Optional[Any] = None,   # pymongo Database or None
        # ---- Sprint 4: Agentic Legal Investigator -----------------------
        use_agent: bool = False,
        agent_v2_pipeline: Optional[Any] = None,  # V2Pipeline for tool wiring
        agent_max_tool_calls: int = 30,
        agent_max_total_tokens: int = 3_000_000,
        agent_max_wall_clock_s: float = 1200.0,
        agent_model: str = "claude-opus-4-6",
        agent_max_tokens_per_call: int = 16384,
        agent_effort: Optional[str] = None,
        agent_seed_with_initial_search: bool = True,
        agent_trace_log_db: Optional[Any] = None,
    ) -> None:
        if not anthropic_api_key and anthropic_client is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is missing. Add it to .env before chatting."
            )
        self.client = anthropic_client or anthropic.Anthropic(api_key=anthropic_api_key)
        self.retriever = retriever
        self.model = model
        self.max_tokens = max_tokens
        self.history: List[Turn] = []
        # v2 hooks
        self.use_enhanced_prompt = use_enhanced_prompt
        self.summary_memory = summary_memory
        self.xml_sources = xml_sources
        # Sprint 3 finish — verified-answer pipeline
        self.use_structured_output = use_structured_output
        self.use_citation_verifier = use_citation_verifier
        self.use_verifier_retry = use_verifier_retry
        self.verifier_threshold = verifier_threshold
        self.verifier_log_db = verifier_log_db
        # Sprint 4 — Agentic Legal Investigator
        self.use_agent = use_agent
        self.agent_v2_pipeline = agent_v2_pipeline
        self.agent_max_tool_calls = agent_max_tool_calls
        self.agent_max_total_tokens = agent_max_total_tokens
        self.agent_max_wall_clock_s = agent_max_wall_clock_s
        self.agent_model = agent_model
        self.agent_max_tokens_per_call = agent_max_tokens_per_call
        self.agent_effort = agent_effort
        self.agent_seed_with_initial_search = agent_seed_with_initial_search
        self.agent_trace_log_db = agent_trace_log_db
        # Hook for the WS layer to subscribe to live agent events.
        # Caller sets this on a per-question basis. Type:
        #   Callable[[event_type: str, payload: dict], None]
        self.on_agent_event: Optional[Any] = None
        # Hook for the WS layer to inject an interrupt request.
        # Caller passes the budget object back to the layer via
        # `get_current_budget()` after `ask()` returns; for the
        # in-progress case the WS handler can set the interrupt flag
        # directly through a shared mutable BudgetTracker (advanced).
        self._current_budget: Optional[Any] = None

    def ask(
        self,
        question: str,
        *,
        atlas_filter: Optional[dict] = None,
        timeline: Optional[bool] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        max_chunks_timeline: int = 120,
        mode: str = "analysis",
    ) -> Turn:
        # ---- Sprint 5.5: Clean mode — exclude privileged at the retrieval
        # layer so privileged strategy CANNOT leak into a shareable answer.
        if mode == "clean":
            from src.rag.provenance import clean_mode_filter
            cf = clean_mode_filter()
            atlas_filter = {**(atlas_filter or {}), **cf} if atlas_filter else cf
        # Auto-detect timeline intent if not explicitly set.
        auto_timeline, auto_from, auto_to = detect_timeline_intent(question)
        if timeline is None:
            timeline = auto_timeline
        if date_from is None:
            date_from = auto_from
        if date_to is None:
            date_to = auto_to

        if timeline:
            logger.info(
                f"Timeline mode  (date_from={date_from}, date_to={date_to}, "
                f"max_chunks={max_chunks_timeline})"
            )
            chunks = self.retriever.retrieve_timeline(
                question,
                date_from=date_from,
                date_to=date_to,
                max_chunks=max_chunks_timeline,
                atlas_filter=atlas_filter,
            )
        else:
            chunks = self.retriever.retrieve(question, atlas_filter=atlas_filter)
        logger.info(f"Retrieved {len(chunks)} chunks for question")

        # ---- prior messages (with optional summary memory) -----------------
        prior_messages = self._build_prior_messages()

        user_msg = _build_user_message(
            question, chunks, timeline=timeline, xml_sources=self.xml_sources,
        )
        prior_messages.append({"role": "user", "content": user_msg})

        # ---- system prompt (v1 default; v2 enhanced if enabled) -----------
        system_prompt = self._build_system_prompt(
            question=question, timeline=timeline, chunks=chunks,
        )

        # ---- Single answer path: Sprint 4 agent ----
        # The agent loop is the sole production pipeline. It internally
        # falls back to the Sprint-3 verified one-shot (`_ask_verified`)
        # if the agent runner crashes or `agent_v2_pipeline` is missing.
        # No legacy plain-Opus branch — every query gets verification
        # and (on failure) one retry pass.
        # In Clean mode, the SAME privilege exclusion must apply to every
        # retrieval the agent does internally (not just the seed) — otherwise
        # the agent's own tool calls leak privileged chunks into a shareable
        # answer. Pass the filter through to the agent's toolbox.
        agent_base_filter = None
        if mode == "clean":
            from src.rag.provenance import clean_mode_filter
            agent_base_filter = clean_mode_filter()
        turn = self._ask_agent(question=question, initial_chunks=chunks,
                               base_filter=agent_base_filter)

        # Surface a v2→v1 retrieval degrade (previously silent — thin
        # answers with no visible cause).
        retrieval_degraded = getattr(self.retriever, "last_degraded", None)
        if retrieval_degraded and turn.answer:
            turn.answer += (
                "\n\n⚠ **Retrieval note** — enhanced (v2) retrieval degraded "
                f"to basic search for this query ({retrieval_degraded}); "
                "evidence coverage may be thinner than usual."
            )

        # ---- Sprint 5.5/5.6/5.7: mode, provenance footer, isolation ------
        turn.mode = mode
        try:
            from src.rag.provenance import provenance_footer
            foot = provenance_footer(turn.chunks or chunks, mode=mode,
                                     fact_verdicts=turn.fact_verdicts)
            # Keep provenance as STRUCTURED metadata only (turn.provenance);
            # do NOT append it to the answer prose — the UI renders it from
            # turn.provenance, and users don't want it inline in the message.
            turn.provenance = foot
            # 5.6 cache/memory isolation: a Clean-mode turn must never become
            # context for a later (possibly shareable) turn, and vice-versa —
            # so Clean turns are NOT appended to the reusable analysis history.
            if mode == "clean" and foot.get("clean_mode_leak"):
                logger.error("CLEAN-MODE LEAK DETECTED — privileged sources in shareable answer")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"provenance footer skipped: {exc}")

        if mode != "clean":
            self.history.append(turn)
        else:
            logger.info("Clean-mode turn — isolated (not added to reusable history)")

        # ---- update summary memory in the background (best-effort) --------
        if self.summary_memory is not None:
            try:
                from src.rag.v2.memory import Turn as MemTurn  # local import — avoids circular
                mem_turns = [
                    MemTurn(question=t.question, answer=t.answer)
                    for t in self.history
                ]
                self.summary_memory.maybe_update_summary(mem_turns)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"summary memory update skipped: {exc}")

        return turn

    # ------------------------------------------------------------------
    # Sprint 4 — Agentic Legal Investigator
    # ------------------------------------------------------------------

    def _ask_agent(
        self,
        *,
        question: str,
        initial_chunks: List[RetrievedChunk],
        base_filter: Optional[dict] = None,
    ) -> Turn:
        """
        Run the v3 agent loop and return a Turn with agent_trace.

        This is the SOLE answer path in production. The Sprint-3
        verified one-shot (`_ask_verified`) is kept ONLY as an internal
        resilience fallback when:
          • `agent_v2_pipeline` is missing (misconfiguration), or
          • the agent runner raises an unhandled exception.
        Under normal operation every query goes through the agent.
        """
        # Resilience: if the agent infrastructure is not wired, degrade
        # gracefully to the verified one-shot instead of crashing.
        if self.agent_v2_pipeline is None:
            logger.warning(
                "agent_v2_pipeline missing; degrading to verified one-shot "
                "for this query"
            )
            return self._degraded_turn(
                question, initial_chunks,
                reason="agent pipeline not configured",
            )

        from src.rag.v3 import AgentRunner, BudgetTracker

        budget = BudgetTracker(
            max_tool_calls=self.agent_max_tool_calls,
            max_total_tokens=self.agent_max_total_tokens,
            max_wall_clock_s=self.agent_max_wall_clock_s,
        )
        # Expose the budget so the WS layer can request interrupt
        # mid-investigation by setting `budget.interrupt_requested = True`.
        self._current_budget = budget

        runner = AgentRunner(
            anthropic_client=self.client,
            v2_pipeline=self.agent_v2_pipeline,
            retriever=self.retriever,
            model=self.agent_model,
            max_tokens_per_call=self.agent_max_tokens_per_call,
            fuzzy_threshold=self.verifier_threshold,
            effort=self.agent_effort,
        )

        # Conversation memory: give the agent the prior turns (verbatim recent
        # + rolling summary) so follow-up questions keep context. Previously the
        # v3 agent ignored history entirely — this fixes that.
        prior_messages = self._build_prior_messages()

        agent_result = None
        crash_reason = None
        try:
            agent_result = runner.run(
                question,
                budget=budget,
                seed_with_initial_search=self.agent_seed_with_initial_search,
                on_event=self.on_agent_event,
                prior_messages=prior_messages,
                base_filter=base_filter,
            )
        except Exception as exc:  # noqa: BLE001
            crash_reason = f"agent loop crashed: {str(exc)[:200]}"
            logger.error(f"agent loop crashed; falling back to verified one-shot: {exc}")
        finally:
            self._current_budget = None

        if agent_result is None:
            return self._degraded_turn(
                question, initial_chunks,
                reason=crash_reason or "agent returned no result",
            )

        # Persist agent_trace if configured.
        if self.agent_trace_log_db is not None:
            try:
                self.agent_trace_log_db["agent_trace_log"].insert_one({
                    **agent_result.agent_trace,
                    "model": self.agent_model,
                    "outcome": agent_result.outcome,
                    "n_facts": agent_result.n_facts,
                    "n_verified": agent_result.n_verified,
                    "elapsed_ms": agent_result.elapsed_ms,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"agent_trace_log write failed: {exc}")

        return Turn(
            question=question,
            answer=agent_result.answer,
            chunks=agent_result.chunks,
            facts=agent_result.facts,
            fact_verdicts=agent_result.fact_verdicts,
            verification_outcome=agent_result.outcome,
            agent_trace=agent_result.agent_trace,
        )

    def _fallback_to_verified(
        self,
        question: str,
        initial_chunks: List[RetrievedChunk],
    ) -> Turn:
        """
        Internal resilience hatch — used ONLY when the agent loop is
        unavailable. Reconstructs the messages + system prompt fresh
        because the original `ask()` already pushed the user turn.
        """
        prior_messages = self._build_prior_messages()
        user_msg = _build_user_message(
            question, initial_chunks, timeline=False, xml_sources=self.xml_sources,
        )
        system_prompt = self._build_system_prompt(
            question=question, timeline=False, chunks=initial_chunks,
        )
        return self._ask_verified(
            question=question,
            chunks=initial_chunks,
            system_prompt=system_prompt,
            user_msg=user_msg,
            prior_messages=prior_messages,
        )

    def _degraded_turn(
        self,
        question: str,
        initial_chunks: List[RetrievedChunk],
        *,
        reason: str,
    ) -> Turn:
        """
        Agent → one-shot degrade, made VISIBLE. Previously this fallback
        was silent: the user got a much shallower answer, the reasoning
        panel froze on "Investigating…", and the failure looked like a
        model-quality problem. Now we (a) emit an `agent_degraded` event
        so the UI can close the panel with an explicit status, and
        (b) annotate the answer so the reader knows this response did
        not go through the deep-investigation pipeline.
        """
        if self.on_agent_event is not None:
            try:
                self.on_agent_event("agent_degraded", {"reason": reason})
            except Exception:  # noqa: BLE001
                pass
        turn = self._fallback_to_verified(question, initial_chunks)
        note = (
            "\n\n---\n⚠ **Degraded answer** — the deep-investigation agent "
            f"was unavailable for this query ({reason}). This response used "
            "the one-shot verified pipeline; re-ask to retry the full agent."
        )
        if turn.answer:
            turn.answer = f"{turn.answer}{note}"
        return turn

    def get_current_budget(self):
        """Expose the running BudgetTracker so the WS layer can set
        `interrupt_requested = True` from an out-of-band 'stop' frame."""
        return self._current_budget

    # ------------------------------------------------------------------
    # Sprint 3 finish — verified-answer pipeline
    # ------------------------------------------------------------------

    def _ask_verified(
        self,
        *,
        question: str,
        chunks: List[RetrievedChunk],
        system_prompt: str,
        user_msg: str,
        prior_messages: List[dict],
    ) -> Turn:
        """
        Run the structured-output + citation-verifier + retry pipeline.

        Falls back gracefully to a legacy text answer if the pipeline
        emits no facts or the model didn't call the submit_answer tool
        (the pipeline already handles those edges and returns whatever
        prose Opus produced).
        """
        # Lazy import — keeps module load fast for legacy users.
        from src.rag.v2.answer_pipeline import (
            generate_verified_answer,
            log_verification,
            OUTCOME_FALLBACK,
        )

        try:
            verified = generate_verified_answer(
                anthropic_client=self.client,
                model=self.model,
                system_prompt=system_prompt,
                user_message=user_msg,
                prior_messages=prior_messages,
                chunks=chunks,
                max_tokens=self.max_tokens,
                fuzzy_threshold=self.verifier_threshold,
                enable_retry=self.use_verifier_retry,
            )
        except Exception as exc:  # noqa: BLE001
            # Catastrophic failure of the verified pipeline — fall back to
            # a plain Opus call so the user still gets an answer.
            logger.error(
                f"verified-answer pipeline crashed; falling back to "
                f"plain answer: {exc}"
            )
            # Streaming so max_tokens may exceed the ~21k non-streaming cap.
            with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system_prompt,
                messages=prior_messages + [{"role": "user", "content": user_msg}],
            ) as _stream:
                response = _stream.get_final_message()
            parts = [
                b.text for b in response.content
                if getattr(b, "type", None) == "text"
            ]
            return Turn(
                question=question,
                answer="\n".join(parts).strip(),
                chunks=chunks,
                verification_outcome=OUTCOME_FALLBACK,
            )

        # Optional audit logging — silent on failure.
        if self.verifier_log_db is not None:
            log_verification(
                mongo_db=self.verifier_log_db,
                verified=verified,
                query=question,
                model=self.model,
            )

        return Turn(
            question=question,
            answer=verified.answer,
            chunks=chunks,
            facts=verified.facts,
            fact_verdicts=verified.fact_verdicts,
            verification_outcome=verified.outcome,
        )

    # ------------------------------------------------------------------
    # Internal helpers — extracted to keep `ask()` readable
    # ------------------------------------------------------------------

    def _build_prior_messages(self) -> List[dict]:
        """
        Returns the list of {role, content} messages representing the
        conversation BEFORE the new user question.

        When `summary_memory` is set, we delegate so older turns get
        compacted into a running summary.
        """
        if self.summary_memory is not None:
            try:
                from src.rag.v2.memory import Turn as MemTurn
                mem_turns = [
                    MemTurn(question=t.question, answer=t.answer)
                    for t in self.history
                ]
                return list(self.summary_memory.build_prior_messages(mem_turns))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"summary memory build failed, using raw history: {exc}")

        # v1 default — just flatten history.
        msgs: List[dict] = []
        for turn in self.history:
            msgs.append({"role": "user", "content": turn.question})
            msgs.append({"role": "assistant", "content": turn.answer})
        return msgs

    def _build_system_prompt(
        self,
        *,
        question: str,
        timeline: bool,
        chunks: List[RetrievedChunk],
    ) -> str:
        """v1 prompt by default; v2 enhanced prompt when configured."""
        if not self.use_enhanced_prompt:
            return SYSTEM_PROMPT

        # Lazy import to keep v1 path free of v2 dependencies.
        try:
            from src.rag.v2.prompts import build_system_prompt
            from src.rag.v2.query_understanding import extract_signals

            sigs = extract_signals(question)
            intent = "timeline" if timeline else sigs.primary_intent()

            corpus_min: Optional[datetime] = None
            corpus_max: Optional[datetime] = None
            valid_dates = [c.date for c in chunks if isinstance(c.date, datetime)]
            if valid_dates:
                corpus_min = min(valid_dates)
                corpus_max = max(valid_dates)

            return build_system_prompt(
                intent=intent,
                today=datetime.now(timezone.utc),
                corpus_min_date=corpus_min,
                corpus_max_date=corpus_max,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"v2 enhanced prompt build failed, using v1: {exc}")
            return SYSTEM_PROMPT

    def reset(self) -> None:
        self.history.clear()
