"""The arrival chain — every stage the corpus went through, run for what is new.

Each stage already selects its own pending work (extraction: no text yet;
segregation: no Opus 5 decision yet; indexing: not embedded; questions: not on
the current embed version; timeline: not read by the model yet), so the chain
is simply the stages in the right order. Nothing is re-done; a stage with no
pending work returns in milliseconds.

The order is the part that matters:

    1. extraction / OCR of attachments  — Opus 5 must read the documents, not filenames
    2. segregation                      — ONE Opus 5 call per email carrying every
                                          attachment's text; a decision per attachment
    3. thread-context pass              — unresolved replies judged with their thread
    4. common-store classification      — portfolio vs business for anything unfiled
    5. attachment inheritance + placement
    6. chunk + Tier-1 + Tier-2 + questions + embed (one voyage-4-large vector)
    7. document-level summary vector; graph link for the new chunks
    8. timeline events (deterministic + Opus 5, quote-verified)
    9. tasks + change cards for the affected properties — debounced

Debounce: tasks and cards wait until a property has been quiet for
``DEBOUNCE_MINUTES``, then run once over everything new. A five-message thread
therefore yields one card that read the whole exchange, not five.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

DEBOUNCE_MINUTES = int(os.environ.get("MT_DEBOUNCE_MINUTES", "10"))


class ArrivalChain:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self._lock = threading.Lock()
        self._pending_props: Dict[str, datetime] = {}      # property_id -> last new artifact time
        self.runs = mongo.db["arrival_runs"]
        self.runs.create_index([("started_at", -1)], name="ix_arrival_started")

    # ------------------------------------------------------------------ stages
    def _extract(self, trace: dict, only_shas: Optional[Sequence[str]] = None) -> None:
        from mangotree.extract.runner import ExtractionRunner
        r = ExtractionRunner(self.mongo, api_key=SETTINGS.anthropic_api_key, openai_api_key=SETTINGS.openai_api_key or None)
        stats = r.run(only_shas=only_shas)
        trace["extraction"] = stats.as_dict() if hasattr(stats, "as_dict") else str(stats)

    def _segregate(self, trace: dict) -> None:
        from mangotree.resolve.segregation_runner import SegregationRunner
        from mangotree.resolve.thread_context import ThreadContextRunner
        seg = SegregationRunner(self.mongo, SETTINGS.anthropic_api_key)
        s = seg.run()
        trace["segregation"] = s.as_dict()
        if s.review_queued:
            t = ThreadContextRunner(self.mongo, SETTINGS.anthropic_api_key).run()
            trace["thread_context"] = {k: v for k, v in t.as_dict().items() if k != "assignments"}

    def _classify_common(self, trace: dict) -> None:
        from mangotree.resolve.common_classifier import CommonClassificationRunner
        s = CommonClassificationRunner(self.mongo, SETTINGS.anthropic_api_key).run()
        trace["common_classification"] = {k: v for k, v in s.as_dict().items() if k != "topics"}

    def _inherit_and_place(self, shas: Sequence[str], trace: dict) -> None:
        from mangotree.resolve.attachment_inherit import inherit_properties
        stats, _ = inherit_properties(self.mongo, apply=True)
        trace["attachment_inherit"] = stats.__dict__ if hasattr(stats, "__dict__") else str(stats)
        # placement for anything touched (email, its attachments, and any chunks)
        art = self.mongo.artifacts
        touched = list(art.find({"$or": [{"sha256": {"$in": list(shas)}}, {"parent_email_shas": {"$in": list(shas)}}]},
                                {"sha256": 1, "property_ids": 1, "common_kind": 1}))
        for a in touched:
            placement = "property" if a.get("property_ids") else (a.get("common_kind") if a.get("common_kind") in ("portfolio", "business") else "unplaced")
            art.update_one({"sha256": a["sha256"]}, {"$set": {"placement": placement}})
            self.mongo.chunks.update_many({"artifact_sha": a["sha256"]}, {"$set": {"placement": placement, "property_ids": a.get("property_ids") or []}})
        trace["placement_written"] = len(touched)

    def _index(self, trace: dict) -> None:
        from mangotree.index.indexer import Indexer
        from mangotree.index.questions import QuestionAugmenter
        ix = Indexer(self.mongo, api_key=SETTINGS.voyage_api_key, anthropic_api_key=SETTINGS.anthropic_api_key, tier1=True)
        s = ix.run()
        trace["indexing"] = s.as_dict() if hasattr(s, "as_dict") else str(s)
        q = QuestionAugmenter(self.mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key).run()
        trace["questions"] = q.as_dict()

    def _doc_summaries_and_graph(self, shas: Sequence[str], trace: dict) -> None:
        from mangotree.embed.embedder import Embedder
        from mangotree.config.models import EMBEDDING_MODEL
        from pymongo import UpdateOne
        coll = self.mongo.db["doc_summaries"]
        want = set(shas) | {a["sha256"] for a in self.mongo.artifacts.find({"parent_email_shas": {"$in": list(shas)}}, {"sha256": 1})}
        have = {d["artifact_sha"] for d in coll.find({"artifact_sha": {"$in": list(want)}}, {"artifact_sha": 1})}
        todo = [s for s in want if s not in have]
        if todo:
            rows = list(self.mongo.chunks.aggregate([
                {"$match": {"artifact_sha": {"$in": todo}}}, {"$sort": {"ordinal": 1}},
                {"$group": {"_id": "$artifact_sha", "display_name": {"$first": "$display_name"}, "tier2": {"$first": "$tier2"},
                            "doc_class": {"$first": "$doc_class"}, "property_ids": {"$first": "$property_ids"}, "placement": {"$first": "$placement"},
                            "privileged": {"$first": "$privileged"}, "source_type": {"$first": "$source_type"}, "date": {"$first": "$date"},
                            "extension": {"$first": "$extension"}, "from_email": {"$first": "$from_email"}, "texts": {"$push": "$text"}, "n": {"$sum": 1}}}]))
            if rows:
                emb = Embedder(SETTINGS.voyage_api_key)
                texts = ["\n".join(p for p in [r.get("display_name") or "", r.get("tier2") or "", f"type: {r['doc_class']}" if r.get("doc_class") else "",
                                               " ".join(t for t in (r.get("texts") or [])[:2] if t)[:2000]] if p) or "document" for r in rows]
                vectors = emb.embed_documents(texts)
                now = datetime.now(timezone.utc)
                ops = [UpdateOne({"artifact_sha": r["_id"]}, {"$set": {
                    "artifact_sha": r["_id"], "display_name": r.get("display_name"), "doc_class": r.get("doc_class"),
                    "property_ids": r.get("property_ids") or [], "placement": r.get("placement"), "privileged": bool(r.get("privileged")),
                    "source_type": r.get("source_type"), "date": r.get("date"), "extension": r.get("extension"), "from_email": r.get("from_email"),
                    "chunks": r.get("n"), "embedding": v, "embedding_model": EMBEDDING_MODEL, "version": "docsum-v1", "updated_at": now}}, upsert=True)
                    for r, v in zip(rows, vectors) if v is not None]
                if ops:
                    coll.bulk_write(ops, ordered=False)
        trace["doc_summaries"] = len(todo)
        # Graph: the builder is a full pass over the corpus (minutes). Chunks of new
        # artifacts are linked nightly by the sweep; the agent's other channels
        # cover them meanwhile. Recorded so the trace is honest about it.
        trace["graph"] = "deferred to nightly sweep"

    def _timeline(self, property_ids: Sequence[str], trace: dict) -> None:
        from mangotree.timeline.runner import TimelineBuilder
        if not property_ids:
            return
        tb = TimelineBuilder(self.mongo, anthropic_api_key=SETTINGS.anthropic_api_key, concurrency=10)
        out = tb.run(property_ids=list(property_ids), use_model=True)
        trace["timeline"] = {k: v for k, v in (out or {}).items() if k in ("events_kept", "documents", "calls", "events_proposed")}

    def _tasks_and_cards(self, property_ids: Sequence[str], trace: dict) -> None:
        from mangotree.briefing.cards import CardDetector
        from mangotree.briefing.dossier import PropertyDossier
        from mangotree.tasks.extractor import TaskExtractor
        if not property_ids:
            return
        # Investigate first (admin directive 2026-09-03): a fresh agent run over
        # the property, now including the documents that just arrived, so tasks,
        # cards and the Wes agenda are judged with the whole history in view.
        dossier = PropertyDossier(self.mongo, anthropic_api_key=SETTINGS.anthropic_api_key,
                                  voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "")
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda p: dossier.block(p, force=True), property_ids))
        trace["dossier"] = f"rebuilt for {len(property_ids)}"
        # Close before creating: read the new documents against every open issue,
        # card and task, so what they settled is marked resolved and nothing
        # already done is raised again below.
        from mangotree.briefing.resolution import ResolutionPass
        rp = ResolutionPass(self.mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
        trace["resolution"] = {p: rp.run_for(p) for p in property_ids}
        te = TaskExtractor(self.mongo, anthropic_api_key=SETTINGS.anthropic_api_key).run(list(property_ids), concurrency=3)
        trace["tasks"] = te.per_property
        cd = CardDetector(self.mongo, anthropic_api_key=SETTINGS.anthropic_api_key).run(list(property_ids), concurrency=3)
        trace["cards"] = cd
        # The Wes agenda reads tasks, cards and Wes's items, so it goes last. New
        # mail on a property therefore re-ranks its three issues within the
        # debounce window, not just at 6 a.m.; "discussed" ticks are re-read.
        from mangotree.briefing.wes_agenda import WesAgenda
        trace["wes_agenda"] = WesAgenda(self.mongo, anthropic_api_key=SETTINGS.anthropic_api_key).run(list(property_ids), force=True, concurrency=3)

    # --------------------------------------------------------------- helpers
    def _properties_of(self, shas: Sequence[str]) -> List[str]:
        pids: Set[str] = set()
        for a in self.mongo.artifacts.find({"$or": [{"sha256": {"$in": list(shas)}}, {"parent_email_shas": {"$in": list(shas)}}]}, {"property_ids": 1}):
            pids.update(a.get("property_ids") or [])
        return sorted(pids)

    def _invalidate(self) -> None:
        try:
            from mangotree.api import data
            data.invalidate_portfolio()
        except Exception:
            pass

    # ------------------------------------------------------------------- run
    def process(self, new_email_shas: Sequence[str]) -> Dict[str, Any]:
        """Stages 1–8 now for the new emails; tasks and cards debounced per property."""
        if not new_email_shas:
            return {"skipped": "nothing new"}
        started = datetime.now(timezone.utc)
        trace: Dict[str, Any] = {"emails": len(new_email_shas)}
        with self._lock:
            for name, fn in (("extract", lambda: self._extract(trace)),
                             ("segregate", lambda: self._segregate(trace)),
                             ("classify_common", lambda: self._classify_common(trace)),
                             ("inherit_place", lambda: self._inherit_and_place(new_email_shas, trace)),
                             ("index", lambda: self._index(trace)),
                             ("summaries", lambda: self._doc_summaries_and_graph(new_email_shas, trace))):
                t0 = time.time()
                try:
                    fn()
                except Exception as exc:
                    logger.exception("arrival stage %s failed", name)
                    trace[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:300]
                trace[f"{name}_ms"] = int((time.time() - t0) * 1000)
            props = self._properties_of(new_email_shas)
            trace["properties"] = props
            t0 = time.time()
            try:
                self._timeline(props, trace)
            except Exception as exc:
                logger.exception("arrival timeline failed")
                trace["timeline_error"] = f"{type(exc).__name__}: {exc}"[:300]
            trace["timeline_ms"] = int((time.time() - t0) * 1000)
            now = datetime.now(timezone.utc)
            for p in props:
                self._pending_props[p] = now
            self._invalidate()
        trace["finished_at"] = datetime.now(timezone.utc)
        trace["elapsed_s"] = round((trace["finished_at"] - started).total_seconds(), 1)
        self.runs.insert_one({"started_at": started, **trace})
        return trace

    def process_documents(self, shas: Sequence[str], property_id: str, *, emit=None) -> Dict[str, Any]:
        """Stages for files whose property is already known (uploads, disk files).

        Same chain as ``process`` minus the two model passes that decide *where*
        a document belongs — segregation and common-store classification — because
        the property page it was added from has already answered that. Extraction
        runs only for these shas via the runner's normal pending query (they are
        the pending ones), then indexing, summaries, timeline, and the debounced
        tasks/cards for the property.
        """
        say = emit or (lambda *_: None)
        if not shas:
            return {"skipped": "nothing new"}
        started = datetime.now(timezone.utc)
        trace: Dict[str, Any] = {"kind": "documents", "documents": len(shas), "properties": [property_id]}
        with self._lock:
            for name, label, fn in (
                ("extract", "Reading the document (OCR where needed)…", lambda: self._extract(trace, only_shas=shas)),
                ("index", "Chunking, writing context, generating questions, embedding…", lambda: self._index(trace)),
                ("summaries", "Document summary vector…", lambda: self._doc_summaries_and_graph(shas, trace)),
                ("timeline", "Dated events for the timeline…", lambda: self._timeline([property_id], trace)),
            ):
                say("status", {"text": label})
                t0 = time.time()
                try:
                    fn()
                except Exception as exc:
                    logger.exception("arrival (documents) stage %s failed", name)
                    trace[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:300]
                trace[f"{name}_ms"] = int((time.time() - t0) * 1000)
            self._pending_props[property_id] = datetime.now(timezone.utc)
            self._invalidate()
        # What the reader wants to know: did it become searchable?
        n_chunks = self.mongo.chunks.count_documents({"artifact_sha": {"$in": list(shas)}})
        status = {a["sha256"]: (a.get("extraction") or {}).get("status")
                  for a in self.mongo.artifacts.find({"sha256": {"$in": list(shas)}}, {"sha256": 1, "extraction.status": 1})}
        trace["chunks"] = n_chunks
        trace["extraction_status"] = status
        trace["finished_at"] = datetime.now(timezone.utc)
        trace["elapsed_s"] = round((trace["finished_at"] - started).total_seconds(), 1)
        self.runs.insert_one({"started_at": started, **trace})
        return trace

    def flush_debounced(self, *, force: bool = False) -> Dict[str, Any]:
        """Run tasks + cards for properties quiet for DEBOUNCE_MINUTES."""
        now = datetime.now(timezone.utc)
        with self._lock:
            due = [p for p, t in self._pending_props.items() if force or (now - t) >= timedelta(minutes=DEBOUNCE_MINUTES)]
            for p in due:
                self._pending_props.pop(p, None)
        if not due:
            return {}
        trace: Dict[str, Any] = {"properties": due, "started_at": now}
        try:
            self._tasks_and_cards(due, trace)
        except Exception as exc:
            logger.exception("debounced tasks/cards failed")
            trace["error"] = f"{type(exc).__name__}: {exc}"[:300]
        self._invalidate()
        self.runs.insert_one({"kind": "tasks_cards", **trace, "finished_at": datetime.now(timezone.utc)})
        return trace

    def nightly(self) -> Dict[str, Any]:
        """Correctness pass: graph rebuild + anything any stage still owes."""
        trace: Dict[str, Any] = {"started_at": datetime.now(timezone.utc)}
        try:
            from mangotree.graph.builder import KnowledgeGraphBuilder
            trace["graph"] = KnowledgeGraphBuilder(self.mongo).build(link_chunks=True)
        except Exception as exc:
            trace["graph_error"] = f"{type(exc).__name__}: {exc}"[:300]
        for name, fn in (("extract", lambda: self._extract(trace)), ("segregate", lambda: self._segregate(trace)),
                         ("classify_common", lambda: self._classify_common(trace)), ("index", lambda: self._index(trace))):
            try:
                fn()
            except Exception as exc:
                trace[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:300]
        self._invalidate()
        trace["finished_at"] = datetime.now(timezone.utc)
        self.runs.insert_one({"kind": "nightly", **trace})
        return trace
