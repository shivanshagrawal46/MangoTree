"""Has this email already been answered?

The one mistake the firm cannot afford from this system is a reply drafted — or
worse, sent — to an email that Rakesh Sir or Manjunath Sir has already dealt
with. So before any reply is written, and again every time a waiting draft is
shown, the sent mail is checked.

Who counts as "us": anyone on the RKB side, by address. That matters because
only Rakesh Sir's mailboxes are read — a reply sent by Manjunath Sir or JP Sir
reaches the system as *received* mail (he was copied), not from a Sent folder.
A check that looked only at Sent Items would never see it.

What counts as a possible answer — any email from an RKB person, dated after the
incoming one, that is:
    * in the same conversation, or
    * a direct reply to it (its message id is in the reply headers), or
    * addressed or copied to the person who wrote it — under any subject.
The third is what catches a reply sent as a fresh email, or folded into a wider
message to the same person.

Whether those emails actually answer it is a judgement, so it is made by reading
them: Opus 5.5 gets the incoming email and every candidate in full and says
answered / partly answered / not answered, which email did it, and what is still
owed. No candidates means not answered, and no model is called.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .context import EMAIL_FIELDS, Records, day
from .llm import BrainError, cost_of, structured

BRIEF = """YOUR JOB NOW: HAS THIS EMAIL ALREADY BEEN ANSWERED?
An email came in to RKB. Below it are the emails that people at RKB (Rakesh Sir, Manjunath
Sir, JP Sir and the team) sent afterwards — in the same conversation, or to the same person
under any subject. Decide whether RKB has already answered the incoming email.

* answered — between them, RKB's later emails deal with everything the incoming email asked
  or needed: each question answered, each request granted, refused or expressly set aside.
  It does not matter whether the answer came in the same conversation or under another
  subject, or from Rakesh Sir or someone else at RKB.
* partly_answered — a later RKB email deals with at least one thing the incoming email
  asked, but something else it asked is still not dealt with; or RKB acknowledged this
  email ("will revert", "Manju, please respond", "let us discuss on the call") without
  giving the substance.
* not_answered — none of the later emails deals with what this email asked. An RKB email to
  the same person about a different matter, or a notice forwarded without comment that
  does not concern this email's subject, is NOT a reply to it — however recent it is. When
  that is all there is, the status is not_answered.

Be exact. Saying "answered" when it was not means a real request is dropped; saying "not
answered" when it was means the firm writes to someone twice about the same thing. Read
what was actually written; do not assume from a subject line.

Give the sha tags of the email(s) that answered it, one or two plain sentences on what RKB
said, and — only if something is still owed — each thing still owed.
"""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["status", "answered_by", "what_rkb_said", "still_owed"],
    "properties": {
        "status": {"type": "string", "enum": ["answered", "partly_answered", "not_answered"]},
        "answered_by": {"type": "array", "items": {"type": "string"}},
        "what_rkb_said": {"type": "string"},
        "still_owed": {"type": "array", "items": {"type": "string"}},
    },
}

LINK_LABEL = {"same_conversation": "in the same conversation", "direct_reply": "a direct reply to it",
              "to_same_person": "sent to the same person afterwards"}


class Answered:
    def __init__(self, mongo: Mongo, records: Optional[Records] = None):
        self.mongo = mongo
        self.records = records or Records(mongo)
        self._rkb: Optional[List[str]] = None

    # -------------------------------------------------------------- who is us
    def rkb_addresses(self) -> List[str]:
        if self._rkb is None:
            self._rkb = sorted({a.lower() for a in self.records.rkb_addresses()})
        return self._rkb

    def from_rkb(self, art: Dict[str, Any]) -> bool:
        """Written by someone at RKB — whichever folder it was found in."""
        frm = [str(a).lower() for a in ((art.get("participants") or {}).get("from") or [])]
        return any(a in set(self.rkb_addresses()) for a in frm)

    # ------------------------------------------------------------ candidates
    def candidates(self, art: Dict[str, Any], *, limit: int = 25) -> List[Dict[str, Any]]:
        """Every later email from RKB that could be the answer, oldest first, each
        marked with how it is linked to the incoming email."""
        if not art.get("date") or self.from_rkb(art):
            return []
        rkb = self.rkb_addresses()
        senders = [str(a).lower() for a in ((art.get("participants") or {}).get("from") or []) if str(a).lower() not in set(rkb)]
        ors: List[Dict[str, Any]] = []
        if art.get("thread_key"):
            ors.append({"thread_key": art["thread_key"]})
        mid = art.get("internet_message_id")
        if mid:
            ors += [{"in_reply_to": mid}, {"references": mid}]
        if senders:
            ors += [{"participants.to": {"$in": senders}}, {"participants.cc": {"$in": senders}}]
        if not ors:
            return []
        q = {"source_type": "email", "date": {"$gt": art["date"]}, "participants.from": {"$in": rkb}, "sha256": {"$ne": art["sha256"]}, "$or": ors}
        rows = list(self.mongo.artifacts.find(q, {**EMAIL_FIELDS, "references": 1}).sort("date", 1).limit(limit))
        for r in rows:
            if art.get("thread_key") and r.get("thread_key") == art["thread_key"]:
                r["link"] = "same_conversation"
            elif mid and (mid in (r.get("in_reply_to") or []) or mid in (r.get("references") or [])):
                r["link"] = "direct_reply"
            else:
                r["link"] = "to_same_person"
        return rows

    # ----------------------------------------------------------------- check
    def check(self, art: Dict[str, Any], *, candidates: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """The verdict for one incoming email.

        Returns ``status`` (answered | partly_answered | not_answered | unknown),
        ``by`` (the emails that answered it, with who and when), ``what_rkb_said``,
        ``still_owed``, and ``latest`` — the newest candidate read, so the same
        check is not paid for twice."""
        cands = self.candidates(art) if candidates is None else candidates
        base = {"checked_at": datetime.now(timezone.utc), "candidates": len(cands), "latest": cands[-1]["sha256"] if cands else None,
                "by": [], "what_rkb_said": "", "still_owed": []}
        if not cands:
            return {**base, "status": "not_answered"}
        frm = ", ".join(self.records.who(a) for a in ((art.get("participants") or {}).get("from") or [])[:2])
        incoming = (f"THE INCOMING EMAIL [sha={art['sha256'][:12]}] received {day(art.get('date'), '%Y-%m-%d %H:%M')} UTC from {frm}\n"
                    f"Subject: {art.get('subject') or '(no subject)'}\n"
                    + (f"Files attached: {', '.join(str(n) for n in (art.get('attachment_names') or [])[:10])}\n" if art.get("attachment_names") else "")
                    + f"\n{(art.get('body_clean') or '').strip()[:bc.EMAIL_BODY_CHARS] or '(empty body)'}")
        later = []
        for c in cands:
            who = ", ".join(self.records.name(a) for a in ((c.get("participants") or {}).get("from") or [])[:1])
            to = ", ".join(self.records.name(a) for a in ((c.get("participants") or {}).get("to") or [])[:6])
            later.append(f"[sha={c['sha256'][:12]}] SENT BY {who} on {day(c.get('date'), '%Y-%m-%d %H:%M')} UTC to {to} — {LINK_LABEL[c['link']]}\n"
                         f"Subject: {c.get('subject') or '(no subject)'}\n\n{(c.get('body_clean') or '').strip()[:bc.EMAIL_BODY_CHARS] or '(empty body)'}")
        user = incoming + f"\n\n\nWHAT RKB SENT AFTERWARDS ({len(cands)} email(s), oldest first):\n\n" + "\n\n---\n\n".join(later)
        try:
            r = structured(system=prompts.CORE + "\n" + BRIEF, user=user, schema=SCHEMA, effort=bc.EFFORT_VERIFY, max_tokens=8000, label="answered?")
        except BrainError as exc:
            # A check that could not run is never read as "not answered".
            logger.warning("answered-check could not run for %s: %s", art["sha256"][:12], exc)
            return {**base, "status": "unknown", "error": str(exc)[:200]}
        d = r["data"]
        by_sha = {c["sha256"][:12]: c for c in cands}
        by = []
        for tag in d.get("answered_by") or []:
            c = by_sha.get(str(tag).replace("sha=", "").strip("[] ")[:12])
            if c:
                frm_c = ((c.get("participants") or {}).get("from") or [""])[0]
                by.append({"sha": c["sha256"], "who": self.records.name(frm_c), "at": c.get("date"), "subject": c.get("subject"), "link": c["link"]})
        status = d.get("status") if d.get("status") in ("answered", "partly_answered", "not_answered") else "unknown"
        if status in ("answered", "partly_answered") and not by:
            # It says RKB answered but named no email we hold: do not act on it.
            status = "unknown"
        return {**base, "status": status, "by": by, "what_rkb_said": str(d.get("what_rkb_said") or "")[:800],
                "still_owed": [str(x)[:300] for x in (d.get("still_owed") or [])][:8], "cost_usd": cost_of(r["usage"])}
