"""Read models for the web app — every number the UI shows, derived here.

Nothing in this module invents a figure. Health, day counts, money in and out,
deadlines, completion — each is computed from records that exist (timeline
events with quotes, artifacts with dates, tasks with evidence) and every payload
carries the source sha the evidence drawer needs. Where a number is derived
rather than recorded, the payload says so (``derived_from``).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mangotree.config.registry import PEOPLE, PROPERTIES, PROPERTY_INDEX
from mangotree.storage.mongo import Mongo

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731

COST_TYPES = {"funding", "construction"}          # money RKB put in / work billed
RETURN_TYPES = {"payment", "payoff"}               # money that came back


def ledger_money(mongo: Mongo, pid: str, summary: Optional[dict] = None) -> Dict[str, Any]:
    """The property's money block, from the ledger — never from summed mentions.

    ``invested`` / ``returned`` / ``billed`` are totals of verified ledger rows;
    ``owed`` is the latest documented balance with its as-of date. Any of them is
    None when the documents do not establish it, and the UI must say so rather
    than show 0. The old event-sum figures were wrong by an order of magnitude.
    """
    s = summary if summary is not None else mongo.db["ledger_summaries"].find_one({"property_id": pid}, {"_id": 0, "balances": 0, "sources": 0})
    if not s:
        return {"established": False, "invested": None, "returned": None, "billed": None, "owed": None, "owed_as_of": None,
                "derived_today": None, "risks": 0, "gaps": 0, "discrepancies": 0, "built_at": None,
                "derived_from": "ledger not built yet"}
    owed = s.get("owed") or {}
    return {
        "established": bool(s.get("established")),
        "invested": s.get("invested"), "returned": s.get("returned"), "billed": s.get("billed"),
        "owed": owed.get("owed_total"), "owed_as_of": owed.get("as_of"), "owed_source_sha": owed.get("source_sha"),
        "derived_today": s.get("derived_today"),
        "risks": len(s.get("risks") or []), "gaps": len(s.get("gaps") or []), "discrepancies": len(s.get("discrepancies") or []),
        "critical_risks": [r["title"] for r in (s.get("risks") or []) if r.get("severity") == "critical"][:3],
        "built_at": s.get("built_at"), "entries": s.get("entries"),
        "derived_from": "ledger of documented movements (RKB ledgers, settlement statements, invoices, payoff statements), each row quote-verified",
    }
RISK_TYPES = {"default", "legal"}

ART_LIST = {"_id": 0, "sha256": 1, "filename": 1, "subject": 1, "source_type": 1, "doc_class": 1, "date": 1,
            "property_ids": 1, "placement": 1, "common_topics": 1, "participants.from": 1, "participants.to": 1,
            "attachment_count": 1, "attachment_names": 1, "thread_key": 1, "raw_size": 1, "extension": 1,
            "content_type": 1, "text_len": 1, "resolution_status": 1, "segregation.confidence": 1,
            "segregation.reasoning": 1, "deal_address": 1, "is_inline_image": 1}


def clean(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if k != "_id"}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    try:
        from bson import ObjectId
        if isinstance(obj, ObjectId):
            return str(obj)
    except Exception:
        pass
    return obj


def artifact_row(r: dict) -> dict:
    d = r.get("date")
    return clean({
        "sha256": r.get("sha256"), "name": r.get("filename") or r.get("subject") or (r.get("sha256") or "")[:12],
        "subject": r.get("subject"), "filename": r.get("filename"), "source_type": r.get("source_type"),
        "doc_class": r.get("doc_class"), "date": d, "property_ids": r.get("property_ids") or [],
        "placement": r.get("placement"), "topics": r.get("common_topics") or [],
        "from": ((r.get("participants") or {}).get("from") or [None])[0],
        "to": ((r.get("participants") or {}).get("to") or [])[:4],
        "attachments": r.get("attachment_count") or 0, "attachment_names": r.get("attachment_names") or [],
        "thread_key": r.get("thread_key"), "size": r.get("raw_size"), "extension": r.get("extension"),
        "confidence": (r.get("segregation") or {}).get("confidence"),
        "reasoning": (r.get("segregation") or {}).get("reasoning"),
        "resolution_status": r.get("resolution_status"), "deal_address": r.get("deal_address"),
    })


# =============================================================================
# Properties
# =============================================================================

def _health(events: List[dict], today: datetime) -> Dict[str, Any]:
    reasons: List[str] = []
    level = "good"
    recent = today - timedelta(days=180)
    for e in events:
        d = e.get("occurred_at")
        if e.get("event_type") in RISK_TYPES and d and d >= recent:
            level = "critical"
            reasons.append(f"{e['event_type']}: {e.get('title')} ({d:%b %d, %Y})")
    if level != "critical":
        for e in events:
            d = e.get("occurred_at")
            t = (e.get("title") or "").lower()
            if e.get("event_type") == "extension" and d and d >= today - timedelta(days=365):
                level = "watch"
                reasons.append(f"extension: {e.get('title')} ({d:%b %d, %Y})")
            if ("matur" in t) and d and d < today and d >= today - timedelta(days=365):
                level = "watch" if level == "good" else level
                reasons.append(f"maturity passed: {e.get('title')} ({d:%b %d, %Y})")
    return {"level": level, "reasons": reasons[:4], "derived_from": "timeline events (default/legal/extension/maturity)"}


def property_summary(mongo: Mongo, pid: str, *, today: Optional[datetime] = None) -> Dict[str, Any]:
    """One property — served from the bulk portfolio pass, which is both faster
    and guaranteed to agree with the grid."""
    for row in portfolio(mongo):
        if row["property_id"] == pid:
            return row
    return _property_summary_slow(mongo, pid, today=today)


def _property_summary_slow(mongo: Mongo, pid: str, *, today: Optional[datetime] = None) -> Dict[str, Any]:
    today = today or NOW()
    prop = PROPERTY_INDEX.get(pid)
    row = mongo.properties.find_one({"property_id": pid}) or {}
    art = mongo.artifacts
    base = {"property_ids": pid, "is_inline_image": {"$ne": True}}
    counts = {r["_id"]: r["n"] for r in art.aggregate([{"$match": base}, {"$group": {"_id": "$source_type", "n": {"$sum": 1}}}])}
    total = sum(counts.values())
    last = art.find_one(base, {"date": 1}, sort=[("date", -1)])
    first = art.find_one(base, {"date": 1}, sort=[("date", 1)])

    ev = list(mongo.db["timeline_events"].find({"property_id": pid}, {"_id": 0, "embedding": 0}).sort("occurred_at", 1))
    orig = next((e for e in ev if e.get("event_type") == "origination" and e.get("occurred_at")), None)
    start = (orig or {}).get("occurred_at") or (first or {}).get("date")
    days = (today - start).days if start else None

    upcoming = [e for e in ev if e.get("occurred_at") and today <= e["occurred_at"] <= today + timedelta(days=60)]
    overdue_like = [e for e in ev if e.get("occurred_at") and e.get("event_type") in ("default", "legal") and e["occurred_at"] >= today - timedelta(days=90)]

    tasks = mongo.db["tasks"]
    t_open = tasks.count_documents({"property_id": pid, "status": "open"})
    t_sugg = tasks.count_documents({"property_id": pid, "status": "suggested"})
    t_done = tasks.count_documents({"property_id": pid, "status": "done"})
    wes = list(mongo.db["wes_work"].find({"property_id": pid}, {"_id": 0}))
    wes_done = sum(1 for w in wes if w.get("status") == "done")

    return clean({
        "property_id": pid,
        "address": prop.canonical_address if prop else row.get("canonical_address"),
        "city": row.get("city"), "state": row.get("state"), "deal_type": getattr(prop, "deal_type", None),
        "status": row.get("status") or "active",
        "documents": {"total": total, **counts},
        "first_activity": (first or {}).get("date"), "last_activity": (last or {}).get("date"),
        "day_count": days, "started": start,
        "events": len(ev),
        "health": _health(ev, today),
        "money": ledger_money(mongo, pid),
        "upcoming": [{"date": e["occurred_at"], "type": e["event_type"], "title": e["title"], "source_sha": e.get("source_sha")} for e in upcoming[:6]],
        "risk_events": [{"date": e["occurred_at"], "type": e["event_type"], "title": e["title"], "source_sha": e.get("source_sha")} for e in overdue_like[:6]],
        "tasks": {"open": t_open, "suggested": t_sugg, "done": t_done},
        "wes": {"total": len(wes), "done": wes_done, "remaining": sum(1 for w in wes if w.get("status") in ("remaining", "in_progress", "blocked"))},
    })


def timeline(mongo: Mongo, pid: str, *, types: Optional[List[str]] = None, start: Optional[datetime] = None,
             end: Optional[datetime] = None, as_of: Optional[datetime] = None, q: Optional[str] = None, limit: int = 600) -> List[dict]:
    query: Dict[str, Any] = {"property_id": pid}
    if types:
        query["event_type"] = {"$in": types}
    rng: Dict[str, Any] = {}
    if start:
        rng["$gte"] = start
    if end:
        rng["$lte"] = end
    if as_of:
        rng["$lte"] = min(as_of, end) if end else as_of
    if rng:
        query["occurred_at"] = rng
    if q:
        query["$or"] = [{"title": {"$regex": re.escape(q), "$options": "i"}}, {"detail": {"$regex": re.escape(q), "$options": "i"}},
                        {"quote": {"$regex": re.escape(q), "$options": "i"}}, {"source_name": {"$regex": re.escape(q), "$options": "i"}}]
    # Only what the timeline renders; ``detail`` and run metadata were 40% of a 400 KB payload.
    proj = {"_id": 0, "event_id": 1, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "amount": 1,
            "source_sha": 1, "source_ref": 1, "source_name": 1, "quote": 1, "confidence": 1, "extracted_by": 1, "date_basis": 1}
    rows = list(mongo.db["timeline_events"].find(query, proj).sort("occurred_at", -1).limit(limit))
    for r in rows:
        if r.get("quote") and len(r["quote"]) > 240:
            r["quote"] = r["quote"][:240] + "…"
    return clean(rows)


def documents(mongo: Mongo, pid: str, *, placement: Optional[str] = None, source_type: Optional[str] = None,
              q: Optional[str] = None, limit: int = 400) -> Dict[str, Any]:
    if placement in ("portfolio", "unplaced"):
        query: Dict[str, Any] = {"placement": placement}
    else:
        query = {"property_ids": pid}
    query["is_inline_image"] = {"$ne": True}
    if source_type:
        query["source_type"] = source_type
    if q:
        query["$or"] = [{"filename": {"$regex": re.escape(q), "$options": "i"}}, {"subject": {"$regex": re.escape(q), "$options": "i"}}]
    rows = list(mongo.artifacts.find(query, ART_LIST).sort("date", -1).limit(limit))
    inv = Counter(r.get("doc_class") or r.get("source_type") for r in rows)
    return {"items": [artifact_row(r) for r in rows], "inventory": dict(inv.most_common()), "total": mongo.artifacts.count_documents(query)}


def comms(mongo: Mongo, pid: str, *, q: Optional[str] = None, limit: int = 300) -> List[dict]:
    query: Dict[str, Any] = {"property_ids": pid, "source_type": "email"}
    if q:
        query["$or"] = [{"subject": {"$regex": re.escape(q), "$options": "i"}}, {"participants.from": {"$regex": re.escape(q), "$options": "i"}}]
    rows = list(mongo.artifacts.find(query, ART_LIST).sort("date", -1).limit(limit))
    return [artifact_row(r) for r in rows]


def thread(mongo: Mongo, thread_key: str) -> Dict[str, Any]:
    msgs = list(mongo.artifacts.find({"thread_key": thread_key, "source_type": "email"},
                                     {**ART_LIST, "body_clean": 1, "person_ids": 1}).sort("date", 1))
    shas = [m["sha256"] for m in msgs]
    atts = list(mongo.artifacts.find({"source_types": "attachment", "parent_email_shas": {"$in": shas}},
                                     {**ART_LIST, "parent_email_shas": 1, "text_len": 1}))
    by_parent: Dict[str, List[dict]] = defaultdict(list)
    for a in atts:
        for p in a.get("parent_email_shas") or []:
            if p in shas and not a.get("is_inline_image"):
                by_parent[p].append(artifact_row(a))
    events = {e["source_sha"]: e for e in mongo.db["timeline_events"].find({"source_sha": {"$in": shas}}, {"_id": 0, "source_sha": 1, "occurred_at": 1, "event_type": 1, "title": 1})}
    out = []
    for m in msgs:
        row = artifact_row(m)
        row["body"] = m.get("body_clean") or ""
        row["attachments_list"] = by_parent.get(m["sha256"], [])
        row["timeline_event"] = clean(events.get(m["sha256"]))
        out.append(row)
    t = mongo.threads.find_one({"thread_key": thread_key}, {"_id": 0}) or {}
    return {"thread_key": thread_key, "subject": t.get("last_subject") or (msgs[0].get("subject") if msgs else ""),
            "messages": out, "participants": clean(t.get("participants") or []), "property_ids": t.get("property_ids") or []}


def money(mongo: Mongo, pid: Optional[str]) -> Dict[str, Any]:
    q: Dict[str, Any] = {"amount": {"$type": "number"}}
    if pid:
        q["property_id"] = pid
    proj = {"_id": 0, "event_id": 1, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "amount": 1,
            "source_sha": 1, "source_name": 1, "quote": 1}
    rows = list(mongo.db["timeline_events"].find(q, proj).sort("occurred_at", 1))
    by_month: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_type: Dict[str, float] = defaultdict(float)
    for e in rows:
        d = e.get("occurred_at")
        key = d.strftime("%Y-%m") if hasattr(d, "strftime") else "undated"
        by_month[key][e.get("event_type") or "other"] += float(e["amount"])
        by_type[e.get("event_type") or "other"] += float(e["amount"])
    series = [{"month": k, **{t: round(v, 2) for t, v in v_.items()}} for k, v_ in sorted(by_month.items())]
    cost = sum(v for t, v in by_type.items() if t in COST_TYPES)
    back = sum(v for t, v in by_type.items() if t in RETURN_TYPES)
    return clean({
        "events": rows[-400:], "series": series, "by_type": dict(by_type),
        "cost": cost, "returned": back, "net": back - cost,
        "derived_from": "timeline events carrying an amount; each row links to its source document",
    })


# =============================================================================
# Portfolio / dashboard
# =============================================================================

_PORTFOLIO_CACHE: Dict[str, Any] = {"at": None, "rows": None, "refreshing": False}
PORTFOLIO_TTL_S = 300
_PORTFOLIO_LOCK = __import__("threading").Lock()


def portfolio(mongo: Mongo, *, fresh: bool = False) -> List[Dict[str, Any]]:
    """All properties from five bulk queries — served from cache, refreshed behind it.

    Stale-while-revalidate: once the grid has been computed, every request gets
    the cached rows immediately; when they are older than the TTL (or a write
    invalidated them) one background thread recomputes and swaps them in. Only
    the very first call after startup blocks, and startup warms it. Recompute
    is ~5s against Atlas from here, which is far too long to sit in front of a
    page load.
    """
    now = NOW()
    rows = _PORTFOLIO_CACHE["rows"]
    age = (now - _PORTFOLIO_CACHE["at"]).total_seconds() if _PORTFOLIO_CACHE["at"] else None
    if rows and not fresh:
        if age is not None and age > PORTFOLIO_TTL_S:
            _refresh_portfolio_async(mongo)
        return rows
    return _compute_portfolio(mongo)


def _refresh_portfolio_async(mongo: Mongo) -> None:
    import threading
    with _PORTFOLIO_LOCK:
        if _PORTFOLIO_CACHE["refreshing"]:
            return
        _PORTFOLIO_CACHE["refreshing"] = True

    def run():
        try:
            _compute_portfolio(mongo)
        finally:
            _PORTFOLIO_CACHE["refreshing"] = False
    threading.Thread(target=run, daemon=True, name="portfolio-refresh").start()


def _compute_portfolio(mongo: Mongo) -> List[Dict[str, Any]]:
    now = NOW()
    today = now
    pids = [p.property_id for p in PROPERTIES]
    art = mongo.artifacts

    docs: Dict[str, Dict[str, int]] = defaultdict(dict)
    span: Dict[str, Dict[str, Any]] = {}
    for r in art.aggregate([
        {"$match": {"property_ids": {"$in": pids}, "is_inline_image": {"$ne": True}}},
        {"$unwind": "$property_ids"},
        {"$match": {"property_ids": {"$in": pids}}},
        {"$group": {"_id": {"p": "$property_ids", "s": "$source_type"}, "n": {"$sum": 1},
                    "first": {"$min": "$date"}, "last": {"$max": "$date"}}},
    ]):
        p, s = r["_id"]["p"], r["_id"]["s"]
        docs[p][s] = r["n"]
        sp = span.setdefault(p, {"first": r["first"], "last": r["last"]})
        if r["first"] and (not sp["first"] or r["first"] < sp["first"]):
            sp["first"] = r["first"]
        if r["last"] and (not sp["last"] or r["last"] > sp["last"]):
            sp["last"] = r["last"]

    events: Dict[str, List[dict]] = defaultdict(list)
    for e in mongo.db["timeline_events"].find(
        {"property_id": {"$in": pids}},
        {"_id": 0, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "amount": 1, "source_sha": 1},
    ).sort("occurred_at", 1):
        events[e["property_id"]].append(e)

    tcounts: Dict[str, Dict[str, int]] = defaultdict(dict)
    for r in mongo.db["tasks"].aggregate([{"$match": {"property_id": {"$in": pids}}},
                                          {"$group": {"_id": {"p": "$property_id", "s": "$status"}, "n": {"$sum": 1}}}]):
        tcounts[r["_id"]["p"]][r["_id"]["s"]] = r["n"]

    wes: Dict[str, Dict[str, int]] = defaultdict(dict)
    for r in mongo.db["wes_work"].aggregate([{"$group": {"_id": {"p": "$property_id", "s": "$status"}, "n": {"$sum": 1}}}]):
        wes[r["_id"]["p"]][r["_id"]["s"]] = r["n"]

    rows_meta = {r["property_id"]: r for r in mongo.properties.find({"property_id": {"$in": pids}}, {"_id": 0})}
    ledgers = {r["property_id"]: r for r in mongo.db["ledger_summaries"].find({}, {"_id": 0, "balances": 0, "sources": 0})}

    out: List[Dict[str, Any]] = []
    floor = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for prop in PROPERTIES:
        pid = prop.property_id
        ev = events.get(pid, [])
        row = rows_meta.get(pid, {})
        counts = docs.get(pid, {})
        first = (span.get(pid) or {}).get("first")
        last = (span.get(pid) or {}).get("last")
        # Origination inside the corpus window; a 2012 deed reference in a title
        # package is history, not the start of this loan.
        orig = next((e for e in ev if e.get("event_type") == "origination" and e.get("occurred_at") and e["occurred_at"] >= floor), None)
        start = (orig or {}).get("occurred_at") or first
        upcoming = [e for e in ev if e.get("occurred_at") and today <= e["occurred_at"] <= today + timedelta(days=60)]
        risk = [e for e in ev if e.get("occurred_at") and e.get("event_type") in RISK_TYPES and e["occurred_at"] >= today - timedelta(days=90)]
        tc = tcounts.get(pid, {})
        w = wes.get(pid, {})
        w_total = sum(w.values())
        out.append(clean({
            "property_id": pid, "address": prop.canonical_address, "city": row.get("city"), "state": row.get("state"),
            "deal_type": getattr(prop, "deal_type", None), "status": row.get("status") or "active",
            "documents": {"total": sum(counts.values()), **counts},
            "first_activity": first, "last_activity": last,
            "day_count": (today - start).days if start else None, "started": start,
            "events": len(ev), "health": _health(ev, today),
            "money": ledger_money(mongo, pid, ledgers.get(pid)),
            "upcoming": [{"date": e["occurred_at"], "type": e["event_type"], "title": e["title"], "source_sha": e.get("source_sha")} for e in upcoming[:6]],
            "risk_events": [{"date": e["occurred_at"], "type": e["event_type"], "title": e["title"], "source_sha": e.get("source_sha")} for e in risk[-6:]],
            "tasks": {"open": tc.get("open", 0), "suggested": tc.get("suggested", 0), "done": tc.get("done", 0)},
            "wes": {"total": w_total, "done": w.get("done", 0), "remaining": w_total - w.get("done", 0)},
        }))
    _PORTFOLIO_CACHE.update({"at": now, "rows": out})
    return out


def invalidate_portfolio() -> None:
    """Mark stale; the next read serves the old rows and refreshes behind them."""
    _PORTFOLIO_CACHE["at"] = datetime(2000, 1, 1, tzinfo=timezone.utc)


def handled_overnight(mongo: Mongo, *, hours: int = 36) -> List[Dict[str, Any]]:
    from concurrent.futures import ThreadPoolExecutor

    since = NOW() - timedelta(hours=hours)
    art, chunks = mongo.artifacts, mongo.chunks
    specs = [
        ("Documents filed by Opus 5 (property segregation)", "segregation", lambda: art.count_documents({"segregation.decided_at": {"$gte": since}})),
        ("Common-store items sorted (portfolio vs business)", "classification", lambda: art.count_documents({"common_classification.decided_at": {"$gte": since}})),
        ("Timeline events written (quote-verified)", "timeline", lambda: mongo.db["timeline_events"].count_documents({"updated_at": {"$gte": since}})),
        ("Chunks re-embedded with questions", "embedding", lambda: chunks.count_documents({"embedded_at": {"$gte": since}})),
        ("Tasks extracted by Opus 5", "tasks", lambda: mongo.db["tasks"].count_documents({"source": "ai_extracted", "created_at": {"$gte": since}})),
        ("Deals outside the registry catalogued", "registry", lambda: mongo.review_queue.count_documents({"kind": "registry_candidate"})),
        ("Answers produced", "answers", lambda: mongo.db["jobs"].count_documents({"kind": "answer", "status": "done", "created_at": {"$gte": since}})),
        ("Open items closed by the records (tasks, cards, Wes issues)", "resolved",
         lambda: mongo.db["tasks"].count_documents({"resolution.by": "evidence", "done_at": {"$gte": since}})
                 + mongo.db["cards"].count_documents({"status": "resolved", "resolved_at": {"$gte": since}})
                 + sum(1 for a in mongo.db["wes_agenda"].find({"issues.resolved_at": {"$gte": since}}, {"issues.resolved_at": 1})
                       for i in a.get("issues", []) if i.get("resolved_at") and i["resolved_at"] >= since)),
    ]
    with ThreadPoolExecutor(max_workers=len(specs)) as pool:
        counts = list(pool.map(lambda s: s[2](), specs))
    return [{"label": s[0], "kind": s[1], "count": n} for s, n in zip(specs, counts) if n]


def needs_attention(mongo: Mongo, user_id: str) -> Dict[str, Any]:
    """Nine independent reads, in parallel: Atlas round trips here cost ~0.5s
    each, so serially this was 2.5s and in a pool it is one."""
    from concurrent.futures import ThreadPoolExecutor

    today = NOW()
    tasks = mongo.db["tasks"]
    owner = {"rakesh": "Rakesh", "jp": "JP", "manjunath": "Manjunath"}.get(user_id)
    ev = mongo.db["timeline_events"]
    jobs = {
        "unplaced": lambda: mongo.artifacts.count_documents({"placement": "unplaced"}),
        "oldest": lambda: mongo.artifacts.find_one({"placement": "unplaced"}, {"date": 1}, sort=[("date", 1)]),
        "low_conf": lambda: mongo.artifacts.count_documents({"resolution_status": "needs_review", "placement": "property"}),
        "overdue": lambda: list(tasks.find({"status": "open", "due": {"$lt": today}}, {"_id": 0}).sort("due", 1).limit(20)),
        "suggested": lambda: tasks.count_documents({"status": "suggested"}),
        "mine_open": lambda: tasks.count_documents({"status": "open", "owner": owner}) if owner else 0,
        "deadlines": lambda: list(ev.find({"occurred_at": {"$gte": today, "$lte": today + timedelta(days=21)}},
                                          {"_id": 0, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "source_sha": 1, "amount": 1}).sort("occurred_at", 1).limit(30)),
        "risks": lambda: list(ev.find({"event_type": {"$in": ["default", "legal"]}, "occurred_at": {"$gte": today - timedelta(days=60)}},
                                      {"_id": 0, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "source_sha": 1}).sort("occurred_at", -1).limit(20)),
        "unverified": lambda: list(mongo.db["chats"].aggregate([
            {"$unwind": "$messages"}, {"$match": {"messages.role": "assistant", "messages.answer.verification.unverified.0": {"$exists": True}}},
            {"$project": {"_id": 0, "chat_id": 1, "property_id": 1, "at": "$messages.at", "n": {"$size": "$messages.answer.verification.unverified"}, "headline": "$messages.answer.headline"}},
            {"$sort": {"at": -1}}, {"$limit": 10}])),
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futs = {k: pool.submit(fn) for k, fn in jobs.items()}
        r = {k: f.result() for k, f in futs.items()}
    return clean({
        "unplaced": {"count": r["unplaced"], "oldest": (r["oldest"] or {}).get("date")},
        "low_confidence": r["low_conf"],
        "overdue_tasks": r["overdue"], "suggested_tasks": r["suggested"], "my_open_tasks": r["mine_open"],
        "deadlines": r["deadlines"], "risk_events": r["risks"], "answers_with_unverified": r["unverified"],
    })


# =============================================================================
# People / CRM
# =============================================================================

def people(mongo: Mongo, *, q: Optional[str] = None, property_id: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = list(mongo.people.find({}, {"_id": 0}))
    ents = {e.get("registry_id"): e for e in mongo.db["entities"].find({"registry_id": {"$exists": True}}, {"_id": 0})}
    out = []
    for p in rows:
        e = ents.get(p["person_id"]) or {}
        props = e.get("property_ids") or []
        if property_id and property_id not in props:
            continue
        if q and q.lower() not in (p.get("display_name", "") + " " + p.get("org", "") + " " + p.get("role", "") + " ".join(p.get("addresses") or [])).lower():
            continue
        out.append(clean({**p, "properties": props, "mentions": e.get("mention_count") or 0,
                          "first_seen": e.get("first_seen"), "last_seen": e.get("last_seen"), "entity_id": e.get("entity_id")}))
    out.sort(key=lambda x: (-(x.get("mentions") or 0), x.get("display_name") or ""))
    return out


def person_detail(mongo: Mongo, person_id: str) -> Dict[str, Any]:
    p = mongo.people.find_one({"person_id": person_id}, {"_id": 0}) or {}
    addrs = [a.lower() for a in (p.get("addresses") or []) + (p.get("send_as") or [])]
    q = {"$or": [{"participants.from": {"$in": addrs}}, {"participants.to": {"$in": addrs}}, {"participants.cc": {"$in": addrs}}]} if addrs else {"person_ids": person_id}
    mails = list(mongo.artifacts.find({**q, "source_type": "email"}, ART_LIST).sort("date", -1).limit(80))
    per_prop = Counter(pid for m in mails for pid in (m.get("property_ids") or []))
    e = mongo.db["entities"].find_one({"registry_id": person_id}, {"_id": 0}) or {}
    edges = list(mongo.db["entity_edges"].find({"$or": [{"src": e.get("entity_id")}, {"dst": e.get("entity_id")}]}, {"_id": 0}).limit(60)) if e else []
    return clean({"person": p, "entity": e, "emails": [artifact_row(m) for m in mails], "by_property": dict(per_prop.most_common()),
                  "edges": edges, "total_emails": mongo.artifacts.count_documents({**q, "source_type": "email"})})


# =============================================================================
# Evidence
# =============================================================================

def chunk_evidence(mongo: Mongo, chunk_id: str) -> Optional[Dict[str, Any]]:
    c = mongo.chunks.find_one({"chunk_id": chunk_id}, {"_id": 0, "embedding": 0})
    if not c:
        return None
    a = mongo.artifacts.find_one({"sha256": c["artifact_sha"]}, ART_LIST) or {}
    return clean({"chunk": c, "artifact": artifact_row(a) if a else None,
                  "has_original": bool(a), "questions": c.get("questions") or []})


def artifact_evidence(mongo: Mongo, sha: str) -> Optional[Dict[str, Any]]:
    a = mongo.artifacts.find_one({"sha256": {"$regex": f"^{re.escape(sha)}"}}, {**ART_LIST, "body_clean": 1, "text": 1, "parent_email_shas": 1, "source_paths": 1, "relative_path": 1})
    if not a:
        return None
    chunks = list(mongo.chunks.find({"artifact_sha": a["sha256"]}, {"_id": 0, "chunk_id": 1, "ordinal": 1, "text": 1, "source_ref": 1, "tier1": 1}).sort("ordinal", 1))
    events = list(mongo.db["timeline_events"].find({"source_sha": a["sha256"]}, {"_id": 0}).sort("occurred_at", 1))
    parents = list(mongo.artifacts.find({"sha256": {"$in": a.get("parent_email_shas") or []}}, ART_LIST).sort("date", 1))
    return clean({"artifact": {**artifact_row(a), "body": a.get("body_clean") or "", "text": (a.get("text") or "")[:200_000],
                               "paths": a.get("source_paths") or ([a["relative_path"]] if a.get("relative_path") else [])},
                  "chunks": chunks, "events": events, "carried_by": [artifact_row(p) for p in parents]})
