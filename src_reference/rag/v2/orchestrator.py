"""
v2 Retrieval Orchestrator.

This is the high-level coordinator that runs the full Sprint 1 + 2
retrieval pipeline. It is the SINGLE entry-point that `retriever.py`
calls when `RAG_V2_ENABLED=true`.

Pipeline:
  1.  Query understanding   — extract dates, $, names, filenames, intent
  2.  Query rewriting       — HyDE + multi-query (Sonnet 4.6)
  3.  Embed all query forms — single Voyage call (batched)
  4.  Hybrid search         — vector × M + BM25 + filename direct lookup
                              fused with Reciprocal Rank Fusion
  5.  Temporal diversification — ensure newest version of facts retained
  6.  Re-scoring            — recency × authority × exact-match boost
  7.  Final cap              — top-K (adaptive based on query complexity)
  8.  Reranker (Voyage rerank-2.5) — final relevance pass on top-K
  9.  Parent-document expand — pull full doc context when chunks cluster

Output: a list of RetrievedChunk objects (same shape as v1) so the chat
layer doesn't care which version produced them.

Fail-safe: every stage is wrapped in try/except. If anything fails the
caller gets back whatever the last successful stage produced. If the
WHOLE pipeline collapses we return [], and the chat layer falls back
to v1 behaviour.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import anthropic

from src.db.mongo import MongoClientWrapper
from src.rag.embedder import VoyageEmbedder
from src.rag.reranker import VoyageReranker
from src.rag.retriever import RetrievedChunk, _to_chunk
from src.rag.v2.hybrid_search import HybridSearcher, ensure_v2_text_index
from src.rag.v2.ordering import interleave_for_attention
from src.rag.v2.parent_doc import parent_document_expand, neighbor_expand
from src.rag.v2.query_rewriter import QueryRewriter, RewrittenQuery
from src.rag.v2.query_understanding import QuerySignals, extract_signals
from src.rag.v2.temporal import (
    ScoredChunk,
    diversify,
    rescore,
    temporal_diversify,
)
from src.utils.logger import logger


# ---------------------------------------------------------------------------
# Settings container — passes configuration in via DI to avoid global state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class V2Settings:
    """Runtime configuration for the v2 pipeline. Built from `Settings`."""

    enabled: bool = False

    # Sprint 1 toggles
    hybrid_search: bool = False
    filename_lookup: bool = False
    hyde: bool = False
    multi_query: bool = False
    date_filters: bool = False

    # Sprint 2 toggles
    parent_doc: bool = False
    temporal_diversity: bool = False
    adaptive_k: bool = False
    rescoring: bool = False

    # Sprint 2.5 levers
    full_doc_mode: bool = False
    interleaved_ordering: bool = False

    # Tunables — channels
    rrf_k: int = 60
    rrf_fused_cap: int = 200
    vector_top_k: int = 150
    vector_min_score: float = 0.0   # Vector recall floor (0.0 = off)
    bm25_top_k: int = 100
    phrase_top_k: int = 80
    body_regex_top_k: int = 80
    filename_top_k: int = 50
    max_per_cluster: int = 5
    max_alt_queries: int = 3

    # Tunables — adaptive K
    rerank_top_k_default: int = 70
    adaptive_k_simple: int = 50
    adaptive_k_complex: int = 70
    adaptive_k_comprehensive: int = 80

    # Tunables — parent doc
    parent_doc_token_budget: int = 8000
    parent_doc_max_parents: int = 5
    parent_doc_max_per_parent: int = 20

    # Neighbor expansion (chunk-boundary miss guard) — on by default; fires on
    # single hits, pulls chunk_index ±window from the same parent doc.
    neighbor_expand: bool = True
    neighbor_expand_window: int = 1
    neighbor_expand_max_added: int = 40

    # Tunables — full-doc mode
    full_doc_token_budget: int = 50_000
    full_doc_max_docs: int = 4

    # Safety — overall evidence ceiling (Opus 4.6 multi-needle safe zone)
    total_evidence_cap_tokens: int = 100_000

    # Models
    query_rewriter_model: str = "claude-sonnet-4-6"

    # Sprint 7.1 — LLM-as-reranker (Opus final relevance pass on top-N)
    llm_reranker: bool = False
    llm_reranker_model: str = "claude-opus-4-8"
    llm_reranker_top_n: int = 50
    llm_reranker_effort: str = "high"

    # Option B: name of the chunks collection. v1 → "email_chunks",
    # Option B v2 → "email_chunks_v2". Settable so we can A/B without
    # code changes.
    chunks_collection_name: str = "email_chunks_v2"

    @property
    def any_query_rewrite_active(self) -> bool:
        return self.enabled and (self.hyde or self.multi_query)


# ---------------------------------------------------------------------------
# Pipeline output
# ---------------------------------------------------------------------------

@dataclass
class V2Pipeline:
    """Convenience holder for the v2 components — built once per process."""

    mongo: MongoClientWrapper
    embedder: VoyageEmbedder
    reranker: VoyageReranker
    anthropic_client: anthropic.Anthropic
    hybrid_searcher: HybridSearcher
    query_rewriter: QueryRewriter
    settings: V2Settings
    llm_reranker_obj: Any = None

    @classmethod
    def build(
        cls,
        *,
        mongo: MongoClientWrapper,
        embedder: VoyageEmbedder,
        reranker: VoyageReranker,
        anthropic_client: anthropic.Anthropic,
        vector_index_name: str,
        v2_settings: V2Settings,
    ) -> "V2Pipeline":
        # Ensure the BM25 text index exists if hybrid is on (no-op otherwise).
        if v2_settings.hybrid_search:
            ensure_v2_text_index(
                mongo,
                collection_name=v2_settings.chunks_collection_name,
            )

        hybrid = HybridSearcher(
            mongo=mongo,
            vector_index_name=vector_index_name,
            rrf_k=v2_settings.rrf_k,
            vector_top_k=v2_settings.vector_top_k,
            bm25_top_k=v2_settings.bm25_top_k,
            phrase_top_k=v2_settings.phrase_top_k,
            body_regex_top_k=v2_settings.body_regex_top_k,
            filename_top_k=v2_settings.filename_top_k,
            chunks_collection_name=v2_settings.chunks_collection_name,
            min_score=v2_settings.vector_min_score,
        )
        rewriter = QueryRewriter(
            client=anthropic_client,
            model=v2_settings.query_rewriter_model,
            max_alt_queries=v2_settings.max_alt_queries,
        )
        llm_rr = None
        if v2_settings.llm_reranker:
            from src.rag.v2.llm_reranker import LLMReranker
            llm_rr = LLMReranker(anthropic_client, model=v2_settings.llm_reranker_model,
                                 top_n=v2_settings.llm_reranker_top_n,
                                 effort=v2_settings.llm_reranker_effort)
        return cls(
            mongo=mongo,
            embedder=embedder,
            reranker=reranker,
            anthropic_client=anthropic_client,
            hybrid_searcher=hybrid,
            query_rewriter=rewriter,
            settings=v2_settings,
            llm_reranker_obj=llm_rr,
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        atlas_filter: Optional[Dict[str, Any]] = None,
    ) -> List[RetrievedChunk]:
        """
        Full v2 pipeline. Returns RetrievedChunk objects for the chat layer.

        The atlas_filter (if any) is honoured throughout — both for vector
        search and BM25/filename channels.
        """
        s = self.settings
        if not s.enabled:
            return []  # caller falls back to v1

        if not query or not query.strip():
            return []

        # ---- 1. Signals -------------------------------------------------
        sigs = extract_signals(query)

        # Merge user-provided filter with any date filter we extracted.
        # Option B: by default we filter on `occurrences.date` (any
        # appearance in the range); for creation-verb queries we flip to
        # the top-level `date` (PRIMARY = earliest occurrence ≈ creation).
        merged_filter = _merge_filters(
            atlas_filter,
            _date_filter_from_signals(sigs) if s.date_filters else None,
        )

        # ---- 2. Query rewriting (HyDE + multi-query) -------------------
        rewritten = self._rewrite_query(query, sigs)

        # ---- 3. Embed all query forms ----------------------------------
        all_queries = rewritten.all_query_strings(include_hyde=s.hyde)
        if not all_queries:
            all_queries = [query]
        try:
            query_vectors = [self.embedder.embed_query(q) for q in all_queries]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"v2 embed_query failed: {exc}")
            return []

        # ---- 4. Hybrid search -----------------------------------------
        # text_queries used by BM25: original query + alt queries
        text_queries = (
            [rewritten.original] + list(rewritten.alt_queries)
            if s.multi_query
            else [rewritten.original]
        )
        if not s.hybrid_search:
            # Hybrid disabled → only the vector channel(s).
            text_queries = []

        filenames_for_lookup = (
            list(sigs.filenames) + list(sigs.quoted_strings)
            if s.filename_lookup else []
        )

        # High-precision literal tokens — these MUST be matched verbatim
        # because MongoDB's BM25 tokenizer strips $/comma/hyphen and would
        # silently return garbage. Regex (body_substrings) is the safety
        # net; quoted-phrase BM25 is the ranked variant.
        # Only populated when hybrid_search is on (otherwise vector-only).
        body_substrings: List[str] = []
        exact_phrases: List[str] = []
        if s.hybrid_search:
            for token in (
                list(sigs.money_terms)
                + list(sigs.case_numbers)
                + [f"Dkt. {n}" for n in sigs.docket_numbers]
                + list(sigs.quoted_strings)
            ):
                t = token.strip()
                if not t or len(t) < 2:
                    continue
                body_substrings.append(t)
                exact_phrases.append(t)

        try:
            hybrid_result = self.hybrid_searcher.search(
                query_vectors=query_vectors if s.hybrid_search or len(query_vectors) > 1 else query_vectors[:1],
                text_queries=text_queries,
                filenames=filenames_for_lookup,
                body_substrings=body_substrings,
                exact_phrases=exact_phrases,
                atlas_filter=merged_filter,
                final_limit=s.rrf_fused_cap,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"v2 hybrid_search failed: {exc}")
            return []

        candidates: List[Dict[str, Any]] = hybrid_result.chunks
        base_scores: Dict[str, float] = hybrid_result.scores

        if not candidates:
            return []

        # ---- 5. Re-scoring + diversification ---------------------------
        if s.rescoring:
            scored = rescore(
                candidates,
                base_scores=base_scores,
                keyword_boost_terms=sigs.keyword_boost_terms,
            )
        else:
            # Use base RRF scores as-is, no boosts.
            scored = rescore(
                candidates,
                base_scores=base_scores,
                keyword_boost_terms=(),
                enable_recency=False,
                enable_authority=False,
                enable_exact_match=False,
            )

        # Source-level diversification (cap per-document clusters).
        scored = diversify(
            scored,
            max_per_cluster=s.max_per_cluster,
            final_limit=s.rrf_fused_cap,
        )

        # Temporal diversification — only for compare / timeline queries
        # where we explicitly want time-spread evidence.
        if s.temporal_diversity and sigs.primary_intent() in (
            "compare", "timeline"
        ):
            scored = temporal_diversify(scored, final_limit=s.rrf_fused_cap)

        # ---- 6. Adaptive K --------------------------------------------
        rerank_k = self._adaptive_k(sigs)

        # ---- 7. External reranker (Voyage rerank-2.5) -----------------
        # Hand the reranker the top candidates; it picks the final K.
        candidate_docs = [scored_chunk.doc for scored_chunk in scored][: max(rerank_k * 3, 30)]
        if not candidate_docs:
            return []

        try:
            texts = [c.get("text") or c.get("body") or "" for c in candidate_docs]
            rerank_results = self.reranker.rerank(query, texts, top_k=rerank_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"v2 reranker failed (using pre-rerank order): {exc}")
            rerank_results = [
                {"index": i, "score": 0.0} for i in range(min(rerank_k, len(candidate_docs)))
            ]

        # Materialise ordered post-rerank chunks (best → worst).
        ordered_docs: List[Dict[str, Any]] = []
        rerank_score_map: Dict[str, float] = {}
        for r in rerank_results:
            i = r["index"]
            if 0 <= i < len(candidate_docs):
                doc = candidate_docs[i]
                ordered_docs.append(doc)
                rerank_score_map[str(doc.get("_id"))] = float(r.get("score", 0.0))

        # ---- 7.5 LLM-as-reranker (Sprint 7.1, Opus final pass) --------
        if self.llm_reranker_obj is not None and len(ordered_docs) > 1:
            try:
                new_order = self.llm_reranker_obj.rerank(query, ordered_docs)
                ordered_docs = [ordered_docs[i] for i in new_order]
                logger.info(f"v2 LLM-reranker reordered top {self.settings.llm_reranker_top_n}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"LLM-reranker skipped: {exc}")

        # ---- 8. Full-document mode (Sprint 2.5 Lever 4) ---------------
        # If the query explicitly names a document, pull the entire doc.
        # The per-doc budget scales down as more docs are named.
        if (
            s.full_doc_mode
            and filenames_for_lookup
            and ordered_docs  # only meaningful if normal retrieval succeeded
        ):
            try:
                full_chunks = self._full_doc_expand(
                    filenames=filenames_for_lookup,
                    atlas_filter=merged_filter,
                )
                if full_chunks:
                    ordered_docs = _merge_dedup_preserve_first(
                        ordered_docs, full_chunks
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"v2 full_doc_expand failed: {exc}")

        # ---- 9. Parent document expansion -----------------------------
        if s.parent_doc and ordered_docs:
            try:
                exp = parent_document_expand(
                    self.mongo,
                    retrieved_chunks=ordered_docs,
                    max_chunks_per_parent=s.parent_doc_max_per_parent,
                    max_parents=s.parent_doc_max_parents,
                    token_budget_single=s.parent_doc_token_budget,
                )
                ordered_docs = exp.chunks
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"v2 parent_doc expand failed: {exc}")

        # ---- 9.5 Neighbor expansion (chunk-boundary miss guard) -------
        # Pull the immediate neighbors of every hit so a fact split across a
        # chunk boundary (e.g. a lien amount continuing into the next chunk)
        # is never silently lost. Fires on single hits (parent_doc needs 2+),
        # additive, fail-safe; the evidence cap below trims any overflow.
        if getattr(s, "neighbor_expand", True) and ordered_docs:
            try:
                ordered_docs = neighbor_expand(
                    self.mongo,
                    retrieved_chunks=ordered_docs,
                    window=getattr(s, "neighbor_expand_window", 1),
                    max_added=getattr(s, "neighbor_expand_max_added", 40),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"v2 neighbor_expand failed: {exc}")

        # ---- 10. Smart interleaved ordering ---------------------------
        # "Lost in the middle" mitigation: put strongest signals at the
        # extremes (front + back). The reranker has already given us
        # best-first order; this re-shapes that order without dropping
        # anything.
        if s.interleaved_ordering and len(ordered_docs) >= 3:
            ordered_docs = interleave_for_attention(ordered_docs)

        # ---- 11. Hard evidence cap (Opus 4.6 multi-needle safe zone) --
        if s.total_evidence_cap_tokens > 0:
            ordered_docs = _cap_by_tokens(
                ordered_docs, max_tokens=s.total_evidence_cap_tokens
            )

        # ---- 12. Convert to RetrievedChunk -----------------------------
        out: List[RetrievedChunk] = []
        for doc in ordered_docs:
            cid = str(doc.get("_id"))
            out.append(_to_chunk(doc, rerank_score=rerank_score_map.get(cid)))
        logger.info(
            f"v2 retrieve: signals={sigs.primary_intent()} "
            f"complex={sigs.is_complex()} comp={sigs.is_comprehensive()} "
            f"channels={hybrid_result.channels_used} "
            f"candidates={len(candidates)} → rerank_k={rerank_k} → final={len(out)}"
        )
        return out

    def _full_doc_expand(
        self,
        *,
        filenames: Sequence[str],
        atlas_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Pull whole document(s) when the user named them, with adaptive
        per-doc budget. With 1 doc named we use full_doc_token_budget;
        with 2 docs we halve it; with 3 we use ~1/3; capped at max_docs.
        """
        s = self.settings
        # De-dupe filename hints so we don't waste expansion slots.
        seen: set = set()
        unique: List[str] = []
        for f in filenames:
            k = (f or "").strip().lower()
            if k and k not in seen:
                seen.add(k)
                unique.append(f)
        if not unique:
            return []

        n = min(len(unique), s.full_doc_max_docs)
        per_doc_budget = max(
            int(s.full_doc_token_budget / max(1, n)),
            8_000,  # never starve a named doc below ~8K tokens
        )

        try:
            return self.hybrid_searcher.full_doc_search(
                filenames=unique[:n],
                atlas_filter=atlas_filter,
                per_doc_token_budget=per_doc_budget,
                max_docs=n,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"v2 full_doc_search failed: {exc}")
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _rewrite_query(self, query: str, sigs: QuerySignals) -> RewrittenQuery:
        """Run the query rewriter only if the caller turned on HyDE/multi-query."""
        s = self.settings
        if not s.any_query_rewrite_active:
            return RewrittenQuery(original=query)
        try:
            return self.query_rewriter.rewrite(
                query,
                enable_hyde=s.hyde,
                enable_multi_query=s.multi_query,
                context_hint=_context_hint_from_signals(sigs),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"v2 query_rewriter failed: {exc}")
            return RewrittenQuery(original=query)

    def _adaptive_k(self, sigs: QuerySignals) -> int:
        """
        Pick top-K based on query complexity.

        Three tiers:
          • Comprehensive  ("all/every", 4+ signals)  → adaptive_k_comprehensive
          • Complex        (compare/timeline, 3+ sig.) → adaptive_k_complex
          • Simple         (lookup-style)              → adaptive_k_simple
        """
        s = self.settings
        if not s.adaptive_k:
            return s.rerank_top_k_default
        if sigs.is_comprehensive():
            return s.adaptive_k_comprehensive
        if sigs.is_complex():
            return s.adaptive_k_complex
        return s.adaptive_k_simple


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _context_hint_from_signals(sigs: QuerySignals) -> str:
    """Build a short context hint we feed to the query rewriter."""
    bits: List[str] = []
    if sigs.has_temporal_signal:
        if sigs.date_from and sigs.date_to:
            bits.append(
                f"timeframe: {sigs.date_from.date()} to {sigs.date_to.date()}"
            )
    if sigs.money_terms:
        bits.append(f"figures mentioned: {', '.join(sigs.money_terms[:3])}")
    if sigs.filenames:
        bits.append(f"document(s) named: {', '.join(sigs.filenames[:2])}")
    if sigs.intents:
        bits.append(f"intent: {sigs.primary_intent()}")
    return "; ".join(bits)


def _date_filter_from_signals(sigs: QuerySignals) -> Optional[Dict[str, Any]]:
    """Build a MongoDB date filter from query signals.

    Option B semantics:
      • Default ("discussion" mode) → filter on `occurrences.date`,
        i.e. "did this content APPEAR in any email within the window?".
        Correct for "what was discussed in March 2024?" — a contract
        drafted in 2022 but discussed throughout March 2024 SHOULD be
        returned.
      • Creation mode (verbs like "drafted/signed/issued/filed") →
        filter on the top-level `date` (= PRIMARY/earliest occurrence ≈
        creation date in our corpus, since we only see email-side dates).

    `occurrences.date` is indexed both as a MongoDB native index AND as
    an Atlas Vector Search filter path (see create_v2_vector_index.py).
    Mongo handles the `{path-into-array: {$gte/$lte}}` form natively
    using "any-element-matches" semantics.
    """
    if not sigs.has_temporal_signal:
        return None
    expr: Dict[str, Any] = {}
    if sigs.date_from:
        expr["$gte"] = sigs.date_from
    if sigs.date_to:
        expr["$lte"] = sigs.date_to
    if not expr:
        return None
    path = "date" if sigs.prefer_creation_date else "occurrences.date"
    return {path: expr}


def _merge_filters(
    a: Optional[Dict[str, Any]],
    b: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Merge two MongoDB filter dicts (b's keys override a's only on collision)."""
    if not a and not b:
        return None
    if not a:
        return dict(b or {})
    if not b:
        return dict(a)
    merged = dict(a)
    merged.update(b)
    return merged


def _merge_dedup_preserve_first(
    primary: Sequence[Dict[str, Any]],
    additional: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge two chunk lists, deduping on `_id` while preserving primary order.
    Chunks unique to `additional` are appended at the end in their original
    order. Used by full-doc mode: the reranker-ordered chunks stay at the
    front; the remaining full-doc chunks fill in behind them.
    """
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for doc in primary:
        cid = str(doc.get("_id"))
        if cid in seen:
            continue
        seen.add(cid)
        out.append(doc)
    for doc in additional:
        cid = str(doc.get("_id"))
        if cid in seen:
            continue
        seen.add(cid)
        out.append(doc)
    return out


def _cap_by_tokens(
    docs: Sequence[Dict[str, Any]],
    *,
    max_tokens: int,
) -> List[Dict[str, Any]]:
    """
    Hard upper bound on total evidence tokens, applied AFTER ordering so
    we keep the most-attended chunks (front + back of the interleaved
    list) and drop from the dip in the middle if we overflow.

    Cheap approximation: 1 token ≈ 4 characters.
    """
    if max_tokens <= 0 or not docs:
        return list(docs)
    docs = list(docs)
    n = len(docs)

    def _toks(doc: Dict[str, Any]) -> int:
        body = doc.get("body") or doc.get("text") or ""
        return max(1, len(body) // 4)

    # Accept from the FRONT and BACK inward, alternating, so if we must
    # drop we drop from the MIDDLE (the low-attention "lost in the middle"
    # zone) — NOT the tail. After interleaving, the tail holds the
    # second-best chunks, so the previous tail-truncation silently dropped
    # high-value evidence on overflow.
    keep = [False] * n
    spent = 0
    lo, hi = 0, n - 1
    take_front = True
    while lo <= hi:
        idx = lo if take_front else hi
        t = _toks(docs[idx])
        if spent + t > max_tokens and any(keep):
            break
        keep[idx] = True
        spent += t
        if lo == hi:
            break
        if take_front:
            lo += 1
        else:
            hi -= 1
        take_front = not take_front
    return [d for d, k in zip(docs, keep) if k]
