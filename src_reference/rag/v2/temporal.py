"""
Temporal & Authority Re-scoring + Diversification.

Two complementary post-retrieval passes:

  1. RE-SCORING — adjusts each candidate's score based on:
       a. Recency       (newer docs slightly preferred)
       b. Authority     (court orders > stipulations > emails > drafts)
       c. Exact-match   (chunks containing the literal query keywords get
                         a meaningful boost — handles the "pure semantic
                         search misses '$450,000' verbatim" failure mode)

  2. DIVERSIFICATION — ensures the final top-K covers different time
     periods and different source documents instead of being dominated by
     a single hot cluster.

Both passes run on the FUSED candidate list (after RRF) and BEFORE the
external reranker. The reranker still gets the final say on relevance,
but our re-scoring guarantees that the most-recent and most-authoritative
versions of any fact survive into the rerank stage.

Design notes:
  • Pure functions over plain dicts — easy to test, no I/O.
  • Configurable weights via Settings; sensible defaults baked in.
  • All boosts are multiplicative on a [0.0, 1.0] base score so we never
    blow up to infinity. Final score = base * recency * authority * exact.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Authority hierarchy — derived from filename heuristics.
#
# We pattern-match the filename to bucket each chunk into a tier. Higher
# tiers carry more weight in the authority boost. Tiers are intentionally
# coarse — fine-grained classification can come later if needed.
# ---------------------------------------------------------------------------

# Order matters — patterns are tried top-to-bottom and the first match wins.
# Drafts MUST be checked first so a "Settlement Draft" gets demoted, not
# promoted by the "settlement" pattern.
_AUTHORITY_PATTERNS: List[tuple[re.Pattern, float]] = [
    # Tier 4 (FIRST): drafts / redlines — demoted regardless of doc type
    (re.compile(r"\b(draft|wip|markup|redline|tracked[\s\-_]?changes)\b", re.I), 0.90),
    # Tier 1: court-issued orders / opinions (highest authority)
    (re.compile(r"\b(order|opinion|ruling|judgment|decree|so[\s\-_]?ordered)\b", re.I), 1.20),
    # Tier 2: filed motions, stipulations, executed agreements
    (re.compile(r"\b(stipulat|settlement|motion|complaint|petition|9019)\b", re.I), 1.12),
    # Tier 3: signed contracts, executed deeds, escrow agreements
    (re.compile(r"\b(agreement|contract|escrow|deed|assignment|amendment)\b", re.I), 1.08),
]
# Default authority for emails / unclassified attachments.
_AUTHORITY_DEFAULT = 1.00


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ScoredChunk:
    """A candidate chunk with its computed final score and component breakdown."""

    doc: Dict[str, Any]
    base_score: float
    recency: float
    authority: float
    exact_match: float
    final_score: float
    # For debugging / observability — what cluster the chunk belongs to.
    cluster_key: str = ""

    def chunk_id(self) -> str:
        cid = self.doc.get("_id")
        return str(cid) if cid is not None else ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rescore(
    candidates: Sequence[Dict[str, Any]],
    *,
    base_scores: Dict[str, float],
    keyword_boost_terms: Sequence[str] = (),
    recency_half_life_days: float = 365.0,
    enable_recency: bool = True,
    enable_authority: bool = True,
    enable_exact_match: bool = True,
) -> List[ScoredChunk]:
    """
    Apply recency / authority / exact-match boosts to fused candidates.

    Args:
      candidates             — the chunks (output of HybridSearcher)
      base_scores            — chunk_id → RRF score (or any base relevance)
      keyword_boost_terms    — surface forms from QuerySignals.keyword_boost_terms
      recency_half_life_days — for recency decay; 365 means a doc 1 year old
                                gets ~0.5x recency boost relative to today's
                                doc. Tuneable. Capped to [0.5, 1.5] effective.

    Returns ScoredChunk list sorted by final_score desc.
    """
    if not candidates:
        return []

    now = datetime.now(timezone.utc)
    norm_keywords = [k.strip().lower() for k in keyword_boost_terms if k and k.strip()]

    out: List[ScoredChunk] = []
    for doc in candidates:
        cid = str(doc.get("_id") or "")
        base = base_scores.get(cid, 0.0)

        # Option B: prefer `latest_date` (max across all occurrences) over
        # the mirrored primary `date`. This means recency reflects the most
        # recent time we saw this content discussed, not the first time it
        # was created — which is the right behaviour for a chat assistant.
        date_for_recency = doc.get("latest_date") or doc.get("date")

        recency = (
            _recency_score(date_for_recency, now=now, half_life_days=recency_half_life_days)
            if enable_recency else 1.0
        )
        authority = _authority_score(doc) if enable_authority else 1.0
        exact = (
            _exact_match_score(doc, keywords=norm_keywords)
            if enable_exact_match else 1.0
        )

        final = base * recency * authority * exact

        out.append(
            ScoredChunk(
                doc=doc,
                base_score=base,
                recency=recency,
                authority=authority,
                exact_match=exact,
                final_score=final,
                cluster_key=_cluster_key(doc),
            )
        )

    out.sort(key=lambda s: s.final_score, reverse=True)
    return out


def diversify(
    scored: Sequence[ScoredChunk],
    *,
    max_per_cluster: int = 3,
    final_limit: int = 50,
) -> List[ScoredChunk]:
    """
    Cap the number of chunks from a single source document so a single hot
    document doesn't crowd out diverse evidence.

    Cluster key = parent email_id OR parent attachment_id (whichever the
    chunk has). Falls back to chunk_id (no clustering possible).

    Greedy: traverse `scored` in order; for each chunk, only keep it if its
    cluster has < max_per_cluster items already kept.
    """
    if not scored:
        return []

    counts: Dict[str, int] = {}
    keep: List[ScoredChunk] = []
    for s in scored:
        key = s.cluster_key or s.chunk_id()
        if counts.get(key, 0) >= max_per_cluster:
            continue
        keep.append(s)
        counts[key] = counts.get(key, 0) + 1
        if len(keep) >= final_limit:
            break
    return keep


def temporal_diversify(
    scored: Sequence[ScoredChunk],
    *,
    final_limit: int = 50,
    min_per_year: int = 1,
) -> List[ScoredChunk]:
    """
    Ensure the final list spans the time periods present in candidates,
    instead of being dominated by one year.

    Greedy: pull the top result from each year (in chronological order),
    cycling through years until `final_limit` is reached or all candidates
    consumed. Within a year, candidates remain sorted by score.

    This is critical for "compare" / "contradict" / amendment-tracking
    questions — guarantees that the most recent version of a fact lands
    in the prompt alongside earlier versions.
    """
    if not scored:
        return []

    by_year: Dict[int, List[ScoredChunk]] = {}
    no_date: List[ScoredChunk] = []
    for s in scored:
        # Option B: prefer latest_date (max across occurrences) for the
        # year bucket. Falls back to top-level date for legacy chunks.
        d = s.doc.get("latest_date") or s.doc.get("date")
        if isinstance(d, datetime):
            by_year.setdefault(d.year, []).append(s)
        else:
            no_date.append(s)

    # Within each year, keep score-descending order.
    for lst in by_year.values():
        lst.sort(key=lambda x: x.final_score, reverse=True)

    # Round-robin pull from each year. Years iterated newest → oldest so
    # recent material is preferred when ties happen.
    sorted_years = sorted(by_year.keys(), reverse=True)
    out: List[ScoredChunk] = []
    seen: set = set()

    # First pass: take `min_per_year` from each year if available.
    for year in sorted_years:
        for s in by_year[year][:min_per_year]:
            cid = s.chunk_id()
            if cid in seen:
                continue
            seen.add(cid)
            out.append(s)
            if len(out) >= final_limit:
                return out

    # Second pass: cycle through years pulling next-best until full.
    indices = {y: min_per_year for y in sorted_years}
    while len(out) < final_limit:
        progressed = False
        for year in sorted_years:
            i = indices[year]
            if i >= len(by_year[year]):
                continue
            s = by_year[year][i]
            indices[year] = i + 1
            cid = s.chunk_id()
            if cid in seen:
                continue
            seen.add(cid)
            out.append(s)
            progressed = True
            if len(out) >= final_limit:
                return out
        if not progressed:
            break

    # Append no-date chunks at the end.
    for s in no_date:
        if len(out) >= final_limit:
            break
        cid = s.chunk_id()
        if cid not in seen:
            out.append(s)
            seen.add(cid)

    return out


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------

def _recency_score(
    date_value: Any, *, now: datetime, half_life_days: float
) -> float:
    """
    Exponential decay clamped to [0.85, 1.20].

    A chunk dated `now` gets 1.20x. A chunk dated `2 * half_life_days` ago
    gets ~0.85x. Curve is multiplicative, not punitive.
    """
    if not isinstance(date_value, datetime):
        return 1.0
    dt = date_value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    # 2^(-age/half_life): newer → 1.0, older → 0.5 at one half-life.
    decay = math.pow(0.5, age_days / max(1.0, half_life_days))
    # Map [0,1] decay to [0.85, 1.20] multiplicative window.
    return 0.85 + 0.35 * decay


def _authority_score(doc: Dict[str, Any]) -> float:
    """Pattern-match filename / source_type to assign an authority multiplier.

    Option B: the same sha256 may have been attached under DIFFERENT
    filenames in different parent emails (e.g. "Settlement.pdf" in one
    thread and "Settlement Draft v2.pdf" in another). We scan EVERY name
    we've seen for this chunk and use the most authoritative match, so a
    finalised order isn't demoted just because some copy of it was labelled
    "draft" in one email's metadata.
    """
    # Document-type authority stamped at ingest (schema.authority_for):
    # title_report=1.15, closing_statement=1.15, deed/mortgage tiers, etc.
    # This is the source of truth for structured docs and was previously
    # ignored here, so title/recorded instruments got no boost in the hybrid
    # path (only in graph fan-out). Use it as an authority floor.
    try:
        dtype = float(doc.get("doc_authority_score") or 0.0)
    except (TypeError, ValueError):
        dtype = 0.0
    dtype = min(dtype, 1.30)  # clamp to the same ceiling as filename tiers

    candidate_names: List[str] = []
    fname = (doc.get("filename") or "").lower()
    if fname:
        candidate_names.append(fname)
    for occ in doc.get("occurrences") or []:
        occ_name = (occ.get("filename") or "").lower()
        if occ_name and occ_name != fname:
            candidate_names.append(occ_name)

    if not candidate_names:
        # Structured docs (title reports, deeds) carry doc_authority_score but
        # no email filename — honour their stamped authority. Plain emails fall
        # back to the neutral default.
        return max(_AUTHORITY_DEFAULT, dtype)

    # `_AUTHORITY_PATTERNS` is ordered draft-first then highest-tier first.
    # We want the HIGHEST multiplier across all candidate names, but if
    # the draft pattern matches ANY name we still want to demote — so we
    # explicitly check draft before others, then take the max of the
    # remaining matches.
    draft_pat, draft_mult = _AUTHORITY_PATTERNS[0]
    if all(draft_pat.search(n) for n in candidate_names):
        # Every occurrence is labelled as a draft → demote (overrides doc type).
        return draft_mult

    best = _AUTHORITY_DEFAULT
    for n in candidate_names:
        for pat, mult in _AUTHORITY_PATTERNS[1:]:  # skip draft pattern
            if pat.search(n):
                if mult > best:
                    best = mult
                break
    # Honour the stamped document-type authority as a floor.
    return max(best, dtype)


def _exact_match_score(
    doc: Dict[str, Any],
    *,
    keywords: Sequence[str],
) -> float:
    """
    Boost chunks whose body / filename contains any keyword from the query.

    Multiplier scales with hit count, capped at 1.5x. This is the single
    most impactful boost for fact-lookup queries (literal $ amounts, case
    numbers, document titles).
    """
    if not keywords:
        return 1.0
    text = " ".join([
        str(doc.get("body") or ""),
        str(doc.get("text") or ""),
        str(doc.get("filename") or ""),
        str(doc.get("subject") or ""),
    ]).lower()
    if not text:
        return 1.0
    hits = sum(1 for kw in keywords if kw and kw in text)
    if hits == 0:
        return 1.0
    # +15% per hit, capped.
    return min(1.5, 1.0 + 0.15 * hits)


def _cluster_key(doc: Dict[str, Any]) -> str:
    """
    Identify the parent source so multiple chunks of the same document are
    grouped into one cluster.

    Option B: chunks are keyed by `sha256` (one unique file, one cluster).
    For email bodies the sha256 is `"email:<email_id>"` (set at build
    time), so all body chunks of the same email cluster together too.
    Falls back to legacy attachment_id / email_id keying for any rows
    that pre-date the migration.
    """
    sha = doc.get("sha256")
    if sha:
        return f"sha:{sha}"
    aid = doc.get("attachment_id")
    if aid:
        return f"att:{aid}"
    eid = doc.get("email_id")
    if eid:
        return f"eml:{eid}"
    return f"chunk:{doc.get('_id')}"
