"""Keeping the property files current.

    bootstrap(pid)        open a file from what is already on record (once)
    run_pending()         read every record that arrived since, in the order it
                          happened, and apply what it changes

A record is read once per property it concerns (``brain_events`` is the
ledger). An email is read together with its attachments and the conversation
before it; a call transcript is cut to the part about the property. A message
RKB sent closes the drafts and open items it deals with — nobody ticks a box.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from mangotree.core import eastern
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import eventcheck
from . import prompts
from .agent import run_loop
from .context import DOC_FIELDS, EMAIL_FIELDS, Records, clip, day, money_lines, property_block, says_nothing, standing_instructions
from .drafts import DraftWriter
from .eventcheck import SUBMIT_CHECK
from .llm import BrainError, BrainRefusal, add_usage, cost_of, structured, system_blocks
from .store import ITEM_KINDS, OWNERS, BrainStore, active_property_ids, all_file_ids, name_of, now
from .tools import Toolkit

Emit = Callable[[str, Dict[str, Any]], None]

_STR = {"type": "string"}
_NSTR = {"type": ["string", "null"]}


def _o(props: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


_ITEM = _o({"title": _STR, "detail": _STR, "owner": {"type": "string", "enum": list(OWNERS)},
            "kind": {"type": "string", "enum": list(ITEM_KINDS)}, "due": _NSTR})
_FACT = _o({"key": _STR, "value": _STR})
_REPLY = _o({"needed": {"type": "boolean"}, "sender": {"type": "string", "enum": ["rakesh", "jp", "manjunath"]},
             "urgency": {"type": "string", "enum": ["today", "this_week", "no_rush"]}, "why": _STR,
             "points": {"type": "array", "items": _STR}})

EVENT_SCHEMA = _o({
    "relevance": {"type": "string", "enum": ["material", "minor", "noise"]},
    "title": _STR, "summary": _STR,
    "headline": _NSTR, "status": _NSTR,
    "facts_set": {"type": "array", "items": _FACT},
    "facts_remove": {"type": "array", "items": _STR},
    "items_add": {"type": "array", "items": _ITEM},
    "items_close": {"type": "array", "items": _o({"id": _STR, "note": _STR})},
    "items_update": {"type": "array", "items": _o({"id": _STR, "title": _NSTR, "detail": _NSTR, "due": _NSTR})},
    "watch": {"type": ["array", "null"], "items": _STR},
    "reply": _REPLY,
})

BOOTSTRAP_SCHEMA = _o({
    "headline": _STR, "status": _STR,
    "facts": {"type": "array", "items": _FACT},
    "open_items": {"type": "array", "items": _ITEM},
    "watch": {"type": "array", "items": _STR},
    "activity": {"type": "array", "items": _o({"sha": _STR, "title": _STR, "summary": _STR})},
    "needs_reply": {"type": "array", "items": _o({"sha": _STR, "sender": {"type": "string", "enum": ["rakesh", "jp", "manjunath"]},
                                                  "why": _STR, "points": {"type": "array", "items": _STR}})},
})


#: How the lean reading hands over its result: the same object, as a tool call that ends the loop.
SUBMIT_UPDATE = {
    "name": "submit_update",
    "description": "Hand over what this record changes on the file. Call exactly once, at the end.",
    "strict": True,
    "input_schema": EVENT_SCHEMA,
}

#: The look-ups a reading may use. Nothing that writes, and not the tool that
#: loads another property's whole record.
LOOKUP_TOOLS = ("open_document", "search_records", "recent_mail", "email_thread", "list_documents", "find_exact")


class CappedKit:
    """The chat's look-up tools, limited to a few uses for one record.

    The limit is on look-ups actually made, not on rounds: once it is reached
    every further call is answered with a plain refusal, so one email can never
    run up the cost of a long investigation."""

    def __init__(self, kit, limit: int = bc.EVENT_LOOKUPS):
        self.kit = kit
        self.limit = int(limit)
        self.used = 0
        self.labels: List[str] = []

    def specs(self) -> List[Dict[str, Any]]:
        return [t for t in self.kit.specs() if t["name"] in LOOKUP_TOOLS]

    def run(self, name: str, args: Dict[str, Any]):
        if name not in LOOKUP_TOOLS:
            return f"Unknown tool {name}.", True, name
        if self.used >= self.limit:
            return (f"The {self.limit} look-ups for this record are used. Call submit_update now from what you have, and say in the "
                    "summary what you could not check."), True, "Look-up refused: limit reached"
        self.used += 1
        content, is_error, label = self.kit.run(name, args)
        self.labels.append(label)
        left = self.limit - self.used
        return content + f"\n\n({left} look-up{'s' if left != 1 else ''} left for this record.)", is_error, label


class _NoLookups:
    """The second reader's hands: none. It checks what is in front of it."""

    def specs(self) -> List[Dict[str, Any]]:
        return []

    def run(self, name: str, args: Dict[str, Any]):
        return "The second reading looks nothing up. Call submit_check now with what you found.", True, "Look-up refused: second reading"


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (t or "").lower())).strip()


def _similar(a: str, b: str) -> bool:
    wa, wb = set(_norm(a).split()), set(_norm(b).split())
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


SCAN = 3000                      # how many of the newest records are looked through for unread ones


def by_file(rows: List[dict], targets) -> List[dict]:
    """One file's records back to back, each file's in the order they came (2026-11-21).

    What a reading is handed for a file — its documents, its mail — is kept by the API for
    five minutes after each reading, and the next record on the same file then reads it at a
    twentieth of the price. Read in date order across files, every switch of file pays for
    that part in full again (measured on one Durham email: $0.58 of its $0.92)."""
    first: Dict[str, int] = {}
    key = lambda r: (list(targets(r)) or [bc.GENERAL_ID])[0]
    for n, r in enumerate(rows):
        first.setdefault(key(r), n)
    return sorted(rows, key=lambda r: first[key(r)])       # stable: the order within a file is kept


class Updater:
    _run_lock = threading.Lock()

    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.records = Records(mongo)
        self.drafts = DraftWriter(mongo, self.store)

    # ------------------------------------------------------------ helpers
    def targets(self, art: dict) -> List[str]:
        active = set(active_property_ids())
        pids = [p for p in (art.get("property_ids") or []) if p in active]
        return pids or [bc.GENERAL_ID]

    @staticmethod
    def is_email(art: dict) -> bool:
        return art.get("source_type") == "email"

    def kind_of(self, art: dict, direction: Optional[str]) -> str:
        if self.is_email(art):
            return "email_out" if direction == "sent" else "email_in"
        if art.get("doc_class") == "meeting_transcript" or art.get("meeting"):
            return "call"
        return "document"

    def who_of(self, art: dict, direction: Optional[str]) -> str:
        if self.is_email(art):
            frm = ((art.get("participants") or {}).get("from") or [""])[0]
            if direction == "sent":
                to = ((art.get("participants") or {}).get("to") or [""])[:2]
                return f"{self.records.name(frm)} to {', '.join(self.records.name(t) for t in to)}"
            return self.records.name(frm)
        m = art.get("meeting") or {}
        if m.get("participants"):
            return ", ".join(str(p) for p in m["participants"][:5])
        up = (art.get("uploads") or [{}])[0]
        return str(up.get("by") or "")

    def _event_at(self, art: dict) -> datetime:
        return art.get("date") or art.get("created_at") or now()

    # --------------------------------------------------------- apply patch
    def _apply(self, pid: str, data: Dict[str, Any], *, art: dict, direction: Optional[str]) -> Dict[str, Any]:
        sha = art["sha256"]
        file = self.store.get_file(pid) or {}
        sets: Dict[str, Any] = {"last_event_at": self._event_at(art), "last_event_sha": sha}
        changed = []
        if data.get("headline"):
            sets["headline"] = str(data["headline"]).strip()[:400]
            changed.append("headline")
        if data.get("status"):
            sets["status"] = str(data["status"]).strip()[:4000]
            changed.append("status")
        if data.get("watch") is not None:
            sets["watch"] = [str(w).strip()[:300] for w in data["watch"] if str(w).strip()][:12]
        facts = {str(f.get("key")).strip().lower(): f for f in (file.get("facts") or []) if f.get("key")}
        for f in data.get("facts_set") or []:
            k = str(f.get("key") or "").strip()
            if k and str(f.get("value") or "").strip():
                facts[k.lower()] = {"key": k[:80], "value": str(f["value"]).strip()[:400], "source_sha": sha, "set_at": now()}
        for k in data.get("facts_remove") or []:
            facts.pop(str(k).strip().lower(), None)
        if data.get("facts_set") or data.get("facts_remove"):
            sets["facts"] = list(facts.values())[:60]
        self.store.files.update_one({"property_id": pid}, {"$set": {**sets, "updated_at": now()}, "$inc": {"version": 1}})

        closed = 0
        for c in data.get("items_close") or []:
            if self.store.close_item(pid, str(c.get("id") or ""), note=c.get("note") or "", by="brain", sha=sha):
                closed += 1
        for u in data.get("items_update") or []:
            fields = {k: u[k] for k in ("title", "detail") if u.get(k)}
            if u.get("due"):
                fields["due"] = u["due"]
            if fields:
                self.store.update_item(pid, str(u.get("id") or ""), fields)
        open_titles = [i["title"] for i in BrainStore.open_items(self.store.get_file(pid))]
        added = 0
        for it in data.get("items_add") or []:
            title = str(it.get("title") or "").strip()
            if not title or any(_similar(title, t) for t in open_titles):
                continue
            self.store.add_item(pid, title=title, detail=it.get("detail") or "", owner=it.get("owner") or "rakesh",
                                kind=it.get("kind") or "action", due=it.get("due"), source_sha=sha, opened_at=self._event_at(art))
            open_titles.append(title)
            added += 1
        self.store.add_activity(pid, at=self._event_at(art), kind=self.kind_of(art, direction), who=self.who_of(art, direction),
                                title=data.get("title") or (art.get("subject") or art.get("filename") or "record"),
                                summary=data.get("summary") or "", sha=sha, relevance=data.get("relevance") or "minor",
                                extra={"changed": changed, "items_added": added, "items_closed": closed})
        if "status" in changed or "headline" in changed:
            # The deal has moved: read again where it stands on the road from closing to repayment.
            try:
                from .snapshot import refresh_stage
                refresh_stage(self.mongo, self.store, pid)
            except Exception:
                logger.exception("stage refresh failed for %s", pid)
        return {"changed": changed, "items_added": added, "items_closed": closed}

    # ---------------------------------------------------------- one record
    def lean_system(self, pid: str, art: dict, *, as_of: Optional[datetime] = None, page: Optional[str] = None) -> Dict[str, Any]:
        """What a new record is read against, most stable part first, so that a
        second record on the same file pays only for what changed in between:

            1. who we are, the list of every document, the site diary   (changes when a file or a log arrives)
            2. the conversations with nothing new today, in full        (changes once a day; empty on a busy file)
            3. the live conversations, the property's page, the job     (changes with every record)

        Parts 1 and 2 are kept for an hour on a busy file and five minutes on a
        quiet one: an hour costs twice the input price to store, and is only
        worth it when another record is likely to come within it."""
        mail = self.records.mail_view(pid, new=art if self.is_email(art) else None, as_of=as_of)
        docs = self.records.documents_block(pid, as_of=as_of)
        site = self.records.site_text(pid)
        page = page if page is not None else property_block(self.mongo, self.store, pid)
        job = prompts.EVENT_LEAN + (prompts.general_note() if pid == bc.GENERAL_ID else "")
        keep: Dict[str, Any] = {"type": "ephemeral", "ttl": "1h"} if (bc.EVENT_CACHE_HOUR and mail["last_day"] >= bc.BUSY_FILE_EMAILS) else {"type": "ephemeral"}
        # Always the same sections in the same order: who we are, the documents, the site,
        # the mail, the property's page, the job. Then the new record, in the user turn.
        blocks = [{"type": "text", "text": prompts.CORE_LEAN + "\n" + docs + ("\n" + site if site else ""), "cache_control": dict(keep)},
                  {"type": "text", "text": mail["stable"] or "(The mail follows.)", "cache_control": dict(keep)},
                  # Stored for five minutes, so the second reading, a minute later, reads it.
                  {"type": "text", "text": mail["live"] + "\n\n" + page + "\n\n" + job, "cache_control": {"type": "ephemeral"}}]
        return {"system": blocks, "busy": "ttl" in keep,
                "chars": {"documents_and_site": len(docs) + len(site), "mail_settled": len(mail["stable"]), "mail_live": len(mail["live"]), "page": len(page),
                          "emails": mail["emails"], "conversations": mail["conversations"], "in_full": mail["full"], "listed": mail["listed"]}}

    def _read_lean(self, pid: str, art: dict, direction: Optional[str], *, as_of: Optional[datetime] = None,
                   page: Optional[str] = None) -> Dict[str, Any]:
        """Read one record with the emails in full, the documents as a list, and up
        to ``EVENT_LOOKUPS`` look-ups of the model's own choosing."""
        ctx = self.lean_system(pid, art, as_of=as_of, page=page)
        if self.is_email(art):
            record = self.records.render_record(art, direction=direction)
        else:
            record = self.records.render_document(art, pid=pid)
        today = as_of or datetime.now(timezone.utc)
        user = f"TODAY: {eastern.to_et(today):%A %d %B %Y}\n\nTHE NEW RECORD for {name_of(pid)}:\n\n{record}"
        label = f"event {pid} {art['sha256'][:10]}"
        usage: Dict[str, int] = {}
        kit = CappedKit(Toolkit(self.mongo, self.store, pid=pid, user={"user_id": "brain", "name": "brain"}, allow_actions=False))
        # One list of tools for the writer and for the second reader, in one order: the tools are
        # the first thing in what the API stores, and a different list is a different prompt.
        tool_list = kit.specs() + [SUBMIT_UPDATE, SUBMIT_CHECK]
        loop = dict(system=ctx["system"], effort=bc.EVENT_EFFORT, tool_list=tool_list)
        messages: List[dict] = []
        res = None
        for max_tokens in (20_000, 40_000):
            # The record is a block of its own: the second reader sends the same block, so it is read from store, not paid for again.
            messages = [{"role": "user", "content": [{"type": "text", "text": user}]}]
            res = run_loop(messages=messages, toolkit=kit, max_rounds=bc.EVENT_LOOKUPS + 1, max_tokens=max_tokens, terminal_tool=SUBMIT_UPDATE, **loop)
            add_usage(usage, res.usage)
            if res.refused:
                raise BrainRefusal(f"{label}: the model declined this request (category: {res.refusal_category})")
            if res.terminal or not res.truncated:
                break
            logger.warning("%s: cut off at max_tokens=%s; reading once more with room", label, max_tokens)
        if not res.terminal:
            raise BrainError(f"{label}: the reading ended without a result (stop: {res.stop_reason or 'none'})")
        data, rounds, check = res.terminal, res.rounds, {"ran": False}
        found: List[str] = []
        if bc.EVENT_CHECK:
            try:
                second = run_loop(messages=[{"role": "user", "content": [{"type": "text", "text": user},
                                                                         {"type": "text", "text": eventcheck.BRIEF + eventcheck.render_entry(data)}]}],
                                  toolkit=_NoLookups(), max_rounds=1, max_tokens=12_000, terminal_tool=SUBMIT_CHECK, **loop)
                add_usage(usage, second.usage)
                if second.terminal:
                    found = eventcheck.findings(second.terminal)
                    corrected = False
                    if found:
                        messages.append({"role": "user", "content": eventcheck.as_instruction(found)})
                        again = run_loop(messages=messages, toolkit=kit, max_rounds=bc.EVENT_LOOKUPS + 1, max_tokens=40_000, terminal_tool=SUBMIT_UPDATE, **loop)
                        add_usage(usage, again.usage)
                        rounds += again.rounds
                        if again.terminal:
                            data, corrected = again.terminal, True
                    check = eventcheck.summary(second.terminal, corrected=corrected)
                else:
                    logger.warning("%s: the second reading gave no result (stop: %s); the entry is filed as written", label, second.stop_reason)
            except Exception:
                # The entry is still worth filing; it is marked as not checked.
                logger.exception("%s: the second reading failed; the entry is filed as written", label)
        return {"data": data, "usage": usage, "lookups": list(kit.labels), "rounds": rounds, "context": ctx["chars"], "user_chars": len(user),
                "busy": ctx["busy"], "check": check, "found": found, "first": res.terminal}

    def _read(self, pid: str, art: dict, direction: Optional[str]) -> Dict[str, Any]:
        if bc.EVENT_MODE == "lean":
            return self._read_lean(pid, art, direction)
        # The earlier way. Shared order — who we are, the record, the file, then the job — so the
        # record is cached once and reused by the update, the draft and the check.
        system = system_blocks(prompts.CORE, self.records.record_block(pid), property_block(self.mongo, self.store, pid),
                               prompts.EVENT + (prompts.general_note() if pid == bc.GENERAL_ID else ""))
        record = self.records.render_thread(art, direction=direction) if self.is_email(art) else self.records.render_document(art, pid=pid)
        user = (f"TODAY: {eastern.to_et(datetime.now(timezone.utc)):%A %d %B %Y}\n\n"
                f"THE NEW RECORD for {name_of(pid)}:\n\n{record}")
        return structured(system=system, user=user, schema=EVENT_SCHEMA, effort=bc.EFFORT_EVENT, max_tokens=20_000,
                          label=f"event {pid} {art['sha256'][:10]}")

    def process(self, art: dict, *, write_drafts: bool = True, emit: Optional[Emit] = None) -> Dict[str, Any]:
        """Read one record for every property it concerns; then, if a reply is owed, write it."""
        sha = art["sha256"]
        direction = self.records.direction(sha) if self.is_email(art) else None
        out: Dict[str, Any] = {"sha": sha, "properties": {}, "direction": direction}
        wants: List[Dict[str, Any]] = []
        for pid in self.targets(art):
            file = self.store.get_file(pid)
            if not file or not file.get("built_at"):
                continue                                            # not opened yet; bootstrap will read it
            if self.store.processed(sha, pid):
                continue
            created = art.get("created_at")
            if created and created <= file["built_at"]:
                self.store.mark_event(sha, pid, status="in_bootstrap")
                continue
            t0 = time.time()
            try:
                r = self._read(pid, art, direction)
                applied = self._apply(pid, r["data"], art=art, direction=direction)
                reply = r["data"].get("reply") or {}
                # An email with no words of its own — a sheet or a PDF with only a name and a
                # signature under it — asks nothing, so nothing is drafted for it, whatever
                # the reading made of the attachment. Every other case is the model's call.
                wordless = bool(reply.get("needed")) and self.is_email(art) and says_nothing(
                    art, [self.records.name(a) for a in ((art.get("participants") or {}).get("from") or [])])
                self.store.mark_event(sha, pid, status="done", detail={
                    "usage": r["usage"], "cost_usd": cost_of(r["usage"]), "elapsed_s": round(time.time() - t0, 1),
                    "relevance": r["data"].get("relevance"), "mode": "lean" if "lookups" in r else "full",
                    "lookups": r.get("lookups") or [], "check": r.get("check"), "reply_wanted": bool(reply.get("needed")),
                    **({"no_draft": "the email has no words of its own"} if wordless else {})})
                out["properties"][pid] = {**applied, "relevance": r["data"].get("relevance"), "title": r["data"].get("title")}
                if reply.get("needed") and not wordless:
                    wants.append({"pid": pid, **reply})
                if emit:
                    emit("status", {"text": f"{name_of(pid)}: {r['data'].get('title')}", "property_id": pid})
            except BrainRefusal as exc:
                logger.warning("brain: %s", exc)
                self.store.mark_event(sha, pid, status="declined", detail={"error": str(exc)[:300]})
                self.store.add_activity(pid, at=self._event_at(art), kind=self.kind_of(art, direction), who=self.who_of(art, direction),
                                        title=art.get("subject") or art.get("filename") or "record",
                                        summary="This record could not be read automatically. Open it directly.", sha=sha, relevance="minor")
            except Exception as exc:
                logger.exception("brain: reading %s for %s failed", sha[:12], pid)
                prev = self.store.events.find_one({"sha": sha, "property_id": pid}, {"attempts": 1}) or {}
                self.store.mark_event(sha, pid, status="error", detail={"error": f"{type(exc).__name__}: {exc}"[:300],
                                                                        "attempts": int(prev.get("attempts") or 0) + 1})
                out.setdefault("errors", []).append(pid)
        # An email from anyone at RKB — Rakesh Sir's sent mail, or a reply from
        # Manjunath Sir or JP Sir that arrived because he was copied — may be the
        # answer to a waiting draft. Those drafts are checked now and closed if so.
        from_us = self.is_email(art) and (direction == "sent" or self.drafts.answered.from_rkb(art))
        if from_us:
            out["drafts_closed"] = self.drafts.on_rkb_email(art)
        elif self.is_email(art) and wants and write_drafts:
            out["draft"] = self._draft_for(art, wants)
        return out

    def _draft_for(self, art: dict, wants: Sequence[Dict[str, Any]]) -> Optional[str]:
        # One draft per email, ever. (Whether RKB has already answered it — in this
        # conversation or any other, by anyone at RKB — is checked by the writer
        # itself before it writes, so every path to a draft goes through it.)
        if self.store.drafts.count_documents({"reply_to_sha": art["sha256"], "status": {"$in": ["ready", "sent"]}}, limit=1):
            return None
        order = {"today": 0, "this_week": 1, "no_rush": 2}
        first = sorted(wants, key=lambda w: order.get(w.get("urgency"), 1))[0]
        points: List[str] = []
        for w in wants:
            for p in w.get("points") or []:
                if p and p not in points:
                    points.append(f"[{name_of(w['pid'])}] {p}" if len(wants) > 1 else p)
        try:
            d = self.drafts.write(art["sha256"], pids=[w["pid"] for w in wants], points=points, sender=first.get("sender") or bc.DEFAULT_SENDER,
                                  why="; ".join(w.get("why") or "" for w in wants if w.get("why"))[:600], urgency=first.get("urgency") or "")
            if d and d.get("already_answered"):
                logger.info("brain: no draft for %s — %s", art["sha256"][:12], str(d.get("note"))[:160])
            return (d or {}).get("draft_id")
        except Exception:
            logger.exception("brain: drafting the reply to %s failed", art["sha256"][:12])
            return None

    # ------------------------------------------------------------ pending
    def pending(self, *, limit: int = 300) -> List[dict]:
        """The records that have arrived and are not yet read into the files, oldest first.
        The NEWEST records are the ones looked at: an unread record is by its nature a new one.
        (Until 2026-11-18 the oldest were taken, so once more records than the cap had come in
        since the files were built, nothing new was ever read.)"""
        built = {f["property_id"]: f.get("built_at") for f in self.store.files.find({"built_at": {"$exists": True}}, {"property_id": 1, "built_at": 1})}
        if not built:
            return []
        earliest = min(built.values())
        q = {"source_type": {"$in": ["email", "upload", "disk_file"]}, "is_inline_image": {"$ne": True},
             "created_at": {"$gt": earliest}, "placement": {"$exists": True}}
        rows = list(self.mongo.artifacts.find(q, {"sha256": 1, "source_type": 1, "property_ids": 1, "created_at": 1, "date": 1,
                                                  "indexing.indexed_at": 1, "doc_class": 1, "meeting": 1}).sort("created_at", -1).limit(max(limit * 3, SCAN)))
        if not rows:
            return []
        done: Dict[str, Dict[str, dict]] = {}
        for e in self.store.events.find({"sha": {"$in": [r["sha256"] for r in rows]}}, {"sha": 1, "property_id": 1, "status": 1, "attempts": 1}):
            done.setdefault(e["sha"], {})[e["property_id"]] = e
        settle = now() - timedelta(minutes=20)
        out = []
        for r in rows:
            # Wait until the arrival chain has finished with it (attachments read, text indexed).
            if not (r.get("indexing") or {}).get("indexed_at") and r.get("created_at") and r["created_at"] > settle:
                continue
            need = False
            for pid in self.targets(r):
                b = built.get(pid)
                if not b or (r.get("created_at") and r["created_at"] <= b):
                    continue
                e = (done.get(r["sha256"]) or {}).get(pid)
                if e is None or (e.get("status") == "error" and int(e.get("attempts") or 0) < 3):
                    need = True
            if need:
                out.append(r)
        out.sort(key=lambda r: r.get("date") or r.get("created_at") or now())
        return out[:limit]

    def run_pending(self, *, max_items: int = 40, emit: Optional[Emit] = None, write_drafts: bool = True) -> Dict[str, Any]:
        """Read what has arrived. One pass at a time; a second caller returns at once."""
        if not Updater._run_lock.acquire(blocking=False):
            return {"skipped": "already running"}
        try:
            rows = by_file(self.pending(limit=max_items), self.targets)
            summary: Dict[str, Any] = {"read": 0, "drafts": 0, "errors": 0, "items": []}
            for r in rows:
                art = self.mongo.artifacts.find_one({"sha256": r["sha256"]}, {**EMAIL_FIELDS, **DOC_FIELDS})
                if not art:
                    continue
                res = self.process(art, write_drafts=write_drafts, emit=emit)
                summary["read"] += 1
                summary["drafts"] += 1 if res.get("draft") else 0
                summary["errors"] += len(res.get("errors") or [])
                summary["items"].append({"sha": r["sha256"][:12], "properties": list(res.get("properties") or {}), "draft": res.get("draft")})
            summary["remaining"] = max(0, len(self.pending(limit=max_items)) if rows else 0)
            return summary
        finally:
            Updater._run_lock.release()

    # ---------------------------------------------------------- bootstrap
    def _material(self, pid: str) -> Dict[str, Any]:
        parts: List[str] = []
        shas: Dict[str, dict] = {}
        general = pid == bc.GENERAL_ID
        if not general:
            dossier = self.mongo.db["dossiers"].find_one({"property_id": pid}, {"block": 1, "built_at": 1})
            if dossier and dossier.get("block"):
                parts.append(f"EARLIER INVESTIGATION (written {day(dossier.get('built_at'))} — a starting picture; newer records below override it):\n"
                             + clip(dossier["block"], 16_000, what="investigation"))
            run = self.mongo.db["next_steps_runs"].find_one({"status": "complete", f"properties.{pid}": {"$exists": True}},
                                                           {"day": 1, f"properties.{pid}": 1}, sort=[("started_at", -1)])
            if run:
                sheet = (run.get("properties") or {}).get(pid) or {}
                lines = [f"LAST NEXT-STEPS SHEET ({run.get('day')}): {sheet.get('headline') or ''}"]
                for person in ("rakesh", "jp", "manjunath", "wes"):
                    for s in sheet.get(person) or []:
                        lines.append(f"- [{person}] {s.get('title')} — {'DONE' if s.get('done') else 'not marked done'}"
                                     f"{', on the sheet since ' + str(s.get('first_seen')) if s.get('first_seen') else ''}. {s.get('detail') or ''}")
                parts.append("\n".join(lines))
            money = money_lines(self.store, pid)
            if money:
                parts.append("MONEY — RKB's own workbook figures:\n" + "\n".join(money))
        notes = standing_instructions(self.mongo, pid)
        if notes:
            parts.append("STANDING INSTRUCTIONS FROM THE TEAM:\n" + "\n".join(notes))

        days = 21 if general else bc.BOOTSTRAP_MAIL_DAYS
        mails = self.records.recent_mail(pid, days=days, limit=400)
        if not general and len(mails) < 15:
            mails = self.records.recent_mail(pid, days=150, limit=60)
        mails = mails[::-1]                                       # oldest first
        dirs = self.records.directions([m["sha256"] for m in mails])
        rendered = [(m, self.records.render_email(m, direction=dirs.get(m["sha256"], "received"), body_chars=30_000)) for m in mails]
        since = now() - timedelta(days=days if len(mails) >= 15 or general else 150)
        dq: Dict[str, Any] = {"source_type": {"$in": ["upload", "disk_file"]}, "created_at": {"$gte": since},
                              "$or": [{"doc_class": "meeting_transcript"}, {"meeting": {"$exists": True}}]}
        if not general:
            dq["property_ids"] = pid
        calls = [] if general else list(self.mongo.artifacts.find(dq, DOC_FIELDS).sort("date", 1).limit(12))
        call_txt = [(c, self.records.render_document(c, pid=pid, chars=14_000)) for c in calls]

        budget = bc.BOOTSTRAP_TOTAL_CHARS - sum(len(p) for p in parts) - sum(len(t) for _, t in call_txt)
        kept: List[tuple] = []
        for m, text in reversed(rendered):                        # newest are kept first when space runs out
            if budget - len(text) < 0:
                break
            budget -= len(text)
            kept.append((m, text))
        kept.reverse()
        dropped = len(rendered) - len(kept)
        merged = sorted([(m.get("date") or m.get("created_at"), m, t) for m, t in kept] +
                        [(c.get("date") or c.get("created_at"), c, t) for c, t in call_txt], key=lambda x: x[0] or now())
        for _, a, _t in merged:
            shas[a["sha256"][:12]] = a
        head = f"RECENT RECORDS, oldest first ({len(kept)} emails, {len(call_txt)} call notes"
        head += f"; {dropped} older emails left out for space — they are searchable)" if dropped else ")"
        parts.append(head + ":\n\n" + "\n\n".join(t for _, _, t in merged))
        return {"text": "\n\n".join(parts), "shas": shas, "dirs": dirs, "emails": len(kept), "calls": len(call_txt), "dropped": dropped}

    def bootstrap(self, pid: str, *, write_drafts: bool = True, emit: Optional[Emit] = None, max_drafts: int = 4) -> Dict[str, Any]:
        """Open the file for one property from what is on record."""
        started = now()
        say = emit or (lambda *_: None)
        say("status", {"text": f"{name_of(pid)}: reading the record…", "property_id": pid})
        mat = self._material(pid)
        brief = prompts.CORE + "\n" + prompts.BOOTSTRAP + (prompts.general_note() if pid == bc.GENERAL_ID else "")
        user = f"TODAY: {eastern.to_et(datetime.now(timezone.utc)):%A %d %B %Y}\nPROPERTY: {name_of(pid)} (id: {pid})\n\n{mat['text']}"
        r = structured(system=brief, user=user, schema=BOOTSTRAP_SCHEMA, effort=bc.EFFORT_BOOTSTRAP, max_tokens=40_000, label=f"bootstrap {pid}")
        d = r["data"]
        items = []
        for it in d.get("open_items") or []:
            title = str(it.get("title") or "").strip()
            if title and not any(_similar(title, x["title"]) for x in items):
                items.append(BrainStore.new_item(title=title, detail=it.get("detail") or "", owner=it.get("owner") or "rakesh",
                                                 kind=it.get("kind") or "action", due=it.get("due"), opened_at=started))
        self.store.save_file(pid, {
            "headline": str(d.get("headline") or "").strip()[:400], "status": str(d.get("status") or "").strip()[:4000],
            "facts": [{"key": str(f["key"]).strip()[:80], "value": str(f["value"]).strip()[:400], "set_at": started}
                      for f in (d.get("facts") or []) if f.get("key") and f.get("value")][:60],
            "open_items": items, "watch": [str(w).strip()[:300] for w in (d.get("watch") or []) if str(w).strip()][:12],
            "built_at": started, "built_from": {"emails": mat["emails"], "calls": mat["calls"], "left_out": mat["dropped"]},
            "build_usage": r["usage"], "build_cost_usd": cost_of(r["usage"]),
        })
        self.store.activity.delete_many({"property_id": pid, "from_bootstrap": True})
        for a in d.get("activity") or []:
            art = mat["shas"].get(str(a.get("sha") or "")[:12])
            if not art:
                continue
            direction = mat["dirs"].get(art["sha256"]) if self.is_email(art) else None
            self.store.add_activity(pid, at=self._event_at(art), kind=self.kind_of(art, direction), who=self.who_of(art, direction),
                                    title=a.get("title") or "", summary=a.get("summary") or "", sha=art["sha256"], extra={"from_bootstrap": True})
        drafted = []
        if write_drafts:
            for want in (d.get("needs_reply") or [])[:max_drafts]:
                art = mat["shas"].get(str(want.get("sha") or "")[:12])
                if not art or not self.is_email(art) or mat["dirs"].get(art["sha256"]) == "sent":
                    continue
                say("status", {"text": f"{name_of(pid)}: drafting the reply to “{art.get('subject')}”…", "property_id": pid})
                did = self._draft_for(art, [{"pid": pid, "needed": True, "sender": want.get("sender") or bc.DEFAULT_SENDER,
                                             "urgency": "this_week", "why": want.get("why") or "", "points": want.get("points") or []}])
                if did:
                    drafted.append(did)
        out = {"property_id": pid, "open_items": len(items), "activity": len(d.get("activity") or []), "drafts": drafted,
               "emails_read": mat["emails"], "calls_read": mat["calls"], "left_out": mat["dropped"], "cost_usd": cost_of(r["usage"]),
               "elapsed_s": round((now() - started).total_seconds(), 1)}
        say("property", out)
        return out
