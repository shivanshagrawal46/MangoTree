"""The real-estate agents and brokers RKB knows, by the area of its properties.

Three kinds of entry, never mixed up on the page:

* ``records``  — people RKB or ROI Blocks has actually dealt with. Their details were read
  from their own email signatures and from signed documents on file.
* ``wes_list`` — names marked as an agent in the contact list Wes sent on 9 Sep 2026
  ("contacts and realtors who have sent us properties"). Phone only; no email in that list.
* ``web``      — agents active in the area, found on the public web (their own or their
  brokerage's page). RKB has had no contact with them; each carries the page it came from.

Nothing here is written by a model and nothing is sent to anyone. The list is kept in two
collections: ``broker_areas`` (an area and the properties in it) and ``brokers``. The only
thing worked out on each look is the last email with each person — from the mail on file,
by address — and where each property stands (construction / on the market).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from mangotree.storage.mongo import Mongo

from . import snapshot
from .store import name_of, now

SOURCES = {"records": "In our records", "wes_list": "Wes's contact list", "web": "Found on the web"}
_ORDER = {"records": 0, "wes_list": 1, "web": 2}
_FIELDS = ("area", "locality", "name", "title", "brokerage", "office_city", "phones", "emails", "website", "linkedin", "link", "property_ids",
           "source", "source_url", "source_note", "checked_on")


def broker_id(name: str, area: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{area} {name}".lower()).strip("-")


class Brokers:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.coll = mongo.db["brokers"]
        self.areas = mongo.db["broker_areas"]

    # ------------------------------------------------------------ writing (seed and research loads)
    def set_area(self, area_id: str, label: str, order: int, property_ids: List[str], note: str = "") -> None:
        self.areas.update_one({"area_id": area_id}, {"$set": {"area_id": area_id, "label": label, "order": order, "property_ids": property_ids, "note": note}}, upsert=True)

    def upsert(self, b: Dict[str, Any]) -> str:
        """Add or replace one person. An entry from the web never replaces one from RKB's own records."""
        if b.get("source") not in SOURCES:
            raise ValueError(f"unknown source {b.get('source')!r}")
        if not (b.get("name") or "").strip() or not b.get("area"):
            raise ValueError("a broker needs a name and an area")
        bid = broker_id(b["name"], b["area"])
        have = self.coll.find_one({"broker_id": bid}, {"source": 1})
        if have and _ORDER[have.get("source", "web")] < _ORDER[b["source"]]:
            return bid
        doc = {k: b.get(k) for k in _FIELDS}
        doc["phones"] = [{"label": (p.get("label") or "").strip(), "number": p["number"].strip()} for p in b.get("phones") or [] if (p.get("number") or "").strip()]
        doc["emails"] = [e.strip().lower() for e in b.get("emails") or [] if e and "@" in e]
        doc["property_ids"] = list(b.get("property_ids") or [])
        self.coll.update_one({"broker_id": bid}, {"$set": {**doc, "broker_id": bid, "updated_at": now()}, "$setOnInsert": {"added_at": now()}}, upsert=True)
        return bid

    # ------------------------------------------------------------ reading
    def _last_mail(self, addresses: List[str]) -> Dict[str, Any]:
        """The newest email on file that each address is on (from, to or copied)."""
        if not addresses:
            return {}
        want = set(addresses)
        rx = [re.compile(f"^{re.escape(a)}$", re.I) for a in addresses]
        last: Dict[str, Any] = {}
        q = {"source_type": "email", "$or": [{"participants.from": {"$in": rx}}, {"participants.to": {"$in": rx}}, {"participants.cc": {"$in": rx}}]}
        for x in self.mongo.db["artifacts"].find(q, {"_id": 0, "participants.from": 1, "participants.to": 1, "participants.cc": 1, "date": 1}):
            d = x.get("date")
            if not d:
                continue
            p = x.get("participants") or {}
            for a in {str(a).lower() for k in ("from", "to", "cc") for a in p.get(k) or []} & want:
                if a not in last or d > last[a]:
                    last[a] = d
        return last

    def page(self) -> Dict[str, Any]:
        areas = sorted(self.areas.find({}, {"_id": 0}), key=lambda a: a.get("order") or 0)
        people = list(self.coll.find({}, {"_id": 0}))
        stages = {f["property_id"]: f.get("stage") for f in self.mongo.db["property_files"].find({}, {"_id": 0, "property_id": 1, "stage.stage": 1, "stage.recovery": 1})}
        sold = {p["property_id"] for p in self.mongo.db["properties"].find({"status": "sold"}, {"_id": 0, "property_id": 1})}
        # Where each property stands: from its file; a house with no file that the register marks sold is "sold".
        group = lambda p: snapshot.group_of(stages[p]) if stages.get(p) else ("sold" if p in sold else None)  # noqa: E731
        rank = {"construction": 0, "market": 1, "nonperforming": 2, None: 3, "sold": 4}
        last = self._last_mail(sorted({e for b in people for e in b.get("emails") or []}))
        by_area: Dict[str, List[Dict[str, Any]]] = {}
        for b in people:
            when = max((last[e] for e in b.get("emails") or [] if e in last), default=None)
            row = {**b, "source_label": SOURCES.get(b.get("source"), b.get("source")), "last_email": when,
                   "properties": [{"property_id": p, "name": name_of(p)} for p in b.get("property_ids") or []]}
            by_area.setdefault(b.get("area") or "other", []).append(row)
        out = []
        for a in areas:
            rows = by_area.pop(a["area_id"], [])
            rows.sort(key=lambda r: (_ORDER.get(r.get("source"), 9), -(r["last_email"].timestamp() if r.get("last_email") else 0), r.get("name") or ""))
            out.append({"area_id": a["area_id"], "label": a["label"], "note": a.get("note") or "",
                        "properties": sorted(({"property_id": p, "name": name_of(p), "group": group(p)} for p in a.get("property_ids") or []), key=lambda x: rank.get(x["group"], 3)),
                        "brokers": rows, "counts": {s: sum(1 for r in rows if r.get("source") == s) for s in SOURCES}})
        loose = [r for rows in by_area.values() for r in rows]          # an area that was removed: its people are not lost
        if loose:
            out.append({"area_id": "_unfiled", "label": "No area", "note": "", "properties": [], "brokers": loose, "counts": {s: sum(1 for r in loose if r.get("source") == s) for s in SOURCES}})
        return {"areas": out, "sources": [{"key": k, "label": v} for k, v in SOURCES.items()],
                "counts": {**{s: sum(1 for b in people if b.get("source") == s) for s in SOURCES}, "all": len(people),
                           "with_email": sum(1 for b in people if b.get("emails")), "with_phone": sum(1 for b in people if b.get("phones"))}}
