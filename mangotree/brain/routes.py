"""Brain API — the property files, the drafts, the chat.

    GET  /brain/home                          today: drafts waiting, your to-dos, what changed, every property in a line
    GET  /brain/properties/{pid}              one property file with its week, drafts and money
    POST /brain/properties/{pid}/items        add a to-do          PATCH …/items/{id}   edit / close / reopen
    GET  /brain/drafts                        replies waiting      GET/PATCH /brain/drafts/{id}
    POST /brain/drafts/{id}/redo              rewrite with an instruction (job)
    POST /brain/drafts/{id}/outlook           put it in the Outlook Drafts folder
    GET  /brain/chat/threads                  chat threads for a property (or the portfolio)
    POST /brain/chat/threads                  start one            GET /brain/chat/threads/{id}
    POST /brain/chat/threads/{id}/files       attach files
    POST /brain/chat/threads/{id}/ask         ask (job; text streams on /jobs/{job_id}/stream)
    POST /brain/run                           read whatever has arrived, now (job)
    GET/POST /brain/settings                  automatic reading on/off; Outlook drafts on/off
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, File, HTTPException, Response, UploadFile
from pydantic import BaseModel

from mangotree.api import data, swr
from mangotree.api.auth import CurrentUser
from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from .chat import ChatEngine
from .context import Records, money_lines
from .drafts import DraftWriter
from .lessons import Lessons, changed_enough
from . import snapshot
from .buildertrend import Buildertrend
from .sitework import SiteWork
from .billtrack import STEPS, BillTracker
from .billmail import BillMail
from .store import BrainStore, active_property_ids, all_file_ids, name_of, now
from .updater import Updater


class ItemBody(BaseModel):
    title: str
    owner: str = "rakesh"
    due: Optional[str] = None
    detail: str = ""
    kind: str = "action"


class ItemPatch(BaseModel):
    status: Optional[str] = None           # open | done | dropped
    title: Optional[str] = None
    detail: Optional[str] = None
    owner: Optional[str] = None
    due: Optional[str] = None
    note: Optional[str] = None


class DraftPatch(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    status: Optional[str] = None           # ready | sent | dismissed
    to: Optional[List[Dict[str, str]]] = None
    cc: Optional[List[Dict[str, str]]] = None


class StepBody(BaseModel):
    step: str
    done: bool = True


class RedoBody(BaseModel):
    instruction: str = ""


class ThreadBody(BaseModel):
    property_id: Optional[str] = None


class AskBody(BaseModel):
    text: str
    file_ids: List[str] = []


def install(app: FastAPI, mongo: Mongo, jobs) -> None:
    store = BrainStore(mongo)
    engine = ChatEngine(mongo, store)
    writer = DraftWriter(mongo, store)
    updater = Updater(mongo, store)
    records = Records(mongo)
    lessons = Lessons(mongo, store)

    def _file_id(pid: str) -> str:
        if pid != bc.GENERAL_ID and pid not in PROPERTY_INDEX:
            raise HTTPException(404, f"unknown property {pid}")
        return pid

    def _draft_view(d: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(d)
        d["properties"] = [{"property_id": p, "name": name_of(p)} for p in d.get("property_ids") or []]
        d.pop("usage", None)
        d.pop("retrieval", None)         # the search passages behind a draft are not shown on any screen
        return d

    def _item_view(i: Dict[str, Any], pid: str) -> Dict[str, Any]:
        overdue = bool(i.get("due") and i["due"].replace(tzinfo=i["due"].tzinfo or timezone.utc) < now() and i.get("status") == "open")
        return {**i, "property_id": pid, "property": name_of(pid), "overdue": overdue}

    def _summary(f: Dict[str, Any], drafts_by: Dict[str, int]) -> Dict[str, Any]:
        items = BrainStore.open_items(f)
        return {"property_id": f["property_id"], "name": f.get("name") or name_of(f["property_id"]), "headline": f.get("headline") or "",
                "updated_at": f.get("updated_at"), "last_event_at": f.get("last_event_at"), "open": len(items),
                "open_mine": {o: sum(1 for i in items if i.get("owner") == o) for o in ("rakesh", "jp", "manjunath")},
                "waiting": sum(1 for i in items if i.get("kind") == "waiting"), "drafts": drafts_by.get(f["property_id"], 0),
                "watch": len(f.get("watch") or []), "is_general": f["property_id"] == bc.GENERAL_ID}

    # ---------------------------------------------------------------- home
    warming = {"on": False}      # true only while pages are being pre-read at start-up: that must not start jobs

    @app.get("/brain/home")
    @swr.cached("home", fresh=20, keep=120)
    def brain_home(days: int = 3, user=CurrentUser):
        me = user.get("user_id")
        # Independent reads, side by side: the page waits for the slowest one, not for the sum.
        g = swr.gather(
            files=store.list_files,
            drafts=lambda: store.list_drafts(),
            activity=lambda: store.recent_activity(None, days=max(1, min(days, 14)), limit=120),
            pending=lambda: swr.memo("pending-records", 60, lambda: len(updater.pending(limit=200))),
            sent=lambda: list(store.drafts.find({"status": "sent", "kept_ratio": {"$exists": True}}, {"_id": 0, "kept_ratio": 1, "sent_as_written": 1}).limit(500)),
            enabled=lambda: bool(store.setting(bc.SETTING_ENABLED, True)),
            outlook=lambda: bool(store.setting(bc.SETTING_OUTLOOK_DRAFTS, False)),
        )
        files, activity, pending, sent = g["files"], g["activity"], g["pending"], g["sent"]
        drafts = [d for d in g["drafts"] if d.get("status") == "ready"]
        # Has any waiting draft been answered since it was written? Checked without holding
        # the page; if the check closes or flags one, the remembered answer is dropped.
        writer.refresh_soon(drafts, on_change=swr.clear)
        drafts_by: Dict[str, int] = {}
        for d in drafts:
            for p in d.get("property_ids") or []:
                drafts_by[p] = drafts_by.get(p, 0) + 1
        todos, waiting = [], []
        for f in files:
            for i in BrainStore.open_items(f):
                row = _item_view(i, f["property_id"])
                (waiting if i.get("kind") == "waiting" else todos).append(row)
        far = datetime(2999, 1, 1, tzinfo=timezone.utc)
        todos.sort(key=lambda i: (0 if i.get("owner") == me else 1, i.get("due") or far, i.get("opened_at") or far))
        for a in activity:
            a["property"] = name_of(a["property_id"])
        built = [f for f in files if f.get("built_at")]
        # Records that arrived and have not been read into the files yet are read now,
        # without anyone pressing anything: the page must not show a to-do an email settled.
        reading = any(j.kind == "brain_run" and j.status in ("queued", "running") for j in list(jobs.jobs.values()))
        if pending and not reading and g["enabled"] and not warming["on"]:
            jobs.start("brain_run", {"by": "auto", "why": "unread records when Today was opened"},
                       lambda job: data.clean(updater.run_pending(emit=job.emit)))
            reading = True
        return data.clean({
            "me": me, "properties": [_summary(f, drafts_by) for f in files],
            "missing": [{"property_id": p, "name": name_of(p)} for p in all_file_ids() if p not in {f["property_id"] for f in built}],
            "drafts": [_draft_view(d) for d in drafts], "todos": todos, "waiting": waiting, "activity": activity,
            "pending_records": pending, "reading": reading,
            "stats": {"drafts_sent": len(sent), "sent_as_written": sum(1 for s in sent if s.get("sent_as_written")),
                      "avg_kept": round(sum(s.get("kept_ratio") or 0 for s in sent) / len(sent), 2) if sent else None},
            "settings": {"enabled": g["enabled"], "outlook_drafts": g["outlook"]},
        })

    @app.get("/brain/counts")
    @swr.cached("counts", fresh=30, keep=300)
    def brain_counts(user=CurrentUser):
        """For the sidebar: how many replies are waiting, and which files exist."""
        rows = list(store.files.find({}, {"_id": 0, "property_id": 1, "open_items.status": 1, "open_items.owner": 1}))
        return {"drafts": store.drafts.count_documents({"status": "ready"}), "bills": billtrack.open_count(),
                "open": {r["property_id"]: sum(1 for i in r.get("open_items") or [] if i.get("status") == "open" and i.get("owner") == "rakesh") for r in rows}}

    # ---------------------------------------------------------- properties
    bt = Buildertrend(mongo)
    sitework = SiteWork(mongo, store)
    billtrack = BillTracker(mongo)
    billmail = BillMail(mongo, store)

    @app.get("/brain/bills")
    @swr.cached("bills", fresh=15, keep=120, per_user=False)
    def brain_bills(view: str = "open", user=CurrentUser):
        """The contractor's bills RKB is handling, with RKB's own four steps and when each was done."""
        g = swr.gather(rows=lambda: billtrack.rows("paid" if view == "paid" else "open"), pulled=bt.last_pull, held=billtrack.held_back)
        mails = billmail.states([r["bill_id"] for r in g["rows"]])
        for r in g["rows"]:
            r["mail"] = mails.get(r["bill_id"], {})
        return data.clean({"view": "paid" if view == "paid" else "open", "steps": [{"key": k, "label": l} for k, l in STEPS], "rows": g["rows"], "pulled_at": g["pulled"],
                           "held_back": g["held"]})

    @app.get("/brain/bills/{bill_id}/mail-preview")
    def brain_bill_mail_preview(bill_id: int, step: str = "paid", user=CurrentUser):
        """What the automatic email for this bill and step would say. Nothing is sent or queued."""
        row = next((r for v in ("open", "paid") for r in billtrack.rows(v) if r["bill_id"] == bill_id), None)
        if not row:
            raise HTTPException(404, "That bill is not on the Bills page.")
        try:
            m = billmail.compose(row, step, user.get("name") or "you")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return data.clean({"to": m["to"], "cc": m["cc"], "subject": m["subject"], "text": m["text"], "html": m["html"]})

    @app.post("/brain/bills/{bill_id}/steps")
    def brain_bill_step(bill_id: int, body: StepBody, user=CurrentUser):
        try:
            row = billtrack.set_step(bill_id, body.step, body.done, user)
            # "Ready to pay" and "Paid" send their email by themselves (see brain/billmail.py); unticking
            # before it has gone cancels it. A mail problem never undoes the tick.
            row["mail_result"] = billmail.on_step(row, body.step, body.done, user)
            row["mail"] = billmail.state(row["bill_id"])
            return data.clean(row)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except KeyError:
            raise HTTPException(404, "That bill is not on file.")

    def _reading_head(pid: str) -> Optional[Dict[str, Any]]:
        """The site-work reading in brief, for the top of the page."""
        a = sitework.latest(pid)
        if not a:
            return None
        return {k: a.get(k) for k in ("at", "overall", "one_line", "headline", "phase", "workmanship", "pace", "documentation", "stats", "photos_shown", "photos_posted")} | {
            "best_photos": (a.get("best_photos") or [])[:6], "flagged": sum(1 for o in a.get("observations") or [] if o.get("kind") != "good")}

    @app.get("/brain/properties/{pid}/site/analysis")
    @swr.cached("site-analysis", fresh=60, keep=900)
    def brain_site_analysis(pid: str, user=CurrentUser):
        pid = _file_id(pid)
        g = swr.gather(a=lambda: sitework.latest(pid), photos=lambda: bt.latest_photos(pid, limit=12), s=lambda: bt.summary(pid))
        return data.clean({"analysis": g["a"], "latest_photos": g["photos"], "in_buildertrend": g["s"]["in_buildertrend"],
                           "last_log": bt._day(g["s"]["last_log"]), "logs_total": g["s"]["logs_total"], "pulled_at": g["s"]["pulled_at"]})

    @app.post("/brain/properties/{pid}/site/analysis")
    def brain_site_analyse(pid: str, user=CurrentUser):
        pid = _file_id(pid)
        if user.get("role") != "ceo":
            raise HTTPException(403, "only Rakesh Sir can start a new reading")
        job = jobs.start("site_work", {"property_id": pid, "by": user["user_id"]}, lambda job: data.clean(sitework.analyse(pid) or {"none": True}))
        return {"job_id": job.job_id}

    @app.get("/brain/site/photo/{file_id}")
    def brain_site_photo(file_id: int, user=CurrentUser):
        url = bt.photo_url(file_id)
        if url:
            # On the server's storage: send the browser there. The image does not pass through, or rest on, this machine.
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url, status_code=302, headers={"Cache-Control": "private, max-age=600"})
        blob = bt.photo(file_id)
        if not blob:
            raise HTTPException(404, "That photo has not been brought over from Buildertrend.")
        return Response(content=blob, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})

    @app.get("/brain/properties/{pid}/site/logs")
    @swr.cached("site-logs", fresh=60, keep=900)
    def brain_site_logs(pid: str, skip: int = 0, limit: int = 30, user=CurrentUser):
        return data.clean(bt.logs_page(_file_id(pid), skip=skip, limit=limit))

    @app.get("/brain/properties/{pid}/site/bills")
    @swr.cached("site-bills", fresh=60, keep=900)
    def brain_site_bills(pid: str, user=CurrentUser):
        return data.clean(bt.bills_page(_file_id(pid)))

    @app.get("/brain/properties/{pid}/arv")
    @swr.cached("property-arv", fresh=60, keep=900)
    def brain_property_arv(pid: str, user=CurrentUser):
        return data.clean(snapshot.arv_detail(mongo, _file_id(pid)))

    @app.get("/brain/properties/{pid}")
    @swr.cached("property", fresh=30, keep=120, per_user=False)
    def brain_property(pid: str, days: int = 7, user=CurrentUser):
        pid = _file_id(pid)
        p = PROPERTY_INDEX.get(pid)
        base = {"property_id": pid, "name": name_of(pid), "is_general": pid == bc.GENERAL_ID,
                "address": (", ".join(x for x in (p.canonical_address, p.city, f"{p.state or ''} {p.postal or ''}".strip()) if x) if p else name_of(pid))}
        general = pid == bc.GENERAL_ID
        # Independent reads, side by side.
        g = swr.gather(
            file=lambda: store.get_file(pid),
            drafts=lambda: store.list_drafts(pid=pid),
            activity=lambda: store.recent_activity(pid, days=max(1, min(days, 90)), limit=150),
            terms=lambda: store.loan_terms.find_one({"property_id": pid}, {"_id": 0}) or {},
            site=lambda: {"in_buildertrend": False} if general else bt.view(pid),
            arv=lambda: None if general else snapshot.arv_of(mongo, pid),
            reading=lambda: None if general else _reading_head(pid),
            photos=lambda: [] if general else bt.latest_photos(pid, limit=6),
            registry=lambda: snapshot._registry(mongo),
        )
        f = g["file"]
        if not f:
            return data.clean({**base, "exists": False})
        drafts = [d for d in g["drafts"] if d.get("status") == "ready"]
        writer.refresh_soon(drafts, on_change=swr.clear)
        items = [_item_view(i, pid) for i in (f.get("open_items") or [])]
        closed = sorted([i for i in items if i.get("status") != "open"], key=lambda i: i.get("closed_at") or now(), reverse=True)[:15]
        doc = g["terms"]
        rows = [{**r, "kind": src.get("kind"), "file": src.get("file"), "as_of": src.get("as_of")} for src in doc.get("sources") or [] for r in src.get("rows") or []]
        return data.clean({
            **base, "exists": True, "headline": f.get("headline"), "status": f.get("status"), "facts": f.get("facts") or [],
            "watch": f.get("watch") or [], "open_items": [i for i in items if i.get("status") == "open"], "closed_items": closed,
            "updated_at": f.get("updated_at"), "built_at": f.get("built_at"), "last_event_at": f.get("last_event_at"),
            "activity": g["activity"], "drafts": [_draft_view(d) for d in drafts], "money": doc.get("sources") or [],
            # The four numbers and the people (read straight from the workbooks and the file,
            # no model), how far along the deal is, the ARV on file, and the contractor's
            # site diary and bills.
            "numbers": snapshot.numbers(store, pid, f, rows=rows), "people": snapshot.people(mongo, store, pid, f, rows=rows),
            "stage": snapshot.stage_view(f), "arv": g["arv"], "site": g["site"],
            # How the work is going (an AI reading of the last month of logs and photos), and the newest photos held.
            "sitework": g["reading"], "latest_photos": g["photos"],
        })

    def _warm_all() -> None:
        """At start-up: read every property page once, so the first click on any of them is answered from memory."""
        import time
        time.sleep(3)
        for p in all_file_ids():
            try:
                brain_property(pid=p, days=7, user={})
            except Exception:
                logger.exception("warming %s failed", p)
        # Today, the reply list and the menu counts, for each RKB user.
        from mangotree.api.auth import public_user
        warming["on"] = True
        try:
            for u in mongo.db["users"].find({"active": {"$ne": False}, "side": {"$ne": "contractor"}}):
                usr = public_user(u)
                for call in (lambda: brain_home(days=3, user=usr), lambda: brain_counts(user=usr), lambda: brain_drafts(status="ready", property_id=None, user=usr)):
                    try:
                        call()
                    except Exception:
                        logger.exception("warming a page for %s failed", usr.get("user_id"))
        finally:
            warming["on"] = False

    import os as _os
    import threading as _threading
    if _os.environ.get("MT_NO_WARM") != "1":
        _threading.Thread(target=_warm_all, daemon=True, name="warm-properties").start()

    @app.post("/brain/properties/{pid}/items")
    def brain_add_item(pid: str, body: ItemBody, user=CurrentUser):
        pid = _file_id(pid)
        if not store.get_file(pid):
            store.save_file(pid, {"open_items": []})
        return data.clean(store.add_item(pid, title=body.title, detail=body.detail, owner=body.owner, kind=body.kind, due=body.due, by=user["user_id"]))

    @app.patch("/brain/properties/{pid}/items/{item_id}")
    def brain_patch_item(pid: str, item_id: str, body: ItemPatch, user=CurrentUser):
        pid = _file_id(pid)
        ok = False
        if body.status in ("done", "dropped"):
            ok = store.close_item(pid, item_id, note=body.note or "", by=user["user_id"], status=body.status)
            if ok:
                f = store.get_file(pid) or {}
                item = next((i for i in f.get("open_items") or [] if i["id"] == item_id), {})
                store.add_activity(pid, at=now(), kind="todo_closed", who=user.get("name") or user["user_id"],
                                   title=f"{'Done' if body.status == 'done' else 'Dropped'}: {item.get('title')}", summary=body.note or "", relevance="minor")
        elif body.status == "open":
            ok = store.reopen_item(pid, item_id)
        fields = {k: v for k, v in body.model_dump().items() if k in ("title", "detail", "owner") and v is not None}
        if "due" in body.model_fields_set:
            fields["due"] = body.due
        if fields:
            ok = store.update_item(pid, item_id, fields) or ok
        if not ok:
            raise HTTPException(404, "item not found or nothing to change")
        return {"ok": True}

    @app.post("/brain/properties/{pid}/rebuild")
    def brain_rebuild(pid: str, user=CurrentUser):
        """Re-open the file from the records (admin). Open items and drafts are rebuilt."""
        pid = _file_id(pid)
        if user.get("role") != "ceo":
            raise HTTPException(403, "only Rakesh Sir can rebuild a file")
        job = jobs.start("brain_build", {"property_id": pid, "by": user["user_id"]}, lambda job: data.clean(updater.bootstrap(pid, emit=job.emit)))
        return {"job_id": job.job_id}

    # --------------------------------------------------------------- drafts
    @app.get("/brain/drafts")
    @swr.cached("drafts", fresh=20, keep=120)
    def brain_drafts(status: str = "ready", property_id: Optional[str] = None, user=CurrentUser):
        statuses = [s for s in status.split(",") if s in ("ready", "sent", "dismissed", "superseded")] or ["ready"]
        rows = store.list_drafts(status=statuses, pid=property_id, limit=200)
        if "ready" in statuses:
            writer.refresh_soon(rows, on_change=swr.clear)
        return data.clean([_draft_view(d) for d in rows])

    @app.get("/brain/drafts/{draft_id}")
    def brain_draft(draft_id: str, user=CurrentUser):
        d = store.get_draft(draft_id)
        if not d:
            raise HTTPException(404, "draft not found")
        out = _draft_view(d)
        if d.get("reply_to_sha"):
            art = mongo.artifacts.find_one({"sha256": d["reply_to_sha"]}, {"_id": 0, "sha256": 1, "subject": 1, "date": 1, "participants": 1,
                                                                             "body_clean": 1, "attachment_names": 1, "thread_key": 1})
            if art:
                frm = ((art.get("participants") or {}).get("from") or [""])[0]
                out["original"] = {"sha256": art["sha256"], "subject": art.get("subject"), "date": art.get("date"), "from": records.who(frm),
                                   "body": (art.get("body_clean") or "")[:20_000], "attachments": art.get("attachment_names") or [],
                                   "thread_key": art.get("thread_key")}
        return data.clean(out)

    @app.patch("/brain/drafts/{draft_id}")
    def brain_patch_draft(draft_id: str, body: DraftPatch, user=CurrentUser):
        d = store.get_draft(draft_id)
        if not d:
            raise HTTPException(404, "draft not found")
        fields: Dict[str, Any] = {}
        if body.subject is not None:
            fields["subject"] = body.subject.strip()[:300]
        if body.body is not None and body.body.strip() != (d.get("body") or "").strip():
            fields["body"] = body.body.strip()
            fields["edited"] = True
            fields["edited_by"] = user["user_id"]
        if body.to is not None:
            fields["to"] = [{"name": t.get("name") or t.get("email") or "", "email": (t.get("email") or "").strip().lower()} for t in body.to]
        if body.cc is not None:
            fields["cc"] = [{"name": t.get("name") or t.get("email") or "", "email": (t.get("email") or "").strip().lower()} for t in body.cc]
        if body.status in ("ready", "sent", "dismissed"):
            fields["status"] = body.status
            fields["status_by"] = user["user_id"]
            if body.status == "sent":
                fields["sent_at"] = now()
                fields["marked_sent_by_hand"] = True
        if not fields:
            return data.clean(_draft_view(d))
        out = store.update_draft(draft_id, fields)
        final = (out.get("body") or "").strip()
        if body.status == "sent" and not d.get("learned") and changed_enough(d.get("original_body") or d.get("body") or "", final):
            store.update_draft(draft_id, {"learned": True})
            lessons.learn_async(draft=d, final_text=final, source="edited")
        return data.clean(_draft_view(out))

    @app.post("/brain/drafts/{draft_id}/redo")
    def brain_redo_draft(draft_id: str, body: RedoBody, user=CurrentUser):
        d = store.get_draft(draft_id)
        if not d:
            raise HTTPException(404, "draft not found")
        if not d.get("reply_to_sha"):
            raise HTTPException(400, "this draft was written in chat; ask the chat to change it")

        def run(job):
            job.emit("status", {"text": "Rewriting the draft…"})
            instruction = body.instruction.strip()
            if d.get("body"):
                instruction = (instruction + "\n\n" if instruction else "") + "THE PREVIOUS DRAFT (rewrite it; keep what still fits):\n" + d["body"]
            out = writer.write(d["reply_to_sha"], pids=d.get("property_ids") or [], points=d.get("points") or [], sender=d.get("sender") or bc.DEFAULT_SENDER,
                               why=d.get("why") or "", urgency=d.get("urgency") or "", instruction=instruction, replaces=draft_id)
            if body.instruction.strip() and out and out.get("draft_id") and d.get("body"):
                lessons.learn_async(draft=d, final_text=out.get("body") or "", before=d["body"], source="rewrite_instruction", instruction=body.instruction.strip())
            return data.clean(_draft_view(out) if out and out.get("draft_id") else {"needed": False, "note": (out or {}).get("note")})
        return {"job_id": jobs.start("brain_draft", {"draft_id": draft_id, "by": user["user_id"]}, run).job_id}

    @app.post("/brain/drafts/{draft_id}/outlook")
    def brain_draft_outlook(draft_id: str, user=CurrentUser):
        if not store.get_draft(draft_id):
            raise HTTPException(404, "draft not found")
        try:
            return data.clean(writer.to_outlook(draft_id))
        except Exception as exc:
            logger.warning("outlook draft failed: %s", exc)
            raise HTTPException(502, f"Could not reach the Outlook mailbox: {str(exc)[:200]}")

    # -------------------------------------------------------------- lessons
    @app.get("/brain/lessons")
    def brain_lessons(user=CurrentUser):
        """What the system has learned from his corrections, with the edit each rule came from."""
        rows = lessons.list()
        pair_ids = [r["pair_ids"][-1] for r in rows if r.get("pair_ids")]
        pairs = {p["pair_id"]: p for p in lessons.pairs.find({"pair_id": {"$in": pair_ids}}, {"_id": 0})}
        for r in rows:
            p = pairs.get((r.get("pair_ids") or [None])[-1]) or {}
            r["from"] = {"subject": p.get("subject"), "what_he_changed": p.get("what_he_changed"), "at": p.get("at"),
                         "before": (p.get("before") or "")[:3000], "after": (p.get("after") or "")[:3000], "instruction": p.get("instruction")}
            r["applies_now"] = ("everyone" if r["applies_to"] == "everyone" and (r.get("times_seen") or 1) >= 2
                                else "this person, until seen again" if r["applies_to"] == "everyone" else r["applies_to"].replace("this_", "this "))
            r["properties"] = [name_of(p_) for p_ in r.get("property_ids") or []]
        return data.clean(rows)

    @app.delete("/brain/lessons/{lesson_id}")
    def brain_remove_lesson(lesson_id: str, user=CurrentUser):
        if not lessons.remove(lesson_id, user["user_id"]):
            raise HTTPException(404, "lesson not found")
        return {"ok": True}

    # ----------------------------------------------------------------- chat
    def _thread_pid(pid: Optional[str]) -> Optional[str]:
        if pid in (None, "", "portfolio"):
            return None
        return _file_id(pid)

    def _thread_view(t: Dict[str, Any], *, with_messages: bool) -> Dict[str, Any]:
        out = {k: t.get(k) for k in ("thread_id", "property_id", "title", "created_at", "updated_at", "n_messages")}
        out["property"] = name_of(t["property_id"]) if t.get("property_id") else "All properties"
        if with_messages:
            msgs = store.thread_messages(t["thread_id"])
            for m in msgs:
                m.pop("model_text", None)
                m.pop("usage", None)
                for f in m.get("files") or []:
                    f.pop("text", None)
                # A draft card always shows the draft as it is now.
                for d in m.get("drafts") or []:
                    cur = store.get_draft(d["draft_id"])
                    if cur:
                        d.update({k: cur.get(k) for k in ("to", "cc", "subject", "body", "note", "status")})
            out["messages"] = msgs
            out["active"] = [{"job_id": j.job_id, "text": (j.meta or {}).get("text")} for j in list(jobs.jobs.values())
                             if j.kind == "brain_chat" and j.status in ("queued", "running") and (j.meta or {}).get("thread_id") == t["thread_id"]]
        return out

    @app.get("/brain/chat/threads")
    def brain_threads(property_id: Optional[str] = None, user=CurrentUser):
        return data.clean([_thread_view(t, with_messages=False) for t in store.list_threads(_thread_pid(property_id))])

    @app.post("/brain/chat/threads")
    def brain_new_thread(body: ThreadBody, user=CurrentUser):
        return data.clean(_thread_view(store.create_thread(pid=_thread_pid(body.property_id), by=user["user_id"]), with_messages=True))

    @app.get("/brain/chat/current")
    def brain_current_thread(property_id: Optional[str] = None, user=CurrentUser):
        """The thread to show when a chat opens: the latest one for this scope, or a new one."""
        return data.clean(_thread_view(store.latest_thread(_thread_pid(property_id), user["user_id"]), with_messages=True))

    @app.get("/brain/chat/threads/{thread_id}")
    def brain_thread(thread_id: str, user=CurrentUser):
        t = store.get_thread(thread_id)
        if not t:
            raise HTTPException(404, "thread not found")
        return data.clean(_thread_view(t, with_messages=True))

    @app.delete("/brain/chat/threads/{thread_id}")
    def brain_archive_thread(thread_id: str, user=CurrentUser):
        store.threads.update_one({"thread_id": thread_id}, {"$set": {"archived": True, "archived_by": user["user_id"], "archived_at": now()}})
        return {"ok": True}

    @app.post("/brain/chat/threads/{thread_id}/files")
    async def brain_attach(thread_id: str, files: List[UploadFile] = File(...), user=CurrentUser):
        from .files import describe
        from .store import _id
        t = store.get_thread(thread_id)
        if not t:
            raise HTTPException(404, "thread not found")
        out, to_file = [], []
        for f in files:
            raw = await f.read()
            try:
                info = describe(raw, f.filename or "file", f.content_type or "")
            except ValueError as exc:
                raise HTTPException(413, str(exc))
            mongo.put_original(info["sha256"], raw, info["name"], {"source": "chat_attachment", "thread_id": thread_id, "by": user["user_id"]})
            doc = {"file_id": _id("f"), "thread_id": thread_id, "by": user["user_id"], "at": now(), **info}
            store.chat_files.insert_one(dict(doc))
            out.append({k: doc[k] for k in ("file_id", "name", "kind", "size")} | {"readable": bool(info["text"]) or info["kind"] in ("pdf", "image")})
            pid = t.get("property_id")
            if pid and pid in PROPERTY_INDEX:
                to_file.append((raw, info["name"], f.content_type, pid))
        # Also file it under the property, so it is part of the record from now on.
        if to_file:
            def keep(job):
                from mangotree.ingest.upload import UploadIngestor
                from mangotree.pipeline.arrival import ArrivalChain
                ing, shas, pid = UploadIngestor(mongo), [], to_file[0][3]
                for raw, name, ctype, pid in to_file:
                    try:
                        r = ing.ingest(raw, filename=name, property_id=pid, uploaded_by=user["user_id"], note="added in chat", content_type=ctype)
                        if r.needs_processing:
                            shas.append(r.sha256)
                    except Exception as exc:
                        logger.warning("chat attachment %s not filed: %s", name, exc)
                return data.clean(ArrivalChain(mongo).process_documents(shas, pid, emit=job.emit)) if shas else {"filed": 0}
            jobs.start("upload", {"property_id": to_file[0][3], "by": user["user_id"], "files": len(to_file), "from": "chat"}, keep)
        return data.clean(out)

    @app.post("/brain/chat/threads/{thread_id}/ask")
    def brain_ask(thread_id: str, body: AskBody, user=CurrentUser):
        t = store.get_thread(thread_id)
        if not t:
            raise HTTPException(404, "thread not found")
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty message")
        files = list(store.chat_files.find({"file_id": {"$in": body.file_ids}, "thread_id": thread_id}, {"_id": 0})) if body.file_ids else []

        def run(job):
            msg = engine.ask(thread_id, user=user, text=text, files=files, emit=job.emit, should_stop=lambda: job.cancelled)
            msg.pop("usage", None)
            msg.pop("model_text", None)
            return data.clean(msg)
        job = jobs.start("brain_chat", {"thread_id": thread_id, "text": text, "by": user["user_id"], "property_id": t.get("property_id")}, run)
        return {"job_id": job.job_id, "thread_id": thread_id}

    @app.post("/brain/chat/jobs/{job_id}/stop")
    def brain_stop(job_id: str, user=CurrentUser):
        return {"stopped": jobs.cancel(job_id)}

    # ------------------------------------------------------------- upkeep
    @app.post("/brain/run")
    def brain_run(user=CurrentUser):
        """Read whatever has arrived since the last pass, now."""
        job = jobs.start("brain_run", {"by": user["user_id"]}, lambda job: data.clean(updater.run_pending(emit=job.emit)))
        return {"job_id": job.job_id}

    @app.get("/brain/settings")
    def brain_settings(user=CurrentUser):
        from mangotree.mail.outbox import Outbox
        can_draft = False
        try:
            can_draft = Outbox(mongo).can_draft()
        except Exception:
            pass
        return {"enabled": bool(store.setting(bc.SETTING_ENABLED, True)), "outlook_drafts": bool(store.setting(bc.SETTING_OUTLOOK_DRAFTS, False)),
                "outlook_ready": can_draft, "model": bc.MODEL}

    @app.post("/brain/settings")
    def brain_set_settings(body: Dict[str, Any] = Body(...), user=CurrentUser):
        if user.get("role") != "ceo":
            raise HTTPException(403, "only Rakesh Sir can change these")
        if "enabled" in body:
            store.set_setting(bc.SETTING_ENABLED, bool(body["enabled"]), user["user_id"])
        if "outlook_drafts" in body:
            store.set_setting(bc.SETTING_OUTLOOK_DRAFTS, bool(body["outlook_drafts"]), user["user_id"])
        return brain_settings(user)
