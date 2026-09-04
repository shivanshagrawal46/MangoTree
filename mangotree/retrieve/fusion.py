"""Weighted Reciprocal Rank Fusion.

Combines *ranks*, not scores. The channels' scores live on unrelated scales —
cosine similarity, BM25, a regex hit — and normalising them would invent a
comparability that does not exist. Rank is the one thing every list shares.

Each list carries a weight: the channel's (exact-match channels trusted a little
more, the coarse document channel a little less) multiplied by the scope list's
(the property's own file at 1.0, the common store at 0.6). So a portfolio
document found by BM25 contributes 0.6 × 1.0 / (k + rank).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from mangotree.retrieve import config as cfg
from mangotree.retrieve.hits import Hit


@dataclass
class RankedList:
    name: str            # e.g. "own/vector", "portfolio/bm25_alt"
    hits: List[Hit]
    weight: float        # channel weight × scope-list weight
    label: str = ""      # scope label to stamp on hits from this list


def fuse(lists: Sequence[RankedList], *, k: int = cfg.RRF_K, cap: int = cfg.FUSED_CAP) -> List[Hit]:
    merged: Dict[str, Hit] = {}
    scores: Dict[str, float] = {}
    for rl in lists:
        for rank, hit in enumerate(rl.hits, start=1):
            key = hit.chunk_id
            if key not in merged:
                merged[key] = hit
                if rl.label and not hit.label:
                    hit.label = rl.label
            else:
                # Union of channel ranks, so the trace shows every list that found it.
                for ch, r in hit.channel_ranks.items():
                    tag = f"{rl.name}:{ch}" if "/" not in ch else ch
                    merged[key].channel_ranks.setdefault(tag, r)
            scores[key] = scores.get(key, 0.0) + rl.weight / (k + rank)
    for key, score in scores.items():
        merged[key].fused_score = score
    out = sorted(merged.values(), key=lambda h: -h.fused_score)
    return out[:cap]
