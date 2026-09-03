"""
Anthropic Contextual Retrieval — per-chunk summary generator.

For each chunk of a document, we ask Claude Sonnet 4.6 to write a short
(100-150 token) summary explaining what the chunk is about and how it
fits in the overall document. This summary is prepended to the chunk
text before embedding, which gives the embedder enough context to
embed the chunk in *roughly the right semantic neighbourhood* even if
the chunk itself doesn't contain the document's main topic phrasing.

Anthropic's published research shows this boosts retrieval recall by
35-50% on legal / contract / multi-document corpora. The trade-off is
one Claude call per chunk. We cut that cost dramatically using
**prompt caching**: when we summarize multiple chunks of the *same*
document in sequence, the document portion of the prompt gets cached
on Anthropic's servers and subsequent calls pay 10x less on input
tokens (cache-read = $0.30/M vs input = $3/M for Sonnet 4.6).

Public API
----------
  summarizer = ContextualSummarizer(api_key=...)
  ctx_strings = summarizer.summarize_doc_chunks(
      doc_text="<full email or attachment text>",
      chunk_texts=["<chunk1>", "<chunk2>", ...],
  )
  # ctx_strings is a list of strings the same length as chunk_texts.
  # Each is ~100-150 tokens of plain text. Empty string on failure.

Failure mode
------------
If Claude fails on a chunk (rate limit, content filter, parse error)
we log a warning and return an empty context string. The chunk gets
embedded WITHOUT context — RAG accuracy regresses to baseline for
that chunk, but the corpus build never crashes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.logger import logger


# Anthropic prompt caching requires at least 1024 input tokens for Sonnet
# in the cached block. Smaller docs don't benefit from caching — we just
# pay the regular input price (still cheap at ~$0.003 per call).
_CACHE_MIN_TOKENS = 1024

# Hard cap on the document we send to Claude. Sonnet 4.6 supports 200K
# context, but giant docs are wasteful — only the first ~150K tokens of
# a single doc are likely to matter for chunk-level context. (For docs
# bigger than this, we still process every chunk; we just don't show
# Claude the *whole* doc when generating context.)
_MAX_DOC_TOKENS_FOR_CONTEXT = 150_000

# Per-chunk output budget for the context. Anthropic charges output at
# $15/M for Sonnet 4.6, so we keep this tight. 50-150 tokens is plenty
# for a short situating sentence; we set 200 as a hard cap.
_MAX_CONTEXT_OUTPUT_TOKENS = 200


_SYSTEM_PROMPT = (
    "You write short, factual context summaries that help a retrieval "
    "system find a specific chunk later. You never editorialise, "
    "speculate, or add information that is not present in the document. "
    "Answer with only the context and nothing else."
)

_CHUNK_INSTRUCTION = (
    "Above is the full document. Below is one chunk from it.\n\n"
    "<chunk>\n{chunk_text}\n</chunk>\n\n"
    "Write a 100-150 token context that situates this chunk within the "
    "document. Include any of the following that are present and "
    "relevant: document type (email, contract, settlement, court "
    "filing, voicemail transcript, spreadsheet, etc.), date or "
    "time-period, key parties / entities / addresses, what this "
    "specific chunk is about, and how it relates to the overall "
    "document. Plain prose, no markdown, no bullet points. Answer with "
    "only the context, nothing else."
)


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    def merge(self, other: "_Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens

    def cost_usd(self, *, in_rate: float = 3.0, out_rate: float = 15.0,
                 cache_write_rate: float = 3.75,
                 cache_read_rate: float = 0.30) -> float:
        # rates are $/M tokens; default is Sonnet 4.6 / Sonnet 4 family.
        return (
            self.input_tokens * in_rate
            + self.cache_creation_tokens * cache_write_rate
            + self.cache_read_tokens * cache_read_rate
            + self.output_tokens * out_rate
        ) / 1_000_000


class ContextualSummarizer:
    """Per-chunk context-summary generator using Anthropic Sonnet + cache."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        *,
        max_output_tokens: int = _MAX_CONTEXT_OUTPUT_TOKENS,
    ) -> None:
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required for ContextualSummarizer."
            )
        # Explicit per-request timeout. The Anthropic SDK's default (600s)
        # is generous enough that a single hung call would stall an entire
        # worker for ten minutes. 90s is long enough for a healthy 200K-
        # context Sonnet call, and the tenacity retry below will re-issue
        # if the API stalls.
        self.client = Anthropic(api_key=api_key, timeout=90.0, max_retries=0)
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.total_usage = _Usage()
        self._usage_lock = threading.Lock()

    # -----------------------------------------------------------------
    # Single-chunk path — used when doc is too small to cache, or as a
    # fallback when caching errors out.
    # -----------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _call_uncached(self, doc_text: str, chunk_text: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"<document>\n{doc_text}\n</document>\n\n"
                    + _CHUNK_INSTRUCTION.format(chunk_text=chunk_text)
                ),
            }],
        )
        self._record_usage(resp.usage)
        return self._extract_text(resp).strip()

    # -----------------------------------------------------------------
    # Cached path — used when the doc is big enough to benefit from
    # the 5-minute ephemeral cache. The first call writes the cache
    # (1.25x input rate); every subsequent call within ~5 min reads
    # it at 0.1x input rate.
    # -----------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _call_cached(self, doc_text: str, chunk_text: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_output_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"<document>\n{doc_text}\n</document>",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": _CHUNK_INSTRUCTION.format(chunk_text=chunk_text),
                    },
                ],
            }],
        )
        self._record_usage(resp.usage)
        return self._extract_text(resp).strip()

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def summarize_doc_chunks(
        self,
        doc_text: str,
        chunk_texts: List[str],
    ) -> List[str]:
        """Generate contextual summaries for every chunk of one document.

        Calls Claude *sequentially* so the prompt cache stays warm — this
        lets every chunk after the first benefit from 90% cheaper input
        tokens (cache read).

        Returns a list of strings, same length as chunk_texts. On per-
        chunk failure the corresponding entry is the empty string.
        """
        if not chunk_texts:
            return []
        if not doc_text or not doc_text.strip():
            doc_text = "(empty document)"

        # Trim doc to a sane upper limit so we never blow Anthropic's
        # 200K context window. We bias toward the start of the doc;
        # legal docs typically state subject/parties early.
        approx_tokens = len(doc_text) // 4
        if approx_tokens > _MAX_DOC_TOKENS_FOR_CONTEXT:
            doc_text = doc_text[: _MAX_DOC_TOKENS_FOR_CONTEXT * 4]
            approx_tokens = _MAX_DOC_TOKENS_FOR_CONTEXT

        use_cache = approx_tokens >= _CACHE_MIN_TOKENS and len(chunk_texts) >= 2
        call_fn = self._call_cached if use_cache else self._call_uncached

        out: List[str] = []
        for i, ct in enumerate(chunk_texts):
            try:
                summary = call_fn(doc_text, ct)
            except RetryError as exc:
                logger.warning(
                    f"  contextual summary giving up on chunk {i} "
                    f"after retries: {exc}"
                )
                summary = ""
            except Exception as exc:
                logger.warning(
                    f"  contextual summary failed on chunk {i}: "
                    f"{type(exc).__name__}: {str(exc)[:160]}"
                )
                summary = ""
            out.append(summary)
        return out

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _extract_text(resp: Any) -> str:
        parts = []
        for block in resp.content or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "\n".join(parts)

    def _record_usage(self, usage: Any) -> None:
        if usage is None:
            return
        u = _Usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
        with self._usage_lock:
            self.total_usage.merge(u)

    # Convenience for logging / reports.
    @property
    def usage_summary(self) -> Dict[str, Any]:
        u = self.total_usage
        return {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_creation_tokens": u.cache_creation_tokens,
            "cache_read_tokens": u.cache_read_tokens,
            "approx_cost_usd": round(u.cost_usd(), 4),
        }
