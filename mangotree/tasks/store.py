"""Task store — one collection, every change audited.

A task has an owner (Rakesh, JP, Manjunath, Wes, or a named other), a property
(or none for portfolio-level), a status, and where it came from: a person typed
it, Opus 5 extracted it from the records, or Opus 5 suggested it at the end of
an answer. AI tasks arrive as ``suggested`` and become ``open`` when a person
accepts them; ticking sets ``done`` and records who ticked and when. Nothing is
deleted — a dismissed task is a status.

Every write appends to ``task_audit`` so "who changed what, when" is never a
question.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.storage.mongo import Mongo

OWNERS = ("Rakesh", "JP", "Manjunath", "Wes")
STATUSES = ("suggested", "open", "done", "dismissed")
SOURCES = ("manual", "ai_extracted", "ai_suggested")
PRIORITIES = ("critical", "high", "normal", "low")


def normalise_owner(raw: Optional[str]) -> str:
    s = (raw or "").strip()
    low = s.lower()
    for o in OWNERS:
        if o.lower() in low:
            return o
    if "jaspreet" in low or low in ("jp sir", "j.p."):
        return "JP"
    if "stone" in low or "listing profit" in low or "lp remodel" in low or "roi blocks" in low:
        return "Wes"
    return s[:40] or "Rakesh"


def task_id_for(title: str, property_id: Optional[str], owner: str, source_sha: Optional[str]) -> str:
    raw = "|".join([re.sub(r"\s+", " ", title.strip().lower())[:120], property_id or "", owner, source_sha or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


class TaskStore:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.coll = mongo.db["tasks"]
        self.audit = mongo.db["task_audit"]
        self.coll.create_index("task_id", unique=True, name="ux_task_id")
        self.coll.create_index([("owner", 1), ("status", 1)], name="ix_task_owner_status")
        self.coll.create_index([("property_id", 1), ("status", 1)], name="ix_task_property_status")
        self.coll.create_index("due", name="ix_task_due")
        self.audit.create_index([("task_id", 1), ("at", -1)], name="ix_audit_task")

    def _log(self, task_id: str, action: str, by: str, detail: Dict[str, Any] = None) -> None:
        self.audit.insert_one({"task_id": task_id, "action": action, "by": by, "at": datetime.now(timezone.utc),
                               "detail": detail or {}})

    # ------------------------------------------------------------------ write
    def upsert(self, *, title: str, owner: str, property_id: Optional[str], by: str, source: str = "manual",
               status: str = "open", priority: str = "normal", due: Optional[datetime] = None, why: str = "",
               evidence: Optional[List[Dict[str, Any]]] = None, source_sha: Optional[str] = None,
               tags: Sequence[str] = ()) -> Dict[str, Any]:
        owner = normalise_owner(owner)
        status = status if status in STATUSES else "open"
        priority = priority if priority in PRIORITIES else "normal"
        tid = task_id_for(title, property_id, owner, source_sha)
        now = datetime.now(timezone.utc)
        existing = self.coll.find_one({"task_id": tid})
        if existing:
            # Re-extraction must not resurrect a task a human already closed.
            if existing.get("status") in ("done", "dismissed"):
                return existing
            update = {"title": title.strip(), "why": why or existing.get("why", ""), "priority": priority, "updated_at": now}
            if due:
                update["due"] = due
            if evidence:
                update["evidence"] = evidence
            self.coll.update_one({"task_id": tid}, {"$set": update})
            return self.coll.find_one({"task_id": tid})
        doc = {
            "task_id": tid, "title": title.strip(), "owner": owner, "property_id": property_id,
            "status": status, "priority": priority, "source": source if source in SOURCES else "manual",
            "due": due, "why": why, "evidence": evidence or [], "source_sha": source_sha, "tags": list(tags),
            "created_by": by, "created_at": now, "updated_at": now,
            "done_at": None, "done_by": None,
        }
        self.coll.insert_one(doc)
        self._log(tid, "created", by, {"source": source, "status": status})
        return doc

    def set_status(self, task_id: str, status: str, by: str, remark: str = "") -> Optional[Dict[str, Any]]:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        now = datetime.now(timezone.utc)
        update: Dict[str, Any] = {"status": status, "updated_at": now}
        if status == "done":
            update.update({"done_at": now, "done_by": by})
        elif status == "open":
            update.update({"done_at": None, "done_by": None, "accepted_by": by, "accepted_at": now})
        if remark:
            update["last_remark"] = remark
        r = self.coll.find_one_and_update({"task_id": task_id}, {"$set": update}, return_document=True)
        if r:
            self._log(task_id, f"status:{status}", by, {"remark": remark})
        return r

    def edit(self, task_id: str, by: str, **fields) -> Optional[Dict[str, Any]]:
        allowed = {k: v for k, v in fields.items() if k in ("title", "owner", "priority", "due", "why", "property_id", "tags")}
        if "owner" in allowed:
            allowed["owner"] = normalise_owner(allowed["owner"])
        allowed["updated_at"] = datetime.now(timezone.utc)
        r = self.coll.find_one_and_update({"task_id": task_id}, {"$set": allowed}, return_document=True)
        if r:
            self._log(task_id, "edited", by, {k: str(v)[:120] for k, v in allowed.items()})
        return r

    # ------------------------------------------------------------------- read
    def list(self, *, owner: Optional[str] = None, property_id: Optional[str] = None,
             statuses: Sequence[str] = ("suggested", "open"), limit: int = 500) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"status": {"$in": list(statuses)}}
        if owner:
            q["owner"] = normalise_owner(owner)
        if property_id:
            q["property_id"] = property_id
        rows = list(self.coll.find(q, {"_id": 0}).sort([("status", 1), ("due", 1), ("priority", 1), ("created_at", -1)]).limit(limit))
        order = {"critical": 0, "high": 1, "normal": 2, "low": 3}
        rows.sort(key=lambda t: (t["status"] != "open", t.get("due") is None, t.get("due") or datetime.max.replace(tzinfo=timezone.utc), order.get(t.get("priority"), 2)))
        return rows

    def counts(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"by_owner": {}, "by_property": {}, "by_status": {}}
        for r in self.coll.aggregate([{"$group": {"_id": {"o": "$owner", "s": "$status"}, "n": {"$sum": 1}}}]):
            out["by_owner"].setdefault(r["_id"]["o"], {})[r["_id"]["s"]] = r["n"]
            out["by_status"][r["_id"]["s"]] = out["by_status"].get(r["_id"]["s"], 0) + r["n"]
        for r in self.coll.aggregate([{"$group": {"_id": {"p": "$property_id", "s": "$status"}, "n": {"$sum": 1}}}]):
            out["by_property"].setdefault(r["_id"]["p"] or "portfolio", {})[r["_id"]["s"]] = r["n"]
        return out

    def history(self, task_id: str) -> List[Dict[str, Any]]:
        return list(self.audit.find({"task_id": task_id}, {"_id": 0}).sort("at", -1).limit(50))
