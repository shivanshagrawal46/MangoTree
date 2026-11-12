"""The two emails a bill sends by itself, from Rakesh Sir's mailbox.

    "Ready to pay" ticked  ->  JP Sir: the bill, and everything about the property
    "Paid" ticked          ->  Wes and Kelly, copy Manjunath Sir and Shivansh:
                               the bill, when each step happened, and how long
                               it took from the bill date to payment

Nobody writes or approves these. They are filled in from the records — no model
is asked for a word — so every figure in them is the figure on the Bills page.
Every date and time is US Eastern (see ``core/eastern.py``).

Three rules keep an automatic email from doing harm:

* It waits a short while before it goes (``hold_seconds``, two minutes). A box
  ticked by mistake and unticked inside that time sends nothing.
* One email per bill per step. Ticking, unticking and ticking again after the
  email has gone does not send a second one.
* Hold-back bills never send anything: they are not bills being paid now.

The email is put on the system's outbox (``mail/outbox.py``), which the server
sends from. Who receives each email is a setting (``bill_mail``), not code.
"""
from __future__ import annotations

import html as _html
from datetime import timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mangotree.config.registry import PROPERTY_INDEX
from mangotree.core import eastern
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from .billtrack import STEPS
from .buildertrend import Buildertrend, is_holdback, usd
from .store import BrainStore, name_of, now

KIND = "bill_step"
SETTING = "bill_mail"
DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "hold_seconds": 120,
    "ready_to": [["JP Sir", "jp@mtreh.com"]],
    "paid_to": [["Wes Stone", "wes@roiblocks.com"], ["Kelly Stone", "kelly@roiblocks.com"]],
    "paid_cc": [["Manjunath Sir", "manjunath@mtreh.com"], ["Shivansh", "shivansh@mtreh.com"]],
}
SENDS_ON = ("ready_to_pay", "paid")
_STEP_LABEL = dict(STEPS)


def _pairs(rows: Any) -> List[Tuple[str, str]]:
    return [(str(r[0]), str(r[1]).strip().lower()) for r in rows or [] if isinstance(r, (list, tuple)) and len(r) == 2 and "@" in str(r[1])]


def _address(pid: Optional[str]) -> str:
    p = PROPERTY_INDEX.get(pid) if pid else None
    if not p:
        return name_of(pid) if pid else "not stated"
    return ", ".join(x for x in (p.canonical_address, p.city, f"{p.state or ''} {p.postal or ''}".strip()) if x)


def _table(rows: Sequence[Tuple[str, str]]) -> str:
    cells = "".join(f'<tr><td style="padding:4px 18px 4px 0;color:#555;vertical-align:top;white-space:nowrap">{_html.escape(k)}</td>'
                    f'<td style="padding:4px 0;vertical-align:top">{_html.escape(v)}</td></tr>' for k, v in rows)
    return f'<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:14px">{cells}</table>'


def _text_rows(rows: Sequence[Tuple[str, str]]) -> str:
    w = max((len(k) for k, _ in rows), default=0)
    return "\n".join(f"{k:<{w}}   {v}" for k, v in rows)


def _wrap(parts: Sequence[str]) -> str:
    return '<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#111;line-height:1.5">' + "".join(parts) + "</div>"


def _h(title: str) -> str:
    return f'<p style="margin:18px 0 6px;font-weight:600">{_html.escape(title)}</p>'


def _p(text: str, *, small: bool = False) -> str:
    style = "margin:10px 0;color:#666;font-size:12px" if small else "margin:10px 0"
    return f'<p style="{style}">{_html.escape(text)}</p>'


class BillMail:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None, outbox=None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self._outbox = outbox

    # ------------------------------------------------------------- plumbing
    def outbox(self):
        if self._outbox is None:
            from mangotree.mail.outbox import Outbox
            self._outbox = Outbox(self.mongo)
        return self._outbox

    def settings(self) -> Dict[str, Any]:
        s = self.store.setting(SETTING, None)
        return {**DEFAULTS, **(s if isinstance(s, dict) else {})}

    # ------------------------------------------------------- what the emails say
    @staticmethod
    def bill_rows(r: Dict[str, Any]) -> List[Tuple[str, str]]:
        bill_at = eastern.wall_clock_et(r.get("bill_date"))
        rows = [("Bill", str(r.get("title") or r.get("number") or "")), ("Bill number", str(r.get("number") or "not stated")),
                ("Property", _address(r.get("property_id"))), ("Pay to", str(r.get("pay_to") or "not stated")),
                ("Amount", usd(r.get("amount"))), ("Bill date", eastern.label(bill_at))]
        return rows

    @staticmethod
    def step_rows(r: Dict[str, Any]) -> List[Tuple[str, str]]:
        """Bill date, then each of RKB's four steps with the Eastern time it was ticked and by whom."""
        rows = [("Bill date", eastern.label(eastern.wall_clock_et(r.get("bill_date"))))]
        for key, label in STEPS:
            m = (r.get("steps") or {}).get(key)
            rows.append((label, f"{eastern.label(eastern.to_et(m['at']))}{' — ' + str(m.get('by_name')) if m.get('by_name') else ''}" if m else "not recorded"))
        return rows

    def property_rows(self, pid: str) -> List[Tuple[str, str]]:
        """Everything on file about the property, read straight from the records (no model)."""
        from . import snapshot
        f = self.store.get_file(pid) or {}
        rows: List[Tuple[str, str]] = [("Address", _address(pid))]
        st = snapshot.stage_view(f) if f else None
        if st:
            where = st["steps"][st["index"]]["label"]
            rows.append(("Stage", where + (" — in legal recovery" if st.get("recovery") else "")))
            if st.get("recovery") and st.get("recovery_note"):
                rows.append(("Recovery", str(st["recovery_note"])))
            if st.get("closing_date"):
                rows.append(("Closed", eastern.label(st["closing_date"], time=False)))
            rows.append(("Construction due", eastern.label(st["construction_expected"], time=False) if st.get("construction_expected") else "no completion date on record"))
            if st.get("site_now"):
                rows.append(("Latest on site", str(st["site_now"])))
        if f.get("headline"):
            rows.append(("Where it stands", str(f["headline"])))
        named = set()
        for p in snapshot.people(self.mongo, self.store, pid, f) if f else []:
            if p["role"] in ("Homeowner / borrower", "Contractor", "Title company") and p["role"] not in named:
                named.add(p["role"])                      # one line per party: the first on file
                rows.append((p["role"], p["value"][:240]))
        nums = snapshot.numbers(self.store, pid, f) if f else {"main": [], "extra": {}}
        for n in nums["main"]:
            rows.append((n["label"], str(n["value"]) + (" (from the emails, not RKB's workbook)" if n["from"] == "file" else "") if n.get("value") else "not on record"))
        for key in ("rate", "total_due"):
            if key in nums["extra"]:
                rows.append((nums["extra"][key]["label"], str(nums["extra"][key]["value"])))
        arv = snapshot.arv_of(self.mongo, pid)
        if arv and arv.get("value") is not None:
            rows.append(("ARV", f"${arv['value']:,.0f} (range ${arv['low']:,.0f}–${arv['high']:,.0f})" + (" — run by the contractor" if arv.get("from_contractor") else "")))
        return rows

    def property_bill_rows(self, pid: str, this_bill: int) -> Tuple[List[Tuple[str, str]], List[Dict[str, Any]]]:
        """The contractor's bills on this property in Buildertrend, and the other bills still to process."""
        bt = Buildertrend(self.mongo)
        bills = bt.bills_of(pid)
        if not bills:
            return [], []
        t = bt.bill_totals(pid)
        held = [b for b in bills if b.get("holdback") and b.get("status") != "Paid"]
        ready = [b for b in bills if b.get("status") == "Ready for payment" and not b.get("holdback")]
        review = [b for b in bills if b.get("in_review") and not b.get("holdback")]
        rows = [("Billed in Buildertrend", f"{usd(t['billed'])} across {t['count']} bills"), ("Paid in Buildertrend", usd(t["paid"])),
                ("Ready for payment there", f"{len(ready)} bills, {usd(round(sum(b.get('balance') or 0 for b in ready), 2))}"),
                ("In review there", f"{len(review)} bills, {usd(round(sum(b.get('amount') or 0 for b in review), 2))}"),
                ("Held back (10% of each draw)", f"{usd(round(sum((b.get('balance') if b.get('balance') is not None else b.get('amount')) or 0 for b in held), 2))} across {len(held)} hold-back bills")]
        return rows, [b for b in review if b.get("bill_id") != this_bill]

    def compose(self, r: Dict[str, Any], step: str, by: str) -> Dict[str, Any]:
        """The email for this bill and step: recipients, subject, HTML and plain text."""
        s = self.settings()
        title, prop, amount = str(r.get("title") or r.get("number") or "Bill"), name_of(r["property_id"]) if r.get("property_id") else "property not stated", usd(r.get("amount"))
        bill, steps = self.bill_rows(r), self.step_rows(r)
        if step == "ready_to_pay":
            to, cc = _pairs(s["ready_to"]), []
            first = " and ".join(n for n, _ in to) or "JP Sir"
            # Only the lines this bill actually charges: Buildertrend lists every budget line, most of them at $0.00.
            lines = [(li.get("title") or li.get("cost_code") or "line", usd(li.get("amount")) + (f" — {li['cost_code']}" if li.get("cost_code") else ""))
                     for li in r.get("line_items") or [] if li.get("amount")]
            extra = [("Due", eastern.label(r.get("due_day"), time=False) + (" — past due" if r.get("past_due") else ""))] if r.get("due_day") else []
            if r.get("created_by"):
                extra.append(("Entered in Buildertrend by", str(r["created_by"])))
            pid = r.get("property_id")
            prop_rows = self.property_rows(pid) if pid else []
            bt_rows, others = self.property_bill_rows(pid, r["bill_id"]) if pid else ([], [])
            other_rows = [(str(b.get("title") or b.get("number")), f"{usd(b.get('amount'))} — bill date {eastern.label(eastern.wall_clock_et(b.get('invoice_date')), time=False)}") for b in others]
            intro = f"{first}, this bill has been marked ready to pay."
            foot = f"Sent automatically from MangoTree when “Ready to pay” was ticked by {by}. All times are US Eastern."
            parts = [_p(intro), _h("The bill"), _table(bill + extra)]
            text = [intro, "", "THE BILL", _text_rows(bill + extra)]
            if lines:
                parts += [_h("Line items"), _table(lines)]
                text += ["", "LINE ITEMS", _text_rows(lines)]
            parts += [_h("Steps so far"), _table(steps), _h("The property"), _table(prop_rows)]
            text += ["", "STEPS SO FAR", _text_rows(steps), "", "THE PROPERTY", _text_rows(prop_rows)]
            if bt_rows:
                parts += [_h("Bills on this property"), _table(bt_rows)]
                text += ["", "BILLS ON THIS PROPERTY", _text_rows(bt_rows)]
            if other_rows:
                parts += [_h("Other bills on this property waiting for approval"), _table(other_rows)]
                text += ["", "OTHER BILLS ON THIS PROPERTY WAITING FOR APPROVAL", _text_rows(other_rows)]
            parts.append(_p(foot, small=True))
            text += ["", foot]
            subject = f"Ready to pay — {title} — {prop} — {amount}"
        elif step == "paid":
            to, cc = _pairs(s["paid_to"]), _pairs(s["paid_cc"])
            first = ", ".join(n.split()[0] for n, _ in to) or "Wes, Kelly"
            paid = (r.get("steps") or {}).get("paid") or {}
            bill_at = eastern.wall_clock_et(r.get("bill_date"))
            within = eastern.took(bill_at, eastern.to_et(paid.get("at")))
            summary = bill + [("Paid", eastern.label(eastern.to_et(paid.get("at")))), ("Paid within", f"{within} of the bill date")]
            intro = f"{first}, this bill has been paid."
            foot = "This notice was sent automatically when the bill was marked paid. All times are US Eastern."
            parts = [_p(intro), _table(summary), _h("From bill date to paid"), _table(steps), _p("Rakesh Bhargava"), _p("RKB Consulting Group"), _p(foot, small=True)]
            text = [intro, "", _text_rows(summary), "", "FROM BILL DATE TO PAID", _text_rows(steps), "", "Rakesh Bhargava", "RKB Consulting Group", "", foot]
            subject = f"Paid — {title} — {prop} — {amount}"
        else:
            raise ValueError(f"no email is sent for the step {step!r}")
        return {"to": to, "cc": cc, "subject": subject, "html": _wrap(parts), "text": "\n".join(text)}

    # ---------------------------------------------------------------- the tick
    def on_step(self, r: Dict[str, Any], step: str, done: bool, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Called after a box is ticked or unticked. Queues the email, or cancels it if it has not gone yet."""
        if step not in SENDS_ON:
            return None
        s = self.settings()
        ref = f"{r['bill_id']}:{step}"
        coll = self.outbox().coll
        if not done:
            res = coll.update_one({"kind": KIND, "ref": ref, "status": {"$in": ["queued", "needs_consent"]}}, {"$set": {"status": "cancelled", "cancelled_at": now()}})
            return {"status": "cancelled"} if res.modified_count else self.state(r["bill_id"]).get(step)
        if not s.get("enabled", True):
            return {"status": "off"}
        if is_holdback(r.get("title")):
            return {"status": "not_sent", "why": "hold-back bills do not send an email"}
        try:
            mail = self.compose(r, step, user.get("name") or user.get("user_id") or "someone")
            if not mail["to"]:
                return {"status": "not_sent", "why": "no recipient is set"}
            send_after = now() + timedelta(seconds=max(0, int(s.get("hold_seconds", 120))))
            self.outbox().queue(kind=KIND, ref=ref, to=mail["to"], cc=mail["cc"], subject=mail["subject"], html=mail["html"], text=mail["text"],
                                send_after=send_after, meta={"bill_id": r["bill_id"], "step": step, "property_id": r.get("property_id"), "by": user.get("user_id")})
        except Exception as exc:
            logger.exception("bill email for %s could not be queued", ref)
            return {"status": "failed", "why": str(exc)[:200]}
        return self.state(r["bill_id"]).get(step)

    def states(self, bill_ids: Sequence[int]) -> Dict[int, Dict[str, Dict[str, Any]]]:
        """For the Bills page: what happened to each bill's emails (waiting to go, sent, cancelled)."""
        out: Dict[int, Dict[str, Dict[str, Any]]] = {}
        if not bill_ids:
            return out
        for d in self.outbox().coll.find({"kind": KIND, "meta.bill_id": {"$in": list(bill_ids)}},
                                         {"_id": 0, "meta": 1, "status": 1, "send_after": 1, "sent_at": 1, "queued_at": 1, "to": 1, "cc": 1, "error": 1}).sort("queued_at", 1):
            m = d.get("meta") or {}
            out.setdefault(m.get("bill_id"), {})[m.get("step")] = {
                "status": d.get("status"), "send_after": d.get("send_after"), "sent_at": d.get("sent_at"), "error": d.get("error"),
                "to": [x.get("name") or x.get("address") for x in d.get("to") or []], "cc": [x.get("name") or x.get("address") for x in d.get("cc") or []]}
        return out

    def state(self, bill_id: int) -> Dict[str, Dict[str, Any]]:
        return self.states([bill_id]).get(bill_id, {})
