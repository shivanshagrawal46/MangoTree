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

    # ---------------------------------------------------------------- process
    def process(self, sha: str, *, emit=None) -> Dict[str, Any]:
        """Extract, segregate with Opus 5, index, summarise, timeline. Idempotent:
        every stage selects its own pending work."""
        from mangotree.pipeline.arrival import ArrivalChain
        from mangotree.resolve.segregation_runner import SegregationRunner, _multi_property_extra
        from mangotree.resolve.segregator import ItemDecision

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

        # 2. Opus 5 decides the properties — the transcript rendered as the one
        #    document on a short cover message, same as an email attachment.
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
            return trace
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
