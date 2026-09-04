"""Chunking — packs property-attributed segments into retrievable units.

Every chunk carries the property tags of **its own segments**, never the parent
document's. That is the whole point: a chunk built only from Decatur sentences is
tagged Decatur alone, even when the email it came from also discussed Varnum.
Retrieval then filters by property at the chunk level and the Varnum text is not
in the candidate pool at all.

Packing rule
------------
Segments are packed up to 1000 tokens, and a segment is **only** added to a chunk
whose property set it matches. Mixing two properties into one chunk to fill a
budget would undo the segmentation entirely — so a chunk is closed early rather
than made impure. Chunks are cheap; contaminated evidence is not.

This is why chunks average well under the 1000-token budget: property purity wins
every time it conflicts with packing density.

Overlap
-------
Neighbouring chunks share a 200-token trailing window so a fact split across a
boundary is still retrievable whole. Overlap is only carried between chunks of
the *same* property set, for the same reason — carrying Varnum text into a
Decatur chunk is precisely the contamination the segmenter exists to prevent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from mangotree.chunk.segmenter import Segment, segment_text
from mangotree.chunk.tokens import count_tokens, split_by_tokens, tail_tokens
from mangotree.core.hashing import sha256_text

#: Admin-specified chunk budget, in tokens.
TARGET_TOKENS = 1000
#: Hard ceiling. A segment above this is split on sentence boundaries.
MAX_TOKENS = 1200
#: Trailing tokens repeated into the next chunk of the same property set.
OVERLAP_TOKENS = 200
#: Chunks smaller than this are folded into their neighbour rather than standing
#: alone, since a 20-token fragment embeds to noise.
MIN_CHUNK_TOKENS = 24


@dataclass
class Chunk:
    chunk_id: str
    artifact_sha: str
    ordinal: int
    text: str
    property_ids: List[str]
    attribution: str
    confidence: float
    segment_indices: List[int] = field(default_factory=list)
    source_ref: str = ""          # "page 4" / "Sheet1!A12" / "email body"
    context: str = ""             # contextual summary, prepended at embed time
    char_count: int = 0
    token_count: int = 0

    @property
    def embed_text(self) -> str:
        """What actually gets embedded — context first, then the verbatim chunk.

        The context line makes an isolated chunk interpretable ("this is from the
        1512 Varnum draw schedule"), which materially improves retrieval for
        chunks that use pronouns or bare figures.
        """
        return f"{self.context}\n\n{self.text}".strip() if self.context else self.text

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "artifact_sha": self.artifact_sha,
            "ordinal": self.ordinal,
            "text": self.text,
            "property_ids": self.property_ids,
            "attribution": self.attribution,
            "confidence": round(self.confidence, 3),
            "segment_indices": self.segment_indices,
            "source_ref": self.source_ref,
            "context": self.context,
            "char_count": self.char_count,
            "token_count": self.token_count,
        }


_PAGE_MARKER = re.compile(r"^\[page (\d+)\]", re.M)
_SHEET_MARKER = re.compile(r"^\[([^\]!]+)!([A-Z]+\d+)\]")


def _source_ref(text: str, fallback: str) -> str:
    page = _PAGE_MARKER.search(text)
    if page:
        return f"page {page.group(1)}"
    sheet = _SHEET_MARKER.search(text)
    if sheet:
        return f"{sheet.group(1)}!{sheet.group(2)}"
    return fallback


def _key(segment: Segment) -> Tuple[str, ...]:
    """Property identity of a segment. Chunks never span two different keys."""
    return tuple(sorted(segment.property_ids))


def pack_segments(
    segments: Sequence[Segment],
    *,
    artifact_sha: str,
    default_ref: str = "",
    include_ambiguous: bool = True,
) -> List[Chunk]:
    """Pack attributed segments into chunks, never mixing property sets."""
    chunks: List[Chunk] = []
    buffer: List[Segment] = []
    buffer_key: Optional[Tuple[str, ...]] = None
    carry_over = ""

    def flush() -> None:
        nonlocal buffer, buffer_key, carry_over
        if not buffer:
            return

        body = "\n\n".join(s.text for s in buffer).strip()
        if not body:
            buffer, buffer_key = [], None
            return

        text = f"{carry_over}\n\n{body}".strip() if carry_over else body
        property_ids = list(buffer_key or ())
        confidences = [s.confidence for s in buffer if s.confidence > 0]
        attributions = {s.attribution for s in buffer}
        attribution = (
            "explicit" if "explicit" in attributions
            else "carried" if "carried" in attributions
            else "document" if "document" in attributions
            else "ambiguous"
        )

        ordinal = len(chunks)
        chunks.append(Chunk(
            chunk_id=sha256_text(f"{artifact_sha}:{ordinal}:{text[:200]}")[:24],
            artifact_sha=artifact_sha,
            ordinal=ordinal,
            text=text,
            property_ids=property_ids,
            attribution=attribution,
            confidence=(sum(confidences) / len(confidences)) if confidences else 0.0,
            segment_indices=[s.index for s in buffer],
            source_ref=_source_ref(text, default_ref),
            char_count=len(text),
            token_count=count_tokens(text),
        ))

        carry_over = tail_tokens(body, OVERLAP_TOKENS)
        buffer, buffer_key = [], None

    for segment in segments:
        if segment.attribution == "ambiguous" and not include_ambiguous:
            continue

        key = _key(segment)

        # A different property set always starts a new chunk — filling a budget
        # is never a good enough reason to mix properties.
        if buffer_key is not None and key != buffer_key:
            flush()
            carry_over = ""

        segment_tokens = count_tokens(segment.text)

        # A single oversized segment is split on sentence boundaries.
        if segment_tokens > MAX_TOKENS:
            flush()
            carry_over = ""
            for piece in split_by_tokens(segment.text, TARGET_TOKENS):
                buffer = [Segment(segment.index, piece, list(segment.property_ids),
                                  segment.attribution, segment.confidence)]
                buffer_key = key
                flush()
            continue

        current = sum(count_tokens(s.text) for s in buffer)
        if buffer and current + segment_tokens > TARGET_TOKENS:
            flush()

        buffer.append(segment)
        buffer_key = key

    flush()

    # Fold a trailing scrap into its predecessor when they share a property set.
    if (len(chunks) >= 2 and chunks[-1].token_count < MIN_CHUNK_TOKENS
            and chunks[-1].property_ids == chunks[-2].property_ids):
        tail = chunks.pop()
        chunks[-1].text = f"{chunks[-1].text}\n\n{tail.text}"
        chunks[-1].char_count = len(chunks[-1].text)
        chunks[-1].token_count = count_tokens(chunks[-1].text)

    return chunks


def chunk_artifact(
    text: str,
    *,
    artifact_sha: str,
    property_ids: Sequence[str] = (),
    default_ref: str = "",
    include_ambiguous: bool = True,
) -> List[Chunk]:
    """Segment then pack — the entry point used by the indexing pipeline."""
    if not text or not text.strip():
        return []
    segments = segment_text(text, document_property_ids=list(property_ids))
    return pack_segments(
        segments,
        artifact_sha=artifact_sha,
        default_ref=default_ref,
        include_ambiguous=include_ambiguous,
    )


def chunk_stats(chunks: Sequence[Chunk]) -> dict:
    per_property: Dict[str, int] = {}
    for chunk in chunks:
        for pid in chunk.property_ids:
            per_property[pid] = per_property.get(pid, 0) + 1
    sizes = [c.token_count for c in chunks] or [0]
    return {
        "chunks": len(chunks),
        "mean_tokens": round(sum(sizes) / len(sizes)),
        "max_tokens": max(sizes),
        "over_budget": sum(1 for c in chunks if c.token_count > MAX_TOKENS),
        "unattributed": sum(1 for c in chunks if not c.property_ids),
        "per_property": per_property,
    }
