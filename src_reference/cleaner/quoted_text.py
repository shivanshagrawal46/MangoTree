"""
Quoted-thread recovery (Sprint 2 — recall).

Today the cleaner DROPS everything below the first reply marker
(`_truncate_at_reply_chain` in text_cleaner.py). That is correct for the
common case — the quoted history is just a copy of emails we already
have — but it silently discards the ONE case that matters forensically:
quoted text that exists NOWHERE ELSE in the corpus (a forwarded message
we were never a direct party to, or a pre-export thread).

This module implements the "three-bucket" rule WITHOUT re-embedding
anything. It only classifies; the caller decides what to persist:

    duplicate   -> we already have this content -> SKIP (no chunk)
    near_match  -> we have an ALMOST-identical original -> emit a
                   TAMPER-CANDIDATE finding (edited-before-forwarding is
                   evidence), do NOT index as a normal chunk
    novel       -> no matching original anywhere -> THIS is the text that
                   should become an `email_quoted` chunk later

Design constraints:
  * Pure logic. No DB, no network, no embeddings. Fully unit-testable.
  * Reuses text_cleaner's own cut patterns so the split point can never
    drift from the production cleaner.
  * The corpus lookup is dependency-injected (a set of known fingerprints
    + a candidate-text provider) so the matching policy is testable in
    isolation and the DB wiring lives at the call site.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Set, Tuple

try:
    from rapidfuzz import fuzz
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "rapidfuzz is required for quoted-text classification. "
        "Install with `pip install rapidfuzz`."
    ) from exc

# Reuse the EXACT cut patterns the production cleaner uses, so the head/
# tail boundary is identical to what clean_email_body() strips.
from src.cleaner.text_cleaner import (
    _FROM_LINE,
    _REPLY_HEADER_PATTERNS,
    _DIVIDER_PATTERNS,
    _SIG_DELIMITER,
)

# Buckets
BUCKET_DUPLICATE = "duplicate"
BUCKET_NEAR_MATCH = "near_match"
BUCKET_NOVEL = "novel"

# A quoted block shorter than this (after normalization) is not worth
# indexing or flagging — it's a stray line, a "> " artefact, or a name.
MIN_BLOCK_CHARS = 40

# Fuzzy ratio (0-100) at/above which a novel-looking block is treated as a
# near-duplicate of a known original (=> tamper candidate rather than novel).
# 90 is deliberately high: we only call something a near-match when it is
# clearly a modified copy of a specific original, not merely on-topic.
DEFAULT_NEAR_THRESHOLD = 90.0


@dataclass(frozen=True)
class QuoteVerdict:
    bucket: str                       # BUCKET_*
    fingerprint: str                  # sha256 of normalized block
    best_similarity: float = 0.0      # 0-100 vs best candidate original
    matched_fingerprint: Optional[str] = None  # original it matched/neared

    @property
    def should_index(self) -> bool:
        """Only NOVEL blocks become new email_quoted chunks."""
        return self.bucket == BUCKET_NOVEL

    @property
    def is_tamper_candidate(self) -> bool:
        return self.bucket == BUCKET_NEAR_MATCH


# ---------------------------------------------------------------------------
# Splitting: head (kept by cleaner today) vs quoted tail (dropped today)
# ---------------------------------------------------------------------------
def _earliest_cut(text: str) -> Optional[int]:
    cuts: List[int] = []
    m = _FROM_LINE.search(text)
    if m:
        cuts.append(m.start())
    for pat in _REPLY_HEADER_PATTERNS:
        m = pat.search(text)
        if m:
            cuts.append(m.start())
    for pat in _DIVIDER_PATTERNS:
        m = pat.search(text)
        if m:
            cuts.append(m.start())
    m = _SIG_DELIMITER.search(text)
    if m and m.start() > 0:
        cuts.append(m.start())
    return min(cuts) if cuts else None


def split_quoted_tail(text: str) -> Tuple[str, str]:
    """Return (head, tail). `head` is what the cleaner keeps today; `tail`
    is the quoted history it discards. `tail` is "" when there is none."""
    if not text:
        return "", ""
    cut = _earliest_cut(text)
    if cut is None:
        return text, ""
    return text[:cut], text[cut:]


# Subsequent per-message boundaries inside the tail, so each historical
# message can be bucketed on its own (a thread can mix duplicate + novel).
_SEGMENT_BOUNDARIES = [_FROM_LINE, *_REPLY_HEADER_PATTERNS, *_DIVIDER_PATTERNS]


def iter_quoted_segments(tail: str) -> List[str]:
    """Split a quoted tail into individual quoted-message segments on reply/
    forward boundaries. Leading '>' quote markers are stripped per line."""
    if not tail.strip():
        return []
    # Gather all boundary positions.
    positions: Set[int] = {0}
    for pat in _SEGMENT_BOUNDARIES:
        for m in pat.finditer(tail):
            positions.add(m.start())
    ordered = sorted(positions)
    segments: List[str] = []
    for i, start in enumerate(ordered):
        end = ordered[i + 1] if i + 1 < len(ordered) else len(tail)
        seg = tail[start:end]
        seg = _strip_quote_markers(seg)
        if len(normalize_quote(seg)) >= MIN_BLOCK_CHARS:
            segments.append(seg.strip())
    return segments


_LEADING_QUOTE = re.compile(r"^\s*>+\s?", re.MULTILINE)


def _strip_quote_markers(text: str) -> str:
    return _LEADING_QUOTE.sub("", text)


# ---------------------------------------------------------------------------
# Fingerprint / normalization for content-identity matching
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def normalize_quote(text: str) -> str:
    """Normalization for content identity: NFKC, drop reply/forward header
    lines (they carry timestamps that differ per copy), lowercase, collapse
    whitespace. Two copies of the SAME underlying message normalize equal;
    an edited copy does not."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    # Drop lines that are pure reply/forward headers so a re-quote of the
    # same body isn't classified as "edited" just because the wrapper
    # timestamp/attribution differs.
    kept: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _FROM_LINE.match(stripped) or any(p.match(stripped) for p in _REPLY_HEADER_PATTERNS):
            continue
        if stripped.lower().startswith(("from:", "sent:", "to:", "cc:", "subject:", "date:")):
            continue
        kept.append(stripped)
    joined = " ".join(kept)
    return _WS.sub(" ", joined).strip().lower()


def quote_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_quote(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The three-bucket classifier
# ---------------------------------------------------------------------------
def classify_block(
    block: str,
    *,
    known_fingerprints: Set[str],
    candidate_texts: Optional[Sequence[str]] = None,
    near_threshold: float = DEFAULT_NEAR_THRESHOLD,
) -> QuoteVerdict:
    """Classify a single quoted block into duplicate / near_match / novel.

    Parameters
    ----------
    block
        The quoted-message text (one segment).
    known_fingerprints
        Fingerprints of every original message already in the corpus
        (from `quote_fingerprint` over the originals' bodies). An exact
        fingerprint hit => duplicate.
    candidate_texts
        A SMALL set of plausible originals (e.g. same thread / subject /
        participants), used only when the fingerprint didn't match, to
        detect an edited copy. Kept tiny by the caller for cost; matching
        here is pure fuzzy ratio.
    near_threshold
        Fuzzy ratio at/above which we call it an edited copy (tamper
        candidate) rather than novel.
    """
    fp = quote_fingerprint(block)
    if fp in known_fingerprints:
        return QuoteVerdict(bucket=BUCKET_DUPLICATE, fingerprint=fp,
                            best_similarity=100.0, matched_fingerprint=fp)

    norm = normalize_quote(block)
    best = 0.0
    best_fp: Optional[str] = None
    for cand in candidate_texts or []:
        r = fuzz.ratio(norm, normalize_quote(cand))
        if r > best:
            best = r
            best_fp = quote_fingerprint(cand)

    if best >= near_threshold and best < 100.0:
        return QuoteVerdict(bucket=BUCKET_NEAR_MATCH, fingerprint=fp,
                            best_similarity=best, matched_fingerprint=best_fp)
    if best >= 100.0:
        # Normalized-equal to a candidate we weren't told the fingerprint of.
        return QuoteVerdict(bucket=BUCKET_DUPLICATE, fingerprint=fp,
                            best_similarity=100.0, matched_fingerprint=best_fp)
    return QuoteVerdict(bucket=BUCKET_NOVEL, fingerprint=fp,
                        best_similarity=best, matched_fingerprint=None)


def classify_email_tail(
    body_with_quotes: str,
    *,
    known_fingerprints: Set[str],
    candidate_provider: Optional[Callable[[str], Sequence[str]]] = None,
    near_threshold: float = DEFAULT_NEAR_THRESHOLD,
) -> List[QuoteVerdict]:
    """Convenience: split an email body's quoted tail into segments and
    classify each. `candidate_provider(segment)` returns plausible original
    texts for near-match detection (the DB lookup lives there)."""
    _, tail = split_quoted_tail(body_with_quotes)
    verdicts: List[QuoteVerdict] = []
    for seg in iter_quoted_segments(tail):
        cands = candidate_provider(seg) if candidate_provider else None
        verdicts.append(classify_block(
            seg, known_fingerprints=known_fingerprints,
            candidate_texts=cands, near_threshold=near_threshold,
        ))
    return verdicts


__all__ = [
    "QuoteVerdict",
    "BUCKET_DUPLICATE", "BUCKET_NEAR_MATCH", "BUCKET_NOVEL",
    "split_quoted_tail", "iter_quoted_segments",
    "normalize_quote", "quote_fingerprint",
    "classify_block", "classify_email_tail",
]
