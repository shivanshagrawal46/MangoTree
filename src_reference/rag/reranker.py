"""
Voyage AI reranker (rerank-2.5).

Takes the top-K candidates from vector search and reorders them by
*query-specific* relevance. This single step is the highest-leverage
quality improvement in modern RAG: it routinely doubles top-3 precision.
"""
from __future__ import annotations

from typing import List, Sequence

import voyageai
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.logger import logger


def _is_token_limit_error(exc: Exception) -> bool:
    """Voyage rerank-2.5 rejects a request whose (query+documents) exceeds its
    600k-token-per-batch cap. This is deterministic — never worth retrying;
    we sub-batch instead."""
    s = str(exc).lower()
    return ("max allowed tokens" in s or "per submitted batch" in s
            or "600000" in s or "lower the number of tokens" in s)


class VoyageReranker:
    def __init__(self, api_key: str, model: str = "rerank-2.5") -> None:
        if not api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY is missing. Add it to .env before running rerank."
            )
        self.client = voyageai.Client(api_key=api_key)
        self.model = model

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_k: int = 8,
    ) -> List[dict]:
        """
        Returns a list of {"index": int, "score": float} sorted by score
        descending, length min(top_k, len(documents)).

        Happy path is a single API call (unchanged). ONLY when Voyage rejects
        the batch for exceeding its 600k-token cap do we transparently split
        the documents into sub-batches, rerank each, and merge by score — so
        no candidate is dropped and the limit can never break a query.
        """
        docs = list(documents)
        if not docs:
            return []
        try:
            return self._rerank_call(query, docs, top_k)
        except Exception as exc:  # noqa: BLE001
            if _is_token_limit_error(exc):
                logger.warning("rerank batch over 600k tokens — sub-batching "
                               f"({len(docs)} docs)")
                return self._rerank_subbatched(query, docs, top_k)
            raise

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        # retry transient errors, but NOT the deterministic token-limit error
        retry=retry_if_exception(lambda e: not _is_token_limit_error(e)),
        reraise=True,
    )
    def _rerank_call(self, query: str, documents: List[str], top_k: int) -> List[dict]:
        result = self.client.rerank(
            query=query,
            documents=documents,
            model=self.model,
            top_k=min(top_k, len(documents)),
            truncation=True,
        )
        return [
            {"index": r.index, "score": float(r.relevance_score)}
            for r in result.results
        ]

    def _rerank_subbatched(self, query: str, documents: List[str], top_k: int) -> List[dict]:
        """Split into halves (recursively if needed), rerank each, merge by
        score. Indices returned are relative to `documents`."""
        n = len(documents)
        if n <= 1:
            # a single doc that alone exceeds the cap — Voyage truncation
            # should handle it; if it still fails, surface it.
            return self._rerank_call(query, documents, top_k)
        mid = n // 2
        merged: List[dict] = []
        for offset, part in ((0, documents[:mid]), (mid, documents[mid:])):
            if not part:
                continue
            try:
                res = self._rerank_call(query, part, len(part))
            except Exception as exc:  # noqa: BLE001
                if _is_token_limit_error(exc) and len(part) > 1:
                    res = self._rerank_subbatched(query, part, len(part))
                else:
                    raise
            for r in res:
                merged.append({"index": r["index"] + offset, "score": r["score"]})
        merged.sort(key=lambda x: x["score"], reverse=True)
        return merged[: min(top_k, n)]
