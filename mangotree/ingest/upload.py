"""Documents a user adds from a property page.

An upload is the third way a file enters the corpus, after the E-drive and email
attachments, and it is modelled on the E-drive path: the place it was added
*names the property*, so — like a file in the ``Chita Ct`` folder — it needs no
Opus 5 segregation. The person choosing the page made that decision, and it is
recorded as such (``resolution.status = "user_upload"``, with who and when).

Everything else is the same pipeline the rest of the corpus went through, run by
``ArrivalChain.process_documents``: OCR / text extraction, chunking, Tier-1 and
Tier-2 context, Opus 5 questions, one voyage-4-large embedding, the document
summary vector, timeline events, tasks and cards, graph link at the nightly
rebuild.

Deduplication is by SHA-256 of the bytes, same key as everywhere else:

* new bytes           -> a new ``upload`` artifact under this property
* bytes already here,
  same property       -> nothing is stored twice; the user is told when and as
                         what it first arrived
* bytes already here,
  other property /
  unplaced            -> the existing artifact gains this property (a document
                         *can* belong to two deals) and, if it was unplaced or in
                         the common store, is now placed. Its chunks follow.

The original ``source_type`` of an existing artifact is never overwritten — that
is what ``source_types`` (plural) is for.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core.hashing import sha256_bytes
from mangotree.core.logging import logger
from mangotree.ingest.disk_ingest import KIND_BY_EXT, classify_document, is_privileged
from mangotree.storage.mongo import Mongo

#: Anything larger is refused at the API. Multi-hundred-megabyte video exists in
#: the E-drive corpus, but a browser upload of one is a mistake, not a document.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@dataclass
class UploadResult:
    sha256: str
    filename: str
    property_id: str
    #: new | duplicate | duplicate_added_property
    status: str
    message: str
    first_seen: Optional[datetime] = None
    first_seen_as: Optional[str] = None
    first_source: Optional[str] = None
    needs_processing: bool = False
    warnings: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class UploadIngestor:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo

    def ingest(
        self,
        data: bytes,
        *,
        filename: str,
        property_id: str,
        uploaded_by: str,
        note: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> UploadResult:
        if property_id not in PROPERTY_INDEX:
            raise ValueError(f"unknown property {property_id!r}")
        filename = Path(filename or "upload").name  # never trust a client path
        if not data:
            raise ValueError("empty file")
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError(f"{filename} is {len(data) / 1e6:.0f} MB; the limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

        sha = sha256_bytes(data)
        now = datetime.now(timezone.utc)
        ext = Path(filename).suffix.lower()
        content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        upload_record = {"by": uploaded_by, "at": now, "property_id": property_id, "filename": filename, "note": note or None}

        art = self.mongo.artifacts
        existing = art.find_one({"sha256": sha}, {"property_ids": 1, "filename": 1, "source_type": 1, "created_at": 1,
                                                  "placement": 1, "date": 1})

        # Bytes are stored regardless: idempotent, and it heals a record whose
        # original went missing from the object store.
        self.mongo.put_original(sha, data, filename, {"source_type": "upload", "property_id": property_id,
                                                      "uploaded_by": uploaded_by})

        if existing is not None:
            already_here = property_id in (existing.get("property_ids") or [])
            art.update_one(
                {"sha256": sha},
                {"$addToSet": {"source_types": "upload", "uploads": upload_record,
                               "property_ids": property_id}},
            )
            label = PROPERTY_INDEX[property_id].canonical_address
            if already_here:
                return UploadResult(
                    sha, filename, property_id, "duplicate",
                    f"Already in the system for {label} as “{existing.get('filename')}” "
                    f"(arrived {existing.get('created_at', now):%d %b %Y} via {existing.get('source_type')}). Nothing stored twice.",
                    first_seen=existing.get("created_at"), first_seen_as=existing.get("filename"),
                    first_source=existing.get("source_type"),
                )
            # Known bytes, new property: it now belongs here too, and if it was
            # sitting unplaced or in the common store it is placed by this act.
            art.update_one({"sha256": sha}, {"$set": {"placement": "property", "scope": "property",
                                                      "resolution.user_upload": upload_record, "updated_at": now}})
            doc = art.find_one({"sha256": sha}, {"property_ids": 1})
            self.mongo.chunks.update_many({"artifact_sha": sha}, {"$set": {
                "property_ids": doc.get("property_ids") or [property_id], "placement": "property", "scope": "property"}})
            self.mongo.db["doc_summaries"].update_many({"artifact_sha": sha}, {"$set": {
                "property_ids": doc.get("property_ids") or [property_id], "placement": "property"}})
            self.mongo.review_queue.update_many({"artifact_sha": sha, "status": "open"},
                                                {"$set": {"status": "resolved", "resolved_by": uploaded_by,
                                                          "resolution": f"uploaded to {property_id}", "resolved_at": now}})
            return UploadResult(
                sha, filename, property_id, "duplicate_added_property",
                f"This file was already in the system as “{existing.get('filename')}” via {existing.get('source_type')}; "
                f"it is now also filed under {label}. Timeline and tasks will refresh.",
                first_seen=existing.get("created_at"), first_seen_as=existing.get("filename"),
                first_source=existing.get("source_type"), needs_processing=True,
            )

        privileged = is_privileged(filename, f"{property_id}/upload")
        artifact = {
            "sha256": sha,
            "source_type": "upload",
            "filename": filename,
            "extension": ext,
            "content_type": content_type,
            "kind": KIND_BY_EXT.get(ext, "unknown"),
            "doc_class": classify_document(filename, f"{property_id}/upload"),
            "raw_size": len(data),
            # The upload moment is the only date the bytes carry; the extractor and
            # the timeline pass read the real dates out of the text.
            "date": now,
            "privileged": privileged,
            "access": "restricted" if privileged else "normal",
            "property_ids": [property_id],
            "placement": "property",
            "scope": "property",
            "resolution": {
                "status": "user_upload",
                "notes": [f"filed under {property_id} by {uploaded_by} at upload"],
                "user_upload": upload_record,
            },
            "extraction": {"status": "pending"},
            "updated_at": now,
        }
        art.update_one(
            {"sha256": sha},
            {"$set": artifact,
             "$addToSet": {"source_types": "upload", "uploads": upload_record},
             "$setOnInsert": {"created_at": now, "first_run_id": f"upload-{now:%Y%m%d}"}},
            upsert=True,
        )
        logger.info("upload stored: %s -> %s by %s (%d bytes)", filename, property_id, uploaded_by, len(data))
        warnings = []
        if KIND_BY_EXT.get(ext, "unknown") == "unknown":
            warnings.append(f"“{ext or 'no extension'}” is not a type we extract text from; the file is stored and downloadable but will not be searchable.")
        return UploadResult(sha, filename, property_id, "new",
                            f"Stored under {PROPERTY_INDEX[property_id].canonical_address}. Reading it now.",
                            needs_processing=True, warnings=warnings)
