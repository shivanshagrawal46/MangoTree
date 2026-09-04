"""Disk corpus ingestion — ``E:\\LP Remodeling Projects\\Hold Properties``.

This source is fundamentally easier than email: **the folder already tells us
the property.** Top-level folder names map to the registry's ``disk_folder``,
giving a deterministic, confidence-1.0 property signal that email ingestion has
to work for. Filename aliases are still matched as corroboration, and a
disagreement between folder and filename is surfaced rather than silently
resolved (that is how "904 doc filed in the 910 folder" gets caught).

``.msg`` / ``.eml`` files found inside the corpus are routed through the *email*
pipeline instead, so a saved email is indistinguishable from one pulled via API.
``.msg`` is converted to RFC822 first — see ``ingest/msg_parser.py``.

Nothing is OCR'd here — Sprint 2 owns extraction. This stage stores originals
byte-for-byte, records provenance, and links to properties.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from mangotree.config.registry import PROPERTIES, PROPERTY_INDEX
from mangotree.core.hashing import sha256_file
from mangotree.core.logging import logger
from mangotree.ingest.msg_parser import MsgIngestor
from mangotree.ingest.pipeline import EmailPipeline
from mangotree.resolve.property_resolver import resolve_property
from mangotree.storage.mongo import Mongo

#: Extension -> coarse class, used to route Sprint 2 extraction.
KIND_BY_EXT: Dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "document", ".doc": "document", ".rtf": "document", ".txt": "text", ".md": "text",
    ".xlsx": "spreadsheet", ".xls": "spreadsheet", ".xlsm": "spreadsheet", ".csv": "spreadsheet",
    ".jpg": "image", ".jpeg": "image", ".png": "image", ".gif": "image",
    ".heic": "image", ".heif": "image", ".bmp": "image", ".tiff": "image", ".webp": "image",
    ".mp4": "video", ".mov": "video", ".avi": "video", ".m4v": "video",
    ".msg": "email_file", ".eml": "email_file",
    ".zip": "archive",
}

#: Filename/path pattern -> lender document class, most specific first.
#: Patterns are matched with word boundaries, so a bare ``DOT.docx`` is caught
#: without ``dot`` also matching inside unrelated words.
DOC_CLASS_RULES: Sequence = (
    # --- closing instruments (the loan itself) ---
    (r"assignment\s*(&|and)\s*allonge|allonge", "assignment_allonge"),
    (r"deed of trust|\bdot\b|balloon dot", "deed_of_trust"),
    (r"loan agreement", "loan_agreement"),
    (r"promissory note|\bnote\b", "promissory_note"),
    (r"\bguarant(y|ee|or)\b", "guaranty"),
    (r"subordination", "subordination_agreement"),
    (r"\bucc\b", "ucc_filing"),
    (r"closing instruction", "closing_instructions"),
    (r"\bconsent\b", "consent"),
    (r"hold harmless", "hold_harmless"),
    (r"operating agr(mnt|eement)", "operating_agreement"),
    # --- title ---
    (r"title policy|\balta\b|loan policy|lender policy", "title_policy"),
    (r"title report|title search|owner search", "title_report"),
    (r"le?in package|\blien\b", "lien_package"),
    # --- money movement ---
    (r"draw schedule|\bdraw\b", "draw_schedule"),
    (r"payoff", "payoff"),
    (r"wire instruction|buy direction|wiring instruction", "wire_instructions"),
    (r"modification", "modification"),
    (r"extension", "extension"),
    (r"change order|approved co\b", "change_order"),
    # --- vendor payment evidence (draw verification depends on these) ---
    (r"\bw-?9\b", "vendor_w9"),
    (r"receipt|invoice|\$\s*\d", "vendor_invoice"),
    # --- deal analysis ---
    (r"investor pack", "investor_package"),
    (r"underwriting|equity rescue", "underwriting"),
    (r"rkb[_\s]*step\d+|waterfall|recovery strateg|poa[_\s]*takeover", "rkb_analysis"),
    (r"\bcma\b|comparative market", "cma"),
    # --- construction ---
    (r"inspection", "inspection_report"),
    (r"construction status", "construction_status"),
    (r"scope of work", "scope_of_work"),
    (r"budget", "budget"),
    (r"estimate", "estimate"),
    (r"proposal", "proposal"),
    (r"daily log", "daily_log"),
    (r"\bcontract\b", "contract"),
    # --- legal ---
    (r"privileged", "legal_privileged"),
    (r"demand letter|demand for", "legal_demand"),
    (r"\blegal\b|counsel|attorney", "legal_other"),
    # --- misc ---
    (r"closing letter", "closing_letter"),
    (r"closing document|executed clo?sing", "closing_documents"),
    (r"accounting", "accounting"),
    (r"term sheet", "term_sheet"),
    # --- field media (checked last: only if nothing more specific matched) ---
    (r"whatsapp image|\bimg[_-]?\d+|\bphoto\b", "site_photo"),
    (r"whatsapp video", "site_video"),
)

_COMPILED_RULES = tuple(
    (re.compile(pattern, re.I), doc_class) for pattern, doc_class in DOC_CLASS_RULES
)

#: Folder/filename markers for attorney work product. These artifacts are
#: flagged restricted so they can be excluded from general answers.
PRIVILEGED_MARKERS = ("privileged", "attorney", "quinn legal", "evidence pack", "counsel")


#: Rules that are safe on a filename but not on document body text. A filename
#: containing "$2400" is strong evidence of an invoice; a *document* containing a
#: dollar amount is evidence of nothing, since nearly every financial instrument
#: in this corpus contains one. Applied to body text this rule alone classified
#: vacancy notices and disclosure statements as vendor invoices.
_FILENAME_ONLY_CLASSES = frozenset({"vendor_invoice"})


def classify_document(
    filename: str, relative_path: str = "", *, from_body_text: bool = False
) -> str:
    """Rule-based class from the filename and its folder path.

    These documents are named with real discipline, so rules resolve most of the
    corpus deterministically and for free. Whatever falls through to a bare
    extension class is what the Sprint-2 model is for.

    Set ``from_body_text`` when ``relative_path`` carries a document's opening
    text rather than a path, which suppresses the filename-only heuristics.
    """
    blob = f"{relative_path} {filename}"
    for pattern, doc_class in _COMPILED_RULES:
        if from_body_text and doc_class in _FILENAME_ONLY_CLASSES:
            continue
        if pattern.search(blob):
            return doc_class
    ext = Path(filename).suffix.lower()
    return KIND_BY_EXT.get(ext, "unknown")


def is_privileged(filename: str, relative_path: str = "") -> bool:
    blob = f"{relative_path} {filename}".lower()
    return any(marker in blob for marker in PRIVILEGED_MARKERS)


@dataclass
class DiskStats:
    seen: int = 0
    stored: int = 0
    duplicates: int = 0
    emails_routed: int = 0
    unresolved: int = 0
    mismatches: int = 0
    privileged: int = 0
    errors: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)
    by_property: Dict[str, int] = field(default_factory=dict)

    def bump(self, bucket: Dict[str, int], key: str) -> None:
        bucket[key] = bucket.get(key, 0) + 1

    def as_dict(self) -> dict:
        return {
            "seen": self.seen,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "emails_routed": self.emails_routed,
            "unresolved": self.unresolved,
            "folder_filename_mismatches": self.mismatches,
            "privileged": self.privileged,
            "errors": self.errors,
            "by_kind": dict(sorted(self.by_kind.items(), key=lambda kv: -kv[1])),
            "by_property": dict(sorted(self.by_property.items(), key=lambda kv: -kv[1])),
        }


class DiskIngestor:
    #: Files larger than this are recorded with provenance but their bytes are
    #: left on disk — GridFS is not the right home for multi-hundred-MB video.
    LARGE_FILE_BYTES = 40 * 1024 * 1024

    def __init__(self, mongo: Mongo, root: Path, *, run_id: Optional[str] = None) -> None:
        self.mongo = mongo
        self.root = Path(root)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("disk-%Y%m%d-%H%M%S")
        self.stats = DiskStats()
        self.email_pipeline = EmailPipeline(mongo, run_id=self.run_id)
        self.msg_ingestor = MsgIngestor(mongo, self.email_pipeline, run_id=self.run_id)

    # ------------------------------------------------------------------
    def _property_for_folder(self, folder_name: str) -> Optional[str]:
        for prop in PROPERTIES:
            if prop.disk_folder and prop.disk_folder == folder_name:
                return prop.property_id
        return None

    # ------------------------------------------------------------------
    def run(self, *, limit: Optional[int] = None) -> DiskStats:
        if not self.root.exists():
            raise RuntimeError(f"Disk corpus not reachable: {self.root}")

        self.mongo.runs.insert_one({
            "run_id": self.run_id,
            "kind": "disk_backfill",
            "root": str(self.root),
            "started_at": datetime.now(timezone.utc),
            "status": "running",
        })
        logger.info("Disk backfill starting: %s", self.root)

        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            property_id = self._property_for_folder(folder.name)
            if property_id is None:
                logger.warning(
                    "Folder not in registry, files will need review: %s", folder.name
                )
            count = 0
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                if limit and self.stats.seen >= limit:
                    break
                try:
                    self._ingest_file(path, folder.name, property_id)
                except Exception as exc:
                    self.stats.errors += 1
                    logger.error("Disk ingest failed for %s: %s", path.name, exc)
                    self.mongo.errors.insert_one({
                        "run_id": self.run_id, "stage": "disk_ingest",
                        "key": str(path), "error_type": type(exc).__name__,
                        "error": str(exc)[:2000],
                        "created_at": datetime.now(timezone.utc),
                    })
                count += 1
            logger.info("  %-46s %4d files", folder.name, count)

        self.mongo.runs.update_one(
            {"run_id": self.run_id},
            {"$set": {
                "status": "complete",
                "finished_at": datetime.now(timezone.utc),
                **self.stats.as_dict(),
            }},
        )
        return self.stats

    # ------------------------------------------------------------------
    def _ingest_file(self, path: Path, folder_name: str, folder_property: Optional[str]) -> None:
        self.stats.seen += 1
        ext = path.suffix.lower()
        kind = KIND_BY_EXT.get(ext, "unknown")
        self.stats.bump(self.stats.by_kind, kind)

        relative = str(path.relative_to(self.root))

        # Saved emails go through the email pipeline so they are stored, cleaned,
        # threaded and deduped exactly like API-fetched mail.
        if ext == ".eml":
            self.stats.emails_routed += 1
            self.email_pipeline.process_raw_email(
                path.read_bytes(),
                mailbox="disk",
                provider="disk",
                provider_id=relative,
                folder=folder_name,
                source_path=str(path),
            )
            return
        if ext == ".msg":
            # Converted to RFC822 and run through the same pipeline as .eml, so a
            # saved Outlook message is indistinguishable from one pulled by API.
            self.stats.emails_routed += 1
            self.msg_ingestor.ingest_file(path, folder=folder_name, relative=relative)
            # The pending-parser note is now answered; clear it so the review
            # queue does not carry work that has been done.
            self.mongo.review_queue.delete_one(
                {"artifact_sha": f"disk::{relative}", "kind": "msg_parser_pending"}
            )
            return

        size = path.stat().st_size
        sha = sha256_file(path)

        existing = self.mongo.artifacts.find_one({"sha256": sha}, {"_id": 1, "source_paths": 1})
        if existing:
            self.stats.duplicates += 1

        # Property: folder is authoritative; filename corroborates or contradicts.
        resolution = resolve_property(
            subject=path.stem,
            filenames=[path.name, relative],
            disk_folder=folder_name,
        )
        property_ids = list(resolution.property_ids)
        if folder_property and folder_property not in property_ids:
            property_ids.append(folder_property)

        filename_hits = {
            hit.property_id for hit in resolution.hits
            if any(s.startswith("alias_filename") or s.startswith("alias_subject") for s in hit.signals)
        }
        mismatch = bool(
            folder_property and filename_hits and folder_property not in filename_hits
        )
        if mismatch:
            self.stats.mismatches += 1
            self._queue_review(
                relative, "folder_filename_property_mismatch", folder_property,
                detail=f"folder={folder_property} filename suggests={sorted(filename_hits)}",
            )

        if not property_ids:
            self.stats.unresolved += 1
            self._queue_review(relative, "property_resolution", None)

        privileged = is_privileged(path.name, relative)
        if privileged:
            self.stats.privileged += 1

        doc_class = classify_document(path.name, relative)

        # Copy into the object store without loading the file into memory —
        # some of this corpus is multi-hundred-megabyte video.
        from mangotree.storage.objectstore import get_object_store

        stored_path = get_object_store().put_file(
            sha, path,
            metadata={
                "source_type": "disk_file", "relative_path": relative,
                "run_id": self.run_id, "kind": kind,
            },
        )

        stat = path.stat()
        now = datetime.now(timezone.utc)
        self.mongo.artifacts.update_one(
            {"sha256": sha},
            {
                "$set": {
                    "sha256": sha,
                    "source_type": "disk_file",
                    "filename": path.name,
                    "relative_path": relative,
                    "folder": folder_name,
                    "kind": kind,
                    "doc_class": doc_class,
                    "extension": ext,
                    "raw_size": size,
                    "object_path": stored_path,
                    "date": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    "privileged": privileged,
                    "access": "restricted" if privileged else "normal",
                    "property_ids": property_ids,
                    "property_candidates": [
                        {"property_id": h.property_id, "confidence": round(h.confidence, 3),
                         "signals": h.signals}
                        for h in resolution.hits
                    ],
                    "resolution": {
                        "status": resolution.status.value,
                        "notes": resolution.notes,
                        "folder_signal": folder_property,
                        "filename_mismatch": mismatch,
                    },
                    "extraction": {"status": "pending"},   # Sprint 2 picks this up
                    "updated_at": now,
                },
                # Additive: a file that also arrived as an email attachment keeps
                # that tag, so segregation still sees it as attached evidence.
                "$addToSet": {
                    "source_paths": str(path),
                    "source_types": "disk_file",
                },
                "$setOnInsert": {"created_at": now, "first_run_id": self.run_id},
            },
            upsert=True,
        )
        if not existing:
            self.stats.stored += 1
        for pid in property_ids:
            self.stats.bump(self.stats.by_property, pid)

    # ------------------------------------------------------------------
    def _queue_review(
        self, key: str, kind: str, property_id: Optional[str], detail: str = ""
    ) -> None:
        self.mongo.review_queue.update_one(
            {"artifact_sha": f"disk::{key}", "kind": kind},
            {
                "$set": {
                    "artifact_sha": f"disk::{key}",
                    "kind": kind,
                    "status": "open",
                    "subject": key,
                    "property_id": property_id,
                    "detail": detail,
                    "run_id": self.run_id,
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
