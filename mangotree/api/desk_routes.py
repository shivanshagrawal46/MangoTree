"""Routes for the next-steps reports, the follow-up tracker, the outbox and the
personal desks. Installed by ``app.py`` (``install(app, mongo, jobs)``) so the
main module stays readable.

Who may do what
  * anyone signed in reads their own desk, their follow-ups, their sheet
  * generating a run, viewing every sheet, sending to the team, and the outbox
    are the CEO's (``role == "ceo"``)
"""
from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Response
from pydantic import BaseModel

from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg

from . import data
from .auth import CurrentUser

PERSON_BY_USER = {"rakesh": "rakesh", "jp": "jp", "manjunath": "manjunath"}
OWNER_BY_USER = {"rakesh": "Rakesh", "jp": "JP", "manjunath": "Manjunath"}
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class FollowupStatus(BaseModel):
    status: str
    remark: str = ""


class StepDone(BaseModel):
    property_id: str
    person: str
    index: int
    done: bool = True


class SendBody(BaseModel):
    persons: List[str] = ["jp", "manjunath"]
    confirm: bool = False


class ExternalSend(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None


class WesSend(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    confirm: bool = False


class AutoSend(BaseModel):
    enabled: bool


class CycleSwitch(BaseModel):
    enabled: bool


def install(app, mongo, jobs) -> None:
    from mangotree.followup.tracker import FollowupTracker
    from mangotree.mail.outbox import Outbox
    from mangotree.nextsteps import dispatch
    from mangotree.nextsteps.generator import PERSONS, NextSteps
    from mangotree.nextsteps.render import build_docx, build_pdf, filename_for, sections, subtitle_for

    ns = NextSteps(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key,
                   openai_api_key=SETTINGS.openai_api_key_critic or "")
    tracker = FollowupTracker(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    outbox = Outbox(mongo)
    app.state.next_steps = ns
    app.state.followups = tracker
    app.state.outbox = outbox

    def ceo(user) -> None:
        if user.get("role") != "ceo":
            raise HTTPException(403, "Rakesh Sir only")

    def run_or_404(run_id: str) -> Dict[str, Any]:
        run = ns.latest() if run_id == "latest" else ns.get(run_id)
        if not run:
            raise HTTPException(404, "no next-steps run yet")
        return run

    def slim(run: Dict[str, Any], persons=None) -> Dict[str, Any]:
        """The run without investigation payloads; optionally one person's view."""
        out = {k: v for k, v in run.items() if k != "properties"}
        props = {}
        for pid, r in (run.get("properties") or {}).items():
            row = {k: v for k, v in r.items() if k != "investigation"}
            if persons is not None:
                for p in PERSONS:
                    if p not in persons:
                        row.pop(p, None)
            props[pid] = row
        out["properties"] = props
        out["subtitle"] = subtitle_for(run)
        return out

    # ---------------------------------------------------------------- runs
    @app.post("/next-steps/generate")
    def next_steps_generate(user=CurrentUser):
        """Rakesh's button. Fourteen fast-mode reviews, four sheets. Confirmation
        happens in the UI; this starts the job and streams progress."""
        ceo(user)
        running = ns.runs.find_one({"status": "running", "started_at": {"$gt": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)}}, {"_id": 0, "run_id": 1})
        if running and jobs.active_of_kind("next_steps"):
            raise HTTPException(409, "a next-steps run is already in progress")

        def run(job):
            def emit(kind, payload):
                job.emit(kind, payload)
            out = ns.generate(by=user["user_id"], emit=emit)
            data.invalidate_portfolio()
            return data.clean(slim(out))
        job = jobs.start("next_steps", {"by": user["user_id"]}, run)
        return {"job_id": job.job_id}

    def in_progress() -> Optional[Dict[str, Any]]:
        """A run still going — started from the button, the morning cycle or a
        script — with its progress, so the panel can say so."""
        from datetime import timedelta
        r = ns.runs.find_one({"status": "running", "started_at": {"$gt": datetime.now(timezone.utc) - timedelta(hours=2)}},
                             {"_id": 0, "run_id": 1, "started_at": 1, "by": 1, "progress": 1})
        return r

    @app.get("/next-steps/latest")
    def next_steps_latest(user=CurrentUser):
        run = ns.latest()
        if not run:
            return data.clean({"run": None, "send_status": outbox.send_status() if user.get("role") == "ceo" else None,
                               "in_progress": in_progress(), "running": bool(in_progress())})
        if user.get("role") == "ceo":
            sent = list(outbox.coll.find({"kind": "next_steps", "meta.run_id": run["run_id"]}, {"_id": 0, "attachments.bytes": 0, "html": 0}))
            prog = in_progress()
            return data.clean({"run": slim(run), "sent": sent, "send_status": outbox.send_status(),
                               "running": bool(jobs.active_of_kind("next_steps")) or bool(prog), "in_progress": prog,
                               "auto_send": dispatch.auto_send_enabled(mongo)})
        person = PERSON_BY_USER.get(user["user_id"])
        return data.clean({"run": slim(run, persons=[person] if person else []), "person": person})

    @app.get("/next-steps/runs")
    def next_steps_runs(user=CurrentUser):
        ceo(user)
        rows = list(ns.runs.find({}, {"_id": 0, "run_id": 1, "day": 1, "started_at": 1, "finished_at": 1, "status": 1, "counts": 1, "by": 1, "sent": 1, "elapsed_s": 1, "errors": 1})
                    .sort("started_at", -1).limit(30))
        return data.clean(rows)

    @app.get("/next-steps/{run_id}/sheet/{person}")
    def next_steps_sheet(run_id: str, person: str, user=CurrentUser):
        """One person's sheet as JSON, in report order (what the web view renders)."""
        run = run_or_404(run_id)
        if person not in PERSONS:
            raise HTTPException(404, "unknown person")
        if user.get("role") != "ceo" and PERSON_BY_USER.get(user["user_id"]) != person:
            raise HTTPException(403, "not your sheet")
        return data.clean({"run_id": run["run_id"], "day": run.get("day"), "person": person, "subtitle": subtitle_for(run),
                           "sections": sections(run, person), "status": run.get("status")})

    @app.get("/next-steps/{run_id}/download/{person}.{ext}")
    def next_steps_download(run_id: str, person: str, ext: str, user=CurrentUser):
        run = run_or_404(run_id)
        if person not in PERSONS or ext not in ("docx", "pdf"):
            raise HTTPException(404, "unknown sheet")
        if user.get("role") != "ceo" and PERSON_BY_USER.get(user["user_id"]) != person:
            raise HTTPException(403, "not your sheet")
        blob = build_docx(run, person) if ext == "docx" else build_pdf(run, person)
        name = filename_for(run, person, ext)
        return Response(content=blob, media_type=DOCX_MIME if ext == "docx" else "application/pdf",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

    @app.post("/next-steps/{run_id}/send")
    def next_steps_send(run_id: str, body: SendBody, user=CurrentUser):
        """Email JP Sir's and Manjunath Sir's sheets from rakesh@mtreh.com. Needs
        confirm=true — the UI asks first."""
        ceo(user)
        if not body.confirm:
            raise HTTPException(400, "confirm=true required")
        run = run_or_404(run_id)
        persons = [p for p in body.persons if p in dispatch.TEAM]
        if not persons:
            raise HTTPException(400, "persons must be jp and/or manjunath")
        out = dispatch.send_to_team(mongo, run, by=user["user_id"], outbox=outbox, persons=persons)
        return data.clean({"result": out, "send_status": outbox.send_status()})

    # Request models live at module level: with ``from __future__ import
    # annotations`` FastAPI cannot resolve a class defined inside this function
    # and silently treats the parameter as a query string (seen 2026-09-16).
    @app.get("/next-steps/{run_id}/wes-preview")
    def next_steps_wes_preview(run_id: str, user=CurrentUser):
        ceo(user)
        return data.clean(dispatch.wes_preview(mongo, run_or_404(run_id)))

    @app.get("/next-steps/{run_id}/preview/{person}")
    def next_steps_team_preview(run_id: str, person: str, user=CurrentUser):
        """The cover note for JP Sir / Manjunath Sir — as it would go now, or as it went."""
        ceo(user)
        if person not in dispatch.TEAM:
            raise HTTPException(404, "jp or manjunath")
        return data.clean(dispatch.team_preview(mongo, run_or_404(run_id), person, outbox))

    @app.post("/next-steps/{run_id}/send-wes")
    def next_steps_send_wes(run_id: str, body: WesSend, user=CurrentUser):
        """Rakesh's one press: Wes's sheet with the (possibly edited) cover note."""
        ceo(user)
        if not body.confirm:
            raise HTTPException(400, "confirm=true required")
        out = dispatch.send_to_wes(mongo, run_or_404(run_id), by=user["user_id"], outbox=outbox, subject=body.subject, body=body.body)
        return data.clean({"result": out, "send_status": outbox.send_status()})

    @app.get("/morning-cycle")
    def morning_cycle_get(user=CurrentUser):
        ceo(user)
        doc = mongo.db["settings"].find_one({"_id": "morning_cycle_enabled"}, {"_id": 0})
        return {"enabled": True if doc is None else bool(doc.get("value", True)), "by": (doc or {}).get("by"), "at": (doc or {}).get("at")}

    @app.post("/morning-cycle")
    def morning_cycle_set(body: CycleSwitch, user=CurrentUser):
        """Pause / resume the daily analysis (investigation, follow-ups, tasks,
        cards, ledger, sheets, brief). Mail intake and the outbox are unaffected."""
        ceo(user)
        mongo.db["settings"].update_one({"_id": "morning_cycle_enabled"},
                                        {"$set": {"value": bool(body.enabled), "by": user["user_id"], "at": datetime.now(timezone.utc)}}, upsert=True)
        return {"enabled": bool(body.enabled)}

    @app.get("/next-steps/auto-send")
    def next_steps_auto_send_get(user=CurrentUser):
        ceo(user)
        return {"enabled": dispatch.auto_send_enabled(mongo), "min_properties": cfg.NEXT_STEPS_AUTO_SEND_MIN_PROPERTIES}

    @app.post("/next-steps/auto-send")
    def next_steps_auto_send_set(body: AutoSend, user=CurrentUser):
        """Rakesh's switch: send JP Sir's and Manjunath Sir's sheets automatically
        when the morning cycle completes (their early afternoon)."""
        ceo(user)
        dispatch.set_auto_send(mongo, body.enabled, by=user["user_id"])
        return {"enabled": dispatch.auto_send_enabled(mongo)}

    @app.post("/next-steps/{run_id}/done")
    def next_steps_done(run_id: str, body: StepDone, user=CurrentUser):
        run = run_or_404(run_id)
        if user.get("role") != "ceo" and PERSON_BY_USER.get(user["user_id"]) != body.person:
            raise HTTPException(403, "not your step")
        ok = ns.mark_done(run["run_id"], body.property_id, body.person, body.index, done=body.done, by=user["user_id"])
        if not ok:
            raise HTTPException(404, "step not found")
        return {"ok": True}

    # ----------------------------------------------------------- follow-ups
    @app.get("/followups")
    def followups_list(owner: Optional[str] = None, status: Optional[str] = None, property_id: Optional[str] = None, user=CurrentUser):
        statuses = [s for s in (status or "open,escalated").split(",") if s]
        if user.get("role") != "ceo":
            owner = PERSON_BY_USER.get(user["user_id"])
        rows = tracker.list(owner=owner, statuses=statuses, property_id=property_id)
        counts = {}
        for r in tracker.coll.aggregate([{"$match": {"status": {"$in": ["open", "escalated"]}}}, {"$group": {"_id": {"owner": "$owner", "kind": "$kind"}, "n": {"$sum": 1}}}]):
            counts.setdefault(r["_id"].get("owner") or "?", {})[r["_id"].get("kind")] = r["n"]
        st = tracker.state.find_one({"_id": "tick"}, {"_id": 0})
        return data.clean({"items": rows, "counts": counts, "last_tick": (st or {}).get("at"), "since": cfg.FOLLOWUP_SINCE,
                           "rules": {"external_due_business_days": cfg.FOLLOWUP_EXTERNAL_DUE_BUSINESS_DAYS,
                                     "internal_due_business_days": cfg.FOLLOWUP_INTERNAL_DUE_BUSINESS_DAYS,
                                     "escalate_after_business_days": cfg.FOLLOWUP_ESCALATE_AFTER_BUSINESS_DAYS}})

    @app.post("/followups/{followup_id}/status")
    def followups_status(followup_id: str, body: FollowupStatus, user=CurrentUser):
        f = tracker.coll.find_one({"followup_id": followup_id}, {"_id": 0, "owner": 1})
        if not f:
            raise HTTPException(404, "follow-up not found")
        if user.get("role") != "ceo" and f.get("owner") != PERSON_BY_USER.get(user["user_id"]):
            raise HTTPException(403, "not your follow-up")
        try:
            doc = tracker.set_status(followup_id, body.status, by=user["user_id"], remark=body.remark)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return data.clean(doc)

    @app.post("/followups/{followup_id}/send-reminder")
    def followups_send_external(followup_id: str, body: ExternalSend, user=CurrentUser):
        """Rakesh's one click: send the drafted reminder to Wes / Kelly from rakesh@mtreh.com."""
        ceo(user)
        f = tracker.coll.find_one({"followup_id": followup_id})
        if not f or f.get("kind") != "ask_external":
            raise HTTPException(404, "external follow-up not found")
        from mangotree.followup.templates import external_reminder
        subj, text = external_reminder(f)
        subj = body.subject or (f.get("draft") or {}).get("subject") or subj
        text = body.body or (f.get("draft") or {}).get("body") or text
        to_addr = (f.get("counterparty") or {}).get("email")
        if not to_addr:
            raise HTTPException(400, "no email address for the counterparty")
        html = "<div style='font-family:Calibri,Segoe UI,Helvetica,Arial,sans-serif;font-size:14.5px;line-height:1.55;color:#2a2a2e;white-space:pre-wrap'>" + \
               text.replace("&", "&amp;").replace("<", "&lt;") + "</div>"
        q = outbox.queue(kind="external_reminder", ref=f"{followup_id}:{datetime.now(timezone.utc):%Y%m%d%H%M}", to=[((f.get("counterparty") or {}).get("name") or to_addr, to_addr)],
                         subject=subj, html=html, text=text, meta={"followup_id": followup_id, "by": user["user_id"]}, dedupe=False)
        tracker.coll.update_one({"followup_id": followup_id}, {"$set": {"last_reminder_at": datetime.now(timezone.utc)},
                                                              "$push": {"reminders": {"at": datetime.now(timezone.utc), "mode": "email", "to": to_addr, "outbox_id": q.get("outbox_id"), "by": user["user_id"]}}})
        flush = outbox.flush()
        return data.clean({"queued": q, "flush": flush, "send_status": outbox.send_status()})

    @app.post("/followups/tick")
    def followups_tick(user=CurrentUser):
        """Run the follow-up batch now (it otherwise runs once, in the morning cycle):
        new mail read for asks, replies to system mail, reminders composed."""
        ceo(user)
        def run(job):
            job.emit("status", {"text": "GPT-6 Astra reading new mail for asks, checking replies, composing due reminders…"})
            return data.clean(tracker.morning(outbox))
        job = jobs.start("followups", {"by": user["user_id"]}, run)
        return {"job_id": job.job_id}

    # --------------------------------------------------------------- outbox
    @app.get("/outbox")
    def outbox_list(user=CurrentUser):
        ceo(user)
        return data.clean({"items": outbox.list(), "send_status": outbox.send_status()})

    @app.post("/outbox/flush")
    def outbox_flush(user=CurrentUser):
        ceo(user)
        return data.clean({"result": outbox.flush(), "send_status": outbox.send_status()})

    # ----------------------------------------------------------------- desk
    @app.get("/desk")
    def desk(user=CurrentUser):
        """JP Sir's and Manjunath Sir's home: their next steps, their follow-ups,
        their tasks — nothing else. Rakesh gets the same shape for his own items
        plus the team's counts."""
        from mangotree.tasks.store import TaskStore
        person = PERSON_BY_USER.get(user["user_id"])
        owner = OWNER_BY_USER.get(user["user_id"])
        run = ns.latest()
        steps = ns.for_person(person, run) if person and run else []
        fups = tracker.list(owner=person) if person else []
        my_tasks = TaskStore(mongo).list(owner=owner, statuses=("open",), limit=100) if owner else []
        sheet_mail = None
        if person in dispatch.TEAM and run:
            sheet_mail = outbox.coll.find_one({"kind": "next_steps", "meta.run_id": run["run_id"], "meta.person": person},
                                              {"_id": 0, "status": 1, "sent_at": 1, "replied_at": 1, "subject": 1})
        team = None
        if user.get("role") == "ceo":
            team = {}
            for p in ("jp", "manjunath", "wes"):
                team[p] = {"steps": len(ns.for_person(p, run)) if run else 0,
                           "followups": tracker.coll.count_documents({"owner": p, "status": {"$in": ["open", "escalated"]}}) if p != "wes" else
                                        tracker.coll.count_documents({"kind": "ask_external", "counterparty.person_id": "wes", "status": {"$in": ["open", "escalated"]}})}
        return data.clean({"user": user, "person": person, "run": {k: run.get(k) for k in ("run_id", "day", "status", "finished_at")} if run else None,
                           "subtitle": subtitle_for(run) if run else None, "steps": steps, "followups": fups, "tasks": my_tasks,
                           "sheet_mail": sheet_mail, "team": team})

    logger.info("desk routes installed (next-steps, follow-ups, outbox, desk)")
