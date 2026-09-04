"""Outlook ``.msg`` support — by conversion to RFC822, not a second pipeline.

``.msg`` is a compound OLE document, not RFC822, so it cannot be handed to
``parse_rfc822`` directly. The tempting fix is a parallel ingestion path that
reads ``.msg`` fields into an artifact. That would be a mistake: participant
filtering, direction, threading, body cleaning, attachment storage, dedup and
property resolution all live in ``EmailPipeline``, and a second path would have
to reimplement every one of them and then drift from it.

So the file is converted to ordinary RFC822 bytes and handed to the existing
pipeline. A saved ``.msg`` becomes indistinguishable from mail pulled via API,
which is exactly the property ``disk_ingest`` already relies on for ``.eml``.

Deduplication needs care here. The corpus is keyed by SHA-256 of the raw bytes,
but a ``.msg`` re-serialised to RFC822 will *not* reproduce the byte stream the
Graph API returned for the same message — the headers are rebuilt. Two copies of
one email would therefore land as two artifacts. The Internet Message-ID is the
identifier that survives both routes, so it is checked before ingesting and the
disk path is recorded on the existing artifact instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from mangotree.core.logging import logger


@dataclass
class MsgStats:
    seen: int = 0
    converted: int = 0
    ingested: int = 0
    duplicate_message_id: int = 0
    convert_failed: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "seen": self.seen,
            "converted": self.converted,
            "ingested": self.ingested,
            "duplicate_message_id": self.duplicate_message_id,
            "convert_failed": self.convert_failed,
            "errors": self.errors[:20],
        }


def msg_to_rfc822(path: Path) -> tuple[bytes, Optional[str]]:
    """Convert one ``.msg`` to RFC822 bytes. Returns (bytes, internet_message_id)."""
    import extract_msg

    with extract_msg.Message(str(path)) as msg:
        message_id = (getattr(msg, "messageId", None) or "").strip() or None
        return msg.asEmailMessage().as_bytes(), message_id


class MsgIngestor:
    """Feeds ``.msg`` files on disk through the ordinary email pipeline."""

    def __init__(self, mongo, pipeline, *, run_id: Optional[str] = None) -> None:
        self.mongo = mongo
        self.pipeline = pipeline
        self.run_id = run_id or datetime.now(timezone.utc).strftime("msg-%Y%m%d-%H%M%S")
        self.stats = MsgStats()
        self._known_ids: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------
    @property
    def known_message_ids(self) -> Dict[str, str]:
        """Internet Message-ID -> artifact sha, for everything already ingested."""
        if self._known_ids is None:
            self._known_ids = {
                doc["internet_message_id"]: doc["sha256"]
                for doc in self.mongo.artifacts.find(
                    {"internet_message_id": {"$nin": [None, ""]}},
                    {"internet_message_id": 1, "sha256": 1},
                )
            }
        return self._known_ids

    # ------------------------------------------------------------------
    def ingest_file(
        self, path: Path, *, folder: Optional[str] = None, relative: Optional[str] = None
    ) -> Optional[str]:
        self.stats.seen += 1

        try:
            raw, message_id = msg_to_rfc822(path)
        except Exception as exc:
            self.stats.convert_failed += 1
            self.stats.errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
            logger.error("  .msg convert failed for %s: %s", path.name, exc)
            return None
        self.stats.converted += 1

        # The same mail may already be here via Graph or Gmail. Re-serialised
        # bytes hash differently, so the Message-ID is the only reliable check.
        existing = self.known_message_ids.get(message_id) if message_id else None
        if existing:
            self.stats.duplicate_message_id += 1
            self.mongo.artifacts.update_one(
                {"sha256": existing},
                {"$addToSet": {"source_paths": str(path), "source_types": "disk_file"}},
            )
            logger.info("  already ingested via API, path recorded: %s", path.name)
            return existing

        sha = self.pipeline.process_raw_email(
            raw,
            mailbox="disk",
            provider="disk_msg",
            provider_id=relative or str(path),
            folder=folder,
            source_path=str(path),
        )
        if sha:
            self.stats.ingested += 1
            if message_id:
                self.known_message_ids[message_id] = sha
            # Carries both origins: it is mail, and it is also a file on disk.
            self.mongo.artifacts.update_one(
                {"sha256": sha},
                {"$addToSet": {"source_paths": str(path), "source_types": "disk_file"}},
            )
        return sha
