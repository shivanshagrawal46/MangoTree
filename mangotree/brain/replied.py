"""Already replied? — what has gone out leaves the list by itself.

What is on a person's list must be what is still owed. A reply that has gone
out, and the to-do that asked for it, must leave the list without anyone
ticking anything; when they stay, nobody trusts the list (admin, 2026-11-21:
"if any reply already send or a to do or follow up already done it should be
automatically removed. If we will not do so the trust on the system reduces").

Reading new mail into the files happens twice a day. This does not wait for it.
It runs every time mail is taken in, in two parts:

1. Finding the reply — from the mail itself, no model. For every waiting reply
   and every "reply to …" to-do, the emails RKB sent afterwards are looked up
   (``Answered.candidates``): in the same conversation, a direct reply, or to the
   same person under another subject.

2. Deciding that it is the reply — one small reading of the incoming email
   against what RKB sent (``Answered.check``), and only when there is something
   new to weigh. This is a judgement and code cannot make it: on 18 Nov 2026 JP
   Sir wrote "please find attached data tape" in a conversation whose open
   request was the recorded deed of trust; same conversation, same person, and
   not the reply. Matching by conversation alone would have taken a request
   that is still owed off the list.

Between the two daily runs only a strong sign starts that reading: RKB writing
in the very conversation (or as a direct reply) to the person who wrote. An
email to the same person under another subject is weighed at the daily runs
(``run(full=True)``).

What happens then
    answered            the waiting reply is closed as sent, and its to-do with it
    answered in part    the waiting reply is closed when RKB replied to that very
                        email (it must not sit beside a reply already sent); the
                        to-do stays, and now says what was sent and what is still owed
    not answered        nothing changes; the same emails are not weighed again

Only Rakesh Sir's mailboxes are read, so a reply from Manjunath Sir or JP Sir is
seen when Rakesh Sir is copied on it — which is why their drafts always carry
him in copy (``DraftWriter.with_rakesh_copied``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from .answered import Answered, replied
from .context import day, EMAIL_FIELDS, et
from .store import BrainStore, now

OWN_LINKS = ("same_conversation", "direct_reply")


def _aware(d: Any) -> Optional[datetime]:
    if not isinstance(d, datetime):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def strong_sign(art: Dict[str, Any], cands: Sequence[Dict[str, Any]], seen: Optional[str]) -> bool:
    """Has RKB written in this very conversation (or as a direct reply) to the person who wrote,
    in an email not yet weighed? ``seen`` is the newest email the last check read."""
    asked = {str(a).lower() for a in ((art.get("participants") or {}).get("from") or [])}
    upto = next((_aware(c.get("date")) for c in cands if c.get("sha256") == seen), None) if seen else None
    for c in cands:
        if c.get("link") not in OWN_LINKS:
            continue
        got = {str(a).lower() for k in ("to", "cc") for a in ((c.get("participants") or {}).get(k) or [])}
        if not (got & asked):
            continue                          # an internal forward: the person who wrote has heard nothing
        when = _aware(c.get("date"))
        if upto is None or (when and when > upto):
            return True
    return False


def owed_note(verdict: Dict[str, Any]) -> str:
    """What a to-do says once RKB has replied in part: who wrote, when, and what is still owed."""
    by = (verdict.get("by") or [{}])[-1]
    owed = [str(x) for x in (verdict.get("still_owed") or []) if x]
    return (f"{by.get('who') or 'RKB'} replied on {et(by.get('at'), '%d %b %Y %H:%M')}. {verdict.get('what_rkb_said') or ''}".strip()
            + (" Still owed: " + "; ".join(owed) + "." if owed else ""))


class Replied:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None, writer=None, answered: Optional[Answered] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self._writer = writer
        self._answered = answered

    @property
    def writer(self):
        if self._writer is None:
            from .drafts import DraftWriter
            self._writer = DraftWriter(self.mongo, self.store)
        return self._writer

    @property
    def answered(self) -> Answered:
        if self._answered is None:
            self._answered = self.writer.answered
        return self._answered

    def _due(self, art: Dict[str, Any], cands: Sequence[Dict[str, Any]], prev: Dict[str, Any], full: bool) -> bool:
        """Is there something to weigh now? Nothing new since the last check: no. At a daily run:
        anything new. In between: only RKB writing in the conversation to the person who wrote."""
        if not cands or prev.get("latest") == cands[-1]["sha256"]:
            return False
        return full or strong_sign(art, cands, prev.get("latest"))

    # ------------------------------------------------------------------ a pass
    def run(self, *, full: bool = False) -> Dict[str, Any]:
        """One pass over what is waiting. Returns what was closed, what was checked and what it cost."""
        out: Dict[str, Any] = {"drafts": [], "items": [], "partly": [], "checked": 0, "cost_usd": 0.0}
        has_draft = set()
        # 1. Replies written and waiting to be sent.
        for d in self.store.list_drafts(status=("ready",), limit=500):
            sha = d.get("reply_to_sha")
            if not sha:
                continue
            has_draft.add(sha)
            try:
                art = self.mongo.artifacts.find_one({"sha256": sha}, EMAIL_FIELDS)
                if not art:
                    continue
                cands = self.answered.candidates(art)
                if not self._due(art, cands, d.get("answer_check") or {}, full):
                    continue
                out["checked"] += 1
                after = self.writer.recheck(d, force=True)       # reads what RKB sent; closes the draft (and its to-do) when that was the reply
                if after.get("status") == "sent":
                    out["drafts"].append({"draft_id": d["draft_id"], "subject": d.get("subject"), "by": after.get("sent_by"), "at": after.get("sent_at")})
            except Exception:
                logger.exception("replied: draft %s could not be checked", d.get("draft_id"))
        # 2. "Reply to …" to-dos with no written reply behind them.
        for f in self.store.list_files():
            pid = f["property_id"]
            for i in BrainStore.open_items(f):
                sha = i.get("source_sha")
                if i.get("kind") != "reply" or not sha or sha in has_draft:
                    continue
                try:
                    art = self.mongo.artifacts.find_one({"sha256": sha, "source_type": "email"}, EMAIL_FIELDS)
                    if not art:
                        continue                  # raised by a call or a document, not by an email
                    cands = self.answered.candidates(art)
                    if not self._due(art, cands, i.get("reply_check") or {}, full):
                        continue
                    out["checked"] += 1
                    verdict = self.answered.check(art, candidates=cands)
                    out["cost_usd"] = round(out["cost_usd"] + float(verdict.get("cost_usd") or 0), 4)
                    if verdict.get("status") == "unknown":
                        continue                  # the check could not run: it is tried again, and nothing is closed on it
                    mark = {"latest": verdict.get("latest"), "status": verdict.get("status"), "checked_at": now()}
                    self.store.files.update_one({"property_id": pid, "open_items.id": i["id"]}, {"$set": {"open_items.$.reply_check": mark}})
                    by = (verdict.get("by") or [{}])[-1]
                    if verdict["status"] == "answered":
                        note = owed_note(verdict) + " Closed from the sent mail."
                        if self.store.close_item(pid, i["id"], note=note, by="mail", sha=by.get("sha")):
                            self.store.add_activity(pid, at=by.get("at") or now(), kind="reply_sent", who=by.get("who") or "RKB",
                                                    title=f"Reply sent by {by.get('who') or 'RKB'} — to-do closed",
                                                    summary=f"“{i.get('title')}” is done. {note}", sha=by.get("sha"), relevance="minor")
                            out["items"].append({"property_id": pid, "item_id": i["id"], "title": i.get("title"), "by": by.get("who"), "at": by.get("at")})
                    elif replied(verdict):
                        # RKB replied to this very email but not to all of it: the to-do stays, saying what is left.
                        self.store.update_item(pid, i["id"], {"detail": owed_note(verdict)})
                        out["partly"].append({"property_id": pid, "item_id": i["id"], "title": i.get("title")})
                except Exception:
                    logger.exception("replied: to-do %s on %s could not be checked", i.get("id"), pid)
        if out["drafts"] or out["items"]:
            logger.info("replied: closed %d waiting repl%s and %d to-do(s) from the sent mail", len(out["drafts"]),
                        "y" if len(out["drafts"]) == 1 else "ies", len(out["items"]))
        return out
