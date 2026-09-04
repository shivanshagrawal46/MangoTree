"""The tool palette — twenty-two ways for the agent to ask for what it wants.

Three distinct ways to widen, because the agent knows different things at
different moments:

* it wants *more like this*           → ``search_more`` (similarity, deeper, skips seen)
* it knows *what kind* of thing        → ``fetch_documents`` (structured, no similarity)
* it knows *which* document            → ``fetch_full_document``

Every tool applies the scope's base filter inside its query — the clean-mode
floor. The agent physically cannot reach a privileged chunk in a shareable chat,
or another property's file in a property chat, whichever tool it picks.

Every tool is seen-aware: the toolbox holds the scratchpad, so results already
on the pad are not re-numbered and repeat searches return the next-best
material. The agent is told exactly which indices are new.
"""
from __future__ import annotations

import difflib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg
from mangotree.retrieve.enumeration import ARTIFACT_PROJECTION, enumerate_set
from mangotree.retrieve.hits import Hit
from mangotree.retrieve.pipeline import HybridSearch
from mangotree.retrieve.query_understanding import understand
from mangotree.retrieve.scope import Scope

from .scratchpad import AgentScratchpad

PASSAGE_CHARS = 1800          # per new passage shown to the planner
MAX_NEW_SHOWN = 24            # new passages rendered per tool result; the rest are listed by index


@dataclass
class ToolResult:
    summary: str
    content: str                       # what the planner reads
    new_indices: List[int] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    is_terminal: bool = False
    is_error: bool = False


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    fn: Callable[..., ToolResult]

    def as_anthropic(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _date_filter(date_from: Any, date_to: Any, field_name: str = "date") -> Dict[str, Any]:
    rng: Dict[str, Any] = {}
    s, e = _parse_date(date_from), _parse_date(date_to)
    if s:
        rng["$gte"] = s
    if e:
        rng["$lte"] = e
    return {field_name: rng} if rng else {}


def _and(*parts: Dict[str, Any]) -> Dict[str, Any]:
    ps = [p for p in parts if p]
    if not ps:
        return {}
    return ps[0] if len(ps) == 1 else {"$and": ps}


class ToolBox:
    def __init__(self, hs: HybridSearch, scope: Scope, pad: AgentScratchpad, *,
                 conversation: Sequence[dict] = (), verifier=None):
        self.hs = hs
        self.mongo = hs.mongo
        self.scope = scope
        self.pad = pad
        self.conversation = list(conversation)
        self.verifier = verifier
        self.base = scope.base_filter()
        self.final_payload: Optional[Dict[str, Any]] = None

    # ================================================================== render
    def _render_new(self, hits: Sequence[Hit], new_indices: Sequence[int], *, header: str = "") -> str:
        by_idx = {self.pad.index_of(h.chunk_id): h for h in hits}
        lines: List[str] = []
        if header:
            lines.append(header)
        shown = 0
        for idx in new_indices:
            h = by_idx.get(idx) or self.pad.get(idx)
            if h is None:
                continue
            if shown >= MAX_NEW_SHOWN:
                break
            shown += 1
            body = f"{h.context}\n{h.text}".strip() if h.context else h.text
            if len(body) > PASSAGE_CHARS:
                body = body[:PASSAGE_CHARS] + " …"
            lines.append(f"\n[#{idx}] {h.passage_header()}\n{body}")
        rest = [i for i in new_indices[shown:]]
        if rest:
            lines.append(f"\n(+{len(rest)} more new passages on your pad: [#{rest[0]}]…[#{rest[-1]}]; "
                         f"use fetch_full_document or evidence_packet by index to read them)")
        already = [self.pad.index_of(h.chunk_id) for h in hits if self.pad.index_of(h.chunk_id) not in new_indices]
        already = [i for i in already if i]
        if already:
            lines.append(f"\nAlready on your pad (not repeated): " + ", ".join(f"[#{i}]" for i in already[:30]))
        return "\n".join(lines) if lines else "(nothing new)"

    def _add(self, hits: Sequence[Hit]) -> List[int]:
        return self.pad.add_chunks(hits)

    def _absorb_trace(self, res) -> None:
        self.pad.searches.append(res.rewrite.standalone if res.rewrite else res.question)
        for k, v in (res.trace.get("lists") or {}).items():
            self.pad.lists_seen[k] = self.pad.lists_seen.get(k, 0) + v
        rr = res.trace.get("rerank") or {}
        for stage in ("stage1", "stage2"):
            if rr.get(stage) and not rr[stage].get("ok"):
                self.pad.degrades.append(f"{stage} rerank unavailable")
        if res.rewrite and res.rewrite.degraded:
            self.pad.degrades.append(f"query rewrite degraded: {res.rewrite.degrade_reason}")
        if res.enumeration:
            self.pad.enumerations.append(res.enumeration.as_dict())
        self.pad.scopes_touched.add(res.scope)

    # ================================================================ retrieval
    def tool_search(self, *, query: str, top_k: int = 12) -> ToolResult:
        res = self.hs.search(query, self.scope, conversation=self.conversation, seen=self.pad.seen_ids,
                             keep=max(4, min(top_k, 30)))
        self._absorb_trace(res)
        new = self._add(res.hits)
        head = (f"search: {res.rewrite.standalone!r} — {len(res.retrieved)} reranked, "
                f"{len(res.hits)} in evidence set, {len(new)} new. route: {res.route_reason}")
        if res.enumeration:
            e = res.enumeration
            head += f"\nENUMERATION available: {e.criteria_text} -> {e.denominator}. Call enumerate_set for the list."
        return ToolResult(summary=head.split("\n")[0], content=self._render_new(res.hits, new, header=head),
                          new_indices=new, data={"trace": res.trace})

    def tool_search_more(self, *, query: str, top_k: int = 12) -> ToolResult:
        res = self.hs.search(query, self.scope, conversation=self.conversation, seen=self.pad.seen_ids,
                             depth=2, keep=max(4, min(top_k, 30)), with_enumeration=False)
        self._absorb_trace(res)
        new = self._add(res.hits)
        head = f"search_more: {res.rewrite.standalone!r} — deeper pull, {len(new)} new passages (seen ones skipped)."
        return ToolResult(summary=head, content=self._render_new(res.hits, new, header=head), new_indices=new)

    def tool_search_timeframe(self, *, query: str, date_from: str = None, date_to: str = None, top_k: int = 12) -> ToolResult:
        flt = _date_filter(date_from, date_to)
        if not flt:
            return ToolResult("search_timeframe needs date_from and/or date_to (YYYY-MM-DD)", "Provide date_from and/or date_to.", is_error=True)
        res = self.hs.search(query, self.scope, conversation=self.conversation, seen=self.pad.seen_ids,
                             keep=max(4, min(top_k, 30)), extra_filter=flt, with_enumeration=False)
        self._absorb_trace(res)
        new = self._add(res.hits)
        head = f"search_timeframe {date_from or '…'} → {date_to or '…'}: {len(new)} new passages."
        return ToolResult(summary=head, content=self._render_new(res.hits, new, header=head), new_indices=new)

    def tool_decompose_search(self, *, query: str) -> ToolResult:
        u = understand(query)
        rw = self.hs.rewriter.rewrite(query, u, conversation=self.conversation, scope_hint=self.scope.describe())
        subs = rw.sub_questions or []
        if not subs:
            # Deterministic split on conjunctions / question marks.
            parts = re.split(r"\?\s+|;\s+|\band also\b|\bas well as\b", query)
            subs = [p.strip(" ?") for p in parts if len(p.strip()) > 8][:5]
        if len(subs) <= 1:
            return self.tool_search(query=query, top_k=12)
        blocks: List[str] = [f"decompose_search: {len(subs)} sub-questions"]
        all_new: List[int] = []
        all_hits: List[Hit] = []
        for i, sq in enumerate(subs[:5], 1):
            res = self.hs.search(sq, self.scope, conversation=self.conversation, seen=self.pad.seen_ids,
                                 keep=8, with_enumeration=False)
            self._absorb_trace(res)
            new = self._add(res.hits)
            all_new += new
            all_hits += res.hits
            blocks.append(f"\n=== sub-question {i}: {sq} — {len(new)} new ===")
            blocks.append(self._render_new(res.hits, new))
        return ToolResult(summary=blocks[0], content="\n".join(blocks), new_indices=all_new,
                          data={"sub_questions": subs})

    # ------------------------------------------------------------ structured
    def _artifact_filter(self) -> Dict[str, Any]:
        clauses: List[dict] = []
        if not self.scope.include_privileged:
            clauses.append({"privileged": {"$ne": True}})
        if self.scope.mode == "property" and self.scope.property_id:
            clauses.append({"$or": [{"property_ids": self.scope.property_id},
                                    {"placement": {"$in": ["portfolio", "unplaced"]}}]})
        elif self.scope.property_ids:
            clauses.append({"property_ids": {"$in": list(self.scope.property_ids)}})
        clauses.append({"is_inline_image": {"$ne": True}})
        return _and(*clauses)

    @staticmethod
    def _artifact_line(r: dict) -> str:
        name = r.get("filename") or r.get("subject") or r.get("sha256", "")[:12]
        d = r.get("date")
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else "----------"
        frm = ((r.get("participants") or {}).get("from") or [""])[0]
        props = ",".join(r.get("property_ids") or []) or r.get("placement") or ""
        tags = ",".join(r.get("common_topics") or []) or (r.get("doc_class") or "")
        return f"  {ds}  {str(name)[:70]:<70} [{props}] {tags} {('from ' + frm) if frm else ''}  sha={r.get('sha256','')[:12]}"

    def tool_fetch_documents(self, *, property_ids: List[str] = None, doc_classes: List[str] = None,
                             topics: List[str] = None, extensions: List[str] = None, from_email: str = None,
                             date_from: str = None, date_to: str = None, filename_pattern: str = None,
                             subject_pattern: str = None, placement: str = None, source_type: str = None,
                             limit: int = 40, include_text: bool = False) -> ToolResult:
        crit: List[dict] = [self._artifact_filter()]
        if property_ids and self.scope.mode != "property":
            crit.append({"property_ids": {"$in": list(property_ids)}})
        if doc_classes:
            crit.append({"doc_class": {"$in": list(doc_classes)}})
        if topics:
            crit.append({"common_topics": {"$in": list(topics)}})
        if extensions:
            crit.append({"filename": {"$regex": "(" + "|".join(re.escape(e) for e in extensions) + ")$", "$options": "i"}})
        if from_email:
            crit.append({"participants.from": {"$regex": re.escape(from_email), "$options": "i"}})
        if filename_pattern:
            crit.append({"$or": [{"filename": {"$regex": filename_pattern, "$options": "i"}},
                                 {"source_paths": {"$regex": filename_pattern, "$options": "i"}}]})
        if subject_pattern:
            crit.append({"subject": {"$regex": subject_pattern, "$options": "i"}})
        if placement:
            crit.append({"placement": placement})
        if source_type:
            crit.append({"source_type": source_type})
        crit.append(_date_filter(date_from, date_to))
        query = _and(*crit)
        total = self.mongo.artifacts.count_documents(query)
        rows = list(self.mongo.artifacts.find(query, {**ARTIFACT_PROJECTION, "source_paths": 1}).sort("date", 1).limit(max(1, min(limit, 200))))
        lines = [f"fetch_documents: {len(rows)} of {total} matching documents in scope ({self.scope.describe()})"]
        lines += [self._artifact_line(r) for r in rows]
        new: List[int] = []
        if include_text and rows:
            budget = 60_000
            spent = 0
            hits: List[Hit] = []
            for r in rows:
                for c in self.hs.channels.chunks_of(r["sha256"], filter=self.base)[:3]:
                    t = c.token_count or len(c.text) // 4
                    if spent + t > budget:
                        break
                    c.label = self.scope.label_for(c)
                    hits.append(c)
                    spent += t
            new = self._add(hits)
            lines.append(self._render_new(hits, new, header=f"\nText of the first chunks ({len(new)} new passages):"))
        return ToolResult(summary=lines[0], content="\n".join(lines), new_indices=new,
                          data={"total": total, "shas": [r["sha256"] for r in rows]})

    def _resolve_sha(self, ref: str) -> Optional[str]:
        ref = (ref or "").strip()
        m = re.match(r"^\[?#?(\d+)\]?$", ref)
        if m:
            h = self.pad.get(int(m.group(1)))
            return h.artifact_sha if h else None
        if re.fullmatch(r"[0-9a-f]{12,64}", ref):
            doc = self.mongo.artifacts.find_one({"sha256": {"$regex": f"^{ref}"}}, {"sha256": 1})
            return doc["sha256"] if doc else None
        shas = self.hs.channels.resolve_filenames([ref], filter=self.base)
        return shas[0] if shas else None

    def tool_fetch_full_document(self, *, ref: str, max_tokens: int = cfg.FULLDOC_PER_DOC_TOKEN_BUDGET) -> ToolResult:
        sha = self._resolve_sha(ref)
        if not sha:
            return ToolResult(f"fetch_full_document: could not resolve {ref!r}", f"Could not resolve {ref!r} to a document. Pass a [#N] index, a sha256 prefix, or a filename.", is_error=True)
        chunks = self.hs.channels.chunks_of(sha, filter=self.base)
        if not chunks:
            return ToolResult("fetch_full_document: no readable chunks (outside scope or privileged)", "No readable chunks for that document in this scope.", is_error=True)
        spent, keep = 0, []
        for c in chunks:
            t = c.token_count or len(c.text) // 4
            if spent + t > max_tokens:
                break
            c.origin = "fulldoc"
            c.label = self.scope.label_for(c)
            keep.append(c)
            spent += t
        new = self._add(keep)
        name = keep[0].display_name if keep else sha[:12]
        head = f"fetch_full_document: {name} — {len(keep)}/{len(chunks)} chunks ({spent:,} tokens), {len(new)} new. Read in order:"
        # Whole document in order — including chunks already on the pad, by index.
        lines = [head]
        for c in keep:
            idx = self.pad.index_of(c.chunk_id)
            body = c.text if len(c.text) <= 6000 else c.text[:6000] + " …"
            lines.append(f"\n[#{idx}] ({c.source_ref or f'chunk {c.ordinal}'})\n{body}")
        return ToolResult(summary=head, content="\n".join(lines), new_indices=new, data={"sha": sha})

    def tool_thread_context(self, *, ref: str) -> ToolResult:
        sha = self._resolve_sha(ref)
        if not sha:
            return ToolResult("thread_context: unresolved ref", f"Could not resolve {ref!r}.", is_error=True)
        art = self.mongo.artifacts.find_one({"sha256": sha}, {"thread_key": 1, "source_type": 1, "subject": 1, "parent_email_shas": 1})
        if not art:
            return ToolResult("thread_context: not found", "Document not found.", is_error=True)
        hits: List[Hit] = []
        lines: List[str] = []
        if art.get("source_type") == "email" and art.get("thread_key"):
            sibs = list(self.mongo.artifacts.find({"thread_key": art["thread_key"], "source_type": "email"},
                                                  {"sha256": 1, "subject": 1, "date": 1, "participants.from": 1}).sort("date", 1).limit(30))
            lines.append(f"thread_context: conversation {art.get('subject')!r} — {len(sibs)} messages")
            for s in sibs:
                lines.append(self._artifact_line(s))
                hits += self.hs.channels.chunks_of(s["sha256"], filter=self.base)[:2]
            atts = list(self.mongo.artifacts.find({"source_types": "attachment", "parent_email_shas": {"$in": [s["sha256"] for s in sibs]}},
                                                  {**ARTIFACT_PROJECTION}).limit(40))
            if atts:
                lines.append(f"\n{len(atts)} attachment(s) carried in this conversation:")
                for a in atts:
                    lines.append(self._artifact_line(a))
                    hits += self.hs.channels.chunks_of(a["sha256"], filter=self.base)[:1]
        else:
            parents = art.get("parent_email_shas") or []
            rows = list(self.mongo.artifacts.find({"sha256": {"$in": parents}}, ARTIFACT_PROJECTION).sort("date", 1))
            lines.append(f"thread_context: this document was carried by {len(rows)} email(s):")
            for r in rows:
                lines.append(self._artifact_line(r))
                hits += self.hs.channels.chunks_of(r["sha256"], filter=self.base)[:2]
        for h in hits:
            h.origin = "thread"
            h.label = self.scope.label_for(h)
        new = self._add(hits)
        return ToolResult(summary=lines[0], content="\n".join(lines) + "\n" + self._render_new(hits, new), new_indices=new)

    # -------------------------------------------------------------- entities
    def _entities(self, query: str, limit: int = 12) -> List[dict]:
        terms = [t for t in re.split(r"[,;/]| and ", query) if t.strip()] or [query]
        return self.hs.channels.resolve_entities(terms, limit=limit)

    def tool_search_entity_cluster(self, *, query: str, limit: int = 40) -> ToolResult:
        ents = self._entities(query)
        if not ents:
            return ToolResult(f"search_entity_cluster: no entity matched {query!r}", f"No entity in the knowledge graph matched {query!r}. Try `search` with the name as text.", is_error=False)
        seeds = [e["entity_id"] for e in ents]
        reach = self.hs.channels.graph_neighbors(seeds, hops=1, limit=80)
        vec = self.hs.embedder.embed_query(query)
        hits = self.hs.channels.graph(reach, filter=self.base, k=max(10, min(limit, 80)), query_vector=vec)
        for h in hits:
            h.label = self.scope.label_for(h)
        by_type = Counter(h.source_type for h in hits)
        new = self._add(hits)
        head = (f"search_entity_cluster: {len(ents)} entit{'y' if len(ents)==1 else 'ies'} "
                f"({', '.join((e.get('name') or e['entity_id']) for e in ents[:5])}) → {len(reach)} linked nodes → "
                f"{len(hits)} passages by type {dict(by_type)}; {len(new)} new")
        return ToolResult(summary=head, content=self._render_new(hits, new, header=head), new_indices=new)

    def tool_list_documents_for_entity(self, *, entity_query: str, limit: int = 60) -> ToolResult:
        ents = self._entities(entity_query)
        if not ents:
            return ToolResult("list_documents_for_entity: no match", f"No entity matched {entity_query!r}.")
        ids = [e["entity_id"] for e in ents]
        shas = self.mongo.chunks.distinct("artifact_sha", _and({"entity_ids": {"$in": ids}}, self.base))
        rows = list(self.mongo.artifacts.find({"sha256": {"$in": shas}}, ARTIFACT_PROJECTION).sort("date", 1).limit(limit))
        lines = [f"list_documents_for_entity: {len(rows)} of {len(shas)} documents linked to "
                 f"{', '.join((e.get('name') or e['entity_id']) for e in ents[:5])}"]
        lines += [self._artifact_line(r) for r in rows]
        return ToolResult(summary=lines[0], content="\n".join(lines), data={"shas": shas})

    def tool_graph_query(self, *, entity_query: str, hops: int = 2) -> ToolResult:
        ents = self._entities(entity_query)
        if not ents:
            return ToolResult("graph_query: no match", f"No entity matched {entity_query!r}.")
        ids = [e["entity_id"] for e in ents]
        edges_coll = self.mongo.db["entity_edges"]
        ent_coll = self.mongo.db["entities"]
        seen = set(ids)
        frontier = set(ids)
        lines = [f"graph_query: {', '.join((e.get('name') or e['entity_id']) + ' (' + str(e.get('entity_type')) + ')' for e in ents[:6])}"]
        for hop in range(1, max(1, min(hops, 3)) + 1):
            edges = list(edges_coll.find({"$or": [{"src": {"$in": list(frontier)}}, {"dst": {"$in": list(frontier)}}]}).limit(400))
            if not edges:
                break
            nxt = set()
            for e in edges:
                for side in (e.get("src"), e.get("dst")):
                    if side and side not in seen:
                        nxt.add(side)
            names = {x["entity_id"]: x for x in ent_coll.find({"entity_id": {"$in": list(nxt | frontier)}}, {"entity_id": 1, "name": 1, "entity_type": 1, "property_ids": 1})}
            lines.append(f"\n-- hop {hop}: {len(edges)} edges --")
            for e in edges[:80]:
                s, d = names.get(e.get("src"), {}), names.get(e.get("dst"), {})
                lines.append(f"  {s.get('name', e.get('src'))} --{e.get('edge_type')}--> {d.get('name', e.get('dst'))}"
                             + (f"  [{','.join(e.get('property_ids') or [])}]" if e.get("property_ids") else ""))
            seen |= nxt
            frontier = nxt
        return ToolResult(summary=lines[0], content="\n".join(lines))

    # ------------------------------------------------------------ chronology
    def _props_for_events(self, property_ids: Optional[List[str]]) -> List[str]:
        if self.scope.mode == "property" and self.scope.property_id:
            return [self.scope.property_id]
        return list(property_ids or self.scope.property_ids or [])

    def tool_timeline(self, *, property_ids: List[str] = None, date_from: str = None, date_to: str = None,
                      event_types: List[str] = None, limit: int = 60) -> ToolResult:
        events = self.hs.channels.timeline_events(property_ids=self._props_for_events(property_ids),
                                                  start=_parse_date(date_from), end=_parse_date(date_to),
                                                  event_types=event_types or (), limit=max(5, min(limit, 300)))
        if not events:
            return ToolResult("timeline: no events in that window", "No timeline events match. Widen the period or drop the event type filter.")
        lines = [f"timeline: {len(events)} events" + (f" {date_from or '…'}→{date_to or '…'}" if (date_from or date_to) else "")]
        shas = list(dict.fromkeys(e.get("source_sha") for e in events if e.get("source_sha")))
        for e in events:
            d = e.get("occurred_at")
            ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else "undated"
            amt = f" ${e['amount']:,.2f}" if isinstance(e.get("amount"), (int, float)) else ""
            lines.append(f"  {ds}  [{e.get('property_id')}] {e.get('event_type'):<13} {str(e.get('title'))[:80]}{amt}  src={str(e.get('source_sha'))[:12]}")
            if e.get("quote"):
                lines.append(f"             “{str(e['quote'])[:160]}”")
        # Source chunks for the events, so the planner can cite them.
        hits: List[Hit] = []
        for sha in shas[:20]:
            hits += self.hs.channels.chunks_of(sha, filter=self.base)[:1]
        for h in hits:
            h.label = self.scope.label_for(h)
        new = self._add(hits)
        if new:
            lines.append(f"\nSource passages added: " + ", ".join(f"[#{i}]" for i in new))
        return ToolResult(summary=lines[0], content="\n".join(lines), new_indices=new, data={"events": len(events)})

    def tool_flow_of_funds(self, *, property_ids: List[str] = None, date_from: str = None, date_to: str = None) -> ToolResult:
        events = self.hs.channels.timeline_events(property_ids=self._props_for_events(property_ids),
                                                  start=_parse_date(date_from), end=_parse_date(date_to), limit=1000)
        money = [e for e in events if isinstance(e.get("amount"), (int, float))]
        if not money:
            return ToolResult("flow_of_funds: no dated monetary events", "No timeline events carry an amount in that window. Try `search` for the figures directly.")
        totals: Dict[str, float] = defaultdict(float)
        lines = [f"flow_of_funds: {len(money)} dated monetary events (of {len(events)} events)"]
        for e in money:
            d = e.get("occurred_at")
            ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else "undated"
            totals[e.get("event_type", "other")] += float(e["amount"])
            lines.append(f"  {ds}  [{e.get('property_id')}] {e.get('event_type'):<13} ${float(e['amount']):>14,.2f}  {str(e.get('title'))[:70]}  src={str(e.get('source_sha'))[:12]}")
        lines.append("\nTotals by type: " + "; ".join(f"{k} ${v:,.2f}" for k, v in sorted(totals.items())))
        hits: List[Hit] = []
        for sha in list(dict.fromkeys(e.get("source_sha") for e in money if e.get("source_sha")))[:15]:
            hits += self.hs.channels.chunks_of(sha, filter=self.base)[:1]
        for h in hits:
            h.label = self.scope.label_for(h)
        new = self._add(hits)
        if new:
            lines.append("Source passages added: " + ", ".join(f"[#{i}]" for i in new))
        return ToolResult(summary=lines[0], content="\n".join(lines), new_indices=new)

    # ------------------------------------------------------------- read deep
    def tool_find_quote(self, *, text: str, limit: int = 8) -> ToolResult:
        phrase = " ".join(text.split())
        hits = self.hs.channels.phrase([phrase], filter=self.base, k=limit)
        if not hits:
            hits = self.hs.channels.substring([phrase], filter=self.base, k=limit)
        if not hits:
            return ToolResult(f"find_quote: {phrase!r} not found", f"The exact text {phrase!r} does not appear in any passage in scope.")
        for h in hits:
            h.label = self.scope.label_for(h)
        new = self._add(hits)
        lines = [f"find_quote: {len(hits)} passage(s) contain {phrase!r}"]
        for h in hits:
            idx = self.pad.index_of(h.chunk_id)
            pos = h.text.lower().find(phrase.lower())
            window = h.text[max(0, pos - 300): pos + len(phrase) + 300] if pos >= 0 else h.text[:600]
            lines.append(f"\n[#{idx}] {h.passage_header()}\n…{window}…")
        return ToolResult(summary=lines[0], content="\n".join(lines), new_indices=new)

    def tool_find_latest_version(self, *, filename_pattern: str) -> ToolResult:
        shas = self.hs.channels.resolve_filenames([filename_pattern], filter=self.base)
        rows = list(self.mongo.artifacts.find(_and({"sha256": {"$in": shas}}, self._artifact_filter()), ARTIFACT_PROJECTION).sort("date", -1))
        if not rows:
            return ToolResult("find_latest_version: none", f"No document matched {filename_pattern!r} in scope.")
        lines = [f"find_latest_version: {len(rows)} version(s) of {filename_pattern!r}, newest first:"]
        lines += [self._artifact_line(r) for r in rows[:15]]
        latest = rows[0]
        hits = self.hs.channels.chunks_of(latest["sha256"], filter=self.base)[:3]
        for h in hits:
            h.label = self.scope.label_for(h)
        new = self._add(hits)
        lines.append(self._render_new(hits, new, header=f"\nLatest ({latest.get('filename') or latest.get('subject')}), opening:"))
        return ToolResult(summary=lines[0], content="\n".join(lines), new_indices=new, data={"latest_sha": latest["sha256"], "shas": [r["sha256"] for r in rows]})

    def tool_compare_versions(self, *, ref_a: str, ref_b: str, max_lines: int = 120) -> ToolResult:
        sa, sb = self._resolve_sha(ref_a), self._resolve_sha(ref_b)
        if not sa or not sb:
            return ToolResult("compare_versions: unresolved", "Could not resolve one of the references.", is_error=True)
        ta = "\n".join(c.text for c in self.hs.channels.chunks_of(sa, filter=self.base))
        tb = "\n".join(c.text for c in self.hs.channels.chunks_of(sb, filter=self.base))
        diff = list(difflib.unified_diff(ta.splitlines(), tb.splitlines(), lineterm="", n=1,
                                         fromfile=ref_a, tofile=ref_b))
        changed = [l for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
        lines = [f"compare_versions: {len(changed)} changed lines ({sum(1 for l in changed if l.startswith('-'))} removed, {sum(1 for l in changed if l.startswith('+'))} added)"]
        lines += diff[:max_lines]
        if len(diff) > max_lines:
            lines.append(f"… {len(diff) - max_lines} more diff lines")
        return ToolResult(summary=lines[0], content="\n".join(lines))

    # ---------------------------------------------------------- completeness
    def tool_enumerate_set(self, *, description: str, doc_classes: List[str] = None, topics: List[str] = None,
                           from_email: str = None, date_from: str = None, date_to: str = None,
                           extensions: List[str] = None, limit: int = 200) -> ToolResult:
        u = understand(description)
        if doc_classes:
            u.doc_classes = list(dict.fromkeys(list(u.doc_classes) + list(doc_classes)))
        filters: Dict[str, Any] = {}
        if from_email:
            filters["from_email"] = from_email
        if _parse_date(date_from):
            filters["date_from"] = _parse_date(date_from)
        if _parse_date(date_to):
            filters["date_to"] = _parse_date(date_to)
        if extensions:
            filters["extensions"] = list(extensions)
        if topics:
            filters["topics"] = list(topics)
        res = enumerate_set(self.mongo, description, u, self.scope, filters=filters, limit=max(10, min(limit, 500)))
        self.pad.enumerations.append(res.as_dict())
        lines = [f"enumerate_set: {res.criteria_text} → {res.denominator}" + (" (list truncated)" if res.truncated else "")]
        for it in res.items:
            lines.append(f"  {it['date'] or '----------'}  {str(it['name'])[:70]:<70} [{','.join(it['property_ids']) or it['placement']}] {','.join(it['topics']) or (it['doc_class'] or '')}  sha={str(it['sha256'])[:12]}")
        if res.matched == 0:
            lines.append(f"\nNEGATIVE EVIDENCE: nothing matching in {res.in_scope} documents in scope. State this with the denominator.")
        return ToolResult(summary=lines[0], content="\n".join(lines),
                          data={"in_scope": res.in_scope, "matched": res.matched, "criteria": res.criteria_text})

    def tool_evidence_packet(self, *, sub_question: str, indices: List[int]) -> ToolResult:
        lines = [f"evidence_packet for: {sub_question}"]
        for i in indices[:30]:
            h = self.pad.get(int(i))
            if not h:
                lines.append(f"  [#{i}] — not on pad")
                continue
            lines.append(f"\n[#{i}] {h.passage_header()}\n{h.text[:2500]}")
        return ToolResult(summary=lines[0], content="\n".join(lines))

    # ------------------------------------------------------------- judgement
    def tool_verify_claim(self, *, claim: str, indices: List[int], quote: str = None) -> ToolResult:
        if self.verifier is None:
            return ToolResult("verify_claim: verifier not attached", "Verifier unavailable.", is_error=True)
        report = self.verifier.check_fact({"claim": claim, "quote": quote or "", "sources": [int(i) for i in indices]}, self.pad)
        return ToolResult(f"verify_claim: {report['verdict']}", json.dumps(report, indent=1, default=str))

    def tool_check_policy(self, *, question: str) -> ToolResult:
        coll = self.mongo.db["policies"]
        try:
            rules = list(coll.find({"active": {"$ne": False}}).limit(50))
        except Exception:
            rules = []
        if not rules:
            return ToolResult("check_policy: no rulebook recorded",
                              "No policy rules are recorded in the system yet (collection `policies` is empty). "
                              "Answer from the documents and say that no written policy was available to check against.")
        words = set(re.findall(r"[a-z]{4,}", question.lower()))
        scored = sorted(rules, key=lambda r: -len(words & set(re.findall(r"[a-z]{4,}", (r.get("text") or "").lower()))))
        lines = [f"check_policy: {len(rules)} rules on file; most relevant:"]
        for r in scored[:8]:
            lines.append(f"  - [{r.get('rule_id') or r.get('_id')}] {r.get('title') or ''}: {str(r.get('text'))[:300]}")
        return ToolResult(summary=lines[0], content="\n".join(lines))

    def tool_contractor_profile(self, *, name: str) -> ToolResult:
        ents = self._entities(name, limit=6)
        privileged = {"privileged": {"$ne": True}} if not self.scope.include_privileged else {}
        crit: Dict[str, Any] = privileged
        if ents:
            ids = [e["entity_id"] for e in ents]
            shas = self.mongo.chunks.distinct("artifact_sha", _and({"entity_ids": {"$in": ids}}, privileged))
            crit = _and(privileged, {"sha256": {"$in": shas}})
        else:
            crit = _and(privileged, {"$or": [{"subject": {"$regex": re.escape(name), "$options": "i"}},
                                             {"filename": {"$regex": re.escape(name), "$options": "i"}},
                                             {"participants.from": {"$regex": re.escape(name), "$options": "i"}}]})
        rows = list(self.mongo.artifacts.find(crit, ARTIFACT_PROJECTION).sort("date", 1).limit(400))
        if not rows:
            return ToolResult(f"contractor_profile: nothing on {name!r}", f"No documents mention {name!r}.")
        by_prop: Counter = Counter()
        topics: Counter = Counter()
        for r in rows:
            for p in (r.get("property_ids") or ["(unfiled: " + str(r.get("placement")) + ")"]):
                by_prop[p] += 1
            for t in r.get("common_topics") or ([r.get("doc_class")] if r.get("doc_class") else []):
                topics[t] += 1
        dates = [r["date"] for r in rows if hasattr(r.get("date"), "strftime")]
        lines = [f"contractor_profile: {name} — {len(rows)} documents across every deal (property scope deliberately ignored; privilege respected)"]
        if dates:
            lines.append(f"  active {min(dates):%Y-%m} → {max(dates):%Y-%m}")
        lines.append("  by property: " + ", ".join(f"{p} {n}" for p, n in by_prop.most_common(20)))
        if topics:
            lines.append("  by type: " + ", ".join(f"{t} {n}" for t, n in topics.most_common(12)))
        lines.append("  most recent:")
        lines += [self._artifact_line(r) for r in rows[-12:]]
        return ToolResult(summary=lines[0], content="\n".join(lines))

    def tool_web_search(self, *, query: str) -> ToolResult:
        return ToolResult("web_search: not configured",
                          "External web search is not configured in this deployment. Answer from the records only "
                          "and say that no external source was consulted.")

    # --------------------------------------------------------------- terminal
    def tool_submit_final_answer(self, *, answer: str, facts: List[Dict[str, Any]] = None,
                                 coverage: str = "", open_items: List[str] = None) -> ToolResult:
        self.final_payload = {"answer": answer, "facts": facts or [], "coverage": coverage,
                              "open_items": open_items or []}
        return ToolResult("final answer submitted", "accepted", is_terminal=True, data=self.final_payload)

    # ================================================================== specs
    def specs(self) -> List[ToolSpec]:
        S = lambda **p: {"type": "object", "properties": p}  # noqa: E731
        s = lambda d="": {"type": "string", "description": d}  # noqa: E731
        i = lambda d="": {"type": "integer", "description": d}  # noqa: E731
        b = lambda d="": {"type": "boolean", "description": d}  # noqa: E731
        arr = lambda d="", t="string": {"type": "array", "items": {"type": t}, "description": d}  # noqa: E731

        return [
            ToolSpec("search", "Hybrid retrieval over the records (vector + BM25 + phrase + graph + timeline, "
                     "reranked). For topics and keywords. Never repeats passages you already have.",
                     {**S(query=s("the question or topic"), top_k=i("passages to return, 4-30")), "required": ["query"]}, self.tool_search),
            ToolSpec("search_more", "The next batch for the same question: deeper pull, skips everything on your pad. "
                     "Use when the first results were relevant but you need more of the same.",
                     {**S(query=s(), top_k=i()), "required": ["query"]}, self.tool_search_more),
            ToolSpec("fetch_documents", "Structured fetch — no similarity. When you know WHAT you want: all invoices "
                     "from the title company in Q1 2025; every .xlsx on this property; documents with topic "
                     "wire_instructions. Returns the list with a count (denominator). include_text adds their opening passages.",
                     {**S(property_ids=arr("property_ids (global scope only)"), doc_classes=arr(), topics=arr("fixed topics e.g. draw_request, wire_instructions, assignment_allonge"),
                          extensions=arr("e.g. ['.pdf', '.xlsx']"), from_email=s("address or name fragment"), date_from=s("YYYY-MM-DD"), date_to=s("YYYY-MM-DD"),
                          filename_pattern=s("regex on filename"), subject_pattern=s("regex on email subject"),
                          placement=s("property | portfolio | unplaced | business"), source_type=s("email | attachment | disk_file"),
                          limit=i("max 200"), include_text=b("also load the first passages of each"))}, self.tool_fetch_documents),
            ToolSpec("decompose_search", "Split a compound question into sub-questions and search each, so no part is dropped.",
                     {**S(query=s()), "required": ["query"]}, self.tool_decompose_search),
            ToolSpec("search_timeframe", "Date-bounded hybrid search for chronology questions.",
                     {**S(query=s(), date_from=s("YYYY-MM-DD"), date_to=s("YYYY-MM-DD"), top_k=i()), "required": ["query"]}, self.tool_search_timeframe),
            ToolSpec("search_entity_cluster", "Resolve a person/company to the knowledge graph and fan out across every "
                     "linked document type at once — emails, title work, deeds, invoices — even when they share no keywords.",
                     {**S(query=s("a name, company, or email address"), limit=i()), "required": ["query"]}, self.tool_search_entity_cluster),
            ToolSpec("list_documents_for_entity", "Every document linked to a named entity, as a list (not a similarity search).",
                     {**S(entity_query=s(), limit=i()), "required": ["entity_query"]}, self.tool_list_documents_for_entity),
            ToolSpec("graph_query", "Multi-hop traversal of the knowledge graph from an entity: who is connected to whom, "
                     "through which relationships, on which properties.",
                     {**S(entity_query=s(), hops=i("1-3")), "required": ["entity_query"]}, self.tool_graph_query),
            ToolSpec("thread_context", "For an email: its whole conversation and the attachments carried in it. "
                     "For an attachment: every email that carried it.",
                     {**S(ref=s("[#N] index, sha256 prefix, or filename")), "required": ["ref"]}, self.tool_thread_context),
            ToolSpec("fetch_full_document", "The entire document, in order, when a passage is not enough.",
                     {**S(ref=s("[#N] index, sha256 prefix, or filename"), max_tokens=i()), "required": ["ref"]}, self.tool_fetch_full_document),
            ToolSpec("find_quote", "Locate an exact phrase, number or amount in the records and return the surrounding text byte-for-byte.",
                     {**S(text=s("the exact text to find"), limit=i()), "required": ["text"]}, self.tool_find_quote),
            ToolSpec("find_latest_version", "Versions of a document that exists in several revisions, newest first, with the latest opened.",
                     {**S(filename_pattern=s()), "required": ["filename_pattern"]}, self.tool_find_latest_version),
            ToolSpec("compare_versions", "Line diff between two documents.",
                     {**S(ref_a=s(), ref_b=s(), max_lines=i()), "required": ["ref_a", "ref_b"]}, self.tool_compare_versions),
            ToolSpec("timeline", "The property timeline: dated, quote-verified events (origination, assignment, funding, "
                     "payment, payoff, extension, default, legal, construction, listing_sale, title, tax_insurance, communication).",
                     {**S(property_ids=arr(), date_from=s(), date_to=s(), event_types=arr(), limit=i())}, self.tool_timeline),
            ToolSpec("flow_of_funds", "Dated monetary events in order, with totals by type — draws, payments, payoffs, invoices.",
                     {**S(property_ids=arr(), date_from=s(), date_to=s())}, self.tool_flow_of_funds),
            ToolSpec("enumerate_set", "Complete sets, bypassing similarity. For 'all', 'every', 'how many', 'is there any'. "
                     "Returns every matching document and the denominator; an empty result is negative evidence you can state.",
                     {**S(description=s("what to enumerate, in words"), doc_classes=arr(), topics=arr(), from_email=s(),
                          date_from=s(), date_to=s(), extensions=arr(), limit=i()), "required": ["description"]}, self.tool_enumerate_set),
            ToolSpec("evidence_packet", "Bundle passages already on your pad, by index, under a sub-question — for assembling the memo.",
                     {**S(sub_question=s(), indices=arr(t="integer")), "required": ["sub_question", "indices"]}, self.tool_evidence_packet),
            ToolSpec("verify_claim", "Check a proposed fact against cited passages before you rely on it: quote byte-for-byte, numbers and dates present.",
                     {**S(claim=s(), indices=arr(t="integer"), quote=s("verbatim quote supporting the claim")), "required": ["claim", "indices"]}, self.tool_verify_claim),
            ToolSpec("check_policy", "The firm's written rules, if any are recorded, relevant to a question.",
                     {**S(question=s()), "required": ["question"]}, self.tool_check_policy),
            ToolSpec("contractor_profile", "One contractor's or vendor's history across every deal, including deals outside the registry.",
                     {**S(name=s()), "required": ["name"]}, self.tool_contractor_profile),
            ToolSpec("web_search", "External web search. Clearly labelled; never mixed with corpus evidence.",
                     {**S(query=s()), "required": ["query"]}, self.tool_web_search),
            ToolSpec("submit_final_answer", "Terminal. The answer with [#N] citations; `facts` = every load-bearing fact with its "
                     "verbatim quote and source indices (checked byte-for-byte); `coverage` = what you searched and did not; "
                     "`open_items` = what could not be verified.",
                     {**S(answer=s("the full answer, with [#N] citations"),
                          facts={"type": "array", "items": {"type": "object", "properties": {
                              "claim": {"type": "string"}, "quote": {"type": "string"},
                              "sources": {"type": "array", "items": {"type": "integer"}}}, "required": ["claim", "sources"]}},
                          coverage=s(), open_items=arr()), "required": ["answer", "facts"]}, self.tool_submit_final_answer),
        ]
