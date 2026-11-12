"""Files dropped into a chat — read on the spot.

A PDF or an image goes to the model as it is (it reads pages and pictures
natively). Word, Excel, CSV, text and saved emails are turned into text here.
Whatever the type, the user's question about the file is answered in the same
turn; filing it into the property record happens alongside, not first.
"""
from __future__ import annotations

import io
import mimetypes
import tempfile
from pathlib import Path
from typing import Any, Dict

from mangotree.core.hashing import sha256_bytes
from mangotree.core.logging import logger

TEXT_LIMIT = 400_000
MAX_BYTES = 30 * 1024 * 1024
_IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def _xlsx_text(data: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"## Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else str(c) for c in row]
            if any(c.strip() for c in cells):
                out.append(" | ".join(cells).rstrip(" |"))
    return "\n".join(out)


def _pdf_text(data: bytes) -> str:
    import fitz
    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n\n".join(f"[page {i + 1}]\n{p.get_text()}" for i, p in enumerate(doc))


def _docx_text(data: bytes, suffix: str) -> str:
    from mangotree.extract.documents import extract_docx
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = Path(tmp.name)
    try:
        res = extract_docx(path)
        return getattr(res, "text", "") or "\n".join(getattr(p, "text", "") for p in getattr(res, "pages", []) or [])
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def _eml_text(data: bytes) -> str:
    import email
    from email import policy
    msg = email.message_from_bytes(data, policy=policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    return f"From: {msg.get('From')}\nTo: {msg.get('To')}\nDate: {msg.get('Date')}\nSubject: {msg.get('Subject')}\n\n{text}"


def describe(data: bytes, filename: str, content_type: str = "") -> Dict[str, Any]:
    """bytes -> {sha256, name, kind, content_type, text, text_truncated, size}."""
    if len(data) > MAX_BYTES:
        raise ValueError(f"{filename} is larger than {MAX_BYTES // (1024 * 1024)} MB")
    suffix = Path(filename).suffix.lower()
    out: Dict[str, Any] = {"sha256": sha256_bytes(data), "name": filename, "size": len(data),
                           "content_type": content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                           "kind": "other", "text": "", "text_truncated": False}
    try:
        if suffix == ".pdf":
            out["kind"] = "pdf"
            out["content_type"] = "application/pdf"
            out["text"] = _pdf_text(data)                    # kept as the fallback if the file is too large to send whole
        elif suffix in _IMAGE:
            out["kind"], out["content_type"] = "image", _IMAGE[suffix]
        elif suffix in (".xlsx", ".xlsm"):
            out["kind"], out["text"] = "text", _xlsx_text(data)
        elif suffix in (".docx", ".doc"):
            out["kind"], out["text"] = "text", _docx_text(data, suffix)
        elif suffix == ".eml":
            out["kind"], out["text"] = "text", _eml_text(data)
        elif suffix in (".txt", ".md", ".csv", ".tsv", ".json", ".html", ".htm", ".log"):
            out["kind"], out["text"] = "text", data.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("chat file %s: could not read (%s)", filename, exc)
    if len(out["text"]) > TEXT_LIMIT:
        out["text"], out["text_truncated"] = out["text"][:TEXT_LIMIT], True
    return out
