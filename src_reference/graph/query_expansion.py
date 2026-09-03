"""Sprint 3 · 3.5.1 — alias + legal-synonym query expansion (recall lever).

Before searching, expand the user's query with (a) every known alias of the
resolved entities and (b) legal synonyms, so the lexical + semantic channels
catch every phrasing. Pure, no LLM.
"""
from __future__ import annotations

from typing import List, Optional, Set

# Bidirectional legal synonym groups. Any term present -> add the others.
_SYNONYM_GROUPS: List[Set[str]] = [
    {"lien", "encumbrance"},
    {"grantor", "seller", "transferor"},
    {"grantee", "buyer", "transferee", "purchaser"},
    {"mortgage", "deed of trust", "security instrument"},
    {"satisfaction", "release", "discharge", "payoff"},
    {"lis pendens", "notice of pendency", "pending litigation"},
    {"deed", "conveyance", "transfer"},
    {"judgment", "judgement", "court order"},
    {"foreclosure", "foreclosure action"},
    {"title report", "title search", "title commitment", "abstract of title"},
    {"insurance", "coverage", "policy", "binder"},
    {"owner", "title holder", "vesting"},
    {"llc", "limited liability company", "entity", "shell"},
]


def legal_synonyms(query: str) -> List[str]:
    """Return synonym phrases to OR into retrieval for terms present in query."""
    q = (query or "").lower()
    extra: Set[str] = set()
    for group in _SYNONYM_GROUPS:
        present = [t for t in group if t in q]
        if present:
            for t in group:
                if t not in q:
                    extra.add(t)
    return sorted(extra)


def expand_query(query: str, entity_index=None, *, max_variants: int = 6) -> List[str]:
    """Return [original, alias-anchored, synonym-augmented...] query variants.

    `entity_index` (graph.fanout.EntityIndex) is optional; if given, resolved
    entities' canonical names + aliases are appended to widen recall.
    """
    variants: List[str] = [query]
    syns = legal_synonyms(query)
    if syns:
        variants.append(query + " " + " ".join(syns))

    if entity_index is not None:
        try:
            res = entity_index.resolve(query)
            aliases: Set[str] = set()
            for eid in list(res.get("all", set()))[:5]:
                e = entity_index.by_id.get(eid, {})
                cn = e.get("canonical_name") or e.get("canonical_address")
                if cn:
                    aliases.add(cn)
                for a in (e.get("aliases") or [])[:3]:
                    aliases.add(a)
            for a in list(aliases)[:5]:
                variants.append(f"{query} {a}")
        except Exception:  # noqa: BLE001
            pass

    # dedup preserving order
    seen: Set[str] = set()
    out: List[str] = []
    for v in variants:
        k = v.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(v.strip())
        if len(out) >= max_variants:
            break
    return out
