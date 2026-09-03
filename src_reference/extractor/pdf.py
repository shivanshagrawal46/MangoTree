"""
PDF text extractor — hybrid strategy.

Per page:
  1. Try the embedded text layer via PyMuPDF (fast, perfect fidelity).
  2. If the page has fewer than `ocr_min_chars` characters of extractable
     text, mark it as "needs OCR".

Then for the OCR-needed pages of a SINGLE document:
  - If count >= `vision_min_pages` (default 3) AND vision is enabled →
    dispatch ALL such pages **in parallel** to Claude Vision (Sonnet 4.5).
  - Else → run RapidOCR on each page sequentially (free, fast for 1-2 pages).

This gives us:
  • Born-digital PDFs:                      perfect text, ~50ms per doc
  • 1-2 page scans / inline images:         RapidOCR, ~2s per page, free
  • Multi-page court scans:                 Claude Vision, ~3s per page (parallel),
                                            $0.025 per page
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import List, Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image

# Disable PIL's "decompression bomb" guard. Some bankruptcy-court scanners
# produce > 178M-pixel images; we trust our own corpus and want to OCR
# them rather than refuse them.
Image.MAX_IMAGE_PIXELS = None

from src.utils.logger import logger


@dataclass
class PdfPage:
    page_no: int                      # 1-indexed
    text: str
    method: str                       # "text_layer" | "ocr" | "claude_vision"
    ocr_confidence: Optional[float] = None


_MAX_PIXELS_PER_PAGE = 12_000_000  # ~12 MP cap per rendered OCR page
_PRIMITIVE_ERR_KEYWORDS = (
    "could not create a primitive",
    "OneDnn",
    "malloc",
    "code=2",
)


def _render_page_to_image(page, dpi: int) -> Image.Image:
    """Render a PyMuPDF page at the given DPI, capping total pixels."""
    rect = page.rect
    page_pts_w = max(rect.width, 1.0)
    page_pts_h = max(rect.height, 1.0)
    zoom = dpi / 72.0
    px_w = page_pts_w * zoom
    px_h = page_pts_h * zoom
    total_px = px_w * px_h
    if total_px > _MAX_PIXELS_PER_PAGE:
        scale = (_MAX_PIXELS_PER_PAGE / total_px) ** 0.5
        zoom *= scale
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def _ocr_page_with_rapidocr(
    page,
    page_no: int,
    *,
    ocr_lang: str,
    ocr_dpi: int,
) -> Tuple[bool, Optional[PdfPage], bool]:
    """
    Try RapidOCR on a single page with DPI fallback.

    Returns (ocr_succeeded, page_obj_or_None, primitive_error_seen).
    """
    from src.extractor.ocr import ocr_image, reset_ocr  # lazy import

    primitive_err_seen = False
    for attempt_dpi in (ocr_dpi, max(150, ocr_dpi // 2), 100):
        try:
            img = _render_page_to_image(page, attempt_dpi)
            ocr_text, conf = ocr_image(img, lang=ocr_lang)
            return (
                True,
                PdfPage(
                    page_no=page_no,
                    text=ocr_text,
                    method="ocr",
                    ocr_confidence=conf,
                ),
                primitive_err_seen,
            )
        except Exception as exc:
            msg = str(exc)
            if any(k in msg for k in _PRIMITIVE_ERR_KEYWORDS):
                primitive_err_seen = True
                try:
                    reset_ocr()
                except Exception:
                    pass
            if attempt_dpi == 100:
                logger.warning(
                    f"  page {page_no}: RapidOCR failed after retries: {msg[:120]}"
                )
    return False, None, primitive_err_seen


def extract_pdf(
    data: bytes,
    *,
    ocr_lang: str = "en",
    ocr_min_chars: int = 80,
    ocr_dpi: int = 200,
    enable_ocr: bool = True,
    # Claude Vision OCR settings (hybrid mode)
    vision_enabled: bool = False,
    vision_model: str = "claude-sonnet-4-6",
    vision_min_pages: int = 3,
    vision_dpi: int = 180,
    vision_concurrency: int = 8,
    # Hard cap: if vision is OFF and a PDF has more OCR-needed pages than
    # this, OCR only the first N and leave the rest empty. Prevents
    # RapidOCR from hanging worker threads on huge scanned filings.
    max_rapidocr_pages_per_doc: int = 12,
) -> List[PdfPage]:
    """Extract per-page text from a PDF blob using the hybrid strategy."""
    pages: List[PdfPage] = []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        logger.warning(f"PDF open failed: {exc}")
        return pages

    try:
        # ---- Pass 1: text layer for every page; collect indices of OCR-needed pages.
        text_layer_pages: List[Optional[PdfPage]] = [None] * len(doc)
        ocr_needed_idxs: List[int] = []  # 0-indexed positions

        for i, page in enumerate(doc):
            page_no = i + 1
            try:
                text = page.get_text("text") or ""
            except Exception as exc:
                logger.debug(f"  page {page_no}: text-layer extraction failed: {exc}")
                text = ""
            text_stripped = text.strip()

            if len(text_stripped) >= ocr_min_chars or not enable_ocr:
                text_layer_pages[i] = PdfPage(
                    page_no=page_no,
                    text=text_stripped,
                    method="text_layer",
                )
            else:
                ocr_needed_idxs.append(i)

        # ---- Decide OCR engine per-document ----
        use_vision = (
            vision_enabled
            and len(ocr_needed_idxs) >= vision_min_pages
        )

        # ---- Pass 2: OCR ----
        if use_vision:
            logger.info(
                f"  PDF: routing {len(ocr_needed_idxs)} OCR page(s) to Claude Vision "
                f"({vision_model}, concurrency={vision_concurrency})"
            )
            from src.extractor.claude_ocr import ocr_pages_via_claude

            images_to_ocr = []
            for i in ocr_needed_idxs:
                try:
                    img = _render_page_to_image(doc[i], vision_dpi)
                    images_to_ocr.append((i + 1, img))
                except Exception as exc:
                    logger.warning(
                        f"  page {i + 1}: render failed before vision OCR: {exc}"
                    )
                    text_layer_pages[i] = PdfPage(
                        page_no=i + 1,
                        text="",
                        method="render_failed",
                    )

            try:
                vision_results = ocr_pages_via_claude(
                    images_to_ocr,
                    model=vision_model,
                    max_concurrency=vision_concurrency,
                )
            except Exception as exc:
                logger.warning(
                    f"  Claude vision batch failed entirely ({exc!r}); "
                    f"falling back to RapidOCR sequentially"
                )
                vision_results = []

            # Map results back; anything missing falls back to RapidOCR.
            # Accept BOTH frontier engines: claude_vision AND openai_vision
            # (GPT-5 fallback for content-filtered pages). Discarding
            # openai_vision here silently downgraded 76 pages to RapidOCR.
            handled_pages = set()
            for vp in vision_results:
                idx0 = vp.page_no - 1
                if vp.text and vp.method in ("claude_vision", "openai_vision"):
                    text_layer_pages[idx0] = PdfPage(
                        page_no=vp.page_no,
                        text=vp.text,
                        method=vp.method,
                        ocr_confidence=vp.ocr_confidence,
                    )
                    handled_pages.add(idx0)
                elif vp.method in ("vision_skipped_budget", "vision_failed"):
                    # fallthrough — try RapidOCR for this page
                    pass

            # Pages not handled by vision → RapidOCR fallback
            consecutive_failures = 0
            for i in ocr_needed_idxs:
                if i in handled_pages or text_layer_pages[i] is not None:
                    continue
                ok, pg, primitive_err = _ocr_page_with_rapidocr(
                    doc[i], i + 1, ocr_lang=ocr_lang, ocr_dpi=ocr_dpi
                )
                if ok and pg is not None:
                    text_layer_pages[i] = pg
                    consecutive_failures = 0
                else:
                    text_layer_pages[i] = PdfPage(
                        page_no=i + 1, text="", method="ocr_failed"
                    )
                    if primitive_err:
                        consecutive_failures += 1
                    if consecutive_failures >= 3:
                        logger.warning(
                            f"  abandoning RapidOCR fallback after {consecutive_failures} failures"
                        )
                        break
        else:
            # Small doc (1-2 OCR pages) → RapidOCR sequentially.
            # If THIS doc has more OCR pages than max_rapidocr_pages_per_doc
            # AND vision is unavailable, only OCR the first N pages —
            # remaining pages get empty text. Prevents RapidOCR from
            # hanging on 100-page scanned filings.
            n_ocr = len(ocr_needed_idxs)
            if n_ocr > max_rapidocr_pages_per_doc:
                logger.warning(
                    f"  PDF has {n_ocr} OCR pages > cap ({max_rapidocr_pages_per_doc}); "
                    f"OCR'ing first {max_rapidocr_pages_per_doc}, leaving {n_ocr - max_rapidocr_pages_per_doc} empty"
                )
                ocr_targets = ocr_needed_idxs[:max_rapidocr_pages_per_doc]
                ocr_skip = ocr_needed_idxs[max_rapidocr_pages_per_doc:]
                for i in ocr_skip:
                    text_layer_pages[i] = PdfPage(
                        page_no=i + 1, text="", method="ocr_capped"
                    )
            else:
                ocr_targets = ocr_needed_idxs

            consecutive_failures = 0
            for i in ocr_targets:
                ok, pg, primitive_err = _ocr_page_with_rapidocr(
                    doc[i], i + 1, ocr_lang=ocr_lang, ocr_dpi=ocr_dpi
                )
                if ok and pg is not None:
                    text_layer_pages[i] = pg
                    consecutive_failures = 0
                else:
                    text_layer_pages[i] = PdfPage(
                        page_no=i + 1, text="", method="ocr_failed"
                    )
                    if primitive_err:
                        consecutive_failures += 1
                    if consecutive_failures >= 3:
                        logger.warning(
                            f"  abandoning RapidOCR after {consecutive_failures} failures"
                        )
                        break

        # ---- Final assembly: produce list in page-order ----
        for i, p in enumerate(text_layer_pages):
            if p is None:
                # Page slot was never filled (rare — skipped after abandon).
                p = PdfPage(page_no=i + 1, text="", method="ocr_failed")
            pages.append(p)

    finally:
        doc.close()

    return pages
