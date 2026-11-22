"""Entered by the contractor — bills, daily logs and change orders put straight into RKB's system.

Until 2026-11-22 everything the contractor recorded reached RKB one way: copied out of the
contractor's Buildertrend account each morning. A bill entered at 3 pm was not here until
the next day, the copy needed a person at a laptop, and each bill was approved twice (admin,
2026-11-22: "bills are sent in after our office times ... if this system works very well we
can process it in less than a day"). Here the contractor enters the record in RKB's own
portal and RKB has it the moment it is sent.

What is entered is written in the SAME shape as a record copied from Buildertrend (``bt_bills``,
``bt_daily_logs``, ``bt_photos``, ``bt_bill_files``, ``bt_change_orders``), marked
``source: "contractor"``. So the Bills page, the bill check, the four ticks and their emails,
the money sheet and the site reading all work on it unchanged — there is one bill list, not two.

Rules the contractor cannot get round (they are checked again on the server when it sends):

* a bill line cannot ask for more than is left on its proposal line — extra work needs a
  change order RKB has agreed to;
* a bill needs its invoice, its receipts when it is for materials, and photos of the work;
* the invoice total must equal the lines;
* for the contractor's own work, 10% of every bill is held back (the Construction Rider's
  retainage): the bill is stored as the 90% to pay and the 10% held, as it always was;
* a change order can be billed only after RKB has agreed to it. The homeowner's approval
  stays in Buildertrend: the contractor attaches the approved copy.

Which jobs this is open on is a setting (``entry_properties``; Durham first).
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core import eastern
from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from .buildertrend import Buildertrend, photo_store
from .store import BrainStore, name_of, now

SETTING_PROPERTIES = "entry_properties"
DEFAULT_PROPERTIES = ("durham_st",)
HOLDBACK = 0.10
MIN_BILL_PHOTOS = 3
MIN_LOG_PHOTOS = 3
MIN_CO_PHOTOS = 2
MAX_FILE = 25 * 1024 * 1024
#: Ids given here start far above Buildertrend's own (nine digits), so the two never meet.
ID_BASE = 9_000_000_000

KINDS = {"work": "Contractor's work", "materials": "Materials", "pm": "Project management"}
ROLES = ("invoice", "receipt", "photo", "waiver", "approval")
PHOTO_EXT = ("jpg", "jpeg", "png", "heic", "heif", "webp")
PAPER_EXT = PHOTO_EXT + ("pdf",)
#: The tags a daily log can carry — the parts of a job, in the order work is done.
LOG_TAGS = ("Demolition", "Framing", "Roof / exterior", "Plumbing", "Electrical", "HVAC", "Insulation / drywall", "Kitchen", "Bathroom",
            "Flooring", "Paint", "Basement", "Clean-up")
STATUS = {"draft": "Not sent yet", "sent": "RKB is checking", "approved": "Approved", "ready": "Approved · ready to pay", "paid": "Paid"}


class EntryError(ValueError):
    """Something the contractor has to put right before the record can be saved or sent."""


def _r2(v: Any) -> float:
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def wall_clock(moment: Optional[datetime] = None) -> datetime:
    """Now as the firm's wall-clock time, stored the way Buildertrend's dates are stored (the
    Eastern time of day, with no shift) — so a bill entered here sorts and reads beside one copied from there."""
    return eastern.to_et(moment or now()).replace(tzinfo=timezone.utc)


def _day(text: Any) -> Optional[datetime]:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(text or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def photo_facts(blob: bytes) -> Dict[str, Any]:
    """When and where a photo was taken, when the camera wrote it into the file."""
    out: Dict[str, Any] = {}
    try:
        from PIL import Image
        exif = Image.open(io.BytesIO(blob)).getexif()
        taken = exif.get(36867) or exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if taken:
            out["taken_at"] = str(taken)[:19]
        gps = exif.get_ifd(0x8825)
        if gps and gps.get(2) and gps.get(4):
            def deg(v, ref):
                d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
                return round(-d if ref in ("S", "W") else d, 6)
            out["gps"] = [deg(gps[2], gps.get(1)), deg(gps[4], gps.get(3))]
    except Exception:
        pass
    return out


class Entry:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None, bt: Optional[Buildertrend] = None, files=None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.bt = bt or Buildertrend(mongo)
        db = mongo.db
        self.bills, self.logs, self.cos = db["entry_bills"], db["entry_logs"], db["entry_change_orders"]
        self.files, self.counters = db["entry_files"], db["entry_counters"]
        self._files = files                              # where the bytes go (the tests give their own)

    # ------------------------------------------------------------ who may, and where
    def properties(self) -> List[str]:
        return list(self.store.setting(SETTING_PROPERTIES, list(DEFAULT_PROPERTIES)) or [])

    def jobs_of(self, user: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The jobs this contractor account may enter records on: its own properties that are open for entry."""
        mine = set(user.get("property_ids") or [])
        out = []
        for pid in self.properties():
            if pid in mine and self.job_id(pid):
                out.append({"property_id": pid, "name": name_of(pid)})
        return out

    def check(self, user: Dict[str, Any], pid: str) -> None:
        if pid not in {j["property_id"] for j in self.jobs_of(user)}:
            raise PermissionError("This job is not open to your account.")

    def job_id(self, pid: str) -> Optional[int]:
        j = self.bt.jobs.find_one({"property_id": pid}, {"job_id": 1})
        return int(j["job_id"]) if j and j.get("job_id") else None

    def _next(self, what: str) -> int:
        from pymongo import ReturnDocument
        d = self.counters.find_one_and_update({"_id": what}, {"$inc": {"n": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
        return ID_BASE + int(d["n"])

    # ------------------------------------------------------------ the bytes
    def _bytes_store(self):
        if self._files is not None:
            return self._files
        store = photo_store()
        if store is not None:
            return store
        from mangotree.config.settings import SETTINGS
        from mangotree.storage.objectstore import LocalObjectStore
        return LocalObjectStore(SETTINGS.raw_store)

    def add_file(self, user: Dict[str, Any], pid: str, *, role: str, name: str, blob: bytes) -> Dict[str, Any]:
        """Keep one paper or photo. It is stored at once, by what it contains; it becomes part of a
        bill, a log or a change order when that record names it."""
        self.check(user, pid)
        if role not in ROLES:
            raise EntryError("That is not a kind of file a record can carry.")
        if not blob:
            raise EntryError(f"{name}: the file is empty.")
        if len(blob) > MAX_FILE:
            raise EntryError(f"{name} is larger than {MAX_FILE // (1024 * 1024)} MB.")
        ext = (name.rsplit(".", 1)[-1] if "." in name else "").lower()
        if ext not in (PHOTO_EXT if role == "photo" else PAPER_EXT):
            raise EntryError(f"{name}: a photo must be a picture file." if role == "photo" else f"{name}: attach a PDF or a picture.")
        sha = hashlib.sha256(blob).hexdigest()
        # A picture of an invoice or a receipt is a paper to be read, not a photo of the work.
        is_photo = ext in PHOTO_EXT and role == "photo"
        self._bytes_store().put(sha, blob, name, {"source": "contractor", "property_id": pid, "uploaded_by": user.get("user_id") or ""})
        doc = {"file_id": self._next("file"), "property_id": pid, "role": role, "name": name[:160], "ext": ext, "sha256": sha, "bytes": len(blob),
               "is_photo": is_photo, "by": user.get("user_id"), "by_name": user.get("name"), "at": now(), **(photo_facts(blob) if is_photo else {})}
        self.files.insert_one(dict(doc))
        return doc

    def file(self, file_id: int) -> Optional[Dict[str, Any]]:
        return self.files.find_one({"file_id": int(file_id)}, {"_id": 0})

    def file_bytes(self, file_id: int) -> Optional[bytes]:
        f = self.file(file_id)
        return self._bytes_store().get(f["sha256"]) if f else None

    def _held(self, pid: str, ids: Sequence[Any], roles: Sequence[str]) -> List[Dict[str, Any]]:
        want = [int(i) for i in ids or [] if str(i).lstrip("-").isdigit()]
        found = {f["file_id"]: f for f in self.files.find({"file_id": {"$in": want}, "property_id": pid, "role": {"$in": list(roles)}}, {"_id": 0})}
        return [found[i] for i in want if i in found]

    # ------------------------------------------------------------ what can be billed
    def lines(self, pid: str, *, without: Sequence[int] = ()) -> Dict[str, Any]:
        """The proposal's lines and the agreed change orders, each with what it is worth, what has
        been billed on it (paid and held alike) and what is left. ``without``: bills to leave out
        of "billed" — the bill being edited."""
        billed: Dict[int, float] = {}
        skip = {int(i) for i in without}
        for b in self.bt.bills.find({"property_id": pid}, {"bill_id": 1, "line_items": 1}):
            if b["bill_id"] in skip:
                continue
            for li in b.get("line_items") or []:
                if li.get("po_line_id") and li.get("amount"):
                    billed[li["po_line_id"]] = billed.get(li["po_line_id"], 0.0) + float(li["amount"])
        out: List[Dict[str, Any]] = []
        est = self.bt.estimate_of(pid) or {}
        for g in est.get("groups") or []:
            for li in g.get("lines") or []:
                cost = _r2(li.get("builder_cost"))
                if not li.get("po_line_id") or not cost or not (li.get("title") or "").strip():
                    continue                      # a line with nothing on it, or one Buildertrend cannot be billed through
                done = _r2(billed.get(li["po_line_id"]))
                out.append({"key": str(li["po_line_id"]), "po_line_id": li["po_line_id"], "group": g.get("title") or "", "title": li["title"].strip(),
                            "cost_code": li.get("cost_code") or "", "proposal": cost, "billed": done, "left": _r2(cost - done), "change_order": None})
        for c in self.bt.change_orders.find({"property_id": pid, "approved": True}, {"_id": 0}).sort("number", 1):
            ids = list(c.get("po_line_ids") or [])
            parts = c.get("lines") or []
            for n, po in enumerate(ids):
                cost = _r2(parts[n].get("builder_cost")) if len(parts) == len(ids) else (_r2(c.get("builder_cost")) if n == 0 else 0.0)
                if not cost:
                    continue
                done = _r2(billed.get(po))
                out.append({"key": str(po), "po_line_id": po, "group": "Approved change orders", "cost_code": (parts[n].get("cost_code") if len(parts) == len(ids) else "") or "",
                            "title": f"CO {c.get('number') or ''} — {c.get('title') or ''}".strip(" —"), "proposal": cost, "billed": done, "left": _r2(cost - done),
                            "change_order": c.get("number") or str(c.get("co_id"))})
        totals = {"proposal": _r2(sum(l["proposal"] for l in out)), "billed": _r2(sum(l["billed"] for l in out)), "left": _r2(sum(l["left"] for l in out))}
        held = _r2(sum(_r2(b.get("balance") if b.get("balance") is not None else b.get("amount"))
                       for b in self.bt.bills.find({"property_id": pid, "holdback": True, "status": {"$ne": "Paid"}}, {"amount": 1, "balance": 1, "bill_id": 1})
                       if b["bill_id"] not in skip))
        return {"lines": out, "totals": totals, "held": held}

    def pay_to(self, pid: str) -> List[str]:
        names: Dict[str, int] = {}
        for b in self.bt.bills.find({"property_id": pid}, {"pay_to": 1}):
            n = (b.get("pay_to") or "").strip()
            if n and n.lower() not in ("unassigned", "buildertrend misc."):
                names[n] = names.get(n, 0) + 1
        return sorted(names, key=lambda n: -names[n])

    # ------------------------------------------------------------ a bill
    def _bill_view(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """A bill as the contractor sees it: its figures, what is missing, and where it stands at RKB."""
        pid = d["property_id"]
        book = self.lines(pid, without=d.get("bill_ids") or [])
        by_key = {l["key"]: l for l in book["lines"]}
        rows, total = [], 0.0
        for key, amount in (d.get("lines") or {}).items():
            l = by_key.get(str(key))
            amount = _r2(amount)
            if not amount:
                continue
            total += amount
            rows.append({"key": str(key), "amount": amount, "title": (l or {}).get("title") or "A line that is no longer on the proposal",
                         "left": (l or {}).get("left"), "over": bool(l is None or amount > l["left"] + 0.005)})
        total = _r2(total)
        held = _r2(sum(r["amount"] - _r2(r["amount"] * (1 - HOLDBACK)) for r in rows)) if d.get("kind") == "work" else 0.0     # line by line, as it is stored
        files = self._held(pid, d.get("files") or [], ROLES)
        count = lambda role: sum(1 for f in files if f["role"] == role)        # noqa: E731
        problems: List[Dict[str, str]] = []
        if not rows:
            problems.append({"code": "lines", "text": "Put an amount on at least one line."})
        for r in rows:
            if r["over"]:
                problems.append({"code": "over", "text": f"{r['title']} is over what is left" + (f" (${r['left']:,.2f})" if r["left"] is not None else "") +
                                 ". Lower it, or enter a change order and wait for RKB to agree."})
        if not (d.get("pay_to") or "").strip():
            problems.append({"code": "pay_to", "text": "Say who is to be paid."})
        if not (d.get("invoice_no") or "").strip():
            problems.append({"code": "invoice_no", "text": "Give the invoice number."})
        if not count("invoice"):
            problems.append({"code": "invoice", "text": "Attach the invoice."})
        if d.get("kind") == "materials" and not count("receipt"):
            problems.append({"code": "receipt", "text": "Attach the receipts for the materials."})
        if d.get("kind") != "pm" and count("photo") < MIN_BILL_PHOTOS:
            n = MIN_BILL_PHOTOS - count("photo")
            problems.append({"code": "photos", "text": f"{n} more photo{'s' if n != 1 else ''} of this work needed (at least {MIN_BILL_PHOTOS})."})
        if rows and abs(_r2(d.get("invoice_total")) - total) > 0.005:
            problems.append({"code": "total", "text": f"The invoice total (${_r2(d.get('invoice_total')):,.2f}) is not the same as the lines (${total:,.2f})."})
        return {"entry_id": d["entry_id"], "property_id": pid, "property": name_of(pid), "kind": d.get("kind") or "work", "kind_name": KINDS.get(d.get("kind") or "work"),
                "pay_to": d.get("pay_to") or "", "invoice_no": d.get("invoice_no") or "", "invoice_date": d.get("invoice_date"), "invoice_total": _r2(d.get("invoice_total")),
                "work_from": d.get("work_from"), "work_to": d.get("work_to"), "note": d.get("note") or "",
                "lines": {r["key"]: r["amount"] for r in rows}, "rows": rows, "total": total, "holdback": held, "pay_now": _r2(total - held),
                "files": files, "problems": problems, "can_send": not problems and d.get("status") == "draft",
                "status": d.get("status") or "draft", "number": d.get("number"), "bill_id": (d.get("bill_ids") or [None])[0],
                "created_at": d.get("created_at"), "sent_at": d.get("sent_at"), "by_name": d.get("by_name"), **self._standing(d)}

    def _standing(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Where a sent bill stands at RKB, from the ticks RKB's people have made on it."""
        if d.get("status") != "sent" or not d.get("bill_ids"):
            return {"stands": STATUS["draft"], "stage": "draft", "can_withdraw": False, "steps": {}}
        t = self.mongo.db["bill_tracking"].find_one({"bill_id": d["bill_ids"][0]}, {"steps": 1, "comment": 1}) or {}
        steps = {k: (v or {}).get("at") for k, v in (t.get("steps") or {}).items() if v}
        stage = "paid" if steps.get("paid") else "ready" if steps.get("ready_to_pay") else "approved" if steps.get("manju_approved") else "sent"
        return {"stands": STATUS[stage], "stage": stage, "can_withdraw": not steps, "steps": steps}

    def bills_of(self, user: Dict[str, Any], pid: str) -> List[Dict[str, Any]]:
        self.check(user, pid)
        return [self._bill_view(d) for d in self.bills.find({"property_id": pid, "status": {"$in": ["draft", "sent"]}}, {"_id": 0}).sort("created_at", -1)]

    def bill(self, user: Dict[str, Any], entry_id: str) -> Dict[str, Any]:
        d = self.bills.find_one({"entry_id": entry_id}, {"_id": 0})
        if not d:
            raise EntryError("That bill is not on file.")
        self.check(user, d["property_id"])
        return self._bill_view(d)

    def save_bill(self, user: Dict[str, Any], pid: str, body: Dict[str, Any], entry_id: Optional[str] = None) -> Dict[str, Any]:
        """Keep a bill as it is being written. Nothing reaches RKB until it is sent."""
        self.check(user, pid)
        kind = body.get("kind") if body.get("kind") in KINDS else "work"
        fields = {"kind": kind, "pay_to": str(body.get("pay_to") or "").strip()[:160], "invoice_no": str(body.get("invoice_no") or "").strip()[:60],
                  "invoice_date": str(body.get("invoice_date") or "")[:10] or None, "invoice_total": _r2(body.get("invoice_total")),
                  "work_from": str(body.get("work_from") or "")[:10] or None, "work_to": str(body.get("work_to") or "")[:10] or None,
                  "note": str(body.get("note") or "").strip()[:1000],
                  "lines": {str(k): _r2(v) for k, v in (body.get("lines") or {}).items() if _r2(v) > 0},
                  "files": [f["file_id"] for f in self._held(pid, body.get("files") or [], ROLES)], "updated_at": now()}
        if entry_id:
            d = self.bills.find_one({"entry_id": entry_id, "property_id": pid})
            if not d:
                raise EntryError("That bill is not on file.")
            if d.get("status") != "draft":
                raise EntryError("This bill has been sent. Take it back first if it has to change.")
            self.bills.update_one({"entry_id": entry_id}, {"$set": fields})
        else:
            entry_id = "b-" + hashlib.sha1(f"{pid}|{user.get('user_id')}|{now().isoformat()}".encode()).hexdigest()[:10]
            self.bills.insert_one({"entry_id": entry_id, "property_id": pid, "status": "draft", "by": user.get("user_id"), "by_name": user.get("name"),
                                   "created_at": now(), **fields})
        return self.bill(user, entry_id)

    def send_bill(self, user: Dict[str, Any], entry_id: str) -> Dict[str, Any]:
        """Send the bill to RKB: it goes onto the Bills page at once, in the shape every other bill has."""
        v = self.bill(user, entry_id)
        if v["status"] != "draft":
            raise EntryError("This bill has already been sent.")
        if v["problems"]:
            raise EntryError("This bill cannot be sent yet: " + " ".join(p["text"] for p in v["problems"]))
        pid = v["property_id"]
        book = {l["key"]: l for l in self.lines(pid)["lines"]}
        when = wall_clock()
        number = f"MT-{self._next('bill_number') - ID_BASE:04d}"
        title = f"{KINDS[v['kind']]} — invoice {v['invoice_no']}"
        invoice_day = _day(v.get("invoice_date"))
        invoice_at = invoice_day.replace(hour=when.hour, minute=when.minute, second=when.second) if invoice_day else when

        def doc(bill_id: int, name: str, held: bool) -> Dict[str, Any]:
            items = []
            for key, amount in v["lines"].items():
                l = book[key]
                # The contractor's own work is split line by line: 90% to pay, and the 10% held is whatever is left of
                # that line to the cent — so the two parts of every line, and of the bill, add up exactly.
                to_pay = _r2(amount * (1 - HOLDBACK)) if v["kind"] == "work" else amount
                part = _r2(amount - to_pay) if held else to_pay
                items.append({"title": l["title"], "description": "", "cost_code": l["cost_code"], "quantity": 1.0, "unit_cost": l["proposal"] if l["change_order"] else part,
                              "amount": part, "variance": "Change Order" if l["change_order"] else None, "po_line_id": l["po_line_id"], "billed_elsewhere": None})
            amount = _r2(sum(i["amount"] for i in items))
            return {"bill_id": bill_id, "job_id": self.job_id(pid), "property_id": pid, "number": number, "title": name, "pay_to": v["pay_to"], "kind": "Bill",
                    "amount": amount, "paid": 0.0, "balance": amount, "status": "In review", "status_code": None, "in_review": True, "bt_in_review": True, "to_approve": True,
                    "holdback": held, "invoice_date": invoice_at, "due_date": None, "past_due": False, "date_paid": None, "created": when,
                    "cost_codes": sorted({i["cost_code"] for i in items if i["cost_code"]}), "variance_codes": [], "approvers": [], "purchase_orders": [],
                    "documents": len(v["files"]), "created_by": user.get("name") or "", "description": v.get("note") or "", "line_items": items,
                    "file_names": [f["name"] for f in v["files"]], "files": [{"file_id": f["file_id"], "title": f["name"], "ext": f["ext"], "size": f["bytes"], "is_photo": f["is_photo"]} for f in v["files"]],
                    "lines_pulled_at": now(), "pulled_at": now(), "source": "contractor", "entry_id": entry_id, "entered_at": now(), "entered_by": user.get("user_id"),
                    "work_from": v.get("work_from"), "work_to": v.get("work_to")}
        ids = [self._next("bill")]
        if v["kind"] == "work":
            ids.append(self._next("bill"))
            docs = [doc(ids[0], f"{title} (Contractor)", False), doc(ids[1], f"{title} (Hold-back)", True)]
        else:
            docs = [doc(ids[0], title, False)]
        self.bt.bills.insert_many([dict(x) for x in docs])
        for f in v["files"]:
            self.bt.record_bill_file(file_id=f["file_id"], bill_id=ids[0], job_id=self.job_id(pid), title=f["name"], ext=f["ext"], sha256=f["sha256"], nbytes=f["bytes"], is_photo=f["is_photo"])
        self.bills.update_one({"entry_id": entry_id}, {"$set": {"status": "sent", "sent_at": now(), "sent_by": user.get("user_id"), "bill_ids": ids, "number": number, "title": title},
                                                       "$push": {"history": {"at": now(), "what": "sent", "by": user.get("user_id"), "bill_ids": ids}}})
        self.bt.__dict__.pop("_views", None)
        logger.info("entry: bill %s sent by %s — %s, $%.2f on %s", number, user.get("user_id"), title, v["total"], pid)
        return self.bill(user, entry_id)

    def withdraw_bill(self, user: Dict[str, Any], entry_id: str) -> Dict[str, Any]:
        """Take a sent bill back to change it. Only while nobody at RKB has ticked anything on it."""
        d = self.bills.find_one({"entry_id": entry_id}, {"_id": 0})
        if not d:
            raise EntryError("That bill is not on file.")
        self.check(user, d["property_id"])
        if d.get("status") != "sent":
            raise EntryError("This bill has not been sent.")
        if not self._standing(d)["can_withdraw"]:
            raise EntryError("RKB has already started on this bill, so it cannot be taken back. Write to RKB if it is wrong.")
        ids = d.get("bill_ids") or []
        self.bt.bills.delete_many({"bill_id": {"$in": ids}, "source": "contractor"})
        self.bt.bill_files.delete_many({"bill_id": {"$in": ids}})
        self.mongo.db["bill_checks"].delete_many({"bill_id": {"$in": ids}})
        self.bills.update_one({"entry_id": entry_id}, {"$set": {"status": "draft", "bill_ids": [], "sent_at": None},
                                                       "$push": {"history": {"at": now(), "what": "taken back", "by": user.get("user_id"), "bill_ids": ids}}})
        self.bt.__dict__.pop("_views", None)
        return self.bill(user, entry_id)

    def discard_bill(self, user: Dict[str, Any], entry_id: str) -> None:
        d = self.bills.find_one({"entry_id": entry_id}, {"_id": 0})
        if d and d.get("status") == "draft":
            self.check(user, d["property_id"])
            self.bills.update_one({"entry_id": entry_id}, {"$set": {"status": "discarded", "updated_at": now()}})

    # ------------------------------------------------------------ a daily log
    def add_log(self, user: Dict[str, Any], pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """One day's log from the site: what was done, with photos. It is on RKB's site page at once."""
        self.check(user, pid)
        today = eastern.to_et(now()).date()
        day = _day(body.get("date")) or datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
        if day.date() > today:
            raise EntryError("A log cannot be written for a day that has not come yet.")
        if (today - day.date()).days > 3:
            raise EntryError("A log can be written for today or the last three days.")
        notes = str(body.get("notes") or "").strip()
        if len(notes) < 10:
            raise EntryError("Say in a sentence what was done.")
        tags = [t for t in body.get("tags") or [] if t in LOG_TAGS]
        if not tags:
            raise EntryError("Tick which work was done.")
        photos = self._held(pid, body.get("files") or [], ("photo",))
        if len(photos) < MIN_LOG_PHOTOS:
            n = MIN_LOG_PHOTOS - len(photos)
            raise EntryError(f"{n} more photo{'s' if n != 1 else ''} needed (at least {MIN_LOG_PHOTOS}).")
        blocked = str(body.get("blocked") or "").strip()[:600]
        log_id, job = self._next("log"), self.job_id(pid)
        when = wall_clock()
        doc = {"log_id": log_id, "job_id": job, "property_id": pid, "date": day.replace(hour=when.hour, minute=when.minute), "title": ", ".join(tags)[:120],
               "notes": notes[:4000] + (f"\n\nHolding the work up: {blocked}" if blocked else ""), "by": user.get("name") or "", "tags": tags, "weather": None,
               "weather_notes": "", "comments": 0, "photos": [{"file_id": f["file_id"], "title": f["name"], "size": f["bytes"]} for f in photos], "files": [],
               "pulled_at": now(), "source": "contractor", "entered_at": now(), "entered_by": user.get("user_id"), "blocked": blocked}
        self.bt.logs.insert_one(dict(doc))
        for f in photos:
            self.bt.photos.update_one({"file_id": f["file_id"]}, {"$set": {"file_id": f["file_id"], "job_id": job, "sha256": f["sha256"], "bytes": f["bytes"], "where": "server",
                                                                           "stored_at": now(), "source": "contractor", "taken_at": f.get("taken_at"), "gps": f.get("gps")},
                                                                  "$setOnInsert": {"added_at": now()}}, upsert=True)
        self.logs.insert_one({"log_id": log_id, "property_id": pid, "by": user.get("user_id"), "at": now()})
        # The same note a Buildertrend pull leaves: this property has a log RKB's site reading has not seen.
        self.bt.pulls.insert_one({"at": now(), "loaded_at": now(), "logs": 1, "new_logs": {pid: 1}, "analysed": False, "source": "contractor"})
        self.bt.__dict__.pop("_views", None)
        return {"log_id": log_id, "date": doc["date"], "tags": tags, "photos": len(photos)}

    def logs_of(self, user: Dict[str, Any], pid: str, *, limit: int = 20) -> Dict[str, Any]:
        self.check(user, pid)
        rows = [{"log_id": l["log_id"], "date": l.get("date"), "by": l.get("by") or "", "title": l.get("title") or "", "notes": (l.get("notes") or "")[:400],
                 "photos": len(l.get("photos") or []), "here": l.get("source") == "contractor"}
                for l in self.bt.logs.find({"property_id": pid}, {"_id": 0}).sort("date", -1).limit(limit)]
        today = eastern.to_et(now()).date()
        return {"logs": rows, "today": f"{today:%Y-%m-%d}", "logged_today": any(isinstance(r["date"], datetime) and r["date"].date() == today for r in rows),
                "tags": list(LOG_TAGS), "min_photos": MIN_LOG_PHOTOS}

    # ------------------------------------------------------------ a change order
    def _co_view(self, d: Dict[str, Any]) -> Dict[str, Any]:
        files = self._held(d["property_id"], d.get("files") or [], ROLES)
        return {**{k: d.get(k) for k in ("co_id", "property_id", "number", "title", "group", "days", "why", "lines", "approved_by", "approved_on", "status", "sent_at",
                                         "agreed_at", "agreed_by_name", "declined_why", "by_name")},
                "property": name_of(d["property_id"]), "cost": _r2(sum(_r2(l.get("cost")) for l in d.get("lines") or [])),
                "price": _r2(sum(_r2(l.get("price")) for l in d.get("lines") or [])), "files": files,
                "stands": {"sent": "Waiting for RKB", "agreed": "RKB agreed · can be billed", "declined": "RKB did not agree"}.get(d.get("status"), d.get("status"))}

    def add_change_order(self, user: Dict[str, Any], pid: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Extra work that is not in the proposal. The homeowner has approved it in Buildertrend
        (the approved copy is attached); RKB now agrees to pay for it, or says why not."""
        self.check(user, pid)
        title = str(body.get("title") or "").strip()
        why = str(body.get("why") or "").strip()
        lines = [{"title": str(l.get("title") or "").strip()[:200], "kind": l.get("kind") if l.get("kind") in ("labour", "materials") else "labour",
                  "cost": _r2(l.get("cost")), "price": _r2(l.get("price"))} for l in body.get("lines") or []]
        lines = [l for l in lines if l["title"] and l["cost"] > 0]
        files = self._held(pid, body.get("files") or [], ROLES)
        problems = []
        if len(title) < 4:
            problems.append("Give the change order a title.")
        if len(why) < 20:
            problems.append("Say what is changing and why it was not in the proposal.")
        if not lines:
            problems.append("Add at least one line with what it costs.")
        if not any(f["role"] == "approval" for f in files):
            problems.append("Attach the approved change order from Buildertrend.")
        if not str(body.get("approved_by") or "").strip() or not _day(body.get("approved_on")):
            problems.append("Say who approved it, and on which date.")
        if sum(1 for f in files if f["role"] == "photo") < MIN_CO_PHOTOS:
            problems.append(f"Add at least {MIN_CO_PHOTOS} photos of what was found.")
        if problems:
            raise EntryError(" ".join(problems))
        co_id = self._next("co")
        d = {"co_id": co_id, "property_id": pid, "number": f"MT-{co_id - ID_BASE:03d}", "title": title[:160], "group": str(body.get("group") or "").strip()[:120],
             "days": max(0, int(_r2(body.get("days")))), "why": why[:3000], "lines": lines, "approved_by": str(body.get("approved_by")).strip()[:120],
             "approved_on": str(body.get("approved_on"))[:10], "files": [f["file_id"] for f in files], "status": "sent", "sent_at": now(), "by": user.get("user_id"),
             "by_name": user.get("name")}
        self.cos.insert_one(dict(d))
        return self._co_view(d)

    def change_orders_of(self, user: Optional[Dict[str, Any]], pid: str) -> Dict[str, Any]:
        """The change orders on a job: the approved ones on file (from Buildertrend, or agreed here), and those waiting for RKB."""
        if user is not None:
            self.check(user, pid)
        book = {l["key"]: l for l in self.lines(pid)["lines"]}
        agreed_here = {c["bt_co_id"] for c in self.cos.find({"property_id": pid, "status": "agreed"}, {"bt_co_id": 1}) if c.get("bt_co_id")}
        approved = []
        for c in self.bt.change_orders.find({"property_id": pid, "approved": True}, {"_id": 0}).sort("approved_at", -1):
            ids = [str(i) for i in c.get("po_line_ids") or []]
            billed, cost = sum(book[i]["billed"] for i in ids if i in book), _r2(c.get("builder_cost"))
            approved.append({"co_id": c["co_id"], "number": c.get("number") or "", "title": c.get("title") or "", "cost": cost, "price": _r2(c.get("owner_price")),
                             "approved_at": c.get("approved_at"), "approved_by": c.get("approved_by") or "",
                             "rkb_agreed": c["co_id"] in agreed_here or bool(c.get("rkb_agreed_at")), "rkb_agreed_at": c.get("rkb_agreed_at"),
                             "billed": _r2(billed), "billed_in_full": cost > 0 and billed >= cost - 0.005})
        waiting = [self._co_view(d) for d in self.cos.find({"property_id": pid, "status": {"$in": ["sent", "declined"]}}, {"_id": 0}).sort("sent_at", -1)]
        est = self.bt.estimate_of(pid) or {}
        return {"approved": approved, "waiting": waiting, "groups": [g.get("title") for g in est.get("groups") or [] if g.get("title") and g.get("title") != "Unassigned"],
                "min_photos": MIN_CO_PHOTOS}

    def waiting_for_rkb(self) -> List[Dict[str, Any]]:
        return [self._co_view(d) for d in self.cos.find({"status": "sent"}, {"_id": 0}).sort("sent_at", 1)]

    def decide_change_order(self, rkb_user: Dict[str, Any], co_id: int, *, agree: bool, why: str = "") -> Dict[str, Any]:
        """RKB agrees to pay for a change order, or does not. Once agreed it is an approved change
        order like any other on file, and the contractor can bill against it."""
        d = self.cos.find_one({"co_id": int(co_id)}, {"_id": 0})
        if not d:
            raise EntryError("That change order is not on file.")
        if d.get("status") != "sent":
            raise EntryError("This change order has already been decided.")
        if not agree:
            if len(str(why or "").strip()) < 5:
                raise EntryError("Say why RKB does not agree, so the contractor knows.")
            self.cos.update_one({"co_id": d["co_id"]}, {"$set": {"status": "declined", "declined_why": str(why).strip()[:1000], "declined_at": now(),
                                                                  "declined_by": rkb_user.get("user_id"), "declined_by_name": rkb_user.get("name")}})
            return self._co_view(self.cos.find_one({"co_id": d["co_id"]}, {"_id": 0}))
        pid, job = d["property_id"], self.job_id(d["property_id"])
        po_ids = [self._next("po_line") for _ in d["lines"]]
        approved_on = _day(d.get("approved_on")) or wall_clock()
        self.bt.change_orders.insert_one({
            "co_id": d["co_id"], "job_id": job, "property_id": pid, "number": d["number"], "title": d["title"], "status": "Approved", "raw_status": None, "approved": True,
            "status_text": f"Approved by {d['approved_by']}", "status_at": approved_on, "status_by": d["approved_by"], "approved_by": d["approved_by"], "approved_at": approved_on,
            "added": wall_clock(_aware(d.get("sent_at"))), "created_by": d.get("by_name") or "", "deadline": None, "client": "", "client_viewed": None,
            "builder_cost": _r2(sum(l["cost"] for l in d["lines"])), "owner_price": _r2(sum(l["price"] for l in d["lines"])), "description": d["why"], "notes": "",
            "po_ids": [], "po_line_ids": po_ids, "files": len(d.get("files") or []),
            "lines": [{"line_id": po, "title": l["title"], "description": "", "cost_code": d.get("group") or "", "unit_cost": l["cost"], "quantity": 1, "builder_cost": l["cost"],
                       "owner_price": l["price"]} for po, l in zip(po_ids, d["lines"])],
            "pulled_at": now(), "source": "contractor", "rkb_agreed_at": now(), "rkb_agreed_by": rkb_user.get("user_id"), "rkb_agreed_by_name": rkb_user.get("name")})
        self.cos.update_one({"co_id": d["co_id"]}, {"$set": {"status": "agreed", "agreed_at": now(), "agreed_by": rkb_user.get("user_id"), "agreed_by_name": rkb_user.get("name"),
                                                              "bt_co_id": d["co_id"], "po_line_ids": po_ids}})
        self.bt.__dict__.pop("_views", None)
        return self._co_view(self.cos.find_one({"co_id": d["co_id"]}, {"_id": 0}))

    # ------------------------------------------------------------ the first screen
    def home(self, user: Dict[str, Any], pid: str) -> Dict[str, Any]:
        self.check(user, pid)
        book = self.lines(pid)
        return {"property_id": pid, "property": name_of(pid), "jobs": self.jobs_of(user), "totals": book["totals"], "held": book["held"], "lines": book["lines"],
                "pay_to": self.pay_to(pid), "kinds": [{"key": k, "name": v, "holdback": HOLDBACK if k == "work" else 0.0} for k, v in KINDS.items()],
                "min_photos": MIN_BILL_PHOTOS, "holdback": HOLDBACK, "bills": self.bills_of(user, pid)}


def _aware(d: Any) -> Optional[datetime]:
    return d.replace(tzinfo=d.tzinfo or timezone.utc) if isinstance(d, datetime) else None
