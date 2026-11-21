"""What the model is shown — records rendered as text, and the property context block.

Nothing here calls a model. Every record carries a ``[sha=…]`` tag (12 hex
characters) so a statement can be traced to the record it came from, and any
record that had to be cut says so and says how to open the rest.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.storage.mongo import Mongo

from . import cleanmail
from . import config as bc
from . import prompts
from .store import BrainStore, active_property_ids, name_of

EMAIL_FIELDS = {"sha256": 1, "subject": 1, "date": 1, "participants": 1, "body_clean": 1, "body_quoted": 1, "thread_key": 1,
                "in_reply_to": 1, "internet_message_id": 1, "attachment_names": 1, "attachment_count": 1, "property_ids": 1,
                "placement": 1, "source_type": 1, "provider": 1, "created_at": 1, "placed_at": 1, "person_ids": 1}
DOC_FIELDS = {"sha256": 1, "filename": 1, "subject": 1, "date": 1, "text": 1, "doc_class": 1, "source_type": 1, "source_types": 1,
              "property_ids": 1, "chunk_sections": 1, "meeting": 1, "created_at": 1, "placed_at": 1, "kind": 1, "placement": 1,
              "parent_email_shas": 1, "uploads": 1}


def day(d: Any, fmt: str = "%Y-%m-%d") -> str:
    try:
        return d.strftime(fmt) if d else "undated"
    except Exception:
        return str(d)[:10]


def clip(text: str, limit: int, *, sha: str = "", what: str = "text") -> str:
    """Cut to a budget, and say so. A reader must never mistake a part for the whole."""
    text = text or ""
    if len(text) <= limit:
        return text
    tail = f"open_document('{sha[:12]}')" if sha else "the full record"
    return text[:limit] + f"\n[… cut here: {len(text) - limit:,} more characters of this {what} are not shown. Use {tail} to read the rest.]"


_PHONE_LINE = re.compile(r"^(?:(?:cell|mobile|phone|office|tel|direct|fax|m|o|p|t|c)\s*[:.]?\s*)?[\d\s()+\-.x]{7,}$", re.I)
_CONTACT_LINE = re.compile(r"^(?:[\w.+-]+@[\w-]+\.[\w.-]+|(?:https?://|www\.)\S+)$", re.I)
_SIGN_OFF = re.compile(r"^(?:thanks|thank you|many thanks|regards|best regards|kind regards|warm regards|best|sincerely|cheers|"
                       r"sent from my \w+(?: \w+)?|get outlook for \w+)[\s,.!\-]*$", re.I)


def says_nothing(art: dict, sender_names: Iterable[str] = ()) -> bool:
    """True when an email carries no words of its own: an empty body, or nothing
    but the sender's name, a phone number, an address and a sign-off.

    Such an email — a draw sheet or a PDF sent with only a signature under it —
    asks nothing, so no reply is drafted for it (admin directive 2026-11-14). The
    test is deliberately narrow: one word of the sender's own ("Final.") and the
    question of whether a reply is owed goes to the model as before."""
    known = set()
    for n in list(sender_names) + [str(a).split("@")[0] for a in ((art.get("participants") or {}).get("from") or [])]:
        known.update(w.lower() for w in re.findall(r"[A-Za-z]{2,}", str(n or "")))
    rest: List[str] = []
    for line in (art.get("body_clean") or "").splitlines():
        line = line.strip().strip("*_-–—|·•> ")
        if not line or _PHONE_LINE.match(line) or _CONTACT_LINE.match(line) or _SIGN_OFF.match(line):
            continue
        words = re.findall(r"[A-Za-z][A-Za-z.'-]*", line)
        if words and len(words) <= 4 and len(line) <= 60 and all(w[0].isupper() for w in words) and any(w.lower().strip(".") in known for w in words):
            continue                                    # the sender's own name
        rest.append(line)
    return sum(c.isalpha() for c in " ".join(rest)) < 3


class Records:
    """Read-only access to the corpus, shaped for prompts."""

    def __init__(self, mongo: Mongo):
        self.mongo = mongo
        self.art = mongo.artifacts
        self._people: Optional[Dict[str, dict]] = None
        self.signatures = cleanmail.Signatures(mongo.artifacts)

    # ------------------------------------------------------------- people
    def people_by_address(self) -> Dict[str, dict]:
        if self._people is None:
            out: Dict[str, dict] = {}
            for p in self.mongo.people.find({}, {"_id": 0, "person_id": 1, "display_name": 1, "addresses": 1, "send_as": 1, "role": 1, "org": 1, "side": 1}):
                for a in (p.get("addresses") or []) + (p.get("send_as") or []):
                    out[str(a).lower()] = p
            self._people = out
        return self._people

    def who(self, address: str, *, with_role: bool = False) -> str:
        a = str(address or "").lower()
        p = self.people_by_address().get(a)
        if not p:
            return a
        label = f"{p.get('display_name') or a} <{a}>"
        if with_role and (p.get("org") or p.get("role")):
            label += f" ({', '.join(x for x in (p.get('org'), str(p.get('role') or '')[:60]) if x)})"
        return label

    def name(self, address: str) -> str:
        p = self.people_by_address().get(str(address or "").lower())
        return (p or {}).get("display_name") or str(address or "")

    def rkb_addresses(self) -> List[str]:
        return [a for a, p in self.people_by_address().items() if p.get("side") in ("rkb", "internal")]

    # ---------------------------------------------------------- direction
    def directions(self, shas: Sequence[str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for o in self.mongo.occurrences.find({"artifact_sha": {"$in": list(shas)}}, {"artifact_sha": 1, "direction": 1}):
            # Sent wins: a message that sits in Sent Items was sent by us, even if a copy also reached an inbox.
            if o.get("direction") == "sent" or o["artifact_sha"] not in out:
                out[o["artifact_sha"]] = o.get("direction") or "received"
        return out

    def direction(self, sha: str) -> str:
        return self.directions([sha]).get(sha, "received")

    def provider_message_id(self, sha: str, mailbox_like: str = "") -> Optional[str]:
        o = self.mongo.occurrences.find_one({"artifact_sha": sha, "provider": "outlook", "provider_id": {"$ne": None}}, {"provider_id": 1})
        return (o or {}).get("provider_id")

    # ------------------------------------------------------------- emails
    def attachments_of(self, email_sha: str) -> List[dict]:
        return list(self.art.find({"parent_email_shas": email_sha, "is_inline_image": {"$ne": True}},
                                  {**DOC_FIELDS, "raw_size": 1}).limit(25))

    def email_header(self, a: dict, direction: str) -> str:
        p = a.get("participants") or {}
        frm = ", ".join(self.who(x, with_role=True) for x in (p.get("from") or [])[:2])
        to = ", ".join(self.who(x) for x in (p.get("to") or [])[:8])
        cc = ", ".join(self.who(x) for x in (p.get("cc") or [])[:8])
        kind = "EMAIL SENT BY RKB" if direction == "sent" else "EMAIL RECEIVED"
        line = f"[sha={a['sha256'][:12]}] {kind} {day(a.get('date'), '%Y-%m-%d %H:%M')} UTC | from: {frm} | to: {to}"
        if cc:
            line += f" | cc: {cc}"
        line += f" | subject: {a.get('subject') or '(no subject)'}"
        if a.get("attachment_names"):
            line += f" | attachments: {', '.join(str(n) for n in a['attachment_names'][:10])}"
        return line

    def render_email(self, a: dict, *, direction: str, body_chars: int = bc.EMAIL_BODY_CHARS, with_attachments: bool = False,
                     with_quoted: bool = False, att_budget: int = bc.ATTACHMENTS_TOTAL_CHARS) -> str:
        parts = [self.email_header(a, direction), clip((a.get("body_clean") or "").strip() or "(empty body)", body_chars, sha=a["sha256"], what="email")]
        if with_quoted and (a.get("body_quoted") or "").strip():
            parts.append("--- earlier messages quoted inside this email ---\n" + clip(a["body_quoted"].strip(), body_chars, sha=a["sha256"], what="quoted text"))
        if with_attachments:
            budget = att_budget
            for att in self.attachments_of(a["sha256"]):
                text = (att.get("text") or "").strip()
                name = att.get("filename") or att["sha256"][:12]
                head = f"--- attachment [sha={att['sha256'][:12]}] {name} ---"
                if not text:
                    parts.append(head + "\n(no readable text was extracted from this file)")
                    continue
                take = min(bc.ATTACHMENT_CHARS, max(0, budget))
                if take <= 0:
                    parts.append(head + f"\n[not shown: the attachment budget for this email is used up. Use open_document('{att['sha256'][:12]}') to read it.]")
                    continue
                parts.append(head + "\n" + clip(text, take, sha=att["sha256"], what="attachment"))
                budget -= min(len(text), take)
        return "\n".join(parts)

    def thread_before(self, a: dict, *, limit: int = bc.THREAD_MESSAGES) -> List[dict]:
        if not a.get("thread_key"):
            return []
        q = {"thread_key": a["thread_key"], "source_type": "email", "sha256": {"$ne": a["sha256"]}}
        if a.get("date"):
            q["date"] = {"$lte": a["date"]}
        rows = list(self.art.find(q, EMAIL_FIELDS).sort("date", -1).limit(limit))
        return rows[::-1]

    def thread_after(self, a: dict) -> List[dict]:
        if not a.get("thread_key") or not a.get("date"):
            return []
        return list(self.art.find({"thread_key": a["thread_key"], "source_type": "email", "sha256": {"$ne": a["sha256"]},
                                   "date": {"$gt": a["date"]}}, EMAIL_FIELDS).sort("date", 1).limit(20))

    def render_thread(self, a: dict, *, direction: Optional[str] = None, earlier_chars: int = bc.EMAIL_BODY_CHARS) -> str:
        """The email with what came before it in the same conversation, oldest first."""
        before = self.thread_before(a)
        dirs = self.directions([m["sha256"] for m in before] + [a["sha256"]])
        out: List[str] = []
        if before:
            out.append(f"EARLIER IN THIS CONVERSATION ({len(before)} message(s), oldest first):")
            out += [self.render_email(m, direction=dirs.get(m["sha256"], "received"), body_chars=earlier_chars) for m in before]
            out.append("")
        out.append("THE NEW MESSAGE:")
        out.append(self.render_email(a, direction=direction or dirs.get(a["sha256"], "received"), with_attachments=True,
                                     with_quoted=not before))
        return "\n\n".join(out)

    def recent_mail(self, pid: str, *, days: int, limit: int = 60, from_contains: str = "", direction: str = "",
                    since: Optional[datetime] = None) -> List[dict]:
        q: Dict[str, Any] = {"source_type": "email", "date": {"$gte": since or (datetime.now(timezone.utc) - timedelta(days=days))}}
        if pid == bc.GENERAL_ID:
            # Everything that belongs to no ACTIVE property: unplaced, portfolio, other business, sold deals.
            q["property_ids"] = {"$nin": active_property_ids()}
        else:
            q["property_ids"] = pid
        if from_contains:
            q["$or"] = [{"participants.from": {"$regex": re.escape(from_contains), "$options": "i"}},
                        {"person_ids": {"$regex": re.escape(from_contains), "$options": "i"}}]
            names = [a for a, p in self.people_by_address().items() if from_contains.lower() in str(p.get("display_name") or "").lower()]
            if names:
                q["$or"].append({"participants.from": {"$in": names}})
        rows = list(self.art.find(q, EMAIL_FIELDS).sort("date", -1).limit(limit * 2 if direction else limit))
        if direction:
            dirs = self.directions([r["sha256"] for r in rows])
            rows = [r for r in rows if dirs.get(r["sha256"], "received") == direction][:limit]
        return rows

    def sent_examples(self, to_addresses: Iterable[str], *, sender_addresses: Sequence[str], limit: int = 5, chars: int = 1400) -> List[str]:
        """Emails this sender actually wrote — to the same people first, then
        anyone — so a draft sounds like him, not like a template."""
        to = [a.lower() for a in to_addresses if a]
        base = {"source_type": "email", "participants.from": {"$in": [s.lower() for s in sender_addresses]},
                "body_clean": {"$regex": r"\S{3}"}}
        rows: List[dict] = []
        if to:
            rows = list(self.art.find({**base, "participants.to": {"$in": to}}, EMAIL_FIELDS).sort("date", -1).limit(limit * 3))
        if len(rows) < limit:
            seen = {r["sha256"] for r in rows}
            rows += [r for r in self.art.find(base, EMAIL_FIELDS).sort("date", -1).limit(limit * 4) if r["sha256"] not in seen]
        out, used = [], 0
        for r in rows:
            body = (r.get("body_clean") or "").strip()
            # Skip one-liners and system-sent mail: they teach nothing about voice.
            if len(body) < 120 or "[RKB]" in (r.get("subject") or ""):
                continue
            out.append(f"(to {', '.join(self.name(x) for x in ((r.get('participants') or {}).get('to') or [])[:3])}, {day(r.get('date'))}, "
                       f"subject: {r.get('subject')})\n{body[:chars]}")
            used += 1
            if used >= limit:
                break
        return out

    # ---------------------------------------------------------- documents
    def render_document(self, a: dict, *, pid: Optional[str] = None, chars: int = 40_000) -> str:
        """An upload, a disk file or a call transcript. A transcript that walks
        through several properties is cut to the part about this one."""
        name = a.get("filename") or a.get("subject") or a["sha256"][:12]
        text = a.get("text") or ""
        meeting = a.get("meeting") or {}
        is_call = a.get("doc_class") == "meeting_transcript" or bool(meeting)
        label = "CALL NOTES / TRANSCRIPT" if is_call else "DOCUMENT ADDED"
        head = f"[sha={a['sha256'][:12]}] {label} {day(a.get('date') or a.get('created_at'))} | {meeting.get('title') or name}"
        if is_call and meeting.get("participants"):
            head += f" | participants: {', '.join(str(p) for p in meeting['participants'][:10])}"
        sections = a.get("chunk_sections") or []
        if is_call and pid and sections and pid != bc.GENERAL_ID:
            mine = [s for s in sections if s.get("property_id") == pid]
            general = [s for s in sections if not s.get("property_id")]
            if mine:
                body = "\n\n".join(f"[{s.get('heading')}]\n{text[s['start']:s['end']].strip()}" for s in mine)
                pre = "\n\n".join(text[s["start"]:s["end"]].strip() for s in general)
                text = (f"(opening and general part of the call)\n{clip(pre, 6000, sha=a['sha256'], what='call')}\n\n" if pre.strip() else "") + \
                       f"(the part of the call about this property)\n{body}"
        return head + "\n" + clip(text.strip() or "(no readable text was extracted)", chars, sha=a["sha256"], what="document")


    # ------------------------------------------------------ the mail itself
    def _window_start(self, days: int, *, at: Optional[datetime] = None) -> datetime:
        """The window starts on a day boundary, so the text is identical from one
        call to the next until something new arrives — which is what lets it cache."""
        return ((at or datetime.now(timezone.utc)) - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)

    def window_files(self, pid: str, *, days: int = bc.MAIL_DAYS) -> Dict[str, Any]:
        """Every file of the window: attached to an email, uploaded, or call notes.
        One entry per file (the same workbook sent three times is one file)."""
        start = self._window_start(days)
        rows = self.recent_mail(pid, days=days, limit=600, since=start)
        by_sha = {r["sha256"]: r for r in rows}
        files: Dict[str, dict] = {}
        carried: Dict[str, List[str]] = {}
        if by_sha:
            for a in self.art.find({"parent_email_shas": {"$in": list(by_sha)}, "is_inline_image": {"$ne": True}}, {**DOC_FIELDS, "raw_size": 1}):
                files[a["sha256"]] = a
                for p in a.get("parent_email_shas") or []:
                    if p in by_sha:
                        carried.setdefault(a["sha256"], []).append(p)
        q: Dict[str, Any] = {"source_type": {"$in": ["upload", "disk_file"]}, "created_at": {"$gte": start}, "is_inline_image": {"$ne": True}}
        q["property_ids"] = {"$nin": active_property_ids()} if pid == bc.GENERAL_ID else pid
        for a in self.art.find(q, DOC_FIELDS).limit(60):
            files.setdefault(a["sha256"], a)
        return {"emails": rows, "files": files, "carried": carried, "by_sha": by_sha, "start": start}

    def record_block(self, pid: str, *, days: int = bc.MAIL_DAYS) -> str:
        """The record of the last ``days`` days, word for word: every email, then
        every file. A summary cannot be quoted from and can be wrong; the record
        cannot. Emails and files are separate things — an attachment is a file in
        its own right, shown once, in full, under THE FILES."""
        w = self.window_files(pid, days=days)
        rows, files, carried, by_sha, start = w["emails"][::-1], w["files"], w["carried"], w["by_sha"], w["start"]
        dirs = self.directions([r["sha256"] for r in rows])

        # ---- files: oldest first; if space runs out the oldest give way and are named
        def when(a: dict):
            ds = [by_sha[p].get("date") for p in carried.get(a["sha256"], []) if by_sha[p].get("date")]
            return min(ds) if ds else (a.get("date") or a.get("created_at") or start)
        ordered = sorted(files.values(), key=lambda a: (when(a), a.get("filename") or ""))
        rendered: List[tuple] = []
        for a in ordered:
            name = a.get("filename") or a.get("subject") or a["sha256"][:12]
            srcs = carried.get(a["sha256"], [])
            if srcs:
                first = min((by_sha[p] for p in srcs), key=lambda r: r.get("date") or start)
                frm = ((first.get("participants") or {}).get("from") or [""])[0]
                origin = f"attached to the email of {day(first.get('date'), '%Y-%m-%d %H:%M')} from {self.name(frm)} (“{first.get('subject') or ''}”)"
                if len(srcs) > 1:
                    origin += f" and {len(srcs) - 1} other email(s)"
            else:
                origin = f"added to the property file on {day(a.get('created_at'))}"
            is_call = a.get("doc_class") == "meeting_transcript" or bool(a.get("meeting"))
            if is_call:
                body = self.render_document(a, pid=pid, chars=bc.ATTACHMENT_CHARS).split("\n", 1)[-1]
            else:
                text = (a.get("text") or "").strip()
                body = clip(text, bc.ATTACHMENT_CHARS, sha=a["sha256"], what="file") if text else \
                    "(NO READABLE TEXT could be extracted from this file. Its contents are unknown to you: say so if it matters, and do not describe it.)"
            rendered.append((a, f"[sha={a['sha256'][:12]}] FILE: {name} — {'call notes, ' if is_call else ''}{origin}\n{body}"))
        shown = list(rendered)
        left_out: List[dict] = []
        while shown and sum(len(t) for _, t in shown) > bc.FILES_BLOCK_CHARS:
            left_out.append(shown.pop(0)[0])
        shown_shas = {a["sha256"] for a, _ in shown}

        # ---- emails: oldest first, each naming its files
        parts: List[str] = []
        att_by_mail: Dict[str, List[dict]] = {}
        for sha, srcs in carried.items():
            for p in srcs:
                att_by_mail.setdefault(p, []).append(files[sha])
        for r in rows:
            text = self.render_email(r, direction=dirs.get(r["sha256"], "received"))
            mine = sorted(att_by_mail.get(r["sha256"]) or [], key=lambda a: a.get("filename") or "")
            if mine:
                text += "\n(files attached: " + "; ".join(
                    f"{a.get('filename') or 'file'} [sha={a['sha256'][:12]}]" + ("" if a["sha256"] in shown_shas else " — NOT shown below, open it")
                    for a in mine[:15]) + (". Each is given in full under THE FILES.)" if all(a["sha256"] in shown_shas for a in mine) else ")")
            parts.append(text)
        dropped = 0
        while parts and sum(len(p) for p in parts) > bc.MAIL_BLOCK_CHARS:
            parts.pop(0)
            dropped += 1

        out = [f"=== THE MAIL — every email for {name_of(pid)} since {day(start)}, in full, oldest first ==="]
        if not parts:
            out.append("(no email in this period)")
        else:
            out.append(f"({len(parts)} emails. Anything older is on file and is found by the document search.)"
                       + (f"\n({dropped} older email(s) of this period are not shown for space; search for them.)" if dropped else ""))
            out.append("\n\n".join(parts))
        out.append(f"\n=== THE FILES — every file attached or added for {name_of(pid)} since {day(start)}, each once, in full, oldest first ===")
        if not shown and not left_out:
            out.append("(no files in this period)")
        else:
            out.append(f"({len(shown)} file(s). A file is evidence in its own right: quote it, cite it by name, and do not describe a file you have not read here or opened.)")
            if left_out:
                out.append("(Not shown for space — open before relying on them: "
                           + "; ".join(f"{a.get('filename') or 'file'} [sha={a['sha256'][:12]}]" for a in left_out) + ")")
            out.append("\n\n".join(t for _, t in shown))
        site = self.site_text(pid)
        if site:
            out.append(site)
        return "\n".join(out)

    def site_text(self, pid: str) -> str:
        """The contractor's own site diary and bills, and the reading of its photos,
        where the property has a Buildertrend job. Empty otherwise."""
        if pid == bc.GENERAL_ID:
            return ""
        out: List[str] = []
        try:
            from .buildertrend import Buildertrend
            site = Buildertrend(self.mongo).site_block(pid)
            if site:
                out.append(site)
                from .sitework import SiteWork
                reading = SiteWork(self.mongo).block(pid)
                if reading:
                    out.append(reading)
        except Exception:
            from mangotree.core.logging import logger
            logger.exception("site block failed for %s", pid)
        return "\n".join(out)

    # ------------------------------------------- what a new record is read against
    # (2026-11-14) A newly arrived record used to be read with every email AND every
    # attachment of 14 days in full — 290,000 tokens to log a one-line email on the
    # busiest file, two thirds of it attachments that had nothing to do with it, and
    # still nothing older than 14 days. Now it is read with the emails in full, a
    # one-line list of EVERY document on file, and the means to open or search for
    # anything else. The parts are separate so that the ones that did not change
    # since the last record are not paid for again.

    # --------------------------------------------------------- conversations
    # (2026-11-14) The mail is given as conversations, not as a pile sorted by
    # date. Each conversation is one unbroken chain, oldest message first, each
    # message once: its own words, without the signature. What an email quotes
    # underneath is left out only when that very message is in the chain as an
    # email of its own; a quote that is not held separately is kept word for
    # word, so the chain is never broken. (Measured before this: 58% of the email
    # text in front of the model was older messages quoted again.)

    def _threads(self, keys: Sequence[str], *, as_of: Optional[datetime] = None, per_thread: int = bc.THREAD_MESSAGES) -> Dict[str, List[dict]]:
        """Every email on file of these conversations, of any age, oldest first."""
        out: Dict[str, List[dict]] = {}
        keys = [k for k in dict.fromkeys(keys) if k]
        if not keys:
            return out
        q: Dict[str, Any] = {"source_type": "email", "thread_key": {"$in": keys}}
        if as_of:
            q["date"] = {"$lte": as_of}
        for r in self.art.find(q, EMAIL_FIELDS).sort("date", 1):
            out.setdefault(r["thread_key"], []).append(r)
        return {k: v[-per_thread:] for k, v in out.items()}

    def _file_names(self, shas: Sequence[str]) -> Dict[str, List[dict]]:
        names: Dict[str, List[dict]] = {}
        if shas:
            for a in self.art.find({"parent_email_shas": {"$in": list(shas)}, "is_inline_image": {"$ne": True}},
                                   {"sha256": 1, "filename": 1, "parent_email_shas": 1}):
                for p in a.get("parent_email_shas") or []:
                    names.setdefault(p, []).append(a)
        return names

    def cleaned(self, a: dict, earlier: Sequence[dict]) -> Dict[str, Any]:
        """One email as it is shown in its conversation (see ``cleanmail``)."""
        frm = ((a.get("participants") or {}).get("from") or [""])[0]
        held = [(e.get("body_clean") or "") + "\n" + (e.get("body_quoted") or "") for e in earlier]
        return cleanmail.clean(a.get("body_clean") or "", a.get("body_quoted") or "", held,
                               learned=self.signatures.lines(frm), names=[self.name(frm), str(frm).split("@")[0]],
                               signature_of=self.signatures.lines, drop_repeats=bc.MAIL_DROP_REPEATS)

    def render_message(self, a: dict, *, direction: str, earlier: Sequence[dict], files: Sequence[dict] = (),
                       with_attachments: bool = False) -> str:
        c = self.cleaned(a, earlier)
        body = self._replying_to(a, earlier) + (c["own"] or "(no words of their own in this email — only what is attached or quoted)")
        label = {"sent": "--- under this email, as it was sent (the chain it carries) ---",
                 "inside": "--- quoted in this email, WITH WORDS WRITTEN INSIDE IT (answers typed between its lines); given here whole ---",
                 "not_held": "--- quoted in this email; not held as an email of its own, so given here ---"}
        last = None
        for k, why in zip(c["kept"], c["why"]):
            body += ("\n\n" + label[why] + "\n" if why != last else "\n\n") + k      # one heading for a chain, not one per message in it
            last = why
        if with_attachments:
            # The new record: its attachments follow in full, exactly as before.
            return self.render_email({**a, "body_clean": body}, direction=direction, with_attachments=True)
        text = self.email_header(a, direction) + "\n" + clip(body, bc.EMAIL_BODY_CHARS, sha=a["sha256"], what="email")
        mine = sorted(files or [], key=lambda f: f.get("filename") or "")
        if mine:
            text += "\n(files attached, not shown here — open one to read it: " + "; ".join(
                f"{f.get('filename') or 'file'} [sha={f['sha256'][:12]}]" for f in mine[:15]) + ")"
        return text

    def _replying_to(self, a: dict, earlier: Sequence[dict]) -> str:
        """One line saying which message this one answers, from the mail's own
        reply marker — so the order of a conversation is stated, not left to be
        worked out. Empty for a message that answers nothing."""
        ids = [str(x) for x in (a.get("in_reply_to") or []) if x]
        if not ids:
            return "(starts the conversation)\n" if not earlier else ""
        by_id = {m.get("internet_message_id"): m for m in earlier if m.get("internet_message_id")}
        for x in ids:
            m = by_id.get(x)
            if m:
                frm = ((m.get("participants") or {}).get("from") or [""])[0]
                return f"(replying to the message of {day(m.get('date'), '%Y-%m-%d %H:%M')} UTC from {self.name(frm)} [sha={m['sha256'][:12]}])\n"
        return "(replying to a message that is not on file)\n"

    @staticmethod
    def _subject(a: dict) -> str:
        return re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", a.get("subject") or "", flags=re.I).strip() or "(no subject)"

    def render_conversation(self, msgs: Sequence[dict], *, dirs: Dict[str, str], names: Dict[str, List[dict]]) -> str:
        head = (f"--- CONVERSATION: “{self._subject(msgs[0])}” — {len(msgs)} message(s), "
                f"{day(msgs[0].get('date'), '%Y-%m-%d %H:%M')} to {day(msgs[-1].get('date'), '%Y-%m-%d %H:%M')} UTC, oldest first ---")
        return head + "\n" + "\n\n".join(self.render_message(m, direction=dirs.get(m["sha256"], "received"), earlier=msgs[:i],
                                                              files=names.get(m["sha256"]) or []) for i, m in enumerate(msgs))

    def _listed(self, msgs: Sequence[dict]) -> str:
        last = msgs[-1]
        people: List[str] = []
        for m in msgs:
            for a in ((m.get("participants") or {}).get("from") or [])[:1]:
                n = self.name(a)
                if n not in people:
                    people.append(n)
        words = " ".join(str(self.cleaned(last, msgs[:-1])["own"]).split())
        return (f"· [sha={last['sha256'][:12]}] “{self._subject(msgs[0])}” — {len(msgs)} message(s), {day(msgs[0].get('date'))} to {day(last.get('date'))}"
                f" — written by {', '.join(people[:5])} — latest, from {self.name(((last.get('participants') or {}).get('from') or [''])[0])}: "
                f"“{words[:180]}{'…' if len(words) > 180 else ''}”")

    def mail_view(self, pid: str, *, new: Optional[dict] = None, days: int = bc.MAIL_DAYS, as_of: Optional[datetime] = None) -> Dict[str, Any]:
        """The file's mail of the window as conversations, for reading ``new`` against.

        A quiet file: every conversation in full. A busy one (more than
        ``MAIL_FULL_CHARS`` once cleaned): in full are the conversations active in
        the last two days and those connected to the new record — the same
        subject, or the same outside person — newest first while there is room;
        every other conversation is listed in one line and can be opened.

        ``new``'s own conversation is not here: it is given with the record itself.
        Returns ``stable`` (conversations with nothing new today, in full — the
        same text until tomorrow), ``live`` (the rest), and counts."""
        at = as_of or datetime.now(timezone.utc)
        start = self._window_start(days, at=at)
        rows = [r for r in self.recent_mail(pid, days=days, limit=600, since=start) if not as_of or (r.get("date") and r["date"] <= as_of)]
        new_sha = (new or {}).get("sha256")
        own_key = (new or {}).get("thread_key")
        rows = [r for r in rows if r["sha256"] != new_sha]
        in_window = {r["sha256"] for r in rows}
        keys = [r.get("thread_key") for r in rows if r.get("thread_key") and r.get("thread_key") != own_key]
        threads = self._threads(keys, as_of=as_of)
        for r in rows:                                   # an email with no conversation key stands alone
            if not r.get("thread_key"):
                threads["sha:" + r["sha256"]] = [r]
        convs = [[m for m in msgs if m["sha256"] != new_sha] for msgs in threads.values()]
        convs = sorted([c for c in convs if c], key=lambda c: (str(c[-1].get("date") or ""), c[-1]["sha256"]))
        shas = [m["sha256"] for c in convs for m in c]
        dirs, names = self.directions(shas), self._file_names(shas)
        text = {id(c): self.render_conversation(c, dirs=dirs, names=names) for c in convs}
        today = at.replace(hour=0, minute=0, second=0, microsecond=0)
        total = sum(len(t) for t in text.values())
        head = (f"=== THE MAIL — the conversations of {name_of(pid)} with an email since {day(start)}, each as one chain, oldest message first ===\n"
                f"({prompts.MAIL_AS} The text of an attachment is not shown: each is named with its "
                "tag, stands on THE DOCUMENTS ON FILE, and can be opened.)")
        info = {"emails": len(in_window), "conversations": len(convs), "listed": 0, "chars": total,
                "last_day": sum(1 for r in rows if r.get("date") and r["date"] >= at - timedelta(hours=24))}
        if total <= bc.MAIL_FULL_CHARS:
            quiet = [text[id(c)] for c in convs if c[-1].get("date") and c[-1]["date"] < today]
            live = [text[id(c)] for c in convs if not (c[-1].get("date") and c[-1]["date"] < today)]
            return {**info, "full": len(convs),
                    "stable": head + "\n\n" + ("\n\n".join(quiet) if quiet else "(no conversation that ended before today)"),
                    "live": f"=== THE MAIL, CONVERSATIONS WITH AN EMAIL TODAY ({day(today)}) — continues the list above ===\n\n"
                            + ("\n\n".join(live) if live else "(none besides the new record's own)")}
        # A busy file: what is connected to the new record in full, the rest by one line each.
        rkb = {a.lower() for a in self.rkb_addresses()}
        p = (new or {}).get("participants") or {}
        who = {a.lower() for a in (p.get("from") or [])} - rkb
        if not who:                                      # RKB wrote it: the people it was written to
            who = {a.lower() for a in (p.get("to") or [])[:4]} - rkb
        subject = self._subject(new or {}).lower() if new else ""
        recent_from = at - timedelta(hours=48)

        def connected(c: Sequence[dict]) -> bool:
            if subject and subject != "(no subject)" and any(self._subject(m).lower() == subject for m in c):
                return True
            return bool(who) and any(who & {a.lower() for k in ("from", "to", "cc") for a in ((m.get("participants") or {}).get(k) or [])} for m in c)

        chosen: List[Sequence[dict]] = [c for c in convs if c[-1].get("date") and c[-1]["date"] >= recent_from]
        room = bc.MAIL_CONNECTED_CHARS
        picked = {id(c) for c in chosen}
        for c in reversed(convs):                        # newest first
            if id(c) in picked or not connected(c):
                continue
            if len(text[id(c)]) > room:
                continue
            room -= len(text[id(c)])
            chosen.append(c)
            picked.add(id(c))
        chosen.sort(key=lambda c: (str(c[-1].get("date") or ""), c[-1]["sha256"]))
        rest = [c for c in convs if id(c) not in picked]
        live = head + "\n(This is a busy file. Shown in full: the conversations with an email in the last two days, and those connected to the new record "
        live += "by subject or by the outside person. Every other conversation of the period is listed after them in one line.)\n\n"
        live += "\n\n".join(text[id(c)] for c in chosen) if chosen else "(no conversation of the last two days and none connected to the new record)"
        if rest:
            live += ("\n\n=== OTHER CONVERSATIONS OF THE PERIOD — one line each, NOT shown in full ===\n"
                     "(Open one with email_thread('<its sha tag>') when the new record touches it. Do not state what one says from its line.)\n"
                     + "\n".join(self._listed(c) for c in reversed(rest)))
        return {**info, "full": len(chosen), "listed": len(rest), "stable": "", "live": live}

    def render_record(self, a: dict, *, direction: Optional[str] = None) -> str:
        """The new email: the conversation it belongs to so far, as one chain, then
        the new message itself with its own attachments in full."""
        before = self.thread_before(a)
        dirs = self.directions([m["sha256"] for m in before] + [a["sha256"]])
        names = self._file_names([m["sha256"] for m in before])
        out: List[str] = []
        if before:
            out.append(f"THIS CONVERSATION SO FAR ({len(before)} message(s), oldest first):")
            out += [self.render_message(m, direction=dirs.get(m["sha256"], "received"), earlier=before[:i], files=names.get(m["sha256"]) or [])
                    for i, m in enumerate(before)]
        else:
            out.append("THIS IS THE FIRST MESSAGE OF ITS CONVERSATION ON FILE.")
        out.append("THE NEW MESSAGE:")
        out.append(self.render_message(a, direction=direction or dirs.get(a["sha256"], "received"), earlier=before, with_attachments=True))
        return "\n\n".join(out)

    _MEDIA = (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".webp", ".tif", ".tiff", ".mp4", ".mov", ".avi", ".zip")

    def documents_block(self, pid: str, *, as_of: Optional[datetime] = None) -> str:
        """Every document held for the file — attached to an email, uploaded, or
        call notes, of any age — one line each. The line says what the document
        is, never what it says; the model opens the ones it needs by their tag."""
        at = as_of or datetime.now(timezone.utc)
        q: Dict[str, Any] = {"source_type": {"$ne": "email"}, "is_inline_image": {"$ne": True}}
        q["property_ids"] = {"$nin": active_property_ids()} if pid == bc.GENERAL_ID else pid
        if as_of:
            q["created_at"] = {"$lte": as_of}
        rows = list(self.art.find(q, {"sha256": 1, "filename": 1, "subject": 1, "date": 1, "created_at": 1, "parent_email_shas": 1,
                                      "doc_class": 1, "meeting.title": 1, "extraction.char_count": 1}))
        when = lambda a: a.get("date") or a.get("created_at")  # noqa: E731
        listed, media = [], 0
        for a in rows:
            name = str(a.get("filename") or "").lower()
            chars = int((a.get("extraction") or {}).get("char_count") or 0)
            if name.endswith(self._MEDIA) and chars < 200:
                media += 1                       # a logo, a photo with no words, an archive: nothing to read
                continue
            listed.append(a)
        listed.sort(key=lambda a: (str(when(a) or ""), a.get("filename") or "", a["sha256"]))
        held_back = max(0, len(listed) - bc.DOC_LIST_MAX)
        listed = listed[held_back:]
        full_from = at - timedelta(days=bc.DOC_LIST_FULL_DAYS)
        recent = [a for a in listed if when(a) and when(a) >= full_from]
        first_page: Dict[str, str] = {}
        parents: Dict[str, dict] = {}
        if recent:
            for c in self.mongo.chunks.find({"artifact_sha": {"$in": [a["sha256"] for a in recent]}, "ordinal": 0}, {"artifact_sha": 1, "tier1": 1}):
                if c.get("tier1"):
                    first_page[c["artifact_sha"]] = " ".join(str(c["tier1"]).split())
            want = list({p for a in recent for p in (a.get("parent_email_shas") or [])[:1]})
            if want:
                parents = {e["sha256"]: e for e in self.art.find({"sha256": {"$in": want}}, {"sha256": 1, "subject": 1, "participants.from": 1})}
        recent_ids = {a["sha256"] for a in recent}
        lines: List[str] = []
        for a in listed:
            name = a.get("filename") or (a.get("meeting") or {}).get("title") or a.get("subject") or a["sha256"][:12]
            line = f"[sha={a['sha256'][:12]}] {day(when(a))} · {name}"
            if a.get("doc_class"):
                line += f" · {str(a['doc_class']).replace('_', ' ')}"
            if a["sha256"] in recent_ids:
                chars = int((a.get("extraction") or {}).get("char_count") or 0)
                if chars:
                    line += f" · {max(1, round(chars / 1000))}k characters"
                e = parents.get(((a.get("parent_email_shas") or [None])[0]) or "")
                if e:
                    frm = ((e.get("participants") or {}).get("from") or [""])[0]
                    line += f" · came from {self.name(frm)} with “{str(e.get('subject') or '')[:70]}”"
                gist = first_page.get(a["sha256"])
                if gist:
                    line += " — first page: " + (gist if len(gist) <= bc.DOC_LIST_LINE_CHARS else gist[:bc.DOC_LIST_LINE_CHARS].rsplit(" ", 1)[0] + " …")
            lines.append(line)
        head = (f"=== THE DOCUMENTS ON FILE — every document held for {name_of(pid)}, of any age, one line each, oldest first ===\n"
                f"({len(lines)} document(s). A line says what a document IS — its name, when and with which email it came and, for the last "
                f"{bc.DOC_LIST_FULL_DAYS} days, a sentence about its first page. It does NOT say what the document says. To rely on a document, "
                "open it with open_document('<its sha tag>'). Never state a figure, a date, a name or a term from this list alone.)")
        if held_back:
            head += f"\n({held_back} older document(s) are not listed for space; find them with list_documents or the search.)"
        if media:
            head += f"\n({media} picture, video or archive file(s) with no readable text are left off the list.)"
        return head + "\n" + ("\n".join(lines) if lines else "(no documents on file)")

    def mail_block(self, pid: str, *, days: int = bc.MAIL_DAYS, budget: int = bc.MAIL_BLOCK_CHARS) -> str:
        """Kept for callers of the earlier name: the mail and the files of the window."""
        return self.record_block(pid, days=days)

    def freshness(self, pid: str) -> str:
        """How current the records are, said plainly. Anything stated as "has not
        happened" is only true as of these times, and the model is told to say so."""
        q: Dict[str, Any] = {"source_type": "email"}
        q["property_ids"] = {"$nin": active_property_ids()} if pid == bc.GENERAL_ID else pid
        last = self.art.find_one(q, {"date": 1}, sort=[("date", -1)]) if pid else self.art.find_one({"source_type": "email"}, {"date": 1}, sort=[("date", -1)])
        run = self.mongo.db["scheduled_runs"].find_one({"job": "intake", "ok": True}, {"at": 1}, sort=[("at", -1)])
        now_ = datetime.now(timezone.utc)
        lines = [f"NOW: {now_:%A %d %B %Y, %H:%M} UTC."]
        if run and run.get("at"):
            mins = int((now_ - run["at"]).total_seconds() // 60)
            lines.append(f"HOW CURRENT THE RECORDS ARE: the mailboxes were last read at {run['at']:%d %b %H:%M} UTC ({mins} minute(s) ago)."
                         + (" THAT IS OVER AN HOUR AGO — anything since is not in front of you; say so when it matters." if mins > 60 else ""))
        else:
            lines.append("HOW CURRENT THE RECORDS ARE: unknown — no record of the mailboxes being read. Say so when it matters.")
        if last and last.get("date"):
            lines.append(f"The newest email on file for {name_of(pid) if pid else 'any property'} is dated {last['date']:%d %b %Y %H:%M} UTC.")
        return " ".join(lines)


# =============================================================================
# The property context block
# =============================================================================

def standing_instructions(mongo: Mongo, pid: str) -> List[str]:
    q: Dict[str, Any] = {"active": {"$ne": False}, "status": {"$ne": "pending"}, "$or": [{"scope": "global"}]}
    if pid != bc.GENERAL_ID:
        q["$or"].append({"scope": "property", "property_id": pid})
    rows = list(mongo.db["remember_notes"].find(q, {"_id": 0, "text": 1, "author": 1, "created_at": 1, "decided_at": 1, "scope": 1}).sort("created_at", -1).limit(40))
    return [f"- ({r.get('author') or 'team'}, {day(r.get('decided_at') or r.get('created_at'))}"
            f"{', all properties' if r.get('scope') == 'global' else ''}) {r.get('text')}" for r in rows]


def money_lines(store: BrainStore, pid: str) -> List[str]:
    doc = store.loan_terms.find_one({"property_id": pid}, {"_id": 0})
    if not doc:
        return []
    out = []
    for src in doc.get("sources") or []:
        out.append(f"From “{src.get('file')}”{' — ' + src['as_of'] if src.get('as_of') else ''}:")
        for row in src.get("rows") or []:
            note = f"  ({row['note']})" if row.get("note") else ""
            out.append(f"  - {row.get('item')}: {row.get('value')}{note}")
    return out


def render_item(i: dict) -> str:
    due = f", due {day(i.get('due'))}" if i.get("due") else ""
    detail = f" — {i['detail']}" if i.get("detail") else ""
    return f"- [{i['id']}] ({i.get('owner')}, {i.get('kind')}{due}, open since {day(i.get('opened_at'))}) {i.get('title')}{detail}"


def render_activity(rows: Sequence[dict]) -> List[str]:
    out = []
    for r in rows:
        tag = f" [sha={str(r['sha'])[:12]}]" if r.get("sha") else ""
        who = f" — {r['who']}" if r.get("who") else ""
        out.append(f"- {day(r.get('at'))} {str(r.get('kind') or '').replace('_', ' ')}{who}: {r.get('title')}. {r.get('summary') or ''}{tag}".strip())
    return out


def property_block(mongo: Mongo, store: BrainStore, pid: str, *, file: Optional[dict] = None, activity_days: int = bc.ACTIVITY_DAYS,
                   with_drafts: bool = True) -> str:
    """Everything known about one property, as the model reads it. Stable between
    events (no clock in it), so it caches until something actually changes."""
    file = file if file is not None else (store.get_file(pid) or {})
    p = PROPERTY_INDEX.get(pid)
    title = f"{name_of(pid)}" + (f", {p.city}, {p.state} {p.postal}" if p else "")
    lines = [f"=== PROPERTY FILE — {title} (id: {pid}) — last updated {day(file.get('updated_at'), '%Y-%m-%d %H:%M')} UTC ==="]
    if p:
        if p.aliases:
            lines.append(f"Also called: {', '.join(list(p.aliases)[:6])}")
        if getattr(p, "notes", ""):
            lines.append(f"Registry note: {p.notes}")
    if not file:
        lines.append("(No file has been built for this property yet. Rely on the records; say what you could not establish.)")
    lines.append(f"\nWHERE IT STANDS: {file.get('headline') or '(not established)'}")
    if file.get("status"):
        lines.append(f"\nSTATUS\n{file['status']}")
    if file.get("facts"):
        lines.append("\nKEY FACTS (from the records; open the source before quoting a figure)")
        lines += [f"- {f.get('key')}: {f.get('value')}" for f in file["facts"]]
    items = BrainStore.open_items(file)
    lines.append("\nOPEN ITEMS (id, owner, kind)")
    lines += [render_item(i) for i in items] or ["- (none)"]
    closed = sorted([i for i in (file.get("open_items") or []) if i.get("status") != "open" and i.get("closed_at")],
                    key=lambda i: i["closed_at"], reverse=True)[:8]
    if closed:
        lines.append("\nRECENTLY CLOSED (do not raise again)")
        lines += [f"- {i.get('title')} — {i.get('status')} {day(i.get('closed_at'))}" + (f": {i['closed_note']}" if i.get("closed_note") else "") for i in closed]
    if file.get("watch"):
        lines.append("\nWATCH (risks and dates)")
        lines += [f"- {w}" for w in file["watch"]]
    money = money_lines(store, pid)
    if money:
        lines.append("\nMONEY — figures kept by RKB in its own workbooks (quote with the as-of date; anything newer must come from a record)")
        lines += money
    notes = standing_instructions(mongo, pid)
    if notes:
        lines.append("\nSTANDING INSTRUCTIONS FROM THE TEAM (Rakesh Sir's are final; they decide how to act, they do not change what a record says)")
        lines += notes
    acts = store.recent_activity(pid, days=activity_days, limit=60)
    lines.append(f"\nLAST {activity_days} DAYS (newest first)")
    lines += render_activity(acts) or ["- (nothing recorded in this period)"]
    if with_drafts:
        drafts = store.list_drafts(pid=pid)
        if drafts:
            lines.append("\nREPLIES DRAFTED AND WAITING TO BE SENT")
            lines += [f"- [{d['draft_id']}] to {', '.join(t.get('name') or t.get('email') or '' for t in d.get('to') or [])}: “{d.get('subject')}” (written {day(d.get('created_at'))})" for d in drafts]
    return "\n".join(lines)


def portfolio_block(store: BrainStore) -> str:
    """Every property in a few lines — what the general chat starts from."""
    lines = ["=== PORTFOLIO — every active property in brief ==="]
    for f in store.list_files():
        items = BrainStore.open_items(f)
        mine = [i for i in items if i.get("owner") == "rakesh"]
        lines.append(f"\n{f.get('name')} (id: {f['property_id']}) — updated {day(f.get('updated_at'))}")
        lines.append(f"  {f.get('headline') or '(no headline)'}")
        if items:
            lines.append(f"  open items: {len(items)} ({len(mine)} with Rakesh Sir)")
            lines += [f"    - ({i.get('owner')}) {i.get('title')}" for i in items[:6]]
    drafts = store.list_drafts()
    if drafts:
        lines.append(f"\nREPLIES DRAFTED AND WAITING: {len(drafts)}")
        lines += [f"- [{d['draft_id']}] {', '.join(d.get('property_ids') or [])}: “{d.get('subject')}”" for d in drafts[:20]]
    return "\n".join(lines)
