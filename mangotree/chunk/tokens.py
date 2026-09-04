"""Token counting for chunk budgets.

The admin specified chunks of 1000 tokens with 200 of overlap, so the budget has
to be measured in tokens rather than the characters the chunker previously used.

Which tokenizer
---------------
``cl100k_base`` is a stand-in, not the exact tokenizer of any model we call.
Voyage does not publish a local tokenizer and Anthropic's only counts over the
network, which is far too slow for per-chunk decisions. Across English prose the
three agree to within a few percent, and the budget is a retrieval-quality knob
rather than a hard API limit, so a close local proxy is the right trade.

If tiktoken is unavailable the fallback estimates 4 characters per token. That
keeps ingestion running, but chunk sizes drift, so it says so loudly once.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Optional

from mangotree.core.logging import logger

_CHARS_PER_TOKEN = 4
_warned = False


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        global _warned
        if not _warned:
            logger.warning(
                "tiktoken unavailable (%s) — chunk budgets fall back to a "
                "%d-chars-per-token estimate", exc, _CHARS_PER_TOKEN,
            )
            _warned = True
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoding = _encoding()
    if encoding is None:
        return max(1, len(text) // _CHARS_PER_TOKEN)
    return len(encoding.encode(text, disallowed_special=()))


def truncate_to_tokens(text: str, limit: int) -> str:
    """First ``limit`` tokens of ``text``."""
    if limit <= 0 or not text:
        return ""
    encoding = _encoding()
    if encoding is None:
        return text[: limit * _CHARS_PER_TOKEN]
    tokens = encoding.encode(text, disallowed_special=())
    if len(tokens) <= limit:
        return text
    return encoding.decode(tokens[:limit])


_SENTENCE_START = re.compile(r"(?<=[.!?])\s+")


def tail_tokens(text: str, limit: int, *, snap_to_sentence: bool = True) -> str:
    """Last ``limit`` tokens, used as the overlap carried into the next chunk.

    Slicing a token array mid-sentence produces an overlap that starts partway
    through a clause, which reads as garbled context to both the embedding model
    and a human auditing the chunk. So the cut is nudged forward to the nearest
    sentence boundary when one exists in the window — trading a few tokens of
    overlap for a fragment that stands on its own.
    """
    if limit <= 0 or not text:
        return ""
    encoding = _encoding()
    if encoding is None:
        window = text[-(limit * _CHARS_PER_TOKEN):]
    else:
        tokens = encoding.encode(text, disallowed_special=())
        if len(tokens) <= limit:
            window = text
        else:
            window = encoding.decode(tokens[-limit:])

    if not snap_to_sentence:
        return window.strip()

    parts = _SENTENCE_START.split(window, maxsplit=1)
    # Only snap when a whole sentence survives; otherwise keep the raw window
    # rather than shrinking the overlap to almost nothing.
    if len(parts) == 2 and count_tokens(parts[1]) >= limit // 2:
        return parts[1].strip()
    return window.strip()


def split_by_tokens(text: str, limit: int) -> List[str]:
    """Break text into pieces of at most ``limit`` tokens, on sentence bounds."""
    if count_tokens(text) <= limit:
        return [text] if text.strip() else []

    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: List[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and count_tokens(candidate) > limit:
            out.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current.strip():
        out.append(current.strip())

    # A single sentence longer than the budget still has to be cut somewhere.
    final: List[str] = []
    for piece in out:
        while count_tokens(piece) > limit:
            head = truncate_to_tokens(piece, limit)
            final.append(head)
            piece = piece[len(head):].strip()
        if piece:
            final.append(piece)
    return final
