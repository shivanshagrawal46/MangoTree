"""
.docx extraction (paragraphs + tables, in document order).
"""
from __future__ import annotations

import io
from typing import List

from docx import Document  # type: ignore


def extract_docx(data: bytes) -> str:
    """Return the document body text (paragraphs + tables) in reading order."""
    try:
        doc = Document(io.BytesIO(data))
    except Exception:
        return ""

    parts: List[str] = []

    # Iterate over body elements in order so paragraphs and tables stay
    # in their original sequence.
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            text = "".join(node.text or "" for node in child.iter() if node.tag.endswith("}t"))
            text = text.strip()
            if text:
                parts.append(text)
        elif tag == "tbl":
            rows = []
            for row in child.iter():
                if not row.tag.endswith("}tr"):
                    continue
                cells = []
                for cell in row.iter():
                    if not cell.tag.endswith("}tc"):
                        continue
                    cell_text = "".join(
                        node.text or "" for node in cell.iter() if node.tag.endswith("}t")
                    ).strip()
                    if cell_text:
                        cells.append(cell_text)
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                parts.append("\n".join(rows))

    return "\n\n".join(parts).strip()
