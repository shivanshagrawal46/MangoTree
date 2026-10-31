"""Recurring sends: the same email, every day at a set local time, until stopped.

Admin directive 2026-10-31: the Durham / Lane Pl reconciliation email goes to Wes
and Kelly every day at 6:05 a.m. Eastern, cc Manjunath and Shivansh, until Rakesh
Sir says stop.

A recurrence is a stored email plus a clock. On every scheduler tick (once a
minute) ``tick`` queues today's copy on the outbox as soon as the day's send
time is within the next hour, held with ``send_after`` = that time, so the
outbox flush releases it at the minute. One copy per day per recurrence is
guaranteed by the outbox's (kind, ref) dedupe with ref ``<rec_id>:<day>``.

Stopping sets ``active`` false and cancels any copy still waiting on the outbox.
Every copy sent is on the outbox like any other email, so replies are watched
in the same way.
"""
from __future__ import annotations

import html as _html
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

KIND = "recurring"


def text_to_html(text: str) -> str:
    """Plain paragraphs (blank-line separated) to simple HTML; single newlines
    inside a paragraph become <br>."""
    paras = [p.strip("\n") for p in text.replace("\r\n", "\n").split("\n\n")]
    out = []
    for p in paras:
        if not p.strip():
            continue
        out.append("<p>" + "<br>".join(_html.escape(line) for line in p.split("\n")) + "</p>")
    return "<div style=\"font-family:Calibri,Arial,sans-serif;font-size:11pt\">" + "\n".join(out) + "</div>"


class RecurringSends:
    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.coll = mongo.db["recurring_sends"]
        self.coll.create_index("rec_id", unique=True, name="ux_recurring_id")
        self.coll.create_index([("active", 1)], name="ix_recurring_active")

    # ----------------------------------------------------------------- add
    def add(self, *, name: str, to: Sequence[Tuple[str, str]], subject: str, text: str,
            hour: int, minute: int, tz: str = "America/New_York", cc: Sequence[Tuple[str, str]] = (),
            created_by: str = "admin", html: Optional[str] = None, start_day: Optional[str] = None) -> Dict[str, Any]:
        rec_id = f"rec-{uuid.uuid4().hex[:10]}"
        doc = {"rec_id": rec_id, "name": name, "active": True,
               "to": [{"name": n, "address": a.lower()} for n, a in to],
               "cc": [{"name": n, "address": a.lower()} for n, a in cc],
               "subject": subject, "text": text, "html": html or text_to_html(text),
               "hour": int(hour), "minute": int(minute), "tz": tz,
               "start_day": start_day,          # first day it may go (local YYYY-MM-DD); None = today
               "created_by": created_by, "created_at": datetime.now(timezone.utc),
               "last_queued_day": None, "sent_days": 0, "stopped_at": None, "stopped_by": None}
        self.coll.insert_one(doc)
        logger.info("recurring: added %s (%s) daily %02d:%02d %s to %s", rec_id, name, hour, minute, tz, [a for _, a in to])
        return {k: v for k, v in doc.items() if k not in ("html",)}

    def list(self, *, active_only: bool = False) -> List[Dict[str, Any]]:
        q = {"active": True} if active_only else {}
        return list(self.coll.find(q, {"_id": 0, "html": 0}).sort("created_at", -1))

    def stop(self, rec_id: str, *, by: str = "admin", outbox=None) -> Dict[str, Any]:
        res = self.coll.find_one_and_update({"rec_id": rec_id}, {"$set": {"active": False, "stopped_at": datetime.now(timezone.utc), "stopped_by": by}},
                                            projection={"_id": 0, "html": 0})
        if not res:
            return {"error": f"no recurrence {rec_id}"}
        cancelled = 0
        if outbox is not None:
            cancelled = outbox.coll.update_many({"kind": KIND, "ref": {"$regex": f"^{rec_id}:"}, "status": {"$in": ["queued", "needs_consent"]}},
                                                {"$set": {"status": "cancelled", "error": f"recurrence stopped by {by}"}}).modified_count
        logger.info("recurring: stopped %s (%d queued copy cancelled)", rec_id, cancelled)
        return {**res, "cancelled_queued": cancelled}

    # ---------------------------------------------------------------- tick
    @staticmethod
    def _zone(name: str):
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)

    def tick(self, outbox, *, now: Optional[datetime] = None, lead: timedelta = timedelta(hours=1)) -> Dict[str, Any]:
        """Queue today's copy of each active recurrence once its send time is
        within ``lead``; the outbox holds it until the exact time."""
        now = now or datetime.now(timezone.utc)
        out = {"queued": 0, "skipped": 0}
        for rec in self.coll.find({"active": True}):
            tz = self._zone(rec.get("tz") or "America/New_York")
            local = now.astimezone(tz)
            day = local.strftime("%Y-%m-%d")
            if rec.get("start_day") and day < rec["start_day"]:
                out["skipped"] += 1
                continue
            if rec.get("last_queued_day") == day:
                out["skipped"] += 1
                continue
            send_local = local.replace(hour=int(rec["hour"]), minute=int(rec["minute"]), second=0, microsecond=0)
            if send_local - local > lead:
                out["skipped"] += 1          # not yet within the hour
                continue
            if local - send_local > timedelta(hours=6):
                # Scheduler was down past the send time by more than six hours:
                # do not send a 6 a.m. email in the afternoon; mark the day as missed.
                self.coll.update_one({"rec_id": rec["rec_id"]}, {"$set": {"last_queued_day": day}, "$push": {"missed_days": day}})
                logger.warning("recurring: %s missed %s (scheduler not running at %02d:%02d)", rec["rec_id"], day, rec["hour"], rec["minute"])
                out["skipped"] += 1
                continue
            send_after = send_local.astimezone(timezone.utc)
            res = outbox.queue(kind=KIND, ref=f"{rec['rec_id']}:{day}",
                               to=[(r["name"], r["address"]) for r in rec["to"]],
                               cc=[(r["name"], r["address"]) for r in rec.get("cc") or []],
                               subject=rec["subject"], html=rec["html"], text=rec["text"],
                               meta={"recurrence": rec["rec_id"], "name": rec["name"], "day": day},
                               send_after=send_after)
            self.coll.update_one({"rec_id": rec["rec_id"]}, {"$set": {"last_queued_day": day, "last_outbox_id": res.get("outbox_id")}, "$inc": {"sent_days": 0 if res.get("deduped") else 1}})
            out["queued"] += 0 if res.get("deduped") else 1
        return out
