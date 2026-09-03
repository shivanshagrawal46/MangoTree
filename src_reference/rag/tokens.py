"""
Token counting helpers.

Voyage AI's tokenizer is not exposed publicly, but tiktoken's `cl100k_base`
is a very close proxy (within ~5% on English legal text). We keep one
encoder instance per process and expose `count_tokens()` and
`split_to_tokens()`.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

import tiktoken


@lru_cache(maxsize=1)
def _enc():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(_enc().encode(text, disallowed_special=()))


def encode(text: str) -> List[int]:
    return _enc().encode(text, disallowed_special=())


def decode(tokens: List[int]) -> str:
    return _enc().decode(tokens)
