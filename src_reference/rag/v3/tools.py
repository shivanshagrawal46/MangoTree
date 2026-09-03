"""
v3 Agent tools.

Each tool is a thin wrapper around one of the v2 retrieval primitives,
or around the Sprint-3 verifier. The agent invokes tools by calling
Anthropic tool-use; we parse the input, execute against the v2 stack,
and return a summarised result that:

  1. Fits in the next planner turn with GENEROUS budgets — the planner
     writes the final forensic analysis from what it reads here, so
     tool results carry substantial chunk text (search briefs ~1,200
     chars; fetch_full_document ships the full body up to 40K chars).
  2. Carries the [#N] indices so the planner can reference them in
     future tool calls and in the final answer.
  3. Records full chunks in the scratchpad so the verifier (and the
     evidence drawer) have everything they need at finalisation time.

Tools added by this module
--------------------------

  • search                 — main hybrid retrieval (uses v2 pipeline)
  • search_by_filename     — direct filename lookup
  • search_timeframe       — date-bounded retrieval
  • fetch_full_document    — pull all chunks for one sha256 / doc
  • find_quote             — verify a specific phrase exists in corpus
  • compare_versions       — pull every dated version of a filename
  • find_latest_version    — chronological list of one document's versions
  • verify_claim           — Sprint-3 verifier as a TOOL
  • submit_final_answer    — terminate the loop (returns the answer)

`TOOL_REGISTRY` maps name -> ToolSpec for the planner. `execute()` is
the single dispatch entry-point used by the agent loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.rag.retriever import RetrievedChunk, Retriever, _to_chunk
from src.rag.v2.orchestrator import V2Pipeline
from src.rag.v2.verifier import verify_facts, DEFAULT_FUZZY_THRESHOLD
from src.utils.logger import logger


# =====================================================================
# Tool result wrapper
# =====================================================================

@dataclass
class ToolResult:
    """What a tool returns to the agent loop."""
    summary: str                                # 1-3 sentence text for stream
    payload: Dict[str, Any] = field(default_factory=dict)  # full data for planner
    new_chunks: List[RetrievedChunk] = field(default_factory=list)
    error: Optional[str] = None
    is_terminal: bool = False                   # True if submit_final_answer


# =====================================================================
# Tool specification
# =====================================================================

@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    fn: Callable[..., ToolResult] = field(repr=False, compare=False)


# =====================================================================
# Shared helpers
# =====================================================================

def _short_body(c: RetrievedChunk, max_chars: int = 1600) -> str:
    """Trim chunk body for the planner-facing tool result."""
    body = (c.body or c.text or "").strip()
    body = re.sub(r"\s+", " ", body)
    if len(body) > max_chars:
        body = body[: max_chars - 3] + "..."
    return body


def _date_str(d: Any) -> str:
    if not d:
        return ""
    if hasattr(d, "strftime"):
        try:
            return d.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pass
    return str(d)


def _chunk_brief(c: RetrievedChunk, display_index: int) -> Dict[str, Any]:
    """Compact dict describing one chunk for the planner."""
    title = c.filename if c.source_type == "attachment" else (c.subject or "(no subject)")
    return {
        "ref": f"[#{display_index}]",
        "type": c.source_type,
        "title": title,
        "date": _date_str(c.date),
        "from": c.from_email or "",
        "snippet": _short_body(c, 1200),
        "chunk_id": c.chunk_id,
        "sha256": getattr(c, "sha256", None),
    }


def _format_chunk_list(chunks: List[RetrievedChunk], display_indices: List[int]) -> List[Dict[str, Any]]:
    return [_chunk_brief(c, idx) for c, idx in zip(chunks, display_indices)]


# =====================================================================
# Tool implementations
# =====================================================================

class ToolBox:
    """
    Toolbox that holds the v2 pipeline / retriever and the scratchpad
    reference. Each `tool_*` method is a ToolSpec executor.

    Instantiated once per agent run. The scratchpad is set explicitly
    via `attach_scratchpad()` so we can keep the tool definitions
    stateless at the class-method level.
    """

    def __init__(self, *, v2_pipeline: V2Pipeline, retriever: Retriever,
                 base_filter: Optional[Dict[str, Any]] = None) -> None:
        self.v2 = v2_pipeline
        self.retriever = retriever
        self._pad = None  # set per-run by AgentRunner
        # base_filter is merged into EVERY retrieval (Clean/privilege mode):
        # e.g. {"privilege_status": {"$ne": "privileged"}} so the agent's own
        # tool retrievals can never pull privileged chunks into a shareable
        # answer. Empty dict = no restriction (Analysis mode).
        self.base_filter: Dict[str, Any] = dict(base_filter or {})
        self.exclude_privileged: bool = (
            self.base_filter.get("privilege_status", {}) == {"$ne": "privileged"})

    def _merge_filter(self, f: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Merge the base (privilege) filter into a tool's own filter."""
        if not self.base_filter:
            return f
        return {**(f or {}), **self.base_filter}

    def attach_scratchpad(self, pad: "AgentScratchpad") -> None:  # noqa: F821 (fwd ref)
        self._pad = pad

    # =================================================================
    # search
    # =================================================================

    def tool_search(self, *, query: str, top_k: int = 8) -> ToolResult:
        """
        Run the v2 hybrid pipeline for `query` and merge new chunks into
        the scratchpad. Returns short briefs of the NEW chunks (we don't
        re-show chunks the planner has already seen).
        """
        try:
            results = self.v2.retrieve(query, atlas_filter=self._merge_filter(None))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                summary=f"search failed: {exc}",
                error=str(exc),
                payload={"query": query, "top_k": top_k},
            )
        # Cap to top_k.
        results = list(results)[: max(1, int(top_k))]
        if not results:
            return ToolResult(
                summary=f'search("{query}") returned 0 hits',
                payload={"query": query, "hits": []},
                new_chunks=[],
            )

        new_idx = self._pad.add_chunks(results)
        # Only stream briefs for NEW chunks; previously-seen chunks
        # would just confuse the planner.
        if not new_idx:
            return ToolResult(
                summary=(
                    f'search("{query}") returned {len(results)} hits — '
                    f"all already in scratchpad"
                ),
                payload={"query": query, "new_chunk_indices": []},
            )

        # Build briefs in display-index order for the NEW chunks.
        new_chunks_by_idx = []
        for i, c in enumerate(results):
            cid = c.chunk_id
            idx = self._pad._by_id.get(cid)
            if idx and idx in new_idx:
                new_chunks_by_idx.append((idx, c))
        new_chunks_by_idx.sort(key=lambda t: t[0])

        briefs = [_chunk_brief(c, idx) for idx, c in new_chunks_by_idx]
        return ToolResult(
            summary=(
                f'search("{query}") returned {len(results)} hits — '
                f"{len(new_idx)} new ({', '.join(b['ref'] for b in briefs[:6])}"
                + (f", +{len(briefs)-6} more" if len(briefs) > 6 else "")
                + ")"
            ),
            payload={
                "query": query,
                "n_hits": len(results),
                "new_chunk_indices": new_idx,
                "briefs": briefs,
            },
            new_chunks=results,
        )

    # =================================================================
    # search_by_filename
    # =================================================================

    def tool_search_by_filename(self, *, filename_pattern: str, top_k: int = 10) -> ToolResult:
        """Direct filename lookup using the v2 filename channel."""
        try:
            # _filename_lookup returns a list of raw Mongo docs sorted by
            # latest_date desc. Public method `search()` only exposes
            # filename as one channel among many — we want the bare
            # filename hits for this tool.
            results = self.v2.hybrid_searcher._filename_lookup(filename_pattern)
            results = (results or [])[: int(top_k)]
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                summary=f"search_by_filename failed: {exc}",
                error=str(exc),
                payload={"filename_pattern": filename_pattern},
            )
        from src.rag.retriever import _to_chunk
        chunks = [_to_chunk(r, rerank_score=None) for r in results]
        new_idx = self._pad.add_chunks(chunks)

        briefs = []
        for idx, c in zip(new_idx, chunks[: len(new_idx)]):
            briefs.append(_chunk_brief(c, idx))

        if not chunks:
            return ToolResult(
                summary=f'search_by_filename("{filename_pattern}") returned 0 hits',
                payload={"filename_pattern": filename_pattern, "n_hits": 0},
            )

        return ToolResult(
            summary=(
                f'search_by_filename("{filename_pattern}") returned '
                f"{len(chunks)} hits — {len(new_idx)} new"
            ),
            payload={
                "filename_pattern": filename_pattern,
                "n_hits": len(chunks),
                "new_chunk_indices": new_idx,
                "briefs": briefs,
            },
            new_chunks=chunks,
        )

    # =================================================================
    # search_timeframe
    # =================================================================

    def tool_search_timeframe(
        self,
        *,
        start_date: str,
        end_date: str,
        query: Optional[str] = None,
        top_k: int = 12,
    ) -> ToolResult:
        """Date-bounded search."""
        try:
            sd = _parse_iso_date(start_date)
            ed = _parse_iso_date(end_date)
        except ValueError as exc:
            return ToolResult(
                summary=f"search_timeframe: invalid date — {exc}",
                error=str(exc),
                payload={"start_date": start_date, "end_date": end_date},
            )

        atlas_filter = self._merge_filter({"date": {"$gte": sd, "$lte": ed}})
        q = (query or self._pad.query or "").strip() if self._pad else (query or "")
        if not q:
            q = "summary of events"
        try:
            results = self.v2.retrieve(q, atlas_filter=atlas_filter)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                summary=f"search_timeframe failed: {exc}",
                error=str(exc),
            )
        results = list(results)[: max(1, int(top_k))]
        new_idx = self._pad.add_chunks(results)
        new_chunks_by_idx = []
        for c in results:
            idx = self._pad._by_id.get(c.chunk_id)
            if idx in new_idx:
                new_chunks_by_idx.append((idx, c))
        new_chunks_by_idx.sort(key=lambda t: t[0])
        briefs = [_chunk_brief(c, idx) for idx, c in new_chunks_by_idx]

        return ToolResult(
            summary=(
                f"search_timeframe({start_date}..{end_date}) returned "
                f"{len(results)} hits — {len(new_idx)} new"
            ),
            payload={
                "start_date": start_date,
                "end_date": end_date,
                "query": q,
                "n_hits": len(results),
                "new_chunk_indices": new_idx,
                "briefs": briefs,
            },
            new_chunks=results,
        )

    # =================================================================
    # fetch_full_document
    # =================================================================

    def tool_fetch_full_document(
        self,
        *,
        sha256: Optional[str] = None,
        chunk_index: Optional[int] = None,
    ) -> ToolResult:
        """
        Pull all chunks belonging to one document (by sha256 OR by an
        existing scratchpad [#N] index from which we resolve the sha256).
        Returns a complete view of the document and its occurrences.
        """
        target_sha = sha256
        seed_chunk = None
        if chunk_index and not target_sha:
            seed_chunk = self._pad.get_chunk(chunk_index)
            if seed_chunk:
                target_sha = getattr(seed_chunk, "sha256", None)
        if not target_sha:
            return ToolResult(
                summary="fetch_full_document: must provide sha256 or chunk_index of a known chunk",
                error="missing sha256",
            )

        col = self.v2.settings.chunks_collection_name
        cursor = self.v2.mongo.db[col].find(
            self._merge_filter({"sha256": target_sha}),
            sort=[("chunk_index", 1)],
        )
        from src.rag.retriever import _to_chunk
        docs = [_to_chunk(d, rerank_score=None) for d in cursor]
        if not docs:
            return ToolResult(
                summary=f"fetch_full_document(sha256={target_sha[:10]}…) returned 0 chunks",
                error="not found",
            )
        new_idx = self._pad.add_chunks(docs)

        # Build full body. The planner NEEDS the actual document text to
        # analyse it (this tool exists for depth) — ship up to 40K chars
        # (~10K tokens); only truly enormous documents get clipped.
        full_text = " ".join((d.body or d.text or "") for d in docs)
        full_text = re.sub(r"\s+", " ", full_text).strip()
        if len(full_text) > 40000:
            full_text_preview = full_text[:40000] + "... [truncated]"
        else:
            full_text_preview = full_text

        # Collect occurrences (which emails referenced this doc)
        all_occurrences: List[Dict[str, Any]] = []
        seen = set()
        for d in docs:
            for occ in (getattr(d, "occurrences", None) or []):
                key = (occ.get("email_id"), occ.get("attachment_id"))
                if key in seen:
                    continue
                seen.add(key)
                all_occurrences.append({
                    "email_id": occ.get("email_id"),
                    "attachment_id": occ.get("attachment_id"),
                    "filename": occ.get("filename"),
                    "date": _date_str(occ.get("date")),
                    "from_email": occ.get("from_email"),
                    "subject": occ.get("subject"),
                })

        return ToolResult(
            summary=(
                f"fetch_full_document(sha256={target_sha[:10]}…): "
                f"{len(docs)} chunks ({len(new_idx)} new) across "
                f"{len(all_occurrences)} email occurrence(s)"
            ),
            payload={
                "sha256": target_sha,
                "n_chunks": len(docs),
                "new_chunk_indices": new_idx,
                "title": docs[0].filename or docs[0].subject or "(no title)",
                "date": _date_str(docs[0].date or docs[0].latest_date),
                "full_text_preview": full_text_preview,
                "occurrences": all_occurrences[:20],  # cap for prompt size
                "n_occurrences": len(all_occurrences),
            },
            new_chunks=docs,
        )

    # =================================================================
    # find_quote
    # =================================================================

    def tool_find_quote(
        self,
        *,
        quote: str,
        max_results: int = 12,
    ) -> ToolResult:
        """
        Find every chunk that contains a verbatim substring. Useful when
        the agent wants to know "where does '$450,000' actually appear?"
        """
        q = (quote or "").strip()
        if len(q) < 6:
            return ToolResult(
                summary="find_quote: quote too short (<6 chars)",
                error="quote too short",
            )

        col = self.v2.settings.chunks_collection_name
        # Case-insensitive substring search on the body field. We rely
        # on Mongo regex; collation is fine for legal text. Escape regex
        # special chars in the quote.
        pattern = re.escape(q)
        cursor = self.v2.mongo.db[col].find(
            self._merge_filter({"$or": [
                {"body": {"$regex": pattern, "$options": "i"}},
                {"text": {"$regex": pattern, "$options": "i"}},
            ]}),
            limit=int(max_results),
        )
        from src.rag.retriever import _to_chunk
        docs = [_to_chunk(d, rerank_score=None) for d in cursor]
        new_idx = self._pad.add_chunks(docs)

        briefs = []
        for c in docs:
            idx = self._pad._by_id.get(c.chunk_id)
            if not idx:
                continue
            b = _chunk_brief(c, idx)
            # Add a context window around the match for grounding
            body = (c.body or c.text or "")
            m = re.search(pattern, body, re.IGNORECASE)
            if m:
                s = max(0, m.start() - 400)
                e = min(len(body), m.end() + 400)
                ctx = body[s:e].replace("\n", " ")
                ctx = re.sub(r"\s+", " ", ctx)
                b["match_context"] = ("..." if s > 0 else "") + ctx + ("..." if e < len(body) else "")
            briefs.append(b)

        return ToolResult(
            summary=(
                f'find_quote("{q[:50]}{"..." if len(q) > 50 else ""}") found '
                f"{len(docs)} chunks ({len(new_idx)} new)"
            ),
            payload={
                "quote": q,
                "n_hits": len(docs),
                "new_chunk_indices": new_idx,
                "briefs": briefs,
            },
            new_chunks=docs,
        )

    # =================================================================
    # find_latest_version  /  compare_versions
    # =================================================================

    def tool_find_latest_version(
        self,
        *,
        filename_pattern: str,
        max_versions: int = 10,
    ) -> ToolResult:
        """
        Find all dated versions of one document, returning them in
        chronological order so the agent can identify the latest /
        operative version.
        """
        col = self.v2.settings.chunks_collection_name
        pattern = re.escape(filename_pattern.strip())
        cursor = self.v2.mongo.db[col].find(
            self._merge_filter({"$or": [
                {"filename": {"$regex": pattern, "$options": "i"}},
                {"occurrences.filename": {"$regex": pattern, "$options": "i"}},
            ]}),
        )
        from src.rag.retriever import _to_chunk
        # Group by sha256 (Option B dedup); take one rep per doc.
        by_sha: Dict[str, Any] = {}
        for d in cursor:
            sha = d.get("sha256")
            if not sha:
                continue
            existing = by_sha.get(sha)
            if existing is None:
                by_sha[sha] = d
        if not by_sha:
            return ToolResult(
                summary=f'find_latest_version("{filename_pattern}") found 0 docs',
                payload={"filename_pattern": filename_pattern, "versions": []},
            )

        chunks = [_to_chunk(d, rerank_score=None) for d in by_sha.values()]
        # Sort by latest_date or date, descending (newest first).
        def _sort_key(c: RetrievedChunk) -> str:
            d = c.latest_date or c.date
            return _date_str(d) or "0000-00-00"

        chunks.sort(key=_sort_key, reverse=True)
        chunks = chunks[: int(max_versions)]

        new_idx = self._pad.add_chunks(chunks)

        versions = []
        for c in chunks:
            idx = self._pad._by_id.get(c.chunk_id)
            versions.append({
                "ref": f"[#{idx}]" if idx else "",
                "filename": c.filename,
                "date": _date_str(c.latest_date or c.date),
                "sha256": getattr(c, "sha256", None),
                "snippet": _short_body(c, 600),
            })

        return ToolResult(
            summary=(
                f'find_latest_version("{filename_pattern}"): '
                f"{len(chunks)} unique versions (newest: "
                f"{versions[0]['date'] if versions else 'n/a'})"
            ),
            payload={
                "filename_pattern": filename_pattern,
                "n_versions": len(chunks),
                "new_chunk_indices": new_idx,
                "versions": versions,
            },
            new_chunks=chunks,
        )

    def tool_compare_versions(
        self,
        *,
        chunk_indices: List[int],
    ) -> ToolResult:
        """
        Compare two or more chunk indices side-by-side. We don't compute
        a true word-level diff here (planner can do that semantically);
        we just lay out the bodies in parallel so Opus has them in one
        view.
        """
        if not chunk_indices or len(chunk_indices) < 2:
            return ToolResult(
                summary="compare_versions: need at least 2 chunk_indices",
                error="too few indices",
            )

        versions = []
        for idx in chunk_indices[:6]:  # cap to keep prompt size sane
            c = self._pad.get_chunk(int(idx))
            if not c:
                versions.append({"ref": f"[#{idx}]", "error": "not found"})
                continue
            versions.append({
                "ref": f"[#{idx}]",
                "filename": c.filename,
                "date": _date_str(c.latest_date or c.date),
                "from": c.from_email or "",
                "body": _short_body(c, 4000),
            })

        return ToolResult(
            summary=f"compare_versions({len(versions)} chunks): see payload for parallel bodies",
            payload={"versions": versions},
        )

    # =================================================================
    # verify_claim
    # =================================================================

    def tool_verify_claim(
        self,
        *,
        claim: str,
        verbatim_quote: str,
        source_chunk_id: int,
        fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
    ) -> ToolResult:
        """
        Use the Sprint-3 verifier as a TOOL. The agent can check whether
        a candidate quote actually exists in a particular chunk BEFORE
        committing to the final answer. This is the key innovation that
        lets the agent self-correct iteratively.
        """
        # Verifier needs a list of chunks indexed 0..N-1 in the same
        # order as the claim's source_chunk_id. We construct a single-
        # element list at the requested index for narrow checking.
        idx = int(source_chunk_id)
        c = self._pad.get_chunk(idx)
        if not c:
            return ToolResult(
                summary=f"verify_claim: chunk #{idx} not in scratchpad",
                payload={"verdict": "CITATION_INVALID", "score": 0},
                error="chunk not found",
            )
        # Build a sparse list so the verifier's source_chunk_id-1
        # indexing lines up with our display index.
        chunks_for_verifier: List[RetrievedChunk] = []
        for i in range(idx):
            chunks_for_verifier.append(self._pad.get_chunk(i + 1) or c)  # pad
        chunks_for_verifier[idx - 1] = c

        facts = [{
            "id": "agent_check",
            "claim": claim,
            "source_chunk_id": idx,
            "verbatim_quote": verbatim_quote,
            "confidence": "high",
        }]
        report = verify_facts(
            facts, chunks_for_verifier, fuzzy_threshold=float(fuzzy_threshold)
        )
        item = report.items[0]
        return ToolResult(
            summary=(
                f"verify_claim(#{idx}) → {item.verdict} (score {item.score:.1f}): "
                f"{(item.reason or '').strip()[:120]}"
            ),
            payload={
                "verdict": item.verdict,
                "score": item.score,
                "matched_span": item.matched_span,
                "reason": item.reason,
                "passed": item.passed,
            },
        )

    # =================================================================
    # submit_final_answer (terminal)
    # =================================================================

    def tool_submit_final_answer(
        self,
        *,
        facts: List[Dict[str, Any]],
        answer: str,
        reasoning_summary: Optional[str] = None,
    ) -> ToolResult:
        """
        Terminal tool. Returns the agent's final structured answer in
        the same shape as Sprint 3's submit_answer schema, so the
        downstream verifier + frontend evidence panel work without
        modification.
        """
        return ToolResult(
            summary=f"submit_final_answer: {len(facts)} facts, {len(answer)} chars",
            payload={
                "facts": facts,
                "answer": answer,
                "reasoning_summary": reasoning_summary or "",
            },
            is_terminal=True,
        )

    # =================================================================
    # ENTITY-GRAPH tools (Sprint 3 fan-out)
    # =================================================================

    def _entity_index(self):
        if getattr(self, "_eidx", None) is None:
            from src.graph.fanout import EntityIndex
            self._eidx = EntityIndex(self.retriever.mongo.db["entities"])
        return self._eidx

    def _new_chunk_briefs(self, chunks: List[RetrievedChunk], new_idx: List[int]) -> List[Dict[str, Any]]:
        out = []
        for c in chunks:
            idx = self._pad._by_id.get(c.chunk_id)
            if idx and idx in new_idx:
                out.append((idx, c))
        out.sort(key=lambda t: t[0])
        return [_chunk_brief(c, idx) for idx, c in out]

    def tool_search_entity_cluster(self, *, query: str, limit: int = 60) -> ToolResult:
        """Resolve the entities named in `query` to canonical IDs, then fan out
        across EVERY linked source type (David email + title + insurance +
        equity + deed + litigation). The default tool for entity questions."""
        from src.graph.fanout import fan_out_chunks, source_type_breakdown
        idx = self._entity_index()
        res = idx.resolve(query)
        if not res["all"]:
            return ToolResult(summary=f'search_entity_cluster: no canonical entity matched "{query}"',
                              payload={"query": query, "resolved_entities": []})
        rows = fan_out_chunks(self.retriever.mongo.db["email_chunks_v2"], res["all"],
                              limit=int(limit), exclude_privileged=self.exclude_privileged)
        chunks = [_to_chunk(r, rerank_score=None) for r in rows]
        new_idx = self._pad.add_chunks(chunks) if chunks else []
        names = [self._eidx.by_id.get(e, {}).get("canonical_name") or e
                 for e in list(res["all"])[:8]]
        briefs = self._new_chunk_briefs(chunks, new_idx)
        bd = source_type_breakdown(rows)
        return ToolResult(
            summary=(f'entity_cluster("{query}") -> {len(res["all"])} entities '
                     f'({", ".join(str(x) for x in names[:5])}), {len(chunks)} chunks '
                     f'across {bd}'),
            payload={"query": query, "resolved_entities": sorted(res["all"]),
                     "entity_names": names, "source_breakdown": bd,
                     "new_chunk_indices": new_idx, "briefs": briefs},
            new_chunks=chunks)

    def tool_list_documents_for_entity(self, *, entity_query: str, limit: int = 60) -> ToolResult:
        """List the distinct DOCUMENTS (title reports, insurance, equity,
        litigation, agreement) linked to the entity named in `entity_query`,
        with type/address/date — a structured index, not chunk text."""
        idx = self._entity_index()
        res = idx.resolve(entity_query)
        if not res["all"]:
            return ToolResult(summary=f'no entity matched "{entity_query}"',
                              payload={"resolved_entities": []})
        docs = self.retriever.mongo.db["documents"]
        q = {"$or": [{"property_ids": {"$in": list(res["properties"])}},
                     {"owner_entity_id": {"$in": list(res["people"] | res["llcs"])}},
                     {"owner_entity_ids": {"$in": list(res["people"] | res["llcs"])}},
                     {"case_ids": {"$in": list(res["cases"])}}]}
        rows = list(docs.find(q, {"_id": 1, "source_type": 1, "property_address": 1,
                                  "vendor": 1, "is_update": 1, "completed_date": 1,
                                  "search_date": 1, "document_date": 1,
                                  "effective_date": 1}).limit(int(limit)))
        listing = [{"doc_id": r["_id"], "type": r.get("source_type"),
                    "address": r.get("property_address"),
                    "date": _date_str(r.get("completed_date") or r.get("search_date")
                                      or r.get("document_date") or r.get("effective_date")),
                    "is_update": r.get("is_update")} for r in rows]
        bd: Dict[str, int] = {}
        for r in listing:
            bd[r["type"]] = bd.get(r["type"], 0) + 1
        return ToolResult(
            summary=f'list_documents_for_entity("{entity_query}") -> {len(listing)} docs {bd}',
            payload={"resolved_entities": sorted(res["all"]), "documents": listing,
                     "breakdown": bd})

    def tool_timeline(self, *, query: str, limit: int = 60) -> ToolResult:
        """Return a CORRECT, cited chronology (from the event store) for the
        property/entity named in `query`. The sequence is pre-sorted by date —
        use for 'what happened to X and when' / flow-of-funds questions."""
        from src.timeline.builder import timeline_for
        idx = self._entity_index()
        res = idx.resolve(query)
        if not res["all"]:
            return ToolResult(summary=f'timeline: no entity matched "{query}"', payload={})
        pid = next(iter(res["properties"]), None)
        if pid:
            tl = timeline_for(self.retriever.mongo, property_id=pid, limit=int(limit))
        else:
            tl = timeline_for(self.retriever.mongo, entity_id=next(iter(res["all"])), limit=int(limit))
        return ToolResult(
            summary=f'timeline("{query}") -> {len(tl)} dated events',
            payload={"resolved_entities": sorted(res["all"]), "events": tl})

    def tool_decompose_search(self, *, query: str) -> ToolResult:
        """Split a compound/multi-part question into sub-questions and run an
        entity-cluster fan-out for EACH, merging results. Use for questions with
        several asks so no part is dropped (recall on complex queries)."""
        from src.rag.query_decomp import decompose_query
        parts = decompose_query(query)
        all_new, per_part = [], []
        for p in parts:
            r = self.tool_search_entity_cluster(query=p, limit=40)
            per_part.append({"sub_query": p, "summary": r.summary})
            all_new.extend(r.new_chunks)
        return ToolResult(
            summary=f'decompose_search -> {len(parts)} sub-questions, {len(all_new)} chunks',
            payload={"sub_questions": parts, "per_part": per_part},
            new_chunks=all_new)

    def tool_flow_of_funds(self, *, query: str) -> ToolResult:
        """Money-movement view for a property/entity: dated monetary events
        (conveyances, mortgages, liens, judgments) with parsed amounts,
        chronological. Use for flow-of-funds / asset-tracing questions."""
        from src.timeline.builder import flow_of_funds
        idx = self._entity_index()
        res = idx.resolve(query)
        if not res["all"]:
            return ToolResult(summary=f'flow_of_funds: no entity matched "{query}"', payload={})
        pid = next(iter(res["properties"]), None)
        if pid:
            fof = flow_of_funds(self.retriever.mongo, property_id=pid)
        else:
            fof = flow_of_funds(self.retriever.mongo, entity_id=next(iter(res["all"])))
        return ToolResult(
            summary=f'flow_of_funds("{query}") -> {fof["n_events"]} monetary events, '
                    f'total seen {fof["total_amount_seen"]}',
            payload=fof)

    def tool_evidence_packet(self, *, query: str) -> ToolResult:
        """Court-ready evidence bundle for the property named in `query`: every
        linked document with custody (source file + SHA + pages), grounded
        facts, the timeline, and findings — the full provenance chain."""
        from src.timeline.builder import evidence_packet
        idx = self._entity_index()
        res = idx.resolve(query)
        pid = next(iter(res["properties"]), None)
        if not pid:
            return ToolResult(summary=f'evidence_packet: no property matched "{query}"', payload={})
        pk = evidence_packet(self.retriever.mongo, property_id=pid)
        return ToolResult(
            summary=(f'evidence_packet for {pk.get("address")}: {len(pk.get("documents", []))} docs, '
                     f'{len(pk.get("timeline", []))} events, {len(pk.get("findings", []))} findings'),
            payload=pk)

    def tool_graph_query(self, *, entity_query: str) -> ToolResult:
        """Multi-hop graph traversal from the named entity: its OWNS / member /
        insurance / litigation neighbours via the relationships graph. Use for
        'which properties does David's LLC own and what's their status?'."""
        idx = self._entity_index()
        res = idx.resolve(entity_query)
        if not res["all"]:
            return ToolResult(summary=f'no entity matched "{entity_query}"',
                              payload={"resolved_entities": []})
        rels = self.retriever.mongo.db["relationships"]
        ents = self.retriever.mongo.db["entities"]
        seeds = list(res["all"])
        edges = list(rels.find({"$or": [{"src": {"$in": seeds}}, {"dst": {"$in": seeds}}]},
                               {"_id": 0, "type": 1, "src": 1, "dst": 1, "as_of": 1}).limit(400))
        neigh = {e["src"] for e in edges} | {e["dst"] for e in edges}
        names = {x["_id"]: x.get("canonical_name") or x.get("canonical_address") or x["_id"]
                 for x in ents.find({"_id": {"$in": list(neigh)}},
                                    {"canonical_name": 1, "canonical_address": 1})}
        summary_edges = [{"type": e["type"], "from": names.get(e["src"], e["src"]),
                          "to": names.get(e["dst"], e["dst"]),
                          "as_of": _date_str(e.get("as_of"))} for e in edges[:60]]
        by_type: Dict[str, int] = {}
        for e in edges:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        return ToolResult(
            summary=f'graph_query("{entity_query}") -> {len(edges)} edges {by_type}',
            payload={"resolved_entities": seeds, "edge_counts": by_type,
                     "edges": summary_edges})


# =====================================================================
# Date helper
# =====================================================================

def _parse_iso_date(s: str) -> datetime:
    """
    Accept 'YYYY-MM-DD' (most common) and 'YYYY-MM-DDTHH:MM:SS'. Returns
    a tz-aware UTC datetime. Raises ValueError on bad input.
    """
    s = (s or "").strip()
    if not s:
        raise ValueError("empty date")
    try:
        # Try the easy formats first.
        if "T" in s or " " in s:
            d = datetime.fromisoformat(s.replace(" ", "T"))
        else:
            d = datetime.strptime(s, "%Y-%m-%d")
    except Exception as exc:
        raise ValueError(f"unrecognised date format: {s!r}") from exc
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


# =====================================================================
# Tool schemas (Anthropic tool-use input_schema)
# =====================================================================

def build_tool_specs(box: ToolBox) -> Dict[str, ToolSpec]:
    return {
        "search_entity_cluster": ToolSpec(
            name="search_entity_cluster",
            description=(
                "PREFERRED for any question about a specific property, person, "
                "LLC, or case. Resolves the named entity to its canonical ID and "
                "fans out across EVERY linked source type at once — David's "
                "emails, title reports, insurance, equity schedule, deeds, and "
                "litigation — even when they share no keywords. Use this FIRST "
                "for entity questions; fall back to `search` for keyword/topic "
                "lookups that name no specific entity."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Question naming a property/person/LLC/case."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
                },
                "required": ["query"],
            },
            fn=box.tool_search_entity_cluster,
        ),
        "list_documents_for_entity": ToolSpec(
            name="list_documents_for_entity",
            description=(
                "List the distinct DOCUMENTS linked to a property/person/LLC/case "
                "(type, address, date) — a structured index to see what exists "
                "before pulling chunks. Good for 'what do we have on X?'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entity_query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 120, "default": 60},
                },
                "required": ["entity_query"],
            },
            fn=box.tool_list_documents_for_entity,
        ),
        "graph_query": ToolSpec(
            name="graph_query",
            description=(
                "Traverse the knowledge graph from an entity to its neighbours "
                "(OWNS, HAS_INSURANCE, LITIGATION_ABOUT, ...). Use for multi-hop "
                "questions like 'which properties does this LLC own?'."
            ),
            input_schema={
                "type": "object",
                "properties": {"entity_query": {"type": "string"}},
                "required": ["entity_query"],
            },
            fn=box.tool_graph_query,
        ),
        "timeline": ToolSpec(
            name="timeline",
            description=(
                "Return a CORRECT, pre-sorted, cited chronology of dated events "
                "(conveyances, mortgages, liens, judgments, lis pendens, "
                "insurance, litigation) for a property/entity. Use for 'what "
                "happened and when' / flow-of-funds. The dates are authoritative "
                "— do not reorder."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 60},
                },
                "required": ["query"],
            },
            fn=box.tool_timeline,
        ),
        "evidence_packet": ToolSpec(
            name="evidence_packet",
            description=(
                "Build a court-ready evidence bundle for a property: every linked "
                "document with custody (source file + SHA + pages), grounded facts, "
                "the timeline, and findings. Use when asked to assemble proof / an "
                "exhibit for a property."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            fn=box.tool_evidence_packet,
        ),
        "decompose_search": ToolSpec(
            name="decompose_search",
            description=(
                "Split a compound/multi-part question into sub-questions and run "
                "an entity fan-out for EACH part, merging results. Use FIRST for "
                "questions with several asks (e.g. 'list David's LLCs, the "
                "properties they own, and each one's latest title status')."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            fn=box.tool_decompose_search,
        ),
        "flow_of_funds": ToolSpec(
            name="flow_of_funds",
            description=(
                "Money-movement / asset-tracing view for a property or entity: "
                "dated monetary events (conveyances, mortgages, liens, judgments) "
                "with parsed amounts, in order. Use for flow-of-funds questions."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            fn=box.tool_flow_of_funds,
        ),
        "search": ToolSpec(
            name="search",
            description=(
                "Run a hybrid retrieval (vector + BM25 + filename) over the "
                "entire corpus. Use for keyword/topic lookups that do NOT name a "
                "specific entity (for entity questions prefer search_entity_cluster). "
                "Returns the new chunks (with [#N] indices) and their snippets."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language search query."},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 30, "default": 8},
                },
                "required": ["query"],
            },
            fn=box.tool_search,
        ),
        "search_by_filename": ToolSpec(
            name="search_by_filename",
            description=(
                "Look up documents by filename substring (case-insensitive). "
                "Use when the user names a specific document, or when you "
                "need every chunk of a specific PDF / order / motion."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "filename_pattern": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
                "required": ["filename_pattern"],
            },
            fn=box.tool_search_by_filename,
        ),
        "search_timeframe": ToolSpec(
            name="search_timeframe",
            description=(
                "Search restricted to a date range. Useful for timeline "
                "questions ('What happened between X and Y?'). Pass ISO "
                "dates as YYYY-MM-DD."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                    "query":      {"type": "string", "description": "Optional refine query"},
                    "top_k":      {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
                },
                "required": ["start_date", "end_date"],
            },
            fn=box.tool_search_timeframe,
        ),
        "fetch_full_document": ToolSpec(
            name="fetch_full_document",
            description=(
                "Pull every chunk of one document plus the list of email "
                "occurrences that referenced it. Provide either `sha256` "
                "(if known) OR `chunk_index` (an existing [#N] reference, "
                "from which we resolve the sha256)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "sha256":      {"type": "string", "description": "Document content hash."},
                    "chunk_index": {"type": "integer", "minimum": 1},
                },
            },
            fn=box.tool_fetch_full_document,
        ),
        "find_quote": ToolSpec(
            name="find_quote",
            description=(
                "Find every chunk containing a verbatim phrase (case-"
                "insensitive substring). Use when you want to confirm "
                "where a specific number / date / phrase appears."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "quote": {"type": "string", "minLength": 6},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
                },
                "required": ["quote"],
            },
            fn=box.tool_find_quote,
        ),
        "find_latest_version": ToolSpec(
            name="find_latest_version",
            description=(
                "List all unique-content versions of one document by "
                "filename pattern, sorted newest-first. Use to identify "
                "which version is operative when multiple drafts exist."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "filename_pattern": {"type": "string"},
                    "max_versions":     {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                },
                "required": ["filename_pattern"],
            },
            fn=box.tool_find_latest_version,
        ),
        "compare_versions": ToolSpec(
            name="compare_versions",
            description=(
                "Show 2-6 chunks side-by-side so you can compare them. "
                "Pass [#N] indices that are already in your scratchpad. "
                "Use after `find_latest_version` to see what actually "
                "changed between drafts."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "chunk_indices": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "minItems": 2,
                        "maxItems": 6,
                    },
                },
                "required": ["chunk_indices"],
            },
            fn=box.tool_compare_versions,
        ),
        "verify_claim": ToolSpec(
            name="verify_claim",
            description=(
                "Use the deterministic citation verifier to check that a "
                "verbatim quote ACTUALLY appears in a specific chunk. "
                "Returns VERIFIED / UNVERIFIED / CITATION_INVALID. Use "
                "this mid-investigation to catch your own hallucinations "
                "before committing to the final answer."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "claim":            {"type": "string"},
                    "verbatim_quote":   {"type": "string", "minLength": 8},
                    "source_chunk_id":  {"type": "integer", "minimum": 1},
                    "fuzzy_threshold":  {"type": "number", "minimum": 50, "maximum": 100, "default": 85},
                },
                "required": ["claim", "verbatim_quote", "source_chunk_id"],
            },
            fn=box.tool_verify_claim,
        ),
        "submit_final_answer": ToolSpec(
            name="submit_final_answer",
            description=(
                "Terminate the investigation and submit your final, "
                "verifiable answer. Same shape as Sprint 3: every fact "
                "has a verbatim_quote + source_chunk_id; the prose "
                "`answer` cites with [#N]. The system will run the "
                "verifier on your output one last time before showing "
                "it to the user."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "facts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id":              {"type": "string"},
                                "claim":           {"type": "string"},
                                "source_chunk_id": {"type": "integer", "minimum": 1},
                                "verbatim_quote":  {"type": "string", "minLength": 8},
                                "confidence":      {"type": "string", "enum": ["high", "medium", "low"]},
                                "note":            {"type": "string"},
                            },
                            "required": ["id", "claim", "source_chunk_id", "verbatim_quote", "confidence"],
                        },
                    },
                    "answer": {"type": "string", "minLength": 1},
                    "reasoning_summary": {"type": "string"},
                },
                "required": ["facts", "answer"],
            },
            fn=box.tool_submit_final_answer,
        ),
    }


__all__ = [
    "ToolBox",
    "ToolSpec",
    "ToolResult",
    "build_tool_specs",
]
