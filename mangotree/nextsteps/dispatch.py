"""Send the next-steps sheets to JP Sir and Manjunath Sir.

One email per person per morning, from rakesh@mtreh.com, the moment the cycle
completes (admin directive 2026-09-16 — they are in India; the cycle ends in
their early afternoon). The cover note is the whole daily message: the steps
that matter most, anything carried over unfinished, the replies they still owe,
what is new since yesterday, and — if yesterday's sheet went unanswered — a
gentle word about that. Tomorrow's sheet is the reminder; nothing is re-sent.

Guards for the automatic send: the run completed for at least
NEXT_STEPS_AUTO_SEND_MIN_PROPERTIES properties, the person has a step or an
open follow-up, auto-send is on (Rakesh's switch on the dashboard). Rakesh's
own Send button ignores the guards except the last.

Wes's and Rakesh's sheets are never emailed by the system.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.followup.templates import next_steps_cover
from mangotree.followup.tracker import ADDRESS, LABEL, business_days_after, reminder_send_time
from mangotree.retrieve import config as cfg
from mangotree.storage.mongo import Mongo

from .generator import NextSteps
from .render import _day_label, build_docx, build_pdf, filename_for

TEAM = ("jp", "manjunath")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _top(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {"critical": 0, "high": 1}
    return sorted(steps, key=lambda s: (order.get(s.get("urgency"), 2), s.get("due") or datetime.max.replace(tzinfo=timezone.utc)))


def _new_since_previous(ns: NextSteps, run: Dict[str, Any], person: str) -> List[Dict[str, Any]]:
    prev = ns.runs.find_one({"status": "complete", "started_at": {"$lt": run.get("started_at")}}, {"_id": 0}, sort=[("started_at", -1)])
    if not prev:
        return []
    seen = {(pid, (s.get("title") or "").strip().lower()) for pid in prev.get("order") or [] for s in ((prev.get("properties") or {}).get(pid) or {}).get(person) or []}
    return [s for s in ns.for_person(person, run) if (s["property_id"], (s.get("title") or "").strip().lower()) not in seen]


def auto_send_enabled(mongo: Mongo) -> bool:
    doc = mongo.db["settings"].find_one({"_id": "next_steps_auto_send"})
    return bool(doc["value"]) if doc and "value" in doc else bool(cfg.NEXT_STEPS_AUTO_SEND)


def set_auto_send(mongo: Mongo, value: bool, by: str) -> None:
    mongo.db["settings"].update_one({"_id": "next_steps_auto_send"}, {"$set": {"value": bool(value), "by": by, "at": datetime.now(timezone.utc)}}, upsert=True)


def _guards(mongo: Mongo, run: Dict[str, Any], person: str, steps: List[dict], fups: List[dict]) -> Optional[str]:
    if run.get("status") != "complete":
        return "run not complete"
    ok = sum(1 for r in (run.get("properties") or {}).values() if not r.get("error"))
    if ok < cfg.NEXT_STEPS_AUTO_SEND_MIN_PROPERTIES:
        return f"only {ok} of {len(run.get('order') or [])} properties completed"
    if not steps and not fups:
        return "nothing on the sheet and no reply owed"
    return None


def send_to_team(mongo: Mongo, run: Dict[str, Any], *, by: str, outbox, persons=TEAM, auto: bool = False) -> Dict[str, Any]:
    from mangotree.config.settings import SETTINGS
    from mangotree.followup.tracker import FollowupTracker
    ns = NextSteps(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "")
    tracker = FollowupTracker(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    day_label = _day_label(run)
    now = datetime.now(timezone.utc)
    out: Dict[str, Any] = {}
    if auto and not auto_send_enabled(mongo):
        return {"skipped": "auto-send is off"}
    for person in persons:
        steps = ns.for_person(person, run)
        all_fups = tracker.list(owner=person, statuses=("open", "escalated"))
        if auto:
            why = _guards(mongo, run, person, steps, all_fups)
            if why:
                out[person] = {"skipped": why}
                logger.info("next steps: not auto-sending to %s — %s", person, why)
                continue
        fups, _more = tracker.top_for(person, all_fups, 3)
        carried = [s for s in steps if int(s.get("carried_days") or 0) > 0 and not s.get("done")]
        # Yesterday's sheet, if it went unanswered, is superseded by today's —
        # and the cover note says so, gently.
        prev_unanswered = outbox.coll.find_one({"kind": "next_steps", "status": "sent", "meta.person": person, "meta.run_id": {"$ne": run["run_id"]}},
                                               {"_id": 0, "outbox_id": 1, "sent_at": 1}, sort=[("sent_at", -1)])
        subject, html, text = next_steps_cover(person, day_label=day_label, top=_top(steps), followups=fups,
                                               new_since_last=_new_since_previous(ns, run, person), carried=carried,
                                               unacknowledged_since=(prev_unanswered or {}).get("sent_at"))
        atts = [(filename_for(run, person, "docx"), build_docx(run, person), DOCX_MIME),
                (filename_for(run, person, "pdf"), build_pdf(run, person), "application/pdf")]
        q = outbox.queue(kind="next_steps", ref=f"{run['run_id']}:{person}", to=[(LABEL[person], ADDRESS.get(person, ""))],
                         subject=subject, html=html, text=text, attachments=atts, send_after=reminder_send_time() if auto else None,
                         meta={"run_id": run["run_id"], "person": person, "day": run.get("day"), "steps": len(steps), "by": by, "auto": auto,
                               "followup_ids": [f["followup_id"] for f in fups]})
        if not q.get("deduped"):
            # Yesterday's sheet — sent and unanswered, or still waiting for the
            # send consent — is superseded: today's is the one that should arrive.
            for old in outbox.coll.find({"kind": "next_steps", "status": {"$in": ["sent", "queued", "needs_consent", "failed"]},
                                         "meta.person": person, "meta.run_id": {"$ne": run["run_id"]}}, {"_id": 0, "outbox_id": 1}):
                outbox.coll.update_one({"outbox_id": old["outbox_id"]}, {"$set": {"status": "superseded", "superseded_by": q["outbox_id"], "superseded_at": now}})
                tracker.coll.update_many({"kind": "report_ack", "outbox_id": old["outbox_id"], "status": {"$in": ["open", "escalated"]}},
                                         {"$set": {"status": "dismissed", "closed_reason": "superseded by today's sheet", "closed_at": now}})
            tracker.coll.insert_one({
                "followup_id": f"fu-ack-{q['outbox_id'][3:]}", "kind": "report_ack", "owner": person, "outbox_id": q["outbox_id"],
                "property_ids": [], "thread_key": None, "subject": subject,
                "counterparty": {"name": LABEL[person], "email": ADDRESS.get(person, ""), "person_id": person},
                "what": f"Acknowledge the next-steps sheet of {day_label}", "topic": "documents", "source_sha": None,
                "asked_at": now, "created_at": now, "updated_at": now,
                "due": business_days_after(now, cfg.FOLLOWUP_INTERNAL_DUE_BUSINESS_DAYS),
                "status": "open", "reminders": [], "last_reminder_at": None, "escalated_at": None, "run_id": run["run_id"]})
            # The follow-ups named in the cover note count as reminded today, so
            # the separate digest does not repeat them.
            if fups:
                tracker.coll.update_many({"followup_id": {"$in": [f["followup_id"] for f in fups]}},
                                         {"$set": {"last_reminder_at": now}, "$push": {"reminders": {"at": now, "mode": "sheet_email", "to": person, "outbox_id": q["outbox_id"]}}})
        out[person] = q
    ns.runs.update_one({"run_id": run["run_id"]}, {"$set": {"sent": {"at": now, "by": by, "auto": auto, "outbox": out}}})
    try:
        out["flush"] = outbox.flush()
    except Exception as exc:
        out["flush_error"] = str(exc)[:200]
    return out


def team_preview(mongo: Mongo, run: Dict[str, Any], person: str, outbox) -> Dict[str, Any]:
    """The cover note exactly as it would go to JP Sir / Manjunath Sir now — and,
    if this run's email already went, what was actually sent."""
    from mangotree.config.settings import SETTINGS
    from mangotree.followup.tracker import FollowupTracker
    ns = NextSteps(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "")
    tracker = FollowupTracker(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    already = outbox.coll.find_one({"kind": "next_steps", "meta.run_id": run["run_id"], "meta.person": person}, {"_id": 0, "attachments.bytes": 0, "html": 0})
    if already:
        return {"to": already["to"][0], "subject": already["subject"], "body": already.get("text"), "attachments": [a["filename"] for a in already.get("attachments") or []],
                "already_sent": already, "as_sent": True}
    steps = ns.for_person(person, run)
    fups, _ = tracker.top_for(person, tracker.list(owner=person, statuses=("open", "escalated")), 3)
    carried = [s for s in steps if int(s.get("carried_days") or 0) > 0 and not s.get("done")]
    prev_unanswered = outbox.coll.find_one({"kind": "next_steps", "status": "sent", "meta.person": person, "meta.run_id": {"$ne": run["run_id"]}},
                                           {"_id": 0, "sent_at": 1}, sort=[("sent_at", -1)])
    subject, _html, text = next_steps_cover(person, day_label=_day_label(run), top=_top(steps), followups=fups,
                                            new_since_last=_new_since_previous(ns, run, person), carried=carried,
                                            unacknowledged_since=(prev_unanswered or {}).get("sent_at"))
    return {"to": {"name": LABEL[person], "address": ADDRESS.get(person, "")}, "subject": subject, "body": text,
            "attachments": [filename_for(run, person, "docx"), filename_for(run, person, "pdf")], "already_sent": None, "as_sent": False}


# ------------------------------------------------------------------- Wes
WES_ADDRESS = ADDRESS.get("wes", "wes@roiblocks.com")


def wes_preview(mongo: Mongo, run: Dict[str, Any]) -> Dict[str, Any]:
    """What Rakesh sees before pressing Send: the note as drafted, the attachments."""
    from mangotree.config.settings import SETTINGS
    from mangotree.followup.templates import wes_cover
    ns = NextSteps(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "")
    steps = ns.for_person("wes", run)
    carried = [s for s in steps if int(s.get("carried_days") or 0) > 0 and not s.get("done")]
    props = len({s["property_id"] for s in steps})
    subject, html, text = wes_cover(day_label=_day_label(run), top=_top(steps), carried=carried, properties=props)
    already = mongo.db["outbox"].find_one({"kind": "next_steps_wes", "meta.run_id": run["run_id"]}, {"_id": 0, "attachments.bytes": 0, "html": 0})
    return {"to": {"name": "Wes Stone", "address": WES_ADDRESS}, "subject": subject, "body": text, "steps": len(steps), "properties": props,
            "attachments": [filename_for(run, "wes", "docx"), filename_for(run, "wes", "pdf")], "already_sent": already}


def send_to_wes(mongo: Mongo, run: Dict[str, Any], *, by: str, outbox, subject: Optional[str] = None, body: Optional[str] = None) -> Dict[str, Any]:
    """One press: Wes's sheet (Word + PDF) with the cover note, from rakesh@mtreh.com,
    and an 'awaiting Wes — acknowledge the sheet' follow-up."""
    from mangotree.config.settings import SETTINGS
    from mangotree.followup.tracker import FollowupTracker
    import html as _h
    prev = wes_preview(mongo, run)
    subject = (subject or prev["subject"]).strip()[:200]
    text = (body or prev["body"]).strip()
    html = ("<div style='font-family:Calibri,Segoe UI,Helvetica,Arial,sans-serif;font-size:14.5px;line-height:1.55;color:#2a2a2e;max-width:640px;white-space:pre-wrap'>"
            + _h.escape(text) + "</div>")
    atts = [(filename_for(run, "wes", "docx"), build_docx(run, "wes"), DOCX_MIME),
            (filename_for(run, "wes", "pdf"), build_pdf(run, "wes"), "application/pdf")]
    now = datetime.now(timezone.utc)
    q = outbox.queue(kind="next_steps_wes", ref=f"{run['run_id']}:wes:{now:%Y%m%d%H%M%S}", to=[("Wes Stone", WES_ADDRESS)], subject=subject, html=html, text=text,
                     attachments=atts, dedupe=False, meta={"run_id": run["run_id"], "person": "wes", "day": run.get("day"), "by": by, "steps": prev["steps"]})
    tracker = FollowupTracker(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    tracker.coll.update_many({"kind": "ask_external", "counterparty.person_id": "wes", "topic": "documents", "status": {"$in": ["open", "escalated"]},
                              "what": {"$regex": "^Acknowledge the next-steps sheet"}},
                             {"$set": {"status": "dismissed", "closed_reason": "superseded by today's sheet", "closed_at": now}})
    tracker.coll.insert_one({
        "followup_id": f"fu-wes-{q['outbox_id'][3:]}", "kind": "ask_external", "owner": "rakesh", "outbox_id": q["outbox_id"],
        "property_ids": [], "thread_key": None, "subject": subject,
        "counterparty": {"name": "Wes Stone", "email": WES_ADDRESS, "person_id": "wes"},
        "what": f"Acknowledge the next-steps sheet of {_day_label(run)} and say where each item stands", "topic": "documents", "source_sha": None,
        "asked_at": now, "created_at": now, "updated_at": now,
        "due": business_days_after(now, cfg.FOLLOWUP_EXTERNAL_DUE_BUSINESS_DAYS),
        "status": "open", "reminders": [], "last_reminder_at": None, "escalated_at": None, "run_id": run["run_id"]})
    NextSteps(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "") \
        .runs.update_one({"run_id": run["run_id"]}, {"$set": {"sent_wes": {"at": now, "by": by, "outbox_id": q["outbox_id"]}}})
    try:
        flush = outbox.flush()
    except Exception as exc:
        flush = {"error": str(exc)[:200]}
    return {"queued": q, "flush": flush, "status": (outbox.get(q["outbox_id"]) or {}).get("status")}


def auto_send_after_run(mongo: Mongo, run: Dict[str, Any], outbox) -> Dict[str, Any]:
    """Called by the morning cycle once the sheets exist."""
    if not run or run.get("status") != "complete":
        return {"skipped": "no complete run"}
    return send_to_team(mongo, run, by="morning", outbox=outbox, auto=True)
