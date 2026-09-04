"""Retrieval channels — each returns one ranked list of hits.

Channels fail in different directions, which is the reason to run several:

* **vector** finds meaning and blurs exact tokens
* **bm25** nails exact tokens and is blind to paraphrase; with Lucene it also
  tolerates OCR spelling and expands lender synonyms
* **phrase** finds a quoted string exactly, in order
* **substring** finds identifiers and amounts the analysers would mangle
* **filename** finds a document by name, through every display name it has had
* **graph** finds a chunk by who or what it is linked to, sharing no words
* **timeline** finds a chunk by when the event it records happened
* **doclevel** finds the right document first, then its chunks
* **question** (after the night job) finds a chunk by the questions it answers

Every channel takes the scope filter and applies it inside the query. A channel
that is unavailable logs and returns an empty list — recall degrades visibly,
retrieval never stops.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.index.vector_index import VECTOR_INDEX_NAME
from mangotree.retrieve import config as cfg
from mangotree.retrieve.hits import HIT_PROJECTION, Hit
from mangotree.retrieve.scope import to_search_filters
from mangotree.retrieve.search_index import SEARCH_INDEX_NAME, SYNONYM_PATHS, SYNONYM_SET
from mangotree.storage.mongo import Mongo

DOC_SUMMARY_COLLECTION = "doc_summaries"
DOC_SUMMARY_INDEX = "doc_summaries_vector"
#: The only fields the document-level index declares as filters. Anything else
#: in a chunk filter (topics, folder, ordinal) must be dropped before the scan
#: or Atlas rejects the whole query.
DOC_SUMMARY_FILTER_FIELDS = frozenset(
    {"property_ids", "placement", "privileged", "source_type", "doc_class", "date", "extension", "from_email"}
)


def _restrict_filter(filter_dict: Dict[str, Any], allowed: frozenset) -> Dict[str, Any]:
    """Keep only clauses on fields the target index can filter on."""
    def clean(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in ("$and", "$or"):
                    kept = [c for c in (clean(x) for x in v) if c]
                    if kept:
                        out[k] = kept
                elif k in allowed:
                    out[k] = v
            return out
        return node
    return clean(filter_dict or {}) or {}


def _rank(hits: Iterable[Hit], channel: str) -> List[Hit]:
    out: List[Hit] = []
    for rank, hit in enumerate(hits, start=1):
        hit.channel_ranks[channel] = rank
        out.append(hit)
    return out


def _and(*clauses: Dict[str, Any]) -> Dict[str, Any]:
    parts = [c for c in clauses if c]
    if not parts:
        return {}
    return parts[0] if len(parts) == 1 else {"$and": parts}


def _vector_safe(filter_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Drop operators $vectorSearch's filter does not accept ($regex, $exists)."""
    def clean(node):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k in ("$regex", "$options", "$exists"):
                    continue
                cv = clean(v)
                if cv is not None and cv != {}:
                    out[k] = cv
            return out
        if isinstance(node, list):
            return [clean(x) for x in node if clean(x) not in (None, {})]
        return node
    return clean(filter_dict) or {}


class Channels:
    def __init__(self, mongo: Mongo, embedder):
        self.mongo = mongo
        self.embedder = embedder

    # ------------------------------------------------------------------ vector
    def vector(
        self,
        query_vector: List[float],
        *,
        filter: Dict[str, Any],
        k: int = cfg.VECTOR_TOP_K,
        channel: str = "vector",
        path: str = "embedding",
        index: str = VECTOR_INDEX_NAME,
    ) -> List[Hit]:
        stage: Dict[str, Any] = {
            "index": index,
            "path": path,
            "queryVector": query_vector,
            "numCandidates": max(k * 6, cfg.VECTOR_NUM_CANDIDATES),
            "limit": k,
        }
        vf = _vector_safe(filter)
        if vf:
            stage["filter"] = vf
        pipeline = [
            {"$vectorSearch": stage},
            {"$project": {**HIT_PROJECTION, "score": {"$meta": "vectorSearchScore"}}},
        ]
        try:
            return _rank((Hit.from_doc(d) for d in self.mongo.chunks.aggregate(pipeline)), channel)
        except Exception as exc:
            logger.warning("vector channel unavailable: %s", exc)
            return []

    # ------------------------------------------------------------------- bm25
    def bm25(
        self,
        text: str,
        *,
        filter: Dict[str, Any],
        k: int = cfg.BM25_TOP_K,
        channel: str = "bm25",
        fuzzy: bool = True,
        synonyms: bool = True,
    ) -> List[Hit]:
        """Lucene BM25 over text+context, with synonyms and OCR-tolerant fuzzy.

        Atlas does not allow synonyms and fuzzy in the same text clause, so they
        are two ``should`` clauses; a chunk matching both scores higher.
        """
        text = text.strip()
        if not text:
            return []
        should: List[dict] = [
            {"text": {"query": text, "path": ["text", "context"],
                      "score": {"boost": {"value": 1.0}}}},
        ]
        if synonyms:
            should.append({"text": {"query": text, "path": SYNONYM_PATHS, "synonyms": SYNONYM_SET,
                                    "score": {"boost": {"value": 0.8}}}})
        if fuzzy:
            should.append({"text": {"query": text, "path": ["text", "context", "display_name"],
                                    "fuzzy": {"maxEdits": 1, "prefixLength": 2, "maxExpansions": 50},
                                    "score": {"boost": {"value": 0.6}}}})
        compound: Dict[str, Any] = {"should": should, "minimumShouldMatch": 1}
        filters, must_not = to_search_filters(filter)
        if filters:
            compound["filter"] = filters
        if must_not:
            compound["mustNot"] = must_not
        pipeline = [
            {"$search": {"index": SEARCH_INDEX_NAME, "compound": compound}},
            {"$limit": k},
            {"$project": {**HIT_PROJECTION, "score": {"$meta": "searchScore"}}},
        ]
        try:
            return _rank((Hit.from_doc(d) for d in self.mongo.chunks.aggregate(pipeline)), channel)
        except Exception as exc:
            logger.warning("bm25 channel unavailable (%s); falling back to $text", exc)
            return self._text_fallback(text, filter=filter, k=k, channel=channel)

    def _text_fallback(self, text: str, *, filter: Dict[str, Any], k: int, channel: str) -> List[Hit]:
        criteria = _and({"$text": {"$search": text}}, filter)
        try:
            cursor = (
                self.mongo.chunks.find(criteria, {**HIT_PROJECTION, "score": {"$meta": "textScore"}})
                .sort([("score", {"$meta": "textScore"})]).limit(k)
            )
            return _rank((Hit.from_doc(d) for d in cursor), channel)
        except Exception as exc:
            logger.warning("$text fallback unavailable: %s", exc)
            return []

    # ----------------------------------------------------------------- phrase
    def phrase(self, phrases: Sequence[str], *, filter: Dict[str, Any], k: int = cfg.PHRASE_TOP_K) -> List[Hit]:
        phrases = [p.strip() for p in phrases if p and p.strip()]
        if not phrases:
            return []
        compound: Dict[str, Any] = {
            "should": [{"phrase": {"query": p, "path": ["text", "context"], "slop": 1}} for p in phrases],
            "minimumShouldMatch": 1,
        }
        filters, must_not = to_search_filters(filter)
        if filters:
            compound["filter"] = filters
        if must_not:
            compound["mustNot"] = must_not
        pipeline = [
            {"$search": {"index": SEARCH_INDEX_NAME, "compound": compound}},
            {"$limit": k},
            {"$project": {**HIT_PROJECTION, "score": {"$meta": "searchScore"}}},
        ]
        try:
            return _rank((Hit.from_doc(d) for d in self.mongo.chunks.aggregate(pipeline)), "phrase")
        except Exception as exc:
            logger.warning("phrase channel unavailable: %s", exc)
            return []

    # --------------------------------------------------------------- substring
    def substring(self, tokens: Sequence[str], *, filter: Dict[str, Any], k: int = cfg.SUBSTRING_TOP_K) -> List[Hit]:
        """Literal presence of an identifier or amount. Regex on the body.

        Money is matched loosely on digits so "$1,250,000" also finds
        "1250000.00" and "$1.25M" is left to the vector channel.
        """
        patterns: List[str] = []
        for tok in tokens:
            t = tok.strip()
            if not t:
                continue
            digits = re.sub(r"[^\d]", "", t)
            if t.startswith("$") and len(digits) >= 4:
                # 1,250,000 -> 1,?250,?000(\.\d\d)?
                groups = re.findall(r"\d{1,3}", digits[::-1])
                loose = r",?".join(g[::-1] for g in reversed(groups))
                patterns.append(rf"\$?\s?{loose}(?:\.\d{{2}})?")
            else:
                patterns.append(re.escape(t))
        if not patterns:
            return []
        regex = "|".join(patterns)
        criteria = _and({"text": {"$regex": regex, "$options": "i"}}, filter)
        try:
            cursor = self.mongo.chunks.find(criteria, HIT_PROJECTION).limit(k)
            return _rank((Hit.from_doc(d) for d in cursor), "substring")
        except Exception as exc:
            logger.warning("substring channel unavailable: %s", exc)
            return []

    # ---------------------------------------------------------------- filename
    def resolve_filenames(self, names: Sequence[str], *, filter: Dict[str, Any]) -> List[str]:
        """Filenames -> artifact shas, through artifact filename and every
        display name an occurrence ever gave the same bytes."""
        names = [n.strip() for n in names if n and n.strip()]
        if not names:
            return []
        shas: List[str] = []
        patterns = [re.escape(n.rsplit(".", 1)[0]) for n in names]
        regex = "|".join(patterns)
        for doc in self.mongo.artifacts.find(
            {"$or": [{"filename": {"$regex": regex, "$options": "i"}},
                     {"source_paths": {"$regex": regex, "$options": "i"}}]},
            {"sha256": 1},
        ).limit(50):
            shas.append(doc["sha256"])
        for occ in self.mongo.occurrences.find(
            {"filename": {"$regex": regex, "$options": "i"}}, {"artifact_sha": 1}
        ).limit(50):
            shas.append(occ["artifact_sha"])
        # Chunks carry display_name / filename too — covers attachments whose
        # display name differs from the stored filename.
        for doc in self.mongo.chunks.find(
            _and({"$or": [{"display_name": {"$regex": regex, "$options": "i"}},
                          {"filename": {"$regex": regex, "$options": "i"}}]}, filter),
            {"artifact_sha": 1},
        ).limit(200):
            shas.append(doc["artifact_sha"])
        return list(dict.fromkeys(shas))

    def filename(self, names: Sequence[str], *, filter: Dict[str, Any], k: int = cfg.FILENAME_TOP_K) -> List[Hit]:
        shas = self.resolve_filenames(names, filter=filter)
        if not shas:
            return []
        cursor = self.mongo.chunks.find(
            _and({"artifact_sha": {"$in": shas}}, filter), HIT_PROJECTION
        ).sort([("artifact_sha", 1), ("ordinal", 1)]).limit(k)
        return _rank((Hit.from_doc(d) for d in cursor), "filename")

    # ------------------------------------------------------------------- graph
    def resolve_entities(self, terms: Sequence[str], *, limit: int = 12) -> List[dict]:
        """Names / emails / addresses -> entity documents."""
        terms = [t.strip() for t in terms if t and len(t.strip()) > 2]
        if not terms:
            return []
        ents = self.mongo.db["entities"]
        regex = "|".join(re.escape(t) for t in terms)
        query = {"$or": [
            {"name": {"$regex": regex, "$options": "i"}},
            {"names": {"$regex": regex, "$options": "i"}},
            {"aliases": {"$regex": regex, "$options": "i"}},
            {"addresses": {"$regex": regex, "$options": "i"}},
            {"emails": {"$regex": regex, "$options": "i"}},
        ]}
        try:
            return list(ents.find(query).limit(limit))
        except Exception as exc:
            logger.warning("entity resolution unavailable: %s", exc)
            return []

    def graph_neighbors(self, entity_ids: Sequence[str], *, hops: int = 1, limit: int = 60) -> List[str]:
        """Entity ids reachable within ``hops`` along any edge."""
        edges = self.mongo.db["entity_edges"]
        seen = set(entity_ids)
        frontier = set(entity_ids)
        for _ in range(max(0, hops)):
            if not frontier:
                break
            nxt = set()
            try:
                for e in edges.find({"$or": [{"src": {"$in": list(frontier)}}, {"dst": {"$in": list(frontier)}}]},
                                    {"src": 1, "dst": 1}).limit(2000):
                    for side in (e.get("src"), e.get("dst")):
                        if side and side not in seen:
                            nxt.add(side)
            except Exception as exc:
                logger.warning("graph traversal unavailable: %s", exc)
                break
            seen |= nxt
            frontier = nxt
            if len(seen) >= limit:
                break
        return list(seen)[:limit]

    def graph(self, entity_ids: Sequence[str], *, filter: Dict[str, Any], k: int = cfg.GRAPH_TOP_K,
              query_vector: Optional[List[float]] = None) -> List[Hit]:
        """Chunks linked to the entities. Ordered by vector similarity when a
        query vector is available, else by recency."""
        ids = [e for e in entity_ids if e]
        if not ids:
            return []
        entity_filter = _and({"entity_ids": {"$in": ids}}, filter)
        if query_vector is not None:
            hits = self.vector(query_vector, filter=entity_filter, k=k, channel="graph")
            if hits:
                return hits
        cursor = self.mongo.chunks.find(entity_filter, HIT_PROJECTION).sort("date", -1).limit(k)
        return _rank((Hit.from_doc(d) for d in cursor), "graph")

    # ---------------------------------------------------------------- timeline
    def timeline_events(
        self,
        *,
        property_ids: Sequence[str],
        start: Optional[datetime],
        end: Optional[datetime],
        event_types: Sequence[str] = (),
        limit: int = 200,
    ) -> List[dict]:
        query: Dict[str, Any] = {}
        if property_ids:
            query["property_id"] = {"$in": list(property_ids)}
        rng: Dict[str, Any] = {}
        if start:
            rng["$gte"] = start
        if end:
            rng["$lte"] = end
        if rng:
            query["occurred_at"] = rng
        if event_types:
            query["event_type"] = {"$in": list(event_types)}
        try:
            return list(self.mongo.db["timeline_events"].find(query).sort("occurred_at", 1).limit(limit))
        except Exception as exc:
            logger.warning("timeline unavailable: %s", exc)
            return []

    def timeline(
        self,
        *,
        filter: Dict[str, Any],
        property_ids: Sequence[str],
        start: Optional[datetime],
        end: Optional[datetime],
        event_types: Sequence[str] = (),
        query_vector: Optional[List[float]] = None,
        k: int = cfg.TIMELINE_TOP_K,
    ) -> List[Hit]:
        """Events in the period -> their source documents -> chunks."""
        events = self.timeline_events(property_ids=property_ids, start=start, end=end,
                                      event_types=event_types, limit=400)
        shas = list(dict.fromkeys(e.get("source_sha") for e in events if e.get("source_sha")))
        if not shas:
            return []
        src_filter = _and({"artifact_sha": {"$in": shas[:500]}}, filter)
        if query_vector is not None:
            hits = self.vector(query_vector, filter=src_filter, k=k, channel="timeline")
            if hits:
                return hits
        cursor = self.mongo.chunks.find(src_filter, HIT_PROJECTION).sort("date", 1).limit(k)
        return _rank((Hit.from_doc(d) for d in cursor), "timeline")

    # -------------------------------------------------------------- doc-level
    def doclevel(self, query_vector: List[float], *, filter: Dict[str, Any],
                 k_docs: int = cfg.DOCLEVEL_TOP_K, per_doc: int = cfg.DOCLEVEL_CHUNKS_PER_DOC) -> List[Hit]:
        """Right document first (Tier-2 summary vectors), then its best chunks."""
        coll = self.mongo.db[DOC_SUMMARY_COLLECTION]
        try:
            if DOC_SUMMARY_INDEX not in {i["name"] for i in coll.list_search_indexes()}:
                return []
        except Exception:
            return []
        stage: Dict[str, Any] = {
            "index": DOC_SUMMARY_INDEX, "path": "embedding", "queryVector": query_vector,
            "numCandidates": k_docs * 10, "limit": k_docs,
        }
        # Document-level filter: only the fields the summary index declares.
        doc_filter = _vector_safe(_restrict_filter(filter, DOC_SUMMARY_FILTER_FIELDS))
        if doc_filter:
            stage["filter"] = doc_filter
        try:
            docs = list(coll.aggregate([{"$vectorSearch": stage},
                                        {"$project": {"_id": 0, "artifact_sha": 1}}]))
        except Exception as exc:
            logger.warning("doclevel channel unavailable: %s", exc)
            return []
        shas = [d["artifact_sha"] for d in docs]
        if not shas:
            return []
        hits = self.vector(query_vector, filter=_and({"artifact_sha": {"$in": shas}}, filter),
                           k=k_docs * per_doc, channel="doclevel")
        # Preserve document order from the summary search; chunks within by score.
        order = {sha: i for i, sha in enumerate(shas)}
        hits.sort(key=lambda h: (order.get(h.artifact_sha, 999), h.channel_ranks.get("doclevel", 999)))
        for h in hits:
            h.channel_ranks.pop("doclevel", None)
        return _rank(hits, "doclevel")

    # ---------------------------------------------------------------- fetches
    def chunks_of(self, artifact_sha: str, *, filter: Dict[str, Any] = None) -> List[Hit]:
        cursor = self.mongo.chunks.find(_and({"artifact_sha": artifact_sha}, filter or {}),
                                        HIT_PROJECTION).sort("ordinal", 1)
        return [Hit.from_doc(d) for d in cursor]

    def chunks_by_id(self, chunk_ids: Sequence[str]) -> Dict[str, Hit]:
        if not chunk_ids:
            return {}
        return {d["chunk_id"]: Hit.from_doc(d)
                for d in self.mongo.chunks.find({"chunk_id": {"$in": list(chunk_ids)}}, HIT_PROJECTION)}

    def neighbors(self, hits: Sequence[Hit], *, filter: Dict[str, Any]) -> Dict[str, List[Hit]]:
        """ordinal ±1 for each hit, one query per artifact batch."""
        wanted: Dict[str, set] = {}
        for h in hits:
            wanted.setdefault(h.artifact_sha, set()).update({h.ordinal - 1, h.ordinal + 1})
        if not wanted:
            return {}
        ors = [{"artifact_sha": sha, "ordinal": {"$in": [o for o in ords if o >= 0]}}
               for sha, ords in wanted.items()]
        out: Dict[str, List[Hit]] = {}
        for d in self.mongo.chunks.find(_and({"$or": ors}, filter), HIT_PROJECTION):
            out.setdefault(d["artifact_sha"], []).append(Hit.from_doc(d))
        return out
