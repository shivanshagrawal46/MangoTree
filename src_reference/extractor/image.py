"""
Image extraction (PNG / JPG / TIFF / BMP / GIF) → text via PaddleOCR.
"""
from __future__ import annotations

import io
from PIL import Image


def extract_image(data: bytes, *, lang: str = "en") -> tuple[str, float]:
    """Run OCR on an image attachment. Returns (text, avg_confidence)."""
    from src.extractor.ocr import ocr_image

    try:
        img = Image.open(io.BytesIO(data))
        # PaddleOCR works best on RGB
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    except Exception:
        return "", 0.0

    return ocr_image(img, lang=lang)
