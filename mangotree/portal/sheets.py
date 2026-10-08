"""Contractor sheets, events and replies — the portal's whole data model.

``contractor_sheets``  one document per organisation per next-steps run: the
                       steps addressed to that organisation's people, with
                       only the fields written FOR them. Built when a run
                       completes; the portal reads nothing else.
``contractor_events``  append-only, hash-chained: viewed / replied /
                       reported_done / signed_in, with the exact text.
``artifacts``          a reply is also a record on the property (doc_class
                       ``portal_reply``), so the morning writer and search
                       see it like any email.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.config.registry import PEOPLE, PROPERTY_INDEX
from mangotree.core.hashing import sha256_bytes
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

PEOPLE_INDEX = {p.person_id: p for p in PEOPLE}

#: Which next-steps persona each contractor person answers for. Kelly answers
#: for the same sheet today (one contractor persona exists); a second persona
#: is a generator change, not a portal one.
PERSONA_FOR_PERSON = {"wes": "wes", "kelly": "wes"}
#: What a contractor may see of a step. Nothing else on the step leaves RKB.
SAFE_STEP_FIELDS = ("title", "detail", "due", "urgency", "first_seen", "carried_days")
_LOCK = threading.Lock()


def _norm(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _step_id(pid: str, title: str) -> str:
    return "st-" + hashlib.sha1(f"{pid}|{_norm(title)}".encode()).hexdigest()[:12]


def contractor_orgs(mongo: Mongo) -> Dict[str, Dict[str, Any]]:
    """Organisations with at least one active contractor account, with the
    union of their people's property scopes."""
    out: Dict[str, Dict[str, Any]] = {}
    for u in mongo.db["users"].find({"side": "contractor", "active": {"$ne": False}}, {"_id": 0, "password_hash": 0}):
        org = u.get("org") or "contractor"
        o = out.setdefault(org, {"org": org, "org_name": u.get("org_name") or org, "property_ids": set(), "persons": set()})
        o["property_ids"].update(u.get("property_ids") or [])
        if u.get("person_id"):
            o["persons"].add(u["person_id"])
    return out


# ---------------------------------------------------------------- sheets
def publish_sheet(mongo: Mongo, run: Dict[str, Any], org: str, property_ids: Sequence[str]) -> Optional[Dict[str, Any]]:
    """Project one run for one organisation. Step ids are stable across days:
    a carried step inherits the id it had on the previous sheet, so a reply
    made on Tuesday's item stays attached to Wednesday's carried version."""
    if not run or run.get("status") != "complete":
        return None
    coll = mongo.db["contractor_sheets"]
    coll.create_index([("org", 1), ("published_at", -1)], name="ix_cs_org_pub")
    coll.create_index("sheet_id", unique=True, name="ux_cs_id")
    prev = coll.find_one({"org": org}, {"_id": 0}, sort=[("published_at", -1)])
    prev_ids: Dict[tuple, str] = {}
    for p in (prev or {}).get("properties") or []:
        for s in p.get("steps") or []:
            prev_ids[(p["property_id"], _norm(s.get("title")))] = s["step_id"]
    persona = "wes"
    props_out: List[Dict[str, Any]] = []
    run_updates: Dict[str, Any] = {}
    scope = set(property_ids)
    for pid in run.get("order") or []:
        if pid not in scope or pid not in PROPERTY_INDEX:
            continue
        r = (run.get("properties") or {}).get(pid) or {}
        steps = []
        for i, s in enumerate(r.get(persona) or []):
            sid = (s.get("step_id") or prev_ids.get((pid, _norm(s.get("carried_from")))) or prev_ids.get((pid, _norm(s.get("title"))))
                   or _step_id(pid, s.get("title") or ""))
            if s.get("step_id") != sid:
                run_updates[f"properties.{pid}.{persona}.{i}.step_id"] = sid
            steps.append({"step_id": sid, "index": i, **{k: s.get(k) for k in SAFE_STEP_FIELDS}})
        props_out.append({"property_id": pid, "address": r.get("address") or PROPERTY_INDEX[pid].canonical_address, "steps": steps})
    if run_updates:
        mongo.db["next_steps_runs"].update_one({"run_id": run["run_id"]}, {"$set": run_updates})
    now = datetime.now(timezone.utc)
    doc = {"sheet_id": f"cs-{org}-{run['run_id']}", "org": org, "run_id": run["run_id"], "day": run.get("day"), "published_at": now,
           "properties": props_out, "step_count": sum(len(p["steps"]) for p in props_out)}
    doc["content_hash"] = hashlib.sha256(json.dumps(props_out, default=str, sort_keys=True).encode()).hexdigest()
    coll.update_one({"sheet_id": doc["sheet_id"]}, {"$set": doc}, upsert=True)
    logger.info("portal: sheet %s published for %s — %d steps on %d properties", doc["sheet_id"], org, doc["step_count"], len(props_out))
    return doc


def publish_all(mongo: Mongo, run: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for org, o in contractor_orgs(mongo).items():
        try:
            d = publish_sheet(mongo, run, org, sorted(o["property_ids"]))
            out[org] = (d or {}).get("step_count")
        except Exception as exc:
            logger.exception("portal: publishing for %s failed", org)
            out[org] = f"error: {type(exc).__name__}"
    return out


def latest_sheet(mongo: Mongo, org: str) -> Optional[Dict[str, Any]]:
    return mongo.db["contractor_sheets"].find_one({"org": org}, {"_id": 0}, sort=[("published_at", -1)])


def find_step(sheet: Dict[str, Any], step_id: str) -> Optional[Dict[str, Any]]:
    for p in sheet.get("properties") or []:
        for s in p.get("steps") or []:
            if s.get("step_id") == step_id:
                return {**s, "property_id": p["property_id"], "address": p.get("address")}
    return None


# ---------------------------------------------------------------- events
def _canon(v: Any) -> Any:
    """Mongo keeps datetimes to the millisecond, naive, UTC; hash the same
    rendering before and after the round trip."""
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v.isoformat(timespec="milliseconds")
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items() if k not in ("hash", "event_id", "_id")}
    if isinstance(v, list):
        return [_canon(x) for x in v]
    return v


def _chain_hash(prev_hash: str, body: Dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + json.dumps(_canon(body), default=str, sort_keys=True)).encode()).hexdigest()


def record_event(mongo: Mongo, *, user: Dict[str, Any], action: str, ip: Optional[str] = None, sheet_id: Optional[str] = None,
                 run_id: Optional[str] = None, property_id: Optional[str] = None, step_id: Optional[str] = None,
                 step_title: Optional[str] = None, text: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One append-only entry, hash-chained to the previous one. Never updated."""
    coll = mongo.db["contractor_events"]
    coll.create_index([("seq", 1)], unique=True, name="ux_ce_seq")
    coll.create_index([("org", 1), ("at", -1)], name="ix_ce_org_at")
    coll.create_index([("step_id", 1), ("at", 1)], name="ix_ce_step")
    with _LOCK:
        last = coll.find_one({}, {"_id": 0, "seq": 1, "hash": 1}, sort=[("seq", -1)])
        seq = int((last or {}).get("seq") or 0) + 1
        body = {"seq": seq, "user_id": user["user_id"], "org": user.get("org"), "person_id": user.get("person_id"), "name": user.get("name"),
                "at": datetime.now(timezone.utc), "ip": ip, "action": action, "sheet_id": sheet_id, "run_id": run_id,
                "property_id": property_id, "step_id": step_id, "step_title": step_title, "text": text, **(extra or {})}
        body["prev_hash"] = (last or {}).get("hash") or "genesis"
        body["hash"] = _chain_hash(body["prev_hash"], body)
        body["event_id"] = f"ce-{seq:07d}"
        coll.insert_one(dict(body))
    body.pop("_id", None)
    return body


def verify_chain(mongo: Mongo) -> Dict[str, Any]:
    """Walk the chain; the first broken link is reported. For audits."""
    prev = "genesis"
    n = 0
    for e in mongo.db["contractor_events"].find({}, {"_id": 0}).sort("seq", 1):
        n += 1
        h = e.get("hash")
        if e.get("prev_hash") != prev or _chain_hash(prev, e) != h:
            return {"ok": False, "broken_at_seq": e.get("seq"), "checked": n}
        prev = h
    return {"ok": True, "checked": n}


# ---------------------------------------------------------------- replies
def store_reply(mongo: Mongo, *, user: Dict[str, Any], sheet: Dict[str, Any], step: Dict[str, Any], text: str,
                action: str, ip: Optional[str] = None) -> Dict[str, Any]:
    """A reply (or a reported-done note) in three places: the event log, the
    run document RKB reads, and a record on the property for the morning."""
    text = (text or "").strip()
    pid = step["property_id"]
    now = datetime.now(timezone.utc)
    ev = record_event(mongo, user=user, action=action, ip=ip, sheet_id=sheet["sheet_id"], run_id=sheet["run_id"], property_id=pid,
                      step_id=step["step_id"], step_title=step.get("title"), text=text)
    # 1. On the run, for RKB's sheet view: replies + "reported done" (never `done`).
    entry = {"at": now, "by": user.get("name") or user["user_id"], "person_id": user.get("person_id"), "text": text, "action": action, "event_id": ev["event_id"]}
    sets: Dict[str, Any] = {}
    if action == "reported_done":
        sets[f"properties.{pid}.wes.{step['index']}.reported_done"] = {"at": now, "by": user.get("name"), "person_id": user.get("person_id"), "note": text}
    upd: Dict[str, Any] = {"$push": {f"properties.{pid}.wes.{step['index']}.replies": entry}}
    if sets:
        upd["$set"] = sets
    mongo.db["next_steps_runs"].update_one({"run_id": sheet["run_id"], f"properties.{pid}.wes.{step['index']}.step_id": step["step_id"]}, upd)
    # 2. A record on the property: searchable, quotable, read by the morning writer.
    person = PEOPLE_INDEX.get(user.get("person_id") or "")
    who = (person.display_name if person else None) or user.get("full_name") or user.get("name") or user["user_id"]
    label = "reports DONE" if action == "reported_done" else "replies"
    address = step.get("address") or PROPERTY_INDEX[pid].canonical_address
    body = (f"Portal reply — {who} {label} on {now:%Y-%m-%d %H:%M} UTC\n"
            f"Property: {address}\nNext step: {step.get('title')}\n"
            f"{'Note' if action == 'reported_done' else 'Reply'}: {text or '(no text)'}\n")
    data = body.encode("utf-8")
    sha = sha256_bytes(data)
    filename = f"Portal reply - {who} - {now:%Y-%m-%d %H%M} - {address}.txt"
    mongo.put_original(sha, data, filename, {"source_type": "upload", "doc_class": "portal_reply", "uploaded_by": user["user_id"]})
    mongo.artifacts.update_one({"sha256": sha}, {"$set": {
        "sha256": sha, "source_type": "upload", "filename": filename, "extension": ".txt", "content_type": "text/plain", "kind": "text",
        "doc_class": "portal_reply", "raw_size": len(data), "date": now, "privileged": False, "access": "normal",
        "author_person_id": user.get("person_id"), "person_ids": [user["person_id"]] if user.get("person_id") else [],
        "text": body, "extraction": {"status": "complete", "method": "portal", "extracted_at": now},
        "property_ids": [pid], "placement": "property", "scope": "property", "placed_at": now,
        "segregation": {"properties": [pid], "confidence": 1.0, "unresolved": False, "reasoning": "portal reply on a step of this property",
                        "fallback_used": "portal", "scope": "property", "model": "portal", "decided_at": now},
        "resolution_status": "resolved", "resolution": {"status": "segregated", "notes": ["portal reply; placed by the step it answers"]},
        "portal": {"event_id": ev["event_id"], "step_id": step["step_id"], "run_id": sheet["run_id"], "action": action, "org": user.get("org")},
        "updated_at": now}, "$addToSet": {"source_types": "upload"},
        "$setOnInsert": {"created_at": now, "first_run_id": f"portal-{now:%Y%m%d}"}}, upsert=True)
    return {"event": ev, "artifact_sha": sha}


def replies_for_steps(mongo: Mongo, org: str, step_ids: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for e in mongo.db["contractor_events"].find({"org": org, "step_id": {"$in": list(step_ids)}, "action": {"$in": ["replied", "reported_done"]}},
                                               {"_id": 0, "step_id": 1, "at": 1, "name": 1, "person_id": 1, "action": 1, "text": 1, "event_id": 1}).sort("at", 1):
        out.setdefault(e["step_id"], []).append(e)
    return out


def recent_events(mongo: Mongo, org: str, *, limit: int = 100, actions: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    q: Dict[str, Any] = {"org": org}
    if actions:
        q["action"] = {"$in": list(actions)}
    return list(mongo.db["contractor_events"].find(q, {"_id": 0}).sort("at", -1).limit(limit))


def writes_today(mongo: Mongo, user_id: str) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return mongo.db["contractor_events"].count_documents({"user_id": user_id, "at": {"$gte": start}, "action": {"$in": ["replied", "reported_done"]}})
