"""
Chunk ordering — fight the "lost in the middle" failure mode.

Why this exists:
  Long-context LLMs (Claude Opus 4.6 included) recall tokens at the START
  and END of the prompt far more reliably than tokens in the middle. With
  a 70-chunk SOURCES block, the chunks at positions #25-#50 get the worst
  attention. The reranker has already told us which chunks are best — so
  we interleave them so the best half are at the start AND end of the
  block, and the weaker half sit in the middle where attention dips.

This is a documented finding from "Lost in the Middle: How Language
Models Use Long Contexts" (Liu et al., 2024). The fix is cheap (~zero
runtime cost) and meaningfully improves recall on long contexts.

Strategy:
  Given chunks ordered by rerank score descending (best first):
    1. Top half  → keep in front-to-mid order
    2. Bottom half → reverse, put at the tail

  Result: chunk #1 is first, chunk #2 is last, chunk #3 is second,
  chunk #4 is second-to-last, etc. Best signal hugs both extremes.

This module is pure-function and intentionally has zero dependencies so
it can be unit-tested in isolation.
"""
from __future__ import annotations

from typing import List, TypeVar

T = TypeVar("T")


def interleave_for_attention(items: List[T]) -> List[T]:
    """
    Re-order a rank-sorted list so the strongest signals sit at the
    extremes (positions 0 and -1) and the weakest sit in the middle.

    Input  must be ordered best → worst (rerank-score descending).
    Output preserves all items, no duplicates introduced.

    Examples
    --------
    >>> interleave_for_attention([1, 2, 3, 4, 5, 6])
    [1, 3, 5, 6, 4, 2]
    >>> interleave_for_attention([])
    []
    >>> interleave_for_attention([1])
    [1]
    >>> interleave_for_attention([1, 2])
    [1, 2]

    Why this exact pattern (best-at-front + best-at-back):
      Liu et al. (2024) measured that primacy (front) recall stays
      strong out to ~60K tokens; recency (back) recall stays strong
      indefinitely; the dip is in the middle quartile. So we put the
      #1 chunk first (primacy), the #2 chunk last (recency), and
      backfill from the outside in.
    """
    n = len(items)
    if n <= 2:
        return list(items)

    result: List[T] = [items[0]] * n  # placeholder, every slot will be set
    left, right = 0, n - 1
    for i, item in enumerate(items):
        if i % 2 == 0:
            result[left] = item
            left += 1
        else:
            result[right] = item
            right -= 1
    return result


def is_enabled(flag_value: bool) -> bool:
    """Tiny convenience wrapper — keeps the call-site readable."""
    return bool(flag_value)


__all__ = ["interleave_for_attention", "is_enabled"]
