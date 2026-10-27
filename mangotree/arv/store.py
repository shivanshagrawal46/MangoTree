"""Persistence for ARV analyses — one document per analysis in
``arv_analyses``. The document is the whole file (inputs, comps, valuation,
underwriting, the analyst's review, the chat), so a page load is one read and
an export needs nothing else.

Reads are served from a small in-process cache: the API and its jobs run in
one process and every write goes through this class, so the cache is always
current, and a page open costs no Atlas round trip. Callers get a copy, so a
request that mutates a document and then fails cannot poison the cache.
"""
from __future__ import annotations

import copy
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mangotree.storage.mongo import Mongo

SUMMARY_FIELDS = ("analysis_id", "created_at", "updated_at", "created_by", "created_by_name", "created_side", "created_org", "created_org_name",
                  "status", "error", "job_id",
                  "subject.address", "subject.property_type", "subject.beds", "subject.baths", "subject.sqft", "geocode.formatted", "geocode.state",
                  "valuation.arv", "valuation.arv_low", "valuation.arv_high", "valuation.confidence", "analysis.headline", "analysis.arv_opinion",
                  "underwriting.mao", "rehab_budget.total", "comps_count", "property_id", "label")

CACHE_SIZE = 64


class ArvStore:
    def __init__(self, mongo: Mongo):
        self.coll = mongo.db["arv_analyses"]
        self.coll.create_index("analysis_id", unique=True, name="ux_arv_id")
        self.coll.create_index([("created_at", -1)], name="ix_arv_created")
        self.coll.create_index("property_id", sparse=True, name="ix_arv_property")
        self._cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def new_id() -> str:
        return "arv_" + uuid.uuid4().hex[:12]

    # ------------------------------------------------------------- cache
    def _remember(self, doc: Dict[str, Any]) -> None:
        with self._lock:
            self._cache[doc["analysis_id"]] = copy.deepcopy(doc)
            self._cache.move_to_end(doc["analysis_id"])
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)

    def _forget(self, analysis_id: str) -> None:
        with self._lock:
            self._cache.pop(analysis_id, None)

    # ------------------------------------------------------------- CRUD
    def create(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        doc.setdefault("analysis_id", self.new_id())
        doc["created_at"] = now
        doc["updated_at"] = now
        self.coll.insert_one(dict(doc))
        doc.pop("_id", None)
        self._remember(doc)
        return doc

    def get(self, analysis_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            hit = self._cache.get(analysis_id)
            if hit is not None:
                self._cache.move_to_end(analysis_id)
                return copy.deepcopy(hit)
        doc = self.coll.find_one({"analysis_id": analysis_id}, {"_id": 0})
        if doc:
            self._remember(doc)
        return doc

    def save(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["updated_at"] = datetime.now(timezone.utc)
        doc["comps_count"] = len(doc.get("comps") or [])
        body = {k: v for k, v in doc.items() if k != "_id"}
        self.coll.replace_one({"analysis_id": doc["analysis_id"]}, body, upsert=True)
        self._remember(body)
        return doc

    def patch(self, analysis_id: str, fields: Dict[str, Any]) -> None:
        fields["updated_at"] = datetime.now(timezone.utc)
        self.coll.update_one({"analysis_id": analysis_id}, {"$set": fields})
        with self._lock:
            hit = self._cache.get(analysis_id)
            if hit is not None:
                hit.update(copy.deepcopy(fields))

    def delete(self, analysis_id: str) -> bool:
        self._forget(analysis_id)
        return self.coll.delete_one({"analysis_id": analysis_id}).deleted_count == 1

    def list(self, *, limit: int = 50, property_id: Optional[str] = None, created_by: Optional[str] = None) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {}
        if property_id:
            q["property_id"] = property_id
        if created_by:
            q["created_by"] = created_by   # a contractor account sees only its own files
        proj = {f: 1 for f in SUMMARY_FIELDS}
        proj["_id"] = 0
        return list(self.coll.find(q, proj).sort("created_at", -1).limit(limit))
