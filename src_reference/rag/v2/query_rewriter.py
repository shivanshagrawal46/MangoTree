"""
Query Rewriter — HyDE + Multi-Query expansion using Claude Sonnet 4.6.

Why this exists:
  Pure semantic similarity between a question and the document containing
  the answer is often weak — especially in legal text where the question
  uses everyday phrasing ("where does the $450K come from?") and the
  answer uses formal phrasing ("the sum of $450,000 shall be paid…").

  Two industry-proven mitigations:

    • HyDE (Hypothetical Document Embeddings) — Claude writes a *plausible
      answer* to the query. We embed THAT instead of (or alongside) the
      raw query. The hypothetical answer is written in the same register
      as the corpus, so its embedding lands closer to the actual answer
      chunk in vector space.

    • Multi-Query Rewriting — Claude generates 2-3 alternate phrasings of
      the question. Each is embedded and searched separately. Results are
      fused via Reciprocal Rank Fusion. Catches queries where one phrasing
      misses but another hits.

Design rules:
  • LLM = Sonnet 4.6 (configurable, never Haiku).
  • One LLM call per query (returns BOTH HyDE answer and alt phrasings).
  • Fail-safe: if Claude errors, return empty expansions — caller falls
    back to v1 (just the original query).
  • Strict output parsing — if Claude's JSON is malformed, we still salvage
    what we can with regex fallbacks.
  • Cap output: each alt query and HyDE answer is length-bounded so the
    embedder never receives runaway tokens.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

import anthropic

from src.utils.logger import logger


# ---------------------------------------------------------------------------
# Tunables — kept conservative; override via Settings.
# ---------------------------------------------------------------------------

_MAX_ALT_QUERY_CHARS = 240
_MAX_HYDE_ANSWER_CHARS = 800
_MAX_OUTPUT_TOKENS = 800

# Prompt tells Claude to act as a domain-expert legal analyst and produce
# strict JSON. We use a "system" + "user" structure for stability.
_REWRITER_SYSTEM = """\
You are an expert legal information retrieval assistant. Your job is to help
a Retrieval-Augmented Generation system find the most relevant evidence in a
case-file corpus (emails + attached legal documents).

You receive ONE user question. You produce a structured JSON object with:

  1. `hyde_answer` — a SHORT, plausible *hypothetical* answer to the question,
      written in the same formal register as a court filing or attorney
      correspondence. This is NOT the real answer — it is a foil whose
      embedding will land near the real answer in vector space. Use realistic
      legal/financial phrasing. 2-4 sentences max. NEVER refuse or hedge —
      just produce the most likely answer shape.

  2. `alt_queries` — 2 or 3 alternate phrasings of the user's question.
      Each phrasing should approach the same information need from a
      different angle:
        - one keyword-focused (formal/legal terminology)
        - one conceptual/everyday phrasing
        - one document/source-focused phrasing
      Each alternate must stand alone (no pronouns referring to prior chats).
      Each must be ≤ 200 chars.

CRITICAL: Output ONLY a single JSON object, no prose, no markdown fences.
Schema:
{"hyde_answer": "...", "alt_queries": ["...", "...", "..."]}
"""

_USER_TEMPLATE = """\
USER QUESTION:
{question}

CONTEXT HINT (optional, may be empty):
{context_hint}

Return strict JSON only.
"""


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class RewrittenQuery:
    """Output of the rewriter. `original` is always populated."""

    original: str
    hyde_answer: Optional[str] = None
    alt_queries: List[str] = field(default_factory=list)

    def all_query_strings(self, *, include_hyde: bool = True) -> List[str]:
        """Return everything we want to embed, deduped."""
        out: List[str] = [self.original]
        if include_hyde and self.hyde_answer:
            out.append(self.hyde_answer)
        out.extend(self.alt_queries)
        # Dedupe on lowercase whitespace-normalised form.
        seen = set()
        deduped: List[str] = []
        for q in out:
            key = " ".join(q.lower().split())
            if key and key not in seen:
                seen.add(key)
                deduped.append(q)
        return deduped


# ---------------------------------------------------------------------------
# QueryRewriter
# ---------------------------------------------------------------------------

class QueryRewriter:
    """
    Produces HyDE answers + alternate queries via a single Claude call.

    Thread-safety: the underlying anthropic.Anthropic client is documented
    as thread-safe across concurrent requests. We don't share state across
    instances of this class beyond the client itself, so multiple callers
    can use the same QueryRewriter safely.
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        *,
        model: str = "claude-sonnet-4-6",
        max_alt_queries: int = 3,
        max_output_tokens: int = _MAX_OUTPUT_TOKENS,
    ) -> None:
        self.client = client
        self.model = model
        self.max_alt_queries = max(1, min(5, max_alt_queries))
        self.max_output_tokens = max_output_tokens

    # ------------------------------------------------------------------
    def rewrite(
        self,
        question: str,
        *,
        enable_hyde: bool = True,
        enable_multi_query: bool = True,
        context_hint: str = "",
    ) -> RewrittenQuery:
        """
        Generate HyDE + alt queries for `question`.

        Failure modes:
          • Claude raises (network, rate limit, etc.)  → return original-only.
          • Claude returns malformed output            → salvage with regex.
          • Both off                                   → return original-only
                                                          without an LLM call.
        """
        original = (question or "").strip()
        if not original:
            return RewrittenQuery(original="")

        # No-op shortcut.
        if not enable_hyde and not enable_multi_query:
            return RewrittenQuery(original=original)

        try:
            raw = self._call_llm(original, context_hint=context_hint)
        except Exception as exc:  # noqa: BLE001 — fail-safe wrapper
            logger.warning(f"QueryRewriter LLM call failed: {exc}")
            return RewrittenQuery(original=original)

        parsed = self._parse_response(raw)
        return RewrittenQuery(
            original=original,
            hyde_answer=parsed["hyde_answer"] if enable_hyde else None,
            alt_queries=(
                parsed["alt_queries"][: self.max_alt_queries]
                if enable_multi_query else []
            ),
        )

    # ------------------------------------------------------------------
    def _call_llm(self, question: str, *, context_hint: str) -> str:
        """One Claude call. Returns the raw text content."""
        user = _USER_TEMPLATE.format(
            question=question,
            context_hint=(context_hint or "").strip() or "(none)",
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=_REWRITER_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        parts: List[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "\n".join(parts).strip()

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_response(raw: str) -> dict:
        """
        Robust parse of Claude's response. Returns a dict with keys
        `hyde_answer` (str | None) and `alt_queries` (list[str]).

        Tolerates:
          • markdown fences around the JSON
          • trailing prose
          • single-quote 'JSON'
          • partial / malformed JSON (regex salvage)
        """
        result = {"hyde_answer": None, "alt_queries": []}
        if not raw:
            return result

        text = raw.strip()

        # Strip markdown fences if present.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

        # First attempt — direct JSON parse.
        parsed_obj: Optional[dict] = None
        try:
            parsed_obj = json.loads(text)
        except json.JSONDecodeError:
            # Try to find the largest valid JSON object substring.
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    parsed_obj = json.loads(match.group(0))
                except json.JSONDecodeError:
                    parsed_obj = None

        if isinstance(parsed_obj, dict):
            hyde = parsed_obj.get("hyde_answer")
            if isinstance(hyde, str) and hyde.strip():
                result["hyde_answer"] = hyde.strip()[:_MAX_HYDE_ANSWER_CHARS]

            alts = parsed_obj.get("alt_queries") or parsed_obj.get("alternates")
            if isinstance(alts, list):
                cleaned: List[str] = []
                for q in alts:
                    if isinstance(q, str):
                        q = q.strip().strip('"\'')
                        if q and len(q) <= _MAX_ALT_QUERY_CHARS:
                            cleaned.append(q)
                result["alt_queries"] = cleaned
            return result

        # Last-resort regex salvage.
        hyde_m = re.search(r'"?hyde_answer"?\s*:\s*"([^"]{10,})"', text, re.DOTALL)
        if hyde_m:
            result["hyde_answer"] = hyde_m.group(1).strip()[:_MAX_HYDE_ANSWER_CHARS]

        alts_m = re.findall(r'"([^"]{5,200})"', text)
        if alts_m and not result["alt_queries"]:
            # Filter out anything that looks like a JSON key.
            keys = {"hyde_answer", "alt_queries", "alternates"}
            result["alt_queries"] = [
                a for a in alts_m if a.lower() not in keys
            ][:5]

        return result
