"""Reply drafts — written the way the sender writes, with the file in view.

A draft is produced by the same tool loop the chat uses: the model has the
property file, the whole conversation the email belongs to, examples of emails
this person really sent, and it may open any record it needs before it commits
to a figure or a date. Nothing is ever sent from here. A draft waits in the
drafts list (and, when switched on, in the Outlook Drafts folder) until the
person sends it himself — and when his sent mail is ingested, the draft is
closed against it automatically.
"""
from __future__ import annotations

import difflib
import html
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.retrieve import config as rcfg
from mangotree.storage.mongo import Mongo

from . import checks
from . import config as bc
from . import prompts
from . import verify
from .agent import run_loop
from .answered import Answered, replied
from .context import EMAIL_FIELDS, Records, day, property_block
from .lessons import Lessons, changed_enough
from .llm import add_usage, cost_of, system_blocks
from .store import BrainStore, name_of, now
from .tools import Toolkit

SUBMIT_DRAFT = {
    "name": "submit_draft",
    "description": "Hand over the finished reply. Call exactly once, at the end.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["needed", "to", "cc", "subject", "body", "note"],
        "properties": {
            "needed": {"type": "boolean", "description": "false if, having read everything, no reply is actually owed"},
            "to": {"type": "array", "items": {"type": "string"}, "description": "recipient email addresses"},
            "cc": {"type": "array", "items": {"type": "string"}, "description": "copied email addresses"},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "the complete email as plain text, greeting to sign-off"},
            "note": {"type": "string", "description": "for the sender only: what this commits to, what could not be confirmed, or the decision it assumes"},
        },
    },
}

_USERS = {"rakesh": "ceo", "jp": "accountant", "manjunath": "operations"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def similarity(a: str, b: str) -> float:
    """How much of the draft survived into what was actually sent (0..1)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def _left_open(verdict: Dict[str, Any]) -> str:
    """What RKB's reply did not take up, for the note on a draft closed because RKB replied."""
    owed = [str(x) for x in (verdict.get("still_owed") or []) if x]
    return (" Not taken up in that reply: " + "; ".join(owed) + ".") if verdict.get("status") == "partly_answered" and owed else ""


def body_html(body: str) -> str:
    paras = [p for p in re.split(r"\n{2,}", (body or "").strip()) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paras)


class DraftWriter:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.records = Records(mongo)
        self.answered = Answered(mongo, self.records)

    # ------------------------------------------------------------- sender
    def sender_addresses(self, sender: str) -> List[str]:
        p = self.mongo.people.find_one({"person_id": sender}, {"addresses": 1, "send_as": 1}) or {}
        return [str(a).lower() for a in (p.get("addresses") or []) + (p.get("send_as") or [])]

    def _user(self, sender: str) -> Dict[str, Any]:
        u = self.mongo.db["users"].find_one({"user_id": sender}, {"_id": 0, "user_id": 1, "name": 1, "role": 1}) or {}
        return {"user_id": sender, "name": u.get("name") or bc.SENDER_NAMES.get(sender, sender), "role": u.get("role") or _USERS.get(sender)}

    # -------------------------------------------------------------- write
    @staticmethod
    def _evidence(seeded: str, messages: Sequence[dict]) -> str:
        """What retrieval and the lookups put in front of the writer, for the checker."""
        parts = [seeded]
        for m in messages:
            c = m.get("content")
            if isinstance(c, list):
                parts += [b["content"] for b in c if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), str)
                          and b["content"] not in ("received", "accepted")]
        return "\n\n".join(p for p in parts if p)

    def write(self, email_sha: str, *, pids: Sequence[str], points: Sequence[str] = (), sender: str = bc.DEFAULT_SENDER,
              why: str = "", urgency: str = "", instruction: str = "", replaces: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Write the reply to one received email, then have it checked.

        What the writer has in front of it: who RKB is and the rules; every
        email AND every file of the last 14 days for the property, in full; the
        property file; what the document search found on this subject across
        the whole record; the email and its conversation; emails this person
        really sent; and what he has corrected in earlier drafts.

        Then a second reader checks every statement against the same record.
        Problems go back to the writer once. Anything still open is put at the
        top of the note. Returns the stored draft, or a dict with
        ``needed: False`` when no reply is owed."""
        art = self.mongo.artifacts.find_one({"sha256": email_sha}, EMAIL_FIELDS)
        if not art:
            raise ValueError(f"email {email_sha[:12]} not found")
        sender = sender if sender in bc.SENDER_NAMES else bc.DEFAULT_SENDER
        pids = [p for p in pids if p] or [bc.GENERAL_ID]
        started = time.time()

        # First: has RKB already answered this email? The sent mail is read — every
        # later email from anyone at RKB in this conversation or to this person,
        # under any subject. Answered means no draft, and an existing draft is closed.
        already = self.answered.check(art)
        if replied(already):
            by = (already.get("by") or [{}])[-1]
            note = (f"Already answered: {by.get('who') or 'RKB'} replied on {day(by.get('at'), '%d %b %Y %H:%M')} UTC. {already.get('what_rkb_said') or ''}"
                    + _left_open(already)).strip()
            if replaces:
                existing = self.store.get_draft(replaces)
                if existing and existing.get("status") == "ready":
                    self.close_as_answered(existing, already)
            logger.info("no draft for %s — %s", email_sha[:12], note[:160])
            return {"needed": False, "already_answered": True, "note": note, "answered_by": already.get("by"), "cost_usd": already.get("cost_usd", 0)}
        partly = ""
        if already["status"] == "partly_answered":
            sent_texts = []
            for x in already.get("by") or []:
                e = self.mongo.artifacts.find_one({"sha256": x["sha"]}, EMAIL_FIELDS)
                if e:
                    sent_texts.append(self.records.render_email(e, direction="sent"))
            partly = ("RKB HAS ALREADY REPLIED IN PART — read what was sent, do not repeat it, and do not contradict it. "
                      f"{already.get('what_rkb_said') or ''}\nStill owed, and the only thing this reply is for: "
                      + "; ".join(already.get("still_owed") or ["(see the email)"]) + "\n\nWHAT RKB SENT:\n\n" + "\n\n".join(sent_texts))
        elif already["status"] == "unknown":
            partly = ("NOTE: RKB has written to this person since this email came in, and the check of whether that already answers it could not be "
                      "completed. Read THE MAIL carefully for an RKB reply before you write; if you find one, submit with needed=false.")

        record = "\n\n".join(self.records.record_block(pid) for pid in pids[:4])
        files = "\n\n".join(property_block(self.mongo, self.store, pid) for pid in pids[:4])
        shared = [prompts.CORE, record, files]
        system = system_blocks(*shared, prompts.DRAFT)
        fresh = self.records.freshness(pids[0])

        p = art.get("participants") or {}
        to_default = list(p.get("from") or [])
        mine = set(self.sender_addresses(sender)) | set(self.records.rkb_addresses())
        cc_default = [a for a in (p.get("to") or []) + (p.get("cc") or []) if a.lower() not in set(self.sender_addresses(sender))]
        examples = self.records.sent_examples(to_default, sender_addresses=self.sender_addresses(sender) or list(mine)[:1])
        learned = Lessons(self.mongo, self.store).block(sender=sender, recipients=to_default, pids=pids)
        signature = rcfg.EMAIL_SIGNATURES.get(bc.SENDER_NAMES.get(sender, "Rakesh"), "")
        thread_text = self.records.render_thread(art, direction="received")
        user_parts = [
            fresh,
            f"THE REPLY IS FROM: {bc.SENDER_NAMES.get(sender, sender)} (RKB). Sign off exactly as:\n{signature}",
            f"REPLY TO: {', '.join(self.records.who(a) for a in to_default) or '(sender unknown)'}",
            ("PEOPLE COPIED ON THE INCOMING EMAIL: " + ", ".join(self.records.who(a) for a in cc_default)) if cc_default else "",
            partly,
            ("WHY A REPLY IS OWED: " + why) if why else "",
            ("POINTS THE REPLY MUST COVER:\n" + "\n".join(f"- {x}" for x in points)) if points else "",
            ("INSTRUCTION FROM THE SENDER FOR THIS DRAFT (follow it):\n" + instruction) if instruction else "",
            (f"YOU ARE REWRITING DRAFT {replaces}: what you submit replaces it. It is not a second reply; do not mention it in the note.") if replaces else "",
            learned,
            ("EMAILS HE ACTUALLY SENT — for voice, length and sign-off only; their content is not about this matter:\n\n" + "\n\n---\n\n".join(examples)) if examples else "",
            thread_text,
        ]
        kit = Toolkit(self.mongo, self.store, pid=pids[0] if len(pids) == 1 else None, user=self._user(sender), allow_actions=False)
        # The document search runs first, across the whole record, on the subject of this email.
        # One search for the subject of the email, and one for each point the reply
        # must cover, side by side. The sender's address and the email's own text
        # are kept out of the search question: the pipeline reads a stated sender,
        # date or file type as a hard filter, and on a Marmon draw email
        # (2026-11-12) that returned nothing at all.
        subject = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", art.get("subject") or "", flags=re.I).strip()
        where = ", ".join(name_of(p) for p in pids[:3] if p != bc.GENERAL_ID)
        seeded, seed_info = kit.seed(f"What do the records show about “{subject}”" + (f" on {where}?" if where else "?"),
                                     also=[f"{x}" + (f" ({where})" if where else "") for x in list(points)[:5]])
        messages = [{"role": "user", "content": "\n\n".join(x for x in user_parts if x) + "\n\n" + seeded}]
        usage: Dict[str, int] = {}
        res = run_loop(system=system, messages=messages, toolkit=kit, effort=bc.EFFORT_DRAFT, max_rounds=bc.DRAFT_MAX_ROUNDS,
                       max_tokens=24_000, terminal_tool=SUBMIT_DRAFT)
        add_usage(usage, res.usage)
        if res.refused:
            logger.warning("draft for %s: the model declined (%s)", email_sha[:12], res.refusal_category)
            return None
        out = res.terminal
        if not out:
            logger.warning("draft for %s: no submit_draft call (stop=%s)", email_sha[:12], res.stop_reason)
            return None
        lookups = [f"Full search of the records ({seed_info.get('searches', 1)} question{'s' if seed_info.get('searches', 1) != 1 else ''}): "
                   f"{seed_info.get('passages', 0)} passages"] + [t["label"] for t in res.tool_log]
        rounds = res.rounds

        # ---- the second reading: every statement against the record, then one correction
        vsystem = system_blocks(*shared, verify.BRIEF)
        passes: List[Dict[str, Any]] = []
        found_first = 0
        for attempt in (1, 2):
            body = str(out.get("body") or "").strip()
            if not out.get("needed") or not body:
                break
            written = f"TO: {', '.join(out.get('to') or to_default)}\nSUBJECT: {out.get('subject') or ''}\n\n{body}"
            v = verify.check(system=vsystem, asked=thread_text, written=written, evidence=self._evidence(seeded, messages), freshness=fresh, kind="draft",
                             tools=list(kit.specs()) + [SUBMIT_DRAFT])
            add_usage(usage, v.get("usage") or {})
            passes.append(v)
            if attempt == 1:
                found_first = len(v["problems"])
            if not v["ran"] or not v["problems"] or attempt == 2:
                break
            messages.append({"role": "user", "content": verify.as_instruction(v["problems"])
                             + "\n\nThen call submit_draft again with the corrected reply (or with needed=false if the reader has shown that no reply is owed)."})
            res2 = run_loop(system=system, messages=messages, toolkit=kit, effort=bc.EFFORT_DRAFT, max_rounds=bc.DRAFT_MAX_ROUNDS,
                            max_tokens=24_000, terminal_tool=SUBMIT_DRAFT)
            add_usage(usage, res2.usage)
            rounds += res2.rounds
            lookups += [t["label"] for t in res2.tool_log]
            if not res2.terminal:
                break
            out = res2.terminal

        meta = {"usage": usage, "cost_usd": cost_of(usage), "rounds": rounds, "elapsed_s": round(time.time() - started, 1),
                "lookups": lookups, "retrieval": kit.search_trace}
        if not out.get("needed") or not str(out.get("body") or "").strip():
            logger.info("draft for %s: no reply needed — %s", email_sha[:12], str(out.get("note"))[:160])
            return {"needed": False, "note": out.get("note"), **meta}

        last = passes[-1] if passes else {"ran": False, "problems": [], "statements_checked": 0}
        open_problems = verify.for_person(last["problems"]) if last.get("ran") else []
        body = str(out["body"]).strip()
        people = kit._people(out.get("to") or to_default)
        # A mechanical check as well: every amount, percentage and reference number must be something that was read.
        read = checks.texts_of(system, messages) + [kit.evidence_text()]
        missing = checks.not_found(body, read)
        note = str(out.get("note") or "").strip()
        flags: List[str] = []
        if not last.get("ran"):
            flags.append("The second check could not be run on this draft, so nothing in it has been independently confirmed. Read it against the email before sending.")
        for pr in open_problems:
            flags.append(f"{pr['label']}: “{pr['statement']}” — {pr['record_shows']}")
        if missing:
            flags.append(f"{', '.join(missing[:8])} {'does' if len(missing) == 1 else 'do'} not appear word for word in the records read "
                         f"({'it is' if len(missing) == 1 else 'they are'} a calculation, or should be confirmed).")
        if flags:
            note = "CHECK FIRST — " + " | ".join(flags) + ("\n\n" + note if note else "")
        meta["checks"] = {"figures": len(checks.figures(body)), "not_found": missing[:20]}
        meta["verification"] = {"ran": bool(last.get("ran")), "passes": len(passes), "statements_checked": last.get("statements_checked", 0),
                                "found": found_first, "corrected": max(0, found_first - len(open_problems)) if len(passes) > 1 else 0,
                                "open": open_problems}
        doc = {"property_ids": [p for p in pids if p], "reply_to_sha": email_sha, "thread_key": art.get("thread_key"),
               "to": people or kit._people(to_default), "cc": kit._people(out.get("cc") or []),
               "subject": str(out.get("subject") or f"Re: {art.get('subject') or ''}").strip()[:300], "body": body,
               "note": note[:2500], "sender": sender, "origin": "auto", "why": why, "urgency": urgency,
               "points": list(points), "in_reply_to": self._answers(art),
               "answer_check": {k: already.get(k) for k in ("status", "latest", "checked_at", "what_rkb_said", "still_owed", "candidates")},
               "original_body": body, "written_at": now(), "stale": None, **meta}
        if replaces:
            d = self.store.update_draft(replaces, {**doc, "status": "ready", "edited": False})
        else:
            # A newer message in the same conversation makes an unsent draft stale.
            if art.get("thread_key"):
                self.store.drafts.update_many({"thread_key": art["thread_key"], "status": "ready", "origin": "auto"},
                                              {"$set": {"status": "superseded", "updated_at": now(), "superseded_by_sha": email_sha}})
            d = self.store.create_draft(doc)
        if self.store.setting(bc.SETTING_OUTLOOK_DRAFTS, False):
            try:
                self.to_outlook(d["draft_id"])
            except Exception as exc:
                logger.warning("draft %s: could not place in Outlook Drafts: %s", d["draft_id"], exc)
        return self.store.get_draft(d["draft_id"])

    # -------------------------------------------- has it already been answered?
    def _answers(self, art: Dict[str, Any]) -> Dict[str, Any]:
        """Which email a draft answers, in the words the person sees."""
        frm = ((art.get("participants") or {}).get("from") or [""])[0]
        return {"sha": art["sha256"], "subject": art.get("subject"), "from": list((art.get("participants") or {}).get("from") or []),
                "from_name": self.records.name(frm), "date": art.get("date")}

    def close_as_answered(self, draft: Dict[str, Any], verdict: Dict[str, Any]) -> Dict[str, Any]:
        """RKB has sent the reply. The draft leaves the waiting list, is marked sent
        and done with who sent it and when, and the property's log says so.

        If the reply went out after the draft was written, what was sent is
        compared with what was drafted — how much was kept, and what the change
        teaches. If it went out before, the draft should never have been written;
        that is recorded as such, and nothing is learned from it."""
        by = (verdict.get("by") or [{}])[-1]
        sent = self.mongo.artifacts.find_one({"sha256": by.get("sha")}, {"sha256": 1, "date": 1, "body_clean": 1, "subject": 1}) if by.get("sha") else None
        made = draft.get("written_at") or draft.get("created_at")
        made = made.replace(tzinfo=made.tzinfo or timezone.utc) if made else None
        after_draft = bool(sent and sent.get("date") and made and sent["date"] >= made)
        fields: Dict[str, Any] = {
            "status": "sent", "auto_closed": True, "sent_sha": by.get("sha"), "sent_at": by.get("at"), "sent_by": by.get("who"), "stale": None,
            "closed_at": now(), "closed_note": (f"{by.get('who') or 'RKB'} replied on {day(by.get('at'), '%d %b %Y %H:%M')} UTC"
                                               + (f" (“{by.get('subject')}”)" if by.get("subject") else "") + f". {verdict.get('what_rkb_said') or ''}"
                                               + _left_open(verdict)).strip(),
            "answer_check": {k: verdict.get(k) for k in ("status", "latest", "checked_at", "what_rkb_said", "still_owed", "candidates")},
            "answered_before_draft": not after_draft,
        }
        if after_draft and (sent.get("body_clean") or "").strip():
            sim = similarity(draft.get("original_body") or draft.get("body") or "", sent["body_clean"])
            fields.update({"kept_ratio": sim, "sent_as_written": sim >= 0.9})
        out = self.store.update_draft(draft["draft_id"], fields) or draft
        for pid in draft.get("property_ids") or [bc.GENERAL_ID]:
            self.store.add_activity(pid, at=by.get("at") or now(), kind="reply_sent", who=by.get("who") or "RKB",
                                    title=f"Reply sent by {by.get('who') or 'RKB'} — draft closed",
                                    summary=(f"Answers “{(draft.get('in_reply_to') or {}).get('subject') or draft.get('subject')}”. {verdict.get('what_rkb_said') or ''}"
                                             + _left_open(verdict)).strip(),
                                    sha=by.get("sha"), relevance="minor")
        if after_draft and not draft.get("learned") and changed_enough(draft.get("original_body") or draft.get("body") or "", sent.get("body_clean") or ""):
            self.store.update_draft(draft["draft_id"], {"learned": True})
            Lessons(self.mongo, self.store).learn_async(draft=draft, final_text=sent["body_clean"], source="sent", sent_sha=sent["sha256"])
        logger.info("draft %s closed: %s", draft["draft_id"], fields["closed_note"][:160])
        return out

    def recheck(self, draft: Dict[str, Any], *, force: bool = False) -> Dict[str, Any]:
        """Check one waiting draft against the sent mail, and act on the result.

        answered         -> the draft is closed as sent and done
        partly answered  -> closed too when RKB replied to this very email and the reply went to the
                            person who wrote it (what was left open is kept on the closed draft);
                            otherwise it stays, marked out of date, saying what RKB said and what is still owed
        not answered     -> it stays
        The other side writing again in the same conversation also marks it out of
        date: it answers a message that is no longer the latest."""
        if draft.get("status") != "ready" or not draft.get("reply_to_sha"):
            return draft
        art = self.mongo.artifacts.find_one({"sha256": draft["reply_to_sha"]}, EMAIL_FIELDS)
        if not art:
            return draft
        fields: Dict[str, Any] = {}
        if not (draft.get("in_reply_to") or {}).get("from_name"):
            fields["in_reply_to"] = self._answers(art)
        cands = self.answered.candidates(art)
        prev = draft.get("answer_check") or {}
        latest = cands[-1]["sha256"] if cands else None
        if cands and (force or prev.get("latest") != latest or prev.get("status") in (None, "unknown")):
            verdict = self.answered.check(art, candidates=cands)
            if replied(verdict):
                if fields:
                    self.store.update_draft(draft["draft_id"], fields)
                return self.close_as_answered({**draft, **fields}, verdict)
            fields["answer_check"] = {k: verdict.get(k) for k in ("status", "latest", "checked_at", "what_rkb_said", "still_owed", "candidates", "error")}
            if verdict["status"] == "partly_answered":
                by = (verdict.get("by") or [{}])[-1]
                fields["stale"] = {"since": by.get("at"), "sha": by.get("sha"), "kind": "partly_answered",
                                   "why": (f"Part of this has already been dealt with. {by.get('who') or 'RKB'} wrote on {day(by.get('at'), '%d %b %H:%M')} UTC: "
                                           f"{verdict.get('what_rkb_said') or ''} Still owed: {'; '.join(verdict.get('still_owed') or []) or 'see the email'}.").strip()}
            elif verdict["status"] == "unknown":
                fields["stale"] = {"kind": "unchecked", "why": "RKB has written to this person since this email came in, and the check of whether "
                                                             "that already answers it could not be completed. Look at the sent mail before sending this."}
            elif (draft.get("stale") or {}).get("kind") in ("partly_answered", "unchecked"):
                fields["stale"] = None
        elif not cands and not prev:
            fields["answer_check"] = {"status": "not_answered", "latest": None, "checked_at": now(), "candidates": 0}
        # The other side has written again in the same conversation.
        if art.get("thread_key") and art.get("date") and not (fields.get("stale") or {}).get("kind"):
            newer = self.mongo.artifacts.find_one({"thread_key": art["thread_key"], "source_type": "email", "date": {"$gt": art["date"]},
                                                   "participants.from": {"$nin": self.answered.rkb_addresses()}},
                                                  {"sha256": 1, "date": 1, "participants.from": 1}, sort=[("date", -1)])
            if newer and (draft.get("stale") or {}).get("sha") != newer["sha256"]:
                frm = ((newer.get("participants") or {}).get("from") or [""])[0]
                fields["stale"] = {"since": newer.get("date"), "sha": newer["sha256"], "kind": "newer_message",
                                   "why": f"{self.records.name(frm)} wrote again in this conversation on {newer['date']:%d %b %H:%M} UTC, after the email this draft answers."}
        return (self.store.update_draft(draft["draft_id"], fields) or draft) if fields else draft

    def needs_check(self, draft: Dict[str, Any]) -> bool:
        """Is there sent mail this draft has not been checked against? (No model call.)"""
        if draft.get("status") != "ready" or not draft.get("reply_to_sha"):
            return False
        art = self.mongo.artifacts.find_one({"sha256": draft["reply_to_sha"]}, EMAIL_FIELDS)
        if not art:
            return False
        cands = self.answered.candidates(art)
        prev = draft.get("answer_check") or {}
        return bool(cands) and (prev.get("latest") != cands[-1]["sha256"] or prev.get("status") in (None, "unknown"))

    def refresh(self, drafts: Sequence[Dict[str, Any]], *, force: bool = False, within: int = 120) -> List[Dict[str, Any]]:
        """Before waiting drafts are shown or rewritten: check each against the sent
        mail. Drafts whose reply has gone out come back as sent, not as waiting."""
        import time
        seen = self.__dict__.setdefault("_checked_at", {})
        t = time.time()
        # A draft looked at in the last two minutes is not looked at again for a page that
        # is merely being reopened. New RKB mail does not wait for this: on_rkb_email checks at once.
        ready = [d for d in drafts if d.get("status") == "ready" and d.get("reply_to_sha") and (force or not within or t - seen.get(d["draft_id"], 0) > within)]
        if not ready:
            return list(drafts)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            done = {d["draft_id"]: r for d, r in zip(ready, pool.map(lambda d: self.recheck(d, force=force), ready))}
        for d in ready:
            seen[d["draft_id"]] = t
        return [done.get(d["draft_id"], d) for d in drafts]

    def refresh_soon(self, drafts: Sequence[Dict[str, Any]], on_change=None) -> bool:
        """The same check, without making the page wait for it. The page is answered
        from what is stored; if the check then closes or flags a draft, ``on_change``
        is called so the next look shows it. (New RKB mail does not depend on this:
        on_rkb_email checks the moment it is read in.)"""
        import threading
        import time
        ready = [d for d in drafts if d.get("status") == "ready" and d.get("reply_to_sha")]
        seen = self.__dict__.setdefault("_checked_at", {})
        t = time.time()
        if not ready or self.__dict__.get("_refreshing") or all(t - seen.get(d["draft_id"], 0) <= 120 for d in ready):
            return False
        self._refreshing = True

        def work():
            try:
                after = self.refresh(ready)
                if on_change and any((a.get("status"), bool(a.get("stale"))) != (b.get("status"), bool(b.get("stale"))) for a, b in zip(after, ready)):
                    on_change()
            except Exception:
                logger.exception("background check of waiting drafts failed")
            finally:
                self._refreshing = False
        threading.Thread(target=work, daemon=True, name="draft-check").start()
        return True

    def on_rkb_email(self, sent: Dict[str, Any]) -> List[str]:
        """Someone at RKB has just sent an email. Every waiting draft it could be the
        answer to — same conversation, or addressed to the same person — is
        checked at once, and closed if this was the reply."""
        people = {str(a).lower() for k in ("to", "cc") for a in ((sent.get("participants") or {}).get(k) or [])}
        ors: List[Dict[str, Any]] = []
        if sent.get("thread_key"):
            ors.append({"thread_key": sent["thread_key"]})
        if people:
            ors += [{"to.email": {"$in": list(people)}}, {"in_reply_to.from": {"$in": list(people)}}]
        if not ors:
            return []
        drafts = list(self.store.drafts.find({"status": "ready", "$or": ors}, {"_id": 0}))
        closed = [d["draft_id"] for d in self.refresh(drafts, within=0) if d.get("status") == "sent"]
        return closed

    # ------------------------------------------------------------ outlook
    def _original_in_outlook(self, g, base: str, headers: Dict[str, str], d: Dict[str, Any]):
        """The Outlook id of the email this draft answers, and — when there is none —
        the plain reason why. The stored id is tried first; if the message has since
        been moved (ids change when mail is filed), it is found again by its Message-ID."""
        from urllib.parse import quote
        sha = d.get("reply_to_sha")
        if not sha:
            return None, "this draft was not written in answer to a particular email"
        mid = self.records.provider_message_id(sha)
        if mid:
            r = g._session.get(f"{base}/messages/{quote(mid)}?$select=id", headers=headers, timeout=max(g.timeout, 60))
            if r.status_code == 200:
                return mid, ""
        art = self.mongo.artifacts.find_one({"sha256": sha}, {"internet_message_id": 1}) or {}
        imid = str(art.get("internet_message_id") or "").replace("'", "''")
        if imid:
            r = g._session.get(f"{base}/messages", params={"$filter": f"internetMessageId eq '{imid}'", "$select": "id", "$top": "1"},
                               headers=headers, timeout=max(g.timeout, 60))
            hits = (r.json().get("value") or []) if r.status_code == 200 else []
            if hits:
                return hits[0]["id"], ""
        providers = {o.get("provider") for o in self.mongo.occurrences.find({"artifact_sha": sha}, {"provider": 1})}
        if "outlook" not in providers and "gmail" in providers:
            return None, "the email it answers arrived in the Gmail mailbox, not in this Outlook mailbox, so Outlook has no conversation to reply in"
        return None, "the email it answers could not be found in the Outlook mailbox (it may have been deleted or moved out of reach)"

    def to_outlook(self, draft_id: str) -> Dict[str, Any]:
        """Put the draft in the mailbox's Drafts folder as a REPLY ALL to the email it
        answers: Outlook itself creates the reply, so it sits inside that conversation,
        carries "RE:" and the quoted original, and is addressed to everyone on the
        email. Our text goes above the quote. Nothing is sent.

        Only when the original is not in this mailbox is a new, unthreaded email
        saved instead — and the result says so and why."""
        from urllib.parse import quote
        from mangotree.ingest.graph_auth import DRAFT_SCOPES
        from mangotree.mail.outbox import GRAPH_ROOT, Outbox
        d = self.store.get_draft(draft_id)
        if not d:
            raise ValueError("draft not found")
        ob = Outbox(self.mongo)
        g = ob._graph()
        token = g.auth.access_token(DRAFT_SCOPES)
        headers = {"Authorization": f"Bearer {token}"}
        content = body_html(d.get("body") or "")
        base = f"{GRAPH_ROOT}/users/{quote(g.mailbox)}"
        wait = max(g.timeout, 60)

        def person(t: Dict[str, Any]) -> Dict[str, Any]:
            return {"emailAddress": {"address": t["email"], "name": t.get("name") or t["email"]}}

        def addr(r: Dict[str, Any]) -> str:
            return str((r.get("emailAddress") or {}).get("address") or "").lower()

        # "Update in Outlook": the copy placed there earlier is replaced, not added to.
        old = (d.get("outlook") or {}).get("message_id")
        if old:
            chk = g._session.get(f"{base}/messages/{quote(old)}?$select=id,isDraft", headers=headers, timeout=wait)
            if chk.status_code == 200 and chk.json().get("isDraft"):
                g._session.delete(f"{base}/messages/{quote(old)}", headers=headers, timeout=wait)

        created, why_not = None, ""
        mid, why_not = self._original_in_outlook(g, base, headers, d)
        if mid:
            r = g._session.post(f"{base}/messages/{quote(mid)}/createReplyAll", headers=headers, timeout=wait)
            if r.status_code == 201:
                reply = r.json()
                # Our text above Outlook's own quoted original.
                quoted = ((reply.get("body") or {}).get("content") or "")
                at = re.search(r"<body[^>]*>", quoted, re.I)
                patch: Dict[str, Any] = {"body": {"contentType": "HTML", "content": (quoted[:at.end()] + content + quoted[at.end():]) if at else content + quoted}}
                # Reply all: everyone Outlook addressed stays. People the draft names who are not
                # already on it are added; the mailbox itself is never added.
                to_now, cc_now = list(reply.get("toRecipients") or []), list(reply.get("ccRecipients") or [])
                on_it = {addr(x) for x in to_now + cc_now} | {str(g.mailbox).lower()}
                add_to = [person(t) for t in d.get("to") or [] if t.get("email") and t["email"].lower() not in on_it]
                on_it |= {addr(x) for x in add_to}
                add_cc = [person(t) for t in d.get("cc") or [] if t.get("email") and t["email"].lower() not in on_it]
                if add_to or not to_now:
                    patch["toRecipients"] = to_now + add_to
                if add_cc:
                    patch["ccRecipients"] = cc_now + add_cc
                r2 = g._session.patch(f"{base}/messages/{quote(reply['id'])}", json=patch, headers=headers, timeout=wait)
                if r2.status_code == 200:
                    created = r2.json()
                else:
                    # Do not leave an empty reply behind.
                    g._session.delete(f"{base}/messages/{quote(reply['id'])}", headers=headers, timeout=wait)
                    why_not = f"Outlook created the reply but would not take the text (HTTP {r2.status_code})"
                    logger.warning("outlook reply draft: patch failed HTTP %s: %s", r2.status_code, r2.text[:200])
            else:
                why_not = f"Outlook would not create a reply to the original email (HTTP {r.status_code})"
                logger.info("outlook reply draft: createReplyAll HTTP %s — falling back to a new draft", r.status_code)
        if created is None:
            saved = ob.save_draft(to=[(t.get("name") or "", t["email"]) for t in d.get("to") or [] if t.get("email")],
                                  cc=[(t.get("name") or "", t["email"]) for t in d.get("cc") or [] if t.get("email")],
                                  subject=d.get("subject") or "", html=content)
            info: Dict[str, Any] = {"message_id": saved.get("graph_message_id"), "web_link": saved.get("web_link"), "as_reply": False, "why_not": why_not}
        else:
            info = {"message_id": created.get("id"), "web_link": created.get("webLink"), "as_reply": True, "why_not": "",
                    "conversation_id": created.get("conversationId"), "subject": created.get("subject"),
                    "to": [addr(x) for x in created.get("toRecipients") or []], "cc": [addr(x) for x in created.get("ccRecipients") or []]}
        info.update({"saved_at": now(), "mailbox": g.mailbox})
        self.store.update_draft(draft_id, {"outlook": info})
        return info
