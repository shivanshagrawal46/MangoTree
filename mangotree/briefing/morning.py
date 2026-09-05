"""The morning briefing — every user starts the day already briefed.

Assembled from the records, then written by Opus 5 in plain words: overnight
intake, health changes, tasks by urgency for this reader, deadlines in the next
two weeks, overdue commitments, the significant new cards. Ninety seconds to
read; every line carries a source. Stored per user per day; the scheduler
writes it before 6 a.m. and it can be regenerated on demand.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core.logging import logger
from mangotree.retrieve import config as cfg
from mangotree.storage.mongo import Mongo

_SYSTEM = """You write the morning briefing for one person at RKB Consulting Group, a
renovation lender. You get the structured FACTS gathered from the records
overnight. Write the brief in plain, direct words — ninety seconds to read.

Reader profiles:
  Rakesh (CEO)  — leads with decisions and money at risk
  JP (accountant) — leads with approvals, payoffs, wires, reconciliations
  Manjunath (operations) — leads with documents due, filings, follow-ups

Return JSON only:
{"headline": "one sentence: the single most important thing today",
 "sections": [
   {"title": "Needs you today", "items": [{"text": "…", "urgency": "critical|high|normal|info|good", "property_id": "…|null", "source_sha": "…|null"}]},
   {"title": "Money", "items": [...]},
   {"title": "Deadlines — next 14 days", "items": [...]},
   {"title": "What changed overnight", "items": [...]},
   {"title": "Handled for you", "items": [...]}
 ],
 "closing": "one calm sentence"}
Rules: only what the facts contain; keep source_sha from the facts on each item;
short sentences; no jargon; at most 5 items per section; drop empty sections.
Money rule: a dollar figure may appear ONLY if it is present in the facts as a
ledger figure (invested, owed, owed_as_of) or on a dated event. Where a property's
money_established is false, say "not established in documents" — never write 0,
never estimate, never round a figure that is not there. Quote owed figures with
their as-of date."""


def _json(raw: str) -> dict:
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.S)
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0) if m else txt)


class Briefing:
    def __init__(self, mongo: Mongo, *, anthropic_api_key: str, model: Optional[str] = None):
        import anthropic

        self.mongo = mongo
        self.client = anthropic.Anthropic(api_key=anthropic_api_key, max_retries=4)
        self.model = model or cfg.AGENT_PLANNER_MODEL
        self.coll = mongo.db["briefings"]
        self.coll.create_index([("user_id", 1), ("day", 1)], unique=True, name="ux_brief_user_day")

    # ------------------------------------------------------------------ facts
    def facts(self, user_id: str) -> Dict[str, Any]:
        from mangotree.api import data
        from mangotree.tasks.store import TaskStore

        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=36)
        owner = {"rakesh": "Rakesh", "jp": "JP", "manjunath": "Manjunath"}.get(user_id, "Rakesh")
        portfolio = data.portfolio(self.mongo)
        store = TaskStore(self.mongo)
        mine = store.list(owner=owner, statuses=("open",))[:25]
        overdue = [t for t in mine if t.get("due") and t["due"] < now]
        suggested = store.list(owner=owner, statuses=("suggested",))[:10]
        deadlines = list(self.mongo.db["timeline_events"].find(
            {"occurred_at": {"$gte": now, "$lte": now + timedelta(days=14)}},
            {"_id": 0, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "source_sha": 1, "amount": 1}).sort("occurred_at", 1).limit(20))
        risk = list(self.mongo.db["timeline_events"].find(
            {"event_type": {"$in": ["default", "legal", "payoff", "extension"]}, "occurred_at": {"$gte": now - timedelta(days=14)}},
            {"_id": 0, "property_id": 1, "occurred_at": 1, "event_type": 1, "title": 1, "source_sha": 1, "amount": 1}).sort("occurred_at", -1).limit(20))
        intake = list(self.mongo.artifacts.aggregate([
            {"$match": {"created_at": {"$gte": since}, "is_inline_image": {"$ne": True}}},
            {"$unwind": {"path": "$property_ids", "preserveNullAndEmptyArrays": True}},
            {"$group": {"_id": "$property_ids", "n": {"$sum": 1}}}]))
        cards = list(self.mongo.db["cards"].find({"status": "new", "significance": {"$gte": 3}}, {"_id": 0}).sort([("significance", -1), ("created_at", -1)]).limit(12))
        handled = data.handled_overnight(self.mongo)
        return {
            "generated_at": now, "user_id": user_id, "owner": owner,
            "portfolio": [{"property_id": p["property_id"], "address": p["address"], "health": p["health"]["level"], "reasons": p["health"]["reasons"][:2],
                           # Ledger figures or nothing. None means "not established in
                           # documents" and the brief must say so, never print 0.
                           "invested": p["money"].get("invested"), "owed": p["money"].get("owed"), "owed_as_of": p["money"].get("owed_as_of"),
                           "money_established": p["money"].get("established"), "critical_money_risks": p["money"].get("critical_risks") or [],
                           "open_tasks": p["tasks"]["open"]} for p in portfolio],
            "at_risk": [p["property_id"] for p in portfolio if p["health"]["level"] == "critical"],
            "my_open_tasks": [{"title": t["title"], "property_id": t.get("property_id"), "due": t.get("due"), "priority": t.get("priority"), "source_sha": (t.get("evidence") or [{}])[0].get("source_sha")} for t in mine],
            "overdue": [{"title": t["title"], "property_id": t.get("property_id"), "due": t.get("due")} for t in overdue],
            "suggested_for_me": [{"title": t["title"], "property_id": t.get("property_id"), "why": t.get("why")} for t in suggested],
            "deadlines": deadlines, "recent_money_and_risk": risk,
            "intake": [{"property_id": i["_id"] or "unfiled", "documents": i["n"]} for i in intake],
            "new_cards": [{"property_id": c["property_id"], "title": c["title"], "why": c["why_it_matters"], "significance": c["significance"], "source_sha": c["source_sha"]} for c in cards],
            "handled": handled,
        }

    # ------------------------------------------------------------------ write
    def generate(self, user_id: str, *, force: bool = False) -> Dict[str, Any]:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not force:
            existing = self.coll.find_one({"user_id": user_id, "day": day}, {"_id": 0})
            if existing:
                return existing
        started = time.time()
        f = self.facts(user_id)
        payload = json.dumps(f, default=str, indent=1)[:60_000]
        data_: Optional[dict] = None
        # The model occasionally emits a stray unescaped quote; one retry clears
        # almost all of them, and the deterministic fallback guarantees the reader
        # still gets a brief rather than an error at 6 a.m.
        for attempt in (1, 2):
            try:
                r = self.client.messages.create(model=self.model, max_tokens=5000,
                                                system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
                                                messages=[{"role": "user", "content": f"READER: {f['owner']} ({user_id})\n\nFACTS:\n{payload}"}],
                                                **cfg.OPUS_HIGH_KWARGS)
                data_ = _json("".join(b.text for b in r.content if b.type == "text"))
                break
            except Exception as exc:
                logger.warning("briefing attempt %d failed: %s", attempt, exc)
        if data_ is None:
            data_ = self._fallback(f)
        doc = {
            "user_id": user_id, "day": day, "generated_at": datetime.now(timezone.utc),
            "headline": str(data_.get("headline") or ""), "sections": data_.get("sections") or [],
            "closing": str(data_.get("closing") or ""), "facts_summary": {"at_risk": f["at_risk"], "overdue": len(f["overdue"]), "deadlines": len(f["deadlines"]), "cards": len(f["new_cards"])},
            "model": self.model, "elapsed_ms": int((time.time() - started) * 1000),
        }
        self.coll.update_one({"user_id": user_id, "day": day}, {"$set": doc}, upsert=True)
        return self.coll.find_one({"user_id": user_id, "day": day}, {"_id": 0})

    @staticmethod
    def _fallback(f: Dict[str, Any]) -> Dict[str, Any]:
        """A brief built straight from the facts — no model, always available."""
        def items(rows, key, urg, pid="property_id", sha="source_sha"):
            return [{"text": r.get(key) or "", "urgency": urg, "property_id": r.get(pid), "source_sha": r.get(sha)} for r in rows[:5] if r.get(key)]
        at_risk = f.get("at_risk") or []
        head = (f"{len(f.get('overdue') or [])} overdue task(s), {len(f.get('deadlines') or [])} dated items in 14 days"
                + (f", {len(at_risk)} propert{'ies' if len(at_risk) != 1 else 'y'} at risk: {', '.join(at_risk)}" if at_risk else ""))
        return {
            "headline": head,
            "sections": [
                {"title": "Needs you today", "items": items(f.get("overdue") or [], "title", "critical") + items(f.get("my_open_tasks") or [], "title", "high")},
                {"title": "Money", "items": items(f.get("recent_money_and_risk") or [], "title", "normal")},
                {"title": "Deadlines — next 14 days", "items": items(f.get("deadlines") or [], "title", "high")},
                {"title": "What changed overnight", "items": items(f.get("new_cards") or [], "title", "normal")},
                {"title": "Handled for you", "items": [{"text": f"{h['label']}: {h['count']:,}", "urgency": "good", "property_id": None, "source_sha": None} for h in (f.get("handled") or [])[:5]]},
            ],
            "closing": "Written from the records directly; the AI-written version was unavailable this morning.",
        }

    def latest(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.coll.find_one({"user_id": user_id}, {"_id": 0}, sort=[("day", -1)])


# =============================================================================
# Scheduler — in-process, standing jobs with a dead-letter record
# =============================================================================

class Scheduler:
    """The standing jobs, in one thread, with every run recorded.

    * mail intake every ``MT_POLL_MINUTES`` (default 10), then the arrival
      chain for anything new; tasks and cards flushed once a property has been
      quiet for the debounce window
    * nightly at 02:00 local: a 72-hour intake sweep, then the correctness pass
      (graph rebuild, anything any stage still owes)
    * change-detection cards hourly; the briefing before 06:00

    In-process on purpose for now: one server, one thread, state in Mongo
    (``scheduled_runs``) so a missed run is visible and a failed one is
    dead-lettered rather than silent.
    """

    def __init__(self, mongo: Mongo, *, anthropic_api_key: str, briefing_hour: int = 6, users=("rakesh", "jp", "manjunath"),
                 intake: bool = True):
        self.mongo = mongo
        self.key = anthropic_api_key
        self.hour = briefing_hour
        self.users = users
        self.runs = mongo.db["scheduled_runs"]
        self._stop = threading.Event()
        self.intake_enabled = intake
        self._watcher = None
        self._chain = None
        self._last_poll: Optional[datetime] = None

    @property
    def watcher(self):
        if self._watcher is None:
            from mangotree.ingest.watch import MailWatcher
            self._watcher = MailWatcher(self.mongo)
        return self._watcher

    @property
    def chain(self):
        if self._chain is None:
            from mangotree.pipeline.arrival import ArrivalChain
            self._chain = ArrivalChain(self.mongo)
        return self._chain

    @property
    def wes(self):
        if getattr(self, "_wes", None) is None:
            from mangotree.briefing.wes_agenda import WesAgenda
            self._wes = WesAgenda(self.mongo, anthropic_api_key=self.key)
        return self._wes

    def _due_daily(self, job: str) -> bool:
        """Once a day, from two hours before the briefing hour, if not yet recorded today.

        Two hours because the pass now starts with a full agent investigation of
        every property (~5 min each, three at a time) before the ledger and the
        agenda, and the 6 a.m. brief must read the finished result."""
        if datetime.now().hour < max(0, self.hour - 2):
            return False
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.runs.count_documents({"job": job, "day": day, "ok": True}) == 0

    def run_money_and_wes(self) -> Dict[str, Any]:
        """Morning pass: investigate every property, then the ledger, then the Wes
        agenda. The agenda reads both; the brief that follows reads all three."""
        from concurrent.futures import ThreadPoolExecutor
        from mangotree.briefing.dossier import PropertyDossier
        from mangotree.config.registry import PROPERTIES
        from mangotree.config.settings import SETTINGS
        from mangotree.ledger.builder import LedgerBuilder
        out: Dict[str, Any] = {}
        dossier = PropertyDossier(self.mongo, anthropic_api_key=self.key, voyage_api_key=SETTINGS.voyage_api_key,
                                  openai_api_key=SETTINGS.openai_api_key_critic or "")
        with ThreadPoolExecutor(max_workers=3) as pool:
            built = list(pool.map(lambda p: bool(dossier.block(p.property_id, force=True)), PROPERTIES))
        out["dossiers"] = f"{sum(built)}/{len(built)} rebuilt"
        # Resolution before generation: yesterday's items are checked against
        # overnight records, so today's agenda cannot re-raise what is done.
        from mangotree.briefing.resolution import ResolutionPass
        rp = ResolutionPass(self.mongo, anthropic_api_key=self.key)
        with ThreadPoolExecutor(max_workers=4) as pool:
            res = list(pool.map(lambda p: rp.run_for(p.property_id), PROPERTIES))
        out["resolution"] = {k: sum(r.get(k, 0) for r in res if isinstance(r, dict)) for k in ("items", "resolved", "superseded", "reported")}
        out["ledger"] = LedgerBuilder(self.mongo, anthropic_api_key=self.key).run(concurrency=4).as_dict()
        out["wes"] = self.wes.run(force=True, concurrency=4)
        return out

    def _due_poll(self) -> bool:
        from mangotree.ingest.watch import POLL_MINUTES
        return self._last_poll is None or (datetime.now(timezone.utc) - self._last_poll) >= timedelta(minutes=POLL_MINUTES)

    def _due_nightly(self) -> bool:
        local = datetime.now()
        if local.hour != 2:
            return False
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.runs.count_documents({"job": "nightly", "day": day}) == 0

    def run_intake(self, *, kind: str = "poll", hours: Optional[float] = None) -> Dict[str, Any]:
        """One intake pass followed by the arrival chain. Also used by the API's 'Check mail now'."""
        report = self.watcher.run(kind=kind, hours=hours)
        out: Dict[str, Any] = {"intake": report.as_dict()}
        if report.new_email_shas:
            out["arrival"] = self.chain.process(report.new_email_shas)
        self._last_poll = datetime.now(timezone.utc)
        return out

    def _record(self, job: str, ok: bool, detail: Any) -> None:
        self.runs.insert_one({"job": job, "ok": ok, "detail": detail if isinstance(detail, (dict, list, str, int)) else str(detail),
                              "at": datetime.now(timezone.utc)})

    def _due_briefing(self) -> bool:
        local = datetime.now()
        if local.hour < self.hour:
            return False
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.mongo.db["briefings"].count_documents({"day": day}) < len(self.users)

    def _due_cards(self) -> bool:
        last = self.runs.find_one({"job": "cards", "ok": True}, sort=[("at", -1)])
        return not last or (datetime.now(timezone.utc) - last["at"]) > timedelta(hours=1)

    def tick(self) -> None:
        if self.intake_enabled and self._due_poll():
            try:
                out = self.run_intake()
                self._record("intake", not out["intake"].get("source_errors"), out["intake"])
            except Exception as exc:
                logger.exception("mail intake failed")
                self._record("intake", False, str(exc)[:400])
                self._last_poll = datetime.now(timezone.utc)
        if self.intake_enabled:
            try:
                flushed = self.chain.flush_debounced()
                if flushed:
                    self._record("tasks_cards", "error" not in flushed, flushed)
            except Exception as exc:
                logger.exception("debounced tasks/cards failed")
        if self.intake_enabled and self._due_nightly():
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                sweep = self.run_intake(kind="sweep", hours=72)
                nightly = self.chain.nightly()
                self.runs.insert_one({"job": "nightly", "day": day, "ok": True, "detail": {"sweep": sweep.get("intake"), "nightly": {k: str(v)[:200] for k, v in nightly.items()}},
                                      "at": datetime.now(timezone.utc)})
            except Exception as exc:
                logger.exception("nightly sweep failed")
                self.runs.insert_one({"job": "nightly", "day": day, "ok": False, "detail": str(exc)[:400], "at": datetime.now(timezone.utc)})
        if self._due_cards():
            try:
                from .cards import CardDetector
                out = CardDetector(self.mongo, anthropic_api_key=self.key).run()
                self._record("cards", True, out)
            except Exception as exc:
                logger.exception("card detection run failed")
                self._record("cards", False, str(exc)[:400])
        # Money and the Wes agenda run BEFORE the briefing so the brief reads the
        # morning's ledger, not yesterday's. Recorded per day; a failure retries on
        # the next tick rather than waiting for tomorrow.
        if self._due_daily("money_wes"):
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            try:
                out = self.run_money_and_wes()
                self.runs.insert_one({"job": "money_wes", "day": day, "ok": True, "detail": {k: str(v)[:400] for k, v in out.items()}, "at": datetime.now(timezone.utc)})
            except Exception as exc:
                logger.exception("money/wes daily run failed")
                self.runs.insert_one({"job": "money_wes", "day": day, "ok": False, "detail": str(exc)[:400], "at": datetime.now(timezone.utc)})
        if self._due_briefing():
            b = Briefing(self.mongo, anthropic_api_key=self.key)
            for u in self.users:
                try:
                    b.generate(u)
                    self._record(f"briefing:{u}", True, "written")
                except Exception as exc:
                    logger.exception("briefing failed for %s", u)
                    self._record(f"briefing:{u}", False, str(exc)[:400])

    def start(self) -> None:
        def loop():
            time.sleep(20)   # let the API finish booting
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    logger.exception("scheduler tick failed")
                self._stop.wait(60)
        threading.Thread(target=loop, name="scheduler", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
