"""Embeddings — Voyage ``voyage-4-large``, the single embedding space.

One model, forever. Vectors from two different models are not comparable, so
mixing them does not degrade retrieval gracefully — it silently corrupts every
similarity score in the index. The model id is therefore stored **on each chunk**,
and the indexer refuses to mix spaces rather than trusting that nobody changed
the config.

Voyage distinguishes ``input_type="document"`` from ``"query"`` and embeds each
into an asymmetric space tuned for retrieval. Using the wrong one costs real
recall, so the two entry points here are deliberately separate functions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from mangotree.config.models import EMBEDDING_DIM, EMBEDDING_MODEL
from mangotree.core.logging import logger

#: Voyage caps batches by both count and total tokens; stay well inside both.
MAX_BATCH = 96
MAX_BATCH_CHARS = 90_000


@dataclass
class EmbedStats:
    texts: int = 0
    batches: int = 0
    retries: int = 0
    failures: int = 0
    total_tokens: int = 0

    def as_dict(self) -> dict:
        return {
            "texts": self.texts, "batches": self.batches, "retries": self.retries,
            "failures": self.failures, "total_tokens": self.total_tokens,
        }


def _batch(texts: Sequence[str]) -> Iterable[List[int]]:
    """Yield index batches bounded by both count and character budget."""
    current: List[int] = []
    size = 0
    for index, text in enumerate(texts):
        length = len(text)
        if current and (len(current) >= MAX_BATCH or size + length > MAX_BATCH_CHARS):
            yield current
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        yield current


class Embedder:
    def __init__(self, api_key: str, *, model: str = EMBEDDING_MODEL):
        import voyageai

        # Retries on: query embeddings for several concurrent answers share one
        # rate limit, and a 429 with zero retries fails the vector channel outright.
        self.client = voyageai.Client(api_key=api_key, max_retries=6, timeout=120)
        self.model = model
        self.stats = EmbedStats()

    # ------------------------------------------------------------------
    def _call(self, texts: List[str], input_type: str, attempts: int = 5) -> List[List[float]]:
        last: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                result = self.client.embed(texts, model=self.model, input_type=input_type)
                self.stats.total_tokens += getattr(result, "total_tokens", 0) or 0
                return result.embeddings
            except Exception as exc:
                last = exc
                message = str(exc).lower()
                if "rate" in message or "429" in message or "timeout" in message:
                    self.stats.retries += 1
                    time.sleep(min(30, 2 ** attempt) + 0.5)
                    continue
                raise
        raise RuntimeError(f"Voyage embed failed after {attempts} attempts: {last}")

    # ------------------------------------------------------------------
    def embed_documents(self, texts: Sequence[str]) -> List[Optional[List[float]]]:
        """Embed chunk texts. Returns ``None`` in place of any failed batch so a
        single bad batch never shifts the alignment of the whole list."""
        vectors: List[Optional[List[float]]] = [None] * len(texts)

        for indices in _batch(texts):
            batch = [texts[i] for i in indices]
            self.stats.batches += 1
            try:
                for position, vector in zip(indices, self._call(batch, "document")):
                    if len(vector) != EMBEDDING_DIM:
                        raise ValueError(
                            f"expected {EMBEDDING_DIM} dims, got {len(vector)} "
                            f"— wrong model in the index"
                        )
                    vectors[position] = vector
                    self.stats.texts += 1
            except Exception as exc:
                self.stats.failures += len(indices)
                logger.error("Embedding batch of %d failed: %s", len(indices), exc)

        return vectors

    def embed_query(self, text: str) -> List[float]:
        """Embed a query. Asymmetric to ``embed_documents`` by design."""
        return self._call([text], "query")[0]


def build_header_line(
    *,
    filename: str,
    property_label: Optional[str],
    doc_class: Optional[str],
    source_ref: str,
    date_hint: Optional[str] = None,
) -> str:
    """The deterministic header field of a chunk's embedded context.

    This is the ``header`` in ``Tier1 + Tier2 + header + chunk`` — the exact,
    structured facts we already hold, so they are stated rather than generated.
    It is **not** a contextual summary and must not be mistaken for one: it says
    where a chunk came from, never what the chunk means inside its document. That
    job belongs to Tier 1, which requires a model (see ``context/tier1.py``).
    """
    parts: List[str] = []
    if property_label:
        parts.append(f"Property: {property_label}")
    if doc_class:
        parts.append(f"Document: {doc_class.replace('_', ' ')}")
    if filename:
        parts.append(f"File: {filename}")
    if source_ref:
        parts.append(f"Location: {source_ref}")
    if date_hint:
        parts.append(f"Date: {date_hint}")
    return " | ".join(parts)
