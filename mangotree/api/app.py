"""The API. Run with:  uvicorn mangotree.api.app:app --reload --port 8000

Every route requires a signed-in user except /auth/login and /health. Long work
(answers, task extraction) runs as jobs with SSE streams; everything else is a
direct read or a small audited write.
"""
from __future__ import annotations

import io
import mimetypes
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from mangotree.config.registry import PROPERTIES, PROPERTY_INDEX
from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.core.sources import DOCUMENT_SOURCE_TYPES
from mangotree.review.decisions import apply_human_decision
from mangotree.retrieve.scope import Scope
from mangotree.storage.mongo import get_mongo
from mangotree.tasks.store import OWNERS, TaskStore

from . import data
from .auth import CurrentUser, change_password, ensure_users, login, logout
from .jobs import JobRunner, _jsonable

app = FastAPI(title="MangoTree API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

mongo = get_mongo()
ensure_users(mongo)
jobs = JobRunner(mongo)
_orphans = jobs.sweep_orphans()
if _orphans:
    logger.warning("%d job(s) left running by the previous process marked failed", _orphans)
tasks = TaskStore(mongo)
_panel = None


def _ensure_api_indexes() -> None:
    """Indexes the read models lean on. Idempotent; cheap on these sizes."""
    a, c, ev = mongo.artifacts, mongo.chunks, mongo.db["timeline_events"]
    wanted = [
        (a, "placement", {"name": "ix_placement"}),
        (a, [("placement", 1), ("date", -1)], {"name": "ix_placement_date"}),
        (a, "deal_address", {"name": "ix_deal_address", "sparse": True}),
        (a, "segregation.decided_at", {"name": "ix_seg_decided", "sparse": True}),
        (a, "common_classification.decided_at", {"name": "ix_cc_decided", "sparse": True}),
        (a, "created_at", {"name": "ix_art_created"}),
        (c, "embedded_at", {"name": "ix_embedded_at", "sparse": True}),
        (ev, [("property_id", 1), ("occurred_at", 1)], {"name": "ix_ev_prop_date"}),
        (ev, "occurred_at", {"name": "ix_ev_date"}),
        (ev, "source_sha", {"name": "ix_ev_source"}),
        (ev, [("event_type", 1), ("occurred_at", -1)], {"name": "ix_ev_type_date"}),
        (ev, "updated_at", {"name": "ix_ev_updated", "sparse": True}),
        (mongo.db["chats"], "chat_id", {"name": "ux_chat_id", "unique": True}),
        (mongo.db["remember_notes"], [("scope", 1), ("property_id", 1), ("active", 1)], {"name": "ix_notes_scope"}),
        (mongo.db["saved_answers"], [("property_id", 1), ("saved_at", -1)], {"name": "ix_saved"}),
    ]
    for coll, keys, opts in wanted:
        try:
            coll.create_index(keys, **opts)
        except Exception as exc:  # an existing equivalent index is fine; never block startup
            logger.debug("index %s: %s", opts.get("name"), exc)


_ensure_api_indexes()

# Warm the portfolio grid so the first dashboard open is not the slow one.
import threading as _threading  # noqa: E402
_threading.Thread(target=lambda: data.portfolio(mongo), daemon=True, name="warm-portfolio").start()

# Standing jobs: briefing before 06:00 local, change-detection cards hourly.
from mangotree.briefing.cards import CardDetector  # noqa: E402
from mangotree.briefing.morning import Briefing, Scheduler  # noqa: E402
from . import exports  # noqa: E402

_scheduler = Scheduler(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
_scheduler.start()
_threading.Thread(target=lambda: (data.portfolio(mongo), _warm_caches()), daemon=True, name="warm-caches").start()
_cards = CardDetector(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
_briefing = Briefing(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)

_SMALL_CACHE: Dict[str, Any] = {}
_REFRESHING: set = set()
_CACHE_LOCK = _threading.Lock()


def _cached(key: str, ttl: int, fn):
    """Stale-while-revalidate. A page never waits on a recompute once a value
    exists: the old value is returned and one thread refreshes it behind the
    request. Only the very first call blocks."""
    now = datetime.now(timezone.utc)
    hit = _SMALL_CACHE.get(key)
    if hit is None:
        v = fn()
        _SMALL_CACHE[key] = {"at": now, "v": v}
        return v
    if (now - hit["at"]).total_seconds() >= ttl:
        with _CACHE_LOCK:
            start = key not in _REFRESHING
            if start:
                _REFRESHING.add(key)
        if start:
            def run():
                try:
                    _SMALL_CACHE[key] = {"at": datetime.now(timezone.utc), "v": fn()}
                except Exception as exc:
                    logger.warning("cache refresh %s failed: %s", key, exc)
                finally:
                    with _CACHE_LOCK:
                        _REFRESHING.discard(key)
            _threading.Thread(target=run, daemon=True, name=f"cache-{key}").start()
    return hit["v"]


def _warm_caches() -> None:
    """First-open latency belongs to the server, not the reader."""
    try:
        _cached("handled", 300, lambda: data.handled_overnight(mongo))
        _cached("money_all", 120, lambda: data.money(mongo, None) | {"events": []})
        _cached("degrades", 300, _degrades)
        _cached("task_counts", 30, tasks.counts)
        _cached("unplaced_count", 60, lambda: mongo.artifacts.count_documents({"placement": "unplaced"}))
    except Exception as exc:
        logger.warning("cache warm: %s", exc)


def panel():
    global _panel
    if _panel is None:
        from mangotree.agent.panel import AnswerPanel
        _panel = AnswerPanel(mongo, anthropic_api_key=SETTINGS.anthropic_api_key,
                             voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "")
    return _panel


def _dt(v: Optional[str]) -> Optional[datetime]:
    if not v:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m"):
        try:
            return datetime.strptime(v[:19] if "T" in v else v, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _pid(pid: str) -> str:
    if pid not in PROPERTY_INDEX:
        raise HTTPException(404, f"unknown property {pid}")
    return pid


# =============================================================================
# auth
# =============================================================================

class LoginBody(BaseModel):
    user_id: str
    password: str


class PasswordBody(BaseModel):
    current: str
    new: str


@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.post("/auth/login")
def auth_login(body: LoginBody, response: Response):
    return login(mongo, body.user_id, body.password, response)


@app.post("/auth/logout")
def auth_logout(response: Response):
    logout(response)
    return {"ok": True}


@app.get("/auth/me")
def auth_me(user=CurrentUser):
    return user


@app.post("/auth/password")
def auth_password(body: PasswordBody, user=CurrentUser):
    change_password(mongo, user["user_id"], body.current, body.new)
    return {"ok": True}


# =============================================================================
# properties
# =============================================================================

@app.get("/properties")
def list_properties(user=CurrentUser):
    return data.portfolio(mongo)


@app.get("/properties/{pid}")
def get_property(pid: str, user=CurrentUser):
    return data.property_summary(mongo, _pid(pid))


@app.get("/properties/{pid}/timeline")
def get_timeline(pid: str, types: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                 as_of: Optional[str] = None, q: Optional[str] = None, user=CurrentUser):
    return data.timeline(mongo, _pid(pid), types=[t for t in (types or "").split(",") if t] or None,
                         start=_dt(start), end=_dt(end), as_of=_dt(as_of), q=q)


@app.get("/properties/{pid}/documents")
def get_documents(pid: str, placement: Optional[str] = None, source_type: Optional[str] = None, q: Optional[str] = None, user=CurrentUser):
    return data.documents(mongo, _pid(pid), placement=placement, source_type=source_type, q=q)


@app.get("/properties/{pid}/comms")
def get_comms(pid: str, q: Optional[str] = None, user=CurrentUser):
    return data.comms(mongo, _pid(pid), q=q)


@app.get("/properties/{pid}/money")
def get_money(pid: str, user=CurrentUser):
    return data.money(mongo, _pid(pid))


@app.get("/properties/{pid}/wes")
def get_wes(pid: str, user=CurrentUser):
    rows = list(mongo.db["wes_work"].find({"property_id": _pid(pid)}, {"_id": 0}).sort([("status", 1), ("due", 1)]))
    order = {"blocked": 0, "in_progress": 1, "remaining": 2, "done": 3}
    rows.sort(key=lambda r: order.get(r.get("status"), 9))
    return data.clean({"items": rows, "done": sum(1 for r in rows if r["status"] == "done"), "total": len(rows)})


@app.get("/properties/{pid}/files")
def get_files(pid: str, user=CurrentUser):
    """The property's documents as a folder tree — the E-drive layout plus an
    'Email attachments' folder — each leaf opening the exact original."""
    _pid(pid)
    rows = list(mongo.artifacts.find(
        {"property_ids": pid, "is_inline_image": {"$ne": True}, "source_type": {"$in": list(DOCUMENT_SOURCE_TYPES)}},
        {**data.ART_LIST, "relative_path": 1, "source_paths": 1, "parent_email_shas": 1, "raw_size": 1, "uploads": 1}).sort("filename", 1))
    tree: Dict[str, Any] = {}
    for r in rows:
        rel = r.get("relative_path") or (r.get("source_paths") or [None])[0]
        if r.get("source_type") == "upload":
            folder = ["Uploaded here"]
        elif rel:
            parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
            folder = parts[:-1] or ["(root)"]
        else:
            folder = ["Email attachments"]
        node = tree
        for f in folder:
            node = node.setdefault(f, {"__files": []}) if f not in node else node[f]
            node.setdefault("__files", [])
        # Upload provenance: who added it here and when (first upload to this property).
        up = next((u for u in (r.get("uploads") or []) if u.get("property_id") == pid), None) or ((r.get("uploads") or [None])[0])
        node["__files"].append({**data.artifact_row(r), "size": r.get("raw_size"),
                                "uploaded_by": _user_name(up.get("by")) if up else None, "uploaded_at": up.get("at") if up else None})
    def shape(node: Dict[str, Any], name: str) -> Dict[str, Any]:
        children = [shape(v, k) for k, v in node.items() if k != "__files"]
        files = node.get("__files", [])
        return {"name": name, "files": files, "children": sorted(children, key=lambda c: c["name"].lower()),
                "count": len(files) + sum(c["count"] for c in children)}
    root = shape(tree, PROPERTY_INDEX[pid].canonical_address)
    return data.clean({"tree": root, "total": len(rows), "store": type(mongo_store()).__name__})


def mongo_store():
    from mangotree.storage.objectstore import get_object_store
    return get_object_store()


_USER_NAMES: Dict[str, str] = {}


def _user_name(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    if user_id not in _USER_NAMES:
        u = mongo.db["users"].find_one({"user_id": user_id}, {"name": 1})
        _USER_NAMES[user_id] = (u or {}).get("name") or user_id
    return _USER_NAMES[user_id]


@app.post("/properties/{pid}/upload")
async def upload_documents(pid: str, files: List[UploadFile] = File(...), note: Optional[str] = Form(None), user=CurrentUser):
    """Add documents to this property. Stored and deduplicated immediately; then a
    job runs the same chain every other document went through and streams progress."""
    from mangotree.ingest.upload import UploadIngestor
    _pid(pid)
    ingestor = UploadIngestor(mongo)
    results, to_process, failed = [], [], []
    for f in files:
        payload = await f.read()
        try:
            r = ingestor.ingest(payload, filename=f.filename or "upload", property_id=pid, uploaded_by=user["user_id"],
                                note=note, content_type=f.content_type)
            results.append(r.as_dict())
            if r.needs_processing:
                to_process.append(r.sha256)
        except ValueError as exc:
            failed.append({"filename": f.filename, "error": str(exc)})
    mongo.db["upload_log"].insert_one({"by": user["user_id"], "at": datetime.now(timezone.utc), "property_id": pid,
                                       "results": results, "failed": failed, "note": note})
    _SMALL_CACHE.pop("handled", None)

    job_id = None
    if to_process:
        def run(job):
            job.emit("status", {"text": f"{len(to_process)} document(s) stored under {PROPERTY_INDEX[pid].canonical_address}"})
            out = _scheduler.chain.process_documents(to_process, pid, emit=job.emit)
            job.emit("status", {"text": "Tasks and what's-new cards…"})
            _scheduler.chain.flush_debounced(force=True)
            for k in ("handled", "task_counts"):
                _SMALL_CACHE.pop(k, None)
            data.invalidate_portfolio()
            return data.clean(out)
        job_id = jobs.start("upload", {"property_id": pid, "by": user["user_id"], "files": len(to_process)}, run).job_id
    return data.clean({"results": results, "failed": failed, "job_id": job_id})


@app.get("/threads/{thread_key:path}")
def get_thread(thread_key: str, user=CurrentUser):
    return data.thread(mongo, thread_key)


@app.get("/money")
def get_money_all(user=CurrentUser):
    return data.money(mongo, None)


# =============================================================================
# evidence
# =============================================================================

@app.get("/evidence/chunk/{chunk_id}")
def evidence_chunk(chunk_id: str, user=CurrentUser):
    out = data.chunk_evidence(mongo, chunk_id)
    if not out:
        raise HTTPException(404, "chunk not found")
    return out


@app.get("/evidence/artifact/{sha}")
def evidence_artifact(sha: str, user=CurrentUser):
    out = data.artifact_evidence(mongo, sha)
    if not out:
        raise HTTPException(404, "artifact not found")
    return out


@app.get("/evidence/original/{sha}")
def evidence_original(sha: str, user=CurrentUser):
    a = mongo.artifacts.find_one({"sha256": {"$regex": f"^{re.escape(sha)}"}}, {"sha256": 1, "filename": 1, "content_type": 1, "source_type": 1})
    if not a:
        raise HTTPException(404, "artifact not found")
    raw = mongo.get_original(a["sha256"])
    if raw is None:
        raise HTTPException(404, "original bytes not in the store")
    name = a.get("filename") or (f"{a['sha256'][:12]}.eml" if a.get("source_type") == "email" else a["sha256"][:12])
    ctype = a.get("content_type") or mimetypes.guess_type(name)[0] or ("message/rfc822" if name.endswith(".eml") else "application/octet-stream")
    return StreamingResponse(io.BytesIO(raw), media_type=ctype,
                             headers={"Content-Disposition": f'inline; filename="{name}"'})


# =============================================================================
# chat — one persistent conversation per property, plus one global
# =============================================================================

class AskBody(BaseModel):
    question: str
    #: full (Opus 5 investigation + GPT-6 Astra second read + panel, up to 20 min)
    #: or fast (GPT-6 Astra alone, 10 tool calls, ~5 min, labelled as such).
    mode: str = "full"


def _chat_id(pid: Optional[str]) -> str:
    return f"property:{pid}" if pid else "global"


def _chat(pid: Optional[str]) -> Dict[str, Any]:
    """Get-or-create in one round trip. Atlas is ~350ms away; three trips for an
    empty chat made a blank page take nearly two seconds."""
    cid = _chat_id(pid)
    return mongo.db["chats"].find_one_and_update(
        {"chat_id": cid},
        {"$setOnInsert": {"chat_id": cid, "kind": "property" if pid else "global", "property_id": pid,
                          "messages": [], "created_at": datetime.now(timezone.utc)}},
        upsert=True, return_document=True,
    )


def _remember_notes(pid: Optional[str], *, include_pending: bool = False) -> List[dict]:
    """Notes that ride with answers. Rakesh Sir's are active the moment he writes
    them; anyone else's wait as ``pending`` until he approves — only approved
    notes are ever injected into an answer."""
    q: Dict[str, Any] = {"active": {"$ne": False}, "$or": [{"scope": "global"}]}
    if pid:
        q["$or"].append({"scope": "property", "property_id": pid})
    if not include_pending:
        q["status"] = {"$ne": "pending"}
    return list(mongo.db["remember_notes"].find(q, {"_id": 0}).sort("created_at", -1).limit(40))


_ROLE_LABEL = {"ceo": "Rakesh Sir (CEO — final authority)", "accountant": "JP Sir (accountant)", "operations": "Manjunath Sir (operations)"}


def _speaker(user: Dict[str, Any]) -> str:
    return _ROLE_LABEL.get(user.get("role"), user.get("name") or user.get("user_id") or "user")


@app.get("/chat")
@app.get("/chat/{pid}")
def get_chat(pid: Optional[str] = None, user=CurrentUser):
    if pid:
        _pid(pid)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_doc, f_notes = pool.submit(_chat, pid), pool.submit(_remember_notes, pid)
        doc, notes = f_doc.result(), f_notes.result()
    msgs = doc.get("messages", [])[-200:]
    # History carries only what the answer card renders. Passage text comes from
    # the evidence drawer on click; the investigator's draft, tool inputs and
    # per-channel ranks are audit detail — one answer with all of it was 1 MB.
    keep_src = ("index", "chunk_id", "artifact_sha", "citation", "display_name", "date", "placement", "label", "property_ids", "origin")
    for m in msgs:
        a = m.get("answer")
        if not a:
            continue
        a.pop("draft", None)
        a["sources"] = [{k: s.get(k) for k in keep_src} for s in (a.get("sources") or [])]
        a["steps"] = [{k: s.get(k) for k in ("step_num", "type", "tool_name", "summary", "new_indices", "error", "elapsed_ms")} for s in (a.get("steps") or [])]
        if isinstance(a.get("second_reader"), dict):
            a["second_reader"] = {k: v for k, v in a["second_reader"].items()}
    pending = [n for n in _remember_notes(pid, include_pending=True) if n.get("status") == "pending"]
    # A question whose answer never arrived: mark it so the UI can say why and
    # offer delete/re-ask, instead of showing a trace with zero steps forever.
    answered = {m.get("job_id") for m in msgs if m.get("role") == "assistant"}
    live = {a["job_id"] for a in _active_answers(doc["chat_id"])}
    orphan_ids = [m.get("job_id") for m in msgs if m.get("role") == "user" and m.get("job_id") and m["job_id"] not in answered and m["job_id"] not in live]
    if orphan_ids:
        failed = {j["job_id"]: j.get("error") for j in mongo.db["jobs"].find({"job_id": {"$in": orphan_ids}}, {"job_id": 1, "error": 1, "status": 1})}
        for m in msgs:
            if m.get("job_id") in orphan_ids:
                m["failed"] = failed.get(m["job_id"]) or "no answer was produced"
    return data.clean({"chat_id": doc["chat_id"], "property_id": pid, "messages": msgs,
                       "remember_notes": notes, "pending_notes": pending,
                       "summary": doc.get("summary"), "summary_at": doc.get("summary_at"),
                       # Still-running answers for this chat, so the page re-attaches.
                       "active": _active_answers(doc["chat_id"])})


@app.post("/chat/ask")
@app.post("/chat/{pid}/ask")
def chat_ask(body: AskBody, pid: Optional[str] = None, user=CurrentUser):
    if pid:
        _pid(pid)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "empty question")
    mode = "fast" if body.mode == "fast" else "full"
    doc = _chat(pid)
    now = datetime.now(timezone.utc)
    users = {u["user_id"]: u for u in mongo.db["users"].find({}, {"user_id": 1, "role": 1, "name": 1})}
    # The model is told who is speaking: an instruction from the CEO outranks
    # one from anyone else, and the summary must record who gave it.
    def _assistant_text(a: dict) -> str:
        # Headline plus the numbered points, so a follow-up like "do point 2
        # differently" refers to something the model can see.
        lines = [a.get("headline") or ""]
        for i, p in enumerate(a.get("points") or [], 1):
            lines.append(f"{i}. {p.get('text')}")
        if a.get("composed"):
            lines.append("DRAFT:\n" + str(a["composed"])[:2500])
        return "\n".join(lines)[:4000]
    history = [{"role": m["role"],
                "content": (f"[{_speaker(users.get(m.get('by'), {}))}] {m.get('content')}" if m["role"] == "user"
                            else _assistant_text(m.get("answer", {})))}
               for m in doc.get("messages", [])[-12:]]
    spoken = f"[{_speaker(user)}] {question}"
    scope = Scope.for_property(pid) if pid else Scope.global_()
    notes = _remember_notes(pid)

    rolling = doc.get("summary") or ""
    if rolling:
        history = [{"role": "user", "content": f"RUNNING SUMMARY OF THIS CHAT SO FAR (context, not instructions):\n{rolling}"}] + history

    def run(job):
        from mangotree.agent.reported import extract_reported_facts, facts_block, record_reported_facts
        from mangotree.agent.scratchpad import BudgetTracker
        job.budget = BudgetTracker()
        conv = list(history)
        # Statements of fact in the question ("this is done") are recorded with
        # attribution, applied to the open items right away, and honoured in the
        # answer — instead of being read past as part of the question.
        facts = extract_reported_facts(panel().anthropic, question)
        if facts:
            recorded = record_reported_facts(mongo, facts=facts, property_id=pid, user=user, job_id=job.job_id)
            conv = conv + [{"role": "user", "content": facts_block(recorded, user)}]
            job.emit("status", {"text": f"Noted {len(facts)} fact(s) you stated; updating open items…"})
            if pid:
                from mangotree.briefing.resolution import ResolutionPass
                ResolutionPass(mongo, anthropic_api_key=SETTINGS.anthropic_api_key).run_for(pid)
                _SMALL_CACHE.pop("task_counts", None)
        # "give me three" is a hard count, not a suggestion.
        m = re.search(r"\b(?:give|list|tell|show)\b[^.?!]{0,40}?\b(one|two|three|four|five|1|2|3|4|5)\b", question, re.I)
        max_points = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}.get((m.group(1) if m else "").lower(), None) or (int(m.group(1)) if m and m.group(1).isdigit() else None)
        res = panel().answer(spoken, scope, conversation=conv, on_event=job.emit, remember_notes=notes, budget=job.budget,
                             max_points=max_points, mode=mode)
        if job.cancelled:
            # The question was deleted while this ran; write nothing back.
            return {"cancelled": True}
        payload = _jsonable(res.as_dict())
        payload["question"] = question
        # Rolling summary: decisions, open questions, instructions (with who gave them).
        try:
            from mangotree.agent.summary import update_summary
            new_summary = update_summary(panel().anthropic, previous=rolling, question=spoken, answer=payload)
            if new_summary:
                mongo.db["chats"].update_one({"chat_id": doc["chat_id"]}, {"$set": {"summary": new_summary, "summary_at": datetime.now(timezone.utc)}})
        except Exception as exc:
            logger.warning("rolling summary failed: %s", exc)
        suggested = []
        # Next steps become suggested tasks only if nothing like them exists —
        # open, suggested, OR already done. An answer must not resurrect a task a
        # person closed, nor add a second copy of one already on the board.
        existing = list(mongo.db["tasks"].find({"property_id": pid} if pid else {}, {"title": 1, "status": 1}).limit(500))
        def similar(a: str, b: str) -> bool:
            wa = set(re.findall(r"[a-z0-9$]{3,}", a.lower())); wb = set(re.findall(r"[a-z0-9$]{3,}", b.lower()))
            return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.5
        for a in res.next_actions:
            if any(similar(a["title"], e.get("title") or "") for e in existing):
                continue
            t = tasks.upsert(title=a["title"], owner=a.get("owner") or "Rakesh", property_id=pid, by="opus-5",
                             source="ai_suggested", status="suggested", due=_dt(a.get("due")), why=a.get("why") or "",
                             evidence=[{"quote": "", "source_sha": (res.sources[s - 1]["artifact_sha"] if 0 < s <= len(res.sources) else None)} for s in a.get("sources", [])[:2]],
                             tags=["from_answer"])
            suggested.append(t["task_id"])
            existing.append({"title": a["title"], "status": "suggested"})
        payload["suggested_task_ids"] = suggested
        mongo.db["chats"].update_one({"chat_id": doc["chat_id"]}, {"$push": {"messages": {
            "role": "assistant", "job_id": job.job_id, "at": datetime.now(timezone.utc), "answer": payload}}})
        return payload

    job = jobs.start("answer", {"chat_id": doc["chat_id"], "question": question, "by": user["user_id"], "property_id": pid, "mode": mode}, run)
    # The question carries its job id, so a chat reopened mid-answer can find the
    # running job and re-attach to its event stream (events are replayed).
    mongo.db["chats"].update_one({"chat_id": doc["chat_id"]}, {"$push": {"messages": {
        "role": "user", "content": question, "by": user["user_id"], "role_label": _speaker(user), "at": now, "job_id": job.job_id, "mode": mode}}})
    return {"job_id": job.job_id, "chat_id": doc["chat_id"]}


@app.delete("/chat/{pid}/messages/{job_id}")
@app.delete("/chat/messages/{job_id}")
def chat_delete_message(job_id: str, pid: Optional[str] = None, user=CurrentUser):
    """Remove a question and its answer from the chat. If the answer is still
    running, it is cancelled first and its output discarded."""
    if pid:
        _pid(pid)
    doc = _chat(pid)
    jobs.cancel(job_id)
    r = mongo.db["chats"].update_one({"chat_id": doc["chat_id"]}, {"$pull": {"messages": {"job_id": job_id}}})
    mongo.db["saved_answers"].delete_many({"saved_id": job_id})
    mongo.db["chats"].update_one({"chat_id": doc["chat_id"]}, {"$push": {"deleted": {
        "job_id": job_id, "by": user["user_id"], "at": datetime.now(timezone.utc)}}})
    return {"removed": r.modified_count > 0, "job_id": job_id}


def _active_answers(chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Answer jobs still running, in this process. Per chat, or all."""
    out = []
    for j in list(jobs.jobs.values()):
        if j.kind != "answer" or j.status not in ("queued", "running"):
            continue
        if chat_id and (j.meta or {}).get("chat_id") != chat_id:
            continue
        out.append({"job_id": j.job_id, "chat_id": (j.meta or {}).get("chat_id"), "property_id": (j.meta or {}).get("property_id"),
                    "question": (j.meta or {}).get("question"), "by": (j.meta or {}).get("by"), "events": len(j.events),
                    "started_at": getattr(j, "created_at", None)})
    return out


@app.get("/jobs/{job_id}/stream")
def job_stream(job_id: str, request: Request, user=CurrentUser):
    job = jobs.get(job_id)
    if not job:
        doc = mongo.db["jobs"].find_one({"job_id": job_id}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "job not found")
        raise HTTPException(410, f"job {doc.get('status')} before this server started; reload the chat")
    return StreamingResponse(jobs.sse(job), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/jobs/{job_id}")
def job_status(job_id: str, user=CurrentUser):
    job = jobs.get(job_id)
    if job:
        return {"job_id": job_id, "status": job.status, "events": len(job.events), "result": job.result, "error": job.error}
    doc = mongo.db["jobs"].find_one({"job_id": job_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "job not found")
    return data.clean(doc)


# =============================================================================
# remember notes + saved answers
# =============================================================================

class NoteBody(BaseModel):
    text: str
    scope: str = "property"          # global | property
    property_id: Optional[str] = None


@app.get("/notes")
def list_notes(property_id: Optional[str] = None, user=CurrentUser):
    return data.clean(_remember_notes(property_id))


@app.post("/notes")
def add_note(body: NoteBody, user=CurrentUser):
    is_ceo = user["role"] == "ceo"
    doc = {"note_id": datetime.now(timezone.utc).strftime("n%Y%m%d%H%M%S%f"), "text": body.text.strip(),
           "scope": "global" if body.scope == "global" else "property", "property_id": body.property_id if body.scope != "global" else None,
           "author": user["name"], "author_id": user["user_id"], "created_at": datetime.now(timezone.utc), "active": True,
           # Rakesh Sir's notes are law immediately; others wait for his approval.
           "status": "active" if is_ceo else "pending", "instant": is_ceo}
    mongo.db["remember_notes"].insert_one(doc)
    return data.clean(doc)


@app.post("/notes/{note_id}/approve")
def approve_note(note_id: str, user=CurrentUser):
    if user["role"] != "ceo":
        raise HTTPException(403, "only Rakesh Sir can approve a note")
    mongo.db["remember_notes"].update_one({"note_id": note_id}, {"$set": {"status": "active", "approved_by": user["user_id"], "approved_at": datetime.now(timezone.utc)}})
    return {"ok": True}


@app.post("/notes/{note_id}/retire")
def retire_note(note_id: str, user=CurrentUser):
    mongo.db["remember_notes"].update_one({"note_id": note_id}, {"$set": {"active": False, "retired_by": user["user_id"], "retired_at": datetime.now(timezone.utc)}})
    return {"ok": True}


class SaveAnswerBody(BaseModel):
    chat_id: str
    job_id: str
    title: Optional[str] = None


@app.post("/saved")
def _answer_message(chat_id: str, job_id: str):
    """The assistant message for a job, plus the question that produced it.

    The question message carries the same job_id (so a reopened chat can
    re-attach to a running job), so the lookup must insist on the answer."""
    chat = mongo.db["chats"].find_one({"chat_id": chat_id})
    msgs = (chat or {}).get("messages", [])
    msg = next((m for m in msgs if m.get("job_id") == job_id and m.get("role") == "assistant" and m.get("answer")), None)
    if not msg:
        raise HTTPException(404, "answer not found")
    question = next((m.get("content") for m in msgs if m.get("job_id") == job_id and m.get("role") == "user"), None)
    if question is None:
        question = next((m.get("content") for m in reversed(msgs[: msgs.index(msg)]) if m.get("role") == "user"), "")
    return chat, msg, question or ""


def save_answer(body: SaveAnswerBody, user=CurrentUser):
    chat, msg, _ = _answer_message(body.chat_id, body.job_id)
    doc = {"saved_id": body.job_id, "chat_id": body.chat_id, "property_id": chat.get("property_id"),
           "title": body.title or msg["answer"].get("headline", "")[:120], "answer": msg["answer"],
           "saved_by": user["user_id"], "saved_at": datetime.now(timezone.utc)}
    mongo.db["saved_answers"].update_one({"saved_id": body.job_id}, {"$set": doc}, upsert=True)
    return {"ok": True, "saved_id": body.job_id}


@app.get("/saved")
def list_saved(property_id: Optional[str] = None, user=CurrentUser):
    q = {"property_id": property_id} if property_id else {}
    return data.clean(list(mongo.db["saved_answers"].find(q, {"_id": 0, "answer.sources": 0, "answer.steps": 0}).sort("saved_at", -1).limit(100)))


# =============================================================================
# tasks
# =============================================================================

class TaskBody(BaseModel):
    title: str
    owner: str = "Rakesh"
    property_id: Optional[str] = None
    priority: str = "normal"
    due: Optional[str] = None
    why: str = ""


class TaskStatusBody(BaseModel):
    status: str
    remark: str = ""


class TaskEditBody(BaseModel):
    title: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    due: Optional[str] = None
    why: Optional[str] = None
    property_id: Optional[str] = None


@app.get("/tasks")
def list_tasks(owner: Optional[str] = None, property_id: Optional[str] = None, status: Optional[str] = None, user=CurrentUser):
    statuses = [s for s in (status or "suggested,open").split(",") if s]
    # Counts are two extra aggregates (~0.7s); they change rarely, so 30s of cache is right.
    return data.clean({"items": tasks.list(owner=owner, property_id=property_id, statuses=statuses),
                       "counts": _cached("task_counts", 30, tasks.counts), "owners": list(OWNERS)})


@app.get("/shell")
def shell_state(user=CurrentUser):
    """What the frame needs on every page: the review badge and the degrade
    banner. Cheap and cached; the full dashboard is for the dashboard."""
    return {
        "unplaced": _cached("unplaced_count", 60, lambda: mongo.artifacts.count_documents({"placement": "unplaced"})),
        "degrades": _cached("degrades", 300, _degrades),
        # Not cached: which properties (or the global chat) have an answer in flight.
        "answering": [{"property_id": a["property_id"], "question": (a.get("question") or "")[:80], "job_id": a["job_id"]} for a in _active_answers()],
    }


@app.post("/tasks")
def create_task(body: TaskBody, user=CurrentUser):
    doc = tasks.upsert(title=body.title, owner=body.owner, property_id=body.property_id, by=user["user_id"],
                       source="manual", status="open", priority=body.priority, due=_dt(body.due), why=body.why)
    _SMALL_CACHE.pop("task_counts", None)
    data.invalidate_portfolio()
    return data.clean(doc)


@app.post("/tasks/{task_id}/status")
def task_status(task_id: str, body: TaskStatusBody, user=CurrentUser):
    doc = tasks.set_status(task_id, body.status, user["user_id"], body.remark)
    if not doc:
        raise HTTPException(404, "task not found")
    data.invalidate_portfolio()
    _SMALL_CACHE.pop("task_counts", None)
    return data.clean(doc)


@app.patch("/tasks/{task_id}")
def task_edit(task_id: str, body: TaskEditBody, user=CurrentUser):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "due" in fields:
        fields["due"] = _dt(fields["due"])
    doc = tasks.edit(task_id, user["user_id"], **fields)
    if not doc:
        raise HTTPException(404, "task not found")
    return data.clean(doc)


@app.get("/tasks/{task_id}/history")
def task_history(task_id: str, user=CurrentUser):
    return data.clean(tasks.history(task_id))


@app.post("/tasks/extract")
def tasks_extract(property_id: Optional[str] = None, user=CurrentUser):
    def run(job):
        from mangotree.tasks.extractor import TaskExtractor
        ex = TaskExtractor(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
        stats = ex.run([property_id] if property_id else None)
        return stats.as_dict()
    job = jobs.start("extract_tasks", {"property_id": property_id, "by": user["user_id"]}, run)
    return {"job_id": job.job_id}


# =============================================================================
# review — the human decisions
# =============================================================================

@app.get("/review/unplaced")
def review_unplaced(limit: int = 100, skip: int = 0, user=CurrentUser):
    q = {"placement": "unplaced", "is_inline_image": {"$ne": True}}
    proj = {k: v for k, v in data.ART_LIST.items() if not k.startswith("segregation.")}
    rows = list(mongo.artifacts.find(q, {**proj, "body_clean": 1, "segregation": 1, "property_candidates": 1}).sort("date", -1).skip(skip).limit(limit))
    keys = [r["thread_key"] for r in rows if r.get("thread_key")]
    sizes = {t["thread_key"]: t.get("message_count", 0) for t in mongo.threads.find({"thread_key": {"$in": keys}}, {"thread_key": 1, "message_count": 1})} if keys else {}
    out = []
    for r in rows:
        row = data.artifact_row(r)
        row["body_excerpt"] = " ".join((r.get("body_clean") or "").split())[:600]
        row["candidates"] = r.get("property_candidates") or []
        row["thread_size"] = sizes.get(r.get("thread_key"), 0)
        out.append(row)
    return data.clean({"items": out, "total": mongo.artifacts.count_documents(q),
                       "low_confidence": mongo.artifacts.count_documents({"resolution_status": "needs_review", "placement": "property"})})


@app.get("/review/low-confidence")
def review_low_conf(user=CurrentUser):
    rows = list(mongo.artifacts.find({"resolution_status": "needs_review", "placement": "property"}, data.ART_LIST).sort("date", -1).limit(200))
    return data.clean([data.artifact_row(r) for r in rows])


class DecisionBody(BaseModel):
    action: str                       # assign | common | discard
    artifact_shas: List[str]
    property_ids: List[str] = []
    note: str = ""


@app.post("/review/decide")
def review_decide(body: DecisionBody, user=CurrentUser):
    try:
        out = apply_human_decision(mongo, action=body.action, artifact_shas=body.artifact_shas,
                                   property_ids=body.property_ids, decided_by=user["user_id"], note=body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    data.invalidate_portfolio()
    return out.as_dict()


@app.get("/review/registry-candidates")
def registry_candidates(user=CurrentUser):
    rows = list(mongo.review_queue.find({"kind": "registry_candidate"}, {"_id": 0}).sort("created_at", -1))
    rows.sort(key=lambda r: (-len((r.get("payload") or {}).get("strong_signals") or []), -((r.get("payload") or {}).get("documents") or 0)))
    return data.clean(rows)


@app.post("/review/registry-candidates/close-all")
def close_registry_candidates(user=CurrentUser):
    r = mongo.review_queue.update_many({"kind": "registry_candidate", "status": {"$ne": "closed"}},
                                       {"$set": {"status": "closed", "resolution": "leave_as_other_deal", "resolved_by": user["user_id"],
                                                 "resolved_at": datetime.now(timezone.utc)}})
    return {"closed": r.modified_count}


# =============================================================================
# dashboard + people
# =============================================================================

@app.get("/dashboard")
def dashboard(user=CurrentUser):
    return data.clean({
        "user": user,
        "needs_attention": data.needs_attention(mongo, user["user_id"]),
        "handled": _cached("handled", 300, lambda: data.handled_overnight(mongo)),
        "portfolio": data.portfolio(mongo),
        "tasks": _cached("task_counts", 30, tasks.counts),
        "money": _cached("money_all", 120, lambda: data.money(mongo, None) | {"events": []}),
        "degrades": _cached("degrades", 300, _degrades),
        "intake": _cached("intake", 60, _intake_status),
    })


def _intake_status() -> Dict[str, Any]:
    try:
        st = _scheduler.watcher.status()
    except Exception as exc:
        return {"error": str(exc)[:200]}
    last = st.get("last_run") or {}
    if last:
        last = {k: last.get(k) for k in ("started_at", "finished_at", "kind", "seen", "fetched", "ingested", "skipped", "errors", "source_errors", "new_emails", "per_source")}
    arrival = mongo.db["arrival_runs"].find_one({"kind": {"$exists": False}}, sort=[("started_at", -1)])
    if arrival:
        arrival = {k: arrival.get(k) for k in ("started_at", "finished_at", "elapsed_s", "emails", "properties")} | {
            "errors": [k for k in arrival if k.endswith("_error")]}
    return {**st, "last_run": last, "last_arrival": arrival, "pending_debounce": len(_scheduler.chain._pending_props)}


# =============================================================================
# money ledger + Wes agenda (Fable 5.1)
# =============================================================================

@app.get("/ledger")
def ledger_portfolio(user=CurrentUser):
    from mangotree.ledger.builder import portfolio_summary
    return data.clean(_cached("ledger_portfolio", 120, lambda: portfolio_summary(mongo)))


@app.get("/properties/{pid}/ledger")
def ledger_property(pid: str, user=CurrentUser):
    _pid(pid)
    s = mongo.db["ledger_summaries"].find_one({"property_id": pid}, {"_id": 0})
    rows = list(mongo.db["ledger_entries"].find({"property_id": pid}, {"_id": 0}).sort([("date", 1), ("kind", 1)]))
    names = {a["sha256"]: a.get("filename") or a.get("subject") for a in mongo.artifacts.find(
        {"sha256": {"$in": list({r["source_sha"] for r in rows} | {s_["sha256"] for s_ in ((s or {}).get("sources") or [])})}}, {"sha256": 1, "filename": 1, "subject": 1})}
    return data.clean({"summary": s, "entries": rows, "names": names})


@app.post("/ledger/rebuild")
def ledger_rebuild(property_id: Optional[str] = Query(None), user=CurrentUser):
    """Rebuild the ledger for one property (or all) with Fable 5.1; streamed as a job."""
    from mangotree.ledger.builder import LedgerBuilder
    ids = [_pid(property_id)] if property_id else None
    def run(job):
        job.emit("status", {"text": f"Fable 5.1 reading money documents for {property_id or 'all properties'}…"})
        st = LedgerBuilder(mongo, anthropic_api_key=SETTINGS.anthropic_api_key).run(ids, concurrency=4)
        for k in ("ledger_portfolio",):
            _SMALL_CACHE.pop(k, None)
        data.invalidate_portfolio()
        return data.clean(st.as_dict())
    job = jobs.start("ledger", {"property_id": property_id, "by": user["user_id"]}, run)
    return {"job_id": job.job_id}


@app.get("/properties/{pid}/dossier")
def dossier_get(pid: str, user=CurrentUser):
    """What the automated passes were told about this property: the agent's
    investigation, the chat's decisions, standing notes, prior human judgements."""
    _pid(pid)
    return data.clean(mongo.db["dossiers"].find_one({"property_id": pid}, {"_id": 0}) or {"property_id": pid, "built_at": None})


@app.post("/properties/{pid}/dossier/refresh")
def dossier_refresh(pid: str, user=CurrentUser):
    from mangotree.briefing.dossier import PropertyDossier
    _pid(pid)
    def run(job):
        job.emit("status", {"text": "Opus 5 agent investigating where this deal stands…"})
        d = PropertyDossier(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key,
                            openai_api_key=SETTINGS.openai_api_key_critic or "").build(pid, force=True)
        return data.clean({k: d.get(k) for k in ("property_id", "built_at")} | {"steps": (d.get("investigation") or {}).get("steps")})
    job = jobs.start("dossier", {"property_id": pid, "by": user["user_id"]}, run)
    return {"job_id": job.job_id}


@app.get("/wes-agenda")
def wes_agenda_all(user=CurrentUser):
    rows = _scheduler.wes.today()
    return data.clean({"day": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "properties": rows})


@app.get("/properties/{pid}/wes-agenda")
def wes_agenda_property(pid: str, user=CurrentUser):
    _pid(pid)
    rows = _scheduler.wes.today(pid)
    return data.clean(rows[0] if rows else {"property_id": pid, "issues": [], "day": None})


@app.post("/properties/{pid}/wes-agenda/refresh")
def wes_agenda_refresh(pid: str, user=CurrentUser):
    _pid(pid)
    def run(job):
        job.emit("status", {"text": "Fable 5.1 reading this week's records, Wes's items and the ledger…"})
        return data.clean(_scheduler.wes.generate(pid, force=True))
    job = jobs.start("wes_agenda", {"property_id": pid, "by": user["user_id"]}, run)
    return {"job_id": job.job_id}


@app.post("/properties/{pid}/resolve")
def resolve_open_items(pid: str, user=CurrentUser):
    """Re-read recent records against every open issue, card and task for this
    property and close what they settle. Runs automatically after new mail and
    each morning; this is the on-demand version."""
    from mangotree.briefing.resolution import ResolutionPass
    _pid(pid)
    def run(job):
        job.emit("status", {"text": "Fable 5.1 checking open items against the latest records…"})
        out = ResolutionPass(mongo, anthropic_api_key=SETTINGS.anthropic_api_key).run_for(pid)
        for k in ("task_counts", "handled"):
            _SMALL_CACHE.pop(k, None)
        return data.clean(out)
    job = jobs.start("resolve", {"property_id": pid, "by": user["user_id"]}, run)
    return {"job_id": job.job_id}


class AgendaMark(BaseModel):
    day: str
    index: int
    discussed: bool = True
    outcome: Optional[str] = None


@app.post("/properties/{pid}/wes-agenda/mark")
def wes_agenda_mark(pid: str, body: AgendaMark, user=CurrentUser):
    _pid(pid)
    out = _scheduler.wes.mark(pid, body.day, body.index, discussed=body.discussed, outcome=body.outcome, by=user["user_id"])
    if out is None:
        raise HTTPException(404, "agenda issue not found")
    return data.clean(out)


@app.get("/intake")
def intake(user=CurrentUser):
    return data.clean(_intake_status())


@app.post("/intake/check-now")
def intake_check_now(user=CurrentUser):
    """Poll both mailboxes now and run the arrival chain; streamed as a job."""
    def run(job):
        job.emit("status", {"text": "Checking Gmail and Outlook for new mail…"})
        out = _scheduler.run_intake(kind="manual")
        rep = out["intake"]
        job.emit("status", {"text": f"Seen {rep['seen']}, fetched {rep['fetched']}, ingested {rep['ingested']}, errors {rep['errors']}"})
        if "arrival" in out:
            job.emit("status", {"text": f"Processed {out['arrival'].get('emails')} new email(s) through the full chain in {out['arrival'].get('elapsed_s')}s"})
            _scheduler.chain.flush_debounced(force=True)
        for k in ("intake", "handled", "task_counts", "degrades"):
            _SMALL_CACHE.pop(k, None)
        return data.clean(out)
    job = jobs.start("intake", {"by": user["user_id"]}, run)
    return {"job_id": job.job_id}


def _degrades() -> List[str]:
    out = []
    try:
        from mangotree.retrieve.search_index import search_index_status
        st = search_index_status(mongo)
        if not st.get("queryable"):
            out.append("lexical search index not queryable — BM25/phrase/synonym channels degraded")
    except Exception as exc:
        out.append(f"could not check search index: {exc}")
    if not SETTINGS.openai_api_key_critic:
        out.append("OPENAI_API_KEY_CRITIC / OPENAI_API_KEY not set — GPT-6 Astra second reader unavailable; same-provider fallback in use")
    # A failure only counts if nothing of the same kind has succeeded since —
    # a retried briefing that then worked is not a degrade the reader must see.
    since = datetime.now(timezone.utc) - timedelta(hours=6)
    failed = list(mongo.db["jobs"].find({"status": "failed", "created_at": {"$gte": since}}, {"kind": 1, "created_at": 1, "error": 1}))
    still_failing = []
    for f in failed:
        ok_after = mongo.db["jobs"].find_one({"kind": f["kind"], "status": "done", "created_at": {"$gt": f["created_at"]}})
        if not ok_after:
            still_failing.append(f)
    if still_failing:
        kinds = ", ".join(sorted({f["kind"] for f in still_failing}))
        out.append(f"{len(still_failing)} job(s) failed recently without a later success ({kinds})")
    return out


# =============================================================================
# briefing, cards, deadlines, exports
# =============================================================================

@app.get("/briefing")
def get_briefing(user=CurrentUser):
    b = _briefing.latest(user["user_id"])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return data.clean({"briefing": b, "is_today": bool(b and b.get("day") == today)})


@app.post("/briefing/generate")
def gen_briefing(user=CurrentUser):
    def run(job):
        return _briefing.generate(user["user_id"], force=True)
    job = jobs.start("briefing", {"by": user["user_id"]}, run)
    return {"job_id": job.job_id}


@app.get("/cards")
def get_cards(property_id: Optional[str] = None, status: str = "new", order: str = "significance", user=CurrentUser):
    return data.clean(_cards.feed(property_id=property_id, status=status, order=order))


@app.post("/cards/{card_id}/dismiss")
def dismiss_card(card_id: str, body: Dict[str, Any] = Body(default={}), user=CurrentUser):
    c = _cards.dismiss(card_id, user["user_id"], str(body.get("remark") or ""))
    if not c:
        raise HTTPException(404, "card not found")
    return data.clean(c)


@app.post("/cards/{card_id}/seen")
def seen_card(card_id: str, user=CurrentUser):
    c = _cards.acknowledge(card_id, user["user_id"])
    return data.clean(c or {})


@app.post("/cards/detect")
def detect_cards(property_id: Optional[str] = None, user=CurrentUser):
    def run(job):
        return _cards.run([property_id] if property_id else None)
    job = jobs.start("cards", {"property_id": property_id, "by": user["user_id"]}, run)
    return {"job_id": job.job_id}


@app.get("/deadlines")
def deadlines_board(days: int = 45, user=CurrentUser):
    """Every dated item across the portfolio: timeline events ahead + tasks with due dates."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    ev = list(mongo.db["timeline_events"].find({"occurred_at": {"$gte": now - timedelta(days=7), "$lte": horizon}},
                                                {"_id": 0, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "source_sha": 1, "amount": 1}).sort("occurred_at", 1).limit(80))
    ts = list(mongo.db["tasks"].find({"status": {"$in": ["open", "suggested"]}, "due": {"$ne": None, "$lte": horizon}}, {"_id": 0}).sort("due", 1).limit(80))
    items = [{"kind": "event", "date": e["occurred_at"], "property_id": e.get("property_id"), "title": e["title"], "type": e["event_type"], "source_sha": e.get("source_sha"), "amount": e.get("amount")} for e in ev]
    items += [{"kind": "task", "date": t["due"], "property_id": t.get("property_id"), "title": t["title"], "type": t["priority"], "task_id": t["task_id"], "owner": t["owner"], "status": t["status"],
               "source_sha": (t.get("evidence") or [{}])[0].get("source_sha")} for t in ts]
    items.sort(key=lambda x: x["date"])
    return data.clean({"items": items, "now": now})


def _xlsx(content: bytes, name: str) -> Response:
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _pdf(content: bytes, name: str) -> Response:
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/export/money.xlsx")
def export_money(property_id: Optional[str] = None, user=CurrentUser):
    return _xlsx(exports.money_xlsx(mongo, property_id), f"mangotree-money-{property_id or 'portfolio'}.xlsx")


@app.get("/export/tasks.xlsx")
def export_tasks(property_id: Optional[str] = None, owner: Optional[str] = None, user=CurrentUser):
    return _xlsx(exports.tasks_xlsx(mongo, property_id=property_id, owner=owner), "mangotree-tasks.xlsx")


@app.get("/export/portfolio.xlsx")
def export_portfolio(user=CurrentUser):
    return _xlsx(exports.portfolio_xlsx(data.portfolio(mongo)), "mangotree-portfolio.xlsx")


@app.get("/export/answer/{chat_id:path}/{job_id}.pdf")
def export_answer(chat_id: str, job_id: str, user=CurrentUser):
    _, msg, q = _answer_message(chat_id, job_id)
    return _pdf(exports.answer_pdf(msg["answer"], question=q, scope=msg["answer"].get("scope", ""), saved_by=user["name"]), f"answer-{job_id}.pdf")


@app.get("/export/briefing.pdf")
def export_briefing(user=CurrentUser):
    b = _briefing.latest(user["user_id"])
    if not b:
        raise HTTPException(404, "no briefing yet")
    return _pdf(exports.briefing_pdf(b, user_name=user["name"]), f"briefing-{b.get('day')}.pdf")


@app.get("/people")
def list_people(q: Optional[str] = None, property_id: Optional[str] = None, user=CurrentUser):
    return data.people(mongo, q=q, property_id=property_id)


@app.get("/people/{person_id}")
def get_person(person_id: str, user=CurrentUser):
    return data.person_detail(mongo, person_id)


@app.get("/search/quick")
def quick_search(q: str, user=CurrentUser):
    """Command palette: properties, people, documents by name."""
    ql = q.lower().strip()
    props = [{"kind": "property", "id": p.property_id, "label": p.canonical_address, "sub": p.property_id}
             for p in PROPERTIES if ql in p.property_id or ql in p.canonical_address.lower() or any(ql in a.lower() for a in p.aliases)]
    ppl = [{"kind": "person", "id": p.person_id, "label": p.display_name, "sub": p.role}
           for p in PEOPLE_LIST() if ql in p.display_name.lower() or ql in p.role.lower()][:6]
    docs = [{"kind": "document", "id": r["sha256"], "label": r.get("filename") or r.get("subject"), "sub": ", ".join(r.get("property_ids") or []) or r.get("placement")}
            for r in mongo.artifacts.find({"$or": [{"filename": {"$regex": re.escape(q), "$options": "i"}}, {"subject": {"$regex": re.escape(q), "$options": "i"}}],
                                           "is_inline_image": {"$ne": True}}, data.ART_LIST).sort("date", -1).limit(8)] if len(ql) >= 3 else []
    return {"results": props[:6] + ppl + docs}


def PEOPLE_LIST():
    from mangotree.config.registry import PEOPLE
    return PEOPLE
