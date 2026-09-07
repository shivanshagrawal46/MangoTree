"""The property dossier — the investigation every automated pass reads first.

Admin directive (2026-09-03): before Fable 5.1 writes the Wes issues, or Opus 5
writes tasks and change cards for a property, the model must have the same
context a person answering a question would have — a full agent investigation,
the property chat's rolling summary, and the standing "remember" notes for that
property. Otherwise each pass judges a week of new mail without knowing what
happened before it, and raises as new something Rakesh Sir settled in a chat.

So one investigation per property, shared:

    dossier = PropertyDossier(mongo, ...).build(pid)     # cached, refreshed when stale
    prompt += dossier["block"]                            # the text every consumer injects

What is in it
    * the Opus 5 agent's answer to the standing question "where does this deal
      stand" — run through the same tool loop, seed search, sufficiency gate and
      byte-for-byte verifier as a chat answer (no GPT second reader; that stage
      exists for a human's question, not a nightly pass), with its open items,
      cited risks and the documents it relied on
    * the rolling summary of the property chat — decisions and instructions with
      who gave them
    * active remember-notes (global + this property)
    * recent human decisions: dismissed cards with remarks, tasks closed by hand

Freshness: rebuilt when older than ``MAX_AGE_HOURS`` or when new documents
arrive (the arrival chain forces it before tasks / cards / agenda). Stored in
``dossiers`` so the UI can show what the models were told.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

#: A dossier is rebuilt every morning and whenever new documents arrive for the
#: property; between those, a consumer reuses it. Twelve hours only matters if
#: both of those failed to run.
MAX_AGE_HOURS = float(os.environ.get("MT_DOSSIER_MAX_AGE_H", "12"))
# The morning pass re-investigates a property whose dossier is older than this
# even if nothing arrived — so the picture is never more than a week old.
MAX_STALE_DAYS = float(os.environ.get("MT_DOSSIER_MAX_STALE_DAYS", "7"))


def cached_block(mongo: Mongo, pid: str, *, max_chars: int = 6000) -> str:
    """The stored dossier block, whatever its age — and nothing if none exists.

    For passes that must stay fast (the resolution pass runs before every
    regeneration): they read what the last investigation wrote and never
    trigger a new 14-minute one themselves."""
    doc = mongo.db["dossiers"].find_one({"property_id": pid}, {"block": 1, "built_at": 1})
    if not doc or not doc.get("block"):
        return ""
    return f"(investigation as of {doc['built_at']:%Y-%m-%d %H:%M} UTC)\n" + doc["block"][:max_chars]


def context_for(mongo: Mongo, pid: str, *, force: bool = False) -> str:
    """The dossier block for consumers (tasks, cards, agenda).

    Reads what exists, at any age. Rebuilding is the job of the morning pass and
    the arrival chain, which force it deliberately; a consumer must never start a
    14-minute investigation as a side effect of someone pressing Refresh. Only
    when no dossier exists at all is one built here.
    """
    from mangotree.config.settings import SETTINGS
    d = PropertyDossier(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key,
                        openai_api_key=SETTINGS.openai_api_key_critic or "")
    if force:
        return d.block(pid, force=True)
    existing = mongo.db["dossiers"].find_one({"property_id": pid}, {"block": 1, "built_at": 1})
    if existing and existing.get("block"):
        return f"(investigation as of {existing['built_at']:%Y-%m-%d %H:%M} UTC)\n" + existing["block"]
    return d.block(pid)

#: The standing question. Phrased so the agent's sufficiency checklist covers
#: money, commitments, deadlines, risks and what has NOT happened.
QUESTION = (
    "Give the current state of this deal as of today for the CEO: (1) what has happened in the "
    "last 30 days and what is pending; (2) every open commitment — by the borrower, the contractor "
    "Wes / Listing Profit, title, counsel — with its date and whether it was met; (3) money: what RKB "
    "funded, what has been received, what is owed and as of when, any payoff or closing in motion; "
    "(4) deadlines in the next 60 days; (5) risks to repayment or collateral; (6) what the records "
    "show has NOT happened that should have. Cite documents for every fact."
)


class PropertyDossier:
    _lock = threading.Lock()
    _building: set = set()

    def __init__(self, mongo: Mongo, *, anthropic_api_key: str, voyage_api_key: str, openai_api_key: str = "",
                 model: Optional[str] = None, max_tool_calls: Optional[int] = None):
        from mangotree.config.models import Seat, model_for
        from mangotree.retrieve import config as cfg
        self.mongo = mongo
        self.keys = dict(anthropic_api_key=anthropic_api_key, voyage_api_key=voyage_api_key, openai_api_key=openai_api_key)
        #: Who reads: Fable 5.1 by default (FINANCE seat). Overridable so the same
        #: property can be investigated by another model for a cost comparison.
        self.model = model or model_for(Seat.FINANCE)
        self.max_tool_calls = max_tool_calls or cfg.MORNING_MAX_TOOL_CALLS
        self.coll = mongo.db["dossiers"]
        self.coll.create_index("property_id", unique=True, name="ux_dossier_property")

    # ------------------------------------------------------------- memory
    def _memory(self, pid: str) -> Dict[str, Any]:
        chat = self.mongo.db["chats"].find_one({"kind": "property", "property_id": pid}, {"summary": 1, "summary_at": 1, "messages": {"$slice": -6}})
        notes = list(self.mongo.db["remember_notes"].find(
            {"active": {"$ne": False}, "status": {"$ne": "pending"},
             "$or": [{"scope": "global"}, {"scope": "property", "property_id": pid}]},
            {"_id": 0, "text": 1, "author": 1, "created_at": 1, "scope": 1}).sort("created_at", -1).limit(30))
        dismissals = list(self.mongo.db["corrections"].find({"kind": "card_dismissal", "property_id": pid}, {"_id": 0, "title": 1, "remark": 1, "at": 1, "by": 1}).sort("at", -1).limit(12))
        # Closed by a person (done_by is a user id), not by the extractor.
        closed = list(self.mongo.db["tasks"].find({"property_id": pid, "status": {"$in": ["done", "dismissed"]}, "done_by": {"$in": self._user_ids()}},
                                                  {"_id": 0, "title": 1, "status": 1, "done_by": 1, "done_at": 1, "last_remark": 1}).sort("updated_at", -1).limit(12))
        recent_qa = []
        for m in (chat or {}).get("messages") or []:
            if m.get("role") == "user":
                recent_qa.append({"who": m.get("by") or "user", "asked": str(m.get("content") or "")[:300], "at": m.get("at")})
        return {"chat_summary": (chat or {}).get("summary"), "chat_summary_at": (chat or {}).get("summary_at"),
                "remember_notes": notes, "dismissals": dismissals, "closed_tasks": closed, "recent_questions": recent_qa[-4:]}

    # -------------------------------------------------------- investigate
    def _investigate(self, pid: str) -> Dict[str, Any]:
        from mangotree.agent.agent import Agent
        from mangotree.agent.scratchpad import BudgetTracker
        from mangotree.retrieve import config as cfg
        from mangotree.retrieve.scope import Scope
        # Fable 5.1 reads for itself (admin directive 2026-09-05): the model that
        # writes the issues and the ledger does the property investigation, so its
        # picture of the deal is its own, not a summary handed over from Opus.
        # Capped at MORNING_MAX_TOOL_CALLS (20, admin directive 2026-09-07).
        agent = Agent(self.mongo, **self.keys, model=self.model, reasoning_effort="high")
        budget = BudgetTracker(max_tool_calls=self.max_tool_calls, max_wall_clock_s=float(cfg.MORNING_MAX_WALL_CLOCK_S))
        res = agent.run(QUESTION, Scope.for_property(pid), critique=False, skeptic=True, budget=budget)
        sources = []
        seen = set()
        for h in res.chunks[:60]:
            sha = getattr(h, "artifact_sha", None)
            if sha and sha not in seen:
                seen.add(sha)
                sources.append({"sha256": sha, "name": getattr(h, "display_name", None) or getattr(h, "filename", None), "date": getattr(h, "date", None)})
        return {
            "model": agent.model,
            "answer": res.answer, "open_items": list(res.open_items or []), "risks": list(res.risks or []),
            "coverage": res.coverage, "verification": res.verification, "outcome": res.outcome, "forced_reason": res.forced_reason,
            "steps": len(res.steps or []), "elapsed_ms": res.elapsed_ms, "sources": sources[:40],
            # Token counts and cost, so a morning pass can be priced from the database:
            # planner turns under the top-level keys; every call the run caused
            # (rewrite, rerank, skeptic, verifier) under "run", by model.
            "budget": {k: (res.budget or {}).get(k) for k in ("tool_calls_used", "max_tool_calls", "input_tokens", "cache_read_tokens",
                                                             "cache_write_tokens", "output_tokens", "context_tokens_last",
                                                             "planner_cost_usd", "elapsed_s", "run")},
        }

    # ------------------------------------------------------------- freshness
    def changed_since(self, pid: str, since: datetime) -> Optional[str]:
        """What, if anything, arrived for this property after ``since``.

        Returns a short reason or None. The morning pass uses this to skip the
        investigation for a property nothing happened to: an investigation is
        20–34 model turns over a conversation that grows to 100–280k tokens, and
        on 2026-09-06 all fifteen were re-run (twice) with zero new documents."""
        db = self.mongo.db
        if self.mongo.artifacts.count_documents(
                {"property_ids": pid, "is_inline_image": {"$ne": True},
                 "$or": [{"created_at": {"$gt": since}}, {"date": {"$gt": since}}]}, limit=1):
            return "new documents"
        if db["chats"].count_documents({"kind": "property", "property_id": pid, "messages": {"$elemMatch": {"at": {"$gt": since}}}}, limit=1):
            return "new chat"
        if db["remember_notes"].count_documents({"active": {"$ne": False}, "created_at": {"$gt": since},
                                                 "$or": [{"scope": "global"}, {"scope": "property", "property_id": pid}]}, limit=1):
            return "new remember note"
        if db["corrections"].count_documents({"property_id": pid, "at": {"$gt": since}}, limit=1):
            return "new human correction"
        if db["tasks"].count_documents({"property_id": pid, "updated_at": {"$gt": since}, "done_by": {"$in": self._user_ids()}}, limit=1):
            return "task closed by a person"
        return None

    def _user_ids(self) -> List[str]:
        """Login ids of real people — the automatic passes close tasks as 'dedupe',
        'evidence', 'opus-5' and must not count as a human change."""
        return [u["user_id"] for u in self.mongo.db["users"].find({}, {"user_id": 1}) if u.get("user_id")]

    def refresh_if_changed(self, pid: str, *, max_stale_days: float = MAX_STALE_DAYS) -> Dict[str, Any]:
        """Rebuild when the property changed since the last dossier, or the dossier
        is older than ``max_stale_days``; otherwise return the existing one."""
        existing = self.coll.find_one({"property_id": pid}, {"_id": 0})
        built = (existing or {}).get("built_at")
        if not existing or not built:
            return self.build(pid, force=True)
        now = datetime.now(timezone.utc)
        reason = self.changed_since(pid, built)
        if reason is None and (now - built) < timedelta(days=max_stale_days):
            logger.info("dossier %s: unchanged since %s — kept", pid, f"{built:%m-%d %H:%M}")
            self.coll.update_one({"property_id": pid}, {"$set": {"checked_at": now, "kept_reason": "unchanged"}})
            return existing
        logger.info("dossier %s: rebuilding (%s)", pid, reason or f"older than {max_stale_days:g} days")
        doc = self.build(pid, force=True)
        doc["rebuild_reason"] = reason or "stale"
        return doc

    # ---------------------------------------------------------------- build
    def build(self, pid: str, *, force: bool = False, max_age_hours: float = MAX_AGE_HOURS) -> Dict[str, Any]:
        if pid not in PROPERTY_INDEX:
            raise ValueError(pid)
        now = datetime.now(timezone.utc)
        existing = self.coll.find_one({"property_id": pid}, {"_id": 0})
        if existing and not force and existing.get("built_at") and (now - existing["built_at"]) < timedelta(hours=max_age_hours):
            return existing
        # One build per property at a time; a second caller waits for the first.
        with self._lock:
            already = pid in self._building
            if not already:
                self._building.add(pid)
        if already:
            import time
            for _ in range(600):
                time.sleep(2)
                with self._lock:
                    if pid not in self._building:
                        break
            return self.coll.find_one({"property_id": pid}, {"_id": 0}) or existing or {}
        try:
            logger.info("dossier %s: investigating", pid)
            inv = self._investigate(pid)
            mem = self._memory(pid)
            doc = {"property_id": pid, "built_at": datetime.now(timezone.utc), "question": QUESTION,
                   "investigation": inv, "memory": mem}
            doc["block"] = self.render(doc)
            self.coll.update_one({"property_id": pid}, {"$set": doc}, upsert=True)
            logger.info("dossier %s: done in %.0fs, %d steps, outcome=%s", pid, inv["elapsed_ms"] / 1000, inv["steps"], inv["outcome"])
            return doc
        except Exception:
            logger.exception("dossier build failed for %s", pid)
            if existing:
                return existing
            raise
        finally:
            with self._lock:
                self._building.discard(pid)

    # --------------------------------------------------------------- render
    @staticmethod
    def render(doc: Dict[str, Any]) -> str:
        inv = doc.get("investigation") or {}
        mem = doc.get("memory") or {}
        parts = [f"=== INVESTIGATION — where this deal stands ({(doc.get('investigation') or {}).get('model') or 'agent'}, {doc['built_at']:%Y-%m-%d %H:%M} UTC, "
                 f"{inv.get('steps', 0)} tool steps, {(inv.get('verification') or {}).get('verified', '?')}/{(inv.get('verification') or {}).get('facts', '?')} facts verified) ==="]
        parts.append((inv.get("answer") or "").strip()[:9000])
        if inv.get("risks"):
            parts.append("\nRISKS the investigation cited:")
            parts += [f"- {r}" for r in inv["risks"][:10]]
        if inv.get("open_items"):
            parts.append("\nOPEN ITEMS the investigation could not settle:")
            parts += [f"- {o}" for o in inv["open_items"][:10]]
        if mem.get("chat_summary"):
            parts.append(f"\n=== WHAT HAS BEEN DISCUSSED AND DECIDED (rolling summary of the property chat, {str(mem.get('chat_summary_at'))[:10]}) ===")
            parts.append(str(mem["chat_summary"])[:3000])
        if mem.get("remember_notes"):
            parts.append("\n=== STANDING INSTRUCTIONS (remember notes — these override inference) ===")
            parts += [f"- [{n.get('scope')}] {n.get('text')} — {n.get('author')}, {str(n.get('created_at'))[:10]}" for n in mem["remember_notes"]]
        if mem.get("dismissals"):
            parts.append("\n=== JUDGED NOISE BEFORE (dismissed cards with remark — do not raise again) ===")
            parts += [f"- {d.get('title')}: {d.get('remark')}" for d in mem["dismissals"]]
        if mem.get("closed_tasks"):
            parts.append("\n=== TASKS CLOSED BY A PERSON (done or dismissed — not open) ===")
            parts += [f"- [{t.get('status')}] {t.get('title')}" + (f" — {t.get('last_remark')}" if t.get("last_remark") else "") for t in mem["closed_tasks"]]
        if mem.get("recent_questions"):
            parts.append("\n=== WHAT PEOPLE ASKED RECENTLY ===")
            parts += [f"- {q.get('who')}: {q.get('asked')}" for q in mem["recent_questions"]]
        return "\n".join(parts)

    def block(self, pid: str, *, force: bool = False) -> str:
        """The injectable context, building if needed. Never raises into a consumer:
        a failed investigation yields a short note instead of blocking tasks or cards."""
        try:
            doc = self.build(pid, force=force)
            return doc.get("block") or self.render(doc)
        except Exception as exc:
            return f"=== INVESTIGATION unavailable ({type(exc).__name__}) — judge from the records below only ==="
