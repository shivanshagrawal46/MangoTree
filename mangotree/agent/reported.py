"""Facts people state in chat — captured, attributed, and applied.

JP wrote "this is done" three times in one question and the answer ignored all
three, because a user's sentence was treated as part of the question rather than
as information. A working tool takes the statement seriously: it is recorded with
who said it and when, used in the answer at once ("JP reports this paid on 5
Sept; not yet in the records"), and handed to the resolution pass, which closes
the matching item. Rakesh Sir's statements are final immediately, as with
remember-notes; anyone else's mark the item "reported done — awaiting record"
until an email or document confirms it.

Only statements of fact about the world are captured ("the redemption is paid",
"Rakesh signed the modification", "Kelly sent the drywall date"). Questions,
instructions and opinions are not.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mangotree.core.llm_json import json_call
from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg

_SYSTEM = """From ONE chat message by a member of a real-estate lending firm, extract the
statements of FACT about the deal — things the writer asserts have happened or
are the case: a payment made, a document signed or received, a task finished, a
date confirmed, a person having answered. Copy each as a short standalone
sentence in the writer's meaning. Do NOT extract questions, requests,
instructions, opinions, or anything hypothetical. If there are none, return an
empty list. Call record_facts once."""

_TOOL = {"name": "record_facts", "description": "Statements of fact in the message.",
         "input_schema": {"type": "object", "properties": {"facts": {"type": "array", "items": {"type": "string"}}}, "required": ["facts"]}}

#: Cheap pre-check so most questions never pay for the call.
_HINT = re.compile(r"\b(done|paid|received|signed|sent|completed|finished|closed|resolved|confirmed|has been|have been|is now|was paid|got|already)\b", re.I)


def extract_reported_facts(client, message: str) -> List[str]:
    if not message or not _HINT.search(message):
        return []
    try:
        data = json_call(client, model=cfg.AGENT_PLANNER_MODEL, system=_SYSTEM, user=message[:4000],
                         tool_name=_TOOL["name"], description=_TOOL["description"], schema=_TOOL["input_schema"], max_tokens=1500)
        return [str(f).strip() for f in (data.get("facts") or []) if str(f).strip()][:10]
    except Exception as exc:
        logger.warning("reported-fact extraction failed: %s", exc)
        return []


def record_reported_facts(mongo, *, facts: List[str], property_id: Optional[str], user: Dict[str, Any], job_id: str) -> List[Dict[str, Any]]:
    coll = mongo.db["reported_facts"]
    coll.create_index([("property_id", 1), ("applied", 1), ("at", -1)], name="ix_reported_prop")
    now = datetime.now(timezone.utc)
    out = []
    for text in facts:
        fid = "rf_" + hashlib.sha1(f"{property_id}|{user.get('user_id')}|{text}".encode()).hexdigest()[:12]
        doc = {"fact_id": fid, "property_id": property_id, "text": text, "by": user.get("user_id"), "by_name": user.get("name"),
               "role": user.get("role"), "at": now, "applied": False, "job_id": job_id}
        coll.update_one({"fact_id": fid}, {"$setOnInsert": doc}, upsert=True)
        out.append(doc)
    return out


def facts_block(facts: List[Dict[str, Any]], user: Dict[str, Any]) -> str:
    """The message injected ahead of the question so the answer honours them."""
    final = user.get("role") == "ceo"
    lines = "\n".join(f"- {f['text']}" for f in facts)
    rule = ("These come from Rakesh Sir and are FINAL: treat them as true and do not raise the matters as open."
            if final else
            f"These come from {user.get('name')} and are not yet in the records: treat them as true for this answer, "
            "do not raise the matters as open, and where you mention them say they are reported by "
            f"{user.get('name')} and awaiting a record.")
    return f"FACTS STATED BY THE ASKER IN THIS MESSAGE\n{lines}\n{rule}"
