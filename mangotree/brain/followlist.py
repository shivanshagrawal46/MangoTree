"""Follow-ups: who has to do what on the construction properties, worked out from the records.

Nothing here is written by a model and nothing is remembered between looks: every
time the list is asked for it is worked out again from Buildertrend's bills and
daily logs, the money sheet and RKB's own ticks. So an item appears the moment its
condition is true and goes by itself the moment it is not — a bill is approved,
a log is written, a figure is entered. That is what makes the list safe to rely
on: it cannot go stale, and nobody has to keep it.

Each item has one owner:

    manju   Manjunath Sir — approve a bill against the work, a line billed past its price
    jp      JP Sir — pay what is approved, record a payment, paper for a change order, figures
    rakesh  Rakesh Sir — a maturity coming up
    wes     the contractor — things RKB has to push him for (no log, proposal not locked)

People can put an item aside ("done" or "later, 3 days") with a note; the mark is
kept with who made it. An item put aside comes back if what it is about changes
(the set of bills waiting, a new date), because its key changes.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mangotree.storage.mongo import Mongo

from . import snapshot
from .buildertrend import IN_REVIEW, PAID, READY, Buildertrend
from .moneysheet import MoneySheet
from .store import BrainStore, name_of

OWNERS = {"manju": "Manjunath Sir", "jp": "JP Sir", "rakesh": "Rakesh Sir", "wes": "Wes (contractor)"}
ROLE_OWNER = {"ceo": "rakesh", "accountant": "jp", "operations": "manju"}
NO_LOG_DAYS = 7
APPROVAL_DAYS = 3            # a bill waiting longer than this for approval is marked late
PAY_DAYS = 5                 # ...and one approved but unpaid longer than this
MATURITY_DAYS = 60
SNOOZE_DAYS = 3


def now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: Any) -> Optional[datetime]:
    return d.replace(tzinfo=d.tzinfo or timezone.utc) if isinstance(d, datetime) else None


def _days(since: Optional[datetime], today: datetime) -> Optional[int]:
    since = _aware(since)
    return None if since is None else max(0, (today.date() - since.date()).days)


def _usd(v: Optional[float]) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _short(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:10]


class FollowUps:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.bt = Buildertrend(mongo)
        self.sheet = MoneySheet(mongo, self.store)
        self.marks = mongo.db["followup_marks"]

    # ------------------------------------------------------------ which properties
    def construction(self) -> List[Dict[str, Any]]:
        files = self.store.files.find({"property_id": {"$ne": "_general"}}, {"_id": 0, "property_id": 1, "stage": 1})
        return sorted((f for f in files if snapshot.group_of(f.get("stage")) == "construction"), key=lambda f: f["property_id"])

    # ------------------------------------------------------------ one property
    def _for_property(self, f: Dict[str, Any], jobs: Dict[str, Any], today: datetime) -> List[Dict[str, Any]]:
        pid, name = f["property_id"], name_of(f["property_id"])
        out: List[Dict[str, Any]] = []

        def add(kind: str, owner: str, title: str, detail: str = "", *, ident: Any = "", since: Any = None, late_after: Optional[int] = None,
                tab: str = "sheet", amount: Optional[float] = None, level: Optional[str] = None) -> None:
            d = _days(since, today)
            out.append({"key": f"{kind}:{pid}:{_short(ident)}", "kind": kind, "property_id": pid, "property": name, "owner": owner, "owner_name": OWNERS[owner],
                        "title": title, "detail": detail, "since": _aware(since), "days": d, "tab": tab, "amount": amount,
                        "level": level or ("high" if (late_after is not None and d is not None and d > late_after) else "normal")})

        if pid not in jobs:
            add("no_job", "wes", "No job in Buildertrend for this property", "Without a job there is no proposal, no bills and no daily logs to check the work against.", tab="overview")
            return out

        s = self.sheet.build(pid, today=today)
        ticks = {b["bill_id"]: b["steps"] for b in s["bills"]}
        bills = [b for b in s["bills"] if not b["holdback"]]
        checked = {c["bill_id"]: c["summary"] for c in self.mongo.db["bill_checks"].find({"property_id": pid, "summary": {"$exists": True}}, {"_id": 0, "bill_id": 1, "summary": 1})}

        # 1. Bills waiting for Manjunath Sir — one item each, because each is its own decision. The line-by-line
        #    check has already been made (billcheck.py): the item says what it found and opens that page.
        for b in bills:
            if b["status"] == IN_REVIEW and not (ticks[b["bill_id"]] or {}).get("manju_approved"):
                c = checked.get(b["bill_id"])
                if not c:
                    found = "Not yet checked against the logs and photos."
                elif c["check"]["count"]:
                    found = (f"Checked against the logs and photos: {c['check']['count']} of {c['lines']} line{'s' if c['lines'] != 1 else ''} to look at "
                             f"({_usd(c['check']['amount'])})" + ("." if c["check"]["count"] >= c["lines"] else "; the rest is supported."))
                else:
                    found = f"Checked against the logs and photos: all {c['lines']} line{'s' if c['lines'] != 1 else ''} supported."
                add("approve_bill", "manju", f"Approve or query: {b['title']} — {_usd(b['amount'])}",
                    f"{found} Pay to {b.get('pay_to') or 'the contractor'}.", ident=b["bill_id"], since=b.get("date"),
                    late_after=APPROVAL_DAYS, tab="sheet", amount=b["amount"])
                out[-1]["href"] = f"/bills/{b['bill_id']}"
                out[-1]["to_check"] = c["check"]["count"] if c else None

        # 2. Approved, not paid — one item for the property, so JP Sir sees one line with the total.
        waiting = [b for b in bills if not (ticks[b["bill_id"]] or {}).get("paid") and b["status"] != PAID
                   and (b["status"] == READY or (ticks[b["bill_id"]] or {}).get("manju_approved"))]
        if waiting:
            oldest = min((b.get("date") for b in waiting if b.get("date")), default=None)
            total = round(sum(b["amount"] or 0 for b in waiting), 2)
            add("pay_bills", "jp", f"{len(waiting)} approved bill{'s' if len(waiting) != 1 else ''} not yet paid — {_usd(total)}",
                "; ".join(f"{b['title']} {_usd(b['amount'])}" for b in waiting[:8]) + (f"; and {len(waiting) - 8} more" if len(waiting) > 8 else ""),
                ident=sorted(b["bill_id"] for b in waiting), since=oldest, late_after=PAY_DAYS, tab="bills", amount=total)

        # 3. Ticked paid here, but Buildertrend still shows it owing.
        for b in bills:
            paid = (ticks[b["bill_id"]] or {}).get("paid")
            if paid and b["status"] != PAID and (_days(paid.get("at"), today) or 0) >= 2:
                add("paid_not_recorded", "jp", f"Marked paid here, still unpaid in Buildertrend: {b['title']} — {_usd(b['amount'])}",
                    "Record the payment in Buildertrend, or untick Paid on the Bills page if it has not been paid.", ident=b["bill_id"], since=paid.get("at"),
                    tab="bills", amount=b["amount"], level="high")

        # 4. A line billed past its price, or billed ahead of the work Manjunath Sir recorded.
        for r in s["rows"]:
            if r["kind"] != "other" and r["revised"] > 0 and r["billed"] > r["revised"] + 1:
                add("overbilled", "manju", f"Billed more than the proposal: {r['title']}",
                    f"Billed {_usd(r['billed'])} against {_usd(r['revised'])} ({r['cost_code']}). Either a change order is missing or the bill is too high.",
                    ident=(r["key"], round(r["billed"])), amount=round(r["billed"] - r["revised"], 2), level="high")
            elif r.get("work_done") is not None and r.get("billed_percent") is not None and r["billed_percent"] > r["work_done"] + 10:
                add("ahead_of_work", "manju", f"Billed ahead of the work: {r['title']}",
                    f"{r['billed_percent']}% of the line is billed; work done is recorded as {r['work_done']}%.", ident=(r["key"], r["billed_percent"], r["work_done"]), level="high")

        # 5. Change orders: money outside the proposal needs its paper.
        for r in s["rows"]:
            if r["kind"] == "change_order":
                add("change_order", "jp", f"Change order: {r['title']} — {_usd(r['change_orders'])}",
                    "Check that a change order signed by the homeowner is on file, then mark this done.", ident=r["key"], amount=r["change_orders"], tab="sheet")

        # 6. The figures the interest depends on.
        figs = s["figures"]
        missing = [figs[k]["label"].lower() for k in ("note_amount", "closing") if figs[k]["value"] is None] + (["interest rate on the note"] if figs["rate"].get("assumed") else [])
        if missing:
            add("figures", "jp", "Enter on the money sheet: " + ", ".join(missing),
                "Interest to the homeowner cannot be worked out, and the markup split uses an assumed 14%, until these are entered.", ident=missing)
        it = s["deal"]["interest"]
        if it.get("free_days_left") and it["free_days_left"] <= 30:
            add("interest_starts", "jp", f"The six interest-free months end on {it['starts']:%d %b %Y}",
                "From that day interest accrues against the homeowner on the full note amount.", ident=it["starts"].date(), tab="sheet")

        # 7. The proposal.
        est = s.get("estimate")
        if est is None:
            add("no_proposal", "wes", "No proposal in Buildertrend's Estimate", "Bills cannot be checked line by line until the proposal is entered.", tab="sheet")
        elif not est.get("locked"):
            add("proposal_open", "wes", "The proposal in Buildertrend is not locked", "Its lines and prices can still be changed after bills have been paid against them.", tab="sheet")

        # 8. Site: a job under construction with no daily log.
        if (f.get("stage") or {}).get("stage") == "construction":
            last = self.bt.logs.find_one({"property_id": pid}, {"date": 1}, sort=[("date", -1)])
            d = _days((last or {}).get("date"), today)
            if d is None:
                add("no_log", "wes", "No daily log has ever been written for this job", "Ask the contractor to log the work, with photos.", tab="site")
            elif d > NO_LOG_DAYS:
                add("no_log", "wes", f"No daily log for {d} days (last on {_aware(last['date']):%d %b})",
                    "Ask the contractor what was done on site since, and to log it with photos.", ident=_aware(last["date"]).date(), since=last["date"], tab="site",
                    level="high" if d > 2 * NO_LOG_DAYS else "normal")

        # 9. Maturity.
        m = _aware(figs["maturity"]["value"])
        if m is not None:
            left = (m.date() - today.date()).days
            if left <= MATURITY_DAYS:
                add("maturity", "rakesh", f"The note matures on {m:%d %b %Y}" + (f" — in {left} days" if left >= 0 else f" — {-left} days ago"),
                    "Decide: payoff, extension, or the next step.", ident=m.date(), tab="money", level="high" if left <= 30 else "normal")
        return out

    # ------------------------------------------------------------ the list
    def build(self, *, today: Optional[datetime] = None, include_put_aside: bool = False) -> List[Dict[str, Any]]:
        today = today or now()
        jobs = {j["property_id"]: j for j in self.bt.jobs.find({"property_id": {"$ne": None}}, {"_id": 0, "property_id": 1, "job_id": 1})}
        items: List[Dict[str, Any]] = []
        files = self.construction()
        # Each property is a handful of database reads; side by side, the list takes as long as the slowest one.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(files) or 1))) as pool:
            for part in pool.map(lambda f: self._for_property(f, jobs, today), files):
                items.extend(part)
        marks = {m["key"]: m for m in self.marks.find({"key": {"$in": [i["key"] for i in items]}}, {"_id": 0})}
        out = []
        for i in items:
            m = marks.get(i["key"])
            aside = bool(m and (m.get("state") == "done" or (m.get("state") == "later" and _aware(m.get("until")) and _aware(m["until"]) > today)))
            i["mark"] = {k: m.get(k) for k in ("state", "note", "by_name", "at", "until")} if m and aside else None
            if include_put_aside or not aside:
                out.append(i)
        order = {"high": 0, "normal": 1}
        out.sort(key=lambda i: (bool(i["mark"]), order[i["level"]], -(i["days"] or 0), i["property"]))
        return out

    def mark(self, key: str, state: str, user: Dict[str, Any], note: str = "") -> Dict[str, Any]:
        if state not in ("done", "later", "open"):
            raise ValueError("state is done, later or open")
        if state == "open":
            self.marks.delete_one({"key": key})
            return {"key": key, "state": "open"}
        doc = {"key": key, "state": state, "note": (note or "").strip()[:300], "by": user.get("user_id"), "by_name": user.get("name") or user.get("user_id"),
               "at": now(), "until": now() + timedelta(days=SNOOZE_DAYS) if state == "later" else None}
        self.marks.update_one({"key": key}, {"$set": doc}, upsert=True)
        return doc

    def counts(self, items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        items = self.build() if items is None else items
        by: Dict[str, Dict[str, int]] = {o: {"open": 0, "high": 0} for o in OWNERS}
        for i in items:
            if not i.get("mark"):
                by[i["owner"]]["open"] += 1
                by[i["owner"]]["high"] += int(i["level"] == "high")
        return by

    @staticmethod
    def message_for_wes(items: List[Dict[str, Any]]) -> str:
        """The contractor's items as one short message, for Rakesh Sir to read, change and send
        himself. Nothing is sent from here."""
        mine = [i for i in items if i["owner"] == "wes" and not i.get("mark")]
        if not mine:
            return ""
        by: Dict[str, List[str]] = {}
        for i in mine:
            by.setdefault(i["property"], []).append(i["title"])
        lines = ["Hi Wes,", "", "A few things we need from you on the jobs under construction:", ""]
        for prop, titles in by.items():
            lines.append(f"{prop}:")
            lines += [f"  - {t}" for t in titles]
        lines += ["", "Please let us know by when each will be done.", "", "Thanks,", "Rakesh"]
        return "\n".join(lines)

    # ------------------------------------------------------------ the alert on Today
    def alerts(self, user: Dict[str, Any], *, today: Optional[datetime] = None, items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """What changed on the construction jobs at the last Buildertrend read, and what is
        waiting for the person looking: shown at the top of Today to everyone at RKB."""
        today = today or now()
        items = self.build(today=today) if items is None else items
        me = ROLE_OWNER.get(user.get("role") or "")
        pids = [f["property_id"] for f in self.construction()]
        pull = self.bt.pulls.find_one({"new_logs": {"$exists": True}}, sort=[("loaded_at", -1)]) or {}
        new_logs = {p: n for p, n in (pull.get("new_logs") or {}).items() if p in pids and n}
        stages = {f["property_id"]: (f.get("stage") or {}) for f in self.store.files.find({"property_id": {"$in": list(new_logs)}}, {"_id": 0, "property_id": 1, "stage.site_now": 1})}
        review = list(self.bt.bills.find({"property_id": {"$in": pids}, "in_review": True, "holdback": {"$ne": True}},
                                         {"_id": 0, "bill_id": 1, "property_id": 1, "title": 1, "amount": 1, "invoice_date": 1, "created": 1}).sort("invoice_date", -1))
        fresh = [b for b in review if (_days(b.get("created") or b.get("invoice_date"), today) or 99) <= 3]
        checked = {c["bill_id"]: c["summary"] for c in self.mongo.db["bill_checks"].find({"bill_id": {"$in": [b["bill_id"] for b in review]}, "summary": {"$exists": True}},
                                                                                         {"_id": 0, "bill_id": 1, "summary": 1})}
        return {
            "read_at": pull.get("loaded_at") or pull.get("at"),
            "new_logs": [{"property_id": p, "property": name_of(p), "count": n, "site_now": (stages.get(p) or {}).get("site_now") or ""} for p, n in sorted(new_logs.items())],
            "analysed": bool(pull.get("analysed")),
            "in_review": {"count": len(review), "amount": round(sum(b.get("amount") or 0 for b in review), 2), "new": len(fresh),
                          "bills": [{"bill_id": b["bill_id"], "property_id": b["property_id"], "property": name_of(b["property_id"]), "title": b.get("title"),
                                     "amount": b.get("amount"), "date": b.get("invoice_date"), "new": b in fresh,
                                     "checked": b["bill_id"] in checked, "to_check": ((checked.get(b["bill_id"]) or {}).get("check") or {}).get("count")} for b in review[:12]]},
            "mine": None if not me else {"owner": me, "owner_name": OWNERS[me], **self.counts(items)[me],
                                         "top": [{**{k: i[k] for k in ("key", "property_id", "property", "title", "days", "level", "tab")}, "href": i.get("href")} for i in items if i["owner"] == me][:5]},
            "counts": self.counts(items),
        }
