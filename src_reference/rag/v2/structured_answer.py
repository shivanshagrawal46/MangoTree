"""
Structured-answer generation via Claude tool-use.

This module is responsible for one thing: convincing Opus 4.6 to emit
its answer as a strongly-typed JSON object (a "submit_answer" tool call)
so we can deterministically verify each citation BEFORE showing the
answer to the user.

The schema we force Opus into:

    {
      "facts": [
        {
          "id":              "f1",
          "claim":           "Settlement amount is $450,000",
          "source_chunk_id": 3,
          "verbatim_quote":  "the total settlement amount of $450,000",
          "confidence":      "high"|"medium"|"low",
          "note":            "<optional: derivation explanation>"
        },
        ...
      ],
      "answer": "<final prose synthesis citing [#N]>"
    }

Why tool-use instead of "tell Claude to output JSON in a prompt"?
  - Tool-use guarantees the response is parseable JSON conforming to
    the input_schema (Claude validates server-side).
  - We get structured data back as `block.input` — no flaky string
    parsing of free-form JSON inside text blocks.
  - It's the same mechanism Anthropic's own examples use for verified
    extractive QA.

Re-extraction is also done via tool-use, but with a NARROWER tool
(`reextract_failed_claims`) that only takes back the specific facts
that failed first-pass verification. This keeps cost down (Opus only
re-reasons about ~1-3 facts, not the whole answer).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.rag.retriever import RetrievedChunk
from src.utils.logger import logger


# =====================================================================
# Tool schemas
# =====================================================================

SUBMIT_ANSWER_TOOL: Dict[str, Any] = {
    "name": "submit_answer",
    "description": (
        "Submit a verifiable, structured legal answer. Use this tool for "
        "every response. Each factual claim must be backed by a verbatim "
        "quote from the cited source chunk."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "description": (
                    "Every concrete factual claim from the corpus, paired "
                    "with the exact text from the cited chunk that supports "
                    "it. Empty list is allowed when the answer is purely "
                    "expertise / scoping / clarification."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": (
                                "Short unique id like 'f1', 'f2'. Used in "
                                "logs and re-extraction tracking."
                            ),
                        },
                        "claim": {
                            "type": "string",
                            "description": (
                                "Natural-language paraphrase of the fact "
                                "being asserted. Used for display, not "
                                "verification."
                            ),
                        },
                        "source_chunk_id": {
                            "type": "integer",
                            "description": (
                                "1-based index [#N] of the source chunk "
                                "(from the SOURCES block above) that "
                                "supports this fact. Must be in range "
                                "1..N where N is the number of sources."
                            ),
                            "minimum": 1,
                        },
                        "verbatim_quote": {
                            "type": "string",
                            "description": (
                                "EXACT text from the cited chunk that "
                                "supports the claim. Copy character-for-"
                                "character — minor whitespace and OCR "
                                "noise are tolerated by the verifier, but "
                                "numbers, dates, and proper nouns MUST "
                                "appear in the chunk."
                            ),
                            "minLength": 8,
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": (
                                "high = chunk states the fact directly. "
                                "medium = derived/calculated from chunk "
                                "content (explain in note). low = "
                                "interpretation, multiple readings possible."
                            ),
                        },
                        "note": {
                            "type": "string",
                            "description": (
                                "Optional. Use for derivations (e.g. "
                                "'5 years derived from 2023 to 2028') or "
                                "to flag uncertainty. Leave empty for "
                                "direct quotes."
                            ),
                        },
                    },
                    "required": ["id", "claim", "source_chunk_id",
                                 "verbatim_quote", "confidence"],
                },
            },
            "answer": {
                "type": "string",
                "description": (
                    "The final prose answer to show the user. Every "
                    "factual claim in this prose must correspond to a "
                    "fact in 'facts' above, cited with [#N]. The prose "
                    "may add legal commentary or framing, but every "
                    "factual assertion must be verifiable."
                ),
                "minLength": 1,
            },
        },
        "required": ["facts", "answer"],
    },
}


REEXTRACT_TOOL: Dict[str, Any] = {
    "name": "reextract_failed_claims",
    "description": (
        "Re-extract verbatim quotes for claims whose initial verification "
        "failed. For each input fact, either provide a new verbatim quote "
        "that DOES appear in the cited chunk, or mark it as NOT_PRESENT "
        "if the chunk does not actually support the claim."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reextractions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_id": {
                            "type": "string",
                            "description": "Matches the failed fact's id (e.g. 'f3').",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["REEXTRACTED", "NOT_PRESENT"],
                            "description": (
                                "REEXTRACTED = providing a new verbatim "
                                "quote. NOT_PRESENT = the chunk does not "
                                "support the claim; the original answer "
                                "had a hallucinated reference."
                            ),
                        },
                        "verbatim_quote": {
                            "type": "string",
                            "description": (
                                "New verbatim quote from the cited chunk. "
                                "Required if status=REEXTRACTED. Leave "
                                "empty if status=NOT_PRESENT."
                            ),
                        },
                        "corrected_claim": {
                            "type": "string",
                            "description": (
                                "Optional: a revised claim paraphrase if "
                                "the original claim's wording was "
                                "incorrect but the underlying fact is "
                                "supported."
                            ),
                        },
                    },
                    "required": ["fact_id", "status"],
                },
            },
        },
        "required": ["reextractions"],
    },
}


# =====================================================================
# Result dataclasses
# =====================================================================

@dataclass
class StructuredAnswer:
    """Parsed output of the submit_answer tool call."""

    facts: List[Dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    raw_tool_input: Dict[str, Any] = field(default_factory=dict)
    stop_reason: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def has_facts(self) -> bool:
        return bool(self.facts)


@dataclass
class ReextractionResult:
    """Parsed output of the reextract_failed_claims tool call."""

    by_fact_id: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    raw_tool_input: Dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0


# =====================================================================
# Prompt fragments (appended to whatever v2 system prompt is in use)
# =====================================================================

_STRUCTURED_PROMPT_TAIL = """\

================================================================================
## REQUIRED OUTPUT FORMAT — submit_answer tool

You MUST respond by calling the `submit_answer` tool. Do NOT respond with
plain text. The tool input has two top-level fields:

  facts:  array of EVERY factual claim from the corpus, each paired with
          a verbatim quote that appears in the cited chunk.
  answer: the prose answer shown to the user. Every factual claim in this
          prose must trace to a `facts[]` entry via [#N] citations.

### Rules for `facts[]`

  1. EVERY number, date, name, dollar amount, percentage, and citation
     in `answer` MUST appear in `facts[]` with a verbatim quote.
  2. `verbatim_quote` is EXACT TEXT from the chunk — copy character-for-
     character. The verifier tolerates whitespace/OCR noise but rejects
     paraphrases of numbers/dates/names. NEVER paraphrase a dollar
     amount or date.
  3. `source_chunk_id` is the 1-based [#N] index from the SOURCES block.
  4. `confidence`:
       high   = chunk directly states the fact
       medium = fact derived/calculated; explain in `note`
       low    = interpretive; multiple readings possible
  5. Use stable ids: "f1", "f2", "f3", ... in order they appear.
  6. Empty `facts` is allowed ONLY if the answer is pure scoping,
     clarification, or commentary with no corpus-derived facts.

### Rules for `answer`

  1. Plain prose, normal legal-advisor tone.
  2. Cite every factual claim with [#N] referencing source_chunk_id.
  3. If you need to derive a number (e.g. duration from two dates),
     state the derivation explicitly and set confidence=medium with a
     `note`.
  4. NEVER paraphrase a number or date — quote it verbatim.
"""


_REEXTRACT_PROMPT_TEMPLATE = """\
You previously submitted a structured answer, but the citation verifier
could not confirm the following claims. For each one, look ONLY at the
cited chunk and decide:

  - If the chunk DOES contain text supporting the claim, provide a NEW
    verbatim_quote (copy exact text from the chunk).
  - If the chunk DOES NOT actually support the claim, mark status as
    NOT_PRESENT — the original citation was incorrect.

Failed claims:
{failed_block}

Respond by calling the `reextract_failed_claims` tool. Do not respond
with plain text. Do not re-cite a different chunk for the same fact —
limit your scope to the same source_chunk_id that was failing.
"""


def get_structured_prompt_tail() -> str:
    """Suffix to append to the v2 system prompt when structured output is on."""
    return _STRUCTURED_PROMPT_TAIL


def build_reextract_prompt(
    failed_facts: List[Dict[str, Any]],
    chunks: List[RetrievedChunk],
) -> str:
    """
    Build the re-extraction prompt that targets only the failed facts.

    We re-show the FULL chunk text for each failed fact's cited chunk so
    Opus has the material it needs to either find a real verbatim quote
    or admit the chunk doesn't support the claim.
    """
    blocks: List[str] = []
    for f in failed_facts:
        fid = f.get("id") or "?"
        claim = f.get("claim") or ""
        chunk_id = f.get("source_chunk_id") or 0
        prev_quote = f.get("verbatim_quote") or ""
        reason = f.get("_verifier_reason") or ""

        if 1 <= chunk_id <= len(chunks):
            chunk = chunks[chunk_id - 1]
            chunk_text = (chunk.body or chunk.text or "").strip()
        else:
            chunk_text = "(chunk index out of range — original citation invalid)"

        # Cap chunk text in the re-extraction prompt to avoid blowing up
        # the context unnecessarily. Most legal-chunk bodies are <2k chars.
        if len(chunk_text) > 4000:
            chunk_text = chunk_text[:4000] + "\n... [truncated]"

        blocks.append(
            f"\nFact id: {fid}\n"
            f"  Original claim:        {claim}\n"
            f"  Original verbatim:     {prev_quote!r}\n"
            f"  Cited chunk [#{chunk_id}]:\n"
            f"  ----------------------------------------\n"
            f"  {chunk_text}\n"
            f"  ----------------------------------------\n"
            f"  Verifier said: {reason}\n"
        )
    return _REEXTRACT_PROMPT_TEMPLATE.format(failed_block="\n".join(blocks))


# =====================================================================
# Response parsers
# =====================================================================

def parse_submit_answer(response: Any) -> StructuredAnswer:
    """
    Extract the StructuredAnswer from an Anthropic Messages API response
    where Claude called the `submit_answer` tool. Robust to extra text
    blocks Claude may emit before the tool call.

    Falls back gracefully (empty StructuredAnswer with raw_tool_input
    set) if the tool wasn't called — caller decides what to do.
    """
    result = StructuredAnswer(stop_reason=getattr(response, "stop_reason", None))

    usage = getattr(response, "usage", None)
    if usage is not None:
        result.input_tokens = getattr(usage, "input_tokens", 0) or 0
        result.output_tokens = getattr(usage, "output_tokens", 0) or 0
        result.cache_read_tokens = getattr(
            usage, "cache_read_input_tokens", 0) or 0
        result.cache_creation_tokens = getattr(
            usage, "cache_creation_input_tokens", 0) or 0

    if not getattr(response, "content", None):
        logger.warning("submit_answer: response has no content blocks")
        return result

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and \
           getattr(block, "name", None) == "submit_answer":
            try:
                payload = block.input or {}
                result.raw_tool_input = payload
                facts = payload.get("facts") or []
                # Defensive copy + minimal normalisation
                clean: List[Dict[str, Any]] = []
                for i, f in enumerate(facts):
                    if not isinstance(f, dict):
                        continue
                    f = dict(f)  # shallow copy
                    f.setdefault("id", f"f{i+1}")
                    clean.append(f)
                result.facts = clean
                result.answer = str(payload.get("answer") or "").strip()
                return result
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"submit_answer parse error: {exc}")
                return result

    # No tool_use block — log for diagnostic purposes
    logger.warning(
        "submit_answer: model did not call the submit_answer tool "
        f"(stop_reason={result.stop_reason}); will fall back."
    )
    return result


def parse_reextract(response: Any) -> ReextractionResult:
    """Parse the reextract_failed_claims tool call."""
    result = ReextractionResult()

    usage = getattr(response, "usage", None)
    if usage is not None:
        result.input_tokens = getattr(usage, "input_tokens", 0) or 0
        result.output_tokens = getattr(usage, "output_tokens", 0) or 0

    if not getattr(response, "content", None):
        return result

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and \
           getattr(block, "name", None) == "reextract_failed_claims":
            payload = block.input or {}
            result.raw_tool_input = payload
            for item in payload.get("reextractions") or []:
                if not isinstance(item, dict):
                    continue
                fid = item.get("fact_id")
                if fid:
                    result.by_fact_id[str(fid)] = dict(item)
            return result
    return result


__all__ = [
    "SUBMIT_ANSWER_TOOL",
    "REEXTRACT_TOOL",
    "StructuredAnswer",
    "ReextractionResult",
    "get_structured_prompt_tail",
    "build_reextract_prompt",
    "parse_submit_answer",
    "parse_reextract",
]
