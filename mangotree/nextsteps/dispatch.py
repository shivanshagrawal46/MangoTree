"""Send the next-steps sheets to JP Sir and Manjunath Sir, and chase the acknowledgement.

``send_to_team(run)`` — on Rakesh's confirmation only — renders each person's
DOCX and PDF, writes the cover note (what matters most today, what they still
owe a reply on, what is new since the previous sheet), queues the email on the
outbox and opens a ``report_ack`` follow-up that closes when they reply to that
email. ``remind_unacknowledged`` re-sends the sheet, kindly, once a day while
the acknowledgement is missing. Wes's and Rakesh's sheets are never emailed by
the system: Rakesh downloads Wes's and sends it himself.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.followup.templates import next_steps_cover
from mangotree.followup.tracker import ADDRESS, LABEL, business_days_after
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


def send_to_team(mongo: Mongo, run: Dict[str, Any], *, by: str, outbox, persons=TEAM) -> Dict[str, Any]:
    from mangotree.followup.tracker import FollowupTracker
    from mangotree.config.settings import SETTINGS
    ns = NextSteps(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "")
    tracker = FollowupTracker(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    day_label = _day_label(run)
    out: Dict[str, Any] = {}
    for person in persons:
        steps = ns.for_person(person, run)
        fups, _more = tracker.top_for(person, tracker.list(owner=person, statuses=("open", "escalated")), 3)
        subject, html, text = next_steps_cover(person, day_label=day_label, top=_top(steps), followups=fups,
                                               new_since_last=_new_since_previous(ns, run, person))
        atts = [(filename_for(run, person, "docx"), build_docx(run, person), DOCX_MIME),
                (filename_for(run, person, "pdf"), build_pdf(run, person), "application/pdf")]
        q = outbox.queue(kind="next_steps", ref=f"{run['run_id']}:{person}", to=[(LABEL[person], ADDRESS.get(person, ""))],
                         subject=subject, html=html, text=text, attachments=atts,
                         meta={"run_id": run["run_id"], "person": person, "day": run.get("day"), "steps": len(steps), "by": by})
        if not q.get("deduped"):
            tracker.coll.insert_one({
                "followup_id": f"fu-ack-{q['outbox_id'][3:]}", "kind": "report_ack", "owner": person, "outbox_id": q["outbox_id"],
                "property_ids": [], "thread_key": None, "subject": subject,
                "counterparty": {"name": LABEL[person], "email": ADDRESS.get(person, ""), "person_id": person},
                "what": f"Acknowledge the next-steps sheet of {day_label}", "topic": "documents", "source_sha": None,
                "asked_at": datetime.now(timezone.utc), "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
                "due": business_days_after(datetime.now(timezone.utc), cfg.FOLLOWUP_INTERNAL_DUE_BUSINESS_DAYS),
                "status": "open", "reminders": [], "last_reminder_at": None, "escalated_at": None, "run_id": run["run_id"]})
        out[person] = q
    ns.runs.update_one({"run_id": run["run_id"]}, {"$set": {"sent": {"at": datetime.now(timezone.utc), "by": by, "outbox": out}}})
    try:
        out["flush"] = outbox.flush()
    except Exception as exc:
        out["flush_error"] = str(exc)[:200]
    return out


def remind_unacknowledged(mongo: Mongo, outbox) -> Dict[str, int]:
    """Once a day, re-send a next-steps email that got no reply."""
    from mangotree.config.settings import SETTINGS
    from mangotree.followup.tracker import FollowupTracker
    now = datetime.now(timezone.utc)
    out = {"reminded": 0}
    ns = NextSteps(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key, openai_api_key=SETTINGS.openai_api_key_critic or "")
    tracker = FollowupTracker(mongo, anthropic_api_key=SETTINGS.anthropic_api_key)
    for ob in outbox.coll.find({"kind": "next_steps", "status": "sent"}, {"_id": 0, "outbox_id": 1, "meta": 1, "sent_at": 1, "last_reminder_at": 1, "to": 1, "reminders": 1}):
        sent_at = ob.get("sent_at") or now
        if now < business_days_after(sent_at, cfg.FOLLOWUP_INTERNAL_DUE_BUSINESS_DAYS):
            continue
        last = ob.get("last_reminder_at")
        if isinstance(last, datetime) and (now - last) < timedelta(hours=cfg.FOLLOWUP_MIN_HOURS_BETWEEN_REMINDERS):
            continue
        if len(ob.get("reminders") or []) >= 3:
            continue
        run = ns.get((ob.get("meta") or {}).get("run_id") or "")
        person = (ob.get("meta") or {}).get("person")
        if not run or person not in TEAM:
            continue
        # A newer sheet supersedes the acknowledgement of an older one.
        if ns.latest() and ns.latest()["run_id"] != run["run_id"]:
            outbox.coll.update_one({"outbox_id": ob["outbox_id"]}, {"$set": {"status": "superseded"}})
            mongo.db["followups"].update_many({"kind": "report_ack", "outbox_id": ob["outbox_id"], "status": {"$in": ["open", "escalated"]}},
                                              {"$set": {"status": "dismissed", "closed_reason": "superseded by a newer sheet", "closed_at": now}})
            continue
        steps = ns.for_person(person, run)
        fups, _more = tracker.top_for(person, tracker.list(owner=person, statuses=("open", "escalated")), 3)
        subject, html, text = next_steps_cover(person, day_label=_day_label(run), top=_top(steps), followups=fups, new_since_last=[], is_reminder=True)
        atts = [(filename_for(run, person, "docx"), build_docx(run, person), DOCX_MIME),
                (filename_for(run, person, "pdf"), build_pdf(run, person), "application/pdf")]
        from mangotree.followup.tracker import reminder_send_time
        q = outbox.queue(kind="next_steps_reminder", ref=f"{ob['outbox_id']}:{now:%Y%m%d}", to=ob["to"], subject=subject, html=html, text=text,
                         attachments=atts, send_after=reminder_send_time(),
                         meta={"for_outbox_id": ob["outbox_id"], "person": person, "run_id": run["run_id"]})
        outbox.coll.update_one({"outbox_id": ob["outbox_id"]}, {"$set": {"last_reminder_at": now}, "$push": {"reminders": {"at": now, "outbox_id": q.get("outbox_id")}}})
        out["reminded"] += 1
    return out
