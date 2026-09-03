"""Sprint 3 · 3.2.3 — bitemporal ownership/control edges.

Conveyance edges (`GRANTEE_OF`) already carry `as_of` (when an entity acquired
a property). Bitemporal closure adds the matching `until` — the date the
property left that owner — so the graph can answer *"who owned it on the date
of the lie?"* (the vision's §9.2 requirement) rather than only "who owns it
now".

Model: within one property's recorded chain of title, an owner's tenure runs
from its acquisition `as_of` until the *next* recorded conveyance. Co-owners
who acquire on the same date share the same `until`. The most recent owner has
`until = None` (open interval = still of record).

`build_ownership_intervals()` writes `until` onto every `GRANTEE_OF` edge and
mirrors the interval onto the matching `OWNS` edge. Idempotent — recomputes
from the current chain each run. `owner_as_of()` / `ownership_intervals()` are
the read-side query helpers (used by the timeline + graph tools).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.graph.schema import REL_GRANTEE_OF, REL_OWNS


def compute_until(acq_dates: List[datetime]) -> Dict[datetime, Optional[datetime]]:
    """Map each distinct acquisition date → the next acquisition date (its
    `until`), or None for the most recent. Pure + unit-testable."""
    uniq = sorted({d for d in acq_dates if d is not None})
    return {d: (uniq[i + 1] if i + 1 < len(uniq) else None)
            for i, d in enumerate(uniq)}


def build_ownership_intervals(rels_col, *, live: bool = False,
                              now: Optional[datetime] = None) -> Dict[str, int]:
    """Close `until` on GRANTEE_OF (and mirror onto OWNS) per property.

    Returns counts. Dry-run by default; pass live=True to write.
    """
    now = now or datetime.now(timezone.utc)
    # group conveyance edges by property (dst)
    by_prop: Dict[str, List[Dict[str, Any]]] = {}
    for e in rels_col.find({"type": REL_GRANTEE_OF},
                           {"src": 1, "dst": 1, "as_of": 1}):
        by_prop.setdefault(e.get("dst"), []).append(e)

    counts = {"properties": 0, "grantee_closed": 0, "grantee_open": 0,
              "owns_mirrored": 0}
    for prop, edges in by_prop.items():
        if not prop:
            continue
        nxt = compute_until([e.get("as_of") for e in edges])
        if not nxt:
            continue
        counts["properties"] += 1
        for e in edges:
            d = e.get("as_of")
            until = nxt.get(d) if d is not None else None
            if until is not None:
                counts["grantee_closed"] += 1
            else:
                counts["grantee_open"] += 1
            if live:
                rels_col.update_one(
                    {"type": REL_GRANTEE_OF, "src": e["src"], "dst": prop},
                    {"$set": {"until": until, "bitemporal_updated_at": now}})
                # mirror the interval onto the OWNS edge for the same owner
                res = rels_col.update_one(
                    {"type": REL_OWNS, "src": e["src"], "dst": prop},
                    {"$set": {"as_of": d, "until": until,
                              "bitemporal_updated_at": now}})
                if res.matched_count:
                    counts["owns_mirrored"] += 1
    return counts


def _active(e: Dict[str, Any], when: datetime) -> bool:
    a, u = e.get("as_of"), e.get("until")
    if a is not None and a > when:
        return False
    if u is not None and u <= when:
        return False
    return True


def owner_as_of(rels_col, property_id: str, when: datetime) -> List[Dict[str, Any]]:
    """Owner edges of record for `property_id` on date `when` (the bitemporal
    'as of' query). Falls back to OWNS edges, else GRANTEE_OF."""
    edges = list(rels_col.find(
        {"type": {"$in": [REL_OWNS, REL_GRANTEE_OF]}, "dst": property_id}))
    owns = [e for e in edges if e.get("type") == REL_OWNS]
    pool = owns if any(e.get("as_of") for e in owns) else edges
    return [e for e in pool if _active(e, when)]


def ownership_intervals(rels_col, property_id: str) -> List[Dict[str, Any]]:
    """Ordered ownership timeline for a property: [{owner, as_of, until}]."""
    edges = list(rels_col.find(
        {"type": REL_GRANTEE_OF, "dst": property_id},
        {"src": 1, "as_of": 1, "until": 1, "source_quote": 1, "amount": 1}))
    edges.sort(key=lambda e: (e.get("as_of") is None, e.get("as_of")))
    return [{"owner": e.get("src"), "as_of": e.get("as_of"),
             "until": e.get("until"), "amount": e.get("amount"),
             "source_quote": e.get("source_quote")} for e in edges]
