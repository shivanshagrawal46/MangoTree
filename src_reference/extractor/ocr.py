"""
OCR backend with two pluggable engines.

Engines (selectable via env `OCR_ENGINE` or kwarg `engine=`):

  rapidocr  (default, recommended)
      Uses the same PP-OCR v4 models as PaddleOCR but runs them through
      ONNX Runtime instead of paddlepaddle. No oneDNN, no MKLDNN —
      rock-solid on Windows. Same accuracy, lower memory, no model
      downloads (the wheel ships the models).

  paddleocr
      The original PaddleOCR + paddlepaddle stack. Available as a
      fallback for users on stable Linux setups, but on Windows the
      `fused_conv2d` op in oneDNN crashes after a few hundred pages.

Both engines expose the same `ocr_image()` -> (text, avg_confidence).

Thread-safety: the predictor is not safe for concurrent calls — we use a
process-wide lock around inference.
"""
from __future__ import annotations

import io
import os
import threading
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

# Disable PIL's anti-DOS pixel cap. Some bankruptcy-court scans are huge
# but legitimate.
Image.MAX_IMAGE_PIXELS = None


_LOCK = threading.Lock()
_OCR_INSTANCE: Optional[object] = None
_OCR_LANG: Optional[str] = None
_OCR_ENGINE: Optional[str] = None
_CALL_COUNT = 0
# Periodic GC after this many OCR calls (prevents slow allocator drift).
_RESET_EVERY = 200


def _build_rapidocr(lang: str):
    """Build a RapidOCR predictor (ONNX-based)."""
    from rapidocr_onnxruntime import RapidOCR  # type: ignore

    # RapidOCR auto-detects English by default; lang is largely a no-op
    # (it bundles English + Chinese models).
    return RapidOCR()


def _build_paddleocr(lang: str):
    """Build a PaddleOCR predictor (only used if OCR_ENGINE=paddleocr)."""
    os.environ["FLAGS_use_mkldnn"] = "0"
    os.environ["FLAGS_enable_mkldnn"] = "0"
    os.environ.setdefault("CPU_NUM", "2")
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    os.environ.setdefault("MKL_NUM_THREADS", "2")
    from paddleocr import PaddleOCR  # type: ignore

    return PaddleOCR(
        use_angle_cls=True,
        lang=lang,
        show_log=False,
        use_gpu=False,
        enable_mkldnn=False,
        cpu_threads=2,
    )


def _selected_engine() -> str:
    """Resolve which engine to use."""
    return os.environ.get("OCR_ENGINE", "rapidocr").lower().strip()


def _get_ocr(lang: str = "en"):
    """Return a process-wide OCR predictor (lazy load)."""
    global _OCR_INSTANCE, _OCR_LANG, _OCR_ENGINE
    engine = _selected_engine()
    if (
        _OCR_INSTANCE is not None
        and _OCR_LANG == lang
        and _OCR_ENGINE == engine
    ):
        return _OCR_INSTANCE
    with _LOCK:
        if (
            _OCR_INSTANCE is not None
            and _OCR_LANG == lang
            and _OCR_ENGINE == engine
        ):
            return _OCR_INSTANCE
        if engine == "paddleocr":
            _OCR_INSTANCE = _build_paddleocr(lang)
        else:
            _OCR_INSTANCE = _build_rapidocr(lang)
        _OCR_LANG = lang
        _OCR_ENGINE = engine
        return _OCR_INSTANCE


def reset_ocr() -> None:
    """Drop the cached predictor; next call rebuilds it.

    Used after an OCR error to recover from a corrupted predictor state.
    """
    global _OCR_INSTANCE, _OCR_LANG, _OCR_ENGINE, _CALL_COUNT
    with _LOCK:
        _OCR_INSTANCE = None
        _OCR_LANG = None
        _OCR_ENGINE = None
        _CALL_COUNT = 0
    import gc
    gc.collect()


def _ocr_with_rapidocr(ocr, arr: np.ndarray) -> Tuple[str, float]:
    """Run RapidOCR; returns (text, avg_confidence)."""
    result, _ = ocr(arr)  # returns (List[[box, text, conf]] | None, elapsed)
    if not result:
        return "", 0.0
    lines: List[str] = []
    confs: List[float] = []
    for entry in result:
        try:
            _box, text, conf = entry
        except (ValueError, TypeError):
            continue
        if text and text.strip():
            lines.append(text)
            try:
                confs.append(float(conf))
            except (TypeError, ValueError):
                pass
    text_out = "\n".join(lines).strip()
    avg = float(sum(confs) / len(confs)) if confs else 0.0
    return text_out, avg


def _ocr_with_paddleocr(ocr, arr: np.ndarray) -> Tuple[str, float]:
    """Run PaddleOCR; returns (text, avg_confidence)."""
    result = ocr.ocr(arr, cls=True)
    if not result:
        return "", 0.0
    pages = result if isinstance(result[0], list) else [result]
    lines: List[str] = []
    confs: List[float] = []
    for page in pages:
        if not page:
            continue
        for entry in page:
            try:
                _bbox, (text, conf) = entry
            except (ValueError, TypeError):
                continue
            if text and text.strip():
                lines.append(text)
                try:
                    confs.append(float(conf))
                except (TypeError, ValueError):
                    pass
    text_out = "\n".join(lines).strip()
    avg = float(sum(confs) / len(confs)) if confs else 0.0
    return text_out, avg


def ocr_image(image, lang: str = "en") -> Tuple[str, float]:
    """
    Run OCR on a single image (PIL Image | bytes | numpy array).

    Returns
    -------
    (text, confidence)
        text       : extracted text with line breaks preserved
        confidence : average per-line confidence in [0, 1]
    """
    if isinstance(image, bytes):
        image = Image.open(io.BytesIO(image))
    if isinstance(image, Image.Image):
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        arr = np.asarray(image)
    else:
        arr = image

    global _CALL_COUNT
    ocr = _get_ocr(lang)
    engine = _OCR_ENGINE or _selected_engine()

    with _LOCK:
        if engine == "paddleocr":
            text, conf = _ocr_with_paddleocr(ocr, arr)
        else:
            text, conf = _ocr_with_rapidocr(ocr, arr)
        _CALL_COUNT += 1
        do_reset = _CALL_COUNT >= _RESET_EVERY

    if do_reset:
        import gc
        gc.collect()
        with _LOCK:
            _CALL_COUNT = 0

    return text, conf
