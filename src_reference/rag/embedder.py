"""
Voyage AI embedder with retry + batching + free-tier rate-limiting.

Rate limits without a payment method on Voyage:
    3 RPM   (requests per minute)
    10K TPM (tokens per minute)

We enforce both with a sliding-window limiter so a long ingestion run
stays well under the cap and never triggers a 429.

We use:
  - Documents:   `input_type="document"`
  - Queries:     `input_type="query"`
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, List, Optional, Tuple

import voyageai
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.logger import logger


# Voyage Free-tier (no card):  3 RPM / 10K TPM.
# With a card it's 2000 RPM / 1M TPM. Override via env if needed.
import os as _os
_FREE_RPM = int(_os.environ.get("VOYAGE_RPM", "3"))
_FREE_TPM = int(_os.environ.get("VOYAGE_TPM", "10000"))
# Hard request caps from the API itself.
_MAX_BATCH = 128
_MAX_TOKENS_PER_REQUEST = 9000  # conservatively under 10K to leave headroom


class _SlidingRateLimiter:
    """
    Enforces  R requests / 60s  AND  T tokens / 60s.

    Thread-safe; blocks on `acquire(n_tokens)` until both budgets allow.
    """

    def __init__(self, rpm: int, tpm: int) -> None:
        self.rpm = rpm
        self.tpm = tpm
        # (timestamp, tokens) of every request in the last 60s
        self._events: Deque[Tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def acquire(self, n_tokens: int) -> None:
        if n_tokens > self.tpm:
            # Should never happen if we respect _MAX_TOKENS_PER_REQUEST.
            raise ValueError(
                f"Single batch ({n_tokens} tok) exceeds TPM cap ({self.tpm})."
            )
        while True:
            now = time.monotonic()
            with self._lock:
                # Drop events older than 60 seconds.
                cutoff = now - 60.0
                while self._events and self._events[0][0] < cutoff:
                    self._events.popleft()

                cur_reqs = len(self._events)
                cur_tokens = sum(t for _, t in self._events)

                if cur_reqs < self.rpm and cur_tokens + n_tokens <= self.tpm:
                    self._events.append((now, n_tokens))
                    return

                # Compute the soonest moment at which BOTH constraints clear.
                req_wait = (
                    self._events[0][0] + 60.0 - now
                    if cur_reqs >= self.rpm and self._events
                    else 0.0
                )

                tok_wait = 0.0
                if cur_tokens + n_tokens > self.tpm and self._events:
                    excess = (cur_tokens + n_tokens) - self.tpm
                    spent = 0
                    for ts, tk in self._events:
                        spent += tk
                        if spent >= excess:
                            tok_wait = ts + 60.0 - now
                            break

                wait = max(req_wait, tok_wait, 0.05) + 0.1
            logger.info(
                f"Voyage rate-limit pause {wait:.1f}s "
                f"(reqs={cur_reqs}/{self.rpm}, tokens={cur_tokens}/{self.tpm})"
            )
            time.sleep(wait)


class VoyageEmbedder:
    """Stateful, retrying embedder. Pass `api_key` once on construction."""

    def __init__(self, api_key: str, model: str = "voyage-4-large") -> None:
        if not api_key:
            raise RuntimeError(
                "VOYAGE_API_KEY is missing. Add it to .env before running embeddings."
            )
        self.client = voyageai.Client(api_key=api_key)
        self.model = model
        self._limiter = _SlidingRateLimiter(rpm=_FREE_RPM, tpm=_FREE_TPM)
        logger.info(
            f"Voyage embedder ready (model={model}, rpm={_FREE_RPM}, tpm={_FREE_TPM})"
        )

    # ----- core single batch (with retry on transient errors) -----

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=10, max=120),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _embed_batch(
        self, texts: List[str], input_type: str, n_tokens: int
    ) -> List[List[float]]:
        self._limiter.acquire(n_tokens)
        response = self.client.embed(
            texts,
            model=self.model,
            input_type=input_type,
            truncation=True,
        )
        return response.embeddings

    # ----- public APIs -----

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of document chunks (`input_type='document'`)."""
        return self._embed(texts, input_type="document")

    def embed_query(self, text: str) -> List[float]:
        """Embed a single user query (`input_type='query'`)."""
        return self._embed([text], input_type="query")[0]

    # ----- batching -----

    def _count_tokens_safely(self, text: str) -> int:
        try:
            return int(self.client.count_tokens([text], model=self.model))
        except Exception:
            return max(1, len(text) // 4)

    def _embed(self, texts: List[str], input_type: str) -> List[List[float]]:
        if not texts:
            return []

        tokens_per_text = [self._count_tokens_safely(t) for t in texts]
        out: List[List[float]] = [None] * len(texts)  # type: ignore[list-item]

        batch: List[int] = []
        batch_tokens = 0
        for i, n in enumerate(tokens_per_text):
            # If a single text is bigger than the per-request cap, send it
            # alone (the API will truncate to its 32K context).
            n_capped = min(n, _MAX_TOKENS_PER_REQUEST)
            if batch and (
                len(batch) >= _MAX_BATCH
                or batch_tokens + n_capped > _MAX_TOKENS_PER_REQUEST
            ):
                self._dispatch(texts, batch, batch_tokens, input_type, out)
                batch, batch_tokens = [], 0
            batch.append(i)
            batch_tokens += n_capped
        if batch:
            self._dispatch(texts, batch, batch_tokens, input_type, out)

        return out  # type: ignore[return-value]

    def _dispatch(
        self,
        texts: List[str],
        idxs: List[int],
        n_tokens: int,
        input_type: str,
        out: List[Optional[List[float]]],
    ) -> None:
        sub = [texts[i] for i in idxs]
        embs = self._embed_batch(sub, input_type=input_type, n_tokens=n_tokens)
        for slot, emb in zip(idxs, embs):
            out[slot] = emb
