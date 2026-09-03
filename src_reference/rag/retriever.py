"""
Hybrid retriever:

  1. Embed the query with Voyage.
  2. Run Atlas $vectorSearch over `email_chunks.embedding` to fetch
     `RETRIEVAL_TOP_K` candidates.
  3. (Optional) merge in a literal-text $text search to catch keyword
     matches that embeddings miss (dollar amounts, file names).
  4. Rerank with Voyage rerank-2.5 down to `RERANK_TOP_K`.
  5. Return enriched candidates ready for the prompt.

Atlas filter expressions can be passed in to narrow by date / sender /
folder before vector search runs (this is much faster than retrieving
1k docs and filtering in Python).

Timeline mode: `retrieve_timeline()` returns chunks SORTED BY DATE
(ascending) instead of by similarity, and pulls a wider net so a
single chronological summary covers the whole period. This is the
right mode when the user asks for a chronological summary or any
"what happened from year X to year Y" question.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.db.mongo import MongoClientWrapper
from src.rag.embedder import VoyageEmbedder
from src.rag.reranker import VoyageReranker
from src.utils.logger import logger


def build_date_filter(
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Helper to build an Atlas $vectorSearch date filter."""
    if not date_from and not date_to:
        return None
    expr: Dict[str, Any] = {}
    if date_from:
        expr["$gte"] = date_from
    if date_to:
        expr["$lte"] = date_to
    return {"date": expr}


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    body: str
    source_type: str            # "email_body" | "attachment"
    email_id: str
    attachment_id: Optional[str]
    filename: Optional[str]
    page_start: Optional[int]
    page_end: Optional[int]
    date: Any
    from_email: Optional[str]
    to_emails: List[str]
    subject: Optional[str]
    folder_path: Optional[str]
    vector_score: Optional[float]
    rerank_score: Optional[float]
    # Option B: the same byte-identical content can live in many parent
    # emails. `occurrences` lists every parent (email_id, attachment_id,
    # filename, date, from_email, subject, ...) so the prompt builder can
    # surface "this doc was sent on N dates by M people" to Claude.
    # Length 1 for legacy v1 chunks and for unique email-body chunks.
    occurrences: List[Dict[str, Any]] = field(default_factory=list)
    # `latest_date` = max(occurrences[].date). Useful for "what's the most
    # recent time this came up?" reasoning at the chat layer.
    latest_date: Any = None
    # Cluster id (sha256 in Option B). Empty for v1 chunks.
    sha256: Optional[str] = None
    # Evidentiary spine — flow corpus / privilege / doc-source-type through so
    # the provenance footer can report a real corpus instead of "unknown".
    corpus: Optional[str] = None
    privilege_status: Optional[str] = None
    doc_source_type: Optional[str] = None


class Retriever:
    def __init__(
        self,
        mongo: MongoClientWrapper,
        embedder: VoyageEmbedder,
        reranker: VoyageReranker,
        *,
        vector_index_name: str,
        retrieval_top_k: int = 50,
        rerank_top_k: int = 8,
        v2_pipeline: Optional[Any] = None,
    ) -> None:
        self.mongo = mongo
        self.embedder = embedder
        self.reranker = reranker
        self.vector_index_name = vector_index_name
        self.retrieval_top_k = retrieval_top_k
        self.rerank_top_k = rerank_top_k
        # Optional v2 orchestrator. When set AND its settings.enabled is
        # True, retrieve() routes through the v2 pipeline. Otherwise we
        # fall through to the original v1 vector→rerank flow.
        self.v2_pipeline = v2_pipeline

    # ----- vector search -----

    def _vector_search(
        self,
        query_vec: List[float],
        atlas_filter: Optional[Dict[str, Any]] = None,
        *,
        collection: Optional[Any] = None,
        index_name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        # `limit` is call-scoped so concurrent callers (e.g. timeline mode)
        # never mutate shared instance state. Defaults to the configured
        # retrieval_top_k when not overridden.
        top_k = limit if limit is not None else self.retrieval_top_k
        stage_vector: Dict[str, Any] = {
            "$vectorSearch": {
                "index": index_name or self.vector_index_name,
                "path": "embedding",
                "queryVector": query_vec,
                "numCandidates": max(150, top_k * 5),
                "limit": top_k,
            }
        }
        if atlas_filter:
            stage_vector["$vectorSearch"]["filter"] = atlas_filter

        pipeline = [
            stage_vector,
            {
                "$project": {
                    "_id": 1,
                    "text": 1,
                    "body": 1,
                    "source_type": 1,
                    "email_id": 1,
                    "attachment_id": 1,
                    "filename": 1,
                    "page_start": 1,
                    "page_end": 1,
                    "date": 1,
                    "from_email": 1,
                    "to_emails": 1,
                    "subject": 1,
                    "folder_path": 1,
                    # Option B fan-out — harmless on v1 chunks (just absent).
                    "occurrences": 1,
                    "latest_date": 1,
                    "sha256": 1,
                    # Evidentiary spine (Sprint 2.3) — powers the provenance footer.
                    "corpus": 1,
                    "privilege_status": 1,
                    "doc_source_type": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        col = collection if collection is not None else self.mongo.chunks
        return list(col.aggregate(pipeline))

    def _v2_timeline_target(self) -> Tuple[Optional[Any], Optional[str]]:
        """When the v2 pipeline is active, return (collection, index_name)
        pointing at the v2 corpus so date-sorted retrieval reads the SAME
        enriched chunks as every other query path — not the legacy v1
        collection."""
        if self.v2_pipeline is not None and getattr(
            self.v2_pipeline.settings, "enabled", False
        ):
            try:
                col_name = self.v2_pipeline.settings.chunks_collection_name
                idx = self.v2_pipeline.hybrid_searcher.vector_index_name
                return self.mongo.db[col_name], idx
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"v2 timeline target unavailable, using v1: {exc}")
        return None, None

    # ----- public API -----

    def retrieve(
        self,
        query: str,
        atlas_filter: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievedChunk]:
        """End-to-end: embed → vector search → rerank → return chunks.

        If a v2 pipeline is attached AND enabled, route through it instead.
        On any v2 failure we transparently fall back to the v1 flow below.
        """
        if not query.strip():
            return []

        # v2 routing — fail-safe: if v2 returns empty (or raises) we fall
        # back to v1 below so production never breaks. The degrade is
        # recorded on `last_degraded` so the chat layer can SURFACE it —
        # a silent v1 fallback produces dramatically thinner answers and
        # previously looked like a model-quality problem.
        self.last_degraded: Optional[str] = None
        if self.v2_pipeline is not None and getattr(
            self.v2_pipeline.settings, "enabled", False
        ):
            try:
                v2_chunks = self.v2_pipeline.retrieve(query, atlas_filter=atlas_filter)
                if v2_chunks:
                    return v2_chunks
                self.last_degraded = "v2 returned 0 chunks"
                logger.warning("v2 pipeline returned 0 chunks — falling back to v1")
            except Exception as exc:  # noqa: BLE001
                self.last_degraded = f"v2 pipeline error: {str(exc)[:120]}"
                logger.warning(f"v2 pipeline failed, falling back to v1: {exc}")

        # ---- v1 path (default behaviour) -------------------------------
        logger.debug(f"Embedding query ({len(query)} chars)")
        qvec = self.embedder.embed_query(query)

        logger.debug(f"Running $vectorSearch (limit={self.retrieval_top_k})")
        candidates = self._vector_search(qvec, atlas_filter=atlas_filter)
        if not candidates:
            return []

        logger.debug(f"Reranking {len(candidates)} candidates → top {self.rerank_top_k}")
        docs = [c.get("text") or c.get("body") or "" for c in candidates]
        rerank_results = self.reranker.rerank(query, docs, top_k=self.rerank_top_k)

        out: List[RetrievedChunk] = []
        for r in rerank_results:
            c = candidates[r["index"]]
            out.append(_to_chunk(c, rerank_score=r["score"]))
        return out

    # ----- timeline mode -----

    def retrieve_timeline(
        self,
        query: str,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        max_chunks: int = 50,
        atlas_filter: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievedChunk]:
        """
        Timeline-mode retrieval.

        Returns chunks **sorted by date ASC**, not by similarity. We pull a
        wider net (`max_chunks`, default 50) so Claude sees enough
        spread-across-time evidence to build a full chronological summary.

        Use this for queries like:
          - "Summarize the events from 2021 to 2026"
          - "What happened to the Mango Tree settlement over time?"
          - "Build me a timeline of all communications about Fort Hill"
        """
        if not query.strip():
            return []

        # Combine user-supplied filter with date-range filter.
        date_f = build_date_filter(date_from, date_to)
        merged = {**(atlas_filter or {}), **(date_f or {})}

        logger.debug(f"Timeline retrieve: filter={merged}, top={max_chunks}")
        qvec = self.embedder.embed_query(query)

        # Timeline queries must read the v2 corpus when v2 is live —
        # previously this silently searched the stale v1 collection.
        tl_col, tl_idx = self._v2_timeline_target()

        # Increase pull size for timeline mode via a call-scoped limit —
        # never mutate self.retrieval_top_k (this retriever is a shared
        # singleton and concurrent WS sessions would race on it).
        candidates = self._vector_search(
            qvec, atlas_filter=merged or None,
            collection=tl_col, index_name=tl_idx,
            limit=max_chunks,
        )

        if not candidates:
            return []

        # Sort by date ASC (None dates last).
        def sort_key(c: Dict[str, Any]):
            d = c.get("date")
            return (d is None, d)
        candidates.sort(key=sort_key)

        # Optional rerank for relevance score, but DO NOT use rerank order.
        # (Rerank can demote dated chunks that are essential for timeline.)
        return [_to_chunk(c, rerank_score=None) for c in candidates]


def _to_chunk(c: Dict[str, Any], *, rerank_score: Optional[float]) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(c["_id"]),
        text=c.get("text") or "",
        body=c.get("body") or "",
        source_type=c.get("source_type") or "",
        email_id=str(c.get("email_id")) if c.get("email_id") else "",
        attachment_id=str(c.get("attachment_id")) if c.get("attachment_id") else None,
        filename=c.get("filename"),
        page_start=c.get("page_start"),
        page_end=c.get("page_end"),
        date=c.get("date"),
        from_email=c.get("from_email"),
        to_emails=c.get("to_emails") or [],
        subject=c.get("subject"),
        folder_path=c.get("folder_path"),
        vector_score=c.get("score"),
        rerank_score=rerank_score,
        # Option B fan-out. Pass through as-is; empty list for v1 chunks.
        occurrences=list(c.get("occurrences") or []),
        latest_date=c.get("latest_date"),
        sha256=c.get("sha256"),
        corpus=c.get("corpus"),
        privilege_status=c.get("privilege_status"),
        doc_source_type=c.get("doc_source_type"),
    )
