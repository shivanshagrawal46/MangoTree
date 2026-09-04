"""Hybrid retrieval — property-scoped, always.

Two channels, because they fail in opposite directions:

* **Vector** finds meaning ("is the roof work finished?" matches "shingles
  complete") but blurs exact tokens — it will happily rank "draw 2" for "draw 3".
* **Lexical** nails exact tokens (a dollar figure, a lien number, "draw 3") but
  is blind to paraphrase.

Results are fused with Reciprocal Rank Fusion, which combines *ranks* rather than
scores. That matters here because the two channels' scores are not on a shared
scale, and normalising them would invent a comparability that does not exist.

The property filter is applied **inside** both searches, never afterwards. A
post-filter would let other properties consume the top-k slots and would turn any
future filtering bug into a silent cross-property leak.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from mangotree.config.models import RERANK_MODEL
from mangotree.core.logging import logger
from mangotree.index.vector_index import VECTOR_INDEX_NAME
from mangotree.storage.mongo import Mongo

#: RRF damping. 60 is the standard value from the original paper; it keeps a
#: strong hit in one channel from being outvoted by mediocre agreement.
RRF_K = 60


@dataclass
class Hit:
    chunk_id: str
    text: str
    context: str
    property_ids: List[str]
    source_ref: str
    display_name: str
    artifact_sha: str
    doc_class: Optional[str] = None
    date: object = None
    source_type: str = ""
    attribution: str = ""
    privileged: bool = False
    vector_rank: Optional[int] = None
    lexical_rank: Optional[int] = None
    fused_score: float = 0.0
    rerank_score: Optional[float] = None

    @property
    def citation(self) -> str:
        """What the UI shows so a human can reach the source in ≤2 clicks."""
        parts = [self.display_name]
        if self.source_ref and self.source_ref not in {"document", "email body"}:
            parts.append(self.source_ref)
        return " — ".join(parts)

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "citation": self.citation,
            "property_ids": self.property_ids,
            "artifact_sha": self.artifact_sha,
            "source_ref": self.source_ref,
            "doc_class": self.doc_class,
            "date": self.date,
            "attribution": self.attribution,
            "fused_score": round(self.fused_score, 5),
            "rerank_score": self.rerank_score,
        }


def _hit_from_doc(doc: dict) -> Hit:
    return Hit(
        chunk_id=doc["chunk_id"],
        text=doc.get("text", ""),
        context=doc.get("context", ""),
        property_ids=doc.get("property_ids", []),
        source_ref=doc.get("source_ref", ""),
        display_name=doc.get("display_name", ""),
        artifact_sha=doc.get("artifact_sha", ""),
        doc_class=doc.get("doc_class"),
        date=doc.get("date"),
        source_type=doc.get("source_type", ""),
        attribution=doc.get("attribution", ""),
        privileged=bool(doc.get("privileged")),
    )


_PROJECTION = {
    "chunk_id": 1, "text": 1, "context": 1, "property_ids": 1, "source_ref": 1,
    "display_name": 1, "artifact_sha": 1, "doc_class": 1, "date": 1,
    "source_type": 1, "attribution": 1, "privileged": 1, "_id": 0,
}


class Retriever:
    def __init__(self, mongo: Mongo, *, voyage_api_key: str):
        self.mongo = mongo
        self.api_key = voyage_api_key
        self._embedder = None
        self._voyage = None

    @property
    def embedder(self):
        if self._embedder is None:
            from mangotree.embed.embedder import Embedder

            self._embedder = Embedder(self.api_key)
        return self._embedder

    # ------------------------------------------------------------------
    @staticmethod
    def _filter(
        property_id: Optional[str],
        *,
        include_privileged: bool,
        source_type: Optional[str],
    ) -> dict:
        clauses: List[dict] = []
        if property_id:
            clauses.append({"property_ids": property_id})
        if not include_privileged:
            clauses.append({"privileged": {"$ne": True}})
        if source_type:
            clauses.append({"source_type": source_type})

        if not clauses:
            return {}
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    # ------------------------------------------------------------------
    def vector_search(
        self,
        query: str,
        *,
        property_id: Optional[str] = None,
        limit: int = 40,
        candidates: int = 400,
        include_privileged: bool = False,
        source_type: Optional[str] = None,
    ) -> List[Hit]:
        vector = self.embedder.embed_query(query)

        stage: dict = {
            "index": VECTOR_INDEX_NAME,
            "path": "embedding",
            "queryVector": vector,
            "numCandidates": candidates,
            "limit": limit,
        }
        filters = self._filter(
            property_id, include_privileged=include_privileged, source_type=source_type
        )
        if filters:
            stage["filter"] = filters

        pipeline = [
            {"$vectorSearch": stage},
            {"$project": {**_PROJECTION, "score": {"$meta": "vectorSearchScore"}}},
        ]

        hits: List[Hit] = []
        for rank, doc in enumerate(self.mongo.chunks.aggregate(pipeline), start=1):
            hit = _hit_from_doc(doc)
            hit.vector_rank = rank
            hits.append(hit)
        return hits

    # ------------------------------------------------------------------
    def lexical_search(
        self,
        query: str,
        *,
        property_id: Optional[str] = None,
        limit: int = 40,
        include_privileged: bool = False,
        source_type: Optional[str] = None,
    ) -> List[Hit]:
        criteria: dict = {"$text": {"$search": query}}
        filters = self._filter(
            property_id, include_privileged=include_privileged, source_type=source_type
        )
        if filters:
            criteria = {"$and": [criteria, filters]}

        try:
            cursor = (
                self.mongo.chunks.find(
                    criteria, {**_PROJECTION, "score": {"$meta": "textScore"}}
                )
                .sort([("score", {"$meta": "textScore"})])
                .limit(limit)
            )
            hits: List[Hit] = []
            for rank, doc in enumerate(cursor, start=1):
                hit = _hit_from_doc(doc)
                hit.lexical_rank = rank
                hits.append(hit)
            return hits
        except Exception as exc:
            # Losing a channel degrades recall; it must be visible, never silent.
            logger.warning("Lexical search unavailable: %s", exc)
            return []

    # ------------------------------------------------------------------
    @staticmethod
    def fuse(channels: Sequence[Sequence[Hit]], *, k: int = RRF_K) -> List[Hit]:
        """Reciprocal Rank Fusion over ranks, not scores."""
        merged: Dict[str, Hit] = {}
        scores: Dict[str, float] = {}

        for channel in channels:
            for rank, hit in enumerate(channel, start=1):
                key = hit.chunk_id
                if key not in merged:
                    merged[key] = hit
                else:
                    # Keep whichever ranks this chunk found, from both channels.
                    if hit.vector_rank is not None:
                        merged[key].vector_rank = hit.vector_rank
                    if hit.lexical_rank is not None:
                        merged[key].lexical_rank = hit.lexical_rank
                scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)

        for key, score in scores.items():
            merged[key].fused_score = score

        return sorted(merged.values(), key=lambda h: -h.fused_score)

    # ------------------------------------------------------------------
    def rerank(self, query: str, hits: Sequence[Hit], *, top_k: int = 12) -> List[Hit]:
        """Voyage cross-encoder rerank — stage one of two.

        Bi-encoder similarity compares two independent summaries of meaning; a
        cross-encoder reads query and passage together and can tell "draw 3 was
        approved" from "draw 3 was requested". Opus 5 is stage two, applied to
        the short list this produces.
        """
        if not hits:
            return []
        try:
            import voyageai

            if self._voyage is None:
                self._voyage = voyageai.Client(api_key=self.api_key, max_retries=6, timeout=120)

            documents = [f"{h.context}\n\n{h.text}".strip() for h in hits]
            result = self._voyage.rerank(
                query=query, documents=documents,
                model=RERANK_MODEL, top_k=min(top_k, len(documents)),
            )
            out: List[Hit] = []
            for item in result.results:
                hit = hits[item.index]
                hit.rerank_score = item.relevance_score
                out.append(hit)
            return out
        except Exception as exc:
            logger.warning("Rerank unavailable (%s); falling back to fused order", exc)
            return list(hits)[:top_k]

    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        property_id: Optional[str] = None,
        top_k: int = 12,
        pool: int = 40,
        use_rerank: bool = True,
        include_privileged: bool = False,
        source_type: Optional[str] = None,
    ) -> List[Hit]:
        """The entry point: hybrid retrieve, fuse, rerank — property-scoped."""
        vector = self.vector_search(
            query, property_id=property_id, limit=pool,
            include_privileged=include_privileged, source_type=source_type,
        )
        lexical = self.lexical_search(
            query, property_id=property_id, limit=pool,
            include_privileged=include_privileged, source_type=source_type,
        )
        fused = self.fuse([vector, lexical])

        # Defence in depth: the filter above should make this impossible, but a
        # cross-property leak is severe enough to warrant a second check that
        # costs nothing.
        if property_id:
            leaked = [h for h in fused if property_id not in h.property_ids]
            if leaked:
                logger.error(
                    "LEAK GUARD: dropped %d chunks not belonging to %s",
                    len(leaked), property_id,
                )
                fused = [h for h in fused if property_id in h.property_ids]

        if use_rerank:
            return self.rerank(query, fused[: max(pool, top_k)], top_k=top_k)
        return fused[:top_k]
