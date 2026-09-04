"""Rescoring and diversification — between fusion and the rerankers.

Fusion says how many channels agreed. Rescoring adds what fusion cannot know:

* **recency** — a lending file is read chronologically; the newest draw request
  is usually the one meant, so recent documents get a small, bounded lift.
* **authority** — an executed instrument outranks a draft, which outranks
  correspondence about it.
* **exact match** — a chunk that literally contains the amount, identifier or
  quoted phrase from the question is almost certainly relevant; rewarded, but
  capped so a lucky number cannot outrank a semantically perfect passage.
* **intent → type** — a question about "the payoff" prefers payoff statements.

Diversification then stops one document from filling the list: a cluster cap
per artifact, MMR against near-duplicate text (forwarded emails quote each
other), and, for temporal questions, a spread across periods. The hit count per
artifact is recorded *before* the cap, because parent expansion later needs it.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from mangotree.retrieve import config as cfg
from mangotree.retrieve.hits import Hit
from mangotree.retrieve.query_understanding import QueryUnderstanding


def _recency(hit: Hit, now: datetime) -> float:
    d = hit.date
    if not d or not hasattr(d, "timestamp"):
        return 1.0
    try:
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        age = (now - d).days
    except Exception:
        return 1.0
    if age < 0:
        return 1.0
    if age >= cfg.RECENCY_HORIZON_DAYS:
        return 1.0
    return 1.0 + (cfg.RECENCY_MAX_BOOST - 1.0) * (1.0 - age / cfg.RECENCY_HORIZON_DAYS)


def _authority(hit: Hit) -> float:
    cls = (hit.doc_class or "").lower()
    if cls in cfg.AUTHORITY_TIERS:
        return cfg.AUTHORITY_TIERS[cls]
    name = (hit.display_name or hit.filename or "").lower()
    if re.search(r"\b(recorded|executed|signed|final)\b", name):
        return 1.10
    if re.search(r"\bdraft\b", name):
        return 0.90
    return 1.0


def _exact(hit: Hit, tokens: Sequence[str]) -> float:
    if not tokens:
        return 1.0
    body = f"{hit.text}\n{hit.context}\n{hit.display_name}".lower()
    matched = 0
    for tok in tokens:
        t = tok.lower().strip()
        if not t:
            continue
        if t.startswith("$"):
            digits = re.sub(r"[^\d.]", "", t)
            if digits and (digits in re.sub(r"[^\d.]", "", body) or t in body):
                matched += 1
        elif t in body:
            matched += 1
    if not matched:
        return 1.0
    return min(cfg.EXACT_MATCH_CAP, 1.0 + 0.2 * matched)


def _intent_type(hit: Hit, understanding: QueryUnderstanding) -> float:
    if not understanding.doc_classes or not hit.doc_class:
        return 1.0
    return 1.12 if hit.doc_class in understanding.doc_classes else 1.0


def rescore(hits: Sequence[Hit], understanding: QueryUnderstanding, *, now: Optional[datetime] = None) -> List[Hit]:
    now = now or datetime.now(timezone.utc)
    tokens = understanding.exact_tokens()
    for h in hits:
        recency = _recency(h, now) if not understanding.prefers_earliest else 1.0
        if understanding.prefers_earliest and h.date:
            recency = 1.05  # mild lift for having a date at all when "first/original" is asked
        h.rescored = h.fused_score * recency * _authority(h) * _exact(h, tokens) * _intent_type(h, understanding)
    return sorted(hits, key=lambda h: -h.rescored)


# =============================================================================
# Diversification
# =============================================================================

_WORD = re.compile(r"[a-z0-9]{3,}")


def _shingles(text: str, n: int = 3) -> Set[str]:
    words = _WORD.findall(text.lower())[:400]
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def hit_counts_by_artifact(hits: Sequence[Hit]) -> Dict[str, int]:
    """How many candidates each artifact produced — read BEFORE the cap."""
    return Counter(h.artifact_sha for h in hits)


def diversify(
    hits: Sequence[Hit],
    understanding: QueryUnderstanding,
    *,
    cap_per_parent: int = cfg.CLUSTER_CAP_PER_PARENT,
    mmr_lambda: float = cfg.MMR_LAMBDA,
    limit: Optional[int] = None,
) -> List[Hit]:
    """Cluster cap per artifact, then MMR on text, then temporal spread."""
    limit = limit or cfg.RERANK_POOL[understanding.complexity]

    # 1. cluster cap — in rescored order, so each artifact keeps its best.
    per_parent: Dict[str, int] = defaultdict(int)
    capped: List[Hit] = []
    for h in hits:
        if per_parent[h.artifact_sha] >= cap_per_parent:
            continue
        per_parent[h.artifact_sha] += 1
        capped.append(h)

    # 2. MMR — greedy: pick the best remaining that is least like what's picked.
    if not capped:
        return []
    shingles = {h.chunk_id: _shingles(h.text) for h in capped}
    top = max(h.rescored for h in capped) or 1.0
    picked: List[Hit] = []
    remaining = list(capped)
    while remaining and len(picked) < limit:
        best, best_score = None, -1e9
        for h in remaining:
            rel = h.rescored / top
            nov = 0.0
            if picked:
                nov = max(_jaccard(shingles[h.chunk_id], shingles[p.chunk_id]) for p in picked[-25:])
            score = mmr_lambda * rel - (1 - mmr_lambda) * nov
            if score > best_score:
                best, best_score = h, score
        picked.append(best)
        remaining.remove(best)

    # 3. temporal spread — only when the question is about time and the pick
    #    collapsed onto one period. Swap in the best hit from each missing month.
    if "temporal" in understanding.intents and len(picked) >= 10:
        months = Counter(h.date_ym for h in picked if h.date_ym)
        if months and months.most_common(1)[0][1] > 0.7 * len(picked):
            seen_months = set(months)
            for h in remaining:
                if h.date_ym and h.date_ym not in seen_months:
                    picked.append(h)
                    seen_months.add(h.date_ym)
                    if len(picked) >= limit + 10:
                        break
    return picked
