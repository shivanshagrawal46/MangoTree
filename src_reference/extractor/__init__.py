"""Text-extraction layer: PDF/DOCX/image -> plain text (with OCR fallback)."""
from src.extractor.extractor import (
    ExtractionResult,
    PageResult,
    extract_from_bytes,
    extract_from_path,
)

__all__ = [
    "ExtractionResult",
    "PageResult",
    "extract_from_bytes",
    "extract_from_path",
]
