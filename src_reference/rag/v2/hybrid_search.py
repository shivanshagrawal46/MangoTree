"""
Hybrid Search — BM25 + Vector + Filename direct lookup, fused via RRF.

Why this exists:
  Pure vector similarity misses queries where the answer is a literal
  string (filenames, dollar amounts, case numbers, proper nouns). BM25
  catches those. Pure BM25 misses paraphrased / conceptual queries.
  Hybrid does both, then fuses results via Reciprocal Rank Fusion (RRF).

  We additionally provide a "filename direct lookup" channel: when the
  user explicitly names a document, we bypass scoring entirely and pull
  every chunk whose filename matches.

Channels (each returns a ranked list of chunk docs):
  V — Atlas $vectorSearch (one OR many query vectors via multi-query/HyDE)
  B — MongoDB $text search on body+filename+subject (BM25)
  F — Filename direct lookup (regex/text match on `filename` field)

Fusion:
  Reciprocal Rank Fusion with constant k=60 (literature standard).
    score(d) = Σ_i  1 / (k + rank_i(d))
  for each ranked list i that contains d. Higher score = better.

Index requirements (created idempotently by `ensure_v2_text_index`):
  • A `$text` index on email_chunks covering body + filename + subject.

Design notes:
  • This module never raises on a corpus-side failure; it logs and
    returns whatever channel(s) succeeded. If ALL channels fail it
    returns an empty list and the caller falls back to v1.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger


_TEXT_INDEX_NAME = "tx_chunks_v2_body_filename_subject"
_DEFAULT_V2_COLLECTION = "email_chunks_v2"


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

def ensure_v2_text_index(
    mongo: MongoClientWrapper,
    *,
    collection_name: str = _DEFAULT_V2_COLLECTION,
) -> None:
    """
    Idempotently create the BM25 text index on the v2 chunks collection.

    Option B indexes both the top-level (`filename` / `subject`) AND the
    occurrences[] array paths (`occurrences.filename` / `occurrences.subject`).
    Mongo handles the array paths via multi-key text indexing — so any
    filename that appears in ANY occurrence will match the query.

    Safe to call on every startup — MongoDB skips creation if an equivalent
    index already exists.
    """
    try:
        col = mongo.db[collection_name]
        existing = col.index_information()
        if _TEXT_INDEX_NAME in existing:
            return
        col.create_index(
            [
                ("body", "text"),
                ("filename", "text"),
                ("subject", "text"),
                ("occurrences.filename", "text"),
                ("occurrences.subject", "text"),
            ],
            name=_TEXT_INDEX_NAME,
            default_language="english",
            weights={
                "filename": 5,
                "occurrences.filename": 5,
                "subject": 3,
                "occurrences.subject": 3,
                "body": 1,
            },
        )
        logger.info(f"Created text index '{_TEXT_INDEX_NAME}' on {collection_name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not create v2 text index (continuing): {exc}")


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class HybridResult:
    """Output of one fused channel — a list of chunk docs in RRF-ranked order."""

    chunks: List[Dict[str, Any]]
    # Per-chunk RRF score (chunk_id → score). Useful for downstream re-scoring.
    scores: Dict[str, float]
    # Which channels contributed (for observability / debugging).
    channels_used: List[str]


# ---------------------------------------------------------------------------
# Hybrid Searcher
# ---------------------------------------------------------------------------

class HybridSearcher:
    """
    Coordinates the V / B / F channels and fuses them via RRF.

    A single instance is reusable across requests (stateless).
    """

    def __init__(
        self,
        mongo: MongoClientWrapper,
        *,
        vector_index_name: str,
        rrf_k: int = 60,
        vector_top_k: int = 150,
        bm25_top_k: int = 100,
        phrase_top_k: int = 80,
        body_regex_top_k: int = 80,
        filename_top_k: int = 50,
        chunks_collection_name: str = _DEFAULT_V2_COLLECTION,
        min_score: float = 0.0,
    ) -> None:
        self.mongo = mongo
        self.vector_index_name = vector_index_name
        self.rrf_k = max(1, rrf_k)
        self.vector_top_k = max(1, vector_top_k)
        self.bm25_top_k = max(1, bm25_top_k)
        self.phrase_top_k = max(1, phrase_top_k)
        self.body_regex_top_k = max(1, body_regex_top_k)
        self.filename_top_k = max(1, filename_top_k)
        self.chunks_collection_name = chunks_collection_name
        # Vector-search recall floor. 0.0 = OFF (preserves current behavior).
        # Applied as a post-projection $match on the cosine score so only
        # candidates at/above the floor survive. Keep LOW — too high hurts recall.
        self.min_score = max(0.0, float(min_score))

    @property
    def _col(self):
        """Resolve the v2 chunks collection lazily so tests can swap in
        a different db without re-instantiating the searcher."""
        return self.mongo.db[self.chunks_collection_name]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        *,
        query_vectors: Sequence[List[float]],
        text_queries: Sequence[str],
        filenames: Sequence[str] = (),
        body_substrings: Sequence[str] = (),
        exact_phrases: Sequence[str] = (),
        atlas_filter: Optional[Dict[str, Any]] = None,
        final_limit: int = 80,
    ) -> HybridResult:
        """
        Run all available channels in parallel-conceptually (sequentially
        in code; pymongo is sync) and fuse results via RRF.

        Args:
          query_vectors:    one or more query embeddings (multi-query / HyDE)
          text_queries:     raw query strings used for BM25 ($text)
          filenames:        explicit filename hints (from query_understanding)
          body_substrings:  *literal* substrings to scan in chunk body using
                             regex (BM25 strips $/comma — regex doesn't).
                             Used for money, case#, docket#, quoted doc names.
          exact_phrases:    quoted-phrase BM25 queries — same idea as
                             body_substrings but uses MongoDB $text "..." syntax
                             so the BM25 ranker can score them.
          atlas_filter:     optional Atlas $vectorSearch filter (date, sender)
          final_limit:      max number of docs to return after fusion

        Returns:
          HybridResult with chunks sorted by RRF score (descending).
        """
        ranked_lists: List[List[Dict[str, Any]]] = []
        channels_used: List[str] = []

        # ---- Channel V: Vector (one search per query vector) ----------
        for i, qvec in enumerate(query_vectors):
            try:
                docs = self._vector_search(qvec, atlas_filter=atlas_filter)
                if docs:
                    ranked_lists.append(docs)
                    channels_used.append(f"vector#{i}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Vector channel #{i} failed: {exc}")

        # ---- Channel B: BM25 / $text ---------------------------------
        for i, q in enumerate(text_queries):
            q = (q or "").strip()
            if not q:
                continue
            try:
                docs = self._bm25_search(q, atlas_filter=atlas_filter)
                if docs:
                    ranked_lists.append(docs)
                    channels_used.append(f"bm25#{i}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"BM25 channel #{i} failed: {exc}")

        # ---- Channel P: BM25 quoted-phrase ----------------------------
        # MongoDB $text supports phrase matching via "..." quoting. This
        # is critical for tokens with punctuation ($, comma, hyphen) where
        # the default tokenizer would otherwise strip them.
        for i, phrase in enumerate(exact_phrases):
            p = (phrase or "").strip()
            if not p:
                continue
            try:
                docs = self._bm25_search(
                    f'"{p}"', atlas_filter=atlas_filter, limit=self.phrase_top_k
                )
                if docs:
                    ranked_lists.append(docs)
                    channels_used.append(f"phrase#{i}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Phrase channel #{i} failed: {exc}")

        # ---- Channel S: Body substring (regex — guaranteed literal) --
        # The deterministic safety net. MongoDB BM25 strips $ and commas;
        # regex doesn't, so this catches money / case# / docket# tokens
        # that BM25 misses. Same mechanism as filename lookup, applied
        # to the chunk body field instead.
        for i, sub in enumerate(body_substrings):
            sub = (sub or "").strip()
            if not sub:
                continue
            try:
                docs = self._body_substring_lookup(
                    sub, atlas_filter=atlas_filter, limit=self.body_regex_top_k
                )
                if docs:
                    ranked_lists.append(docs)
                    channels_used.append(f"bodysub#{i}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Body-substring channel #{i} failed: {exc}")

        # ---- Channel F: Filename direct lookup ------------------------
        for i, fname in enumerate(filenames):
            fname = (fname or "").strip()
            if not fname:
                continue
            try:
                docs = self._filename_lookup(fname, atlas_filter=atlas_filter)
                if docs:
                    ranked_lists.append(docs)
                    channels_used.append(f"filename#{i}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Filename channel #{i} failed: {exc}")

        # ---- Fuse via RRF --------------------------------------------
        fused = _reciprocal_rank_fusion(ranked_lists, k=self.rrf_k)
        if not fused:
            return HybridResult(chunks=[], scores={}, channels_used=channels_used)

        # Materialise top-N chunks in order, with per-chunk RRF scores.
        ordered_chunks = [doc for doc, _ in fused[:final_limit]]
        scores = {
            _doc_id(doc): score for doc, score in fused[:final_limit]
        }
        logger.debug(
            f"Hybrid search: channels={channels_used} → {len(ordered_chunks)} fused"
        )
        return HybridResult(
            chunks=ordered_chunks,
            scores=scores,
            channels_used=channels_used,
        )

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    def _vector_search(
        self,
        query_vec: List[float],
        atlas_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        stage_vector: Dict[str, Any] = {
            "$vectorSearch": {
                "index": self.vector_index_name,
                "path": "embedding",
                "queryVector": query_vec,
                "numCandidates": max(150, self.vector_top_k * 5),
                "limit": self.vector_top_k,
            }
        }
        if atlas_filter:
            stage_vector["$vectorSearch"]["filter"] = atlas_filter

        pipeline: List[Dict[str, Any]] = [
            stage_vector,
            {"$project": _PROJECTION},
        ]
        # Add the vector search score for downstream consumers.
        pipeline[-1]["$project"]["score"] = {"$meta": "vectorSearchScore"}
        # Optional recall floor: drop candidates below the cosine threshold.
        # $vectorSearch has no native minScore, so we filter on the projected
        # score. Only applied when min_score > 0 (default 0.0 = no-op).
        if self.min_score > 0.0:
            pipeline.append({"$match": {"score": {"$gte": self.min_score}}})
        return list(self._col.aggregate(pipeline))

    def _bm25_search(
        self,
        text_query: str,
        atlas_filter: Optional[Dict[str, Any]] = None,
        *,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        match: Dict[str, Any] = {"$text": {"$search": text_query}}
        # Atlas $vectorSearch filter syntax differs from regular find filter,
        # but BM25 uses normal find. We translate the simple $eq / $in / $gte
        # subset that we actually emit.
        if atlas_filter:
            match.update(_translate_filter_for_find(atlas_filter))

        cursor = (
            self._col.find(
                match,
                {**_PROJECTION, "score": {"$meta": "textScore"}},
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(limit if limit is not None else self.bm25_top_k)
        )
        return list(cursor)

    def _body_substring_lookup(
        self,
        needle: str,
        atlas_filter: Optional[Dict[str, Any]] = None,
        *,
        limit: int = 80,
    ) -> List[Dict[str, Any]]:
        """
        Find chunks whose `body` (or `text`) contains the literal substring.

        This is the workhorse channel for money / case# / docket# queries —
        MongoDB's default $text tokenizer strips '$' and ',', so a query
        like "$450,000" returns garbage via BM25. Regex matches the
        verbatim string and is guaranteed correct.
        """
        import re as _re

        needle = needle.strip()
        if not needle:
            return []
        escaped = _re.escape(needle)
        match: Dict[str, Any] = {
            "$or": [
                {"body": {"$regex": escaped, "$options": "i"}},
                {"text": {"$regex": escaped, "$options": "i"}},
            ]
        }
        if atlas_filter:
            match.update(_translate_filter_for_find(atlas_filter))

        cursor = (
            self._col.find(match, _PROJECTION)
            .sort([("latest_date", -1)])
            .limit(limit)
        )
        return list(cursor)

    def _filename_lookup(
        self,
        filename_or_substr: str,
        atlas_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find chunks whose `filename` matches (case-insensitive substring).

        We escape regex metacharacters so a query like "Re Escrow.docx" does
        not accidentally match arbitrary text via the dot.
        """
        import re as _re

        needle = filename_or_substr.strip()
        if not needle:
            return []

        # Option B: a sha256 may appear under multiple filenames (different
        # emails attached the same file under different display names). We
        # match against BOTH the top-level (PRIMARY-occurrence mirror) and
        # the occurrences[].filename array. Mongo's $or + array path works
        # natively here.
        escaped = _re.escape(needle)
        match: Dict[str, Any] = {
            "$or": [
                {"filename": {"$regex": escaped, "$options": "i"}},
                {"occurrences.filename": {"$regex": escaped, "$options": "i"}},
            ]
        }
        if atlas_filter:
            match.update(_translate_filter_for_find(atlas_filter))

        cursor = (
            self._col.find(match, _PROJECTION)
            .sort([("latest_date", -1)])  # newest occurrence first
            .limit(self.filename_top_k)
        )
        return list(cursor)

    # ------------------------------------------------------------------
    # Full-document mode (Sprint 2.5 Lever 4)
    # ------------------------------------------------------------------

    def full_doc_search(
        self,
        *,
        filenames: Sequence[str],
        atlas_filter: Optional[Dict[str, Any]] = None,
        per_doc_token_budget: int = 50_000,
        max_docs: int = 4,
    ) -> List[Dict[str, Any]]:
        """
        Pull EVERY chunk of each named document, in chunk_index order, up
        to a per-doc token budget and a global max_docs cap.

        Used when the query explicitly names a document AND we have enough
        budget headroom to read the entire thing rather than just a few
        chunks. For one-doc queries we typically pass per_doc_budget = 50K
        which is enough to fit a >100-page stipulation in Claude's window.

        Per-doc budget scales DOWN as we add more docs (caller decides);
        this method just enforces the budget it's given.

        Returns a flat list of chunks (across all matched docs) in the
        order [doc_A_chunk_0, doc_A_chunk_1, ..., doc_B_chunk_0, ...].
        """
        import re as _re

        if not filenames:
            return []

        # Option B: resolve filenames → sha256s (one document = one sha256).
        # We match BOTH the top-level filename and occurrences[].filename
        # because the same sha256 may appear under different display names
        # in different parent emails.
        sha_ids: List[str] = []
        seen_sha: set = set()
        for fname in filenames[:max_docs * 2]:  # over-fetch a bit, dedup later
            fname = (fname or "").strip()
            if not fname:
                continue
            escaped = _re.escape(fname)
            match: Dict[str, Any] = {
                "$or": [
                    {"filename": {"$regex": escaped, "$options": "i"}},
                    {"occurrences.filename": {"$regex": escaped, "$options": "i"}},
                ]
            }
            if atlas_filter:
                match.update(_translate_filter_for_find(atlas_filter))
            cursor = self._col.find(
                match, {"sha256": 1, "_id": 0}
            ).limit(20)
            for doc in cursor:
                sha = doc.get("sha256")
                if not sha:
                    continue
                if sha in seen_sha:
                    continue
                seen_sha.add(sha)
                sha_ids.append(sha)
                if len(sha_ids) >= max_docs:
                    break
            if len(sha_ids) >= max_docs:
                break

        if not sha_ids:
            return []

        # Pull every chunk of each unique document (keyed by sha256) in
        # chunk_index order, bounded by the per-doc token budget.
        out: List[Dict[str, Any]] = []
        for sha in sha_ids:
            cursor = (
                self._col.find(
                    {"sha256": sha},
                    _PROJECTION,
                )
                .sort([("chunk_index", 1)])
                .limit(2000)  # hard upper bound — even huge PDFs cap here
            )
            spent = 0
            took_any = False
            for chunk in cursor:
                t = _approx_tokens(chunk)
                if spent + t > per_doc_token_budget and took_any:
                    break
                out.append(chunk)
                spent += t
                took_any = True

        logger.debug(
            f"Full-doc mode: {len(sha_ids)} docs, "
            f"{len(out)} chunks, per_doc_budget={per_doc_token_budget}"
        )
        return out


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

def _reciprocal_rank_fusion(
    ranked_lists: Iterable[List[Dict[str, Any]]],
    *,
    k: int = 60,
) -> List[Tuple[Dict[str, Any], float]]:
    """
    Standard RRF.

    For each ranked list, document at rank r contributes 1 / (k + r) to its
    fused score. Higher fused score = better. We keep one canonical doc per
    chunk_id (taking the first one we see — they're all the same chunk).

    Returns a list of (doc, fused_score) tuples sorted by score desc.
    """
    score_map: Dict[str, float] = defaultdict(float)
    doc_map: Dict[str, Dict[str, Any]] = {}

    for lst in ranked_lists:
        for rank, doc in enumerate(lst, start=1):
            cid = _doc_id(doc)
            if not cid:
                continue
            score_map[cid] += 1.0 / (k + rank)
            if cid not in doc_map:
                doc_map[cid] = doc

    fused = sorted(
        ((doc_map[cid], score) for cid, score in score_map.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    return fused


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Projected fields — kept consistent across all channels so RRF can dedupe
# on `_id` and the rest of the pipeline gets uniform shape. Option B adds
# `sha256`, `latest_date`, `total_chunks`, and the full `occurrences[]`
# array so downstream scoring / citation / diversification can lean on
# them.
_PROJECTION: Dict[str, Any] = {
    "_id": 1,
    "text": 1,
    "body": 1,
    "source_type": 1,
    "email_id": 1,
    "attachment_id": 1,
    "filename": 1,
    "extension": 1,
    "page_start": 1,
    "page_end": 1,
    "date": 1,
    "from_email": 1,
    "to_emails": 1,
    "subject": 1,
    "folder_path": 1,
    "chunk_index": 1,
    "total_chunks": 1,
    "sha256": 1,
    "latest_date": 1,
    "occurrences": 1,
    # Evidentiary spine (Sprint 2.3) — flow onto chunks so the provenance
    # footer reports a real corpus / privilege posture instead of "unknown".
    "corpus": 1,
    "privilege_status": 1,
    "doc_source_type": 1,
}


def _doc_id(doc: Dict[str, Any]) -> str:
    """Stable per-chunk identifier for RRF dedup."""
    cid = doc.get("_id")
    return str(cid) if cid is not None else ""


def _translate_filter_for_find(atlas_filter: Dict[str, Any]) -> Dict[str, Any]:
    """
    Atlas $vectorSearch supports a small filter DSL that is also a valid
    MongoDB find filter for the operators we emit (`$eq`, `$in`, `$gte`,
    `$lte`, `$gt`, `$lt`). So we can just pass it through. We deep-copy
    to be safe in case the caller mutates afterwards.
    """
    import copy as _copy
    return _copy.deepcopy(atlas_filter)


def _approx_tokens(doc: Dict[str, Any]) -> int:
    """Cheap token estimate (1 token ≈ 4 chars). Used for full-doc budgeting."""
    body = doc.get("body") or doc.get("text") or ""
    return max(1, len(body) // 4)
