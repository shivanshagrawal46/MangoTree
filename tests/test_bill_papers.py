"""The papers attached to a bill (receipt, invoice, photos): that the pull knows which ones to
bring over, that the bill check is handed them and reads again when one arrives, that only a
paper that was really given can be quoted, and that an account number never stays in a result."""
import base64
import re
from datetime import datetime, timedelta, timezone

import mongomock

from mangotree.brain import billcheck
from mangotree.brain.billcheck import BillCheck, _no_account_numbers, clean_reading
from mangotree.brain.buildertrend import Buildertrend
from mangotree.brain.store import BrainStore

PDF = b"%PDF-1.4 a receipt"


class FakeMongo:
    def __init__(self):
        self.db = mongomock.MongoClient().db
        self.artifacts = self.db["artifacts"]


def _setup(monkeypatch):
    BrainStore._indexed = True
    m = FakeMongo()
    from mangotree.brain import moneysheet
    monkeypatch.setattr(moneysheet.snapshot, "numbers", lambda store, pid, file: {"main": [], "extra": {}, "in_workbooks": False})
    bc = BillCheck(m, BrainStore(m))
    today = datetime.now(timezone.utc)
    line = {"title": "Construction Fund", "cost_code": "Materials", "unit_cost": 20000.0, "variance": None, "amount": 500.0}
    m.db["bt_bills"].insert_one({"bill_id": 7, "job_id": 45, "property_id": "durham_st", "title": "Materials", "number": "B7", "amount": 500.0, "status": "In review", "in_review": True,
                                 "holdback": False, "invoice_date": today - timedelta(days=1), "created_by": "Kelly Stone", "description": "Urgent&nbsp;order. <b>See receipt.</b>",
                                 "line_items": [line], "file_names": ["HD receipt.pdf", "notes.docx"],
                                 "files": [{"file_id": 501, "title": "HD receipt.pdf", "ext": "pdf", "size": len(PDF), "is_photo": False},
                                           {"file_id": 502, "title": "notes.docx", "ext": "docx", "size": 10, "is_photo": False}]})
    m.db["bt_daily_logs"].insert_one({"log_id": 1, "property_id": "durham_st", "date": today - timedelta(days=2), "title": "Update", "notes": "The materials are needed.", "photos": []})
    return bc, m


def test_the_bills_own_page_gives_each_attached_file_its_id():
    d = Buildertrend._detail_fields({"createdByName": "Kelly", "lineItems": {"value": []}, "attachedFiles": {"files": [
        {"id": 501, "title": "HD receipt.pdf", "extension": "PDF", "fileSize": 37308, "isPhoto": False}, {"title": "old row with a name only"}]}})
    assert d["file_names"] == ["HD receipt.pdf", "old row with a name only"]
    assert d["files"] == [{"file_id": 501, "title": "HD receipt.pdf", "ext": "pdf", "size": 37308, "is_photo": False}]


def test_the_pull_is_told_which_files_of_the_waiting_bills_are_not_held_yet(monkeypatch):
    bc, m = _setup(monkeypatch)
    m.db["bt_bills"].insert_one({"bill_id": 8, "job_id": 45, "property_id": "durham_st", "title": "Paid long ago", "in_review": False,
                                 "files": [{"file_id": 900, "title": "x.pdf", "ext": "pdf", "size": 1, "is_photo": False}]})
    assert [(f["bill_id"], f["file_id"]) for f in bc.bt.files_wanted()] == [(7, 501), (7, 502)]          # only bills waiting for approval
    bc.bt.record_bill_file(file_id=501, bill_id=7, job_id=45, title="HD receipt.pdf", ext="PDF", sha256="a" * 64, nbytes=len(PDF), is_photo=False)
    assert [f["file_id"] for f in bc.bt.files_wanted()] == [502]
    f = bc.bt.bill_files_of([7])[0]
    assert (f["ext"], f["where"], f["bytes"]) == ("pdf", "server", len(PDF))


def test_the_check_is_handed_the_receipt_and_reads_again_when_a_file_arrives(monkeypatch):
    bc, m = _setup(monkeypatch)
    calls = []

    def fake(**kw):
        calls.append(kw["user"])
        key = re.findall(r"^  (\S+) \| ", kw["user"][0]["text"], re.M)[0]
        papers = [{"file_id": 501, "kind": "receipt", "says": "Home Depot, 36 items, delivery 18 Nov, wire to account 435061476992.", "total": 500.0},
                  {"file_id": 777, "kind": "invoice", "says": "a paper nobody gave", "total": 1}] if len(kw["user"]) > 2 else []
        return {"data": {"lines": [{"key": key, "verdict": "nothing", "basis": "No log shows a delivery.", "log_dates": [], "photo_ids": [], "ask": "A photo of the delivery."}],
                         "papers": papers, "other_points": []}, "usage": {}}
    monkeypatch.setattr(billcheck, "structured", fake)
    d0 = bc.draw(7)                                                                                  # no proposal on file: the line is "not placed", never "outside the proposal"
    assert d0["lines"][0]["group"] == "The proposal has not been read from Buildertrend" and "has not been read" in d0["lines"][0]["flags"][0]["text"]
    assert "not a line of the proposal" not in d0["message_for_wes"]
    first = bc.read(7)
    assert len(calls[0]) == 2 and "none could be read here" in calls[0][0]["text"]                  # nothing held yet: head, then the log
    assert "NOTE WRITTEN ON THE BILL by Kelly Stone: Urgent order. See receipt." in calls[0][0]["text"]
    assert "HD receipt.pdf; notes.docx" in calls[0][0]["text"]
    assert "proposal not read" in calls[0][0]["text"] and "NOT read this job's proposal" in calls[0][0]["text"]     # no proposal held: a blank budget is not a zero budget                                       # named as not readable, never described
    assert first["papers"] == [] and first["papers_held"] == [] and bc.stale(7) is None
    # The receipt is brought over: the stored check is out of date, and the next one is given the file itself.
    bc.bt.record_bill_file(file_id=501, bill_id=7, job_id=45, title="HD receipt.pdf", ext="pdf", sha256="a" * 64, nbytes=len(PDF), is_photo=False)
    bc.bt.record_bill_file(file_id=502, bill_id=7, job_id=45, title="notes.docx", ext="docx", sha256="b" * 64, nbytes=10, is_photo=False)
    monkeypatch.setattr(Buildertrend, "bill_file", lambda self, fid: PDF)
    assert bc.stale(7) == "a file attached to the bill has not been read yet"
    again = bc.read(7)
    blocks = calls[1]
    assert blocks[1]["text"] == "attached file id 501 — “HD receipt.pdf”:"
    assert blocks[2] == {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(PDF).decode()}}
    assert "1 file(s) follow" in blocks[0]["text"] and "NOT readable here" in blocks[0]["text"] and "notes.docx" in blocks[0]["text"]
    assert [p["file_id"] for p in again["papers"]] == [501]                                          # a paper that was not given is dropped
    assert again["papers"][0]["title"] == "HD receipt.pdf" and again["papers"][0]["total"] == 500.0
    assert "435061476992" not in again["papers"][0]["says"] and "[number removed]" in again["papers"][0]["says"]
    assert again["papers_held"] == [501, 502] and again["papers_unread"] == ["notes.docx"]
    assert bc.stale(7) is None                                                                       # a file that cannot be read does not make it read for ever
    assert bc.draw(7)["reading"]["papers"][0]["kind"] == "receipt"
    tried = bc.read(7, force=True, save=False)                                                       # a trial reading leaves the stored one alone
    assert tried["papers"] and m.db["bill_checks"].find_one({"bill_id": 7})["reading"]["at"] == m.db["bill_checks"].find_one({"bill_id": 7})["reading"]["at"]


def test_a_file_the_model_cannot_take_does_not_stop_the_check(monkeypatch):
    bc, m = _setup(monkeypatch)
    bc.bt.record_bill_file(file_id=501, bill_id=7, job_id=45, title="HD receipt.pdf", ext="pdf", sha256="a" * 64, nbytes=len(PDF), is_photo=False)
    monkeypatch.setattr(Buildertrend, "bill_file", lambda self, fid: PDF)
    seen = []

    def fake(**kw):
        seen.append(kw["user"])
        if any(b.get("type") == "document" for b in kw["user"]):
            raise ValueError("400: the PDF could not be processed")
        key = re.findall(r"^  (\S+) \| ", kw["user"][0]["text"], re.M)[0]
        return {"data": {"lines": [{"key": key, "verdict": "said", "basis": "x", "log_dates": [], "photo_ids": [], "ask": "y"}], "papers": [], "other_points": []}, "usage": {}}
    monkeypatch.setattr(billcheck, "structured", fake)
    r = bc.read(7)
    assert len(seen) == 2 and not any(b.get("type") == "document" for b in seen[1])
    assert r["papers"] == [] and r["papers_unread"] == ["HD receipt.pdf", "notes.docx"] and r["papers_held"] == [501] and bc.stale(7) is None


def test_a_photo_attached_to_the_bill_can_show_a_line_and_is_served_like_a_log_photo(monkeypatch):
    import io
    from PIL import Image
    bc, m = _setup(monkeypatch)
    buf = io.BytesIO()
    Image.new("RGB", (3000, 1500), "white").save(buf, "PNG")
    bc.bt.record_bill_file(file_id=601, bill_id=7, job_id=45, title="IMG_1.png", ext="png", sha256="c" * 64, nbytes=len(buf.getvalue()), is_photo=True)
    monkeypatch.setattr(Buildertrend, "bill_file", lambda self, fid: buf.getvalue())

    def fake(**kw):
        key = re.findall(r"^  (\S+) \| ", kw["user"][0]["text"], re.M)[0]
        img = [b for b in kw["user"] if b.get("type") == "image"][0]
        w, h = Image.open(io.BytesIO(base64.b64decode(img["source"]["data"]))).size
        assert (w, h) == (2000, 1000)                                                                # large enough to read a photographed receipt
        return {"data": {"lines": [{"key": key, "verdict": "shown", "basis": "The attached photo shows the pallets.", "log_dates": [], "photo_ids": [601, 999], "ask": ""}],
                         "papers": [{"file_id": 601, "kind": "photo", "says": "Pallets of drywall on the porch.", "total": None}], "other_points": []}, "usage": {}}
    monkeypatch.setattr(billcheck, "structured", fake)
    r = bc.read(7)
    line = list(r["lines"].values())[0]
    assert line["verdict"] == "shown" and line["photo_ids"] == [601] and r["photos_shown"] == 0
    assert bc.bt.photo(601) == buf.getvalue() and bc.bt.photo(999) is None


def test_long_numbers_are_removed_and_ordinary_ones_are_kept():
    text = "Order H4647-376467 of 17 Nov 2026, total $5,928.18, 70 panels, SKU 525423; wire to 435061476992, routing 026009593, card 4111 1111 1111 1111."
    out = _no_account_numbers(text)
    assert "H4647-376467" in out and "$5,928.18" in out and "17 Nov 2026" in out and "525423" in out
    assert "435061476992" not in out and "026009593" not in out and "4111" not in out and out.count("[number removed]") == 3
    r = clean_reading({"lines": [], "papers": [], "other_points": ["Pay by wire to 435061476992."]}, set(), set(), set(), {})
    assert r["points"] == ["Pay by wire to [number removed]."] and r["papers"] == []


def test_the_lines_of_a_receipt_are_kept_and_its_total_is_checked_against_the_bill():
    from mangotree.brain.billcheck import paper_check
    raw = {"lines": [], "other_points": [], "papers": [{"file_id": 501, "kind": "receipt", "says": "Home Depot.", "total": 500, "items": [
        {"item": "Drywall 1/2 in. x 4 ft. x 8 ft.", "qty": 70, "unit_price": 20.03, "amount": 1402.1}, {"item": "", "qty": 1, "unit_price": 1, "amount": 1},
        {"item": "Sales tax", "qty": None, "unit_price": None, "amount": "334.15"}]}]}
    p = clean_reading(raw, set(), set(), set(), {501: "HD receipt.pdf"})["papers"][0]
    assert [{k: i[k] for k in ("item", "qty", "unit_price", "amount")} for i in p["items"]] == [
        {"item": "Drywall 1/2 in. x 4 ft. x 8 ft.", "qty": 70.0, "unit_price": 20.03, "amount": 1402.1},
        {"item": "Sales tax", "qty": None, "unit_price": None, "amount": None}]                           # a row with no name is dropped; a figure that is not a number is not kept
    assert paper_check([p], 500.0) == {"key": "paper_total", "ok": True, "text": "The attached receipt comes to $500.00, the same as the bill."}
    assert paper_check([p], 650.0) == {"key": "paper_total", "ok": False, "text": "The attached receipt comes to $500.00; the bill asks $650.00."}
    inv = {"kind": "invoice", "total": 150.0, "title": "inv.pdf"}
    assert paper_check([p, inv], 650.0)["text"] == "The 2 attached papers add up to $650.00, the same as the bill."
    assert paper_check([{"kind": "photo", "total": None}], 650.0) is None and paper_check([], 650.0) is None
    assert paper_check([{"kind": "payment_proof", "total": 650.0}], 650.0) is None                     # a proof of payment is not what the bill is checked against


def test_a_material_counts_as_seen_only_on_a_photo_that_was_shown():
    """Item by item: where on the property is what the receipt lists. A claim rests on a photo, or it is 'not seen'."""
    from mangotree.brain.billcheck import _item
    shown = {11, 12}
    faucet = _item({"item": "Delta Faryn Tub and Shower Faucet, Brushed Nickel", "qty": 1, "unit_price": 179, "amount": 179, "seen": "installed",
                    "where": "Upstairs bathroom, tub wall, 20 Nov", "photo_ids": [11, 99]}, shown)
    assert (faucet["seen"], faucet["where"], faucet["photo_ids"]) == ("installed", "Upstairs bathroom, tub wall, 20 Nov", [11])     # a photo never shown is dropped
    toilet = _item({"item": "Glacier Bay toilet", "qty": 2, "unit_price": 94, "amount": 188, "seen": "installed", "where": "Hall bath", "photo_ids": [99]}, shown)
    assert (toilet["seen"], toilet["photo_ids"]) == ("not_seen", [])                                                                   # no photo left behind it: not seen
    studs = _item({"item": "2x4 stud", "qty": 180, "unit_price": 3.82, "amount": 687.6, "seen": "similar", "where": "Basement framing, 14 Nov; cannot be tied to this order", "photo_ids": [12]}, shown)
    assert studs["seen"] == "similar" and studs["photo_ids"] == [12]
    tax = _item({"item": "Sales Tax", "qty": None, "unit_price": None, "amount": 334.15, "seen": "not_a_material", "where": "", "photo_ids": [11]}, shown)
    assert (tax["seen"], tax["photo_ids"]) == ("not_a_material", [])
    waiting = _item({"item": "Shower pan", "qty": 1, "unit_price": 239, "amount": 239, "seen": "not_seen", "where": "Delivery set for 18 Nov; newest log 17 Nov; wire 435061476992", "photo_ids": []}, shown)
    assert waiting["seen"] == "not_seen" and "[number removed]" in waiting["where"]
    old = _item({"item": "From a reading made before this", "qty": 1, "unit_price": 1, "amount": 1}, shown)
    assert (old["seen"], old["where"], old["photo_ids"]) == (None, "", [])


def test_a_hidden_material_counts_as_used_only_with_a_photo_of_the_work_that_covers_it():
    """Nails, thinset, tape: they vanish into the work. 'Likely used, now covered' rests on a photo of that work."""
    from mangotree.brain.billcheck import SEEN_LABEL, _item
    thinset = _item({"item": "VersaBond thinset mortar 50 lb", "qty": 10, "unit_price": 13.47, "amount": 134.7, "seen": "covered",
                     "where": "Behind the tiled tub wall, upstairs bath, 20 Nov; covered, tile set over it", "photo_ids": [11]}, {11})
    assert (thinset["seen"], thinset["photo_ids"]) == ("covered", [11]) and SEEN_LABEL["covered"] == "Likely used, now covered"
    nails = _item({"item": "Framing nails 4000 per box", "qty": 1, "unit_price": 121, "amount": 121, "seen": "covered", "where": "In the basement framing", "photo_ids": [77]}, {11})
    assert (nails["seen"], nails["photo_ids"]) == ("not_seen", [])          # no photo of the covering work was shown: not counted as used
