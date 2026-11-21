"""The second reading of an entry before it is filed.

When a new record is read into its file, one reader writes the entry: the log
line, the to-dos, whether a reply is owed. A single reading leaves things out —
on a trial of ten emails (2026-11-14) both the old and the new way dropped a
point that mattered on about one email in three, though every statement they
did make was right. So a second reader goes through the record again with the
entry beside it and says what the record carries that the entry does not, what
the entry got wrong, an action nobody was given, and whether the reply decision
is right. What it finds goes back to the writer once.

It costs little because it reads what is already stored: it is sent the same
tools, the same system prompt and the same record as the writer, at the same
effort, and adds only the entry. Nothing here calls a tool or looks anything up.
"""
from __future__ import annotations

from typing import Any, Dict, List

_S = {"type": "string"}

SUBMIT_CHECK = {
    "name": "submit_check",
    "description": "Hand over the result of the second reading of an entry. Used only when you are asked to make a second reading.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "required": ["missed", "wrong", "actions", "reply_right", "reply_note", "rating_right", "rating_note"],
        "properties": {
            "missed": {"type": "array", "items": _S, "description": "a point in the new record that matters and that the entry does not carry at all"},
            "wrong": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["statement", "record_shows"],
                                                 "properties": {"statement": _S, "record_shows": _S}}},
            "actions": {"type": "array", "items": _S, "description": "an action the record creates for someone that is neither among the entry's new items nor already open on the file"},
            "reply_right": {"type": "boolean"}, "reply_note": _S,
            "rating_right": {"type": "boolean"}, "rating_note": _S,
        },
    },
}

BRIEF = """THIS IS THE SECOND READING. Do not call submit_update and do not look anything up.

A colleague has read THE NEW RECORD above and written the entry below for the file. Before it is
filed, go through the new record from top to bottom with the entry beside it, and through THE
MAIL where the entry leans on it. Then call submit_check with:

* missed — each point in the new record that matters and that the entry does not carry at all:
  money asked for, paid or owed; a date or a deadline; something asked of RKB, or by RKB of
  someone else; something that is stuck, held up, refused, failing or disputed, and why; a
  decision; a change to who, what or how much. Only what a person acting on this file would
  need. Not courtesies, not signatures, not what the property file already says.
* wrong — each statement in the entry that the new record or the mail contradicts: quote it,
  and say what the record shows. A statement you cannot check is not wrong; leave it out.
* actions — each thing the new record now requires somebody to do that is neither among the
  entry's new items nor already an open item on the property file: a reply or a decision
  owed, a document to check before it is paid, something broken that has to be put right.
  Name who should do it.
* reply_right — whether the entry's reply decision is right under the rule: a reply is owed
  only when the sender, in the words of this message, asks RKB a question, asks for money, a
  document, a decision or a confirmation, or proposes something RKB must accept or decline;
  never for a file sent with no request in words, for an email RKB itself sent, or when a
  later message has already answered it. Say why in reply_note when it is wrong.
* rating_right — whether the rating fits: material changes the picture or needs action,
  minor is worth a line and changes nothing, noise is neither. Say why in rating_note when
  it is wrong.

Return empty lists and true when the entry is sound. Most entries are. Do not ask for more
words for their own sake, and do not repeat a point the entry already makes in other words.

THE ENTRY:
"""


def render_entry(d: Dict[str, Any]) -> str:
    """The writer's result as the second reader sees it."""
    reply = d.get("reply") or {}
    lines = [f"rating: {d.get('relevance')}", f"title: {d.get('title')}", f"summary: {d.get('summary')}"]
    if d.get("headline"):
        lines.append(f"new headline: {d['headline']}")
    if d.get("status"):
        lines.append(f"new status: {d['status']}")
    for f in d.get("facts_set") or []:
        lines.append(f"fact set: {f.get('key')} = {f.get('value')}")
    for i in d.get("items_add") or []:
        lines.append(f"new item: ({i.get('owner')}, {i.get('kind')}{', due ' + str(i['due']) if i.get('due') else ''}) {i.get('title')}"
                     + (f" — {i['detail']}" if i.get("detail") else ""))
    for i in d.get("items_close") or []:
        lines.append(f"closes item {i.get('id')}: {i.get('note')}")
    for i in d.get("items_update") or []:
        lines.append(f"updates item {i.get('id')}: " + "; ".join(f"{k}={i[k]}" for k in ("title", "detail", "due") if i.get(k)))
    if not (d.get("items_add") or d.get("items_close") or d.get("items_update")):
        lines.append("items: none added, closed or changed")
    lines.append("reply owed: " + (f"yes, from {reply.get('sender')} — {reply.get('why')}" if reply.get("needed") else "no"))
    for p in (reply.get("points") or []) if reply.get("needed") else []:
        lines.append(f"  reply point: {p}")
    return "\n".join(lines)


def findings(c: Dict[str, Any]) -> List[str]:
    """What the second reading found, one plain line each. Empty when the entry is sound."""
    out = [f"Left out: {m}" for m in (c.get("missed") or []) if str(m).strip()]
    out += [f"Wrong: “{w.get('statement')}” — the record shows: {w.get('record_shows')}" for w in (c.get("wrong") or []) if w.get("statement")]
    out += [f"No item for: {a}" for a in (c.get("actions") or []) if str(a).strip()]
    if c.get("reply_right") is False:
        out.append(f"The reply decision is wrong: {c.get('reply_note') or 'see the rule'}")
    if c.get("rating_right") is False:
        out.append(f"The rating is wrong: {c.get('rating_note') or 'see the definitions'}")
    return out


def as_instruction(found: List[str]) -> str:
    return ("A second reader went through the record with your entry beside it and found the following.\n\n"
            + "\n".join(f"- {x}" for x in found)
            + "\n\nCorrect the entry and call submit_update again with the complete result. Keep everything that was right. "
              "Take up each point the record supports; if the record does not support one, leave it out. "
              "Do not add an item that is already open on the file.")


def summary(c: Dict[str, Any], *, corrected: bool) -> Dict[str, Any]:
    """What is kept on the event: how many of each, not the text."""
    return {"ran": True, "missed": len(c.get("missed") or []), "wrong": len(c.get("wrong") or []), "actions": len(c.get("actions") or []),
            "reply_right": c.get("reply_right") is not False, "rating_right": c.get("rating_right") is not False, "corrected": bool(corrected)}
