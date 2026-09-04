"""The hybrid search pipeline — one call, every stage, one trace.

    understand → rewrite (Opus 5) → route → channels × scope lists → weighted RRF
    → rescore → diversify → rerank-2.5 → Opus 5 rerank → expand → assemble

Same steps for a property chat and a global chat; the scope decides which ranked
lists are built and at what weight, and in global mode the router may fan out
across properties with quotas so a large file cannot crowd out a small one.

Seen-aware: callers pass the chunk ids already in hand and get only new ones
back. Repeating a search therefore returns the next-best material, not the same
top twenty — which is what "not satisfied, give me more" needs.

Every stage is guarded. A channel or reranker that fails logs, is recorded in
the trace as degraded, and the pipeline continues. Retrieval degrades visibly;
it never stops.
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from mangotree.config.registry import PROPERTIES
from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg
from mangotree.retrieve.channels import Channels
from mangotree.retrieve.enumeration import EnumerationResult, enumerate_set, is_enumeration
from mangotree.retrieve.expansion import Expander
from mangotree.retrieve.fusion import RankedList, fuse
from mangotree.retrieve.hits import Hit
from mangotree.retrieve.query_rewrite import QueryRewriter, Rewrite, deterministic_rewrite, route
from mangotree.retrieve.query_understanding import QueryUnderstanding, understand
from mangotree.retrieve.rerank import Reranker
from mangotree.retrieve.rescoring import diversify, hit_counts_by_artifact, rescore
from mangotree.retrieve.scope import Scope
from mangotree.storage.mongo import Mongo

_CAP_SEQ = re.compile(r"\b([A-Z][a-zA-Z&'.-]+(?:\s+[A-Z][a-zA-Z&'.-]+){0,3})\b")
_NAME_STOP = {"What", "When", "Where", "Which", "Who", "How", "Why", "Is", "Are", "Was", "Were", "Did", "Does",
              "Do", "Show", "List", "Give", "Tell", "Find", "The", "A", "An", "In", "On", "For", "Of", "To",
              "Please", "Can", "Could", "Would", "Should", "Has", "Have", "Any", "All", "Every"}


@dataclass
class SearchResult:
    question: str
    scope: str
    hits: List[Hit]                       # final evidence set, assembled and capped
    retrieved: List[Hit]                  # reranked hits before expansion
    understanding: QueryUnderstanding
    rewrite: Rewrite
    route_reason: str = ""
    enumeration: Optional[EnumerationResult] = None
    trace: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "scope": self.scope,
            "route": self.route_reason,
            "understanding": self.understanding.as_dict(),
            "rewrite": {k: (v.isoformat() if isinstance(v, datetime) else v)
                        for k, v in self.rewrite.as_dict().items()} if self.rewrite else None,
            "enumeration": self.enumeration.as_dict() if self.enumeration else None,
            "retrieved": [h.as_dict() for h in self.retrieved],
            "hits": [h.as_dict() for h in self.hits],
            "trace": self.trace,
            "elapsed_ms": self.elapsed_ms,
        }


class HybridSearch:
    def __init__(self, mongo: Mongo, *, voyage_api_key: str, anthropic_api_key: str):
        from mangotree.embed.embedder import Embedder

        self.mongo = mongo
        self.embedder = Embedder(voyage_api_key)
        self.channels = Channels(mongo, self.embedder)
        self.rewriter = QueryRewriter(anthropic_api_key)
        self.reranker = Reranker(voyage_api_key=voyage_api_key, anthropic_api_key=anthropic_api_key)
        self.expander = Expander(mongo, self.channels)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _merge_filter(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
        parts = [p for p in (base, extra) if p]
        if not parts:
            return {}
        return parts[0] if len(parts) == 1 else {"$and": parts}

    @staticmethod
    def _question_filters(rw: Rewrite, u: QueryUnderstanding, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Structured filters the question stated, in chunk-field terms.

        ``extra`` is a caller-supplied chunk filter (a tool forcing a period or
        a document type); it is merged in and wins over anything inferred.
        """
        clauses: List[dict] = []
        if extra:
            clauses.append(dict(extra))
        f = rw.filters or {}
        start, end = f.get("date_from"), f.get("date_to")
        rng: Dict[str, Any] = {}
        if isinstance(start, datetime):
            rng["$gte"] = start
        if isinstance(end, datetime):
            rng["$lte"] = end
        if rng:
            clauses.append({"date": rng})
        if f.get("from_email") and "@" in f["from_email"]:
            clauses.append({"from_email": f["from_email"].lower()})
        exts = list(dict.fromkeys(list(u.extensions) + list(f.get("extensions") or [])))
        if exts:
            clauses.append({"extension": {"$in": exts}})
        # Topics and doc classes are deliberately NOT hard filters here. They are
        # populated on a minority of chunks (topics only on the common store,
        # doc_class on ~16%), so filtering on them would exclude most of the
        # corpus. They shape rescoring instead; fetch_documents and enumerate_set
        # apply them where a complete set is the point.
        if not clauses:
            return {}
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    @staticmethod
    def _name_candidates(question: str, u: QueryUnderstanding) -> List[str]:
        names = [m.group(1) for m in _CAP_SEQ.finditer(question)]
        names = [n for n in names if n.split()[0] not in _NAME_STOP and len(n) > 3]
        # Drop property aliases; those are handled by scope, not the graph.
        alias_words = {a.lower() for p in PROPERTIES for a in p.aliases} | {p.canonical_address.lower() for p in PROPERTIES}
        names = [n for n in names if n.lower() not in alias_words]
        return list(dict.fromkeys(names + u.emails + u.addresses))[:8]

    # -------------------------------------------------------------- one scope
    def _search_scope(
        self,
        question: str,
        scope: Scope,
        u: QueryUnderstanding,
        rw: Rewrite,
        *,
        seen: Set[str],
        depth: int,
        keep: int,
        use_stage2: bool,
        trace: Dict[str, Any],
        extra_filter: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[Hit], List[Hit]]:
        """Returns (retrieved_after_rerank, final_assembled)."""
        mult = max(1, depth)
        qfilter = self._question_filters(rw, u, extra_filter)

        # --- embeddings for every query form, in one batch of calls ---------
        texts = [rw.standalone]
        if rw.hyde:
            texts.append(rw.hyde)
        texts += rw.alternates
        with ThreadPoolExecutor(max_workers=len(texts)) as pool:
            vectors = list(pool.map(self.embedder.embed_query, texts))
        v_standalone = vectors[0]
        v_hyde = vectors[1] if rw.hyde else None
        v_alts = vectors[(2 if rw.hyde else 1):]

        # --- graph entities and timeline window, once ------------------------
        entity_ids: List[str] = []
        names = self._name_candidates(question, u)
        if names:
            ents = self.channels.resolve_entities(names)
            if ents:
                seeds = [e["entity_id"] for e in ents]
                entity_ids = self.channels.graph_neighbors(seeds, hops=1, limit=80)
                trace["graph"] = {"terms": names, "seeds": len(seeds), "reach": len(entity_ids)}
        temporal = "temporal" in u.intents or bool(rw.filters.get("date_from") or rw.filters.get("date_to"))
        t_start = rw.filters.get("date_from") or (u.date_range.start if u.date_range else None)
        t_end = rw.filters.get("date_to") or (u.date_range.end if u.date_range else None)

        # --- ranked lists per scope list × channel ---------------------------
        lists: List[RankedList] = []
        W = cfg.CHANNEL_WEIGHTS

        def add(name: str, hits: List[Hit], channel: str, spec) -> None:
            if hits:
                lists.append(RankedList(f"{spec.name}/{name}", hits, W[channel] * spec.weight, spec.label))

        jobs = []
        for spec in scope.ranked_lists():
            flt = self._merge_filter(spec.filter, qfilter)
            jobs.append(("vector", spec, lambda flt=flt, spec=spec: add(
                "vector", self.channels.vector(v_standalone, filter=flt, k=cfg.VECTOR_TOP_K * mult), "vector", spec)))
            if v_hyde is not None:
                jobs.append(("vector_hyde", spec, lambda flt=flt, spec=spec: add(
                    "vector_hyde", self.channels.vector(v_hyde, filter=flt, k=cfg.VECTOR_TOP_K * mult, channel="vector_hyde"), "vector_hyde", spec)))
            for i, va in enumerate(v_alts):
                jobs.append((f"vector_alt{i}", spec, lambda flt=flt, spec=spec, va=va, i=i: add(
                    f"vector_alt{i}", self.channels.vector(va, filter=flt, k=cfg.VECTOR_TOP_K * mult, channel=f"vector_alt{i}"), "vector_alt", spec)))
            jobs.append(("bm25", spec, lambda flt=flt, spec=spec: add(
                "bm25", self.channels.bm25(rw.standalone, filter=flt, k=cfg.BM25_TOP_K * mult), "bm25", spec)))
            for i, alt in enumerate(rw.alternates):
                jobs.append((f"bm25_alt{i}", spec, lambda flt=flt, spec=spec, alt=alt, i=i: add(
                    f"bm25_alt{i}", self.channels.bm25(alt, filter=flt, k=cfg.BM25_TOP_K * mult, channel=f"bm25_alt{i}"), "bm25_alt", spec)))
            phrases = list(u.quoted)
            if phrases:
                jobs.append(("phrase", spec, lambda flt=flt, spec=spec, phrases=phrases: add(
                    "phrase", self.channels.phrase(phrases, filter=flt, k=cfg.PHRASE_TOP_K * mult), "phrase", spec)))
            exact = [t for t in u.exact_tokens() if t not in u.quoted]
            if exact:
                jobs.append(("substring", spec, lambda flt=flt, spec=spec, exact=exact: add(
                    "substring", self.channels.substring(exact, filter=flt, k=cfg.SUBSTRING_TOP_K * mult), "substring", spec)))
            if u.filenames:
                jobs.append(("filename", spec, lambda flt=flt, spec=spec: add(
                    "filename", self.channels.filename(u.filenames, filter=flt, k=cfg.FILENAME_TOP_K * mult), "filename", spec)))
            if entity_ids:
                jobs.append(("graph", spec, lambda flt=flt, spec=spec: add(
                    "graph", self.channels.graph(entity_ids, filter=flt, k=cfg.GRAPH_TOP_K * mult, query_vector=v_standalone), "graph", spec)))
            if temporal and (t_start or t_end):
                props = list(scope.property_ids) or ([scope.property_id] if scope.property_id else [])
                jobs.append(("timeline", spec, lambda flt=flt, spec=spec, props=props: add(
                    "timeline", self.channels.timeline(filter=flt, property_ids=props or [], start=t_start, end=t_end,
                                                       query_vector=v_standalone, k=cfg.TIMELINE_TOP_K * mult), "timeline", spec)))
            jobs.append(("doclevel", spec, lambda flt=flt, spec=spec: add(
                "doclevel", self.channels.doclevel(v_standalone, filter=flt), "doclevel", spec)))

        started = time.time()
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda j: j[2](), jobs))
        trace["lists"] = {rl.name: len(rl.hits) for rl in lists}
        trace["channels_ms"] = int((time.time() - started) * 1000)

        # --- fuse, drop seen, rescore, count, diversify ------------------------
        fused = fuse(lists, cap=cfg.FUSED_CAP * mult)
        fused = [h for h in fused if h.chunk_id not in seen and scope.allows(h)]
        trace["fused"] = len(fused)
        rescored = rescore(fused, u)
        counts = hit_counts_by_artifact(rescored)           # before the cap, deliberately
        pool_hits = diversify(rescored, u)
        for h in pool_hits:
            if not h.label:
                h.label = scope.label_for(h)
        trace["pool"] = len(pool_hits)

        # --- rerank -----------------------------------------------------------
        stage1 = self.reranker.stage1(rw.standalone, pool_hits, keep=max(cfg.RERANK_STAGE1_KEEP, keep))
        retrieved = self.reranker.stage2(rw.standalone, stage1, keep=keep) if use_stage2 else stage1[:keep]
        trace["rerank"] = dict(self.reranker.last_trace)

        # --- expand -----------------------------------------------------------
        seen_local = set(seen) | {h.chunk_id for h in retrieved}
        base_filter = scope.base_filter()
        expanded: List[Hit] = []
        expanded += self.expander.neighbours(retrieved, filter=base_filter, seen=seen_local)
        expanded += self.expander.parents(retrieved, counts, filter=base_filter, seen=seen_local)
        expanded += self.expander.threads(retrieved, filter=base_filter, seen=seen_local)
        if rw.wants_full_document and u.filenames:
            expanded += self.expander.full_documents(u.filenames, filter=base_filter, seen=seen_local,
                                                     label=retrieved[0].label if retrieved else "")
        for h in expanded:
            if not h.label:
                h.label = scope.label_for(h)
        final = self.expander.assemble(retrieved, [h for h in expanded if scope.allows(h)])
        trace["expansion"] = dict(self.expander.trace)
        return retrieved, final

    # ------------------------------------------------------------------ entry
    def search(
        self,
        question: str,
        scope: Scope,
        *,
        conversation: Sequence[dict] = (),
        seen: Optional[Set[str]] = None,
        depth: int = 1,
        keep: int = cfg.RERANK_STAGE2_KEEP,
        use_rewrite: bool = True,
        use_stage2: bool = True,
        with_enumeration: bool = True,
        extra_filter: Optional[Dict[str, Any]] = None,
    ) -> SearchResult:
        started = time.time()
        seen = set(seen or ())
        trace: Dict[str, Any] = {"scope": scope.describe(), "depth": depth}

        u = understand(question)
        if use_rewrite:
            rw = self.rewriter.rewrite(question, u, conversation=conversation, scope_hint=scope.describe())
        else:
            rw = deterministic_rewrite(question, u, "rewrite disabled")
        trace["rewrite"] = {"model": rw.model, "degraded": rw.degraded, "reason": rw.degrade_reason,
                            "hyde": bool(rw.hyde), "alternates": len(rw.alternates), "ms": rw.elapsed_ms}

        # Routing (global only). Property mode is fixed by construction.
        r = route(u, rw, scope_mode=scope.mode)
        trace["route"] = r.reason

        enumeration: Optional[EnumerationResult] = None
        if with_enumeration and is_enumeration(u, rw.intent):
            try:
                enum_scope = scope if scope.mode == "property" or not r.property_ids else \
                    Scope(mode="global", property_ids=r.property_ids, include_privileged=scope.include_privileged)
                enumeration = enumerate_set(self.mongo, question, u, enum_scope, filters=rw.filters)
                trace["enumeration"] = {"in_scope": enumeration.in_scope, "matched": enumeration.matched}
            except Exception as exc:
                logger.warning("enumeration unavailable: %s", exc)
                trace["enumeration"] = {"error": str(exc)[:200]}

        if scope.mode == "global" and r.fan_out:
            props = r.property_ids or [p.property_id for p in PROPERTIES]
            quota = max(3, keep // max(1, len(props)))
            retrieved_all: List[Hit] = []
            final_all: List[Hit] = []
            per: Dict[str, Any] = {}
            for pid in props:
                sub = Scope(mode="global", property_ids=[pid], include_privileged=scope.include_privileged,
                            role_filter=scope.role_filter)
                sub_trace: Dict[str, Any] = {}
                try:
                    ret, fin = self._search_scope(question, sub, u, rw, seen=seen, depth=depth,
                                                  keep=quota, use_stage2=use_stage2, trace=sub_trace,
                                                  extra_filter=extra_filter)
                except Exception as exc:
                    logger.error("fan-out search failed for %s: %s", pid, exc)
                    per[pid] = {"error": str(exc)[:200]}
                    continue
                per[pid] = {"retrieved": len(ret), "final": len(fin)}
                retrieved_all += ret
                final_all += fin
            trace["fan_out"] = per
            retrieved_all.sort(key=lambda h: (-(h.rerank2_score or 0), -(h.rerank1_score or 0)))
            final = self.expander.cap_tokens(final_all)
            result = SearchResult(question, scope.describe() + " (fan-out)", final, retrieved_all, u, rw,
                                  r.reason, enumeration, trace)
        else:
            eff_scope = scope
            if scope.mode == "global" and r.property_ids:
                eff_scope = Scope(mode="global", property_ids=r.property_ids,
                                  include_privileged=scope.include_privileged, role_filter=scope.role_filter)
            retrieved, final = self._search_scope(question, eff_scope, u, rw, seen=seen, depth=depth,
                                                  keep=keep, use_stage2=use_stage2, trace=trace,
                                                  extra_filter=extra_filter)
            result = SearchResult(question, eff_scope.describe(), final, retrieved, u, rw, r.reason, enumeration, trace)

        result.elapsed_ms = int((time.time() - started) * 1000)
        return result
