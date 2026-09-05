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
    """Convenience for consumers: the dossier block, keys taken from settings."""
    from mangotree.config.settings import SETTINGS
    return PropertyDossier(mongo, anthropic_api_key=SETTINGS.anthropic_api_key, voyage_api_key=SETTINGS.voyage_api_key,
                           openai_api_key=SETTINGS.openai_api_key_critic or "").block(pid, force=force)

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

    def __init__(self, mongo: Mongo, *, anthropic_api_key: str, voyage_api_key: str, openai_api_key: str = ""):
        self.mongo = mongo
        self.keys = dict(anthropic_api_key=anthropic_api_key, voyage_api_key=voyage_api_key, openai_api_key=openai_api_key)
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
        closed = list(self.mongo.db["tasks"].find({"property_id": pid, "status": {"$in": ["done", "dismissed"]}, "done_by": {"$nin": [None, "opus-5"]}},
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
        from mangotree.retrieve.scope import Scope
        agent = Agent(self.mongo, **self.keys)
        res = agent.run(QUESTION, Scope.for_property(pid), critique=False, skeptic=True)
        sources = []
        seen = set()
        for h in res.chunks[:60]:
            sha = getattr(h, "artifact_sha", None)
            if sha and sha not in seen:
                seen.add(sha)
                sources.append({"sha256": sha, "name": getattr(h, "display_name", None) or getattr(h, "filename", None), "date": getattr(h, "date", None)})
        return {
            "answer": res.answer, "open_items": list(res.open_items or []), "risks": list(res.risks or []),
            "coverage": res.coverage, "verification": res.verification, "outcome": res.outcome, "forced_reason": res.forced_reason,
            "steps": len(res.steps or []), "elapsed_ms": res.elapsed_ms, "sources": sources[:40],
        }

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
        parts = [f"=== INVESTIGATION — where this deal stands (Opus 5 agent, {doc['built_at']:%Y-%m-%d %H:%M} UTC, "
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
