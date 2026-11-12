"""Chat — a conversation with someone who already knows the file.

Each turn sends the model three things: who it is and how to work (cached, never
changes), the property file as it stands right now (cached until an event
changes it), and the real conversation so far. It answers from those when it
can, and calls a lookup only when the answer needs the exact words or figures.
Text is streamed as it is written; a follow-up costs seconds, not a new
investigation.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from . import checks
from . import verify
from .agent import run_loop
from .context import Records, portfolio_block, property_block
from .lessons import Lessons
from .llm import add_usage, cost_of, system_blocks
from .store import BrainStore, name_of, now
from .tools import Toolkit

Emit = Callable[[str, Dict[str, Any]], None]

_ROLE = {"ceo": "Rakesh Sir (CEO)", "accountant": "JP Sir (accountant)", "operations": "Manjunath Sir (operations)"}
PDF_INLINE_MAX = 18 * 1024 * 1024


def speaker(user: Dict[str, Any]) -> str:
    return _ROLE.get(user.get("role"), user.get("name") or user.get("user_id") or "team member")


class ChatEngine:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)

    # --------------------------------------------------------------- files
    def _file_blocks(self, files: Sequence[Dict[str, Any]]) -> List[dict]:
        blocks: List[dict] = []
        for f in files:
            name = f.get("name") or "file"
            kind = f.get("kind")
            raw = None
            if kind in ("pdf", "image"):
                try:
                    raw = self.mongo.get_original(f["sha256"])
                except Exception:
                    raw = None
            if kind == "pdf" and raw and len(raw) <= PDF_INLINE_MAX:
                blocks.append({"type": "document", "title": name,
                               "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(raw).decode("ascii")}})
            elif kind == "image" and raw and len(raw) <= 4 * 1024 * 1024:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": f.get("content_type") or "image/png",
                                                           "data": base64.standard_b64encode(raw).decode("ascii")}})
            elif (f.get("text") or "").strip():
                note = "" if not f.get("text_truncated") else "\n[This file was longer than what is shown here.]"
                blocks.append({"type": "text", "text": f"ATTACHED FILE “{name}”:\n{f['text']}{note}"})
            else:
                blocks.append({"type": "text", "text": f"ATTACHED FILE “{name}”: (its contents could not be read here — tell the user)"})
        return blocks

    def _history(self, rows: Sequence[Dict[str, Any]]) -> List[dict]:
        """Stored messages -> API messages. Files ride with the two most recent
        messages that carried them; older ones are named so the thread still
        reads correctly."""
        with_files = [m["message_id"] for m in rows if m.get("role") == "user" and m.get("files")][-2:]
        out: List[dict] = []
        for m in rows:
            if m.get("role") == "user":
                text = m.get("model_text") or m.get("text") or ""
                if m.get("files"):
                    if m["message_id"] in with_files:
                        out.append({"role": "user", "content": self._file_blocks(m["files"]) + [{"type": "text", "text": text}]})
                        continue
                    text += "\n(attached earlier: " + ", ".join(f.get("name") or "file" for f in m["files"]) + ")"
                out.append({"role": "user", "content": text})
            elif m.get("role") == "assistant":
                text = (m.get("text") or "").strip()
                for d in m.get("drafts") or []:
                    to = ", ".join(t.get("name") or t.get("email") or "" for t in d.get("to") or [])
                    text += f"\n\n[Draft {d.get('draft_id')} saved — to: {to}; subject: {d.get('subject')}]\n{d.get('body') or ''}"
                if text:
                    out.append({"role": "assistant", "content": text})
        return out

    def _sources(self, answer: str, kit: Toolkit, earlier: Sequence[str]) -> List[Dict[str, Any]]:
        """The passages the answer cites. A number from an earlier turn of the
        same thread still resolves to its passage."""
        out = kit.cited(answer)
        have = {s["index"] for s in out}
        import re as _re
        old = sorted({int(n) for n in _re.findall(r"\[#(\d+)\]", answer or "")} - have)
        old = [n for n in old if 0 < n <= len(earlier)]
        if old:
            docs = {d["chunk_id"]: d for d in self.mongo.chunks.find({"chunk_id": {"$in": [earlier[n - 1] for n in old]}},
                                                                     {"_id": 0, "chunk_id": 1, "artifact_sha": 1, "display_name": 1, "date": 1, "placement": 1})}
            for n in old:
                d = docs.get(earlier[n - 1])
                if d:
                    out.append({"index": n, "chunk_id": d["chunk_id"], "artifact_sha": d.get("artifact_sha"), "citation": d.get("display_name") or "",
                                "display_name": d.get("display_name"), "date": str(d.get("date") or "")[:10], "placement": d.get("placement")})
        return sorted(out, key=lambda s: s["index"])

    def _context(self, pid: Optional[str]) -> str:
        if pid:
            return property_block(self.mongo, self.store, pid)
        return portfolio_block(self.store)

    # ---------------------------------------------------------------- turn
    @staticmethod
    def _evidence(seeded: str, messages: Sequence[dict], start: int) -> str:
        parts = [seeded]
        for m in messages[start:]:
            c = m.get("content")
            if isinstance(c, list):
                parts += [b["content"] for b in c if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), str)]
        return "\n\n".join(p for p in parts if p)

    def _drafts_of(self, kit: Toolkit) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for a in kit.actions:
            if a.get("type") == "draft" and a["draft_id"] not in {x["draft_id"] for x in out}:
                d = self.store.get_draft(a["draft_id"])
                if d:
                    out.append({k: d.get(k) for k in ("draft_id", "to", "cc", "subject", "body", "note", "status", "property_ids")})
        return out

    def ask(self, thread_id: str, *, user: Dict[str, Any], text: str, files: Sequence[Dict[str, Any]] = (),
            emit: Optional[Emit] = None, should_stop: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
        """One question, answered properly.

        1. The document search runs for the question across the whole record.
        2. The model answers with four things in view: what the search found,
           every email and file of the last 14 days, the property file, and the
           conversation so far.
        3. A second reader checks every statement against the same record.
        4. Problems go back to the writer once; anything still open is shown to
           the person with the answer.
        Nothing is shown until step 3 has run — a statement that had to be taken
        back is worse than a minute's wait."""
        say = emit or (lambda *_: None)
        step = lambda label, **kw: say("tool", {"name": "step", "label": label, **kw})   # noqa: E731
        started = time.time()
        thread = self.store.get_thread(thread_id)
        if not thread:
            raise ValueError("chat thread not found")
        pid = thread.get("property_id")
        records = Records(self.mongo)
        prior = self.store.thread_messages(thread_id, limit=bc.CHAT_HISTORY_MESSAGES)
        stamp = datetime.now(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")
        model_text = f"[{speaker(user)} · {stamp}]\n{text.strip()}"
        user_msg = self.store.add_message(thread_id, {"role": "user", "text": text.strip(), "model_text": model_text,
                                                      "by": user.get("user_id"), "by_name": user.get("name"), "files": list(files)})
        if not thread.get("title"):
            self.store.threads.update_one({"thread_id": thread_id}, {"$set": {"title": text.strip().replace("\n", " ")[:80]}})

        context = self._context(pid)
        # In a property chat the record itself is in view — every email and every file of the last 14 days.
        shared = [prompts.CORE] + ([records.record_block(pid)] if pid else []) + [context]
        learned = Lessons(self.mongo, self.store).block(sender=user.get("user_id") or bc.DEFAULT_SENDER, recipients=[], pids=[pid] if pid else [])
        system = system_blocks(*shared, prompts.CHAT + ("\n\n" + learned if learned else ""))
        fresh = records.freshness(pid) if pid else records.freshness(None)
        messages = self._history(prior + [user_msg])
        turn_start = len(messages) - 1
        earlier = list(thread.get("source_chunk_ids") or [])
        conversation = [{"role": m["role"], "content": m["content"] if isinstance(m["content"], str)
                         else " ".join(b.get("text") or "" for b in m["content"] if b.get("type") == "text")} for m in messages[:-1]]
        kit = Toolkit(self.mongo, self.store, pid=pid, user=user, base_index=len(earlier), conversation=conversation, thread_id=thread_id,
                      user_text=text.strip())

        # 1 ---- the document search, for every question
        step("Searching every record on file for your question…", running=True)
        seeded, seed_info = kit.seed(text.strip())
        step((f"Searched the records: {seed_info.get('passages', 0)} passages"
              + (f", {seed_info['relevant']} directly relevant" if seed_info.get("relevant") is not None else "")) if not seed_info.get("error")
             else "The document search failed for this question", error=bool(seed_info.get("error")))
        last = messages[-1]
        block = {"type": "text", "text": fresh + "\n\n" + seeded}
        last["content"] = ([{"type": "text", "text": last["content"]}] if isinstance(last["content"], str) else list(last["content"])) + [block]

        # 2 ---- the answer (held back until it has been checked)
        usage: Dict[str, int] = {}
        tool_log: List[Dict[str, Any]] = []

        def on_tool(entry: Dict[str, Any]) -> None:
            tool_log.append(entry)
            say("tool", entry)

        step("Reading the mail, the files and what the search found…", running=True)
        res = run_loop(system=system, messages=messages, toolkit=kit, effort=bc.EFFORT_CHAT, max_rounds=bc.CHAT_MAX_ROUNDS,
                       on_tool=on_tool, should_stop=should_stop)
        add_usage(usage, res.usage)
        rounds = res.rounds
        if res.cancelled:
            return {"cancelled": True}
        answer = res.text
        verification: Dict[str, Any] = {"ran": False, "passes": 0, "statements_checked": 0, "found": 0, "corrected": 0, "open": []}
        if res.refused:
            answer = ("I could not answer that as it was asked — the model's safety filter declined the request"
                      + (f" (category: {res.refusal_category})" if res.refusal_category else "") + ". Try rephrasing, or ask about the specific record.")
        elif not answer:
            answer = "I did not manage to produce an answer for that. Please ask again."
        else:
            # 3 ---- the second reading
            asked = "\n\n".join([f"{m['role'].upper()}: {m['content'][:3000]}" for m in conversation[-4:]] + [f"THE QUESTION NOW: {text.strip()}"])
            vsystem = system_blocks(*shared, verify.BRIEF)
            passes: List[Dict[str, Any]] = []
            for attempt in (1, 2):
                drafts_now = self._drafts_of(kit)
                written = answer + "".join(f"\n\n--- EMAIL DRAFTED IN THIS ANSWER (to {', '.join(t.get('email') or t.get('name') or '' for t in d.get('to') or [])}; "
                                           f"subject: {d.get('subject')}) ---\n{d.get('body')}" for d in drafts_now)
                step("Checking every statement against the records…" if attempt == 1 else "Checking the corrected answer…", running=True)
                v = verify.check(system=vsystem, asked=asked, written=written, evidence=self._evidence(seeded, messages, turn_start),
                                 freshness=fresh, kind="answer", tools=kit.specs())
                add_usage(usage, v.get("usage") or {})
                passes.append(v)
                if attempt == 1:
                    verification["found"] = len(v["problems"])
                if should_stop and should_stop():
                    return {"cancelled": True}
                if not v["ran"] or not v["problems"] or attempt == 2:
                    break
                # 4 ---- back to the writer, once
                step(f"The check found {len(v['problems'])} point{'s' if len(v['problems']) != 1 else ''} to correct — correcting…", running=True)
                kit.learning = False        # a correction asked for by the checker is not the person teaching us
                messages.append({"role": "user", "content": verify.as_instruction(v["problems"])
                                 + "\n\nWrite the answer again in full, corrected. It replaces the one above; do not mention the check. "
                                   "If you drafted an email, correct it with save_draft using its draft_id."})
                res2 = run_loop(system=system, messages=messages, toolkit=kit, effort=bc.EFFORT_CHAT, max_rounds=6, on_tool=on_tool, should_stop=should_stop)
                add_usage(usage, res2.usage)
                rounds += res2.rounds
                if res2.cancelled:
                    return {"cancelled": True}
                if not res2.text or res2.refused:
                    break
                answer = res2.text
            final = passes[-1]
            open_problems = verify.for_person(final["problems"]) if final.get("ran") else []
            verification = {"ran": bool(final.get("ran")), "passes": len(passes), "statements_checked": final.get("statements_checked", 0),
                            "found": verification["found"], "corrected": max(0, verification["found"] - len(open_problems)) if len(passes) > 1 else 0,
                            "open": open_problems, "error": final.get("error")}
            step(("Checked: " + (f"{verification['statements_checked']} statements, " if verification["statements_checked"] else "")
                  + ("nothing left open" if not open_problems else f"{len(open_problems)} point{'s' if len(open_problems) != 1 else ''} could not be confirmed"))
                 if verification["ran"] else "The check could not be run on this answer", error=not verification["ran"])
        if res.truncated:
            answer += "\n\n_(This answer was cut off at the length limit. Ask me to continue.)_"

        drafts = self._drafts_of(kit)
        # A mechanical check as well: every amount, percentage and reference number against what was read.
        read = checks.texts_of(system, messages) + [kit.evidence_text()] + [f.get("text") or "" for f in files]
        amounts = checks.figures(answer)
        unverified = checks.not_found(answer, read)

        msg = self.store.add_message(thread_id, {
            "role": "assistant", "text": answer, "sources": self._sources(answer, kit, earlier),
            "tool_log": [{"name": "retrieval", "label": f"Full search of the records: {seed_info.get('passages', 0)} passages for “{str(seed_info.get('standalone') or text)[:140]}”",
                          "error": bool(seed_info.get("error"))}] + tool_log,
            "retrieval": kit.search_trace, "drafts": drafts, "verification": verification,
            "actions": [a for a in kit.actions if a.get("type") != "draft"], "usage": usage, "cost_usd": cost_of(usage),
            "elapsed_ms": int((time.time() - started) * 1000), "rounds": rounds, "refused": res.refused,
            "checks": {"amounts": len(amounts), "not_found": unverified[:12]}, "reply_to": user_msg["message_id"],
            "records_as_of": fresh,
        })
        self.store.threads.update_one({"thread_id": thread_id}, {"$set": {"source_chunk_ids": earlier + kit.chunk_ids(), "updated_at": now()}})
        logger.info("brain chat %s: %d round(s), %d lookups, %d check pass(es), %d open, %.1fs, $%s", thread_id, rounds, len(tool_log),
                    verification["passes"], len(verification["open"]), time.time() - started, msg["cost_usd"])
        return msg
