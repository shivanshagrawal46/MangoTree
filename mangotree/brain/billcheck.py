"""The check of one contractor bill, before RKB approves it.

People do not work on the big money sheet. They work on one bill at a time: the
contractor asks to be paid for certain lines of the proposal, and somebody at RKB
has to decide whether to pay. This lays that one decision out.

Two checks, kept apart:

* the accountant's check — arithmetic only, no model. Which proposal line each
  billed line is, the line's budget, what was billed for it before, what this
  bill asks, what is left; whether a line goes over its budget, whether a line is
  not in the proposal at all, whether the hold-back is the agreed 10%, whether
  the lines add up to the bill.
* the engineer's check — one reading by Opus 5.5 of the contractor's own daily
  logs and photos, with the billed lines in hand: is each thing billed shown in a
  photo, only said in a log, not in the record at all, or contradicted by it.
  It is a reading of the contractor's record, not an inspection, and it only
  suggests: a person marks each line OK or Hold, and that is what stands.

A draw is entered in Buildertrend as two bills — "(Contractor)" 90% and
"(Hold-back)" 10% — so the check is of the pair, as one request.

The reading runs by itself after every Buildertrend pull, for each bill RKB has not
approved yet that is new, has changed, or has had a newer log since it was read
(``read_waiting``). Lists elsewhere (the Bills page, Today, Follow-ups) show the
stored count of lines to check.

Collection: ``bill_checks`` (one document per draw, keyed by the contractor bill).
"""
from __future__ import annotations

import base64
import hashlib
import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

from . import config as bc
from . import prompts
from .llm import BrainError, cost_of, structured
from .moneysheet import LINE_ROLES, MoneySheet
from .sitework import _small, pick_photos
from .store import BrainStore, name_of, now

LOG_DAYS = 45                    # how far back the logs are read for one bill
MAX_PHOTOS = 120
MAX_PAPERS = 12                  # attached files given to one reading
MAX_PAPER_BYTES = 8_000_000      # a PDF larger than this is named, not read
MAX_PAPER_ITEMS = 120            # lines kept from one receipt or invoice
PAPER_SIDE = 2000                # pixels: a photographed receipt must stay legible
IMAGE_EXT = ("jpg", "jpeg", "png", "webp", "gif", "heic", "bmp")
HOLDBACK = 0.10
VERDICTS = ("shown", "said", "partly", "nothing", "against")
VERDICT_LABEL = {"shown": "Shown in a photo", "said": "Said in a log, no photo", "partly": "Partly supported", "nothing": "Not in the record",
                 "against": "The record says otherwise"}

BRIEF = """YOUR JOB NOW: CHECK ONE CONTRACTOR BILL AGAINST THE SITE RECORD.
The contractor has sent RKB a bill. It asks to be paid for the lines listed under THE BILL.
RKB's engineer (Manjunath Sir) must decide, line by line, whether what is billed is really
there. You are given the contractor's own daily logs and the photos posted with them. For
each billed line, say what that record shows. Give exactly one verdict per line:

* shown — at least one photo you were given shows this item installed, or delivered and on
  site. Name the photo ids. A materials bill is supported by the material being on site.
* said — a log says it is done, installed or delivered, but no photo you were given shows
  it. Name the log date.
* partly — most of what is billed on the line is shown or said, but a part of it is not, or
  is shown unfinished (paint billed in full with two walls still unpainted; two fixtures
  billed, one visible). Say exactly which part is supported and which is not.
* nothing — neither a log nor a photo supports it.
* against — the record contradicts the line as a whole: a log says it has not arrived or
  has not been done, or a photo taken on or after the bill date shows it missing.

Rules:
- The share billed matters. A line billed in full needs the whole of it: three toilets
  billed means three toilets. If only part is supported, the verdict is "partly", never
  "shown".
- Use the newest record. If a log says an item "will arrive" and a later log or photo
  shows it there, it is shown or said — and say on which date it appeared. If nothing
  later shows it, it is against.
- Read what is written and look at what is pictured. Do not go by what is usual on a job,
  and do not take one item as proof of another: a vanity in a photo does not prove its
  faucet unless the faucet is visible or a log names it.
- A photo id may be given only if you were shown that photo here and it shows the item.
- A photo id may also be given for a photo that shows the item missing or unfinished.
- basis: one or two short plain sentences on what the record shows, naming the log date.
  Do not write photo ids in the basis; put them in photo_ids.
- ask: when the verdict is not "shown", the exact thing to ask the contractor for (a photo
  of what, in which room; or a date), in one or two sentences of at most 60 words in all:
  it is sent to the contractor as written, so it must be complete. Empty when "shown".
- Say who a person is, or which company they work for, only if a log or a paper says so.
- log_dates: the dates (YYYY-MM-DD) of the logs your basis rests on. Everywhere else — in
  basis, ask and other_points — write a date the way people do: "11 Nov", never "11-11".

THE PAPERS ATTACHED TO THE BILL. When the bill was entered in Buildertrend, files may have
been attached to it: the supplier's receipt, the subcontractor's invoice, a proof of
payment, photos. You are given each one that could be read, before the logs. Read them fully.
- They say WHAT is being paid for (items, quantities, prices, delivery date and address)
  and whether the paper's total is the bill's amount. Use that to name exactly what a line
  covers and to ask a precise question.
- A receipt or invoice is the seller's paper. It is NOT proof that the material is on site
  or the work is done: the verdict still comes from the logs and photos. But if the paper
  gives a delivery or service date later than the newest log, say so in the basis — the
  record could not show it yet — and ask for a photo once it is delivered.
- A photo attached to the bill counts like a log photo: it can make a line "shown"; give
  its file id in photo_ids.
- Never ask for a receipt, invoice or item list that is already attached.
- In `papers`, give one entry per attached file you were shown: what it is, and in at most
  three sentences (under 90 words) what it says that matters for approving the bill
  (seller, date, the main items, delivery date and address, the total). `total` is the paper's grand total in
  dollars when it states one, else null.
- `items`: for a receipt or an invoice, EVERY line the paper lists, in its order, copied
  exactly: the item as written (shortened to under 12 words, keep the brand and the size),
  the quantity, the unit price actually charged (after any discount shown) and the line's
  amount. Include delivery, shipping, discount and tax rows when the paper shows them as
  rows or totals, so that the amounts can be added up to the total. Use null for a figure
  the paper does not give; never work one out yourself. Empty for photos and other files.
- THE MATERIALS, ONE BY ONE. For every item of a receipt or invoice that is a material or a
  fixture, go through ALL the photos and logs you were given and say where that material is
  on the property. RKB wants to know, item by item, that what was bought is really there and
  what it was used for. `seen` is one of:
    installed       a photo shows this item fixed in place or in use. `where`: the room, what
                    it was used for, and the log date ("Upstairs bathroom, tub wall, 14 Nov").
    on_site         a photo shows it delivered or stacked, not yet installed. Say where.
    covered         the material itself cannot be seen because of what it is: it goes INTO
                    or BEHIND the work and is hidden once the work is done (nails, screws,
                    joint tape and compound under paint, thinset and cement board behind
                    tile, waterproofing membrane, pipe and fittings inside a closed wall,
                    primer under paint, foam, sealant, studs behind drywall). Use this when
                    a photo dated ON OR AFTER the purchase shows the finished or closed-up
                    work that this material goes into, at about the amount bought. Say in
                    `where` which work, in which room, on which log date, and that the item
                    itself is covered ("Behind the tiled tub wall, upstairs bath, 20 Nov —
                    covered; tile set over it"). It is a reasoned judgement, not a sighting:
                    give the photos of the covering work in `photo_ids`.
    similar         a photo shows material of this kind (studs, drywall sheets, joint compound,
                    plain pipe) but nothing in the photo ties it to this purchase: generic
                    material looks the same whoever bought it. Say where it is seen and that
                    it cannot be tied to this order. Use this also when the bill or receipt
                    is dated after the photo.
    log_only        a log names it as delivered or used, and no photo shows it.
    not_seen        nothing in the logs or photos. If the paper's delivery date is after the
                    newest log, say so in `where` ("Delivery set for 18 Nov; newest log 17 Nov").
                    For a material that gets covered, also say how far the work it belongs
                    to has got, because that tells RKB whether it CAN have been used yet:
                    "Tile not started in any photo, so the thinset cannot have been used yet",
                    or "Drywall is hung but not taped in the 20 Nov photos: tape and compound
                    not used yet". If the work it would go into is already finished in photos
                    taken BEFORE the purchase, say that too — the purchase came after the work.
    not_a_material  delivery, shipping, discount, tax, subtotal and total rows; a service.
  Go by what tells an item apart: brand, model, colour, size, finish, shape, the count. A
  white round-front toilet with the seat in the box is that toilet; a 2x4 is any 2x4. Count
  what you can see against the quantity bought and say so when fewer are visible ("2 bought,
  1 visible"). `photo_ids`: the photos that show it (at most 3), only photos you were shown.
  `where` is at most 25 words. Never guess a room you cannot see.
- Never copy a bank account number, routing number or card number into anything you write.

other_points: at most three things in the record that bear on approving THIS bill but are
not one of its lines — for example work the logs call a change order with no price on file.
One or two short sentences each. Do not repeat the arithmetic (it is checked separately),
do not say how many photos you were shown, and do not add a disclaimer: the page already
says this is a reading of the contractor's record and not an inspection. Leave it empty if
there is nothing of that kind. If an attached paper's total, property or date does not
agree with the bill, that is one of these points.
"""

PAPER_KINDS = ("receipt", "invoice", "payment_proof", "photo", "other")
#: Where one material of a receipt stands in the site record.
SEEN = ("installed", "on_site", "covered", "similar", "log_only", "not_seen", "not_a_material")
SEEN_LABEL = {"installed": "Seen installed", "on_site": "Seen on site, not installed", "covered": "Likely used, now covered", "similar": "Similar material seen",
              "log_only": "In a log only, no photo",
              "not_seen": "Not seen yet", "not_a_material": ""}

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["lines", "papers", "other_points"],
    "properties": {
        "papers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["file_id", "kind", "says", "total", "items"],
            "properties": {"file_id": {"type": "integer"}, "kind": {"type": "string", "enum": list(PAPER_KINDS)}, "says": {"type": "string"},
                           "total": {"type": ["number", "null"]},
                           "items": {"type": "array", "items": {
                               "type": "object", "additionalProperties": False, "required": ["item", "qty", "unit_price", "amount", "seen", "where", "photo_ids"],
                               "properties": {"item": {"type": "string"}, "qty": {"type": ["number", "null"]}, "unit_price": {"type": ["number", "null"]},
                                              "amount": {"type": ["number", "null"]}, "seen": {"type": "string", "enum": list(SEEN)},
                                              "where": {"type": "string"}, "photo_ids": {"type": "array", "items": {"type": "integer"}}}}}}}},
        "lines": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["key", "verdict", "basis", "log_dates", "photo_ids", "ask"],
            "properties": {"key": {"type": "string"}, "verdict": {"type": "string", "enum": list(VERDICTS)}, "basis": {"type": "string"},
                           "log_dates": {"type": "array", "items": {"type": "string"}}, "photo_ids": {"type": "array", "items": {"type": "integer"}},
                           "ask": {"type": "string"}}}},
        "other_points": {"type": "array", "items": {"type": "string"}},
    },
}

_SIDE = re.compile(r"\s*\((?:contractor|hold[\s-]?back)\)\s*$", re.I)


def stem(title: Any) -> str:
    """A bill's name without its "(Contractor)" / "(Hold-back)" ending."""
    return _SIDE.sub("", str(title or "")).strip()


_ISO_DAY = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")


def plain_dates(text: Any) -> str:
    """'2026-11-11' as people write it: '11 Nov'."""
    def one(m):
        try:
            return f"{datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))):%d %b}".lstrip("0")
        except ValueError:
            return m.group(0)
    return _ISO_DAY.sub(one, str(text or ""))


def usd(n: Any) -> str:
    return f"${float(n or 0):,.2f}"


def _when(b: Dict[str, Any]) -> Tuple[datetime, int]:
    d = b.get("invoice_date")
    d = d.replace(tzinfo=d.tzinfo or timezone.utc) if isinstance(d, datetime) else datetime.min.replace(tzinfo=timezone.utc)
    return d, int(b.get("bill_id") or 0)


def signature(lines: List[Dict[str, Any]]) -> str:
    """What the bill asks for, in one string: if the contractor revises the bill, this changes."""
    raw = "|".join(f"{l['key']}:{l['this']:.2f}" for l in sorted(lines, key=lambda l: l["key"]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


_SECRET_NUMBER = re.compile(r"\b\d[\d\s-]{7,}\d\b")


def _no_account_numbers(text: str) -> str:
    """A long run of digits (bank account, routing, card) is never kept, whatever the paper says."""
    return _SECRET_NUMBER.sub(lambda m: m.group(0) if sum(c.isdigit() for c in m.group(0)) < 9 else "[number removed]", text)


def _number(x: Any) -> Optional[float]:
    return round(float(x), 2) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _item(i: Dict[str, Any], shown: set) -> Dict[str, Any]:
    """One line of a receipt, with where the material is seen. A claim to have seen it stands
    only on a photo that was really shown; without one it falls back to "not seen"."""
    ids = [int(x) for x in i.get("photo_ids") or [] if isinstance(x, int) and not isinstance(x, bool) and x in shown][:3]
    seen = i.get("seen") if i.get("seen") in SEEN else None
    if seen in ("installed", "on_site", "covered", "similar") and not ids:
        seen = "not_seen"
    if seen in ("log_only", "not_seen", "not_a_material"):
        ids = []
    return {"item": _no_account_numbers(str(i.get("item") or "").strip())[:160], "qty": _number(i.get("qty")), "unit_price": _number(i.get("unit_price")),
            "amount": _number(i.get("amount")), "seen": seen, "where": _no_account_numbers(str(i.get("where") or "").strip())[:240] if seen else "", "photo_ids": ids}


def paper_check(papers: List[Dict[str, Any]], gross: float) -> Optional[Dict[str, Any]]:
    """Arithmetic only: do the attached receipts and invoices come to what the bill asks?
    None when no attached paper states a total."""
    totals = [(p.get("title") or "the attached paper", p["total"]) for p in papers or [] if p.get("kind") in ("receipt", "invoice") and p.get("total") is not None]
    if not totals:
        return None
    what = "receipt" if all(p.get("kind") == "receipt" for p in papers if p.get("total") is not None and p.get("kind") in ("receipt", "invoice")) else "invoice"
    if any(abs(t - gross) < 0.02 for _, t in totals):
        return {"key": "paper_total", "ok": True, "text": f"The attached {what} comes to {usd(gross)}, the same as the bill."}
    if len(totals) > 1 and abs(sum(t for _, t in totals) - gross) < 0.02:
        return {"key": "paper_total", "ok": True, "text": f"The {len(totals)} attached papers add up to {usd(gross)}, the same as the bill."}
    return {"key": "paper_total", "ok": False, "text": ("The attached " + what + f" comes to {usd(totals[0][1])}" if len(totals) == 1 else
                                                         f"The {len(totals)} attached papers come to {usd(sum(t for _, t in totals))}") + f"; the bill asks {usd(gross)}."}


def paper_image(blob: bytes) -> Optional[bytes]:
    """An attached picture at a size where a photographed receipt can still be read; None if it cannot be opened."""
    try:
        import io
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(blob))).convert("RGB")
        im.thumbnail((PAPER_SIDE, PAPER_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return None


def clean_reading(data: Dict[str, Any], keys: set, shown: set, log_days: set, papers: Optional[Dict[int, str]] = None) -> Dict[str, Any]:
    """Keep only what can be pointed at. A line the bill does not have is dropped; a photo
    that was never shown is dropped; "shown" with no photo left behind it is not "shown";
    a paper that was not given is dropped. ``papers``: file id -> name, of the files given."""
    said: List[Dict[str, Any]] = []
    for p in data.get("papers") or []:
        fid = p.get("file_id")
        if not isinstance(fid, int) or fid not in (papers or {}) or any(x["file_id"] == fid for x in said):
            continue
        total = p.get("total")
        said.append({"file_id": fid, "title": (papers or {})[fid], "kind": p.get("kind") if p.get("kind") in PAPER_KINDS else "other",
                     "says": _no_account_numbers(str(p.get("says") or "").strip())[:1200], "total": _number(total),
                     "items": [_item(i, shown) for i in (p.get("items") or []) if isinstance(i, dict) and str(i.get("item") or "").strip()][:MAX_PAPER_ITEMS]})
    lines: Dict[str, Dict[str, Any]] = {}
    for l in data.get("lines") or []:
        key = str(l.get("key") or "")
        if key not in keys or key in lines or l.get("verdict") not in VERDICTS:
            continue
        ids = [int(x) for x in l.get("photo_ids") or [] if isinstance(x, int) and x in shown][:6]
        days = [d for d in (str(x)[:10] for x in l.get("log_dates") or []) if d in log_days][:4]
        verdict = l["verdict"]
        if verdict == "shown" and not ids:
            verdict = "said" if days else "nothing"
        lines[key] = {"verdict": verdict, "basis": _no_account_numbers(str(l.get("basis") or "").strip())[:600], "log_dates": days, "photo_ids": ids,
                      "ask": "" if verdict == "shown" else _no_account_numbers(str(l.get("ask") or "").strip())[:700]}
    return {"lines": lines, "points": [_no_account_numbers(str(x).strip())[:400] for x in data.get("other_points") or [] if str(x).strip()][:3], "papers": said}


class BillCheck:
    def __init__(self, mongo: Mongo, store: Optional[BrainStore] = None):
        self.mongo = mongo
        self.store = store or BrainStore(mongo)
        self.sheet = MoneySheet(mongo, self.store)
        self.bt = self.sheet.bt
        self.coll = mongo.db["bill_checks"]
        self.track = mongo.db["bill_tracking"]

    # ------------------------------------------------------------ the draw
    @staticmethod
    def pair(bill: Dict[str, Any], bills: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """The bill to pay and the hold-back bill entered with it (same name, same day)."""
        day = _when(bill)[0].date()
        twins = [b for b in bills if b["bill_id"] != bill["bill_id"] and bool(b.get("holdback")) != bool(bill.get("holdback"))
                 and stem(b.get("title")).lower() == stem(bill.get("title")).lower() and _when(b)[0].date() == day]
        twin = min(twins, key=lambda b: abs(b["bill_id"] - bill["bill_id"])) if twins else None
        if bill.get("holdback"):
            return (twin, bill) if twin else (bill, None)
        return bill, twin

    def _lines(self, pid: str, main: Dict[str, Any], held: Optional[Dict[str, Any]], bills: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Each billed line on the proposal line it belongs to — by building the money
        sheet twice, without and with this draw, and taking the difference. The matching
        is therefore the sheet's own, twin lines included."""
        mine = [b for b in (main, held) if b]
        ids = {b["bill_id"] for b in mine}
        before = [b for b in bills if b["bill_id"] not in ids and _when(b) < _when(main)]
        estimate = self.bt.estimate_of(pid)
        saved = self.sheet.inputs.find_one({"property_id": pid}, {"_id": 0}) or {}
        paid_here = {t["bill_id"] for t in self.track.find({"bill_id": {"$in": [b["bill_id"] for b in bills]}, "steps.paid": {"$ne": None}}, {"bill_id": 1})}

        def rows(bs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            r, _ = self.sheet._rows(estimate, bs, paid_here)
            return self.sheet._place(r, saved)
        was = {r["key"]: r for r in rows(before)}
        after = rows(before + mine)
        out: List[Dict[str, Any]] = []
        for a in after:
            b0 = was.get(a["key"]) or {}
            this = round(a["billed"] - (b0.get("billed") or 0.0), 2)
            if abs(this) < 0.005:
                continue
            budget, prev = a["revised"], round(b0.get("billed") or 0.0, 2)
            line = {"key": a["key"], "kind": a["kind"], "group": a["group"], "title": a["title"], "cost_code": a["cost_code"], "budget": budget,
                    "before": prev, "this": this, "after": round(prev + this, 2),
                    "left": (round(budget - prev - this, 2) + 0.0) if a["kind"] != "other" else 0.0,      # + 0.0: never "-0.00"
                    "percent_after": round(100 * (prev + this) / budget) if budget else None, "flags": []}
            if a["kind"] == "other" and a.get("unplaced"):
                line["flags"].append({"kind": "outside", "text": "The proposal has more than one line with this name, and Buildertrend's line id has not been read "
                                                                 "for this bill. Say which line it belongs to."})
            elif a["kind"] == "other" and not estimate:
                line["flags"].append({"kind": "outside", "text": "This job's proposal has not been read from Buildertrend, so the line could not be placed on it."})
            elif a["kind"] == "other":
                line["flags"].append({"kind": "outside", "text": "Not a line of the proposal. Say what it is: a contingency, a payment to the homeowner, or which line."})
            elif a["kind"] == "change_order":
                line["flags"].append({"kind": "change_order", "text": "A change order. Check that RKB agreed to it and to this price."})
            if a["kind"] != "other" and prev + this > budget + 0.01:
                line["flags"].append({"kind": "over", "text": f"Over this line's budget by {usd(prev + this - budget)}."})
            out.append(line)
        inside = [r for r in after if r["kind"] != "other"]
        job = {"budget": round(sum(r["revised"] for r in inside), 2), "before": round(sum((was.get(r["key"]) or {}).get("billed") or 0.0 for r in inside), 2),
               "after": round(sum(r["billed"] for r in inside), 2), "paid": round(sum(r["paid"] for r in after), 2), "has_proposal": bool(estimate)}
        job["this"] = round(job["after"] - job["before"], 2)
        job["left"] = round(job["budget"] - job["after"], 2)
        return out, job

    @staticmethod
    def _checks(main: Dict[str, Any], held: Optional[Dict[str, Any]], lines: List[Dict[str, Any]], job: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The accountant's check: four plain statements, each true, false, or not applicable (None)."""
        gross = round((main.get("amount") or 0) + ((held or {}).get("amount") or 0), 2)
        outside = [l for l in lines if l["kind"] == "other"]
        over = [l for l in lines if any(f["kind"] == "over" for f in l["flags"])]
        out = []
        if not job["has_proposal"]:
            out.append({"key": "in_proposal", "ok": None, "text": "No proposal has been read from Buildertrend for this job, so the lines cannot be placed."})
        else:
            out.append({"key": "in_proposal", "ok": not outside, "text": "Every line is a line of the proposal." if not outside else
                        f"{len(outside)} line(s), {usd(sum(l['this'] for l in outside))}, are not in the proposal."})
        out.append({"key": "within_budget", "ok": not over, "text": "No line goes over its budget." if not over else
                    f"{len(over)} line(s) go over their budget, by {usd(sum(l['after'] - l['budget'] for l in over))} in all."})
        if held:
            share = (held.get("amount") or 0) / gross if gross else 0
            out.append({"key": "holdback", "ok": abs(share - HOLDBACK) < 0.005, "text": f"Hold-back is {share * 100:.1f}% of the request ({usd(held.get('amount'))})."
                        + ("" if abs(share - HOLDBACK) < 0.005 else " The agreed hold-back is 10%.")})
        elif re.search(r"\(contractor\)\s*$", str(main.get("title") or ""), re.I):
            out.append({"key": "holdback", "ok": False, "text": "No hold-back bill was entered with this bill."})
        else:
            out.append({"key": "holdback", "ok": None, "text": "Not entered as a draw with a hold-back."})
        billed = round(sum(l["this"] for l in lines), 2)
        has_lines = bool(main.get("line_items"))
        out.append({"key": "adds_up", "ok": None if not has_lines else abs(billed - gross) < 0.02, "text": "The bill's lines have not been read from Buildertrend yet." if not has_lines else
                    "The lines add up to the bill." if abs(billed - gross) < 0.02 else f"The lines come to {usd(billed)}, the bill to {usd(gross)}."})
        return out

    @staticmethod
    def _state(line: Dict[str, Any], ev: Optional[Dict[str, Any]], mine: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """(what the system suggests, what stands). A person's mark always stands over the suggestion."""
        if any(f["kind"] in ("over", "outside") for f in line["flags"]):
            suggest: Optional[str] = "check"
        elif not ev:
            suggest = None
        else:
            suggest = "ok" if ev["verdict"] in ("shown", "said") else "check"        # partly, nothing, against: a person looks
        decided = (mine or {}).get("decision")
        return suggest, decided or suggest

    def _find(self, bill_id: int) -> Tuple[str, Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        bill = self.bt.bills.find_one({"bill_id": int(bill_id)}, {"_id": 0})
        if not bill or not bill.get("property_id"):
            raise KeyError(bill_id)
        pid = bill["property_id"]
        bills = list(self.bt.bills.find({"property_id": pid}, {"_id": 0}).sort("invoice_date", 1))
        main, held = self.pair(bill, bills)
        return pid, main, held, bills

    def draw(self, bill_id: int) -> Dict[str, Any]:
        """Everything the page shows for one bill. No model is called here."""
        pid, main, held, bills = self._find(bill_id)
        lines, job = self._lines(pid, main, held, bills)
        doc = self.coll.find_one({"bill_id": main["bill_id"]}, {"_id": 0}) or {}
        reading = doc.get("reading") or None
        evidence = (reading or {}).get("lines") or {}
        decisions = doc.get("decisions") or {}
        for l in lines:
            ev = evidence.get(l["key"])
            if ev:
                ev = {**ev, "basis": plain_dates(ev.get("basis")), "ask": plain_dates(ev.get("ask"))}
            l["evidence"] = ev
            l["decision"] = decisions.get(l["key"])
            l["suggest"], l["state"] = self._state(l, ev, l["decision"])
        gross = round((main.get("amount") or 0) + ((held or {}).get("amount") or 0), 2)
        # The hold-back is one flat share of the whole request, so each line's part of it is that same share.
        share = ((held or {}).get("amount") or 0) / gross if gross else 0.0
        for l in lines:
            l["held"] = round(l["this"] * share, 2)
            l["pay_now"] = round(l["this"] - l["held"], 2)

        def total(pick) -> Dict[str, Any]:
            xs = [l for l in lines if pick(l)]
            return {"count": len(xs), "amount": round(sum(l["this"] for l in xs), 2)}
        summary = {"ok": total(lambda l: l["state"] == "ok"), "check": total(lambda l: l["state"] in ("check", "hold")), "unread": total(lambda l: l["state"] is None),
                   "photo": total(lambda l: (l["evidence"] or {}).get("verdict") == "shown"), "log_only": total(lambda l: (l["evidence"] or {}).get("verdict") == "said")}
        if reading:
            newest = self.bt.logs.find_one({"property_id": pid}, {"date": 1}, sort=[("date", -1)])
            last = reading.get("last_log")
            reading = {k: reading.get(k) for k in ("at", "points", "photos_shown", "photos_posted", "logs_read", "last_log", "cost_usd", "papers", "papers_unread")}
            reading["points"] = [plain_dates(x) for x in reading.get("points") or []]
            reading["newer_logs"] = bool(newest and last and newest["date"].replace(tzinfo=newest["date"].tzinfo or timezone.utc) > last.replace(tzinfo=last.tzinfo or timezone.utc))
        t = self.track.find_one({"bill_id": main["bill_id"]}, {"_id": 0, "steps": 1}) or {}
        draws = [{"bill_id": b["bill_id"], "title": stem(b.get("title")), "number": b.get("number"), "date": b.get("invoice_date"), "status": b.get("status"),
                  "amount": round((b.get("amount") or 0) + ((self.pair(b, bills)[1] or {}).get("amount") or 0), 2), "in_review": bool(b.get("in_review"))}
                 for b in sorted(bills, key=_when, reverse=True) if not b.get("holdback")][:12]
        out = {
            "bill_id": main["bill_id"], "property_id": pid, "property": name_of(pid), "title": stem(main.get("title")), "number": main.get("number"),
            "pay_to": main.get("pay_to"), "bill_date": main.get("invoice_date"), "status": main.get("status"), "in_review": bool(main.get("in_review")),
            "created_by": main.get("created_by"), "file_names": main.get("file_names") or [], "description": main.get("description") or "",
            "gross": gross, "pay_now": round(main.get("amount") or 0, 2) if not main.get("holdback") else 0.0,
            "held": round((held or {}).get("amount") or (main.get("amount") if main.get("holdback") else 0) or 0, 2), "holdback_bill": (held or {}).get("number"),
            "lines": lines, "job": job, "checks": self._checks(main, held, lines, job) + [c for c in [paper_check((reading or {}).get("papers") or [], gross)] if c],
            "summary": summary, "reading": reading,
            "steps": t.get("steps") or {}, "draws": draws, "history": (doc.get("history") or [])[-20:][::-1],
        }
        out["message_for_wes"] = self.message(out)
        return out

    def _remember(self, d: Dict[str, Any]) -> None:
        """Keep the short result with the record, so a list can show it without building every page."""
        s = d["summary"]
        self.coll.update_one({"bill_id": d["bill_id"]}, {"$set": {"summary": {"lines": len(d["lines"]), "ok": s["ok"], "check": s["check"], "unread": s["unread"], "at": now()},
                                                                  "property_id": d["property_id"]}})

    def summaries(self, bill_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """bill id -> {lines, ok, check, unread, read_at} for the bills that have been checked."""
        out: Dict[int, Dict[str, Any]] = {}
        for c in self.coll.find({"bill_id": {"$in": [int(i) for i in bill_ids]}, "summary": {"$exists": True}}, {"_id": 0, "bill_id": 1, "summary": 1, "reading.at": 1}):
            out[c["bill_id"]] = {**c["summary"], "read_at": (c.get("reading") or {}).get("at")}
        return out

    # ------------------------------------------------------------ the papers attached to the bill
    def _paper_ids(self, main: Dict[str, Any], held: Optional[Dict[str, Any]]) -> List[int]:
        """The attached files of the draw that are held (both bills of the pair), as file ids."""
        return [f["file_id"] for f in self.bt.bill_files_of([b["bill_id"] for b in (main, held) if b])]

    def _papers(self, main: Dict[str, Any], held: Optional[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[int, str], List[int], List[str]]:
        """(what to show the model, {file id: name} of the files shown, the ids among them that
        are photos, the names of files that could not be shown). A PDF goes as it is; a
        picture is shown large enough to read a photographed receipt."""
        blocks: List[Dict[str, Any]] = []
        given: Dict[int, str] = {}
        photos: List[int] = []
        files = self.bt.bill_files_of([b["bill_id"] for b in (main, held) if b])
        names = {f.get("title") for f in files}
        unread = [n for b in (main, held) if b for n in (b.get("file_names") or []) if n not in names]     # attached in Buildertrend, never brought over
        seen = set()
        for f in files:
            name = f.get("title") or f"file {f['file_id']}"
            if f["sha256"] in seen:                                     # the same file attached to both bills of the pair
                continue
            if len(given) >= MAX_PAPERS:
                unread.append(name)
                continue
            blob = self.bt.bill_file(f["file_id"])
            ext = (f.get("ext") or "").lower()
            block = None
            if blob and ext == "pdf" and len(blob) <= MAX_PAPER_BYTES:
                block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(blob).decode()}}
            elif blob and (ext in IMAGE_EXT or f.get("is_photo")):
                small = paper_image(blob)
                if small:
                    block = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(small).decode()}}
                    photos.append(f["file_id"])
            if not block:
                unread.append(name)
                continue
            seen.add(f["sha256"])
            given[f["file_id"]] = name
            blocks += [{"type": "text", "text": f"attached file id {f['file_id']} — “{name}”:"}, block]
        return blocks, given, photos, list(dict.fromkeys(unread))

    # ------------------------------------------------------------ the engineer's reading
    def read(self, bill_id: int, *, force: bool = False, save: bool = True) -> Optional[Dict[str, Any]]:
        """One reading of the bill's attached papers and the contractor's logs and photos against
        this bill's lines. Stored (unless ``save`` is off, to try a reading); shown by ``draw``.

        A reading that looked at photos is not replaced by one that cannot (the photos are
        not held on every machine): that returns ``{"kept": True, ...}`` and changes nothing."""
        pid, main, held, bills = self._find(bill_id)
        lines, job = self._lines(pid, main, held, bills)
        if not lines:
            return None
        logs = self.bt.logs_of(pid, days=LOG_DAYS)[::-1]                # oldest first
        have = {p["file_id"] for p in self.bt.photos.find({"file_id": {"$in": [p["file_id"] for l in logs for p in l.get("photos") or []]}}, {"file_id": 1})}
        if have and all(self.bt.photo(f) is None for f in sorted(have)[:4]):
            have = set()                                                # listed, but the files are not on this machine
        earlier = (self.coll.find_one({"bill_id": main["bill_id"]}, {"reading.photos_shown": 1, "reading.sig": 1}) or {}).get("reading") or {}
        if not have and (earlier.get("photos_shown") or 0) > 0 and earlier.get("sig") == signature(lines) and not force:
            return {"kept": True, "photos_shown": earlier["photos_shown"], "cost_usd": 0.0,
                    "why": f"The photos are not on this machine. The earlier check, which looked at {earlier['photos_shown']} photos, was kept."}
        chosen = pick_photos(logs, have, MAX_PHOTOS) if have else {}
        posted = sum(len(l.get("photos") or []) for l in logs)
        gross = round((main.get("amount") or 0) + ((held or {}).get("amount") or 0), 2)
        head = [f"TODAY: {now():%d %b %Y}", f"PROPERTY: {name_of(pid)}", "",
                f"THE BILL: “{stem(main.get('title'))}” ({main.get('number')}), dated {_when(main)[0]:%d %b %Y}, entered by {main.get('created_by') or 'the contractor'}, "
                f"pay to {main.get('pay_to') or 'not stated'}. Request {usd(gross)} in all" + (f", of which {usd(held.get('amount'))} is the 10% hold-back." if held else ".")]
        note = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(main.get("description") or "")))).strip()
        if note:
            head.append(f"NOTE WRITTEN ON THE BILL by {main.get('created_by') or 'whoever entered it'}: {note[:1200]}")
        papers, given, paper_photos, unread = self._papers(main, held)
        head += ["", "THE LINES IT BILLS (key | proposal section | line | cost code | line budget | billed before | this bill | share of the line billed after this bill):"]
        for l in lines:
            head.append(f"  {l['key']} | {l['group']} | {l['title']} | {l['cost_code']} | {usd(l['budget'])} | {usd(l['before'])} | {usd(l['this'])} | "
                        + (f"{l['percent_after']}%" if l["percent_after"] is not None else "not a proposal line" if job["has_proposal"] else "proposal not read"))
        if not job["has_proposal"]:
            head.append("NOTE: RKB's system has NOT read this job's proposal from Buildertrend. So the budgets above are blank, not zero, and no line can be "
                        "called outside the proposal or over budget: do not say either. You may say that the proposal has not been read, so the line could not be placed.")
        papers_head = (["", f"THE PAPERS ATTACHED TO THE BILL: {len(given)} file(s) follow, each preceded by its file id."] if given else
                       ["", "THE PAPERS ATTACHED TO THE BILL: none could be read here."])
        if unread:
            papers_head.append("Attached in Buildertrend but NOT readable here (you have not seen them; do not say what they contain): " + "; ".join(unread[:20]))
        logs_head = ["", f"THE CONTRACTOR'S DAILY LOGS OF THE LAST {LOG_DAYS} DAYS: {len(logs)} log(s), {posted} photo(s) posted; you are shown {sum(len(v) for v in chosen.values())} of them."
                     + ("" if have else " NO PHOTO is held on this machine, so nothing can be 'shown': judge from the log text only."),
                     "The logs follow, oldest first. Each photo is preceded by its id."]
        content: List[Dict[str, Any]] = ([{"type": "text", "text": "\n".join(head + papers_head)}, *papers, {"type": "text", "text": "\n".join(logs_head)}] if papers else
                                         [{"type": "text", "text": "\n".join(head + papers_head + logs_head)}])
        shown: List[int] = list(paper_photos)
        for l in logs:
            ids = chosen.get(l["log_id"]) or []
            n = len(l.get("photos") or [])
            content.append({"type": "text", "text": (f"\n──────── LOG {l['date']:%Y-%m-%d} — “{l.get('title') or 'untitled'}” — written by {l.get('by') or 'unknown'} — "
                                                     f"{n} photo(s) posted{'' if len(ids) == n else f', {len(ids)} shown here'}\n{l.get('notes') or '(no text; photos only)'}")})
            for fid in ids:
                blob = self.bt.photo(fid)
                if not blob:
                    continue
                content.append({"type": "text", "text": f"photo id {fid}:"})
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(_small(blob)).decode()}})
                shown.append(fid)
        if not logs:
            content.append({"type": "text", "text": "\n(There is no daily log in this period.)"})
        def ask(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
            return structured(system=prompts.CORE + "\n" + BRIEF, user=blocks, schema=SCHEMA, effort="high", max_tokens=32_000, label=f"bill check {main['bill_id']}")
        try:
            try:
                r = ask(content)
            except BrainError:
                raise
            except Exception as exc:
                if not papers:
                    raise
                # A file the model cannot take (damaged, too long) must not stop the bill being checked: read it without the papers.
                logger.warning("bill %s: the attached files could not be read by the model (%s); checking without them", main["bill_id"], str(exc)[:200])
                unread = list(given.values()) + unread
                shown = [i for i in shown if i not in paper_photos]
                given, paper_photos = {}, []
                content[0] = {"type": "text", "text": "\n".join(head + ["", "THE PAPERS ATTACHED TO THE BILL: none could be read here.",
                                                                        "Attached in Buildertrend but NOT readable here (you have not seen them; do not say what they contain): " + "; ".join(unread[:20])])}
                r = ask([b for b in content if not any(b is p for p in papers)])
        except BrainError as exc:
            logger.warning("bill %s could not be checked against the site record: %s", main["bill_id"], exc)
            return None
        d = clean_reading(r["data"], {l["key"] for l in lines}, set(shown), {f"{l['date']:%Y-%m-%d}" for l in logs}, given)
        reading = {"at": now(), "lines": d["lines"], "points": d["points"], "photos_shown": len(shown) - len(paper_photos), "photos_posted": posted, "logs_read": len(logs),
                   "last_log": logs[-1]["date"] if logs else None, "sig": signature(lines), "model": bc.MODEL, "cost_usd": cost_of(r["usage"]),
                   "papers": d["papers"], "papers_held": sorted(self._paper_ids(main, held)), "papers_unread": unread[:20]}
        if not save:
            return reading
        self.coll.update_one({"bill_id": main["bill_id"]}, {"$set": {"reading": reading, "property_id": pid, "updated_at": now()},
                                                            "$setOnInsert": {"bill_id": main["bill_id"], "created_at": now()}}, upsert=True)
        self._remember(self.draw(main["bill_id"]))
        return reading

    # ------------------------------------------------------------ by itself, after a pull
    def waiting(self) -> List[Dict[str, Any]]:
        """The bills RKB still has to decide on: on the Bills page, and not yet approved by Manjunath Sir."""
        from .billtrack import BillTracker
        return [r for r in BillTracker(self.mongo).rows("open") if r.get("in_buildertrend") and not (r.get("steps") or {}).get("manju_approved")]

    def stale(self, bill_id: int) -> Optional[str]:
        """Why this bill needs reading (again), or None when its reading still holds."""
        pid, main, held, bills = self._find(bill_id)
        lines, _job = self._lines(pid, main, held, bills)
        if not lines:
            return None                                                 # its lines have not been read from Buildertrend: nothing to check yet
        reading = (self.coll.find_one({"bill_id": main["bill_id"]}, {"reading.at": 1, "reading.sig": 1, "reading.last_log": 1, "reading.papers_held": 1}) or {}).get("reading")
        if not reading:
            return "not checked yet"
        if reading.get("sig") and reading["sig"] != signature(lines):
            return "the bill has changed"
        if set(self._paper_ids(main, held)) - set(reading.get("papers_held") or []):
            return "a file attached to the bill has not been read yet"
        newest = self.bt.logs.find_one({"property_id": pid}, {"date": 1}, sort=[("date", -1)])
        last = reading.get("last_log")
        if newest and (not last or newest["date"].replace(tzinfo=newest["date"].tzinfo or timezone.utc) > last.replace(tzinfo=last.tzinfo or timezone.utc)):
            return "a newer log has come in"
        return None

    def read_waiting(self, *, workers: int = 2) -> Dict[str, Any]:
        """Check every waiting bill whose reading is missing or out of date. Bills whose
        reading still holds cost nothing. Safe to call as often as wanted."""
        from concurrent.futures import ThreadPoolExecutor
        out: Dict[str, Any] = {"read": [], "kept": [], "failed": [], "cost_usd": 0.0}
        todo = []
        for r in self.waiting():
            try:
                why = self.stale(r["bill_id"])
            except Exception:
                logger.exception("bill %s could not be looked at", r["bill_id"])
                continue
            if why:
                todo.append((r, why))

        def one(item):
            r, why = item
            try:
                return r, why, self.read(r["bill_id"])
            except Exception:
                logger.exception("bill %s could not be checked", r["bill_id"])
                return r, why, None
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
            for r, why, got in (pool.map(one, todo) if todo else []):
                row = {"bill_id": r["bill_id"], "property": r.get("property"), "title": stem(r.get("title")), "why": why}
                if not got:
                    out["failed"].append(row)
                elif got.get("kept"):
                    out["kept"].append(row)
                else:
                    s = self.summaries([r["bill_id"]]).get(r["bill_id"]) or {}
                    out["read"].append({**row, "to_check": (s.get("check") or {}).get("count"), "to_check_amount": (s.get("check") or {}).get("amount")})
                    out["cost_usd"] += float(got.get("cost_usd") or 0)
        out["cost_usd"] = round(out["cost_usd"], 2)
        return out

    # ------------------------------------------------------------ what a person decides
    def decide(self, bill_id: int, key: str, user: Dict[str, Any], *, decision: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
        """Mark one line OK or Hold (or clear the mark, with ""), and/or leave a note on it."""
        if user.get("role") not in LINE_ROLES:
            raise PermissionError("Only RKB's team can mark a bill's lines.")
        if decision not in (None, "", "ok", "hold"):
            raise ValueError("A line is marked OK or Hold.")
        d = self.draw(bill_id)
        if key not in {l["key"] for l in d["lines"]}:
            raise ValueError("That line is not on this bill.")
        who = {"by": user.get("user_id"), "by_name": user.get("name") or user.get("user_id"), "at": now()}
        fields: Dict[str, Any] = {"updated_at": now(), "property_id": d["property_id"]}
        log: Dict[str, Any] = {"key": key, **who}
        if decision is not None:
            fields[f"decisions.{key}.decision"] = decision or None
            log["decision"] = decision or None
        if note is not None:
            fields[f"decisions.{key}.note"] = note.strip()[:400]
            log["note"] = note.strip()[:400]
        for k, v in who.items():
            fields[f"decisions.{key}.{k}"] = v
        self.coll.update_one({"bill_id": d["bill_id"]}, {"$set": fields, "$push": {"history": log}, "$setOnInsert": {"bill_id": d["bill_id"], "created_at": now()}}, upsert=True)
        d = self.draw(d["bill_id"])
        self._remember(d)
        return d

    # ------------------------------------------------------------ what to ask the contractor
    @staticmethod
    def message(d: Dict[str, Any]) -> str:
        """The lines not accepted, as a message to the contractor. Written from the page's own
        rows — no model — and only copied by a person: nothing is sent from here."""
        open_lines = [l for l in d["lines"] if l["state"] in ("check", "hold")]
        if not open_lines:
            return ""
        when = f"{d['bill_date']:%d %b %Y}" if isinstance(d.get("bill_date"), datetime) else ""
        out = [f"Wes, on the bill “{d['title']}” ({d['number']}{', ' + when if when else ''}) for {d['property']}, we need the following before it is approved:", ""]
        for i, l in enumerate(open_lines, 1):
            why = ((l.get("decision") or {}).get("note") or (l.get("evidence") or {}).get("ask")
                   or next((f["text"] for f in l["flags"]), "") or "please send a photo showing this on site.")
            out.append(f"{i}. {l['title']} ({l['group']}), {usd(l['this'])}: {why}")
        out += ["", f"These lines come to {usd(sum(l['this'] for l in open_lines))} of the {usd(d['gross'])} requested."]
        return "\n".join(out)
