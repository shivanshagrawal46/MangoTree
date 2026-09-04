"""Extraction orchestrator — turns stored originals into text, with provenance.

Routing (cheapest correct path first):

    .pdf          native text layer per page; vision OCR only for pages that need it
    .docx/.doc    native
    .xlsx/.xls    cell-level extraction (never OCR — see extract/spreadsheet.py)
    images        vision OCR
    .txt/.md      read
    video         deferred (transcription is a separate job)

Two properties this runner guarantees:

* **Idempotent.** Extraction state lives on the artifact and completed work is
  skipped, so a re-run after an interruption costs nothing and changes nothing.
* **Pre-flight cost estimate, mandatory.** The cheap model tier was removed from
  this stack by directive, so an un-estimated OCR run is a real financial risk.
  Nothing bills until the estimate has been shown and accepted.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from mangotree.config.models import OCR as OCR_CFG
from mangotree.core.logging import logger
from mangotree.extract.documents import extract_native
from mangotree.extract.legacy_doc import extract_legacy_doc, is_legacy_doc
from mangotree.extract.spreadsheet import extract_workbook, money_cells
from mangotree.storage.mongo import Mongo

#: Rough per-page vision cost (USD) for the pinned OCR model. Used only for the
#: pre-flight estimate, never for billing.
COST_PER_VISION_PAGE = 0.015

SPREADSHEET_EXT = {".xlsx", ".xls", ".xlsm", ".csv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".heic", ".heif"}
NATIVE_EXT = {".pdf", ".docx", ".doc", ".txt", ".md"}
DEFERRED_EXT = {".mp4", ".mov", ".avi", ".m4v", ".zip"}

#: Both classes of original are extractable. Attachments were excluded by a
#: hard-coded filter until 2026-08-31; they are frequently the *evidence* an
#: email refers to, so they belong in the default pass, not an opt-in one.
from mangotree.core.sources import DOCUMENT_SOURCE_TYPES

DEFAULT_SOURCE_TYPES: Sequence[str] = DOCUMENT_SOURCE_TYPES

#: Fallback when an attachment has no usable filename extension.
_EXT_BY_MIME: Dict[str, str] = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/heic": ".heic",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "application/csv": ".csv",
    "text/plain": ".txt",
}

#: Content signatures, checked when name and MIME type are both uninformative.
#: Ordered longest-first so a more specific signature is never shadowed.
_MAGIC_SIGNATURES: Sequence[tuple[bytes, str]] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", ".doc"),   # OLE2: legacy Office
    (b"%PDF-", ".pdf"),
    (b"GIF89a", ".gif"),
    (b"GIF87a", ".gif"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"II*\x00", ".tiff"),
    (b"MM\x00*", ".tiff"),
)


@dataclass
class ExtractionEstimate:
    documents: int = 0
    native_pages: int = 0
    vision_pages: int = 0
    spreadsheets: int = 0
    images: int = 0
    deferred: int = 0
    unreadable: List[str] = field(default_factory=list)

    @property
    def estimated_cost(self) -> float:
        return (self.vision_pages + self.images) * COST_PER_VISION_PAGE

    def render(self) -> str:
        return (
            "\n=== Pre-flight extraction estimate ===\n"
            f"  documents to process   {self.documents}\n"
            f"  pages via text layer   {self.native_pages}  (free)\n"
            f"  pages via vision OCR   {self.vision_pages}\n"
            f"  images via vision OCR  {self.images}\n"
            f"  spreadsheets (native)  {self.spreadsheets}  (free)\n"
            f"  deferred (video/zip)   {self.deferred}\n"
            f"  unreadable             {len(self.unreadable)}\n"
            f"\n  estimated vision cost  ${self.estimated_cost:,.2f} "
            f"({self.vision_pages + self.images} pages @ ${COST_PER_VISION_PAGE}/page)\n"
        )


@dataclass
class ExtractionStats:
    processed: int = 0
    skipped_done: int = 0
    native_pages: int = 0
    vision_pages: int = 0
    money_cells: int = 0
    deferred: int = 0
    errors: int = 0
    by_kind: Dict[str, int] = field(default_factory=dict)

    def bump(self, key: str) -> None:
        self.by_kind[key] = self.by_kind.get(key, 0) + 1

    def as_dict(self) -> dict:
        return {
            "processed": self.processed,
            "skipped_already_done": self.skipped_done,
            "native_pages": self.native_pages,
            "vision_pages": self.vision_pages,
            "money_cells": self.money_cells,
            "deferred": self.deferred,
            "errors": self.errors,
            "by_kind": dict(sorted(self.by_kind.items(), key=lambda kv: -kv[1])),
        }


#: A run that is force-killed never reaches cleanup(), and each orphaned render
#: cache holds hundreds of megabytes — eleven of them had quietly taken 3 GB of
#: the system drive. Windows cannot trap a hard kill, so the next run sweeps
#: instead. The age bound is generous because a legitimate extraction of this
#: corpus runs for five hours and must never delete a live sibling's directory.
_STALE_TEMP_AGE_HOURS = 24


def _sweep_stale_temp_dirs(*, keep: Path) -> None:
    """Remove render caches abandoned by earlier runs. Never raises."""
    try:
        parent = Path(tempfile.gettempdir())
        cutoff = time.time() - _STALE_TEMP_AGE_HOURS * 3600
        for path in parent.glob("mangotree-extract-*"):
            if path == keep or not path.is_dir():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue
    except Exception:  # pragma: no cover - housekeeping must never break a run
        pass


def _disagreements(native, ocr_pages: Dict[int, str], *, threshold: float = 0.5) -> List[dict]:
    """Pages where the PDF's own text and the OCR read diverge badly.

    Compared on word overlap rather than character equality: OCR legitimately
    differs in whitespace, hyphenation and reading order, and none of that is a
    fault. A large drop in shared vocabulary is a different matter — it means one
    of the two is reading something the other cannot see.

    Only pages that carry text on both sides are compared. A page with an empty
    text layer is a scan, which is the ordinary case, not a disagreement.
    """
    out: List[dict] = []
    for page in native.pages:
        native_text = (page.text or "").strip()
        ocr_text = (ocr_pages.get(page.page) or "").strip()
        if not native_text or not ocr_text:
            continue
        native_words = set(native_text.lower().split())
        ocr_words = set(ocr_text.lower().split())
        if not native_words:
            continue
        overlap = len(native_words & ocr_words) / len(native_words)
        if overlap < threshold:
            out.append({
                "page": page.page,
                "word_overlap": round(overlap, 3),
                "native_chars": len(native_text),
                "ocr_chars": len(ocr_text),
            })
    return out


class ExtractionRunner:
    def __init__(
        self,
        mongo: Mongo,
        *,
        api_key: str,
        run_id: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        ocr_all_pdf_pages: bool = True,
    ):
        self.mongo = mongo
        self.api_key = api_key
        self.openai_api_key = openai_api_key or None
        #: Admin directive (2026-09-02): every PDF page goes through vision OCR,
        #: including pages that already carry an embedded text layer. Word and
        #: Excel still use the native extractor.
        #:
        #: The text layer is still read and kept alongside the OCR result rather
        #: than thrown away. For a born-digital PDF it is the exact character
        #: stream, so it is the one source that can prove whether a given OCR
        #: page was read correctly — worth keeping precisely because it is free.
        self.ocr_all_pdf_pages = ocr_all_pdf_pages
        self.run_id = run_id or datetime.now(timezone.utc).strftime("extract-%Y%m%d-%H%M%S")
        self.stats = ExtractionStats()
        self._ocr = None
        # Attachments have no path on disk, so their bytes are materialised here
        # on demand. Created lazily and removed in cleanup() — a run over 109
        # attachments can otherwise leave hundreds of megabytes behind.
        self._temp_root = Path(tempfile.mkdtemp(prefix="mangotree-extract-"))
        self._materialised: Set[Path] = set()
        _sweep_stale_temp_dirs(keep=self._temp_root)

    @property
    def _temp_dir(self) -> Path:
        self._temp_root.mkdir(parents=True, exist_ok=True)
        return self._temp_root

    def cleanup(self) -> None:
        """Remove materialised originals. Safe to call twice."""
        shutil.rmtree(self._temp_root, ignore_errors=True)
        self._materialised.clear()

    def __enter__(self) -> "ExtractionRunner":
        return self

    def __exit__(self, *exc_info) -> None:
        self.cleanup()

    @property
    def ocr(self):
        if self._ocr is None:
            from mangotree.extract.ocr import VisionOCR

            self._ocr = VisionOCR(
                self.api_key, openai_api_key=self.openai_api_key
            )
        return self._ocr

    # ------------------------------------------------------------------
    def _pending(
        self,
        only_kind: Optional[str] = None,
        *,
        include_vision_backlog: bool = True,
        source_types: Sequence[str] = DEFAULT_SOURCE_TYPES,
    ) -> List[dict]:
        """Artifacts still owed extraction work.

        A ``--skip-vision`` pass leaves real work outstanding: PDFs saved as
        ``partial`` with pages awaiting vision, and images deferred by the flag.
        Those must be picked back up when vision is enabled, or the free pass
        would permanently mask the pages it could not read.

        ``source_types`` defaults to disk files **and email attachments**. It was
        previously hard-coded to ``disk_file``, which silently excluded every
        attachment from extraction — 109 of them, including ALTA settlement
        statements, title commitments and deeds of trust. An email announcing
        "see attached ALTA" is not evidence; the ALTA is, and none of them had
        ever been read.
        """
        states: List[dict] = [
            {"extraction.status": {"$in": ["pending", "failed"]}},
            {"extraction.status": {"$exists": False}},
            {"extraction": None},
        ]
        if include_vision_backlog:
            states.append({"extraction.status": "partial"})
            states.append({"extraction.reason": "vision skipped by flag"})

        query: dict = {"source_type": {"$in": list(source_types)}, "$or": states}
        if only_kind:
            query["kind"] = only_kind
        return list(
            self.mongo.artifacts.find(
                query,
                {"sha256": 1, "filename": 1, "extension": 1, "kind": 1,
                 "source_paths": 1, "property_ids": 1, "raw_size": 1,
                 "source_type": 1, "content_type": 1, "object_path": 1},
            )
        )

    @staticmethod
    def _extension_of(doc: dict) -> str:
        """Extension, derived from the filename or MIME type when absent.

        Disk artifacts carry an explicit ``extension``; attachments do not — they
        have ``filename`` and ``content_type``. Routing keys on extension, so an
        attachment with no ``extension`` field would fall through to "no
        extractor" no matter what it actually was.

        Inline images are the case that forced this: mail clients name them
        ``img-<uuid>`` with no extension and declare them
        ``application/octet-stream``, so neither the name nor the MIME type
        identifies them. Content sniffing in ``_sniff_extension`` is the backstop.
        """
        ext = (doc.get("extension") or "").lower()
        if ext:
            return ext
        ext = Path(doc.get("filename") or "").suffix.lower()
        if ext:
            return ext
        return _EXT_BY_MIME.get((doc.get("content_type") or "").lower().split(";")[0], "")

    @staticmethod
    def _sniff_extension(path: Path) -> str:
        """Identify a file by its leading bytes.

        Used only when name and MIME type both fail. Every signature here is a
        format the pipeline can actually route, so a match always changes the
        outcome from "no extractor" to real extraction.
        """
        try:
            with path.open("rb") as handle:
                head = handle.read(16)
        except Exception:
            return ""
        for magic, ext in _MAGIC_SIGNATURES:
            if head.startswith(magic):
                return ext
        return ""

    def _local_path(self, doc: dict) -> Optional[Path]:
        """A readable path for the original, materialising it if necessary.

        Disk files still live on the E: drive. Attachments never had a
        filesystem path — their bytes are in the content-addressed object store —
        so they are written to a per-run temp directory on demand. Extraction
        needs a real file because PyMuPDF, python-docx and openpyxl all open
        paths rather than buffers.
        """
        for candidate in doc.get("source_paths", []) or []:
            path = Path(candidate)
            if path.exists():
                return path

        object_path = doc.get("object_path")
        if object_path:
            path = Path(object_path)
            if path.exists():
                return path

        sha = doc.get("sha256")
        if not sha:
            return None
        try:
            from mangotree.storage.objectstore import get_object_store

            data = get_object_store().get(sha)
        except Exception:
            return None

        # Preserve the extension: every downstream extractor sniffs it.
        suffix = self._extension_of(doc) or ".bin"
        target = self._temp_dir / f"{sha[:24]}{suffix}"
        if not target.exists():
            target.write_bytes(data)
        self._materialised.add(target)
        return target

    # ------------------------------------------------------------------
    def estimate(
        self,
        only_kind: Optional[str] = None,
        *,
        include_vision_backlog: bool = True,
        source_types: Sequence[str] = DEFAULT_SOURCE_TYPES,
    ) -> ExtractionEstimate:
        est = ExtractionEstimate()
        for doc in self._pending(
            only_kind,
            include_vision_backlog=include_vision_backlog,
            source_types=source_types,
        ):
            ext = self._extension_of(doc)
            path = self._local_path(doc)
            if path is None:
                est.unreadable.append(doc.get("filename", "?"))
                continue

            est.documents += 1
            if ext in DEFERRED_EXT:
                est.deferred += 1
            elif ext in SPREADSHEET_EXT:
                est.spreadsheets += 1
            elif ext in IMAGE_EXT:
                est.images += 1
            elif ext == ".pdf":
                native = extract_native(path)
                if self.ocr_all_pdf_pages:
                    est.vision_pages += len(native.pages)
                else:
                    needs = len(native.pages_needing_vision)
                    est.vision_pages += needs
                    est.native_pages += max(0, len(native.pages) - needs)
            elif ext in NATIVE_EXT:
                est.native_pages += 1
        return est

    # ------------------------------------------------------------------
    def run(
        self,
        *,
        only_kind: Optional[str] = None,
        limit: Optional[int] = None,
        skip_vision: bool = False,
        max_vision_pages: Optional[int] = None,
        source_types: Sequence[str] = DEFAULT_SOURCE_TYPES,
        only_shas: Optional[Sequence[str]] = None,
    ) -> ExtractionStats:
        # A vision-skipping pass must not re-open the backlog it is about to
        # defer again; it would churn the same artifacts on every run.
        pending = self._pending(
            only_kind,
            include_vision_backlog=not skip_vision,
            source_types=source_types,
        )
        if only_shas is not None:
            # A user waiting on one upload should not also wait for the whole
            # vision backlog; the scheduled passes own that.
            wanted = set(only_shas)
            pending = [d for d in pending if d["sha256"] in wanted]
        if limit:
            pending = pending[:limit]

        logger.info("Extraction starting: %d artifacts (run %s)", len(pending), self.run_id)
        self.mongo.runs.insert_one({
            "run_id": self.run_id, "kind": "extraction",
            "started_at": datetime.now(timezone.utc), "status": "running",
            "target_count": len(pending),
        })

        vision_budget = max_vision_pages

        for index, doc in enumerate(pending, start=1):
            sha = doc["sha256"]
            ext = self._extension_of(doc)
            path = self._local_path(doc)

            if path is None:
                self._fail(sha, "original file not reachable on disk")
                continue

            # Last resort before giving up on routing: read the leading bytes.
            if not ext:
                ext = self._sniff_extension(path)
                if ext:
                    logger.info(
                        "Sniffed %s as %s (no filename extension, MIME was %r)",
                        doc.get("filename") or sha[:12], ext, doc.get("content_type"),
                    )

            try:
                if ext in DEFERRED_EXT:
                    self._defer(sha, f"{ext} handled by a separate job")
                elif ext in SPREADSHEET_EXT:
                    self._do_spreadsheet(sha, path)
                elif ext in IMAGE_EXT:
                    if skip_vision:
                        self._defer(sha, "vision skipped by flag")
                    else:
                        used = self._do_image(sha, path)
                        if vision_budget is not None:
                            vision_budget -= used
                elif ext == ".pdf":
                    used = self._do_pdf(
                        sha, path,
                        allow_vision=not skip_vision and (vision_budget is None or vision_budget > 0),
                        vision_budget=vision_budget,
                    )
                    if vision_budget is not None:
                        vision_budget -= used
                elif ext in NATIVE_EXT:
                    self._do_native(sha, path)
                else:
                    self._defer(sha, f"no extractor for {ext}")

                self.stats.processed += 1
                self.stats.bump(doc.get("kind", "unknown"))
            except Exception as exc:
                self.stats.errors += 1
                logger.error("Extraction failed for %s: %s", path.name, exc)
                self._fail(sha, f"{type(exc).__name__}: {exc}")

            if index % 20 == 0:
                logger.info(
                    "  %d/%d  native_pages=%d vision_pages=%d errors=%d",
                    index, len(pending), self.stats.native_pages,
                    self.stats.vision_pages, self.stats.errors,
                )
            if vision_budget is not None and vision_budget <= 0 and not skip_vision:
                logger.info("Vision page budget exhausted; stopping vision work")
                skip_vision = True

        self.mongo.runs.update_one(
            {"run_id": self.run_id},
            {"$set": {"status": "complete", "finished_at": datetime.now(timezone.utc),
                      **self.stats.as_dict()}},
        )
        return self.stats

    # ------------------------------------------------------------------
    def _do_spreadsheet(self, sha: str, path: Path) -> None:
        extract = extract_workbook(path)
        cells = money_cells(extract)
        self.stats.money_cells += len(cells)
        self._save(
            sha,
            text=extract.text,
            method="spreadsheet_native",
            detail=extract.as_dict(),
            extra={"money_cells": cells[:2000]},
        )

    def _do_native(self, sha: str, path: Path) -> None:
        # A ``.doc`` may be either OOXML with a misleading extension or a genuine
        # Word 97-2003 binary. python-docx handles only the former and fails on
        # the latter with a confusing content-type complaint, so the format is
        # sniffed from the file itself rather than trusted from its name.
        if path.suffix.lower() == ".doc" and is_legacy_doc(path):
            result = extract_legacy_doc(path)
            self.stats.native_pages += 1
            if not result.text:
                self._fail(
                    sha,
                    f"legacy .doc unreadable ({result.method}): "
                    + "; ".join(result.warnings)[:300],
                )
                return
            self._save(
                sha, text=result.text, method=f"legacy_doc:{result.method}",
                detail={
                    "pieces": result.pieces,
                    "warnings": result.warnings,
                    "format": "ole2_word97",
                },
                confidence=result.confidence,
            )
            return

        native = extract_native(path)
        self.stats.native_pages += max(1, len(native.pages))
        if native.errors and not native.has_usable_text:
            self._fail(sha, "; ".join(native.errors)[:400])
            return
        self._save(sha, text=native.text, method="native", detail=native.as_dict())

    def _do_image(self, sha: str, path: Path) -> int:
        doc = self.ocr.read_document(path, sha)
        self.stats.vision_pages += len(doc.pages)
        self._save(
            sha, text=doc.text, method="vision_ocr", detail=doc.as_dict(),
            confidence=doc.mean_confidence,
        )
        return len(doc.pages)

    def _do_pdf(
        self, sha: str, path: Path, *, allow_vision: bool, vision_budget: Optional[int]
    ) -> int:
        native = extract_native(path)
        if self.ocr_all_pdf_pages:
            need_vision = [p.page for p in native.pages]
        else:
            need_vision = native.pages_needing_vision

        if not need_vision:
            self.stats.native_pages += len(native.pages)
            self._save(
                sha, text=native.text, method="native_text_layer",
                detail=native.as_dict(), confidence=1.0,
            )
            return 0

        if not allow_vision:
            # Keep whatever the text layer gave us and mark the rest outstanding,
            # so a partial result is never mistaken for a complete one.
            self._save(
                sha, text=native.text, method="native_partial",
                detail=native.as_dict(), status="partial",
                extra={"pages_awaiting_vision": need_vision},
            )
            return 0

        if vision_budget is not None:
            need_vision = need_vision[:max(0, vision_budget)]

        from mangotree.extract.ocr import render_pdf_pages

        images = render_pdf_pages(path)
        page_texts: Dict[int, str] = {
            p.page: p.text for p in native.pages if not p.needs_vision
        }
        page_meta: List[dict] = []

        batch = [(n, images[n - 1]) for n in need_vision if n - 1 < len(images)]
        for result in self.ocr.read_pages(batch):
            page_texts[result.page] = result.text
            page_meta.append(result.as_dict())
            self.stats.vision_pages += 1

        self.stats.native_pages += len(native.pages) - len(need_vision)

        combined = "\n\n".join(
            f"[page {n}]\n{page_texts[n].strip()}"
            for n in sorted(page_texts) if page_texts[n].strip()
        )
        confidences = [m["confidence"] for m in page_meta] or [1.0]

        method = "vision_all_pages" if self.ocr_all_pdf_pages else "hybrid_native_vision"
        detail = {**native.as_dict(), "vision_pages": page_meta}
        if self.ocr_all_pdf_pages:
            # A page whose embedded text and OCR text disagree wildly is the
            # signal worth surfacing: either the OCR misread it or the PDF's own
            # text layer is corrupt, and both are worth a human's attention.
            detail["text_layer_disagreements"] = _disagreements(native, page_texts)

        self._save(
            sha, text=combined, method=method,
            detail=detail,
            confidence=sum(confidences) / len(confidences),
        )
        return len(need_vision)

    # ------------------------------------------------------------------
    def reocr_failed_pages(
        self,
        *,
        include_blocked: bool = True,
        include_low_confidence: bool = True,
        include_forbidden_engine: bool = True,
        confidence_floor: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> dict:
        """Re-read only the pages that previously failed, in place.

        Targets two populations:

        * **blocked** — Anthropic's content policy refused them, so they hold
          offline-OCR text at best. These are the title reports and policies.
        * **low confidence** — Claude read them poorly and GPT-5 did no better.
        * **forbidden engine** — text produced by an engine no longer permitted.
          Any page whose model is not in ``ALLOWED_ENGINES`` must be re-read,
          because it is carrying text the admin has ruled inadmissible.

        Only the affected pages are re-read and the surrounding good pages are
        left untouched, so this costs a few dozen calls rather than re-running
        the whole corpus. A page is replaced **only if the new read is better**,
        with one deliberate exception: a page from a forbidden engine is replaced
        even by a *worse-scoring* permitted read, because inadmissible text has no
        standing to be preserved on confidence grounds.
        """
        from mangotree.extract.ocr import (
            ALLOWED_ENGINES, render_pdf_pages, load_image_bytes,
        )

        floor = confidence_floor if confidence_floor is not None else OCR_CFG.confidence_floor
        summary = {
            "documents": 0, "pages_targeted": 0, "pages_improved": 0,
            "pages_unchanged": 0, "pages_failed": 0, "still_needs_human": 0,
            "by_model": {},
        }

        query = {"extraction.detail.vision_pages": {"$exists": True}}
        candidates = list(self.mongo.artifacts.find(
            query,
            {"sha256": 1, "filename": 1, "extension": 1, "source_paths": 1,
             "text": 1, "extraction": 1},
        ))

        def is_forbidden(page: dict) -> bool:
            model = str(page.get("model") or "").split("@")[0]
            return bool(model) and model not in ALLOWED_ENGINES

        for doc in candidates:
            pages = doc["extraction"]["detail"].get("vision_pages") or []
            targets = [
                p for p in pages
                if (include_forbidden_engine and is_forbidden(p))
                or (include_blocked and p.get("blocked"))
                or (include_low_confidence and (p.get("confidence") or 0) < floor)
            ]
            if not targets:
                continue
            forbidden_pages = {p["page"] for p in targets if is_forbidden(p)}

            path = self._local_path(doc)
            if path is None:
                logger.warning("Original unreachable for %s; skipping", doc.get("filename"))
                continue

            if limit and summary["pages_targeted"] >= limit:
                break

            logger.info(
                "Re-reading %d page(s) of %s", len(targets), doc.get("filename")
            )
            summary["documents"] += 1

            try:
                images = (
                    render_pdf_pages(path) if path.suffix.lower() == ".pdf"
                    else [load_image_bytes(path)]
                )
            except Exception as exc:
                logger.error("Render failed for %s: %s", path.name, exc)
                summary["pages_failed"] += len(targets)
                continue

            batch = [
                (p["page"], images[p["page"] - 1])
                for p in targets if 0 < p["page"] <= len(images)
            ]
            summary["pages_targeted"] += len(batch)

            results = {r.page: r for r in self.ocr.read_pages(batch)}
            by_page = {p["page"]: p for p in pages}

            for number, result in results.items():
                previous = by_page.get(number, {})
                old_text = (previous.get("text") or "").strip()
                old_conf = float(previous.get("confidence") or 0.0)

                better = bool(result.text.strip()) and (
                    result.confidence > old_conf
                    or (not old_text and result.text.strip())
                    # Inadmissible text is replaced regardless of score.
                    or number in forbidden_pages
                )
                if not better and number in forbidden_pages:
                    # Nothing permitted could read it. The forbidden text must
                    # still go — a visible gap is safe, silently-retained banned
                    # text is not.
                    by_page[number] = {
                        **previous,
                        "text": "",
                        "confidence": 0.0,
                        "model": result.model,
                        "needs_human": True,
                        "notes": (
                            "previous text came from a prohibited offline OCR engine and "
                            "was discarded; no permitted engine could read this page"
                        ),
                    }
                    summary["pages_failed"] += 1
                    summary["still_needs_human"] += 1
                    continue
                if not better:
                    summary["pages_unchanged"] += 1
                    if previous.get("needs_human"):
                        summary["still_needs_human"] += 1
                    continue

                by_page[number] = result.as_dict()
                summary["pages_improved"] += 1
                summary["by_model"][result.model] = (
                    summary["by_model"].get(result.model, 0) + 1
                )
                if result.needs_human:
                    summary["still_needs_human"] += 1

            self._rewrite_document(doc, by_page)

        return summary

    # ------------------------------------------------------------------
    def _rewrite_document(self, doc: dict, by_page: Dict[int, dict]) -> None:
        """Rebuild the artifact's text from its (now partly re-read) pages.

        Native-text pages are preserved: they were never in ``vision_pages``, so
        they are recovered from the stored text rather than re-extracted.
        """
        vision_pages = [by_page[n] for n in sorted(by_page)]
        native = extract_native(self._local_path(doc)) if doc.get("extension", "").lower() == ".pdf" else None

        page_texts: Dict[int, str] = {}
        if native is not None:
            for page in native.pages:
                if not page.needs_vision and page.text.strip():
                    page_texts[page.page] = page.text
        for number, page in by_page.items():
            if (page.get("text") or "").strip():
                page_texts[number] = page["text"]

        combined = "\n\n".join(
            f"[page {n}]\n{page_texts[n].strip()}"
            for n in sorted(page_texts) if page_texts[n].strip()
        )
        confidences = [p.get("confidence") or 0.0 for p in vision_pages] or [1.0]

        detail = dict(doc["extraction"].get("detail") or {})
        detail["vision_pages"] = vision_pages

        self.mongo.artifacts.update_one(
            {"sha256": doc["sha256"]},
            {"$set": {
                "text": combined,
                "extraction.detail": detail,
                "extraction.confidence": round(sum(confidences) / len(confidences), 3),
                "extraction.reocr_run_id": self.run_id,
                "extraction.reocr_at": datetime.now(timezone.utc),
                # Re-reading changes the text, so the old embeddings no longer
                # match their source and must be regenerated.
                "indexing.model": None,
            }},
        )

    # ------------------------------------------------------------------
    def _save(
        self, sha: str, *, text: str, method: str, detail: dict,
        confidence: float = 1.0, status: str = "complete", extra: Optional[dict] = None,
    ) -> None:
        payload = {
            "extraction": {
                "status": status,
                "method": method,
                "confidence": round(confidence, 3),
                "char_count": len(text or ""),
                "detail": detail,
                "run_id": self.run_id,
                "extracted_at": datetime.now(timezone.utc),
            },
            "text": text or "",
        }
        if extra:
            payload.update(extra)
        self.mongo.artifacts.update_one({"sha256": sha}, {"$set": payload})

    def _fail(self, sha: str, reason: str) -> None:
        self.stats.errors += 1
        self.mongo.artifacts.update_one(
            {"sha256": sha},
            {"$set": {"extraction": {
                "status": "failed", "error": reason, "run_id": self.run_id,
                "extracted_at": datetime.now(timezone.utc),
            }}},
        )

    def _defer(self, sha: str, reason: str) -> None:
        self.stats.deferred += 1
        self.mongo.artifacts.update_one(
            {"sha256": sha},
            {"$set": {"extraction": {
                "status": "deferred", "reason": reason, "run_id": self.run_id,
            }}},
        )
