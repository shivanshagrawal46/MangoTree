"""Model registry — the seats from docs/01-AI-MODEL-STACK.md, pinned.

Verified available on this key (2026-08-30):
    claude-opus-5 · claude-sonnet-5 · claude-fable-5 · claude-sonnet-4-6

Admin directives encoded here:
* **OCR is Sonnet 4.6**, not Sonnet 5 — Sonnet 5 regressed on document extraction.
* **No Gemini, no Haiku** anywhere in the stack.
* **Voyage is the sole embedding model.** One embedding space, forever: vectors
  from two models are not comparable, so mixing them silently corrupts every
  similarity score in the index.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Seat(str, Enum):
    MANAGER = "manager"              # orchestration, expert-panel chair, verdicts
    ANALYST = "analyst"              # deep analysis, producer, stage-2 rerank
    CRITIC = "critic"                # high-recall critic + answer writer
    OCR = "ocr"                      # page-level vision extraction
    WORKHORSE = "workhorse"          # contextual summaries, high-volume work
    OCR_ESCALATION = "ocr_escalation"  # hard pages the primary could not read
    FINANCE = "finance"              # money ledger + daily Wes agenda (admin directive 2026-09-03)


#: seat -> pinned model id
MODELS = {
    Seat.MANAGER: "claude-fable-5",
    Seat.FINANCE: "claude-fable-5-1",   # verified on this key 2026-09-03
    Seat.ANALYST: "claude-opus-5",
    Seat.CRITIC: "gpt-5.6",              # provider diversity; not yet wired
    Seat.OCR: "claude-sonnet-4-6",
    Seat.WORKHORSE: "claude-sonnet-5",
    Seat.OCR_ESCALATION: "claude-opus-5",
}

#: The one embedding model. Changing this invalidates the entire index.
#: Verified available at 1024 dimensions on this key (2026-08-30).
EMBEDDING_MODEL = "voyage-4-large"
EMBEDDING_DIM = 1024

#: Voyage reranker — first stage. Opus 5 (the ANALYST seat) is stage two.
RERANK_MODEL = "rerank-2.5"

#: Never route to these, per admin directive.
FORBIDDEN_SUBSTRINGS = ("gemini", "haiku")


def model_for(seat: Seat) -> str:
    model = MODELS[seat]
    lowered = model.lower()
    if any(bad in lowered for bad in FORBIDDEN_SUBSTRINGS):
        raise RuntimeError(f"Model '{model}' is excluded by directive (seat={seat.value})")
    return model


@dataclass(frozen=True)
class OCRConfig:
    model: str = MODELS[Seat.OCR]
    #: Retained so previously-escalated pages still validate against
    #: ``ALLOWED_ENGINES``, but no longer part of the live cascade: a poorly-read
    #: page now goes straight to GPT-5, per the admin's two-engine directive of
    #: 2026-09-02.
    escalation_model: str = MODELS[Seat.OCR_ESCALATION]
    #: Cross-provider tier. Reads pages Anthropic's content policy refuses —
    #: title reports and policies, which are dense with personal identifiers.
    #: A same-provider retry returns the identical refusal, so this seat cannot
    #: be filled by another Claude model.
    openai_model: str = "gpt-5"
    #: A dense legal page can run past 4k tokens of verbatim text; truncating it
    #: loses real content, so the budget is set above the worst case observed.
    max_output_tokens: int = 8000
    #: Pages rendered at this DPI before being sent to vision.
    render_dpi: int = 200
    #: Longest edge in pixels; larger costs more without reading better.
    max_edge_px: int = 1600
    #: Below this self-reported confidence a page is escalated.
    confidence_floor: float = 0.75
    #: Pages read in parallel. The run is latency-bound, not compute-bound: a
    #: dense page spends 15-25s waiting on the model, so this is wall-clock time
    #: bought directly. Per-call backoff absorbs the throttling this provokes.
    concurrency: int = 10


OCR = OCRConfig()


@dataclass(frozen=True)
class ContextConfig:
    """Tier-1 contextual summaries — the retrieval quality lever.

    A chunk lifted out of a 40-page title policy reads as an orphan: "approved,
    proceed with recording" matches nothing and means nothing. Tier 1 prepends
    one or two sentences that situate the chunk inside its own document, and that
    line is embedded with the chunk. It is the single highest-leverage retrieval
    improvement available to us, and it requires a model — no template can say
    what a passage *means* in its document.
    """
    model: str = MODELS[Seat.WORKHORSE]
    #: Chunks summarised per call. The document sits in a prompt-cached prefix,
    #: so the marginal cost of a call is the cache read plus a short completion;
    #: batching mainly buys wall-clock time. Small enough that the model keeps
    #: every excerpt distinct in view, and small enough that a batch comfortably
    #: fits ``max_output_tokens`` — measured at ~150 output tokens per summary.
    batch_size: int = 8
    #: Documents processed in parallel. Caching is per-document, so parallelism
    #: across documents never causes cache misses within one.
    concurrency: int = 8
    #: Batches run in parallel *within* one document, after its first batch has
    #: written the cache entry. A 163-chunk title package is 21 batches; running
    #: them serially made one document a 3-minute job and dominated the whole
    #: run. The first batch is always alone — firing batches at a cold cache
    #: would have each of them pay the full document price instead of a tenth.
    batch_concurrency: int = 4
    #: Hard ceiling on the cached document prefix. Beyond this the document is
    #: windowed around each batch instead — a 300-page title report would
    #: otherwise cost more to cache than the summaries are worth.
    max_document_chars: int = 180_000
    #: Tier-1 output budget per batch. Sized generously against the measured
    #: ~150 tokens per summary: running out mid-list is the one failure mode that
    #: costs a whole batch, and headroom is far cheaper than a retry.
    max_output_tokens: int = 4000
    #: Word ceiling per context line, set to land at the **~150 output tokens**
    #: the admin specified (2026-09-02). Measured on this corpus a Tier-1 line
    #: tokenises at ~2.5 tokens/word — these sentences are dense with addresses,
    #: dollar amounts and party names, so they run well above the usual 1.3 —
    #: which puts 150 tokens at ~60 words.
    #:
    #: The ratio to the chunk matters more than the absolute number. Against the
    #: 1000-token chunk budget, 150 tokens of context is ~13% of the embedded
    #: vector: enough to make an orphaned passage findable, small enough that the
    #: context cannot outvote the passage itself in the similarity score.
    max_words: int = 60
    #: Bumped when the Tier-1 prompt changes, so stale summaries are detectable
    #: and re-generated rather than silently mixed with new ones. v2 widened the
    #: line from ~127 to ~150 tokens.
    prompt_version: str = "tier1-v2"


CONTEXT = ContextConfig()
