"""Learning from his corrections.

Every time the person changes a draft — in the draft box, by telling the chat
to change it, or by sending something different from what was written — that is
him teaching the system how he works. The difference between what was written
and what he sent is read once, and anything he would want *every time* is kept
as a short rule:

    "Open with the decision; no thank-you line."          applies to: everyone
    "With Erin, ask for dates in a numbered list."        applies to: this recipient
    "On Marmon, never quote a draw net of holdback."      applies to: this property

Rules ride with every later draft, newest first, together with the last two
"what was written / what he sent" pairs for the same recipient — the examples
do more than the rules.

Four guards, because a wrong lesson is worse than none:
  * most edits are about one situation and teach nothing — the reader is told so
    and may return no rule at all, and never more than four;
  * one edit proves little: a rule said to hold for all his email applies only to
    the person it was learned on until a second, separate correction shows it
    again. Then it applies to everyone;
  * a changed fact (a figure, a date) is not a style rule: it is recorded on the
    property as a correction, where the next draft will read it as a fact;
  * every rule is visible on the Drafts page with the edit it came from, and one
    click removes it.
"""
from __future__ import annotations

import difflib
import re
import threading
import uuid
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .llm import BrainError, cost_of, structured
from .store import BrainStore, name_of, now

SCOPES = ("everyone", "this_recipient", "this_property")
KINDS = ("tone", "length", "structure", "content", "policy")

BRIEF = """YOUR JOB NOW: LEARN FROM HIS CORRECTION
A reply was drafted for someone at RKB. He changed it. Below are the draft as written, the
version he actually used, and — if he gave one — the instruction he typed. Work out what
his change teaches about how he wants his emails written.

Be strict. Most edits are about this one situation and teach nothing general: fixing a
detail, adding a fact he happened to know, reacting to news. Those are not rules. Return a
rule only for something he would plainly want EVERY time in the same circumstances:
* tone      how warm or firm, how he opens and closes, what he never says
* length    how long, how many paragraphs, what he cuts
* structure numbered or prose, order of points, where the ask goes
* content   what he always includes or always leaves out (copying someone, attaching a
            schedule, restating a condition)
* policy    a position of the firm his edit reveals ("never approve a draw without the
            change order")

Write each rule as one plain instruction to a future writer, specific enough to follow,
and say what in his edit shows it. At most four rules — the ones that matter most; one
edit rarely teaches more. Choose the narrowest scope the evidence supports: this_recipient
if it is about how he writes to this person, this_property if about this deal, everyone
only if it clearly holds for all his email.

RULES ALREADY LEARNED are listed with their ids. If his edit shows one of them again, do
not write it a second time in new words: return it with `same_as` set to that id. That is
how a rule gets confirmed. Set `same_as` to null for a rule that is new.

Separately, list any FACT he corrected — a figure, a date, a name, who is responsible —
as "the draft said X; he wrote Y". Those are corrections to the record, not style.

If the two versions differ only in trivial wording, return no rules and no facts.
"""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["what_he_changed", "rules", "facts_corrected"],
    "properties": {
        "what_he_changed": {"type": "string"},
        "rules": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["rule", "applies_to", "kind", "shown_by", "same_as"],
            "properties": {"rule": {"type": "string"}, "applies_to": {"type": "string", "enum": list(SCOPES)},
                           "kind": {"type": "string", "enum": list(KINDS)}, "shown_by": {"type": "string"},
                           "same_as": {"type": ["string", "null"]}}}},
        "facts_corrected": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["draft_said", "he_wrote"],
            "properties": {"draft_said": {"type": "string"}, "he_wrote": {"type": "string"}}}},
    },
}


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def changed_enough(before: str, after: str) -> bool:
    """Is there anything to learn from? Identical or near-identical text is not an edit."""
    a, b = _norm(before), _norm(after)
    if not a or not b or a == b:
        return False
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() < 0.985


def _similar(a: str, b: str) -> bool:
    wa = set(re.findall(r"[a-z0-9]{3,}", (a or "").lower()))
    wb = set(re.findall(r"[a-z0-9]{3,}", (b or "").lower()))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6


class Lessons:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.coll = mongo.db["draft_lessons"]
        self.pairs = mongo.db["draft_corrections"]
        try:
            self.coll.create_index("lesson_id", unique=True, name="ux_dl_id")
            self.coll.create_index([("active", 1), ("sender", 1), ("created_at", -1)], name="ix_dl_active")
            self.pairs.create_index([("sender", 1), ("recipients", 1), ("at", -1)], name="ix_dc_recipient")
        except Exception:
            pass

    # --------------------------------------------------------------- learn
    def learn(self, *, draft: Dict[str, Any], final_text: str, source: str, instruction: str = "", before: Optional[str] = None,
              sent_sha: Optional[str] = None) -> Dict[str, Any]:
        """Read one correction. ``source``: sent | edited | chat_instruction | rewrite_instruction."""
        before = before if before is not None else (draft.get("original_body") or draft.get("body") or "")
        if not changed_enough(before, final_text):
            return {"learned": 0, "reason": "no real change"}
        sender = draft.get("sender") or bc.DEFAULT_SENDER
        recipients = sorted({(t.get("email") or "").lower() for t in draft.get("to") or [] if t.get("email")})
        pids = list(draft.get("property_ids") or [])
        to_names = ", ".join(t.get("name") or t.get("email") or "" for t in draft.get("to") or [])
        known = list(self.coll.find({"active": True, "sender": sender}, {"_id": 0, "lesson_id": 1, "rule": 1, "applies_to": 1}).sort("created_at", -1).limit(80))
        known_text = "\n".join(f"- {k['lesson_id']} ({k['applies_to']}): {k['rule']}" for k in known) or "(none yet)"
        user = (f"RULES ALREADY LEARNED:\n{known_text}\n\n"
                f"WRITTEN FOR: {bc.SENDER_NAMES.get(sender, sender)} — TO: {to_names or '(unknown)'} — "
                f"ABOUT: {', '.join(name_of(p) for p in pids) or 'general'} — SUBJECT: {draft.get('subject') or ''}\n\n"
                + (f"THE INSTRUCTION HE TYPED: {instruction.strip()}\n\n" if instruction.strip() else "")
                + f"THE DRAFT AS WRITTEN:\n{before.strip()}\n\nWHAT HE ACTUALLY USED:\n{final_text.strip()}")
        try:
            r = structured(system=prompts.CORE + "\n" + BRIEF, user=user, schema=SCHEMA, effort=bc.EFFORT_LESSON, max_tokens=12_000, label="lesson")
        except BrainError as exc:
            logger.warning("lesson not learned: %s", exc)
            return {"learned": 0, "error": str(exc)[:200]}
        d = r["data"]
        pair_id = f"c-{uuid.uuid4().hex[:10]}"
        self.pairs.insert_one({"pair_id": pair_id, "draft_id": draft.get("draft_id"), "sender": sender, "recipients": recipients, "property_ids": pids,
                               "subject": draft.get("subject"), "before": before.strip()[:8000], "after": final_text.strip()[:8000], "instruction": instruction.strip()[:1000],
                               "source": source, "sent_sha": sent_sha, "what_he_changed": str(d.get("what_he_changed") or "")[:1000], "at": now(),
                               "cost_usd": cost_of(r["usage"])})
        added, merged = [], 0
        known_ids = {k["lesson_id"] for k in known}
        for rule in (d.get("rules") or [])[:4]:
            text = str(rule.get("rule") or "").strip()
            if not text:
                continue
            scope = rule.get("applies_to") if rule.get("applies_to") in SCOPES else "this_recipient"
            same_id = rule.get("same_as") if rule.get("same_as") in known_ids else None
            if not same_id:
                same_id = next((k["lesson_id"] for k in known if _similar(k["rule"], text)), None)
            if same_id:
                # He has done this before: the rule is confirmed, not written twice. A
                # correction on a different draft is what counts; the same draft read
                # again proves nothing new.
                prior = self.coll.find_one({"lesson_id": same_id}, {"draft_ids": 1}) or {}
                new_draft = draft.get("draft_id") not in (prior.get("draft_ids") or [])
                self.coll.update_one({"lesson_id": same_id}, {"$inc": {"times_seen": 1 if new_draft else 0}, "$set": {"last_seen": now()},
                                                              "$addToSet": {"pair_ids": pair_id, "draft_ids": draft.get("draft_id"),
                                                                            "seen_recipients": {"$each": recipients}}})
                merged += 1
                continue
            doc = {"lesson_id": f"l-{uuid.uuid4().hex[:10]}", "rule": text[:500], "applies_to": scope, "kind": rule.get("kind") if rule.get("kind") in KINDS else "content",
                   "shown_by": str(rule.get("shown_by") or "")[:500], "sender": sender, "recipients": recipients if scope == "this_recipient" else [],
                   "recipient_names": to_names if scope == "this_recipient" else "", "property_ids": pids if scope == "this_property" else [],
                   "seen_recipients": recipients, "learned_on": to_names,
                   "source": source, "draft_id": draft.get("draft_id"), "draft_ids": [draft.get("draft_id")], "pair_ids": [pair_id],
                   "times_seen": 1, "active": True, "created_at": now(), "last_seen": now()}
            self.coll.insert_one(dict(doc))
            added.append(doc["lesson_id"])
        # A corrected fact belongs on the property, where the next reader will meet it as a fact.
        facts = [f for f in (d.get("facts_corrected") or []) if f.get("draft_said") and f.get("he_wrote")]
        for f in facts:
            for pid in pids or [bc.GENERAL_ID]:
                self.store.add_activity(pid, at=now(), kind="correction", who=bc.SENDER_NAMES.get(sender, sender),
                                        title=f"{bc.SENDER_NAMES.get(sender, sender)} corrected a draft",
                                        summary=f"The draft said: {str(f['draft_said'])[:400]} — he wrote: {str(f['he_wrote'])[:400]}", relevance="material")
        return {"learned": len(added), "confirmed": merged, "facts": len(facts), "lesson_ids": added, "pair_id": pair_id,
                "what_he_changed": d.get("what_he_changed")}

    def learn_async(self, **kw) -> None:
        """Learning never holds up the person: it runs behind the action that caused it."""
        def run():
            try:
                out = self.learn(**kw)
                logger.info("lesson: %s", {k: out.get(k) for k in ("learned", "confirmed", "facts", "reason", "error")})
            except Exception:
                logger.exception("learning from a correction failed")
        threading.Thread(target=run, name="lesson", daemon=True).start()

    # ----------------------------------------------------------------- use
    def applicable(self, *, sender: str, recipients: Sequence[str], pids: Sequence[str], limit: int = 40) -> List[Dict[str, Any]]:
        recipients = [r.lower() for r in recipients if r]
        # A rule for all his email applies to everyone once two separate corrections
        # have shown it; after one, only to the person it was learned on.
        ors: List[Dict[str, Any]] = [{"applies_to": "everyone", "times_seen": {"$gte": 2}}]
        if recipients:
            ors.append({"applies_to": "everyone", "times_seen": {"$lt": 2}, "seen_recipients": {"$in": recipients}})
            ors.append({"applies_to": "this_recipient", "recipients": {"$in": recipients}})
        if pids:
            ors.append({"applies_to": "this_property", "property_ids": {"$in": list(pids)}})
        return list(self.coll.find({"active": True, "sender": sender, "$or": ors}, {"_id": 0}).sort([("times_seen", -1), ("last_seen", -1)]).limit(limit))

    def block(self, *, sender: str, recipients: Sequence[str], pids: Sequence[str]) -> str:
        """What rides with a draft: his rules, then two earlier corrections to the
        same recipient shown in full."""
        rules = self.applicable(sender=sender, recipients=recipients, pids=pids)
        out: List[str] = []
        if rules:
            out.append("WHAT HE HAS CORRECTED IN EARLIER DRAFTS — these are his own changes; follow them:")
            for r in rules:
                where = {"everyone": "all his email" if (r.get("times_seen") or 1) >= 2 else "seen once, on an email to this person",
                         "this_recipient": f"to {r.get('recipient_names') or 'this person'}",
                         "this_property": f"on {', '.join(name_of(p) for p in r.get('property_ids') or [])}"}[r["applies_to"]]
                seen = f", seen {r['times_seen']} times" if (r.get("times_seen") or 1) > 1 else ""
                out.append(f"- ({where}{seen}) {r['rule']}")
        recips = [r.lower() for r in recipients if r]
        pairs = list(self.pairs.find({"sender": sender, "recipients": {"$in": recips}}, {"_id": 0}).sort("at", -1).limit(2)) if recips else []
        if pairs:
            out.append("\nEARLIER REPLIES TO THIS PERSON — what was drafted, and what he actually sent. Write the way the second one reads:")
            for p in pairs:
                out.append(f"\n[{p.get('subject') or ''}]\nDRAFTED:\n{str(p.get('before') or '')[:2500]}\n\nHE SENT:\n{str(p.get('after') or '')[:2500]}")
        return "\n".join(out)

    # -------------------------------------------------------------- manage
    def list(self, *, active_only: bool = True, limit: int = 200) -> List[Dict[str, Any]]:
        q = {"active": True} if active_only else {}
        return list(self.coll.find(q, {"_id": 0}).sort("created_at", -1).limit(limit))

    def remove(self, lesson_id: str, by: str) -> bool:
        r = self.coll.update_one({"lesson_id": lesson_id}, {"$set": {"active": False, "removed_by": by, "removed_at": now()}})
        return r.modified_count > 0

    def pair(self, pair_id: str) -> Optional[Dict[str, Any]]:
        return self.pairs.find_one({"pair_id": pair_id}, {"_id": 0})
