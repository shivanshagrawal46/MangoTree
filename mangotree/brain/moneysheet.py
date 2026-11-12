"""The money sheet of one property.

One row per line of the proposal (Buildertrend's Estimate), and against each row
what the contractor has billed for it, what is still in review, what has been
paid, and what is held back until the sale. Change orders and anything billed
that is not in the proposal get rows of their own, so the columns always add up
to the bills.

On top: the deal's own figures — what RKB has funded, what has come back, the
note, the interest — and how the markup is shared, worked out by the firm's rules
(``dealmodel.py``).

Two kinds of cell:

* what the system reads (Buildertrend, RKB's workbooks) — never typed over;
* what people add. JP Sir and Rakesh Sir may correct a money figure; the
  system's own reading is kept beside it, with who changed it and when. Manjunath
  Sir (and they) may record the work done on a line and a note.

Nothing here is sent anywhere. The sheet is for RKB's people only.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from mangotree.storage.mongo import Mongo

from . import snapshot
from .buildertrend import IN_REVIEW, PAID, Buildertrend
from .store import BrainStore

#: The figures a person may set, who may set them, and how each is read.
FIGURES: Dict[str, Dict[str, str]] = {
    "funded": {"label": "RKB funded so far", "kind": "money"},
    "payback": {"label": "Paid back to RKB so far", "kind": "money"},
    "note_amount": {"label": "Amount on the note", "kind": "money"},
    "rate": {"label": "Interest rate on the note", "kind": "percent"},
    "closing": {"label": "Closing date", "kind": "date"},
    "maturity": {"label": "Maturity", "kind": "date"},
}
MONEY_ROLES = ("ceo", "accountant")                  # Rakesh Sir, JP Sir
LINE_ROLES = ("ceo", "accountant", "operations")     # ...and Manjunath Sir
POINTS = 0.02
FREE_MONTHS = 6
DEFAULT_RATE = 14.0


def now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _num(v: Any) -> Optional[float]:
    """A figure out of text such as '$154,733.85' or '14%'."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v or ""))
    return float(m.group(0).replace(",", "")) if m else None


def _day(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v.replace(tzinfo=v.tzinfo or timezone.utc)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(v or ""))
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, tzinfo=timezone.utc) if m else None
    except ValueError:
        return None


def _add_months(d: datetime, months: int) -> datetime:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return d.replace(year=y, month=m, day=min(d.day, last))


class MoneySheet:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.bt = Buildertrend(mongo)
        self.inputs = mongo.db["money_sheet"]
        self.track = mongo.db["bill_tracking"]

    # ------------------------------------------------------------ the rows
    @staticmethod
    def _extra_key(li: Dict[str, Any]) -> str:
        raw = f"{_norm(li.get('cost_code'))}|{_norm(li.get('title'))}|{li.get('unit_cost') or 0:.2f}"
        return "x" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    @staticmethod
    def _put(row: Dict[str, Any], amount: float, b: Dict[str, Any], paid_here: set) -> None:
        """Add one bill line's money to a row, in the column the bill's state puts it in."""
        row["billed"] += amount
        row["bills"] += 1
        if b.get("holdback"):
            row["held_back"] += amount
        elif b.get("status") == PAID or b["bill_id"] in paid_here:
            row["paid"] += amount
        elif b.get("status") == IN_REVIEW:
            row["in_review"] += amount

    def _rows(self, estimate: Optional[Dict[str, Any]], bills: List[Dict[str, Any]], paid_here: set) -> Tuple[List[Dict[str, Any]], List[str]]:
        """The proposal's lines, with every bill line added to the row it belongs to."""
        rows: List[Dict[str, Any]] = []
        index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for g in (estimate or {}).get("groups") or []:
            for li in g["lines"]:
                row = {"key": f"e{li['line_id']}", "kind": "proposal", "group": g["title"], "title": li["title"] or li["cost_code"], "cost_code": li["cost_code"],
                       "description": li.get("description") or "", "proposal": li.get("builder_cost") or 0.0, "price": li.get("price") or 0.0,
                       "_unit": li.get("unit_cost") or 0.0, "change_orders": 0.0, "billed": 0.0, "in_review": 0.0, "paid": 0.0, "held_back": 0.0, "bills": 0}
                rows.append(row)
                index.setdefault((_norm(li["cost_code"]), _norm(li["title"])), []).append(row)
        extras: Dict[str, Dict[str, Any]] = {}
        notes: List[str] = []
        no_lines = [b for b in bills if not b.get("line_items")]
        if no_lines:
            notes.append(f"{len(no_lines)} bill(s) have not had their lines read from Buildertrend yet (${sum(b.get('amount') or 0 for b in no_lines):,.2f}); "
                         "they are in the bill list below but not in the columns.")
        for b in bills:
            for li in b.get("line_items") or []:
                amount = li.get("amount") or 0.0
                cands = index.get((_norm(li.get("cost_code")), _norm(li.get("title")))) or []
                row = None
                if len(cands) == 1:
                    row = cands[0]
                elif cands:
                    # Two proposal lines with the same name (two "Framing/Masonry", two "Toilet"): the bill line carries
                    # the line's price, which usually tells them apart. Where it does not (same unit price on both), the
                    # money goes to the line that still has the most room, so one line is not shown as billed three
                    # times over while its twin shows nothing.
                    unit = li.get("unit_cost") or 0
                    same = [c for c in cands if abs((c["proposal"] or 0) - unit) < 0.01 or abs((c.get("_unit") or 0) - unit) < 0.01]
                    pool = same or cands
                    row = max(pool, key=lambda c: (c["proposal"] or 0) - c["billed"]) if amount else pool[0]
                    if amount > 0 and len(pool) > 1:
                        # The contractor's purchase order can hold these as ONE line (three toilets) where the proposal
                        # has two (two toilets, one toilet). Fill the line with the most room, and pass on what does not fit.
                        left = amount
                        for c in sorted(pool, key=lambda c: (c["proposal"] or 0) - c["billed"], reverse=True)[:-1]:
                            part = round(min(left, max(0.0, (c["proposal"] or 0) - c["billed"])), 2)
                            if part > 0 and part < left:
                                self._put(c, part, b, paid_here)
                                left = round(left - part, 2)
                                row = next((x for x in pool if x is not c and (x["proposal"] or 0) - x["billed"] > 0), row)
                            else:
                                row = c
                                break
                        amount = left
                if row is None:
                    is_co = "change order" in str(li.get("variance") or "").lower()
                    if not amount and not is_co:
                        continue                                   # an unbilled line of some other purchase order: not this proposal's
                    k = self._extra_key(li)
                    row = extras.get(k)
                    if row is None:
                        row = extras[k] = {"key": k, "kind": "change_order" if is_co else "other",
                                           "group": "Change orders" if is_co else "Billed, but not a line of the proposal",
                                           "title": li.get("title") or b.get("title") or "", "cost_code": li.get("cost_code") or "",
                                           "description": li.get("description") or "", "proposal": 0.0, "price": 0.0,
                                           "change_orders": (li.get("unit_cost") or 0.0) if is_co else 0.0,
                                           "billed": 0.0, "in_review": 0.0, "paid": 0.0, "held_back": 0.0, "bills": 0}
                if not amount:
                    continue
                self._put(row, amount, b, paid_here)
        rows += sorted(extras.values(), key=lambda r: (r["kind"] != "change_order", r["cost_code"], r["title"]))
        for r in rows:
            r.pop("_unit", None)
            r["revised"] = round(r["proposal"] + r["change_orders"], 2)
            for k in ("proposal", "price", "change_orders", "billed", "in_review", "paid", "held_back"):
                r[k] = round(r[k], 2)
            # What is billed but neither paid, held back nor still in review: approved in Buildertrend and waiting to be paid.
            r["to_pay"] = round(r["billed"] - r["paid"] - r["held_back"] - r["in_review"], 2) + 0.0     # + 0.0: never "-0.00"
            r["left"] = round(r["revised"] - r["billed"], 2) if r["kind"] != "other" else 0.0
            r["billed_percent"] = round(100 * r["billed"] / r["revised"]) if r["revised"] else None
        return rows, notes

    # ------------------------------------------------------------ the figures on top
    def _figures(self, pid: str, file: Dict[str, Any], estimate: Optional[Dict[str, Any]], saved: Dict[str, Any]) -> Dict[str, Any]:
        nums = snapshot.numbers(self.store, pid, file)
        main = {m["key"]: m for m in nums["main"]}
        extra = nums["extra"]
        stage = file.get("stage") or {}
        read: Dict[str, Tuple[Any, str]] = {
            "funded": (_num(main.get("invested", {}).get("value")), " ".join(x for x in ("RKB's workbook", main.get("invested", {}).get("note") or "") if x)),
            "payback": (None, "not recorded anywhere the system reads"),
            "note_amount": (_num(main.get("note_amount", {}).get("value")), main.get("note_amount", {}).get("source") or ""),
            "rate": (None, ""),
            "closing": (_day((extra.get("closing") or {}).get("value")) or _day(stage.get("closing_date")), "RKB's workbook" if extra.get("closing") else "read from the records"),
            "maturity": (_day(main.get("maturity", {}).get("headline") or main.get("maturity", {}).get("value")), main.get("maturity", {}).get("source") or ""),
        }
        out: Dict[str, Any] = {}
        for key, spec in FIGURES.items():
            sysv, src = read[key]
            ov = (saved.get("figures") or {}).get(key)
            value = ov["value"] if ov else sysv
            if spec["kind"] == "date":
                value = _day(value)
            out[key] = {"key": key, "label": spec["label"], "kind": spec["kind"], "value": value, "system": sysv, "source": src,
                        "set_by": (ov or {}).get("by_name"), "set_at": (ov or {}).get("at")}
        out["rate"]["assumed"] = out["rate"]["value"] is None
        if out["rate"]["value"] is None:
            out["rate"]["value"] = DEFAULT_RATE
            out["rate"]["source"] = "assumed 14%, the usual rate — set it from the note"
        return out

    @staticmethod
    def _deal(figs: Dict[str, Any], estimate: Optional[Dict[str, Any]], today: datetime) -> Dict[str, Any]:
        """The firm's rules applied to this deal (see dealmodel.py). Every part is shown, so a
        wrong input is visible; nothing here is a final settlement figure."""
        rate = (figs["rate"]["value"] or DEFAULT_RATE) / 100.0
        t = (estimate or {}).get("totals") or {}
        cost, price = t.get("builder_cost"), t.get("price")
        markup: Optional[Dict[str, Any]] = None
        if cost and price:
            points = round(POINTS * cost, 2)
            six = round(cost * rate * FREE_MONTHS / 12.0, 2)
            rest = round(price - cost - points - six, 2)
            markup = {"builder_cost": cost, "price": price, "markup": round(price - cost, 2), "points": points, "six_months_interest": six,
                      "remainder": rest, "rkb_half": round(rest / 2, 2), "roi_at_start": round(rest / 4, 2), "roi_after_sale": round(rest / 4, 2),
                      "basis": "Two points and six months' interest are calculated on the builder cost. This basis is awaiting Rakesh Sir's confirmation."}
        closing, note = figs["closing"]["value"], figs["note_amount"]["value"]
        interest: Dict[str, Any] = {"starts": None, "daily": None, "days": None, "accrued": None, "why_not": None}
        if not closing:
            interest["why_not"] = "no closing date on record"
        elif not note:
            interest["why_not"] = "the amount on the note is not on record"
            interest["starts"] = _add_months(closing, FREE_MONTHS)
        else:
            starts = _add_months(closing, FREE_MONTHS)
            days = max(0, (today.date() - starts.date()).days)
            daily = round(note * rate / 365.0, 2)
            interest = {"starts": starts, "daily": daily, "days": days, "accrued": round(daily * days, 2), "why_not": None,
                        "free_days_left": max(0, (starts.date() - today.date()).days)}
        return {"markup": markup, "interest": interest, "rate_assumed": bool(figs["rate"].get("assumed"))}

    # ------------------------------------------------------------ the sheet
    def build(self, pid: str, *, today: Optional[datetime] = None) -> Dict[str, Any]:
        today = today or now()
        file = self.store.files.find_one({"property_id": pid}, {"_id": 0, "property_id": 1, "address": 1, "facts": 1, "stage": 1}) or {"property_id": pid}
        estimate = self.bt.estimate_of(pid)
        bills = list(self.bt.bills.find({"property_id": pid}, {"_id": 0}).sort("invoice_date", 1))
        tracking = {t["bill_id"]: t for t in self.track.find({"bill_id": {"$in": [b["bill_id"] for b in bills]}}, {"_id": 0, "bill_id": 1, "steps": 1})}
        paid_here = {bid for bid, t in tracking.items() if (t.get("steps") or {}).get("paid")}
        saved = self.inputs.find_one({"property_id": pid}, {"_id": 0}) or {}
        rows, notes = self._rows(estimate, bills, paid_here)
        for r in rows:
            mine = (saved.get("lines") or {}).get(r["key"]) or {}
            r["work_done"], r["note"], r["input_by"], r["input_at"] = mine.get("work_done"), mine.get("note") or "", mine.get("by_name"), mine.get("at")
        cols = ("proposal", "price", "change_orders", "revised", "billed", "in_review", "to_pay", "paid", "held_back", "left")
        totals = {c: round(sum(r[c] for r in rows), 2) for c in cols}
        totals["billed_percent"] = round(100 * totals["billed"] / totals["revised"]) if totals["revised"] else None
        figs = self._figures(pid, file, estimate, saved)
        bill_rows = [{
            "bill_id": b["bill_id"], "title": b.get("title"), "number": b.get("number"), "pay_to": b.get("pay_to"), "amount": b.get("amount"),
            "date": b.get("invoice_date"), "status": b.get("status"), "holdback": bool(b.get("holdback")), "has_lines": bool(b.get("line_items")),
            "purchase_order": (b.get("purchase_orders") or [None])[0], "steps": (tracking.get(b["bill_id"]) or {}).get("steps") or {},
        } for b in bills]
        if not estimate:
            notes.insert(0, "No proposal has been read from Buildertrend's Estimate for this property, so the rows below are only what has been billed.")
        elif not estimate.get("locked"):
            notes.insert(0, "The proposal in Buildertrend is not locked — its lines can still change.")
        def paid_at(b: Dict[str, Any]) -> Optional[datetime]:
            tick = ((tracking.get(b["bill_id"]) or {}).get("steps") or {}).get("paid") or {}
            return b.get("date_paid") or tick.get("at")

        paid_bills = [b for b in bills if not b.get("holdback") and (b.get("status") == PAID or b["bill_id"] in paid_here)]
        open_bills = [b for b in bills if not b.get("holdback") and b not in paid_bills]
        last = max(paid_bills, key=lambda b: (paid_at(b) or datetime(1970, 1, 1)).replace(tzinfo=None), default=None)
        payments = {
            "paid": round(sum(b.get("amount") or 0 for b in paid_bills), 2), "paid_count": len(paid_bills),
            "to_pay": round(sum(b.get("amount") or 0 for b in open_bills if b.get("status") != IN_REVIEW), 2),
            "to_pay_count": sum(1 for b in open_bills if b.get("status") != IN_REVIEW),
            "in_review": round(sum(b.get("amount") or 0 for b in open_bills if b.get("status") == IN_REVIEW), 2),
            "in_review_count": sum(1 for b in open_bills if b.get("status") == IN_REVIEW),
            "held_back": round(sum((b.get("balance") if b.get("balance") is not None else b.get("amount")) or 0 for b in bills if b.get("holdback") and b.get("status") != PAID), 2),
            "last_paid": None if not last else {"title": last.get("title"), "amount": last.get("amount"), "at": paid_at(last)},
        }
        return {
            "property_id": pid, "address": file.get("address"), "rows": rows, "totals": totals, "notes": notes, "figures": figs, "payments": payments,
            "deal": self._deal(figs, estimate, today), "bills": bill_rows,
            "estimate": None if not estimate else {k: estimate.get(k) for k in ("locked", "locked_by", "locked_at", "pulled_at", "totals", "sent_to_budget")},
            "bills_pulled_at": max((b.get("pulled_at") for b in bills if b.get("pulled_at")), default=None),
            "history": (saved.get("history") or [])[-30:][::-1],
        }

    # ------------------------------------------------------------ what people add
    def set_figure(self, pid: str, key: str, value: Any, user: Dict[str, Any]) -> Dict[str, Any]:
        if key not in FIGURES:
            raise ValueError(f"unknown figure {key!r}")
        if user.get("role") not in MONEY_ROLES:
            raise PermissionError("Only JP Sir and Rakesh Sir can change a money figure.")
        kind = FIGURES[key]["kind"]
        cleared = value is None or str(value).strip() == ""
        new: Any = None
        if not cleared:
            new = _day(value) if kind == "date" else _num(value)
            if new is None:
                raise ValueError("That is not a date (use YYYY-MM-DD)." if kind == "date" else "That is not a number.")
            if kind == "percent" and not 0 < new <= 40:
                raise ValueError("An interest rate is a number such as 14 or 18.")
        was = ((self.inputs.find_one({"property_id": pid}, {"figures": 1}) or {}).get("figures") or {}).get(key, {}).get("value")
        who = {"by": user.get("user_id"), "by_name": user.get("name") or user.get("user_id"), "at": now()}
        log = {"what": "figure", "key": key, "label": FIGURES[key]["label"], "value": new, "was": was, **who}
        if cleared:
            self.inputs.update_one({"property_id": pid}, {"$unset": {f"figures.{key}": ""}, "$push": {"history": log}}, upsert=True)
        else:
            self.inputs.update_one({"property_id": pid}, {"$set": {f"figures.{key}": {"value": new, **who}}, "$push": {"history": log}}, upsert=True)
        return {"key": key, "value": new, "cleared": cleared}

    def set_line(self, pid: str, key: str, user: Dict[str, Any], *, work_done: Any = None, note: Optional[str] = None) -> Dict[str, Any]:
        if user.get("role") not in LINE_ROLES:
            raise PermissionError("Only RKB's team can write on the sheet.")
        if not re.fullmatch(r"[ex][0-9a-f]+", key or ""):
            raise ValueError("unknown line")
        who = {"by": user.get("user_id"), "by_name": user.get("name") or user.get("user_id"), "at": now()}
        fields: Dict[str, Any] = {f"lines.{key}.{k}": v for k, v in who.items()}
        log: Dict[str, Any] = {"what": "line", "key": key, **who}
        if work_done is not None:
            pct = None if str(work_done).strip() == "" else _num(work_done)
            if pct is not None and not 0 <= pct <= 100:
                raise ValueError("Work done is a percentage from 0 to 100.")
            fields[f"lines.{key}.work_done"] = None if pct is None else int(round(pct))
            log["work_done"] = fields[f"lines.{key}.work_done"]
        if note is not None:
            fields[f"lines.{key}.note"] = note.strip()[:500]
            log["note"] = fields[f"lines.{key}.note"]
        self.inputs.update_one({"property_id": pid}, {"$set": fields, "$push": {"history": log}}, upsert=True)
        return {"key": key, **{k.rsplit(".", 1)[1]: v for k, v in fields.items()}}

    # ------------------------------------------------------------ Excel
    def xlsx(self, pid: str) -> bytes:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        s = self.build(pid)
        wb = Workbook()
        ws = wb.active
        ws.title = "Money sheet"
        bold, grey = Font(bold=True), PatternFill("solid", fgColor="EEF1F5")
        ws.append([s.get("address") or pid])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([f"Made {now().strftime('%d %b %Y %H:%M')} UTC from Buildertrend and RKB's records. Internal to RKB."])
        ws.append([])
        for f in s["figures"].values():
            v = f["value"]
            v = v.strftime("%Y-%m-%d") if isinstance(v, datetime) else v
            ws.append([f["label"], v, (f"set by {f['set_by']}" if f.get("set_by") else f.get("source") or "")])
        it = s["deal"]["interest"]
        ws.append(["Interest to the homeowner starts", it["starts"].strftime("%Y-%m-%d") if it.get("starts") else None, it.get("why_not") or "six months after closing"])
        ws.append(["Daily interest from then", it.get("daily"), ""])
        ws.append(["Interest accrued to date", it.get("accrued"), f"{it.get('days')} days" if it.get("days") is not None else ""])
        mk = s["deal"]["markup"]
        if mk:
            ws.append([])
            for label, k in (("Price to the homeowner", "price"), ("Builder cost", "builder_cost"), ("Markup", "markup"), ("Less 2 points", "points"),
                             ("Less six months' interest", "six_months_interest"), ("Left to share", "remainder"), ("RKB's half", "rkb_half"),
                             ("ROI Blocks at the start", "roi_at_start"), ("ROI Blocks after the sale", "roi_after_sale")):
                ws.append([label, mk[k], mk["basis"] if k == "points" else ""])
        ws.append([])
        head = ["Group", "Line item", "Cost code", "Proposal (builder cost)", "Change orders", "Revised", "Billed", "In review", "To pay", "Paid",
                "Held back", "Left to bill", "% billed", "Work done %", "Note", "Price to homeowner"]
        ws.append(head)
        top = ws.max_row
        for c in ws[top]:
            c.font, c.fill, c.alignment = bold, grey, Alignment(wrap_text=True, vertical="top")
        for r in s["rows"]:
            ws.append([r["group"], r["title"], r["cost_code"], r["proposal"], r["change_orders"], r["revised"], r["billed"], r["in_review"], r["to_pay"], r["paid"],
                       r["held_back"], r["left"], r["billed_percent"], r["work_done"], r["note"], r["price"]])
        t = s["totals"]
        ws.append(["", "Total", "", t["proposal"], t["change_orders"], t["revised"], t["billed"], t["in_review"], t["to_pay"], t["paid"], t["held_back"], t["left"],
                   t["billed_percent"], None, "", t["price"]])
        for c in ws[ws.max_row]:
            c.font = bold
        for row in ws.iter_rows(min_row=top + 1, min_col=4, max_col=12):
            for c in row:
                c.number_format = "#,##0.00"
        for col in ("P",):
            for c in ws[col][top:]:
                c.number_format = "#,##0.00"
        for i, w in enumerate([30, 34, 26, 16, 14, 14, 14, 12, 12, 14, 12, 14, 9, 11, 40, 16], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = ws.cell(row=top + 1, column=3)
        b = wb.create_sheet("Bills")
        b.append(["Bill date", "Bill", "Pay to", "Amount", "Buildertrend status", "Hold-back", "Manju Sir approved", "Team reviewed", "Ready to pay", "Paid"])
        for c in b[1]:
            c.font, c.fill = bold, grey
        for x in s["bills"]:
            st = x["steps"]
            tick = lambda k: (st.get(k) or {}).get("at").strftime("%Y-%m-%d") if (st.get(k) or {}).get("at") else ""
            b.append([x["date"].strftime("%Y-%m-%d") if x.get("date") else "", x["title"], x["pay_to"], x["amount"], x["status"], "yes" if x["holdback"] else "",
                      tick("manju_approved"), tick("team_reviewed"), tick("ready_to_pay"), tick("paid")])
        for i, w in enumerate([12, 44, 26, 14, 20, 10, 18, 14, 14, 12], 1):
            b.column_dimensions[get_column_letter(i)].width = w
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
