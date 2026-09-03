"""
Sprint 3, Step 1.5 — Rescue Pass.

Handlers for file formats the v1 extractor marked as `skipped`. Each
handler returns an `ExtractionResult` shaped exactly like the ones the
main `extract_from_bytes` produces, so downstream code can stay
identical.

Routing strategy
----------------
  .doc            -> MS Word COM (pywin32). Lossless digital text.
  .xls            -> xlrd 1.2.0, with MS Excel COM fallback for modern
                     .xls files xlrd doesn't grok (unknown FuncIDs etc).
  .htm / .html    -> BeautifulSoup.         Strips tags.
  .eml            -> email.parser stdlib.   Reads MIME.
  .mp3 / .wav etc -> OpenAI Whisper API.    Voicemail transcription.
  no-extension    -> libmagic sniff -> recurse into the right handler
                     above. Falls back to the existing extractor for
                     anything that sniffs as PDF / DOCX / XLSX / image.
                     Also detects MAPI Outlook-message blobs (stripped
                     .msg items) and string-mines them.
  any image       -> Claude Vision re-OCR (stronger than RapidOCR).

Method tags emitted (`ExtractionResult.method`):
    "doc"            – legacy Word rescued
    "xls"            – legacy Excel rescued
    "xls_excel_com"  – modern .xls rescued via MS Excel COM
    "html"           – HTML attachment rescued
    "eml"            – nested email rescued
    "mapi_blob"      – stripped Outlook MAPI item rescued via string mining
    "audio_whisper"  – audio transcribed
    "image_vision"   – image re-OCR'd by Claude Vision

When no-ext sniffs as a format that the *original* extractor handles
(pdf, docx, xlsx, image, raw text), we delegate to the original
extractor and pass-through its method tag.
"""
from __future__ import annotations

import email
import io
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from src.extractor.extractor import (
    ExtractionResult,
    PageResult,
    extract_from_bytes,
)
from src.utils.logger import logger


# =========================================================================
# 1. Legacy Word (.doc)  via MS Word COM
# =========================================================================

def extract_doc_via_word_com(data: bytes, filename: str) -> ExtractionResult:
    """Use MS Word COM automation to extract text from a legacy .doc file.

    Word must be installed on the host. Falls back to a skipped result if
    Word isn't reachable or the file is encrypted/corrupt.
    """
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="doc_no_word_com_lib",
        )

    tmp_path: Optional[str] = None
    word = None
    doc = None
    pythoncom.CoInitialize()
    try:
        # Word demands a filesystem path; write the GridFS bytes to a
        # temporary .doc file.
        with tempfile.NamedTemporaryFile(
            suffix=".doc", delete=False, prefix="rescue_doc_"
        ) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone — suppress all dialogs
        # msoAutomationSecurityForceDisable = 3 — prevents macro auto-run.
        try:
            word.AutomationSecurity = 3
        except Exception:
            pass

        doc = word.Documents.Open(
            tmp_path,
            ReadOnly=True,
            ConfirmConversions=False,
            AddToRecentFiles=False,
            PasswordDocument="",  # empty pwd — skip password-protected docs
            NoEncodingDialog=True,
        )
        text = (doc.Range().Text or "").replace("\r", "\n").strip()

    except Exception as exc:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"doc_word_com_error:{type(exc).__name__}",
        )
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        pythoncom.CoUninitialize()

    if not text:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="doc_empty",
        )
    return ExtractionResult(
        text=text,
        method="doc",
        pages=[PageResult(1, text, "word_com")],
        char_count=len(text),
    )


# =========================================================================
# 2. Legacy Excel (.xls)  via xlrd
# =========================================================================

def extract_xls_via_xlrd(data: bytes, filename: str) -> ExtractionResult:
    """Try `xlrd` first; if it asserts on a modern .xls feature (newer
    function IDs, encryption hint, etc.), fall back to MS Excel COM."""
    try:
        import xlrd  # type: ignore  (we pinned xlrd==1.2.0 which supports .xls)
    except ImportError:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="xls_no_xlrd_lib",
        )
    try:
        wb = xlrd.open_workbook(file_contents=data)
    except Exception as exc:
        # xlrd has known issues with modern .xls files (unknown FuncIDs,
        # newer property records). Try MS Excel COM as a second chance.
        result = extract_xls_via_excel_com(data, filename)
        if result.method != "skipped":
            return result
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"xls_open_error:{type(exc).__name__}",
        )

    parts: list[str] = []
    for sheet in wb.sheets():
        parts.append(f"# Sheet: {sheet.name}")
        for row_idx in range(sheet.nrows):
            row = sheet.row(row_idx)
            cells = []
            for c in row:
                v = c.value
                if v is None:
                    continue
                if isinstance(v, float) and v.is_integer():
                    v = int(v)
                s = str(v).strip()
                if s:
                    cells.append(s)
            if cells:
                parts.append(" | ".join(cells))
    text = "\n".join(parts).strip()
    if not text:
        # xlrd opened the file but didn't see any data — try COM too,
        # which sometimes recovers data from sheets xlrd's reader skips.
        result = extract_xls_via_excel_com(data, filename)
        if result.method != "skipped":
            return result
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="xls_empty",
        )
    return ExtractionResult(
        text=text,
        method="xls",
        pages=[PageResult(1, text, "xlrd")],
        char_count=len(text),
    )


# =========================================================================
# 2b. Legacy/Modern Excel (.xls) via MS Excel COM (fallback)
# =========================================================================

def extract_xls_via_excel_com(data: bytes, filename: str) -> ExtractionResult:
    """Open a .xls file in MS Excel silently and read every cell of every
    sheet. Slow but bulletproof for files xlrd can't parse."""
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="xls_no_excel_com_lib",
        )

    tmp_path: Optional[str] = None
    excel = None
    wb = None
    pythoncom.CoInitialize()
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".xls", delete=False, prefix="rescue_xls_"
        ) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        try:
            excel.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        except Exception:
            pass

        wb = excel.Workbooks.Open(
            tmp_path,
            UpdateLinks=0,
            ReadOnly=True,
            IgnoreReadOnlyRecommended=True,
            CorruptLoad=2,  # xlExtractData — give us as much as we can salvage
        )

        parts: list[str] = []
        for sheet in wb.Worksheets:
            parts.append(f"# Sheet: {sheet.Name}")
            used = sheet.UsedRange
            if used is None or used.Rows.Count == 0:
                continue
            # `.Value2` returns a tuple-of-tuples for multi-cell ranges
            # or a scalar for single cells. Normalise to 2D iterable.
            values = used.Value2
            if values is None:
                continue
            if not isinstance(values, tuple):
                values = ((values,),)
            elif values and not isinstance(values[0], tuple):
                values = (values,)
            for row in values:
                cells = []
                for v in row:
                    if v is None:
                        continue
                    if isinstance(v, float) and v.is_integer():
                        v = int(v)
                    s = str(v).strip()
                    if s:
                        cells.append(s)
                if cells:
                    parts.append(" | ".join(cells))
        text = "\n".join(parts).strip()

    except Exception as exc:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"xls_excel_com_error:{type(exc).__name__}",
        )
    finally:
        try:
            if wb is not None:
                wb.Close(SaveChanges=False)
        except Exception:
            pass
        try:
            if excel is not None:
                excel.Quit()
        except Exception:
            pass
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        pythoncom.CoUninitialize()

    if not text:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="xls_excel_com_empty",
        )
    return ExtractionResult(
        text=text,
        method="xls_excel_com",
        pages=[PageResult(1, text, "excel_com")],
        char_count=len(text),
    )


# =========================================================================
# 3. HTML (.htm / .html)  via BeautifulSoup
# =========================================================================

def extract_html(data: bytes, filename: str) -> ExtractionResult:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="html_no_bs4",
        )

    raw = _decode_text_bytes(data)
    try:
        soup = BeautifulSoup(raw, "html.parser")
    except Exception as exc:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"html_parse_error:{type(exc).__name__}",
        )
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n").strip()
    # Collapse runs of blank lines (common in Outlook HTML emails).
    text = "\n".join(line for line in (l.strip() for l in text.splitlines()) if line)
    if not text:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="html_empty",
        )
    return ExtractionResult(
        text=text,
        method="html",
        pages=[PageResult(1, text, "html")],
        char_count=len(text),
    )


# =========================================================================
# 4. Nested email (.eml)  via stdlib email.parser
# =========================================================================

def extract_eml(data: bytes, filename: str) -> ExtractionResult:
    try:
        msg = email.message_from_bytes(data)
    except Exception as exc:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"eml_parse_error:{type(exc).__name__}",
        )

    parts: list[str] = []
    for hdr in ("From", "To", "Cc", "Subject", "Date"):
        v = msg.get(hdr)
        if v:
            parts.append(f"{hdr}: {v}")
    parts.append("")  # blank line between headers and body

    for sub in msg.walk():
        ctype = sub.get_content_type()
        if ctype == "text/plain":
            try:
                body = sub.get_payload(decode=True) or b""
                parts.append(_decode_text_bytes(body))
            except Exception:
                continue
        elif ctype == "text/html":
            try:
                from bs4 import BeautifulSoup  # type: ignore
                body = sub.get_payload(decode=True) or b""
                soup = BeautifulSoup(_decode_text_bytes(body), "html.parser")
                for t in soup(["script", "style"]):
                    t.decompose()
                parts.append(soup.get_text(separator="\n"))
            except ImportError:
                pass

    text = "\n".join(p for p in parts if p).strip()
    if not text:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="eml_empty",
        )
    return ExtractionResult(
        text=text,
        method="eml",
        pages=[PageResult(1, text, "eml")],
        char_count=len(text),
    )


# =========================================================================
# 5. Audio (.mp3 / .wav / .m4a / ...)  via OpenAI Whisper API
# =========================================================================

def extract_audio_via_whisper(data: bytes, filename: str) -> ExtractionResult:
    """Transcribe an audio file via OpenAI Whisper.

    Requires OPENAI_API_KEY in env. ~$0.006 per minute. Returns
    plain transcribed text. Audio is uploaded to OpenAI servers.
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="audio_no_openai_key",
        )
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="audio_no_openai_lib",
        )

    suffix = Path(filename).suffix or ".mp3"
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=suffix, delete=False, prefix="rescue_audio_"
        ) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        client = OpenAI(api_key=api_key)
        with open(tmp_path, "rb") as fh:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=fh,
                response_format="text",
            )
        # response_format="text" returns a plain str
        text = (resp if isinstance(resp, str) else getattr(resp, "text", "")).strip()
    except Exception as exc:
        # Capture as much detail as possible — OpenAI errors are nuanced
        # (permission denied vs no credit vs model unavailable vs file too
        # big) and the type alone doesn't tell the operator what to fix.
        msg = str(exc) or repr(exc)
        logger.warning(f"  Whisper error on {filename!r}: {msg[:300]}")
        # Trim to a single-line db-safe reason that includes the API msg.
        short = msg.replace("\n", " ").replace("\r", " ")[:160]
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"audio_whisper_error:{type(exc).__name__}:{short}",
        )
    finally:
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

    if not text:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="audio_whisper_empty",
        )
    return ExtractionResult(
        text=text,
        method="audio_whisper",
        pages=[PageResult(1, text, "whisper")],
        char_count=len(text),
    )


# =========================================================================
# 6. Image re-OCR  via Claude Vision (stronger than RapidOCR)
# =========================================================================

def re_ocr_image_via_vision(data: bytes, filename: str) -> ExtractionResult:
    """Re-run image OCR with Claude Vision instead of RapidOCR.

    Used for images where the v1 RapidOCR pass returned no text. Claude
    Vision is materially stronger on small / low-contrast / handwritten
    images. If Vision itself also returns nothing, we mark the doc as
    `image_no_text_confirmed` so a human can decide whether it's truly
    blank (logo, icon, signature with no readable name) or worth a
    manual review.
    """
    try:
        from PIL import Image
    except ImportError:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="image_vision_no_pil",
        )

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        # Normalise to RGB so claude_ocr can encode as JPEG.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
    except Exception as exc:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"image_open_error:{type(exc).__name__}",
        )

    try:
        from src.extractor.claude_ocr import ocr_pages_via_claude
    except ImportError:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="image_vision_no_claude_ocr",
        )

    try:
        results = ocr_pages_via_claude(
            [(1, img)],
            model="claude-sonnet-4-6",
            max_concurrency=1,
        )
    except Exception as exc:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason=f"image_vision_error:{type(exc).__name__}",
        )

    text = (results[0].text if results else "").strip()
    if not text:
        return ExtractionResult(
            text="",
            method="skipped",
            skipped_reason="image_no_text_confirmed",
        )
    return ExtractionResult(
        text=text,
        method="image_vision",
        pages=[PageResult(1, text, "claude_vision",
                          results[0].ocr_confidence if results else None)],
        char_count=len(text),
        avg_ocr_confidence=results[0].ocr_confidence if results else None,
    )


# =========================================================================
# 7. Stripped Outlook MAPI message blob  (string mining)
# =========================================================================
#
# When PST/Outlook export tools encounter an Outlook-embedded-message
# attachment (e.g. a forwarded email which Outlook stores as a MAPI item,
# NOT as a .eml file), they sometimes dump the raw MAPI item stream with
# no extension. These blobs are full of UTF-16LE strings (subject, body,
# sender, recipients, message-id, etc.) separated by MAPI binary headers.
#
# We can't fully parse the proprietary MAPI structure, but we can mine
# every readable UTF-16LE / ASCII string out of it. For a legal-RAG use
# case that's more than enough — we just need the *text* to be indexable.

def _is_likely_mapi_blob(data: bytes) -> bool:
    """Heuristic: the first ~256 bytes contain UTF-16LE 'IPM.' which is the
    Outlook MessageClass prefix (e.g. IPM.Note, IPM.Appointment)."""
    head = data[:512]
    # 'IPM.' as UTF-16LE bytes: I=0x49 ' '=0x00 P=0x50 0x00 M=0x4d 0x00 .=0x2e 0x00
    return b"\x49\x00\x50\x00\x4d\x00\x2e\x00" in head


def _utf16le_strings(data: bytes, min_len: int = 4) -> List[str]:
    """Scan bytes for runs of printable UTF-16LE characters >= min_len."""
    out: List[str] = []
    cur: List[str] = []
    i = 0
    n = len(data)
    while i + 1 < n:
        lo, hi = data[i], data[i + 1]
        if hi == 0 and (32 <= lo < 127 or lo in (9, 10, 13)):
            cur.append(chr(lo))
            i += 2
        else:
            if len(cur) >= min_len:
                out.append("".join(cur).strip())
            cur = []
            i += 1
    if len(cur) >= min_len:
        out.append("".join(cur).strip())
    return [s for s in out if s]


_ASCII_RE = re.compile(rb"[\x20-\x7e\t\r\n]{8,}")


def _ascii_strings(data: bytes) -> List[str]:
    return [m.group().decode("ascii", errors="ignore").strip()
            for m in _ASCII_RE.finditer(data)]


def extract_mapi_blob(data: bytes, filename: str) -> ExtractionResult:
    """Mine readable strings from a stripped Outlook MAPI item blob.

    Output order:
      1. Headers / metadata (subject, sender, recipients, ids)  – any
         short UTF-16LE strings that look like names or addresses.
      2. Body text — the longest UTF-16LE string we find.
      3. Any additional ASCII-only sections (rare but possible).
    """
    if not data:
        return ExtractionResult(
            text="", method="skipped",
            skipped_reason="mapi_empty",
        )

    utf16 = _utf16le_strings(data, min_len=4)
    ascii_only = _ascii_strings(data)

    if not utf16 and not ascii_only:
        return ExtractionResult(
            text="", method="skipped",
            skipped_reason="mapi_no_text",
        )

    # De-duplicate while preserving order. Many MAPI items repeat strings
    # (sender appears in From, in Reply-To, in display name, etc.) so
    # we just want each unique line once. Keep the longest version of
    # near-duplicates so we don't drop the actual body.
    seen: set = set()
    lines: List[str] = []
    for s in sorted(utf16, key=len, reverse=True):  # longest first
        key = s[:120]  # de-dup key
        if key in seen:
            continue
        seen.add(key)
        # Skip obvious MAPI noise.
        if s in ("IPM.Note", "IPM.", "SMTP"):
            continue
        lines.append(s)
    # Append ASCII-only strings that didn't appear in UTF-16LE.
    for s in ascii_only:
        if not any(s in u for u in utf16):
            lines.append(s)

    # Put the longest line (= almost always the body) at the end so the
    # RAG chunker treats it as the most-recent / most-prominent content;
    # short metadata leads the doc.
    if lines:
        longest = max(lines, key=len)
        if longest != lines[-1]:
            lines.remove(longest)
            lines.append(longest)

    text = "\n".join(lines).strip()
    if not text:
        return ExtractionResult(
            text="", method="skipped",
            skipped_reason="mapi_no_text_after_dedup",
        )
    return ExtractionResult(
        text=text,
        method="mapi_blob",
        pages=[PageResult(1, text, "mapi_strings")],
        char_count=len(text),
    )


# =========================================================================
# 8. No-extension  via magic-byte sniff
# =========================================================================

def _sniff_mime(data: bytes) -> Tuple[str, str]:
    """Return (mime_type, description) using libmagic if available, with a
    pure-Python fallback that handles the most common signatures.
    """
    try:
        import magic  # type: ignore
        # python-magic-bin ships libmagic for Windows.
        mime = magic.from_buffer(data, mime=True) or ""
        desc = magic.from_buffer(data) or ""
        return mime.lower(), desc
    except Exception:
        # Pure-Python fallback — header sniff for the common formats we
        # care about. We only return mime; description is best-effort.
        head = data[:16]
        if head.startswith(b"%PDF"):
            return "application/pdf", "pdf"
        if head.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "jpeg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "png"
        if head.startswith(b"GIF8"):
            return "image/gif", "gif"
        if head.startswith(b"BM"):
            return "image/bmp", "bmp"
        if head.startswith(b"PK\x03\x04"):
            return "application/zip", "zip-based (docx/xlsx/pptx/zip)"
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            return "application/x-ole-storage", "ole2 (doc/xls/msg)"
        if head.startswith(b"ID3") or head[:2] == b"\xff\xfb":
            return "audio/mpeg", "mp3"
        if head.startswith(b"RIFF"):
            return "audio/x-wav", "riff (wav)"
        if head.lower().lstrip().startswith(b"<!doctype html") or \
           head.lower().lstrip().startswith(b"<html"):
            return "text/html", "html"
        try:
            data[:512].decode("utf-8")
            return "text/plain", "text"
        except UnicodeDecodeError:
            return "application/octet-stream", "unknown binary"


def _zip_subtype(data: bytes) -> str:
    """For ZIP-based files (PK\\x03\\x04), peek inside to tell docx/xlsx
    apart from a plain .zip archive."""
    try:
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" in names:
                return "docx"
            if "xl/workbook.xml" in names:
                return "xlsx"
            if "ppt/presentation.xml" in names:
                return "pptx"
            return "zip"
    except Exception:
        return "zip"


def extract_no_ext_via_sniff(data: bytes, filename: str) -> ExtractionResult:
    """Sniff bytes, then route to the best handler."""
    # Check for stripped Outlook MAPI items FIRST — these would otherwise
    # sniff as 'application/octet-stream' and be lost. The MAPI signature
    # is the UTF-16LE 'IPM.' string near the head of the file.
    if _is_likely_mapi_blob(data):
        return extract_mapi_blob(data, filename or "mapi_blob")

    mime, desc = _sniff_mime(data)
    if mime == "application/pdf":
        return extract_from_bytes(
            data,
            (filename or "unknown") + ".pdf",
            vision_enabled=True,
            vision_min_pages=1,
        )
    if mime in ("image/jpeg",):
        return re_ocr_image_via_vision(data, filename + ".jpg")
    if mime in ("image/png",):
        return re_ocr_image_via_vision(data, filename + ".png")
    if mime in ("image/gif", "image/bmp", "image/tiff", "image/webp"):
        return re_ocr_image_via_vision(data, filename + ".img")
    if mime == "audio/mpeg":
        return extract_audio_via_whisper(data, filename + ".mp3")
    if mime in ("audio/x-wav", "audio/wav"):
        return extract_audio_via_whisper(data, filename + ".wav")
    if mime in ("audio/mp4", "audio/x-m4a"):
        return extract_audio_via_whisper(data, filename + ".m4a")
    if mime == "text/html":
        return extract_html(data, filename + ".html")
    if mime == "text/plain":
        text = _decode_text_bytes(data).strip()
        if not text:
            return ExtractionResult(
                text="", method="skipped",
                skipped_reason="noext_text_empty",
            )
        return ExtractionResult(
            text=text,
            method="raw_text",
            pages=[PageResult(1, text, "raw")],
            char_count=len(text),
        )
    if mime == "application/zip":
        sub = _zip_subtype(data)
        if sub == "docx":
            return extract_from_bytes(data, filename + ".docx")
        if sub == "xlsx":
            return extract_from_bytes(data, filename + ".xlsx")
        return ExtractionResult(
            text="", method="skipped",
            skipped_reason=f"noext_zip:{sub}",
        )
    if mime == "application/x-ole-storage":
        # OLE2 compound — almost always a legacy .doc, .xls, or .msg.
        # Try .doc first (Word COM happily reads compound files
        # regardless of extension), then .xls as fallback.
        result = extract_doc_via_word_com(data, filename + ".doc")
        if result.text:
            return result
        result = extract_xls_via_xlrd(data, filename + ".xls")
        if result.text:
            return result
        return ExtractionResult(
            text="", method="skipped",
            skipped_reason="noext_ole2_unhandled",
        )

    return ExtractionResult(
        text="", method="skipped",
        skipped_reason=f"noext_unknown_mime:{mime or 'none'}",
    )


# =========================================================================
# 8b. TNEF (winmail.dat) — unwrap embedded documents
# =========================================================================

def extract_tnef(data: bytes, filename: str) -> ExtractionResult:
    """Decode an Outlook winmail.dat (TNEF) blob and extract text from every
    document wrapped inside it (PDF / Word / Excel / image / …), recursively.
    Each embedded file is run back through the full extractor (incl. Claude
    Vision OCR for scanned PDFs) so nothing inside the .dat is missed."""
    try:
        from tnefparse import TNEF
    except ImportError:
        return ExtractionResult(text="", method="skipped",
                                skipped_reason="tnef_lib_missing")
    try:
        tnef = TNEF(data)
    except Exception as exc:  # noqa: BLE001
        return ExtractionResult(text="", method="skipped",
                                skipped_reason=f"tnef_parse_error:{exc}")

    # Full extraction params so embedded scanned PDFs still get Vision OCR.
    try:
        from config.settings import Settings
        st = Settings.load()
        vparams = dict(
            ocr_lang=st.ocr_lang, ocr_min_chars=st.ocr_text_layer_min_chars,
            ocr_dpi=st.ocr_dpi, enable_ocr=True,
            vision_enabled=st.ocr_vision_enabled, vision_model=st.ocr_vision_model,
            vision_min_pages=st.ocr_vision_min_pages, vision_dpi=st.ocr_vision_dpi,
            vision_concurrency=st.ocr_vision_max_concurrency,
        )
    except Exception:  # noqa: BLE001
        vparams = dict(enable_ocr=True)

    parts: List[str] = []
    pages: List[PageResult] = []
    pno = 1

    # TNEF message body (html / plain) if present.
    body = ""
    try:
        hb = getattr(tnef, "htmlbody", None)
        if hb:
            from src.cleaner import html_to_text
            body = html_to_text(hb if isinstance(hb, str) else hb.decode("utf-8", "ignore"))
        else:
            b = getattr(tnef, "body", None)
            if b:
                body = b if isinstance(b, str) else b.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        body = ""
    if body and body.strip():
        parts.append(body.strip())
        pages.append(PageResult(pno, body.strip(), "tnef_body"))
        pno += 1

    n_emb = 0
    for att in (getattr(tnef, "attachments", None) or []):
        try:
            aname = att.name
            if isinstance(aname, bytes):
                aname = aname.decode("utf-8", "ignore")
            aname = (aname or "tnef_attachment").strip("\x00 ")
            adata = att.data
        except Exception:  # noqa: BLE001
            continue
        if not adata:
            continue
        n_emb += 1
        header = f"[Embedded: {aname}]"
        try:
            sub = extract_from_bytes(adata, aname, **vparams)
        except Exception:  # noqa: BLE001
            sub = None
        if sub and (sub.text or "").strip():
            parts.append(f"{header}\n{sub.text.strip()}")
            pages.append(PageResult(pno, sub.text.strip(), f"tnef:{sub.method}"))
            pno += 1
        else:
            parts.append(f"{header} (no extractable text)")

    text = "\n\n".join(parts).strip()
    if not text:
        return ExtractionResult(text="", method="skipped",
                                skipped_reason=f"tnef_no_content(emb={n_emb})")
    return ExtractionResult(
        text=text, method="tnef",
        pages=pages or [PageResult(1, text, "tnef")],
        char_count=len(text),
    )


# =========================================================================
# 8c. Modern zip-based Office (.xlsx / .xlsm / .pptx / .odt / .docx)
# =========================================================================

def extract_office_zip(data: bytes, filename: str) -> ExtractionResult:
    """Generic text extraction from modern zip-based Office formats by reading
    the XML parts and stripping tags. Good enough for retrieval across
    spreadsheets, decks, and ODF docs without per-format libraries."""
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return ExtractionResult(text="", method="skipped",
                                skipped_reason=f"officezip_bad:{exc}")
    keep = ("word/document", "xl/sharedstrings", "xl/worksheets",
            "ppt/slides/slide", "ppt/notesslides", "content.xml")
    chunks: List[str] = []
    for name in zf.namelist():
        low = name.lower()
        if not low.endswith(".xml") or not any(k in low for k in keep):
            continue
        try:
            raw = zf.read(name).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        txt = re.sub(r"<[^>]+>", " ", raw)
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            chunks.append(txt)
    text = "\n".join(chunks).strip()
    if not text:
        return ExtractionResult(text="", method="skipped",
                                skipped_reason="officezip_no_text")
    return ExtractionResult(
        text=text, method="office_zip",
        pages=[PageResult(1, text, "office_zip")],
        char_count=len(text),
    )


# =========================================================================
# 8d. Outlook message (.msg)  via extract_msg
# =========================================================================

def extract_msg_outlook(data: bytes, filename: str) -> ExtractionResult:
    """Parse an Outlook .msg (OLE2 MAPI) file: headers + body, and recurse
    into every embedded attachment (PDF / Word / Excel / image / nested .msg)
    so documents *inside* the message are not lost. Falls back to MAPI string
    mining if the extract_msg library can't open the file."""
    try:
        import extract_msg  # type: ignore
    except ImportError:
        # Library missing — still salvage text via raw MAPI string mining.
        return extract_mapi_blob(data, filename)

    tmp_path: Optional[str] = None
    msg = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".msg", delete=False, prefix="rescue_msg_"
        ) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        msg = extract_msg.openMsg(tmp_path)

        parts: List[str] = []
        for label, val in (
            ("From", getattr(msg, "sender", None)),
            ("To", getattr(msg, "to", None)),
            ("Cc", getattr(msg, "cc", None)),
            ("Subject", getattr(msg, "subject", None)),
            ("Date", getattr(msg, "date", None)),
        ):
            if val:
                parts.append(f"{label}: {val}")
        parts.append("")

        body = (getattr(msg, "body", None) or "").strip()
        if not body:
            hb = getattr(msg, "htmlBody", None)
            if hb:
                try:
                    from bs4 import BeautifulSoup  # type: ignore
                    raw = hb.decode("utf-8", "ignore") if isinstance(hb, bytes) else hb
                    soup = BeautifulSoup(raw, "html.parser")
                    for t in soup(["script", "style"]):
                        t.decompose()
                    body = soup.get_text(separator="\n").strip()
                except Exception:  # noqa: BLE001
                    body = ""
        if body:
            parts.append(body)

        # Recurse into embedded attachments.
        for att in (getattr(msg, "attachments", None) or []):
            try:
                aname = (getattr(att, "longFilename", None)
                         or getattr(att, "shortFilename", None)
                         or "attachment")
                adata = getattr(att, "data", None)
            except Exception:  # noqa: BLE001
                continue
            if adata is None:
                continue
            header = f"[Embedded: {aname}]"
            if isinstance(adata, (bytes, bytearray)):
                try:
                    sub = extract_from_bytes(bytes(adata), aname)
                    if sub.method == "skipped":
                        sub = rescue_extract(bytes(adata), aname,
                                             skipped_reason=sub.skipped_reason)
                except Exception:  # noqa: BLE001
                    sub = None
                if sub and (sub.text or "").strip():
                    parts.append(f"{header}\n{sub.text.strip()}")
                else:
                    parts.append(f"{header} (no extractable text)")
            else:
                # Embedded Outlook message object — pull its body text.
                emb_body = (getattr(adata, "body", None) or "").strip()
                emb_subj = getattr(adata, "subject", None)
                if emb_subj:
                    parts.append(f"{header} Subject: {emb_subj}")
                if emb_body:
                    parts.append(emb_body)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"  .msg parse failed for {filename!r}: {exc}; "
                       f"falling back to MAPI string mining")
        return extract_mapi_blob(data, filename)
    finally:
        try:
            if msg is not None:
                msg.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:  # noqa: BLE001
            pass

    text = "\n".join(p for p in parts if p is not None).strip()
    if not text:
        return extract_mapi_blob(data, filename)
    return ExtractionResult(
        text=text, method="msg",
        pages=[PageResult(1, text, "msg")],
        char_count=len(text),
    )


# =========================================================================
# 8e. ZIP archive (.zip)  — unpack and recurse into every member
# =========================================================================

# Safety caps so a malicious/huge archive can't exhaust memory.
_ZIP_MAX_MEMBERS = 200
_ZIP_MAX_MEMBER_BYTES = 60 * 1024 * 1024     # 60 MB per member
_ZIP_MAX_TOTAL_BYTES = 300 * 1024 * 1024     # 300 MB uncompressed total


def extract_zip_archive(data: bytes, filename: str) -> ExtractionResult:
    """Unpack a .zip and run every member back through the full extractor
    (and rescue, for legacy members) so documents bundled inside an archive
    are indexed individually. Best-effort, with anti-zip-bomb guards."""
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        return ExtractionResult(text="", method="skipped",
                                skipped_reason=f"zip_bad:{type(exc).__name__}")

    # Extraction params for archive members: SCANNED PDF/image pages go to
    # Claude/GPT vision (vision_min_pages=1), while BORN-DIGITAL PDF pages keep
    # their exact embedded text layer (the ground truth — re-OCR'ing perfect
    # digital text only risks introducing errors and is hugely slower on big
    # docs). Mirrors the TNEF handler.
    try:
        from config.settings import Settings
        st = Settings.load()
        vparams = dict(
            ocr_lang=st.ocr_lang, ocr_min_chars=st.ocr_text_layer_min_chars,
            ocr_dpi=st.ocr_dpi, enable_ocr=True,
            vision_enabled=True, vision_model=st.ocr_vision_model,
            vision_min_pages=1, vision_dpi=st.ocr_vision_dpi,
            vision_concurrency=st.ocr_vision_max_concurrency,
        )
    except Exception:  # noqa: BLE001
        vparams = dict(enable_ocr=True, vision_enabled=True, vision_min_pages=1)

    parts: List[str] = []
    pages: List[PageResult] = []
    pno = 1
    total = 0
    n_files = 0
    n_text = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        if n_files >= _ZIP_MAX_MEMBERS:
            parts.append(f"[ZIP truncated: >{_ZIP_MAX_MEMBERS} members]")
            break
        if info.file_size > _ZIP_MAX_MEMBER_BYTES:
            parts.append(f"[Skipped oversized member: {info.filename} "
                         f"({info.file_size} bytes)]")
            continue
        if total + info.file_size > _ZIP_MAX_TOTAL_BYTES:
            parts.append("[ZIP truncated: total uncompressed size cap hit]")
            break
        n_files += 1
        try:
            member = zf.read(info.filename)
        except Exception:  # noqa: BLE001
            continue
        total += len(member)
        mname = info.filename.rsplit("/", 1)[-1]
        header = f"[Archived: {info.filename}]"
        try:
            sub = extract_from_bytes(member, mname, **vparams)
            if sub.method == "skipped":
                sub = rescue_extract(member, mname,
                                     skipped_reason=sub.skipped_reason)
        except Exception:  # noqa: BLE001
            sub = None
        if sub and (sub.text or "").strip():
            n_text += 1
            parts.append(f"{header}\n{sub.text.strip()}")
            pages.append(PageResult(pno, sub.text.strip(), f"zip:{sub.method}"))
            pno += 1
        else:
            parts.append(f"{header} (no extractable text)")

    text = "\n\n".join(p for p in parts if p).strip()
    if not text:
        return ExtractionResult(text="", method="skipped",
                                skipped_reason=f"zip_no_content(members={n_files})")
    return ExtractionResult(
        text=text, method="zip",
        pages=pages or [PageResult(1, text, "zip")],
        char_count=len(text),
    )


# =========================================================================
# 9. Dispatcher used by the orchestrator
# =========================================================================

_IMAGE_RE_OCR_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp",
                       ".tif", ".tiff", ".webp"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_OFFICE_ZIP_EXTS = {".xlsx", ".xlsm", ".pptx", ".odt", ".docx"}


def rescue_extract(
    data: bytes,
    filename: str,
    *,
    skipped_reason: Optional[str] = None,
) -> ExtractionResult:
    """Single entrypoint used by the rescue orchestrator.

    `skipped_reason` is the v1 reason this attachment was skipped; we
    use it as a hint (e.g. `image_no_text` -> re-OCR with Vision even
    though the extension is .png).
    """
    if not data:
        return ExtractionResult(
            text="", method="skipped",
            skipped_reason="empty_bytes",
        )
    ext = Path(filename or "").suffix.lower()

    if ext == ".doc":
        return extract_doc_via_word_com(data, filename)
    if ext == ".xls":
        return extract_xls_via_xlrd(data, filename)
    if ext in (".htm", ".html"):
        return extract_html(data, filename)
    if ext == ".eml":
        return extract_eml(data, filename)
    if ext == ".msg":
        return extract_msg_outlook(data, filename)
    if ext == ".zip":
        return extract_zip_archive(data, filename)
    if ext in (".ics", ".calendar", ".vcs", ".vcf"):
        # iCalendar invite / vCard contact — plain text. Index verbatim.
        cal = _decode_text_bytes(data).strip()
        if not cal:
            return ExtractionResult(text="", method="skipped",
                                    skipped_reason="ics_empty")
        tag = "vcard" if ext == ".vcf" else "ics"
        return ExtractionResult(text=cal, method=tag,
                                pages=[PageResult(1, cal, tag)],
                                char_count=len(cal))
    if ext in _AUDIO_EXTS:
        return extract_audio_via_whisper(data, filename)
    if ext == "":
        return extract_no_ext_via_sniff(data, filename)
    if ext == ".dat":
        return extract_tnef(data, filename)
    if ext in _OFFICE_ZIP_EXTS:
        return extract_office_zip(data, filename)
    if ext in _IMAGE_RE_OCR_EXTS:
        # Either the v1 pass said `image_no_text` (RapidOCR found nothing
        # readable) or this image was skipped for another reason. Either
        # way, give it to Claude Vision now.
        return re_ocr_image_via_vision(data, filename)

    # Unknown / mangled extension (e.g. a MIME-encoded ".pdf?=" filename, a
    # ".vcf" vCard, or a misnamed blob). Sniff the magic bytes and route by
    # actual content type — this recovers real PDFs/images/text hiding behind
    # a bad extension. Genuine non-text blobs (.emz/.dwg/.mso) still come back
    # skipped, which is correct.
    sniffed = extract_no_ext_via_sniff(data, filename)
    if sniffed.method != "skipped":
        return sniffed
    return ExtractionResult(
        text="", method="skipped",
        skipped_reason=f"rescue_unsupported_ext:{ext or 'none'}",
    )


# =========================================================================
# Helpers
# =========================================================================

def _decode_text_bytes(data: bytes) -> str:
    for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")
