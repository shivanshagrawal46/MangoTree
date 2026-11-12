"""RKB's own handling of each contractor bill, step by step, with the time each step took.

A bill arrives in Buildertrend "in review". RKB then does four things to it, and
this records when each was done and by whom:

    Manju Sir approved  ->  Team reviewed  ->  Ready to pay  ->  Paid

The steps are ticked by a person on the Bills page; nothing here ticks a box by
itself, and no model is involved. From the ticks the page shows how long each
bill took from its bill date to each step and to payment.

Hold-back bills are left out. The contractor enters each draw as two bills — 90%
"(Contractor)" and 10% "(Hold-back)" — and RKB keeps the 10% until the work is
complete, so a hold-back bill is not something to approve and pay now. The page
says how many are being held and for how much.

A bill stays on the list from the moment Buildertrend shows it in review until
RKB ticks it paid — including after the contractor's system has moved it on —
so a bill cannot drop out of sight half-way. The first tick stores the bill's
essentials with the record, so the row survives even if the bill is later
deleted in Buildertrend.

Collection: ``bill_tracking`` (one document per bill).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from mangotree.core import eastern
from mangotree.storage.mongo import Mongo

from .buildertrend import is_holdback
from .store import name_of, now

STEPS = (("manju_approved", "Manju Sir approved"), ("team_reviewed", "Team reviewed"), ("ready_to_pay", "Ready to pay"), ("paid", "Paid"))
STEP_KEYS = tuple(k for k, _ in STEPS)
_KEEP = ("bill_id", "job_id", "property_id", "number", "title", "pay_to", "amount", "invoice_date", "due_date")


class BillTracker:
    def __init__(self, mongo: Mongo):
        self.bills = mongo.db["bt_bills"]
        self.track = mongo.db["bill_tracking"]

    @staticmethod
    def _midday(d: Any) -> Any:
        return d.replace(hour=12, minute=0, second=0, microsecond=0) if isinstance(d, datetime) else d

    def _row(self, b: Dict[str, Any], t: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        steps = (t or {}).get("steps") or {}
        return {
            "bill_id": b["bill_id"], "property_id": b.get("property_id"), "property": name_of(b["property_id"]) if b.get("property_id") else "—",
            "number": b.get("number"), "title": b.get("title"), "pay_to": b.get("pay_to"), "amount": b.get("amount"),
            # The bill date as Buildertrend holds it (its own wall-clock time, which is US Eastern), the true
            # moment that is (so "time since the bill date" is right from any time zone), and the calendar day.
            "bill_date": b.get("invoice_date"), "bill_moment": eastern.wall_clock_et(b.get("invoice_date")), "bill_day": self._midday(b.get("invoice_date")), "due_day": self._midday(b.get("due_date")), "past_due": bool(b.get("past_due")),
            "buildertrend_status": b.get("status"), "in_buildertrend": not b.get("_gone"), "approvers": b.get("approvers") or [],
            "line_items": b.get("line_items") or [], "created_by": b.get("created_by"), "documents": b.get("documents") or 0, "file_names": b.get("file_names") or [],
            "steps": {k: steps.get(k) for k in STEP_KEYS},
        }

    def rows(self, view: str = "open") -> List[Dict[str, Any]]:
        tracked = {t["bill_id"]: t for t in self.track.find({}, {"_id": 0})}
        paid = {i for i, t in tracked.items() if (t.get("steps") or {}).get("paid")}
        if view == "paid":
            want: Dict[str, Any] = {"bill_id": {"$in": list(paid)}}
        else:
            started = [i for i, t in tracked.items() if i not in paid and any((t.get("steps") or {}).values())]
            # Hold-back bills are never bills to process, ticked or not.
            want = {"$and": [{"bill_id": {"$nin": list(paid)}}, {"holdback": {"$ne": True}}, {"$or": [{"in_review": True}, {"bill_id": {"$in": started}}]}]}
        found = {b["bill_id"]: b for b in self.bills.find(want, {"_id": 0})}
        # A tracked bill that has since been deleted in Buildertrend is shown from what was kept at its first tick.
        for i, t in tracked.items():
            if i not in found and ((i in paid) == (view == "paid")) and t.get("bill") and (view == "paid" or (any((t.get("steps") or {}).values()) and not is_holdback(t["bill"].get("title")) and not self.bills.count_documents({"bill_id": i}, limit=1))):
                found[i] = {**t["bill"], "status": "No longer in Buildertrend", "_gone": True}
        out = [self._row(b, tracked.get(i)) for i, b in found.items()]
        if view == "paid":
            out.sort(key=lambda r: (r["steps"]["paid"] or {}).get("at") or now(), reverse=True)
        else:
            out.sort(key=lambda r: r.get("bill_date") or now(), reverse=True)        # newest bill first
        return out

    def open_count(self) -> int:
        tracked = list(self.track.find({}, {"bill_id": 1, "steps": 1}))
        paid = [t["bill_id"] for t in tracked if (t.get("steps") or {}).get("paid")]
        started = [t["bill_id"] for t in tracked if t["bill_id"] not in paid and any((t.get("steps") or {}).values())]
        return self.bills.count_documents({"$and": [{"bill_id": {"$nin": paid}}, {"holdback": {"$ne": True}}, {"$or": [{"in_review": True}, {"bill_id": {"$in": started}}]}]})

    def held_back(self) -> Dict[str, Any]:
        """The hold-back bills not shown: those in review now, and everything held so far."""
        review = list(self.bills.find({"holdback": True, "in_review": True}, {"amount": 1}))
        held = list(self.bills.find({"holdback": True, "status": {"$ne": "Paid"}}, {"amount": 1, "balance": 1}))
        return {"in_review": len(review), "in_review_amount": round(sum(b.get("amount") or 0 for b in review), 2),
                "held": len(held), "held_amount": round(sum((b.get("balance") if b.get("balance") is not None else b.get("amount")) or 0 for b in held), 2)}

    def set_step(self, bill_id: int, step: str, done: bool, user: Dict[str, Any]) -> Dict[str, Any]:
        if step not in STEP_KEYS:
            raise ValueError(f"unknown step {step!r}")
        b = self.bills.find_one({"bill_id": int(bill_id)}, {"_id": 0})
        t = self.track.find_one({"bill_id": int(bill_id)}, {"_id": 0})
        if not b and not t:
            raise KeyError(bill_id)
        if done:
            mark = {"at": now(), "by": user.get("user_id"), "by_name": user.get("name") or user.get("user_id")}
            change: Dict[str, Any] = {"$set": {f"steps.{step}": mark, "updated_at": now()}, "$setOnInsert": {"bill_id": int(bill_id), "started_at": now()},
                                      "$push": {"history": {"step": step, "done": True, **mark}}}
            if b:
                change["$set"]["bill"] = {k: b.get(k) for k in _KEEP}
        else:
            change = {"$unset": {f"steps.{step}": ""}, "$set": {"updated_at": now()},
                      "$push": {"history": {"step": step, "done": False, "at": now(), "by": user.get("user_id"), "by_name": user.get("name") or user.get("user_id")}}}
        self.track.update_one({"bill_id": int(bill_id)}, change, upsert=bool(done))
        t = self.track.find_one({"bill_id": int(bill_id)}, {"_id": 0})
        return self._row(b or {**(t or {}).get("bill", {}), "status": "No longer in Buildertrend", "_gone": True}, t)
