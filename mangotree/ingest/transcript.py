"""Meeting transcripts — a call with Wes, a Granola export, notes typed after a call.

A transcript is the one kind of user-added document whose property is *not*
known from where it was added: one call with Wes walks through Varnum, Lane Pl,
Ridge Road and Briardale in twenty minutes. So unlike ``UploadIngestor`` (the
property page names the property, no model call) a transcript goes through the
same Opus 5 segregation an email attachment does, and can land on several
properties at once. Everything after that is the ordinary chain:

    store bytes -> extract text -> Opus 5 places it (multi-property, full-text
    alias scan for anything it stopped reading before) -> chunk, Tier-1/2
    context, questions, embedding -> document summary vector -> timeline events
    -> picked up by the 2 a.m. cycle (placed_at) for resolution, tasks, cards
    and the Wes issues.

The segregator is fed the transcript exactly as it would see a document on an
email: a short cover ("call with Wes, 15 Sep") as the message and the transcript
as the single attachment, so the whole 30k-token attachment budget is available
and the decision recorded on the artifact is the attachment decision — the same
shape every other segregated document carries.
"""
from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from mangotree.config.registry import PEOPLE
from mangotree.config.settings import SETTINGS
from mangotree.core.hashing import sha256_bytes
from mangotree.core.logging import logger
from mangotree.ingest.disk_ingest import KIND_BY_EXT
from mangotree.storage.mongo import Mongo

DOC_CLASS = "meeting_transcript"
PEOPLE_INDEX = {p.person_id: p for p in PEOPLE}


def _person_email(person_id: str) -> str:
    p = PEOPLE_INDEX.get(person_id)
    addresses = list(p.addresses) if p else []
    return f"{p.display_name} <{addresses[0]}>" if addresses else person_id


class TranscriptIngestor:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo

    # ------------------------------------------------------------------ store
    def store(
        self,
        data: bytes,
        *,
        filename: str,
        title: str,
        meeting_date: datetime,
        participants: Sequence[str],
        added_by: str,
        source: str = "manual",
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write the artifact (or add this origin to existing bytes). Returns
        ``{"sha256", "status": new|duplicate}``."""
        if not data:
            raise ValueError("empty file")
        filename = Path(filename or "transcript").name
        sha = sha256_bytes(data)
        now = datetime.now(timezone.utc)
        ext = Path(filename).suffix.lower()
        if meeting_date.tzinfo is None:
            meeting_date = meeting_date.replace(tzinfo=timezone.utc)
        meeting = {"title": title, "date": meeting_date, "participants": list(participants),
                   "source": source, "added_by": added_by, "added_at": now, "note": note or None}

        self.mongo.put_original(sha, data, filename, {"source_type": "upload", "doc_class": DOC_CLASS,
                                                      "uploaded_by": added_by, "meeting_title": title})
        art = self.mongo.artifacts
        existing = art.find_one({"sha256": sha}, {"sha256": 1, "filename": 1, "created_at": 1, "source_type": 1})
        if existing is not None:
            art.update_one({"sha256": sha}, {"$addToSet": {"source_types": "upload"},
                                            "$set": {"meeting": meeting, "updated_at": now}})
            logger.info("transcript already in the system as %s (%s); nothing stored twice",
                        existing.get("filename"), existing.get("source_type"))
            return {"sha256": sha, "status": "duplicate", "filename": existing.get("filename")}

        artifact = {
            "sha256": sha,
            "source_type": "upload",
            "filename": filename,
            "extension": ext,
            "content_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
            "kind": KIND_BY_EXT.get(ext, "unknown"),
            "doc_class": DOC_CLASS,
            "raw_size": len(data),
            # The meeting date, not the upload moment: timeline, cards and the
            # morning change-check all read ``date`` as "when this happened".
            "date": meeting_date,
            "privileged": False,
            "access": "normal",
            "person_ids": [p for p in participants if p in PEOPLE_INDEX],
            "meeting": meeting,
            # Nothing is assumed about the property. Opus 5 decides in process().
            "property_ids": [],
            "placement": "unplaced",
            "scope": "common",
            "resolution": {"status": "pending_segregation",
                           "notes": [f"meeting transcript added by {added_by}; property decided by Opus 5"]},
            "extraction": {"status": "pending"},
            "updated_at": now,
        }
        art.update_one({"sha256": sha},
                       {"$set": artifact, "$addToSet": {"source_types": "upload"},
                        "$setOnInsert": {"created_at": now, "first_run_id": f"transcript-{now:%Y%m%d}"}},
                       upsert=True)
        logger.info("transcript stored: %s (%d bytes) — %s", filename, len(data), title)
        return {"sha256": sha, "status": "new", "filename": filename}

    # ------------------------------------------------------- by headings
    @staticmethod
    def split_by_property_headings(text: str) -> List[Dict[str, Any]]:
        """Sections of a transcript whose author put a property name on its own
        line before each part (Rakesh's Wes-response notes). Returns
        [{heading, property_id|None, start, end}] in order; text before the
        first heading is a preamble (property None); a heading that names no
        registered property (e.g. "other properties in pipeline") is None too."""
        from mangotree.resolve.property_resolver import _match_aliases
        lines = text.split("\n")
        marks: List[tuple] = []
        pos = 0
        for line in lines:
            s = line.strip()
            if s and len(s) < 70 and not s.startswith(("Me:", "Wes", "Them:", "Rakesh")) and not s.endswith((".", "?", "!", ",")):
                hits = _match_aliases(s, "alias_body")
                strong = [pid for pid, h in hits.items() if not any("(ambiguous)" in x for x in h.signals) or any(k in x for x in h.signals for k in ("(numbered)", "(single)"))]
                if len(strong) == 1 or (s.lower().startswith("other propert")):
                    marks.append((pos, s, strong[0] if len(strong) == 1 else None))
            pos += len(line) + 1
        sections: List[Dict[str, Any]] = []
        if not marks or marks[0][0] > 0:
            sections.append({"heading": "(preamble)", "property_id": None, "start": 0, "end": marks[0][0] if marks else len(text)})
        for i, (start, heading, pid) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            sections.append({"heading": heading, "property_id": pid, "start": start, "end": end})
        return sections

    # ---------------------------------------------------------------- process
    def process(self, sha: str, *, emit=None, by_headings: bool = False) -> Dict[str, Any]:
        """Extract, place, index, summarise, timeline. Idempotent: every stage
        selects its own pending work.

        ``by_headings``: the author labelled each part with the property name;
        the headings decide the placement (no Opus 5 segregation) and every
        chunk is attributed to its own section — the exact per-property split a
        multi-property transcript needs."""
        from mangotree.pipeline.arrival import ArrivalChain

        say = emit or (lambda *_: None)
        chain = ArrivalChain(self.mongo)
        trace: Dict[str, Any] = {"kind": "transcript", "sha256": sha}
        art = self.mongo.artifacts
        doc = art.find_one({"sha256": sha})
        if not doc:
            raise ValueError(f"no artifact {sha}")
        meeting = doc.get("meeting") or {}

        # 1. text
        say("status", {"text": "Reading the transcript…"})
        chain._extract(trace, only_shas=[sha])
        doc = art.find_one({"sha256": sha}, {"text": 1, "filename": 1, "content_type": 1, "extraction.status": 1, "segregation": 1})
        text = doc.get("text") or ""
        trace["extraction_status"] = (doc.get("extraction") or {}).get("status")
        trace["chars"] = len(text)
        if not text.strip():
            trace["error"] = "no text extracted; segregation skipped"
            return trace

        sections: List[Dict[str, Any]] = []
        # Standard for transcripts (admin directive 2026-09-17): when the author
        # put property names as headings, each property's part is stored under
        # that property only and the rest goes to the common store. Detected
        # automatically — two or more property sections — else Opus 5 places it.
        if not by_headings:
            probe = self.split_by_property_headings(text)
            if sum(1 for s in probe if s.get("property_id")) >= 2:
                by_headings = True
                trace["headings_detected"] = True
        if by_headings:
            # 2a. The author's headings decide. No model call; exact per-section split.
            sections = self.split_by_property_headings(text)
            props = sorted({s["property_id"] for s in sections if s.get("property_id")})
            say("status", {"text": f"Placing by the document's headings: {len(props)} properties, {sum(1 for s in sections if not s.get('property_id'))} section(s) to the common store…"})
            now = datetime.now(timezone.utc)
            art.update_one({"sha256": sha}, {"$set": {
                "property_ids": props, "placed_at": now if props else None, "placement": "property" if props else "unplaced",
                "scope": "property" if props else "common",
                "segregation": {"properties": props, "confidence": 1.0, "unresolved": False, "out_of_scope": [s["heading"] for s in sections if not s.get("property_id") and s["heading"] != "(preamble)"],
                                "reasoning": "placed by the document's own property headings", "fallback_used": "document_headings", "scope": "property" if props else "common",
                                "model": "headings", "decided_at": now},
                "resolution_status": "resolved" if props else "no_property", "resolution.status": "segregated", "updated_at": now,
                # The indexer's chunker cuts at these boundaries and attributes
                # each segment to its section — exact per-property chunks.
                "chunk_sections": [{"start": s["start"], "end": s["end"], "property_id": s.get("property_id"), "heading": s["heading"]} for s in sections]}})
            trace["segregation"] = {"properties": props, "by": "headings", "sections": [(s["heading"], s.get("property_id")) for s in sections]}
            placement = "property" if props else "unplaced"
        else:
            props, placement = self._segregate_with_opus(sha, doc, text, meeting, trace, say)
            if props is None:
                return trace

        # 3. searchable: chunks, context, questions, embedding; summary vector.
        say("status", {"text": "Chunking, writing context, generating questions, embedding…"})
        chain._index(trace)
        if by_headings:
            # Chunks were cut and attributed by section inside the chunker; just
            # set placement/scope per chunk and report the split.
            counts: Dict[str, int] = {}
            for c in self.mongo.chunks.find({"artifact_sha": sha}, {"_id": 1, "property_ids": 1}):
                pids = list(c.get("property_ids") or [])
                self.mongo.chunks.update_one({"_id": c["_id"]}, {"$set": {"placement": "property" if pids else "portfolio", "scope": "property" if pids else "common"}})
                for p in (pids or ["(common)"]):
                    counts[p] = counts.get(p, 0) + 1
            trace["chunk_attribution"] = counts
        else:
            self._widen_multi_property_chunks(sha, props, placement)
        chain._doc_summaries_and_graph([sha], trace)
        self.mongo.db["doc_summaries"].update_many({"artifact_sha": sha}, {"$set": {"property_ids": props, "placement": placement}})

        # 4. dated events for each property it touches
        if props:
            say("status", {"text": f"Dated events for {len(props)} propert{'y' if len(props) == 1 else 'ies'}…"})
            try:
                chain._timeline(props, trace)
            except Exception as exc:  # timeline is best-effort; the document is already searchable
                logger.exception("transcript timeline failed")
                trace["timeline_error"] = f"{type(exc).__name__}: {exc}"[:300]
        chain._invalidate()
        trace["chunks"] = self.mongo.chunks.count_documents({"artifact_sha": sha})
        trace["finished_at"] = datetime.now(timezone.utc)
        chain.runs.insert_one({"started_at": trace["finished_at"], **trace})
        return trace

    def _widen_multi_property_chunks(self, sha: str, props: List[str], placement: str) -> None:
        """A transcript without headings: the segmenter yields one long
        multi-property segment and the alias matcher misses spoken names. Where a
        chunk is already multi-property, add the properties Opus 5 read the call
        as covering; single-property chunks are left exactly as read."""
        self.mongo.chunks.update_many({"artifact_sha": sha}, {"$set": {"placement": placement, "scope": "property" if props else "common"}})
        for c in self.mongo.chunks.find({"artifact_sha": sha}, {"_id": 1, "property_ids": 1}):
            have = list(c.get("property_ids") or [])
            if len(have) >= 3:
                missing = [p for p in props if p not in have]
                if missing:
                    self.mongo.chunks.update_one({"_id": c["_id"]}, {"$set": {"property_ids": sorted(have + missing)}})

    def _segregate_with_opus(self, sha, doc, text, meeting, trace, say):
        """2b. Opus 5 decides the properties — the transcript rendered as the one
        document on a short cover message, same as an email attachment.
        Returns (props, placement), or (None, None) after a failed call."""
        from mangotree.resolve.segregation_runner import SegregationRunner, _multi_property_extra
        from mangotree.resolve.segregator import ItemDecision
        art = self.mongo.artifacts
        say("status", {"text": "Opus 5 is deciding which properties this call covers…"})
        runner = SegregationRunner(self.mongo, SETTINGS.anthropic_api_key)
        participants = list(meeting.get("participants") or [])
        rakesh = _person_email("rakesh")
        others = [_person_email(p) for p in participants if p != "rakesh"]
        cover = {
            "sha256": sha,
            "subject": meeting.get("title") or doc.get("filename"),
            "date": str(meeting.get("date") or ""),
            "from": rakesh,
            "to": ", ".join(others) or "(call)",
            "cc": "",
            "body": (f"Transcript of a call between Rakesh and {', '.join(others) or 'the team'} "
                     f"on {str(meeting.get('date') or '')[:10]}. The call moves property by property; "
                     f"list EVERY registered property that is actually discussed (not merely named in passing). "
                     f"The full transcript is the attachment."),
            "hints": [],
        }
        attachment = {"sha256": sha, "filename": doc.get("filename"), "content_type": doc.get("content_type"), "text": text}
        result = runner.segregator.segregate(cover, [attachment])
        if result.error and not result.attachments:
            trace["segregation_error"] = result.error
            runner._queue_review(sha, "segregation_error", f"Opus 5 call failed: {result.error}")
            return None, None
        decision = result.attachments.get(sha) or ItemDecision(unresolved=True, reasoning="model returned no entry")
        if not decision.properties and result.email.properties:
            decision.properties = list(result.email.properties)
            decision.fallback_used = "inherited_from_cover"
        extra = _multi_property_extra(text, decision.properties)
        if extra:
            decision.properties = list(decision.properties) + extra
            decision.reasoning = (decision.reasoning or "") + f" [full-text scan added {', '.join(extra)}]"
        runner._write_decision(sha, decision, result, is_email=False)
        placement = "property" if decision.properties else "unplaced"
        art.update_one({"sha256": sha}, {"$set": {"placement": placement, "resolution.status": "segregated",
                                                  "updated_at": datetime.now(timezone.utc)}})
        trace["segregation"] = {"properties": decision.properties, "confidence": decision.confidence,
                                "unresolved": decision.unresolved, "out_of_scope": decision.out_of_scope,
                                "reasoning": decision.reasoning[:600], "tokens": [result.input_tokens, result.output_tokens]}
        return list(decision.properties), placement
        decision = result.attachments.get(sha) or ItemDecision(unresolved=True, reasoning="model returned no entry")
        if not decision.properties and result.email.properties:
            decision.properties = list(result.email.properties)
            decision.fallback_used = "inherited_from_cover"
        extra = _multi_property_extra(text, decision.properties)
        if extra:
            decision.properties = list(decision.properties) + extra
            decision.reasoning = (decision.reasoning or "") + f" [full-text scan added {', '.join(extra)}]"
        runner._write_decision(sha, decision, result, is_email=False)
        placement = "property" if decision.properties else "unplaced"
        art.update_one({"sha256": sha}, {"$set": {"placement": placement, "resolution.status": "segregated",
                                                  "updated_at": datetime.now(timezone.utc)}})
        trace["segregation"] = {"properties": decision.properties, "confidence": decision.confidence,
                                "unresolved": decision.unresolved, "out_of_scope": decision.out_of_scope,
                                "reasoning": decision.reasoning[:600], "tokens": [result.input_tokens, result.output_tokens]}
        props: List[str] = list(decision.properties)

        # 3. searchable: chunks, context, questions, embedding; summary vector.
        #    Chunk property_ids are NOT overwritten with the document's: the
        #    indexer attributes each chunk from its own segment, so the Lane Pl
        #    part of the call is reachable from Lane Pl and not from Bayshore.
        say("status", {"text": "Chunking, writing context, generating questions, embedding…"})
        chain._index(trace)
        self.mongo.chunks.update_many({"artifact_sha": sha}, {"$set": {"placement": placement,
                                                                        "scope": "property" if props else "common"}})
        #    A transcript has no headings, so the segmenter usually yields one
        #    long multi-property segment, and the alias matcher misses spoken
        #    names ("Cheetah Court", "nine-ten"). Where a chunk is already
        #    multi-property, add the properties Opus 5 read the call as
        #    covering — otherwise a Bayshore search could never reach the
        #    Bayshore minutes. Single-property chunks are left exactly as read.
        for c in self.mongo.chunks.find({"artifact_sha": sha}, {"_id": 1, "property_ids": 1}):
            have = list(c.get("property_ids") or [])
            if len(have) >= 3:
                missing = [p for p in props if p not in have]
                if missing:
                    self.mongo.chunks.update_one({"_id": c["_id"]}, {"$set": {"property_ids": sorted(have + missing)}})
        chain._doc_summaries_and_graph([sha], trace)
        self.mongo.db["doc_summaries"].update_many({"artifact_sha": sha}, {"$set": {"property_ids": props, "placement": placement}})

        # 4. dated events for each property it touches
        if props:
            say("status", {"text": f"Dated events for {len(props)} propert{'y' if len(props) == 1 else 'ies'}…"})
            try:
                chain._timeline(props, trace)
            except Exception as exc:  # timeline is best-effort; the document is already searchable
                logger.exception("transcript timeline failed")
                trace["timeline_error"] = f"{type(exc).__name__}: {exc}"[:300]
        chain._invalidate()
        trace["chunks"] = self.mongo.chunks.count_documents({"artifact_sha": sha})
        trace["finished_at"] = datetime.now(timezone.utc)
        chain.runs.insert_one({"started_at": trace["finished_at"], **trace})
        return trace
