"""Replies owed with nothing written — written, or moved to where they belong.

The list of replies must hold only replies that are written and ready to read and
send (admin, 2026-11-21, on seeing "Not drafted" rows: "either we draft the reply
or if not needed then why showing in replies?").

A reply is written at one moment only: when an email comes in and is read as
needing an answer. A reply owed for any other reason had no draft and never got
one — promised on a call, taken from the older record when a file was opened,
added by hand, or its draft set aside or replaced. At each of the two daily runs
every such "reply" to-do is settled one of two ways:

    write   the file gives what the reply has to say, and there is an email from
            that person to answer: the reply is written by the same writer, with
            the same checks, as any other. The to-do is tied to that email, so
            the draft and the to-do leave the list together when it goes out.

    wait    the reply cannot be written yet — it depends on a fact, a document or
            a decision RKB does not have, or on what only the sender knows, or
            there is no email to answer. Then it is not a reply yet: the to-do
            becomes the next step (a thing to do, or a decision), saying what is
            missing. When that arrives, the reply is raised in the usual way.

One small reading decides which (``decide``); it is a judgement about what the
record holds and code cannot make it. A to-do is tried once: what could not be
settled is left as it is, marked, and not paid for again at every run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .context import day, EMAIL_FIELDS, et, property_block, Records
from .llm import cost_of, structured, system_blocks
from .store import BrainStore, name_of, now

MAIL_DAYS = 21            # how far back the reading looks for the email to answer
RETRY_DAYS = 7            # a to-do that could not be settled is looked at again after this long
EFFORT = "high"

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["decision", "why", "answers", "points", "becomes", "title"], "properties": {
    "decision": {"type": "string", "enum": ["write", "wait"]},
    "why": {"type": "string"},
    "answers": {"type": ["string", "null"]},
    "points": {"type": "array", "items": {"type": "string"}},
    "becomes": {"type": "string", "enum": ["action", "decision"]},
    "title": {"type": ["string", "null"]}}}

JOB = """YOUR JOB NOW: A REPLY IS OWED AND NOTHING IS WRITTEN
One open item of this file says RKB owes someone a reply, and no reply is written for it. The
file is below, then the emails received on it in the last weeks (each with a tag), then the
item. Decide one of two things.

* write — the file and the emails give what the reply has to say, and one of the listed emails
  is the one it answers: the latest from that person on this matter. answers = that email's tag,
  exactly as listed. points = what the reply must cover, each a full sentence with its figures
  and dates, taken from the item and the file.
* wait — the reply cannot be written yet. It depends on a fact, a document or a decision RKB does
  not have (say exactly what, and from whom); or what it must say is the sender's own knowledge
  or opinion and is in no record; or no listed email is from that person. Then it is not a reply
  yet. becomes = decision when the missing thing is the owner's own decision, action otherwise.
  title = the item rewritten as that next step (begins with a verb, names the thing and the
  person, under 110 characters, plain words). answers = null, points = [].

Do not choose write to produce a holding reply ("we will come back to you") unless the item
itself asks for one. why — one plain sentence a person can check."""


def _aware(d: Any) -> Optional[datetime]:
    return d.replace(tzinfo=d.tzinfo or timezone.utc) if isinstance(d, datetime) else None


class Owed:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None, writer=None,
                 decide: Optional[Callable[[str, Dict[str, Any], Sequence[dict]], Dict[str, Any]]] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.records = Records(mongo)
        self._writer = writer
        self._decide = decide

    @property
    def writer(self):
        if self._writer is None:
            from .drafts import DraftWriter
            self._writer = DraftWriter(self.mongo, self.store)
        return self._writer

    # ------------------------------------------------------------ what is waiting
    def waiting(self, *, today: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Every open "reply" to-do of a person who sends replies, with no written reply behind it."""
        today = today or now()
        drafted = {d.get("reply_to_sha") for d in self.store.list_drafts(status=("ready",), limit=500) if d.get("reply_to_sha")}
        out = []
        for f in self.store.list_files():
            for i in BrainStore.open_items(f):
                if i.get("kind") != "reply" or i.get("owner") not in bc.SENDER_NAMES:
                    continue
                if i.get("source_sha") and i["source_sha"] in drafted:
                    continue
                tried = _aware((i.get("owed") or {}).get("at"))
                if tried and today - tried < timedelta(days=RETRY_DAYS):
                    continue
                out.append({"pid": f["property_id"], "item": i})
        return out

    def mails(self, pid: str, item: Dict[str, Any]) -> List[dict]:
        """The emails received on the file in the last weeks, newest first — and the one that raised the to-do, however old."""
        rows = self.records.recent_mail(pid, days=MAIL_DAYS, limit=40, direction="received")
        sha = str(item.get("source_sha") or "")
        if sha and not sha.startswith("granola-") and sha not in {r["sha256"] for r in rows}:
            a = self.mongo.artifacts.find_one({"sha256": sha, "source_type": "email"}, EMAIL_FIELDS)
            if a and self.records.direction(sha) != "sent":
                rows.append(a)
        return rows

    # ------------------------------------------------------------ the decision
    def decide(self, pid: str, item: Dict[str, Any], mails: Sequence[dict]) -> Dict[str, Any]:
        if self._decide:
            return self._decide(pid, item, mails)
        today = now()
        listed = []
        for a in mails:
            frm = ((a.get("participants") or {}).get("from") or [""])[0]
            words = " ".join(str(a.get("body_clean") or "").split())[:700]
            raised = "  ← the email that raised this item" if a["sha256"] == item.get("source_sha") else ""
            listed.append(f"[{a['sha256'][:12]}] {et(a.get('date'))} | from {self.records.who(frm)} | {a.get('subject') or '(no subject)'}{raised}\n    {words}")
        due = f", due {day(item.get('due'))}" if item.get("due") else ""
        user = (f"TODAY: {today:%A %d %B %Y}\n\n{property_block(self.mongo, self.store, pid)}\n\n"
                f"=== EMAILS RECEIVED ON THIS FILE SINCE {today - timedelta(days=MAIL_DAYS):%Y-%m-%d} ({len(listed)}) ===\n" + ("\n".join(listed) or "(none)") +
                f"\n\n=== THE ITEM ===\n[{item['id']}] (owner {item.get('owner')}{due}) {item.get('title')}\n{item.get('detail') or ''}")
        job = (prompts.general_note() if pid == bc.GENERAL_ID else "") + "\n" + JOB
        r = structured(system=system_blocks(prompts.CORE, job), user=user, schema=SCHEMA, effort=EFFORT, max_tokens=8_000, label=f"owed {pid} {item['id']}")
        return {**r["data"], "cost_usd": cost_of(r["usage"])}

    # ------------------------------------------------------------ one to-do
    def _mark(self, pid: str, iid: str, fields: Dict[str, Any]) -> None:
        self.store.files.update_one({"property_id": pid, "open_items": {"$elemMatch": {"id": iid, "status": "open"}}},
                                    {"$set": {**{f"open_items.$.{k}": v for k, v in fields.items()}, "updated_at": now()}})

    def settle(self, pid: str, item: Dict[str, Any], *, force: Optional[str] = None) -> Dict[str, Any]:
        """Settle one to-do. ``force`` ("write" or "wait") is a person's own ruling on what to do with it;
        the reading still says which email is answered, or what the next step is."""
        iid, title = item["id"], str(item.get("title") or "")
        mails = self.mails(pid, item)
        d = self.decide(pid, item, mails)
        cost = float(d.get("cost_usd") or 0)
        by_tag = {a["sha256"][:12]: a["sha256"] for a in mails}
        anchor = by_tag.get(str(d.get("answers") or "").strip().strip("[]")[:12])
        want = force or d.get("decision")
        row = {"property_id": pid, "item_id": iid, "title": title, "owner": item.get("owner"), "why": str(d.get("why") or "").strip()}

        if want == "write" and anchor:
            ready = self.store.drafts.find_one({"reply_to_sha": anchor, "status": "ready"}, {"draft_id": 1})
            if ready:        # a reply to that email is already written: this to-do is the same reply
                self._mark(pid, iid, {"source_sha": anchor, "owed": {"at": now(), "outcome": "drafted", "draft_id": ready["draft_id"]}})
                return {**row, "outcome": "drafted", "draft_id": ready["draft_id"], "cost_usd": cost}
            instruction = (f"This reply settles an open item on RKB's list: “{title}”. {item.get('detail') or ''}\n"
                           "The email below is the latest from this person on the matter. Write the reply RKB owes on it, in that conversation.")
            new = self.writer.write(anchor, pids=[pid], points=[str(p) for p in d.get("points") or [] if p], sender=item.get("owner") or bc.DEFAULT_SENDER,
                                    why=title, urgency="today" if item.get("due") else "", instruction=instruction)
            cost += float((new or {}).get("cost_usd") or 0)
            if new and new.get("draft_id"):
                self._mark(pid, iid, {"source_sha": anchor, "owed": {"at": now(), "outcome": "drafted", "draft_id": new["draft_id"]}})
                return {**row, "outcome": "drafted", "draft_id": new["draft_id"], "subject": new.get("subject"), "cost_usd": round(cost, 4)}
            # The writer found that nothing is owed on that email (already answered, or no reply needed), or could not write.
            note = str((new or {}).get("note") or "the writer returned nothing").strip()
            self._mark(pid, iid, {"owed": {"at": now(), "outcome": "left", "note": note[:400]}})
            return {**row, "outcome": "left", "note": note, "cost_usd": round(cost, 4)}

        if want == "wait" or (want == "write" and not anchor):
            why = row["why"] or "There is no email from this person to answer."
            kind = d.get("becomes") if d.get("becomes") in ("action", "decision") else "action"
            fields = {"kind": kind, "owed": {"at": now(), "outcome": "moved", "was": title}}
            new_title = str(d.get("title") or "").strip()[:160]
            if new_title and d.get("decision") == "wait":
                fields["title"] = new_title
            fields["detail"] = (f"No reply is written yet: {why}\n" + str(item.get("detail") or "")).strip()[:600]
            self._mark(pid, iid, fields)
            self.store.add_activity(pid, at=now(), kind="sweep", who="MangoTree", relevance="minor",
                                    title=f"A reply owed became {'a decision' if kind == 'decision' else 'a to-do'}: nothing can be written yet",
                                    summary=f"“{title}” — {why}")
            return {**row, "outcome": "moved", "kind": kind, "new_title": fields.get("title") or title, "cost_usd": round(cost, 4)}

        self._mark(pid, iid, {"owed": {"at": now(), "outcome": "left", "note": row["why"][:400]}})
        return {**row, "outcome": "left", "note": row["why"], "cost_usd": round(cost, 4)}

    # ------------------------------------------------------------ a pass
    def run(self, *, limit: int = 6, force: Optional[Dict[str, str]] = None, today: Optional[datetime] = None) -> Dict[str, Any]:
        """Settle what is waiting, a few at a time (each written reply is a full draft). ``force`` maps a to-do's id to "write" or "wait"."""
        out: Dict[str, Any] = {"drafted": [], "moved": [], "left": [], "failed": [], "cost_usd": 0.0, "waiting": 0}
        rows = self.waiting(today=today)
        out["waiting"] = len(rows)
        for w in rows[:limit]:
            pid, item = w["pid"], w["item"]
            try:
                r = self.settle(pid, item, force=(force or {}).get(item["id"]))
            except Exception as exc:
                logger.exception("owed: %s on %s could not be settled", item.get("id"), pid)
                out["failed"].append({"property_id": pid, "item_id": item.get("id"), "title": item.get("title"), "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            out[r["outcome"]].append(r)
            out["cost_usd"] = round(out["cost_usd"] + float(r.get("cost_usd") or 0), 4)
            logger.info("owed: %s — “%s” (%s)", r["outcome"], str(item.get("title"))[:80], name_of(pid))
        return out
