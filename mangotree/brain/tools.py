"""The chat's hands — retrieval, lookups and a few actions.

Retrieval is the firm's full pipeline over the chunk index, unchanged: the
question rewritten to stand alone from the conversation, a hypothetical answer
and three rephrasings, every channel (vector, keyword, phrase, exact match,
filename, knowledge graph, timeline, document-level), fusion, rescoring, two
rerank passes, then widening to neighbouring passages, parent documents and
email threads. It runs for every question before the model answers
(``Toolkit.seed``) and again whenever the model searches.

Lookups are scoped by construction: in a property chat every query carries that
property's filter (plus the portfolio store), so another deal's file cannot come
back. Passages are numbered ``[#N]``; numbers never repeat within a chat thread,
so a citation in an earlier answer still points at the same text.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mangotree.agent.scratchpad import AgentScratchpad
from mangotree.agent.tools import ToolBox, _date_filter
from mangotree.config.registry import PROPERTY_INDEX
from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.retrieve import config as rcfg
from mangotree.retrieve.hits import HIT_PROJECTION, Hit
from mangotree.retrieve.pipeline import HybridSearch
from mangotree.retrieve.scope import Scope
from mangotree.storage.mongo import Mongo

from . import config as bc
from .context import Records, day, property_block
from .lessons import Lessons
from .store import BrainStore, active_property_ids, name_of, now

_hs: Optional[HybridSearch] = None
_hs_lock = threading.Lock()


def hybrid(mongo: Mongo) -> HybridSearch:
    global _hs
    with _hs_lock:
        if _hs is None:
            _hs = HybridSearch(mongo, voyage_api_key=SETTINGS.voyage_api_key, anthropic_api_key=SETTINGS.anthropic_api_key)
        return _hs


def _obj(props: Dict[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required)}


_S = lambda d="": {"type": "string", "description": d}          # noqa: E731
_I = lambda d="": {"type": "integer", "description": d}         # noqa: E731
_B = lambda d="": {"type": "boolean", "description": d}         # noqa: E731
_A = lambda d="": {"type": "array", "items": {"type": "string"}, "description": d}  # noqa: E731


class TurnPad(AgentScratchpad):
    """The passages of one turn, numbered on from where the thread left off.

    Numbers never repeat within a chat thread, so a citation in an earlier answer
    keeps pointing at its passage. But each turn starts with nothing "already
    seen": a passage that matters again is retrieved and shown again, in full,
    rather than being skipped because an earlier question happened to touch it."""

    def __init__(self, base: int = 0):
        super().__init__("chat")
        self.base = int(base or 0)

    def get(self, index: int):
        return super().get(int(index) - self.base)

    def index_of(self, chunk_id: str):
        i = super().index_of(chunk_id)
        return None if i is None else i + self.base

    def add_chunks(self, chunks):
        return [i + self.base for i in super().add_chunks(chunks)]


class Toolkit:
    def __init__(self, mongo: Mongo, store: BrainStore, *, pid: Optional[str], user: Dict[str, Any],
                 base_index: int = 0, conversation: Sequence[dict] = (), allow_actions: bool = True, thread_id: Optional[str] = None,
                 user_text: str = ""):
        self.mongo = mongo
        self.store = store
        self.pid = pid if pid and pid != bc.GENERAL_ID else None
        self.file_id = pid or None
        self.user = user or {}
        self.allow_actions = allow_actions
        self.thread_id = thread_id
        self.records = Records(mongo)
        self.hs = hybrid(mongo)
        self.scope = Scope.for_property(self.pid) if self.pid else Scope.global_()
        self.pad = TurnPad(base_index)
        #: The conversation so far, for the query rewriter: "and what did the city
        #: say about it?" is searched as the standalone question it stands for.
        self.conversation = [{"role": m.get("role", "user"), "content": str(m.get("content") or "")} for m in conversation][-8:]
        self.box = ToolBox(self.hs, self.scope, self.pad, conversation=self.conversation)
        #: What this turn changed, for the UI: drafts written, to-dos touched, notes kept.
        self.actions: List[Dict[str, Any]] = []
        self.search_trace: List[Dict[str, Any]] = []
        #: What the person typed this turn — when he tells the chat to change a draft,
        #: his words and the before/after are what the system learns from.
        self.user_text = user_text
        self.learning = True

    # ------------------------------------------------------------- helpers
    def _target(self, property_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """Which file an action applies to. In a property chat it is that
        property; in the general chat the model must name one."""
        if self.pid:
            return self.pid, None
        pid = (property_id or "").strip()
        if pid in PROPERTY_INDEX or pid == bc.GENERAL_ID:
            return pid, None
        if self.file_id == bc.GENERAL_ID and not pid:
            return bc.GENERAL_ID, None
        return None, f"Name the property with property_id (one of: {', '.join(active_property_ids())})."

    def _cite_emails(self, shas: Sequence[str]) -> Dict[str, int]:
        """Put the opening passage of each email on the pad in one query, so each
        can be cited as [#N]."""
        from mangotree.retrieve.channels import _and
        first: Dict[str, Hit] = {}
        for d in self.mongo.chunks.find(_and({"artifact_sha": {"$in": list(shas)}, "ordinal": {"$lte": 1}}, self.box.base),
                                        HIT_PROJECTION).sort("ordinal", 1):
            first.setdefault(d["artifact_sha"], Hit.from_doc(d))
        hits = [first[s] for s in shas if s in first]
        for h in hits:
            h.label = self.scope.label_for(h)
        self.pad.add_chunks(hits)
        return {h.artifact_sha: self.pad.index_of(h.chunk_id) for h in hits}

    # ----------------------------------------------------------- retrieval
    def _render(self, hits: Sequence[Hit], header: str) -> str:
        """Every passage with its number and where it stands — nothing is left
        out for having been seen in an earlier turn."""
        lines = [header]
        for h in hits:
            idx = self.pad.index_of(h.chunk_id)
            if idx is None:
                continue
            body = f"{h.context}\n{h.text}".strip() if h.context else h.text
            if len(body) > 6000:
                body = body[:6000] + f" … [cut: open_document('#{idx}') for the whole document]"
            lines.append(f"\n[#{idx}] {h.passage_header()}\n{body}")
        return "\n".join(lines)

    def _search_once(self, query: str, *, keep: int, extra_filter: Optional[Dict[str, Any]] = None, depth: int = 1,
                     use_rewrite: bool = True, seen: Optional[set] = None):
        """One pass of the whole retrieval pipeline over the chunk index: the question
        rewritten to stand alone (using the conversation), a hypothetical answer
        and three rephrasings, every channel — vector, keyword, phrase, exact
        match, filename, knowledge graph, timeline, document-level — fused,
        rescored, reranked twice (Voyage, then Opus 5.5), then widened with
        neighbouring passages, parent documents and email threads. Touches no
        shared state, so several can run side by side."""
        t0 = time.time()
        res = self.hs.search(query, self.scope, conversation=self.conversation, seen=set(seen or ()), keep=keep,
                             depth=depth, extra_filter=extra_filter, use_rewrite=use_rewrite)
        rr = res.trace.get("rerank") or {}
        filters = {k: str(v)[:80] for k, v in ((res.rewrite.filters if res.rewrite else None) or {}).items()
                   if k in ("from_email", "date_from", "date_to", "extensions") and v}
        info = {"query": query, "standalone": res.rewrite.standalone if res.rewrite else query,
                "rewrite_degraded": bool(res.rewrite and res.rewrite.degraded), "lists": len(res.trace.get("lists") or {}),
                "fused": res.trace.get("fused"), "reranked": len(res.retrieved), "passages": len(res.hits),
                "relevant": (rr.get("stage2") or {}).get("relevant"), "stage1_ok": (rr.get("stage1") or {}).get("ok"),
                "stage2_ok": (rr.get("stage2") or {}).get("ok"), "route": res.route_reason, "seconds": round(time.time() - t0, 1),
                "hard_filters": filters, "enumeration": res.enumeration.as_dict() if res.enumeration else None}
        return res, info

    def _search_wide(self, query: str, *, keep: int, extra_filter: Optional[Dict[str, Any]] = None, seen: Optional[set] = None):
        """A search, and if it comes back thin, the same search without the filters
        it inferred. The pipeline turns a sender, a date or a file type it reads
        in the question into a hard filter; one that is too narrow returns three
        passages, or none. A thin result is widened, never simply accepted."""
        res, info = self._search_once(query, keep=keep, extra_filter=extra_filter, seen=seen)
        hits = list(res.hits)
        if len(hits) < 8 and (info["hard_filters"] or not hits):
            res2, info2 = self._search_once(query, keep=keep, extra_filter=extra_filter, use_rewrite=False, seen=seen)
            have = {h.chunk_id for h in hits}
            hits += [h for h in res2.hits if h.chunk_id not in have]
            info.update({"widened": True, "narrow_filters": info["hard_filters"], "passages": len(hits),
                         "reranked": info["reranked"] + info2["reranked"], "seconds": round(info["seconds"] + info2["seconds"], 1),
                         "relevant": (info.get("relevant") or 0) + (info2.get("relevant") or 0) if (info.get("relevant") is not None or info2.get("relevant") is not None) else None})
            res = res2 if not res.hits else res
        return res, hits, info

    def _register(self, res, hits: Sequence[Hit], info: Dict[str, Any]) -> List[Hit]:
        """Put a search's passages on this turn's pad; returns those not already shown this turn."""
        try:
            self.box._absorb_trace(res)
        except Exception:
            pass
        for h in hits:
            if not h.label:
                h.label = self.scope.label_for(h)
        before = self.pad.seen_ids
        self.pad.add_chunks(hits)
        self.search_trace.append(info)
        return [h for h in hits if h.chunk_id not in before]

    def full_search(self, query: str, *, keep: int = rcfg.RERANK_STAGE2_KEEP, extra_filter: Optional[Dict[str, Any]] = None) -> Tuple[List[Hit], Dict[str, Any]]:
        res, hits, info = self._search_wide(query, keep=keep, extra_filter=extra_filter, seen=self.pad.seen_ids)
        return self._register(res, hits, info), info

    def seed(self, question: str, *, also: Sequence[str] = ()) -> Tuple[str, Dict[str, Any]]:
        """Run the full retrieval before the model writes a word. Every question and
        every draft starts here.

        ``also`` are further questions searched alongside the first — for a reply,
        one per point it must cover, so that each point is answered from the
        records and not only the email's subject. They run side by side."""
        queries = [q for q in dict.fromkeys([question] + [str(x) for x in also]) if q and q.strip()][:6]
        if self.pid is None and len(queries) > 1:
            # Across all properties one search is run, not one per point: each can fan
            # out over every active property, and five of those is neither quick nor cheap.
            queries = [queries[0] + " In particular: " + "; ".join(q.split(" (")[0] for q in queries[1:])]
        results: List[Any] = [None] * len(queries)

        def one(i: int) -> None:
            try:
                results[i] = self._search_wide(queries[i], keep=rcfg.RERANK_STAGE2_KEEP if i == 0 else 10, seen=set())
            except Exception as exc:
                logger.exception("retrieval failed for %r", queries[i][:80])
                results[i] = exc

        if len(queries) == 1:
            one(0)
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(queries))) as pool:
                list(pool.map(one, range(len(queries))))

        t_total = 0.0
        blocks: List[str] = []
        summary: Dict[str, Any] = {"searches": len(queries), "passages": 0, "relevant": None, "failed": 0, "standalone": question, "seconds": 0.0}
        for i, r in enumerate(results):
            if isinstance(r, Exception) or r is None:
                summary["failed"] += 1
                blocks.append(f"SEARCH FAILED for: {queries[i]} ({type(r).__name__ if r is not None else 'no result'}). "
                              "Nothing was retrieved for this; use search_records before stating anything about it.")
                continue
            res, hits, info = r
            new = self._register(res, hits, info)
            if i == 0:
                summary["standalone"] = info["standalone"]
            summary["passages"] += len(new)
            if info.get("relevant") is not None:
                summary["relevant"] = (summary["relevant"] or 0) + info["relevant"]
            t_total = max(t_total, info["seconds"])
            head = (f"RETRIEVED FROM THE RECORDS for: {info['standalone']}\n"
                    f"({len(hits)} passages: {info['reranked']} ranked best after two rerank passes"
                    + (f", {info['relevant']} judged directly relevant" if info.get("relevant") is not None else "")
                    + "; the rest are their neighbouring passages, parent documents and email threads."
                    + (f" {len(hits) - len(new)} of them are already shown above and are not repeated." if len(new) < len(hits) else "") + ")")
            notes = []
            if info.get("widened"):
                notes.append(f"the first pass was too narrow ({info.get('narrow_filters') or 'no match'}), so the search was widened — check that what follows is on the point")
            if info.get("rewrite_degraded") and not info.get("widened"):
                notes.append("the question could not be rewritten by the model; it was searched as typed")
            if info.get("stage2_ok") is False:
                notes.append("the second rerank pass was unavailable; passages are in first-pass order")
            if notes:
                head += "\nNOTE: " + "; ".join(notes) + "."
            if info.get("enumeration"):
                e = info["enumeration"]
                head += f"\nCOUNT: {e.get('criteria_text')} -> {e.get('denominator')}. Use list_documents for the full list."
            room = bc.SEED_CHARS - sum(len(x) for x in blocks)
            if new and room < 20_000:
                listed = ", ".join(f"[#{self.pad.index_of(h.chunk_id)}] {h.citation[:60]}" for h in new[:40])
                blocks.append(head + f"\n(Not shown for space — {len(new)} passages are on hand by number; open the ones you need with open_document: {listed})")
                continue
            text = self._render(new, head) if new else head + ("\n(nothing in the records matched)" if not hits else "")
            if len(text) > room:
                # Keep whole passages; say how many were held back.
                kept, used = [], len(head)
                for h in new:
                    body_len = len(h.text or "") + len(h.context or "") + 200
                    if used + min(body_len, 6200) > room:
                        break
                    kept.append(h)
                    used += min(body_len, 6200)
                rest = new[len(kept):]
                text = self._render(kept, head) + (f"\n\n(+{len(rest)} more passages on hand, not shown for space: "
                                                   + ", ".join(f"[#{self.pad.index_of(h.chunk_id)}]" for h in rest[:60]) + " — open with open_document.)")
            blocks.append(text)
        summary["seconds"] = round(t_total, 1)
        if summary["failed"] == len(queries):
            summary["error"] = "every search failed"
            return ("RETRIEVAL FAILED. The document search could not be run. Say so plainly, and use search_records before stating "
                    "anything from the records."), summary
        return "\n\n".join(blocks), summary

    # ------------------------------------------------------------- lookups
    def search_records(self, *, query: str, date_from: str = None, date_to: str = None, top_k: int = 12) -> Tuple[str, str]:
        flt = _date_filter(date_from, date_to)
        hits, info = self.full_search(query, keep=max(4, min(int(top_k or 12), 30)), extra_filter=flt)
        head = (f"{len(hits)} new passage(s) for “{info['standalone']}”" + (f" between {date_from or '…'} and {date_to or '…'}" if flt else "")
                + " (passages already shown in this turn are not repeated)")
        if not hits:
            return (head + ". Nothing further matched — what is relevant may already be in front of you, or the records do not "
                    "contain it. Try different words, or list_documents / recent_mail for a deterministic look."), f"Searched: {query}"
        return self._render(hits, head), f"Searched: {query}"

    def recent_mail(self, *, days: int = 7, from_contains: str = "", direction: str = "", with_body: bool = True,
                    limit: int = 12, property_id: str = None) -> Tuple[str, str]:
        pid = self.pid or (property_id if property_id in PROPERTY_INDEX else None) or bc.GENERAL_ID
        rows = self.records.recent_mail(pid, days=max(1, min(int(days or 7), 120)), limit=max(1, min(int(limit or 12), 40)),
                                        from_contains=from_contains or "", direction=direction if direction in ("sent", "received") else "")
        label = f"Read the last {days} days of mail" + (f" from {from_contains}" if from_contains else "")
        if not rows:
            return f"No emails in the last {days} days" + (f" from “{from_contains}”" if from_contains else "") + f" for {name_of(pid)}.", label
        dirs = self.records.directions([r["sha256"] for r in rows])
        out = [f"{len(rows)} email(s), newest first, for {name_of(pid)}:"]
        nums = self._cite_emails([r["sha256"] for r in rows])
        for r in rows:
            n = nums.get(r["sha256"])
            tag = f"[#{n}] " if n else ""
            d = dirs.get(r["sha256"], "received")
            if with_body:
                out.append("\n" + tag + self.records.render_email(r, direction=d))
            else:
                out.append(tag + self.records.email_header(r, d))
        return "\n".join(out), label

    def open_document(self, *, ref: str) -> Tuple[str, str]:
        r = self.box.tool_fetch_full_document(ref=str(ref), max_tokens=60_000)
        return r.content, f"Opened: {r.summary.split(':', 1)[-1].split('—')[0].strip()[:80]}"

    def email_thread(self, *, ref: str) -> Tuple[str, str]:
        r = self.box.tool_thread_context(ref=str(ref))
        return r.content, "Read the email conversation"

    def list_documents(self, **kw) -> Tuple[str, str]:
        allowed = ("property_ids", "extensions", "from_email", "date_from", "date_to", "filename_pattern", "subject_pattern",
                   "source_type", "limit", "doc_classes")
        r = self.box.tool_fetch_documents(**{k: v for k, v in kw.items() if k in allowed and v not in (None, "", [])})
        return r.content, "Listed matching documents"

    def timeline(self, *, date_from: str = None, date_to: str = None, event_types: List[str] = None, property_ids: List[str] = None) -> Tuple[str, str]:
        r = self.box.tool_timeline(property_ids=property_ids, date_from=date_from, date_to=date_to, event_types=event_types, limit=80)
        return r.content, "Checked the timeline"

    def find_exact(self, *, text: str) -> Tuple[str, str]:
        r = self.box.tool_find_quote(text=text)
        return r.content, f"Looked for the exact text “{text[:40]}”"

    def money_movements(self, *, date_from: str = None, date_to: str = None, property_ids: List[str] = None) -> Tuple[str, str]:
        r = self.box.tool_flow_of_funds(property_ids=property_ids, date_from=date_from, date_to=date_to)
        return r.content, "Traced the money movements"

    def property_file(self, *, property_id: str) -> Tuple[str, str]:
        if property_id not in PROPERTY_INDEX and property_id != bc.GENERAL_ID:
            return f"Unknown property_id {property_id!r}. Use one of: {', '.join(active_property_ids())}.", "Opened a property file"
        # The correspondence itself comes with the file, so a question asked from the
        # all-properties chat is answered from the emails, not from a summary of them.
        text = "\n\n".join([self.records.mail_block(property_id), property_block(self.mongo, self.store, property_id)])
        return text, f"Opened the file and recent mail for {name_of(property_id)}"

    # ------------------------------------------------------------- actions
    def _people(self, entries: Sequence[str]) -> List[Dict[str, str]]:
        out = []
        by_addr = self.records.people_by_address()
        for e in entries or []:
            e = str(e or "").strip()
            if not e:
                continue
            m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", e)
            if m:
                addr = m.group(0).lower()
                out.append({"name": (by_addr.get(addr) or {}).get("display_name") or e.replace(m.group(0), "").strip(" <>") or addr, "email": addr})
                continue
            match = next((a for a, p in by_addr.items() if e.lower() in str(p.get("display_name") or "").lower()), None)
            out.append({"name": (by_addr.get(match) or {}).get("display_name") or e, "email": match or ""})
        return out

    def save_draft(self, *, to: List[str], subject: str, body: str, cc: List[str] = None, draft_id: str = None,
                   property_id: str = None, reply_to: str = None, note: str = "") -> Tuple[str, str]:
        pid, err = self._target(property_id)
        if err and not draft_id:
            return err, "Draft not saved"
        fields = {"to": self._people(to), "cc": self._people(cc or []), "subject": str(subject or "").strip()[:300],
                  "body": str(body or "").strip(), "note": str(note or "").strip()[:600]}
        if draft_id:
            existing = self.store.get_draft(draft_id)
            if not existing:
                return f"No draft with id {draft_id}.", "Draft not saved"
            d = self.store.update_draft(draft_id, {**fields, "status": "ready", "edited": True})
            self.actions.append({"type": "draft", "draft_id": draft_id, "revised": True})
            if self.learning and self.user_text:
                Lessons(self.mongo, self.store).learn_async(draft=existing, final_text=fields["body"], before=existing.get("body") or "",
                                                            source="chat_instruction", instruction=self.user_text)
            return f"Draft {draft_id} updated.", f"Revised the draft “{d.get('subject')}”"
        reply_sha = self.box._resolve_sha(reply_to) if reply_to else None
        art = self.mongo.artifacts.find_one({"sha256": reply_sha}, {"thread_key": 1}) if reply_sha else None
        d = self.store.create_draft({**fields, "property_ids": [pid] if pid else [], "reply_to_sha": reply_sha,
                                     "thread_key": (art or {}).get("thread_key"), "sender": self.user.get("user_id") or bc.DEFAULT_SENDER,
                                     "origin": "chat", "thread_id": self.thread_id, "created_by": self.user.get("user_id")})
        self.actions.append({"type": "draft", "draft_id": d["draft_id"]})
        missing = [t["name"] for t in fields["to"] if not t.get("email")]
        warn = f" No email address on file for: {', '.join(missing)} — tell the user." if missing else ""
        return f"Draft saved as {d['draft_id']}. It is shown to the user as a card; do not paste it again.{warn}", f"Wrote the draft “{fields['subject']}”"

    def add_todo(self, *, title: str, owner: str = "rakesh", due: str = None, detail: str = "", kind: str = "action",
                 property_id: str = None) -> Tuple[str, str]:
        pid, err = self._target(property_id)
        if err:
            return err, "To-do not added"
        if not self.store.get_file(pid):
            self.store.save_file(pid, {"open_items": []})
        item = self.store.add_item(pid, title=title, detail=detail, owner=owner, kind=kind, due=due, by=self.user.get("user_id") or "chat")
        self.actions.append({"type": "todo_added", "id": item["id"], "property_id": pid, "title": item["title"]})
        return f"Added [{item['id']}] to {name_of(pid)}.", f"Added a to-do: {item['title']}"

    def close_todo(self, *, id: str, note: str = "", property_id: str = None) -> Tuple[str, str]:
        pid, err = self._target(property_id)
        if err:
            # The id is unique enough to find its file.
            f = self.store.files.find_one({"open_items.id": id}, {"property_id": 1})
            pid = (f or {}).get("property_id")
            if not pid:
                return err, "To-do not closed"
        ok = self.store.close_item(pid, id, note=note, by=self.user.get("user_id") or "chat")
        if ok:
            self.actions.append({"type": "todo_closed", "id": id, "property_id": pid})
        return (f"Closed {id}." if ok else f"No open item {id} on {name_of(pid)}."), "Closed a to-do"

    def note_to_file(self, *, text: str, property_id: str = None) -> Tuple[str, str]:
        pid, err = self._target(property_id)
        if err:
            return err, "Note not kept"
        who = self.user.get("name") or self.user.get("user_id") or "team"
        self.store.add_activity(pid, at=now(), kind="said_in_chat", who=who, title=f"{who} said", summary=text, relevance="material")
        self.mongo.db["reported_facts"].insert_one({"property_id": pid if pid != bc.GENERAL_ID else None, "text": str(text)[:1000],
                                                    "by": self.user.get("user_id"), "by_name": who, "role": self.user.get("role"), "at": now(), "source": "brain_chat"})
        self.actions.append({"type": "note", "property_id": pid})
        return f"Recorded on the file for {name_of(pid)}.", "Recorded what you said on the file"

    def remember(self, *, text: str, all_properties: bool = False, property_id: str = None) -> Tuple[str, str]:
        pid, err = self._target(property_id)
        if err and not all_properties:
            return err, "Not remembered"
        is_ceo = self.user.get("role") == "ceo"
        scope_global = bool(all_properties) or pid == bc.GENERAL_ID
        self.mongo.db["remember_notes"].insert_one({
            "note_id": now().strftime("n%Y%m%d%H%M%S%f"), "text": str(text).strip(), "scope": "global" if scope_global else "property",
            "property_id": None if scope_global else pid, "author": self.user.get("name") or "team", "author_id": self.user.get("user_id"),
            "created_at": now(), "active": True, "status": "active" if is_ceo else "pending", "instant": is_ceo, "source": "brain_chat"})
        self.actions.append({"type": "remember"})
        return ("Stored as a standing instruction." if is_ceo else "Stored; it takes effect once Rakesh Sir approves it."), "Stored a standing instruction"

    # --------------------------------------------------------------- specs
    def specs(self) -> List[Dict[str, Any]]:
        general = self.pid is None
        pid_prop = {"property_id": _S("which property (id from the list of active properties)")} if general else {}
        tools: List[Dict[str, Any]] = [
            {"name": "search_records", "description": "Search the emails, attachments and documents on file for a topic. Returns numbered passages. "
             "Write the query as the words the document would use. Optional dates narrow it.",
             "input_schema": _obj({"query": _S("what to look for"), "date_from": _S("YYYY-MM-DD"), "date_to": _S("YYYY-MM-DD"), "top_k": _I("passages, 4-20")}, ["query"])},
            {"name": "recent_mail", "description": "The actual emails of the last N days, newest first — received and sent — optionally only those from one "
             "person. The reliable way to find 'the latest email from X' or to see exactly what was said this week.",
             "input_schema": _obj({"days": _I("how far back, default 7"), "from_contains": _S("a name or address fragment, e.g. 'kelly'"),
                                   "direction": _S("'received' or 'sent'; omit for both"), "with_body": _B("include the text (default true)"),
                                   "limit": _I("max emails, default 12"), **pid_prop})},
            {"name": "open_document", "description": "Read a whole document or email in order. Pass a passage number like '#12', a sha tag like 'a1b2c3d4e5f6', or a filename.",
             "input_schema": _obj({"ref": _S("#N, sha tag, or filename")}, ["ref"])},
            {"name": "email_thread", "description": "The whole conversation around an email, with the attachments it carried.",
             "input_schema": _obj({"ref": _S("#N or sha tag of any message in it")}, ["ref"])},
            {"name": "list_documents", "description": "List documents by what they are, not by similarity: by filename or subject pattern, sender, file type, date range. "
             "Gives a complete list with a count — use it for 'all', 'every', 'is there any'.",
             "input_schema": _obj({"filename_pattern": _S("regex on filename"), "subject_pattern": _S("regex on email subject"), "from_email": _S("address or name fragment"),
                                   "extensions": _A("e.g. ['.pdf', '.xlsx']"), "date_from": _S("YYYY-MM-DD"), "date_to": _S("YYYY-MM-DD"),
                                   "source_type": _S("email | attachment | disk_file | upload"), "limit": _I("max 200"),
                                   **({"property_ids": _A("property ids")} if general else {})})},
            {"name": "timeline", "description": "Dated events on the property — funding, payments, permits, legal steps, sale activity — each tied to its source.",
             "input_schema": _obj({"date_from": _S("YYYY-MM-DD"), "date_to": _S("YYYY-MM-DD"), "event_types": _A("optional event types"),
                                   **({"property_ids": _A("property ids")} if general else {})})},
            {"name": "money_movements", "description": "Dated money events in order with totals by type — draws, payments, payoffs, invoices — from the records.",
             "input_schema": _obj({"date_from": _S("YYYY-MM-DD"), "date_to": _S("YYYY-MM-DD"), **({"property_ids": _A("property ids")} if general else {})})},
            {"name": "find_exact", "description": "Find an exact phrase, number or amount in the records and show the text around it.",
             "input_schema": _obj({"text": _S("the exact text")}, ["text"])},
        ]
        if general:
            tools.append({"name": "property_file", "description": "Open the full file for one property: status, open items, money, the last week.",
                          "input_schema": _obj({"property_id": _S("property id")}, ["property_id"])})
        if self.allow_actions:
            tools += [
                {"name": "save_draft", "description": "Write an email for the user to review and send. Nothing is sent. The draft is shown to them as an editable card. "
                 "To change a draft you wrote earlier, pass its draft_id with the full new text.",
                 "input_schema": _obj({"to": _A("recipients: email addresses, or names of people on file"), "cc": _A("copied recipients"),
                                       "subject": _S("subject line"), "body": _S("the complete email as plain text, greeting to sign-off"),
                                       "reply_to": _S("#N or sha tag of the email being answered, if any"),
                                       "draft_id": _S("only when revising an existing draft"), "note": _S("one line for the user: what to check before sending"),
                                       **pid_prop}, ["to", "subject", "body"])},
                {"name": "add_todo", "description": "Add an open item to the property file.",
                 "input_schema": _obj({"title": _S("one line, starting with a verb"), "owner": _S("rakesh | jp | manjunath | wes | kelly | other"),
                                       "due": _S("YYYY-MM-DD if there is a date"), "detail": _S("one sentence, optional"),
                                       "kind": _S("action | reply | decision | waiting"), **pid_prop}, ["title"])},
                {"name": "close_todo", "description": "Close an open item by its id, with a short note on what settled it.",
                 "input_schema": _obj({"id": _S("the item id, e.g. i-3f2a…"), "note": _S("what settled it"), **pid_prop}, ["id"])},
                {"name": "note_to_file", "description": "Record on the property file something the user just told you happened or decided, in their words, so it is not lost.",
                 "input_schema": _obj({"text": _S("what they said, as a plain statement with the date if given"), **pid_prop}, ["text"])},
                {"name": "remember", "description": "Store a standing instruction from the user that should govern this property (or all properties) from now on.",
                 "input_schema": _obj({"text": _S("the instruction, in their words"), "all_properties": _B("true if it applies to every property"), **pid_prop}, ["text"])},
            ]
        return tools

    def run(self, name: str, args: Dict[str, Any]) -> Tuple[str, bool, str]:
        """Execute one tool call. Returns (content, is_error, plain-words label)."""
        fn = getattr(self, name, None)
        known = {t["name"] for t in self.specs()}
        if name not in known or fn is None:
            return f"Unknown tool {name}.", True, name
        try:
            content, label = fn(**{k: v for k, v in (args or {}).items() if v is not None})
            return content or "(nothing returned)", False, label
        except TypeError as exc:
            return f"Bad arguments for {name}: {exc}", True, name
        except Exception as exc:
            logger.exception("brain tool %s failed", name)
            return f"{name} failed: {type(exc).__name__}: {str(exc)[:300]}", True, name

    # ------------------------------------------------------------- sources
    def chunk_ids(self) -> List[str]:
        """This turn's passages in number order (number = base + position + 1)."""
        return [h.chunk_id for h in self.pad.chunks]

    def cited(self, text: str) -> List[Dict[str, Any]]:
        out = []
        for n in sorted({int(m) for m in re.findall(r"\[#(\d+)\]", text or "")}):
            h = self.pad.get(n)
            if h:
                out.append({"index": n, "chunk_id": h.chunk_id, "artifact_sha": h.artifact_sha, "citation": h.citation,
                            "display_name": h.display_name, "date": h.date_str, "placement": h.placement, "label": h.label,
                            "property_ids": h.property_ids, "origin": h.origin})
        return out

    def evidence_text(self) -> str:
        return "\n".join((h.context or "") + "\n" + (h.text or "") for h in self.pad.chunks)
