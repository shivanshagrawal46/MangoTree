"""Brain storage — small documents, one concern per collection.

    property_files      one per property: headline, status, facts, open items, watch list
    property_activity   one line per event (email in/out, call, upload, chat decision)
    reply_drafts        a reply written and waiting for the person to send
    brain_events        which record was read for which property (idempotency)
    brain_threads       a chat thread (per property, or general)
    brain_messages      one message per document — a thread never grows one record
    brain_files         files dropped into a chat
    loan_terms          money figures imported from RKB's own workbooks
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.config.registry import PROPERTIES, PROPERTY_INDEX
from mangotree.retrieve import config as rcfg
from mangotree.storage.mongo import Mongo

from . import config as bc

OWNERS = ("rakesh", "jp", "manjunath", "wes", "kelly", "other")
ITEM_KINDS = ("reply", "action", "waiting", "decision")


def now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def active_property_ids() -> List[str]:
    return [p.property_id for p in PROPERTIES if p.property_id not in rcfg.ANALYSIS_EXCLUDED_PROPERTIES]


def all_file_ids() -> List[str]:
    return active_property_ids() + [bc.GENERAL_ID]


def name_of(pid: str) -> str:
    if pid == bc.GENERAL_ID:
        return bc.GENERAL_NAME
    p = PROPERTY_INDEX.get(pid)
    return p.canonical_address if p else pid


def norm_owner(value: Any) -> str:
    v = re.sub(r"[^a-z]", "", str(value or "").lower().replace("sir", ""))
    for o in OWNERS:
        if v.startswith(o):
            return o
    return "other"


def parse_day(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


class BrainStore:
    _indexed = False

    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        db = mongo.db
        self.files = db["property_files"]
        self.activity = db["property_activity"]
        self.drafts = db["reply_drafts"]
        self.events = db["brain_events"]
        self.threads = db["brain_threads"]
        self.messages = db["brain_messages"]
        self.chat_files = db["brain_files"]
        self.loan_terms = db["loan_terms"]
        self.settings = db["settings"]
        if not BrainStore._indexed:
            self._ensure_indexes()
            BrainStore._indexed = True

    def _ensure_indexes(self) -> None:
        for coll, keys, opts in (
            (self.files, "property_id", {"unique": True, "name": "ux_pf_property"}),
            (self.activity, [("property_id", 1), ("at", -1)], {"name": "ix_pa_prop_at"}),
            (self.activity, [("at", -1)], {"name": "ix_pa_at"}),
            (self.activity, [("sha", 1), ("property_id", 1)], {"name": "ix_pa_sha", "sparse": True}),
            (self.drafts, "draft_id", {"unique": True, "name": "ux_rd_id"}),
            (self.drafts, [("status", 1), ("created_at", -1)], {"name": "ix_rd_status"}),
            (self.drafts, "thread_key", {"name": "ix_rd_thread", "sparse": True}),
            (self.drafts, "reply_to_sha", {"name": "ix_rd_reply", "sparse": True}),
            (self.events, [("sha", 1), ("property_id", 1)], {"unique": True, "name": "ux_be_sha_prop"}),
            (self.threads, "thread_id", {"unique": True, "name": "ux_bt_id"}),
            (self.threads, [("property_id", 1), ("updated_at", -1)], {"name": "ix_bt_prop"}),
            (self.messages, [("thread_id", 1), ("seq", 1)], {"name": "ix_bm_thread_seq"}),
            (self.chat_files, "file_id", {"unique": True, "name": "ux_bf_id"}),
            (self.loan_terms, "property_id", {"unique": True, "name": "ux_lt_property"}),
        ):
            try:
                coll.create_index(keys, **opts)
            except Exception:
                pass

    # ------------------------------------------------------------ settings
    def setting(self, key: str, default: Any = None) -> Any:
        doc = self.settings.find_one({"_id": key})
        return doc.get("value", default) if doc else default

    def set_setting(self, key: str, value: Any, by: str) -> None:
        self.settings.update_one({"_id": key}, {"$set": {"value": value, "by": by, "at": now()}}, upsert=True)

    # --------------------------------------------------------------- files
    def get_file(self, pid: str) -> Optional[Dict[str, Any]]:
        return self.files.find_one({"property_id": pid}, {"_id": 0})

    def list_files(self) -> List[Dict[str, Any]]:
        order = {pid: i for i, pid in enumerate(all_file_ids())}
        rows = list(self.files.find({"property_id": {"$in": list(order)}}, {"_id": 0}))
        rows.sort(key=lambda f: order.get(f["property_id"], 999))
        return rows

    def save_file(self, pid: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        fields = {**fields, "property_id": pid, "name": name_of(pid), "updated_at": now()}
        self.files.update_one({"property_id": pid}, {"$set": fields, "$inc": {"version": 1},
                                                     "$setOnInsert": {"created_at": now()}}, upsert=True)
        return self.get_file(pid) or {}

    @staticmethod
    def new_item(*, title: str, detail: str = "", owner: str = "rakesh", kind: str = "action", due: Any = None,
                 source_sha: Optional[str] = None, opened_at: Optional[datetime] = None, by: str = "brain") -> Dict[str, Any]:
        return {"id": _id("i"), "title": str(title or "").strip()[:160], "detail": str(detail or "").strip()[:600],
                "owner": norm_owner(owner), "kind": kind if kind in ITEM_KINDS else "action", "due": parse_day(due),
                "status": "open", "opened_at": opened_at or now(), "source_sha": source_sha, "by": by,
                "closed_at": None, "closed_note": None, "closed_by": None, "closed_by_sha": None}

    def add_item(self, pid: str, **kw) -> Dict[str, Any]:
        item = self.new_item(**kw)
        self.files.update_one({"property_id": pid}, {"$push": {"open_items": item}, "$set": {"updated_at": now()}, "$inc": {"version": 1}})
        return item

    def close_item(self, pid: str, item_id: str, *, note: str = "", by: str = "brain", sha: Optional[str] = None,
                   status: str = "done") -> bool:
        r = self.files.update_one(
            {"property_id": pid, "open_items": {"$elemMatch": {"id": item_id, "status": "open"}}},
            {"$set": {"open_items.$.status": status, "open_items.$.closed_at": now(), "open_items.$.closed_note": str(note or "")[:400],
                      "open_items.$.closed_by": by, "open_items.$.closed_by_sha": sha, "updated_at": now()}, "$inc": {"version": 1}})
        return r.modified_count > 0

    def reopen_item(self, pid: str, item_id: str) -> bool:
        r = self.files.update_one({"property_id": pid, "open_items.id": item_id},
                                  {"$set": {"open_items.$.status": "open", "open_items.$.closed_at": None, "open_items.$.closed_note": None,
                                            "open_items.$.closed_by": None, "updated_at": now()}})
        return r.modified_count > 0

    def update_item(self, pid: str, item_id: str, fields: Dict[str, Any]) -> bool:
        sets: Dict[str, Any] = {}
        if fields.get("title"):
            sets["open_items.$.title"] = str(fields["title"]).strip()[:160]
        if fields.get("detail") is not None:
            sets["open_items.$.detail"] = str(fields["detail"]).strip()[:600]
        if fields.get("owner"):
            sets["open_items.$.owner"] = norm_owner(fields["owner"])
        if "due" in fields:
            sets["open_items.$.due"] = parse_day(fields.get("due"))
        if not sets:
            return False
        sets["updated_at"] = now()
        return self.files.update_one({"property_id": pid, "open_items.id": item_id}, {"$set": sets}).modified_count > 0

    @staticmethod
    def open_items(file: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [i for i in ((file or {}).get("open_items") or []) if i.get("status") == "open"]

    # ------------------------------------------------------------ activity
    def add_activity(self, pid: str, *, at: Optional[datetime], kind: str, who: str = "", title: str, summary: str = "",
                     sha: Optional[str] = None, relevance: str = "material", extra: Optional[Dict[str, Any]] = None) -> None:
        doc = {"property_id": pid, "at": at or now(), "kind": kind, "who": str(who or "")[:120], "title": str(title or "")[:200],
               "summary": str(summary or "")[:1200], "sha": sha, "relevance": relevance, "created_at": now(), **(extra or {})}
        if sha:
            self.activity.update_one({"property_id": pid, "sha": sha}, {"$set": doc}, upsert=True)
        else:
            self.activity.insert_one(doc)

    def recent_activity(self, pid: Optional[str] = None, *, days: int = bc.ACTIVITY_DAYS, limit: int = 80,
                        include_noise: bool = False) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"at": {"$gte": now() - timedelta(days=days)}}
        if pid:
            q["property_id"] = pid
        if not include_noise:
            q["relevance"] = {"$ne": "noise"}
        return list(self.activity.find(q, {"_id": 0}).sort("at", -1).limit(limit))

    # -------------------------------------------------------------- drafts
    def create_draft(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        d = {"draft_id": _id("d"), "status": "ready", "created_at": now(), "updated_at": now(), "edited": False, **doc}
        self.drafts.insert_one(dict(d))
        d.pop("_id", None)
        return d

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        return self.drafts.find_one({"draft_id": draft_id}, {"_id": 0})

    def list_drafts(self, *, status: Sequence[str] = ("ready",), pid: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"status": {"$in": list(status)}}
        if pid:
            q["property_ids"] = pid
        # A list never shows the search passages a draft was written from (about 30 KB each):
        # leaving them in the database makes every list several times quicker to fetch.
        return list(self.drafts.find(q, {"_id": 0, "retrieval": 0, "usage": 0}).sort("created_at", -1).limit(limit))

    def update_draft(self, draft_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.drafts.update_one({"draft_id": draft_id}, {"$set": {**fields, "updated_at": now()}})
        return self.get_draft(draft_id)

    # -------------------------------------------------------------- events
    def processed(self, sha: str, pid: str) -> bool:
        return self.events.count_documents({"sha": sha, "property_id": pid}, limit=1) > 0

    def mark_event(self, sha: str, pid: str, *, status: str, detail: Optional[Dict[str, Any]] = None) -> None:
        self.events.update_one({"sha": sha, "property_id": pid},
                               {"$set": {"sha": sha, "property_id": pid, "status": status, "at": now(), **(detail or {})}}, upsert=True)

    # ---------------------------------------------------------------- chat
    def create_thread(self, *, pid: Optional[str], by: str, title: str = "") -> Dict[str, Any]:
        t = {"thread_id": _id("t"), "property_id": pid, "title": title or "", "created_by": by, "created_at": now(),
             "updated_at": now(), "n_messages": 0, "source_chunk_ids": [], "archived": False}
        self.threads.insert_one(dict(t))
        return t

    def get_thread(self, thread_id: str) -> Optional[Dict[str, Any]]:
        return self.threads.find_one({"thread_id": thread_id}, {"_id": 0})

    def list_threads(self, pid: Optional[str], *, limit: int = 30) -> List[Dict[str, Any]]:
        return list(self.threads.find({"property_id": pid, "archived": {"$ne": True}, "n_messages": {"$gt": 0}},
                                      {"_id": 0, "source_chunk_ids": 0}).sort("updated_at", -1).limit(limit))

    def latest_thread(self, pid: Optional[str], by: str) -> Dict[str, Any]:
        t = self.threads.find_one({"property_id": pid, "archived": {"$ne": True}}, {"_id": 0}, sort=[("updated_at", -1)])
        return t or self.create_thread(pid=pid, by=by)

    def add_message(self, thread_id: str, msg: Dict[str, Any]) -> Dict[str, Any]:
        t = self.threads.find_one_and_update({"thread_id": thread_id}, {"$inc": {"n_messages": 1}, "$set": {"updated_at": now()}},
                                             return_document=True)
        doc = {"message_id": _id("m"), "thread_id": thread_id, "seq": (t or {}).get("n_messages", 1), "at": now(), **msg}
        self.messages.insert_one(dict(doc))
        doc.pop("_id", None)
        return doc

    def thread_messages(self, thread_id: str, *, limit: int = 400) -> List[Dict[str, Any]]:
        rows = list(self.messages.find({"thread_id": thread_id}, {"_id": 0}).sort("seq", -1).limit(limit))
        return rows[::-1]
