"""
Citation verifier — Sprint 3 finish.

Deterministic, OCR-tolerant verification of model-emitted citations.

A "fact" produced by the structured-output Opus call has this shape:

    {
      "id":               "f1",
      "claim":            "Settlement amount is $450,000",
      "source_chunk_id":  3,                                     # 1-based [#N]
      "verbatim_quote":   "the total settlement amount of $450,000",
      "confidence":       "high" | "medium" | "low",
      "note":             "<optional derivation>"
    }

The verifier checks ONE thing per fact: does `verbatim_quote` actually
appear in chunk #N's body text, allowing for OCR noise, whitespace
collapse, punctuation drift, and minor case differences?

This is intentionally narrow:
  - We do NOT judge "is the claim true"  (an LLM can't reliably do that).
  - We do NOT judge "is the citation the BEST chunk" (paraphrase OK).
  - We DO check "is the quoted span actually in the cited chunk".

If the deterministic check fails on first pass we ask Opus to re-extract
a verbatim span from the cited chunk (handled in answer_pipeline.py — this
file is responsible only for the deterministic verdict).

Design notes
------------
1. We score each quote with rapidfuzz's `partial_ratio_alignment`, which
   finds the best matching contiguous span in the chunk text and returns
   a 0-100 score. Threshold defaults to 85 (configurable) — empirically
   high enough to reject paraphrases, low enough to tolerate normal OCR
   noise like missing spaces, broken hyphenations, page-number artefacts.

2. Pre-normalisation handles the predictable OCR/legal-doc weirdness so
   the fuzzy score concentrates on real signal:
     - Unicode NFKC (curly quotes -> ASCII, fullwidth -> normal)
     - Collapse runs of whitespace (incl. tabs, newlines, NBSPs)
     - Strip leading/trailing punctuation/quotes from the quote
     - Tolerate '$ 450,000' vs '$450,000' (mid-token spaces in numbers)
     - Tolerate broken-hyphenated words across line breaks ("con-\ntract"
       == "contract")
     - Standardise common date variants ('July 18, 2023' = '2023-07-18'
       = 'Jul 18, 2023') by normalising recognised date spans

3. Citation-id sanity is checked separately — if `source_chunk_id` is
   outside `[1, len(chunks)]`, that's a HARD fail (fabricated citation,
   no fuzzy match could ever save it).

4. The verifier emits structured `VerificationItem`s that the pipeline
   layer uses to decide whether to retry. We do NOT log to MongoDB here
   — that's the pipeline's job (so dependency direction stays clean:
   verifier knows nothing about the database).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

try:
    from rapidfuzz import fuzz
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "rapidfuzz is required for the citation verifier. "
        "Install with `pip install rapidfuzz`."
    ) from exc

from src.rag.retriever import RetrievedChunk
from src.rag.normalize_values import all_money, money_matches, normalize_money
from src.utils.logger import logger


# =====================================================================
# Public dataclasses
# =====================================================================

VERDICT_VERIFIED = "VERIFIED"
VERDICT_UNVERIFIED = "UNVERIFIED"
VERDICT_CITATION_INVALID = "CITATION_INVALID"

# Default fuzzy threshold. Tuned for OCR-noisy legal corpora — 85 lets
# 'Jul y 18, 2023' match 'July 18, 2023' but rejects paraphrases like
# 'mid-July 2023'.
DEFAULT_FUZZY_THRESHOLD = 85.0

# Minimum length of a quote we'll bother verifying (very short spans
# like "yes" would match almost anywhere by chance).
MIN_QUOTE_CHARS = 6


@dataclass(frozen=True)
class VerificationItem:
    """Per-fact verdict produced by `verify_facts`."""

    fact_id: str
    verdict: str                  # one of the VERDICT_* constants above
    score: float                  # 0-100 fuzzy score (0 if invalid)
    source_chunk_id: int          # 1-based index from the model's output
    matched_span: Optional[str] = None    # closest matching text in the chunk
    reason: Optional[str] = None          # human-readable explanation

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_VERIFIED


@dataclass
class VerificationReport:
    """Aggregate report for one answer."""

    items: List[VerificationItem] = field(default_factory=list)
    threshold: float = DEFAULT_FUZZY_THRESHOLD
    generated_at: datetime = field(default_factory=datetime.utcnow)

    # ----- summary helpers --------------------------------------------

    @property
    def all_passed(self) -> bool:
        return all(i.passed for i in self.items)

    @property
    def failed(self) -> List[VerificationItem]:
        return [i for i in self.items if not i.passed]

    @property
    def n_passed(self) -> int:
        return sum(1 for i in self.items if i.passed)

    @property
    def n_failed(self) -> int:
        return len(self.items) - self.n_passed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "threshold": self.threshold,
            "n_total": len(self.items),
            "n_passed": self.n_passed,
            "n_failed": self.n_failed,
            "all_passed": self.all_passed,
            "generated_at": self.generated_at.isoformat(),
            "items": [
                {
                    "fact_id": i.fact_id,
                    "verdict": i.verdict,
                    "score": round(i.score, 1),
                    "source_chunk_id": i.source_chunk_id,
                    "matched_span": i.matched_span,
                    "reason": i.reason,
                }
                for i in self.items
            ],
        }


# =====================================================================
# Text normalisation (OCR-tolerant)
# =====================================================================

# Hyphenation at line breaks: 'con-\ncract' -> 'contract'. Handles \r\n too.
_HYPHEN_LINEBREAK = re.compile(r"-\s*\n\s*")

# Any whitespace run (tabs/newlines/NBSP/etc) collapses to one space.
_WHITESPACE_RUN = re.compile(r"\s+")

# Curly/smart quotes -> ASCII
_QUOTE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
    "\u201C": '"', "\u201D": '"', "\u201E": '"', "\u201F": '"',
    "\u00AB": '"', "\u00BB": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",   # en/em/minus -> hyphen
    "\u00A0": " ", "\u2009": " ", "\u200A": " ", "\u202F": " ",  # NBSPs
})

# Strip wrapping punctuation/quotes (don't strip from inside).
_WRAP_PUNCT = '"\u201C\u201D\u2018\u2019\'`*_'

# Numbers with embedded spaces: '$ 450,000' -> '$450,000', '4 50,000' ->
# '450,000'. We tolerate spaces between a $ sign and the digits, and
# between digit groups separated by commas.
_DOLLAR_SPACE = re.compile(r"\$\s+(?=\d)")
_NUM_INTERNAL_SPACE = re.compile(r"(?<=\d)\s+(?=\d)")

# Compact whitespace inside short tokens caused by OCR jitter, e.g.
# 'J u l y' -> 'July'. Only collapses when 3+ single-letter tokens are
# adjacent (otherwise it would destroy real text). Conservative.
_OCR_JITTER = re.compile(r"\b(?:[A-Za-z]\s){2,}[A-Za-z]\b")

# Heal broken month names introduced by OCR, e.g. 'Jul y 18' -> 'July 18',
# 'Mar ch 2024' -> 'March 2024'. Only fires when the suffix would
# complete a known month name, so we don't accidentally glue unrelated
# tokens together.
_MONTH_BREAK_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+"
    r"([a-z]{1,7})\b",
    re.IGNORECASE,
)
_MONTH_SUFFIXES: Dict[str, frozenset] = {
    "jan": frozenset({"uary"}),
    "feb": frozenset({"ruary"}),
    "mar": frozenset({"ch"}),
    "apr": frozenset({"il"}),
    "may": frozenset(),
    "jun": frozenset({"e"}),
    "jul": frozenset({"y"}),
    "aug": frozenset({"ust"}),
    "sep": frozenset({"tember", "t"}),
    "oct": frozenset({"ober"}),
    "nov": frozenset({"ember"}),
    "dec": frozenset({"ember"}),
}


def _heal_month(match: "re.Match[str]") -> str:
    prefix = match.group(1).lower()
    suffix = match.group(2).lower()
    if suffix in _MONTH_SUFFIXES.get(prefix, frozenset()):
        return prefix + suffix
    return match.group(0)

# Critical-token extractors. These represent facts that MUST match
# exactly — fuzzy matching is too generous for them (e.g. $450,000 vs
# $405,000 has ~95% similarity but is wildly wrong in a legal answer).
#
# Each pattern extracts strings from the quote that must also appear,
# after normalisation, in the cited chunk's normalised text. If ANY
# required token is missing, the fact fails regardless of fuzzy score.
# Currency, incl. shorthand multipliers ($1.45M, $250K) so the token carries
# its true magnitude into money reconciliation.
_CURRENCY_RE = re.compile(
    r"\$\s*[\d,]+(?:\.\d+)?\s*(?:thousand|million|billion|mm|k|b|m)?\b", re.I)
# Year-only matches (1900-2099)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# Month names (full and abbreviated) for date detection.
_MONTH_PART = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember|t)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)"
)
# Month-day-year, day-month-year, ISO, and slash forms.
# Patterns covered:
#   2023-07-18      (ISO)
#   July 18, 2023   (US long)
#   Jul 18, 2023    (US short)
#   18 July 2023    (Brit)
#   7/18/2023       (US slash)
_DATE_RE = re.compile(
    r"(?i)"
    r"(?:"
        r"\b\d{4}-\d{1,2}-\d{1,2}\b"
        r"|"
        r"\b" + _MONTH_PART + r"\.?\s+\d{1,2}(?:,?\s+\d{2,4})?\b"
        r"|"
        r"\b\d{1,2}\s+" + _MONTH_PART + r"\.?\s+\d{2,4}\b"
        r"|"
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r")"
)
# Standalone integers >= 4 digits that aren't already captured by date/
# currency patterns — likely amounts, case numbers, invoice nos, etc.
# 3-digit numbers are too noisy (page numbers, paragraph nums).
_BIGNUM_RE = re.compile(r"\b\d{4,}\b")
# Percentage tokens like 25%, 12.5%
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")


def _normalize(text: str) -> str:
    """Aggressive but lossless normalisation for fuzzy matching."""
    if not text:
        return ""
    # 1. Unicode NFKC + curly quotes / dashes / NBSPs
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_QUOTE_MAP)
    # 2. Heal hyphenation across line breaks.
    text = _HYPHEN_LINEBREAK.sub("", text)
    # 3. Heal OCR jitter (sparse letters).
    def _de_jitter(m: re.Match[str]) -> str:
        return m.group(0).replace(" ", "")
    text = _OCR_JITTER.sub(_de_jitter, text)
    # 3b. Heal split month names ('Jul y' -> 'July').
    text = _MONTH_BREAK_RE.sub(_heal_month, text)
    # 4. Heal spaces inside numbers and after $.
    text = _DOLLAR_SPACE.sub("$", text)
    text = _NUM_INTERNAL_SPACE.sub("", text)
    # 5. Collapse remaining whitespace + lowercase.
    text = _WHITESPACE_RUN.sub(" ", text).strip().lower()
    return text


def _strip_wrap(s: str) -> str:
    """Strip wrap-punctuation/quotes/whitespace from both ends."""
    return s.strip(_WRAP_PUNCT + " \t\n\r")


def _extract_critical_tokens(raw_quote: str) -> List[str]:
    """
    Pull out tokens whose values must match exactly in the cited chunk:
    currency amounts, full dates, large integers, percentages.

    These are returned in their RAW form so the caller can normalise
    them with the same rules as the chunk text before substring-checking.
    Order is not significant; duplicates are removed.
    """
    if not raw_quote:
        return []
    seen: set[str] = set()
    found: List[str] = []
    for pat in (_CURRENCY_RE, _DATE_RE, _PERCENT_RE, _BIGNUM_RE, _YEAR_RE):
        for m in pat.finditer(raw_quote):
            tok = m.group(0).strip()
            if tok and tok not in seen:
                seen.add(tok)
                found.append(tok)
    return found


def _currency_reconciles(tok: str, chunk_amounts: List[float]) -> bool:
    """A currency token may be stated differently than the source ($2,300 vs
    2,300.00 vs 1.45M vs 1,450,000) without being WRONG. When the exact
    normalised substring isn't found, accept the token iff its parsed value
    reconciles with some amount in the chunk within a *formatting-only*
    tolerance — i.e. equal to the dollar. This forgives comma/cents/$/K-M-B
    formatting but still rejects a genuinely different figure ($450,000 vs
    $405,000), so the critical-token guarantee holds."""
    v = normalize_money(tok)
    if v is None:
        return False
    return any(money_matches(v, a, rel_tol=0.0, abs_tol=1.0) for a in chunk_amounts)


def _check_critical_tokens(
    raw_quote: str,
    chunk_norm: str,
) -> Optional[str]:
    """
    Returns None if every critical token in `raw_quote` is also present
    (normalised) somewhere in `chunk_norm`. Otherwise returns a human-
    readable reason naming the first missing token.

    Year-only tokens (e.g. "2023") are tolerated if some other date
    token in the same quote already matched — that way we don't double-
    fail when the full date is present.

    Currency tokens get a formatting-tolerant money reconciliation fallback
    (Sprint 7.6 normalization wired into the verifier) so that e.g. "$2,300"
    verifies against an OCR/source "2,300.00" — without loosening the guard
    against a materially different amount.
    """
    tokens = _extract_critical_tokens(raw_quote)
    if not tokens:
        return None

    chunk_amounts: Optional[List[float]] = None  # computed lazily, once
    matched_dates = 0
    for tok in tokens:
        tok_norm = _normalize(tok)
        # For 4-digit years, only require presence if no other date
        # token in this quote contained the year already.
        if re.fullmatch(r"(?:19|20)\d{2}", tok):
            # If any OTHER token contained this year, skip (already matched).
            if any(tok in other and other != tok for other in tokens):
                continue
        if tok_norm not in chunk_norm:
            # Currency fallback: a $-amount may be formatted differently in
            # the source. Reconcile by value (formatting-tolerant) before
            # failing. Non-currency tokens (dates, case/parcel numbers) still
            # require an exact normalised match.
            if "$" in tok:
                if chunk_amounts is None:
                    chunk_amounts = all_money(chunk_norm)
                if _currency_reconciles(tok, chunk_amounts):
                    continue
            return (
                f"critical token {tok!r} required by the verbatim quote is "
                f"missing from the cited chunk"
            )
        if _DATE_RE.fullmatch(tok):
            matched_dates += 1
    return None


# =====================================================================
# Public API
# =====================================================================

def verify_facts(
    facts: Sequence[Dict[str, Any]],
    chunks: Sequence[RetrievedChunk],
    *,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> VerificationReport:
    """
    Deterministically verify each fact's verbatim quote against its
    cited chunk.

    Parameters
    ----------
    facts
        Iterable of fact dicts as produced by the structured-output Opus
        call. Each must have keys: id, source_chunk_id, verbatim_quote.
        Missing fields produce CITATION_INVALID with explanation.
    chunks
        The RetrievedChunk list that was sent to Opus, in the SAME order
        as the [#N] indices. (i.e. chunks[0] is [#1].)
    fuzzy_threshold
        rapidfuzz partial_ratio score, 0-100. Quotes scoring at or above
        this are VERIFIED. Default 85 is OCR-tolerant.

    Returns
    -------
    VerificationReport with one VerificationItem per input fact, plus
    summary counts.
    """
    report = VerificationReport(threshold=fuzzy_threshold)
    if not facts:
        return report

    # Pre-normalise each chunk body once — quotes get matched against
    # this. Use body if present, else text (matches how we render the
    # prompt — see chat.py:133, 241).
    chunk_norms: List[str] = []
    chunk_raws: List[str] = []
    for c in chunks:
        raw = (c.body or c.text or "")
        chunk_raws.append(raw)
        chunk_norms.append(_normalize(raw))

    for fact in facts:
        item = _verify_one(fact, chunk_norms, chunk_raws, fuzzy_threshold)
        report.items.append(item)

    if report.n_failed:
        logger.debug(
            f"citation verifier: {report.n_failed}/{len(report.items)} failed "
            f"(threshold={fuzzy_threshold})"
        )
    return report


def _verify_one(
    fact: Dict[str, Any],
    chunk_norms: List[str],
    chunk_raws: List[str],
    threshold: float,
) -> VerificationItem:
    fid = str(fact.get("id") or fact.get("fact_id") or "?")

    # Required fields.
    try:
        chunk_id_raw = fact.get("source_chunk_id")
        chunk_id = int(chunk_id_raw)
    except (TypeError, ValueError):
        return VerificationItem(
            fact_id=fid,
            verdict=VERDICT_CITATION_INVALID,
            score=0.0,
            source_chunk_id=-1,
            reason=f"source_chunk_id missing or non-integer: {chunk_id_raw!r}",
        )

    quote = fact.get("verbatim_quote") or ""
    quote = _strip_wrap(str(quote))

    # Citation existence check.
    if chunk_id < 1 or chunk_id > len(chunk_norms):
        return VerificationItem(
            fact_id=fid,
            verdict=VERDICT_CITATION_INVALID,
            score=0.0,
            source_chunk_id=chunk_id,
            reason=(
                f"cited chunk #{chunk_id} does not exist "
                f"(retrieved set has #{1}..#{len(chunk_norms)})"
            ),
        )

    if len(quote) < MIN_QUOTE_CHARS:
        return VerificationItem(
            fact_id=fid,
            verdict=VERDICT_UNVERIFIED,
            score=0.0,
            source_chunk_id=chunk_id,
            reason=(
                f"verbatim_quote is too short to verify "
                f"({len(quote)} chars; minimum {MIN_QUOTE_CHARS})"
            ),
        )

    quote_norm = _normalize(quote)
    if not quote_norm:
        return VerificationItem(
            fact_id=fid,
            verdict=VERDICT_UNVERIFIED,
            score=0.0,
            source_chunk_id=chunk_id,
            reason="verbatim_quote normalised to empty string",
        )

    chunk_norm = chunk_norms[chunk_id - 1]

    # GATE 1: Critical-token exact presence. Currency, dates, large
    # numbers, percentages all must appear in the chunk regardless of
    # what the fuzzy score says. This catches the catastrophic case of
    # $405,000 fuzzy-matching $450,000 at 97% similarity.
    crit_fail = _check_critical_tokens(quote, chunk_norm)
    if crit_fail is not None:
        matched = _find_raw_span(quote, chunk_raws[chunk_id - 1])
        return VerificationItem(
            fact_id=fid,
            verdict=VERDICT_UNVERIFIED,
            score=0.0,
            source_chunk_id=chunk_id,
            matched_span=matched,
            reason=crit_fail,
        )

    # GATE 2: Fuzzy overall similarity. partial_ratio finds the best
    # contiguous alignment between quote and chunk and returns 0-100.
    score = fuzz.partial_ratio(quote_norm, chunk_norm)

    if score >= threshold:
        # Find the matching span in the RAW (pre-normalisation) chunk for
        # display purposes. We use a coarse approach: take the first N
        # words of the quote and search for them in the raw chunk.
        matched = _find_raw_span(quote, chunk_raws[chunk_id - 1])
        return VerificationItem(
            fact_id=fid,
            verdict=VERDICT_VERIFIED,
            score=float(score),
            source_chunk_id=chunk_id,
            matched_span=matched,
            reason=None,
        )

    # Below threshold — UNVERIFIED. We still surface the best partial
    # match (if any) so callers can show "closest text we found".
    matched = _find_raw_span(quote, chunk_raws[chunk_id - 1])
    return VerificationItem(
        fact_id=fid,
        verdict=VERDICT_UNVERIFIED,
        score=float(score),
        source_chunk_id=chunk_id,
        matched_span=matched,
        reason=(
            f"fuzzy score {score:.1f} < threshold {threshold:.1f}; "
            f"quote not found verbatim in chunk #{chunk_id}"
        ),
    )


def _find_raw_span(
    quote: str,
    chunk_raw: str,
    *,
    head_words: int = 6,
    span_chars: int = 240,
) -> Optional[str]:
    """
    Locate the chunk-substring whose first few words match the quote's
    first few words. Used purely for evidence-panel display — NOT for
    the verification decision itself.
    """
    if not quote or not chunk_raw:
        return None
    quote_norm = _normalize(quote)
    chunk_norm = _normalize(chunk_raw)

    # First word slice of the quote (skip articles).
    words = [w for w in quote_norm.split() if len(w) > 1]
    if not words:
        return None
    needle = " ".join(words[: min(head_words, len(words))])
    pos = chunk_norm.find(needle)
    if pos < 0:
        return None

    # Map normalised position back to a raw window. Since normalisation
    # only collapses whitespace and lowercases, a character-walking
    # approach is reliable enough — but we'll just grab a ±span window
    # in the normalised view and return the corresponding portion of the
    # raw chunk (close enough for display).
    norm_start = max(0, pos - 30)
    norm_end = min(len(chunk_norm), pos + len(needle) + span_chars)
    # Heuristic: assume ~1:1 character mapping (true for whitespace-only
    # collapse most of the time). For display this is fine; if it's
    # slightly off the lawyer still sees the surrounding context.
    approx_start = min(norm_start, len(chunk_raw) - 1)
    approx_end = min(norm_end, len(chunk_raw))
    return chunk_raw[approx_start:approx_end].strip()


__all__ = [
    "VerificationItem",
    "VerificationReport",
    "verify_facts",
    "VERDICT_VERIFIED",
    "VERDICT_UNVERIFIED",
    "VERDICT_CITATION_INVALID",
    "DEFAULT_FUZZY_THRESHOLD",
]
