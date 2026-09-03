"""Sprint 3 · 3.4 — entity-anchored fan-out retrieval.

Turns a plain question ("what's the story on 520 E 81st?") into:
  1. resolve mentioned entities -> canonical IDs (alias + address aware)
  2. fan out across email_chunks_v2.entity_ids -> EVERY linked source type
     (David email + title + insurance + equity + litigation + deed...)
  3. rank by authority x recency x entity-match, ready for rerank.

Pure DB + in-memory; no LLM. Used by the new agent tools and the grid.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set

from src.graph.normalize import norm_name, address_key, strip_suffixes
from src.graph.schema import authority_for

_STOP = {"the", "and", "llc", "inc", "corp", "estate", "realty", "asset", "management",
         "properties", "property", "associates", "holdings", "real", "new", "york",
         "trust", "bank", "lending", "capital", "group", "company", "co", "as", "to"}


def _distinctive(phrase: str) -> bool:
    """A phrase safe to match as an entity. Full multi-word firm/person names
    (e.g. 'IPA Asset Management') are kept even if individual tokens are common,
    because the FULL phrase is specific. Single short generic tokens are not."""
    p = (phrase or "").strip()
    if len(p) < 5:
        return False
    toks = re.findall(r"[a-z0-9]+", p.lower())
    if len(toks) >= 2 and len(p) >= 8:
        return True
    nonstop = [t for t in toks if t not in _STOP]
    return any(len(t) >= 6 for t in nonstop) or any(c.isdigit() for c in p)


class EntityIndex:
    """In-memory alias index for query-time entity resolution. Build once."""

    def __init__(self, ents_col):
        self.by_id: Dict[str, Dict[str, Any]] = {}
        self.phrase_to_ids: Dict[str, Set[str]] = {}
        self.addr_to_ids: Dict[str, Set[str]] = {}
        for e in ents_col.find({"is_active": {"$ne": False}},
                               {"_id": 1, "kind": 1, "canonical_name": 1, "aliases": 1,
                                "canonical_address": 1, "address_variants": 1,
                                "side": 1, "is_david": 1, "parcel_id": 1}):
            eid = e["_id"]
            self.by_id[eid] = e
            if e.get("kind") in ("person", "llc", "org", "case"):
                for a in [e.get("canonical_name")] + (e.get("aliases") or []):
                    if not a:
                        continue
                    if _distinctive(a):
                        self.phrase_to_ids.setdefault(a.strip().lower(), set()).add(eid)
                    # also index the suffix-stripped form so 'IPA Asset Management'
                    # (no 'LLC') in a question still resolves to 'IPA ... LLC'.
                    stripped = strip_suffixes(norm_name(a)).lower()
                    if stripped and _distinctive(stripped) and stripped != a.strip().lower():
                        self.phrase_to_ids.setdefault(stripped, set()).add(eid)
            if e.get("kind") == "property":
                for a in [e.get("canonical_address")] + (e.get("address_variants") or []):
                    if a:
                        ak = address_key(a)
                        if ak:
                            self.addr_to_ids.setdefault(ak, set()).add(eid)

    def resolve(self, query: str) -> Dict[str, Set[str]]:
        """Return {'all': set, 'properties': set, 'people': set, 'llcs': set}."""
        q = (query or "").lower()
        hits: Set[str] = set()
        for phrase, ids in self.phrase_to_ids.items():
            if phrase in q:
                hits |= ids
        # address: scan house-number + street tokens in the query
        qk = address_key(query)
        if qk and qk in self.addr_to_ids:
            hits |= self.addr_to_ids[qk]
        # also try every (house-number, following-word) pair from the query
        for mo in re.finditer(r"\b(\d{1,5})\s+([a-z0-9]+(?:\s+[a-z0-9]+)?)", q):
            cand = address_key(f"{mo.group(1)} {mo.group(2)}")
            if cand in self.addr_to_ids:
                hits |= self.addr_to_ids[cand]
        out = {"all": hits, "properties": set(), "people": set(), "llcs": set(), "cases": set()}
        for eid in hits:
            k = self.by_id.get(eid, {}).get("kind")
            if k == "property":
                out["properties"].add(eid)
            elif k == "person":
                out["people"].add(eid)
            elif k == "llc":
                out["llcs"].add(eid)
            elif k == "case":
                out["cases"].add(eid)
        return out


def fan_out_chunks(chunks_col, entity_ids: Set[str], *, exclude_privileged: bool = False,
                   limit: int = 400, diversify: bool = True) -> List[Dict[str, Any]]:
    """All chunks linked to ANY of entity_ids, ranked by authority x recency.
    When `diversify`, round-robin across source types so EVERY linked source
    (title, insurance, equity, email, attachment, litigation) is represented
    near the top — the vision's 'never miss a source type' guarantee — instead
    of the highest-authority type filling every slot."""
    if not entity_ids:
        return []
    q: Dict[str, Any] = {"entity_ids": {"$in": list(entity_ids)}}
    if exclude_privileged:
        q["privilege_status"] = {"$ne": "privileged"}
    rows = list(chunks_col.find(q, {
        "_id": 1, "document_id": 1, "source_type": 1, "doc_source_type": 1,
        "body": 1, "text": 1, "entity_ids": 1, "entity_refs": 1, "doc_date": 1,
        "latest_date": 1, "corpus": 1, "privilege_status": 1, "property_address": 1,
        "vendor": 1, "is_update": 1, "occurrences": 1,
        # citation/display fields so results convert cleanly to RetrievedChunk
        "email_id": 1, "attachment_id": 1, "filename": 1, "page_start": 1,
        "page_end": 1, "date": 1, "from_email": 1, "to_emails": 1, "subject": 1,
        "sha256": 1,
    }).limit(3000))  # fetch wide BEFORE scoring so high-authority title/
    #                  insurance chunks are never starved by a dominant
    #                  source type's natural insertion order.

    def score(c: Dict[str, Any]) -> float:
        st = c.get("doc_source_type") or c.get("source_type")
        auth = authority_for(st)
        match = len(set(c.get("entity_ids") or []) & entity_ids)
        d = c.get("latest_date") or c.get("doc_date")
        rec = 0.0
        try:
            rec = d.timestamp() / 1e11
        except Exception:  # noqa: BLE001
            pass
        return auth + 0.15 * match + rec

    rows.sort(key=score, reverse=True)
    if not diversify:
        return rows[:limit]
    # round-robin across source types (each already score-sorted) so the top
    # `limit` always spans every linked source type.
    from collections import defaultdict, deque
    groups: Dict[str, deque] = defaultdict(deque)
    for c in rows:
        st = c.get("doc_source_type") or c.get("source_type") or "unknown"
        groups[st].append(c)
    out: List[Dict[str, Any]] = []
    order = sorted(groups.keys(),
                   key=lambda st: authority_for(st), reverse=True)
    while len(out) < limit and any(groups[st] for st in order):
        for st in order:
            if groups[st]:
                out.append(groups[st].popleft())
                if len(out) >= limit:
                    break
    return out


def source_type_breakdown(chunks: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in chunks:
        st = c.get("doc_source_type") or c.get("source_type") or "unknown"
        out[st] = out.get(st, 0) + 1
    return out
