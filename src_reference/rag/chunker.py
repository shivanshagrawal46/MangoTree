"""
Structural, metadata-aware chunker for emails and attachments.

Why this design (not naive recursive char splitter):

  1. **Legal context preservation**: paragraph boundaries are respected
     first, sentence boundaries second. We never cut in the middle of a
     sentence unless an individual sentence already exceeds the token
     budget.

  2. **Citation fidelity**: every chunk carries a structured header
     (sender, date, subject, page #) embedded in the chunk text itself.
     This boosts retrieval recall ("show me Boris's emails about Fort
     Hill in March 2024") and gives Claude a stable string to cite.

  3. **Overlap**: 100-token sliding overlap between consecutive chunks of
     the same source so that information spanning a chunk boundary is
     never lost.

  4. **Page-anchored**: PDF chunks remember their page span so we can
     surface "p. 4 of contract.pdf" in citations.

Outputs are plain dicts ready to be inserted as `email_chunks` documents
(after `embedding` is added downstream by the embedder).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from src.rag.tokens import count_tokens, encode, decode


# --------------------------------------------------------------------------
# Splitters
# --------------------------------------------------------------------------

_PARAGRAPH_RE = re.compile(r"\n{2,}")
# A reasonably aggressive sentence splitter that preserves the punctuation
# attached to the previous sentence.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\(\[\"'])")


def _split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in _PARAGRAPH_RE.split(text) if p.strip()]


def _split_sentences(paragraph: str) -> List[str]:
    parts = _SENTENCE_RE.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def _hard_split(text: str, max_tokens: int) -> List[str]:
    """Last-resort token-window split for monstrously long single sentences."""
    toks = encode(text)
    out: List[str] = []
    for i in range(0, len(toks), max_tokens):
        out.append(decode(toks[i : i + max_tokens]).strip())
    return [c for c in out if c]


# --------------------------------------------------------------------------
# Chunk model
# --------------------------------------------------------------------------

@dataclass
class Chunk:
    text: str                       # text WITH header prefix
    body: str                       # text WITHOUT header prefix (for highlighting)
    n_tokens: int
    chunk_index: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Email-body chunking
# --------------------------------------------------------------------------

def _format_email_header(meta: dict) -> str:
    """Build a one-line metadata header to prepend to every email chunk."""
    parts: List[str] = []
    date = meta.get("date")
    if date is not None:
        try:
            parts.append(date.strftime("%Y-%m-%d %H:%M"))
        except AttributeError:
            parts.append(str(date))

    frm = (meta.get("from") or {}).get("email") or ""
    if frm:
        parts.append(f"from {frm}")

    to_emails = [(t or {}).get("email") for t in (meta.get("to") or []) if t]
    to_emails = [e for e in to_emails if e]
    if to_emails:
        parts.append(f"to {', '.join(to_emails[:3])}{'…' if len(to_emails) > 3 else ''}")

    subject = (meta.get("subject") or "").strip()
    if subject:
        parts.append(f"subject: {subject}")

    return f"[Email — {' | '.join(parts)}]"


def chunk_email_body(
    body_text: str,
    *,
    email_meta: dict,
    chunk_size_tokens: int = 500,
    chunk_overlap_tokens: int = 100,
) -> List[Chunk]:
    """Split a cleaned email body into metadata-anchored chunks."""
    if not body_text or not body_text.strip():
        return []

    header = _format_email_header(email_meta)
    header_tokens = count_tokens(header) + 2  # +2 for the joining \n\n

    # Reserve room for the header inside each chunk.
    body_budget = max(64, chunk_size_tokens - header_tokens)

    raw_chunks = _chunk_text(
        body_text,
        max_tokens=body_budget,
        overlap_tokens=chunk_overlap_tokens,
    )

    out: List[Chunk] = []
    for i, body in enumerate(raw_chunks):
        full = f"{header}\n\n{body}"
        out.append(
            Chunk(
                text=full,
                body=body,
                n_tokens=count_tokens(full),
                chunk_index=i,
            )
        )
    return out


# --------------------------------------------------------------------------
# Attachment chunking (page-aware)
# --------------------------------------------------------------------------

def _format_attachment_header(att_meta: dict, page_start: int, page_end: int) -> str:
    parts: List[str] = []
    fname = att_meta.get("filename") or "attachment"
    parts.append(fname)

    if att_meta.get("date"):
        try:
            parts.append(att_meta["date"].strftime("%Y-%m-%d"))
        except AttributeError:
            parts.append(str(att_meta["date"]))

    parent_subject = att_meta.get("email_subject")
    if parent_subject:
        parts.append(f"parent email: {parent_subject}")

    page_label = (
        f"p. {page_start}"
        if page_start == page_end
        else f"pp. {page_start}-{page_end}"
    )
    return f"[Attachment — {' | '.join(parts)} | {page_label}]"


def chunk_attachment(
    pages: List[dict],   # [{"page_no": int, "text": str}, ...] OR a single page-less doc
    *,
    attachment_meta: dict,
    chunk_size_tokens: int = 500,
    chunk_overlap_tokens: int = 100,
) -> List[Chunk]:
    """
    Chunk a multi-page attachment. We keep page boundaries soft: small
    consecutive pages are merged until the budget fills, large pages are
    split internally with overlap.
    """
    if not pages:
        return []

    out: List[Chunk] = []
    chunk_index = 0

    buffer: List[tuple[int, str]] = []  # [(page_no, text), ...]
    buffer_tokens = 0

    def flush():
        nonlocal chunk_index, buffer, buffer_tokens
        if not buffer:
            return
        page_start = buffer[0][0]
        page_end = buffer[-1][0]
        body = "\n\n".join(t for _, t in buffer if t)
        header = _format_attachment_header(attachment_meta, page_start, page_end)
        full = f"{header}\n\n{body}"
        out.append(
            Chunk(
                text=full,
                body=body,
                n_tokens=count_tokens(full),
                chunk_index=chunk_index,
                page_start=page_start,
                page_end=page_end,
            )
        )
        chunk_index += 1
        buffer = []
        buffer_tokens = 0

    for page in pages:
        page_no = int(page.get("page_no") or len(out) + 1)
        text = (page.get("text") or "").strip()
        if not text:
            continue

        page_tokens = count_tokens(text)
        header_overhead = 30  # rough header length

        # Big page → split internally; flush whatever was buffered first.
        if page_tokens > (chunk_size_tokens - header_overhead):
            if buffer:
                flush()
            sub_chunks = _chunk_text(
                text,
                max_tokens=chunk_size_tokens - header_overhead,
                overlap_tokens=chunk_overlap_tokens,
            )
            for sub in sub_chunks:
                header = _format_attachment_header(attachment_meta, page_no, page_no)
                full = f"{header}\n\n{sub}"
                out.append(
                    Chunk(
                        text=full,
                        body=sub,
                        n_tokens=count_tokens(full),
                        chunk_index=chunk_index,
                        page_start=page_no,
                        page_end=page_no,
                    )
                )
                chunk_index += 1
            continue

        # Small page → accumulate; flush when budget would overflow.
        if buffer_tokens + page_tokens > (chunk_size_tokens - header_overhead):
            flush()
        buffer.append((page_no, text))
        buffer_tokens += page_tokens

    flush()
    return out


# --------------------------------------------------------------------------
# Core paragraph/sentence/window splitter (used by both flows)
# --------------------------------------------------------------------------

def _chunk_text(text: str, *, max_tokens: int, overlap_tokens: int) -> List[str]:
    """
    Greedy paragraph-then-sentence packer with a token-window overlap.

    1. Try to fit whole paragraphs into a chunk.
    2. If a paragraph is too big, fall back to sentences.
    3. If a sentence is too big, fall back to a hard token split.
    4. Once a chunk is emitted, prepend the last `overlap_tokens` tokens
       of it to the next chunk (continuity).
    """
    if not text or not text.strip():
        return []

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    units: List[str] = []  # atomic units that always fit
    for para in paragraphs:
        if count_tokens(para) <= max_tokens:
            units.append(para)
            continue

        sentences = _split_sentences(para)
        if not sentences:
            sentences = [para]
        for sent in sentences:
            if count_tokens(sent) <= max_tokens:
                units.append(sent)
            else:
                units.extend(_hard_split(sent, max_tokens))

    chunks: List[str] = []
    cur: List[str] = []
    cur_tokens = 0

    for unit in units:
        u_tokens = count_tokens(unit)
        if cur_tokens + u_tokens > max_tokens and cur:
            chunks.append("\n\n".join(cur))
            # Build overlap from the tail tokens of the chunk we just emitted.
            tail = encode("\n\n".join(cur))[-overlap_tokens:] if overlap_tokens > 0 else []
            cur = [decode(tail).strip()] if tail else []
            cur_tokens = count_tokens("\n\n".join(cur)) if cur else 0

        cur.append(unit)
        cur_tokens += u_tokens

    if cur:
        chunks.append("\n\n".join(cur))

    return [c.strip() for c in chunks if c.strip()]
