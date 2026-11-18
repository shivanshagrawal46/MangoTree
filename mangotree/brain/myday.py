"""My day — each person sees a few things to do now, not everything at once.

The files hold every open matter (about two hundred lines across the properties).
Shown together they are a wall nobody works from. This module turns them into one
short list per person:

    what is mine      my to-dos on every file · the replies written in my name and
                      waiting to be sent · the follow-ups worked out from the bills and
                      the site (a bill to approve, bills to pay) · a task Rakesh Sir gave me
    do now            the first five, most pressing first; the rest wait under "later"
                      and move up by themselves as things get done
    the team          Rakesh Sir sees, for each person, what is left and what is late
    give a task       Rakesh Sir writes a task for someone: it lands on their list and
                      an email goes to them from his mailbox
    the morning email 4:30 am US Eastern, to each person: their first five for the day

The order is worked out from plain signals (a task Rakesh Sir gave, a date that has
passed, money waiting, a reply owed) and, once a day, read over by the model, which
puts the list in the order a careful chief of staff would and says in a few words why
each of the first ones matters today (``plan``). The list is never stored: it is
worked out from the files each time, so a to-do that an email or a call closed is gone
from it at once.
"""
from __future__ import annotations

import html as _html
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core import eastern
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from .store import BrainStore, name_of, now, parse_day

#: The people who work from the system. ``follow`` is the name the follow-up list uses for them.
TEAM: Dict[str, Dict[str, Any]] = {
    "rakesh": {"name": "Rakesh Sir", "first": "Rakesh Sir", "email": "rakesh@mtreh.com", "follow": "rakesh"},
    "jp": {"name": "JP Sir", "first": "JP Sir", "email": "jp@mtreh.com", "follow": "jp"},
    "manjunath": {"name": "Manjunath Sir", "first": "Manjunath Sir", "email": "manjunath@mtreh.com", "follow": "manju"},
    "shivansh": {"name": "Shivansh", "first": "Shivansh", "email": "shivansh@mtreh.com", "follow": None},
}
NOW = 5                      # how many are shown as "do now"
MORNING_HOUR, MORNING_MINUTE = 4, 30      # US Eastern (user, 2026-11-18: "a morning email for everyone at 4:30 am est")
KIND_VERB = {"reply": "Reply", "decision": "Decide", "action": "Do", "waiting": "Waiting"}
BILL_KINDS = ("approve_bill", "pay_bills", "paid_not_recorded")
EFFORT = "high"
#: The first line of a test email, so nobody acts on it.
TEST_NOTE = "THIS IS A TEST of a new automatic email from MangoTree. Please do not act on it yet. It shows what the real one will look like."

PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["order", "doubts"], "properties": {
    "order": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "why_now"],
                                        "properties": {"key": {"type": "string"}, "why_now": {"type": "string"}}}},
    "doubts": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "why", "covered_by"],
                                         "properties": {"key": {"type": "string"}, "why": {"type": "string"}, "covered_by": {"type": ["string", "null"]}}}}}}

PLAN = """YOUR JOB NOW: PUT ONE PERSON'S DAY IN ORDER
Below is everything that is waiting for @@NAME@@ today: to-dos from the property files, replies
written in their name and not yet sent, bills and site matters worked out from the records,
and tasks Rakesh Sir gave them. They can do a handful of things well in a day, not fifty. Say
which to do first.

order — the keys of the ten things to do first, the most pressing first. Think as a careful
chief of staff would: money about to go out or already late; a closing, a filing or an
insurance date that cannot move; a person outside RKB who is blocked until this is done; a
task Rakesh Sir gave directly; a reply already written that only needs to be sent. A small
thing that unblocks someone else today comes before a large thing with no date. Do not put
two lines about the same matter both in the first five — take the one that settles it.
why_now — at most 16 words, plain, saying why it is today's: the date, the amount, or who is
waiting. No general words such as "important" or "high priority".
doubts — keys of lines that look already dealt with, said twice, or not really this person's
to do, with the reason in a few words. covered_by — when the line is the same matter as
another line in the list, the key of the one to keep (keep the written reply rather than the
to-do that asks for it; keep the line with the date); otherwise null. A line covered by
another is never in order. A doubt of the other kinds is checked separately: do not leave such
a line out of the order because of the doubt alone.
Use only the keys given."""


def _aware(d: Any) -> Optional[datetime]:
    return d.replace(tzinfo=d.tzinfo or timezone.utc) if isinstance(d, datetime) else None


def et_today(at: Optional[datetime] = None) -> datetime:
    """Midnight US Eastern of today, as a UTC moment."""
    e = eastern.to_et(at or now())
    return e.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


class MyDay:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None, follow=None, outbox=None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self._follow = follow                 # callable -> the follow-up lines, or None
        self._outbox = outbox
        self.plans = mongo.db["day_plans"]

    def outbox(self):
        if self._outbox is None:
            from mangotree.mail.outbox import Outbox
            self._outbox = Outbox(self.mongo)
        return self._outbox

    # ----------------------------------------------------------- what is mine
    def _follow_lines(self) -> List[Dict[str, Any]]:
        try:
            if self._follow is not None:
                return [i for i in (self._follow() or []) if not i.get("mark")]
            from .followlist import FollowUps
            return FollowUps(self.mongo, self.store).build()
        except Exception:
            logger.exception("follow-up lines could not be worked out for My day")
            return []

    def cards(self, person: str, *, files: Optional[Sequence[dict]] = None, drafts: Optional[Sequence[dict]] = None,
              follow: Optional[Sequence[dict]] = None, today: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Everything waiting for one person, as cards, most pressing first."""
        today = today or now()
        files = self.store.list_files() if files is None else files
        drafts = self.store.list_drafts() if drafts is None else drafts
        follow = self._follow_lines() if follow is None else follow
        out: List[Dict[str, Any]] = []
        mine_drafts = [d for d in drafts if d.get("status", "ready") == "ready" and (d.get("sender") or "rakesh") == person]
        by_reply = {d.get("reply_to_sha"): d for d in mine_drafts if d.get("reply_to_sha")}
        used = set()
        for f in files:
            pid = f["property_id"]
            for i in BrainStore.open_items(f):
                if i.get("owner") != person:
                    continue
                due = _aware(i.get("due"))
                d = by_reply.get(i.get("source_sha")) if i.get("kind") == "reply" and i.get("source_sha") else None
                card = {"key": f"todo:{pid}:{i['id']}", "type": "todo", "kind": i.get("kind") or "action", "title": i.get("title"), "detail": i.get("detail") or "",
                        "property_id": pid, "property": name_of(pid), "due": due, "overdue": bool(due and due.date() < eastern.to_et(today).date()),   # late from the day after, not on the day
                        "opened_at": _aware(i.get("opened_at")), "item_id": i["id"], "source_sha": i.get("source_sha"),
                        "assigned_by": i.get("assigned_by"), "assigned_by_name": i.get("assigned_by_name"), "href": f"/property/{pid}"}
                if d:  # the reply is already written: the to-do is "send it"
                    used.add(d["draft_id"])
                    card.update({"type": "reply", "draft_id": d["draft_id"], "href": f"/drafts?open={d['draft_id']}", "stale": bool(d.get("stale")),
                                 "urgency": d.get("urgency"), "to": [t.get("name") or t.get("email") for t in d.get("to") or []][:3], **self._letter(d)})
                out.append(card)
        for d in mine_drafts:
            if d["draft_id"] in used:
                continue
            pids = d.get("property_ids") or [bc.GENERAL_ID]
            to = [t.get("name") or t.get("email") for t in d.get("to") or []][:3]
            out.append({"key": f"reply:{d['draft_id']}", "type": "reply", "kind": "reply", "title": f"Send the reply to {', '.join(x for x in to if x) or 'them'}: {d.get('subject') or ''}"[:160],
                        "detail": d.get("why") or "", "property_id": pids[0], "property": name_of(pids[0]), "due": None, "overdue": False,
                        "opened_at": _aware(d.get("created_at")), "draft_id": d["draft_id"], "href": f"/drafts?open={d['draft_id']}", "stale": bool(d.get("stale")),
                        "urgency": d.get("urgency"), "to": to, **self._letter(d)})
        fkey = (TEAM.get(person) or {}).get("follow")
        for i in follow:
            if not fkey or i.get("owner") != fkey:
                continue
            out.append({"key": f"follow:{i['key']}", "type": "bill" if i.get("kind") in BILL_KINDS else "check", "kind": i.get("kind"), "title": i.get("title"),
                        "detail": i.get("detail") or "", "property_id": i.get("property_id"), "property": i.get("property"), "due": None, "overdue": i.get("level") == "high",
                        "opened_at": _aware(i.get("since")), "follow_key": i["key"], "amount": i.get("amount"), "days": i.get("days"),
                        "href": i.get("href") or (f"/bills" if i.get("kind") in BILL_KINDS else f"/property/{i.get('property_id')}?tab={i.get('tab') or 'sheet'}")})
        for c in out:
            c["score"] = self.score(c, today)
        out.sort(key=lambda c: (-c["score"], c.get("due") or datetime(2999, 1, 1, tzinfo=timezone.utc), c.get("opened_at") or today))
        return out

    @staticmethod
    def _letter(d: Dict[str, Any]) -> Dict[str, Any]:
        """What the list of replies shows for one waiting reply: who wrote, about what, since when,
        and why the written reply needs another look (if it does)."""
        r = d.get("in_reply_to") or {}
        to = (d.get("to") or [{}])[0]
        return {"from_name": r.get("from_name") or to.get("name") or to.get("email") or "", "subject": r.get("subject") or d.get("subject") or "",
                "received": _aware(r.get("date")), "stale_kind": (d.get("stale") or {}).get("kind") or ("partly" if d.get("stale") else None),
                "why": d.get("why") or ""}

    @staticmethod
    def score(c: Dict[str, Any], today: datetime) -> int:
        s = 0
        if c.get("assigned_by"):
            s += 100
        due = c.get("due")
        if due:
            days = (due.date() - eastern.to_et(today).date()).days
            s += 70 + min(20, -days) if days < 0 else (55 if days <= 1 else (30 if days <= 3 else 10))
        elif c.get("overdue"):
            s += 60 + min(15, int(c.get("days") or 0))
        if c["type"] == "bill":
            s += 35
        if c["type"] == "reply":
            s += 40 if c.get("urgency") == "high" else 25
        if c.get("kind") == "decision":
            s += 20
        opened = c.get("opened_at")
        if opened:
            s += min(12, max(0, (today - opened).days))
        return s

    def _ordered(self, person: str, cards: List[Dict[str, Any]], today: datetime) -> List[Dict[str, Any]]:
        """Today's reading of the list (if there is one) decides the order of the lines it named; a line
        that came in later goes in front of them only when it is a task Rakesh Sir gave or is past due."""
        plan = self.plans.find_one({"person": person, "day": f"{eastern.to_et(today):%Y-%m-%d}"}, {"_id": 0}) or {}
        rank = {r["key"]: n for n, r in enumerate(plan.get("order") or [])}
        why = {r["key"]: r.get("why_now") for r in plan.get("order") or []}
        for c in cards:
            c["why_now"] = why.get(c["key"]) or self.why(c, today)
        if not rank:
            return cards
        here = {c["key"] for c in cards}
        seen = set(plan.get("seen") or [])
        # Two lines for one matter: the one the reading kept stays where it is, the other goes to the end
        # (it disappears by itself when the matter is settled).
        twice = {d["key"]: d["covered_by"] for d in plan.get("doubts") or [] if d.get("covered_by") in here and d["covered_by"] != d["key"]}
        for c in cards:
            if c["key"] in twice:
                c["covered_by"] = twice[c["key"]]
        new = [c for c in cards if c["key"] not in seen and c["key"] not in twice and (c.get("assigned_by") or (c.get("due") and c["overdue"]))]
        planned = sorted((c for c in cards if c["key"] in rank and c["key"] not in twice), key=lambda c: rank[c["key"]])
        rest = [c for c in cards if c not in new and c not in planned and c["key"] not in twice]
        return new + planned + rest + [c for c in cards if c["key"] in twice]

    @staticmethod
    def why(c: Dict[str, Any], today: datetime) -> str:
        """Why this is today's, in a few plain words — from the signals alone."""
        if c.get("assigned_by"):
            return f"{c.get('assigned_by_name') or 'Rakesh Sir'} asked you to do this"
        due = c.get("due")
        if due:
            days = (due.date() - eastern.to_et(today).date()).days
            if days < 0:
                return f"Was due {-days} day{'s' if days != -1 else ''} ago"
            return "Due today" if days == 0 else ("Due tomorrow" if days == 1 else f"Due in {days} days")
        if c["type"] == "bill":
            return f"A bill has waited {c['days']} days" if c.get("days") else "Money is waiting on this"
        if c["type"] == "reply":
            return "The reply is written — read it and send" if not c.get("stale") else "The reply is written but things changed — read it again"
        if c["type"] == "check" and c.get("overdue"):
            return "Flagged on the site and bills check"
        if c.get("kind") == "decision":
            return "Waiting for your decision"
        days = (today - c["opened_at"]).days if c.get("opened_at") else 0
        return f"Open for {days} days" if days >= 2 else "New"

    def board(self, person: str, **given) -> Dict[str, Any]:
        """One person's day: the first few to do now, the rest for later, and what they closed today."""
        today = given.pop("today", None) or now()
        cards = self._ordered(person, self.cards(person, today=today, **given), today)
        start = et_today(today)
        done = []
        for f in (given.get("files") if given.get("files") is not None else self.store.list_files()):
            for i in f.get("open_items") or []:
                if i.get("owner") == person and i.get("status") in ("done",) and _aware(i.get("closed_at")) and _aware(i["closed_at"]) >= start:
                    done.append({"title": i.get("title"), "property": name_of(f["property_id"]), "property_id": f["property_id"], "closed_at": i.get("closed_at"),
                                 "closed_by": i.get("closed_by"), "note": i.get("closed_note"), "assigned_by": i.get("assigned_by")})
        t = TEAM.get(person) or {"name": person}
        return {"person": person, "name": t["name"], "now": cards[:NOW], "later": cards[NOW:], "left": len(cards),
                "late": sum(1 for c in cards if c.get("overdue")), "done_today": sorted(done, key=lambda d: d["closed_at"], reverse=True),
                "day": f"{eastern.to_et(today):%Y-%m-%d}"}

    def team(self, viewer: str) -> List[Dict[str, Any]]:
        """Everyone but the person looking: how much is left, how much is late, their first three."""
        files, drafts, follow = self.store.list_files(), self.store.list_drafts(), self._follow_lines()
        out = []
        for p in TEAM:
            if p == viewer:
                continue
            b = self.board(p, files=files, drafts=drafts, follow=follow)
            given = [c for c in b["now"] + b["later"] if c.get("assigned_by")]
            out.append({"person": p, "name": b["name"], "left": b["left"], "late": b["late"], "done_today": len(b["done_today"]), "given_open": len(given),
                        "now": b["now"], "later": b["later"], "done": b["done_today"][:8]})
        return out

    # ------------------------------------------------------------ give a task
    def assign(self, by: Dict[str, Any], *, to: str, title: str, detail: str = "", due: Any = None, pid: Optional[str] = None,
               email: bool = True, test: bool = False) -> Dict[str, Any]:
        """A task from one person to another: on their list at once, and an email to them from Rakesh Sir's mailbox."""
        if to not in TEAM:
            raise ValueError(f"a task can be given to: {', '.join(TEAM)}")
        title = str(title or "").strip()
        if len(title) < 4:
            raise ValueError("say what the task is")
        pid = pid or bc.GENERAL_ID
        if not self.store.get_file(pid):
            self.store.save_file(pid, {"open_items": []})
        item = BrainStore.new_item(title=title, detail=detail, owner=to, kind="action", due=due, by=by.get("user_id") or "rakesh")
        item.update({"assigned_by": by.get("user_id") or "rakesh", "assigned_by_name": by.get("name") or "Rakesh Sir", "assigned_at": now()})
        self.store.files.update_one({"property_id": pid}, {"$push": {"open_items": item}, "$set": {"updated_at": now()}, "$inc": {"version": 1}})
        self.store.add_activity(pid, at=now(), kind="task_given", who=item["assigned_by_name"], title=f"Task for {TEAM[to]['name']}: {title}"[:200],
                                summary=detail, relevance="minor", extra={"item_id": item["id"], "to": to})
        mail = None
        if email:
            mail = self.task_mail(item, pid, test=test)
            q = self.outbox().queue(kind="task_given", ref=item["id"], to=[(TEAM[to]["name"], TEAM[to]["email"])], subject=mail["subject"], html=mail["html"], text=mail["text"],
                                    meta={"item_id": item["id"], "property_id": pid, "to": to, "by": item["assigned_by"], "test": test})
            mail = {"to": TEAM[to]["email"], "subject": mail["subject"], **q}
        return {"item": {**item, "property_id": pid, "property": name_of(pid)}, "email": mail}

    def remind(self, by: Dict[str, Any], *, to: str, key: str, test: bool = False) -> Dict[str, Any]:
        """Rakesh Sir reminds someone of one thing on their list: an email from his mailbox, at most one a day per thing.
        Nothing on the list changes: the owner still marks it done."""
        if to not in TEAM:
            raise ValueError(f"a reminder can go to: {', '.join(TEAM)}")
        card = next((c for c in self.cards(to) if c["key"] == key), None)
        if not card:
            raise ValueError("that is no longer on their list")
        from mangotree.retrieve.config import PUBLIC_URL
        from .billmail import _p, _table, _text_rows, _wrap
        who, person = by.get("name") or "Rakesh Sir", TEAM[to]
        rows = [("What", card["title"]), ("Property", card.get("property") or "General")]
        if card.get("due"):
            rows.append(("Due", eastern.label(eastern.to_et(card["due"]), time=False) + (" (past due)" if card.get("overdue") else "")))
        intro = f"{person['first']}, {who} is asking where this stands."
        foot = "It is on your list in MangoTree. Mark it done there when it is finished, or tell Rakesh Sir what is holding it. All times are US Eastern."
        head = [TEST_NOTE] if test else []
        link = f"{PUBLIC_URL}/"
        html = _wrap([_p(x) for x in head] + [_p(intro), _table(rows), f'<p style="margin:14px 0"><a href="{_html.escape(link)}" style="color:#0b6b4f">Open my day in MangoTree</a></p>', _p(foot, small=True)])
        text = "\n".join(head + [intro, "", _text_rows(rows), "", f"Open my day: {link}", "", foot])
        subject = f"{'TEST — ' if test else ''}Reminder from {who}: {card['title']}"[:180]
        q = self.outbox().queue(kind="task_reminder", ref=f"{key}:{eastern.to_et(now()):%Y-%m-%d}{':test' if test else ''}", to=[(person["name"], person["email"])],
                                subject=subject, html=html, text=text, meta={"key": key, "to": to, "by": by.get("user_id") or "rakesh", "test": test})
        return {"to": person["email"], "name": person["name"], "subject": subject, **q}

    @staticmethod
    def task_mail(item: Dict[str, Any], pid: str, *, test: bool = False) -> Dict[str, Any]:
        from mangotree.retrieve.config import PUBLIC_URL
        from .billmail import _p, _table, _text_rows, _wrap
        who, to = item.get("assigned_by_name") or "Rakesh Sir", TEAM[item["owner"]]
        rows = [("Task", item["title"])]
        if item.get("detail"):
            rows.append(("Details", item["detail"]))
        rows.append(("Property", name_of(pid)))
        rows.append(("Due", eastern.label(eastern.to_et(item["due"]), time=False) if item.get("due") else "no date given"))
        rows.append(("Given", eastern.label(eastern.to_et(item.get("assigned_at") or now()))))
        intro = f"{to['first']}, {who} has asked you to do this."
        foot = "It is on your list in MangoTree. Tick it there when it is done and Rakesh Sir will see it. All times are US Eastern."
        link = f"{PUBLIC_URL}/"
        head = [TEST_NOTE] if test else []
        html = _wrap([_p(x) for x in head] + [_p(intro), _table(rows), f'<p style="margin:14px 0"><a href="{_html.escape(link)}" style="color:#0b6b4f">Open my day in MangoTree</a></p>', _p(foot, small=True)])
        text = "\n".join(head + [intro, "", _text_rows(rows), "", f"Open my day: {link}", "", foot])
        return {"subject": f"{'TEST — ' if test else ''}Task from {who}: {item['title']}"[:180], "html": html, "text": text}

    # ------------------------------------------------------- the day's reading
    def plan(self, person: str, *, today: Optional[datetime] = None) -> Dict[str, Any]:
        """Once a day the model reads one person's whole list and puts it in order."""
        from .llm import cost_of, structured, system_blocks
        from . import prompts
        today = today or now()
        cards = self.cards(person, today=today)
        day_key = f"{eastern.to_et(today):%Y-%m-%d}"
        if len(cards) <= 3:
            self.plans.update_one({"person": person, "day": day_key}, {"$set": {"order": [], "doubts": [], "seen": [c["key"] for c in cards], "at": now(), "cards": len(cards), "cost_usd": 0.0}}, upsert=True)
            return {"person": person, "cards": len(cards), "order": [], "cost_usd": 0.0}
        lines = []
        for c in cards:
            bits = [c["type"], c.get("kind") or "", c["property"]]
            if c.get("due"):
                bits.append(f"due {c['due']:%Y-%m-%d}")
            if c.get("opened_at"):
                bits.append(f"open since {c['opened_at']:%Y-%m-%d}")
            if c.get("amount"):
                bits.append(f"${c['amount']:,.2f}")
            if c.get("assigned_by"):
                bits.append("given by Rakesh Sir")
            if c.get("stale"):
                bits.append("the written reply may be out of date")
            lines.append(f"[{c['key']}] ({', '.join(b for b in bits if b)}) {c['title']}" + (f" — {c['detail'][:400]}" if c.get("detail") else ""))
        name = TEAM.get(person, {}).get("name", person)
        r = structured(system=system_blocks(prompts.CORE, PLAN.replace("@@NAME@@", name)),
                       user=f"TODAY: {eastern.label(eastern.to_et(today))}\nPERSON: {name}\n\nWAITING FOR THEM ({len(cards)})\n" + "\n".join(lines),
                       schema=PLAN_SCHEMA, effort=EFFORT, max_tokens=12_000, label=f"day plan {person}")
        keys = {c["key"] for c in cards}
        order = [{"key": o["key"], "why_now": str(o.get("why_now") or "")[:140]} for o in r["data"].get("order") or [] if o.get("key") in keys][:10]
        doubts = [{"key": o["key"], "why": str(o.get("why") or "")[:200], "covered_by": o.get("covered_by") if o.get("covered_by") in keys else None}
                  for o in r["data"].get("doubts") or [] if o.get("key") in keys]
        covered = {d["key"] for d in doubts if d["covered_by"]}
        order = [o for o in order if o["key"] not in covered]
        cost = cost_of(r["usage"])
        self.plans.update_one({"person": person, "day": day_key}, {"$set": {"order": order, "doubts": doubts, "seen": sorted(keys), "at": now(), "cards": len(cards),
                                                                            "usage": r["usage"], "cost_usd": cost}}, upsert=True)
        return {"person": person, "cards": len(cards), "order": order, "doubts": doubts, "cost_usd": cost}

    # ------------------------------------------------------ the morning email
    def morning_mail(self, person: str, *, today: Optional[datetime] = None, test: bool = False) -> Dict[str, Any]:
        from mangotree.retrieve.config import PUBLIC_URL
        from .billmail import _h, _p, _wrap
        today = today or now()
        b = self.board(person, today=today)
        t = TEAM[person]
        et = eastern.to_et(today)
        n = len(b["now"])
        intro = (f"{t['first']}, good morning. " + (f"{n} thing{'s' if n != 1 else ''} to do first today" + (f", and {len(b['later'])} more after that." if b["later"] else ".")
                                                     if n else "Nothing is waiting for you this morning."))
        parts, text = [_p(intro)], [intro, ""]
        if test:
            parts.insert(0, _p(TEST_NOTE))
            text[:0] = [TEST_NOTE, ""]
        for k, c in enumerate(b["now"], 1):
            late = " — LATE" if c.get("overdue") else ""
            head = f"{k}. {c['title']}"
            sub = f"{c['property']} · {c.get('why_now') or ''}{late}"
            parts.append(f'<p style="margin:14px 0 2px;font-weight:600">{_html.escape(head)}</p><p style="margin:0;color:#555;font-size:13px">{_html.escape(sub)}</p>')
            text += [head, f"   {sub}", ""]
        if person == "rakesh":
            rows = self.team("rakesh")
            parts.append(_h("The team"))
            lines = [f"{r['name']}: {r['left']} left" + (f", {r['late']} late" if r["late"] else "") + (f", {r['done_today']} done today" if r["done_today"] else "") for r in rows]
            parts.append("".join(_p(x) for x in lines))
            text += ["THE TEAM"] + lines + [""]
        link = f"{PUBLIC_URL}/"
        foot = "Sent every morning at 4:30 am US Eastern. The list on the site is always the current one: what an email, a call or a tick has settled is already gone from it."
        parts += [f'<p style="margin:16px 0"><a href="{_html.escape(link)}" style="color:#0b6b4f">Open my day in MangoTree</a></p>', _p(foot, small=True)]
        text += [f"Open my day: {link}", "", foot]
        subject = f"{'TEST — ' if test else ''}Your day, {et.day} {et:%b}: " + (f"{n} thing{'s' if n != 1 else ''} first" if n else "nothing waiting")
        return {"to": (t["name"], t["email"]), "subject": subject, "html": _wrap(parts), "text": "\n".join(text), "now": n, "later": len(b["later"])}

    def send_morning(self, *, people: Optional[Sequence[str]] = None, today: Optional[datetime] = None, test: bool = False, plan: bool = True) -> Dict[str, Any]:
        """Read each person's list over, then queue their morning email. One email per person per day."""
        today = today or now()
        day_key = f"{eastern.to_et(today):%Y-%m-%d}"
        out: Dict[str, Any] = {"day": day_key, "sent": [], "failed": [], "cost_usd": 0.0}
        for p in (people or list(TEAM)):
            try:
                if plan:
                    out["cost_usd"] = round(out["cost_usd"] + (self.plan(p, today=today).get("cost_usd") or 0), 4)
                m = self.morning_mail(p, today=today, test=test)
                q = self.outbox().queue(kind="my_day", ref=f"{p}:{day_key}{':test' if test else ''}", to=[m["to"]], subject=m["subject"], html=m["html"], text=m["text"],
                                        meta={"person": p, "day": day_key, "test": test, "now": m["now"], "later": m["later"]})
                out["sent"].append({"person": p, "to": m["to"][1], "subject": m["subject"], **q})
            except Exception as exc:
                logger.exception("morning email for %s failed", p)
                out["failed"].append({"person": p, "error": f"{type(exc).__name__}: {exc}"[:300]})
        return out


def cannot_mark(owner: Any, user: Dict[str, Any]) -> Optional[str]:
    """Why this person may NOT mark a to-do done, not needed, or open again — or None when they may.
    A to-do on someone's own list is theirs alone to mark (admin directive 2026-11-18: not even Rakesh Sir
    marks another person's work done). What waits on someone outside RKB (Wes, Kelly, others) has no owner
    who signs in, so anyone at RKB may close it."""
    if owner in TEAM and owner != user.get("user_id"):
        return f"Only {TEAM[owner]['name']} can mark this done: it is on their list."
    return None


def cannot_move(owner: Any, new_owner: Any, user: Dict[str, Any]) -> Optional[str]:
    """Why this person may NOT give a to-do to someone else — or None when they may (Rakesh Sir, or its owner)."""
    if new_owner is None or new_owner == owner or user.get("role") == "ceo" or owner == user.get("user_id"):
        return None
    return "Only Rakesh Sir, or the person it belongs to, can give a to-do to someone else."


def due_morning(at: Optional[datetime] = None) -> bool:
    """True from 4:30 am Eastern until 9 am: the scheduler claims the day once inside that window."""
    e = eastern.to_et(at or now())
    return (e.hour, e.minute) >= (MORNING_HOUR, MORNING_MINUTE) and e.hour < 9
