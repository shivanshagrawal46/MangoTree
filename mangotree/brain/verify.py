"""The second reading — nothing reaches the person until it has been checked.

After an answer or a reply is written, a separate Opus 5.5 call reads it
statement by statement against the same records and answers one question for
each: does a record say this, and is it still true? Four ways to fail:

    unsupported   no record in front of it says this
    contradicted  a record says otherwise
    overtaken     it was true once — a later email or file answers it, changes it,
                  or makes it moot ("we are still waiting for…" when it has arrived;
                  asking again for something already sent; a date that has passed)
    misattributed the fact is real but pinned on the wrong person, property, date
                  or document

For a reply it also checks that every question in the incoming email is dealt
with. What it finds goes back to the writer to be fixed from the record or
removed. Whatever is still open after that is put in front of the person, first
thing — never hidden.

The checker shares the record with the writer (same cached context) but not the
writer's conversation: it has no stake in the draft.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger

from . import config as bc
from .llm import BrainError, structured

KINDS = ("unsupported", "contradicted", "overtaken", "misattributed", "wrong_arithmetic", "question_not_answered")

BRIEF = """YOUR JOB NOW: CHECK IT BEFORE HE SEES IT
Someone else at RKB has just written the text below — an answer to a question, or a reply
to an email. It is about to go in front of Rakesh Sir, and he will act on it or send it
under his name. You are the last check. You did not write it and you have no reason to
defend it.

Go through it statement by statement. For every statement of fact — an amount, a date, a
name, who said or sent or agreed what, what a document contains, whether something has or
has not happened — find the record that says so in THE MAIL, THE FILES, the property file,
or the evidence gathered. Then ask whether it is still true as of the newest record.

Report a problem when a statement is:
* unsupported — nothing in front of you says it. A plausible statement with no record
  behind it is a problem.
* contradicted — a record says otherwise.
* overtaken — a later email or file answers it, changes it or makes it moot. Look hardest
  for this: "we have not received", "still waiting", "please send", "no reply yet", "is
  due on" — check the newest mail from that person and on that subject before you accept
  any of them. A reply that asks for something already sent, or says nothing came when it
  did, costs the firm its credibility.
* misattributed — the fact is real but belongs to another person, property, date or
  document. 904 Bayshore and 910 Bayshore are different loans.
* wrong_arithmetic — a total, difference or percentage that does not follow from the
  figures it is built on. Redo the sum yourself.
* question_not_answered — only for a reply: a question or request in the incoming email
  that the reply neither answers nor explicitly sets aside.

Do not report: matters of tone or wording; opinions and recommendations that are marked
as the writer's view; a statement the property file's standing instructions support;
figures quoted from the MONEY section with their as-of date; anything you merely would
have phrased differently. A statement that something is absent ("nothing on file shows…")
is fine if you also cannot find it, and it carries its as-of time.

For each problem give the statement as written, what the record actually shows (name the
email by sender and date, or the file by name), and the correction. If the text is sound,
say so — an empty list is a good result, and inventing a problem is as bad as missing one.
"""

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["statements_checked", "problems", "sound"],
    "properties": {
        "statements_checked": {"type": "integer"},
        "sound": {"type": "boolean"},
        "problems": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["statement", "kind", "record_shows", "fix"],
            "properties": {"statement": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
                           "record_shows": {"type": "string"}, "fix": {"type": "string"}}}},
    },
}


def check(*, system: Sequence[dict], asked: str, written: str, evidence: str, freshness: str, kind: str,
          tools: Optional[Sequence[dict]] = None) -> Dict[str, Any]:
    """One checking pass. ``system`` is the shared record context followed by the
    checker's brief; ``asked`` is the question or the incoming email; ``evidence``
    is what retrieval and lookups put in front of the writer."""
    # The evidence comes first and is marked for caching: the second pass, after the
    # writer's correction, reads the same evidence and only the text under check differs.
    user = [
        {"type": "text", "cache_control": {"type": "ephemeral"},
         "text": (f"{'THE EMAIL BEING ANSWERED' if kind == 'draft' else 'THE QUESTION THAT WAS ASKED'}:\n{asked}\n\n"
                  f"EVIDENCE GATHERED FOR THE WRITER (retrieved passages and documents opened):\n{evidence or '(none)'}")},
        {"type": "text",
         "text": f"{freshness}\n\n{'THE REPLY AS WRITTEN' if kind == 'draft' else 'THE ANSWER AS WRITTEN'} — check this:\n{written}"},
    ]
    try:
        r = structured(system=system, user=user, schema=SCHEMA, effort=bc.EFFORT_VERIFY, max_tokens=24_000, label=f"verify {kind}", tools=tools)
    except BrainError as exc:
        # A check that could not run is said out loud; it is never reported as a pass.
        logger.warning("verification could not run: %s", exc)
        return {"ran": False, "error": str(exc)[:200], "problems": [], "statements_checked": 0, "usage": {}}
    d = r["data"]
    problems = [p for p in (d.get("problems") or []) if str(p.get("statement") or "").strip()]
    return {"ran": True, "statements_checked": int(d.get("statements_checked") or 0), "problems": problems, "usage": r["usage"]}


def as_instruction(problems: List[Dict[str, Any]]) -> str:
    """The checker's findings, put to the writer."""
    lines = ["A second reader checked what you wrote against the records and found the following. For each one, correct the "
             "statement from the record, or take it out. If you are sure the reader is wrong, open the record and cite the exact "
             "passage — do not simply repeat the statement.", ""]
    for i, p in enumerate(problems, 1):
        lines.append(f"{i}. [{str(p.get('kind')).replace('_', ' ')}] You wrote: “{p.get('statement')}”\n"
                     f"   The record shows: {p.get('record_shows')}\n   Correction: {p.get('fix')}")
    return "\n".join(lines)


def for_person(problems: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """What is still open after the writer's correction, in plain words."""
    label = {"unsupported": "No record found for this", "contradicted": "A record says otherwise", "overtaken": "A later record changes this",
             "misattributed": "May be attributed to the wrong person, property or date", "wrong_arithmetic": "The arithmetic does not check",
             "question_not_answered": "A question in the email is not answered"}
    return [{"kind": p.get("kind"), "label": label.get(p.get("kind"), "Check this"), "statement": str(p.get("statement") or "")[:400],
             "record_shows": str(p.get("record_shows") or "")[:500], "fix": str(p.get("fix") or "")[:400]} for p in problems]
