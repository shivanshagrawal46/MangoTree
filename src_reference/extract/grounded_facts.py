"""Sprint 2 · 2.1 — grounded legal field extraction.

Pulls TYPED facts out of title-report text — chain of title, mortgages, liens,
lis pendens, judgments, assignments, satisfactions — where EVERY fact carries a
verbatim `source_quote` (and we verify that quote really appears in the source,
OCR-tolerant). Ungrounded facts are dropped, never stored. Schema-constrained
via Anthropic tool-use (Sonnet 4.6) with prompt caching on the document.

This is the structured backbone the Sprint-4 fraud detectors + event store +
bank entities build on.
"""
from __future__ import annotations

from typing import Any, Dict, List

from anthropic import Anthropic
from rapidfuzz import fuzz
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

_MAX_DOC_CHARS = 160_000  # ~40k tokens; title reports fit, bias to the front

_FACT_TOOL = {
    "name": "record_title_facts",
    "description": "Record the structured legal facts found in this title document. "
                   "Every fact MUST include a verbatim source_quote copied EXACTLY "
                   "from the document (no paraphrase). Omit anything not stated.",
    "input_schema": {
        "type": "object",
        "properties": {
            "chain_of_title": {"type": "array", "items": {"type": "object", "properties": {
                "grantor": {"type": "string"}, "grantee": {"type": "string"},
                "instrument_type": {"type": "string", "description": "deed/bargain&sale/quitclaim/referee etc."},
                "dated": {"type": "string"}, "recorded": {"type": "string"},
                "instrument_no": {"type": "string"}, "amount": {"type": "string"},
                "source_quote": {"type": "string"}}, "required": ["source_quote"]}},
            "mortgages": {"type": "array", "items": {"type": "object", "properties": {
                "lender": {"type": "string"}, "borrower": {"type": "string"},
                "amount": {"type": "string"}, "dated": {"type": "string"},
                "recorded": {"type": "string"}, "instrument_no": {"type": "string"},
                "satisfied": {"type": "boolean"}, "source_quote": {"type": "string"}},
                "required": ["source_quote"]}},
            "liens": {"type": "array", "items": {"type": "object", "properties": {
                "lien_type": {"type": "string", "description": "tax/judgment/mechanics/HOA/federal etc."},
                "creditor": {"type": "string"}, "amount": {"type": "string"},
                "dated": {"type": "string"}, "source_quote": {"type": "string"}},
                "required": ["source_quote"]}},
            "lis_pendens": {"type": "array", "items": {"type": "object", "properties": {
                "case": {"type": "string"}, "filed": {"type": "string"},
                "plaintiff": {"type": "string"}, "source_quote": {"type": "string"}},
                "required": ["source_quote"]}},
            "judgments": {"type": "array", "items": {"type": "object", "properties": {
                "creditor": {"type": "string"}, "debtor": {"type": "string"},
                "amount": {"type": "string"}, "entered": {"type": "string"},
                "source_quote": {"type": "string"}}, "required": ["source_quote"]}},
            "assignments": {"type": "array", "items": {"type": "object", "properties": {
                "assignor": {"type": "string"}, "assignee": {"type": "string"},
                "dated": {"type": "string"}, "source_quote": {"type": "string"}},
                "required": ["source_quote"]}},
        },
        "required": [],
    },
}

_SYS = ("You are a meticulous title abstractor. Extract ONLY facts explicitly "
        "stated in the document. For every item copy a short verbatim source_quote "
        "EXACTLY as written (used for citation + verification). If the document is "
        "an update/continuation search, extract what it states. Never infer or "
        "fabricate. Omit empty categories.")

_FACT_KEYS = ["chain_of_title", "mortgages", "liens", "lis_pendens", "judgments", "assignments"]


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


class GroundedExtractor:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 *, ground_threshold: float = 82.0) -> None:
        self.client = Anthropic(api_key=api_key, timeout=120.0, max_retries=0)
        self.model = model
        self.ground_threshold = ground_threshold

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30),
           retry=retry_if_exception_type(Exception), reraise=True)
    def _call(self, doc_text: str) -> Dict[str, Any]:
        resp = self.client.messages.create(
            model=self.model, max_tokens=8000, system=_SYS,
            tools=[_FACT_TOOL], tool_choice={"type": "tool", "name": "record_title_facts"},
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"<document>\n{doc_text}\n</document>",
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "Extract all title facts via record_title_facts."},
            ]}])
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input or {})
        return {}

    def extract(self, doc_text: str) -> Dict[str, Any]:
        """Return grounded facts dict; each fact verified to appear in doc_text."""
        text = (doc_text or "")[:_MAX_DOC_CHARS]
        if not text.strip():
            return {k: [] for k in _FACT_KEYS}
        raw = self._call(text)
        hay = _norm(text)
        out: Dict[str, List[Dict[str, Any]]] = {}
        dropped = 0
        for key in _FACT_KEYS:
            kept = []
            for fact in (raw.get(key) or []):
                if not isinstance(fact, dict):  # model occasionally emits a bare string
                    dropped += 1
                    continue
                q = _norm(fact.get("source_quote", ""))
                if not q:
                    dropped += 1
                    continue
                # OCR-tolerant grounding: quote must appear (fuzzily) in source
                if q in hay or fuzz.partial_ratio(q, hay) >= self.ground_threshold:
                    fact["grounded"] = True
                    kept.append(fact)
                else:
                    dropped += 1
            out[key] = kept
        out["_dropped_ungrounded"] = dropped
        return out
