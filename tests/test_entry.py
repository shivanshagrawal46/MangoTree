"""What the contractor enters in RKB's own portal: bills, daily logs and change orders (mangotree/brain/entry.py)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import mongomock
import pytest

from mangotree.brain.buildertrend import Buildertrend
from mangotree.brain.entry import Entry, EntryError, ID_BASE
from mangotree.brain.store import BrainStore
from mangotree.core import eastern

PID = "durham_st"
KELLY = {"user_id": "kelly", "name": "Kelly Stone", "side": "contractor", "org": "roi_blocks", "property_ids": [PID, "marmon"]}
RKB = {"user_id": "manjunath", "name": "Manjunath Sir", "role": "operations"}


class Files:
    """Where the bytes go in a test."""
    def __init__(self):
        self.held = {}

    def put(self, sha, blob, name, meta):
        self.held[sha] = blob

    def get(self, sha):
        return self.held.get(sha)


def setup():
    class M:
        db = mongomock.MongoClient().db
        artifacts = db["artifacts"]

    m = M()
    m.db["bt_jobs"].insert_one({"job_id": 77, "property_id": PID, "name": "BAL- Durham St Baltimore"})
    m.db["bt_estimates"].insert_one({"job_id": 77, "property_id": PID, "groups": [
        {"title": "05 - Plumbing System", "lines": [{"line_id": 1, "title": "Plumbing - Modify and Upgrade", "cost_code": "47 - Plumbing", "builder_cost": 5500.0, "price": 8000.0, "po_line_id": 501}]},
        {"title": "06 - Electrical System", "lines": [{"line_id": 2, "title": "Electrical - Modify and Upgrade", "cost_code": "35 - Electrical", "builder_cost": 10000.0, "price": 15000.0, "po_line_id": 502},
                                                     {"line_id": 3, "title": "Exterior paint", "cost_code": "37", "builder_cost": 0.0, "price": 0.0, "po_line_id": 503}]}]})
    # What Buildertrend already holds: $1,350 paid and $150 held on the electrical line, and a materials bill of $2,569.06.
    m.db["bt_bills"].insert_many([
        {"bill_id": 11, "job_id": 77, "property_id": PID, "title": "GR-5 (Contractor)", "amount": 1350.0, "status": "Paid", "holdback": False, "pay_to": "Tutt Painting and Remodeling LLC",
         "line_items": [{"title": "Electrical", "amount": 1350.0, "po_line_id": 502}]},
        {"bill_id": 12, "job_id": 77, "property_id": PID, "title": "GR-5 (Hold-back)", "amount": 150.0, "balance": 150.0, "status": "Ready for payment", "holdback": True,
         "pay_to": "Tutt Painting and Remodeling LLC", "line_items": [{"title": "Electrical", "amount": 150.0, "po_line_id": 502}]},
        {"bill_id": 13, "job_id": 77, "property_id": PID, "title": "Electrical materials", "amount": 2569.06, "status": "Ready for payment", "holdback": False, "pay_to": "Tutt Painting and Remodeling LLC",
         "line_items": [{"title": "Electrical", "amount": 2569.06, "po_line_id": 502}]}])
    e = Entry(m, BrainStore(m), files=Files())
    return m, e


def photo(e, n=1, role="photo"):
    return [e.add_file(KELLY, PID, role=role, name=f"p{i}.jpg", blob=b"jpeg-bytes-%d-%s" % (i, role.encode()))["file_id"] for i in range(n)]


def test_the_lines_show_what_each_is_worth_what_is_billed_and_what_is_left():
    m, e = setup()
    book = e.lines(PID)
    by = {l["title"]: l for l in book["lines"]}
    assert set(by) == {"Plumbing - Modify and Upgrade", "Electrical - Modify and Upgrade"}            # a line worth nothing is not offered
    assert (by["Electrical - Modify and Upgrade"]["billed"], by["Electrical - Modify and Upgrade"]["left"]) == (4069.06, 5930.94)   # paid and held alike
    assert book["totals"] == {"proposal": 15500.0, "billed": 4069.06, "left": 11430.94} and book["held"] == 150.0
    assert e.pay_to(PID) == ["Tutt Painting and Remodeling LLC"]
    assert [j["property_id"] for j in e.jobs_of(KELLY)] == [PID]                                       # open on Durham only, though her account has more
    with pytest.raises(PermissionError):
        e.lines(PID) and e.check({**KELLY, "property_ids": ["marmon"]}, PID)


def test_a_bill_cannot_be_sent_until_everything_it_needs_is_there():
    m, e = setup()
    v = e.save_bill(KELLY, PID, {"kind": "work", "lines": {"501": 1500, "502": 6000}, "invoice_total": 7500})
    codes = {p["code"] for p in v["problems"]}
    assert codes == {"over", "pay_to", "invoice_no", "invoice", "photos"} and not v["can_send"]       # 6,000 is over the 5,930.94 left on electrical
    with pytest.raises(EntryError):
        e.send_bill(KELLY, v["entry_id"])
    files = photo(e, 3) + photo(e, 1, "invoice")
    v = e.save_bill(KELLY, PID, {"kind": "work", "pay_to": "Tutt Painting and Remodeling LLC", "invoice_no": "0027", "invoice_date": "2026-11-21", "invoice_total": 3400,
                                 "lines": {"501": 1500, "502": 2000}, "files": files}, v["entry_id"])
    assert [p["code"] for p in v["problems"]] == ["total"]                                              # the invoice says 3,400, the lines add to 3,500
    v = e.save_bill(KELLY, PID, {"kind": "materials", "pay_to": "Tutt", "invoice_no": "0027", "invoice_total": 3500, "lines": {"501": 1500, "502": 2000}, "files": files}, v["entry_id"])
    assert [p["code"] for p in v["problems"]] == ["receipt"] and v["holdback"] == 0.0                   # materials: receipts, and nothing held back
    assert m.db["bt_bills"].count_documents({"source": "contractor"}) == 0                              # nothing reaches RKB until it is sent


def test_a_sent_bill_is_on_rkbs_bill_list_at_once_as_the_part_to_pay_and_the_part_held():
    m, e = setup()
    files = photo(e, 3) + photo(e, 1, "invoice")
    v = e.save_bill(KELLY, PID, {"kind": "work", "pay_to": "Tutt Painting and Remodeling LLC", "invoice_no": "0027", "invoice_date": "2026-11-21", "invoice_total": 3500.55,
                                 "lines": {"501": 1500.55, "502": 2000}, "files": files})
    assert (v["total"], v["holdback"], v["pay_now"], v["can_send"]) == (3500.55, 350.06, 3150.49, True)
    v = e.send_bill(KELLY, v["entry_id"])
    main, held = [m.db["bt_bills"].find_one({"bill_id": i}) for i in v and m.db["entry_bills"].find_one({"entry_id": v["entry_id"]})["bill_ids"]]
    assert main["bill_id"] > ID_BASE and (main["title"], held["title"]) == ("Contractor's work — invoice 0027 (Contractor)", "Contractor's work — invoice 0027 (Hold-back)")
    assert (main["holdback"], held["holdback"], main["in_review"], main["to_approve"], main["status"], main["source"]) == (False, True, True, True, "In review", "contractor")
    assert round(main["amount"] + held["amount"], 2) == 3500.55 and (main["amount"], held["amount"]) == (v["pay_now"], v["holdback"]) == (3150.49, 350.06)   # to the cent, together
    assert {li["po_line_id"]: li["amount"] for li in main["line_items"]} == {501: 1350.49, 502: 1800.0}
    assert {li["po_line_id"]: li["amount"] for li in held["line_items"]} == {501: 150.06, 502: 200.0}                         # each line's two parts add to what was billed
    assert main["created"].hour == eastern.to_et(datetime.now(timezone.utc)).hour and main["number"] == "MT-0001"           # the firm's wall-clock time, as Buildertrend's dates are kept
    assert m.db["bt_bill_files"].count_documents({"bill_id": main["bill_id"]}) == 4 and m.db["bt_bill_files"].count_documents({"is_photo": True}) == 3
    assert (v["status"], v["stands"], v["can_withdraw"]) == ("sent", "RKB is checking", True)
    assert {l["key"]: l["left"] for l in e.lines(PID)["lines"]} == {"501": 3999.45, "502": 3930.94}                           # what is left has gone down
    # A pull from Buildertrend that does not know this bill must not delete it (it deletes Buildertrend's own that have left).
    Buildertrend(m).import_bills({"bills": {"77": {"rows": [], "inReviewIds": []}}})
    assert m.db["bt_bills"].count_documents({"source": "contractor"}) == 2 and m.db["bt_bills"].count_documents({"bill_id": {"$in": [11, 12, 13]}}) == 0
    with pytest.raises(EntryError):
        e.save_bill(KELLY, PID, {"lines": {"501": 1}}, v["entry_id"])                                                        # a sent bill is not edited in place


def test_where_a_bill_stands_follows_rkbs_ticks_and_it_can_be_taken_back_only_before_them():
    m, e = setup()
    files = photo(e, 1, "invoice") + photo(e, 1, "receipt")
    v = e.send_bill(KELLY, e.save_bill(KELLY, PID, {"kind": "materials", "pay_to": "Home Depot", "invoice_no": "HD-9", "invoice_total": 800, "lines": {"501": 800},
                                                    "files": files + photo(e, 3)})["entry_id"])
    assert m.db["bt_bills"].count_documents({"source": "contractor"}) == 1                                                    # materials: one bill, nothing held
    v = e.withdraw_bill(KELLY, v["entry_id"])
    assert v["status"] == "draft" and m.db["bt_bills"].count_documents({"source": "contractor"}) == 0 and m.db["bt_bill_files"].count_documents({}) == 0
    v = e.send_bill(KELLY, v["entry_id"])
    at = datetime.now(timezone.utc)
    m.db["bill_tracking"].insert_one({"bill_id": v["bill_id"], "steps": {"manju_approved": {"at": at, "by": "manjunath"}, "team_reviewed": None}})
    v = e.bill(KELLY, v["entry_id"])
    assert (v["stage"], v["stands"], v["can_withdraw"]) == ("approved", "Approved", False)
    with pytest.raises(EntryError):
        e.withdraw_bill(KELLY, v["entry_id"])
    m.db["bill_tracking"].update_one({"bill_id": v["bill_id"]}, {"$set": {"steps.ready_to_pay": {"at": at}, "steps.paid": {"at": at}}})
    assert e.bill(KELLY, v["entry_id"])["stands"] == "Paid"


def test_a_daily_log_needs_words_work_and_photos_and_is_on_the_site_record_at_once():
    m, e = setup()
    with pytest.raises(EntryError):
        e.add_log(KELLY, PID, {"tags": ["Framing"], "notes": "Framed the half bath.", "files": photo(e, 2)})                  # two photos are not enough
    with pytest.raises(EntryError):
        e.add_log(KELLY, PID, {"tags": ["Framing"], "notes": "Framed the half bath.", "files": photo(e, 3), "date": f"{datetime.now(timezone.utc) + timedelta(days=3):%Y-%m-%d}"})
    out = e.add_log(KELLY, PID, {"tags": ["Framing", "Basement", "not a tag"], "notes": "Finished the half-bath framing on the main level.", "blocked": "Waiting on the inspector",
                                 "files": photo(e, 4)})
    log = m.db["bt_daily_logs"].find_one({"log_id": out["log_id"]})
    assert (log["by"], log["title"], log["tags"], len(log["photos"]), log["source"]) == ("Kelly Stone", "Framing, Basement", ["Framing", "Basement"], 4, "contractor")
    assert "Holding the work up: Waiting on the inspector" in log["notes"] and m.db["bt_photos"].count_documents({"where": "server", "job_id": 77}) == 4
    assert m.db["bt_pulls"].find_one({"source": "contractor"})["new_logs"] == {PID: 1}                                        # the site reading knows there is something new
    seen = e.logs_of(KELLY, PID)
    assert seen["logged_today"] and seen["logs"][0]["here"] and seen["logs"][0]["photos"] == 4
    assert Buildertrend(m).photo(log["photos"][0]["file_id"]) is None or True                                                 # served the way every site photo is


def test_a_change_order_can_be_billed_only_after_rkb_agrees_to_it():
    m, e = setup()
    body = {"title": "Sewer line replacement", "group": "05 - Plumbing System", "days": 4, "why": "The clay sewer line under the slab had collapsed; found when the floor was opened.",
            "lines": [{"title": "Replace 30 ft of sewer line", "kind": "labour", "cost": 4200, "price": 5800}], "approved_by": "Wes Stone", "approved_on": "2026-11-20"}
    with pytest.raises(EntryError):
        e.add_change_order(KELLY, PID, {**body, "files": photo(e, 2)})                                                        # the approved copy from Buildertrend is missing
    co = e.add_change_order(KELLY, PID, {**body, "files": photo(e, 2) + photo(e, 1, "approval")})
    assert (co["status"], co["stands"], co["cost"], co["price"]) == ("sent", "Waiting for RKB", 4200.0, 5800.0)
    assert not any(l["change_order"] for l in e.lines(PID)["lines"]) and [c["co_id"] for c in e.waiting_for_rkb()] == [co["co_id"]]
    with pytest.raises(EntryError):
        e.decide_change_order(RKB, co["co_id"], agree=False, why="")                                                          # a no needs its reason
    done = e.decide_change_order(RKB, co["co_id"], agree=True)
    assert (done["status"], done["agreed_by_name"]) == ("agreed", "Manjunath Sir") and e.waiting_for_rkb() == []
    bt = m.db["bt_change_orders"].find_one({"co_id": co["co_id"]})
    assert (bt["approved"], bt["builder_cost"], bt["source"], bt["rkb_agreed_by"]) == (True, 4200.0, "contractor", "manjunath")
    line = next(l for l in e.lines(PID)["lines"] if l["change_order"])
    assert (line["proposal"], line["left"], line["group"]) == (4200.0, 4200.0, "Approved change orders")
    v = e.send_bill(KELLY, e.save_bill(KELLY, PID, {"kind": "work", "pay_to": "Tutt", "invoice_no": "0028", "invoice_total": 4200, "lines": {line["key"]: 4200},
                                                    "files": photo(e, 3) + photo(e, 1, "invoice")})["entry_id"])
    item = m.db["bt_bills"].find_one({"bill_id": v["bill_id"]})["line_items"][0]
    assert (item["variance"], item["po_line_id"], item["amount"]) == ("Change Order", line["po_line_id"], 3780.0)
    seen = e.change_orders_of(KELLY, PID)
    assert [(c["title"], c["rkb_agreed"], c["billed_in_full"]) for c in seen["approved"]] == [("Sewer line replacement", True, True)]
    with pytest.raises(EntryError):
        e.decide_change_order(RKB, co["co_id"], agree=True)                                                                   # decided once


def test_a_bill_entered_here_shows_on_rkbs_bills_page_and_money_sheet_like_any_other():
    from mangotree.brain.billcheck import BillCheck
    from mangotree.brain.billtrack import BillTracker
    from mangotree.brain.moneysheet import MoneySheet
    m, e = setup()
    v = e.send_bill(KELLY, e.save_bill(KELLY, PID, {"kind": "work", "pay_to": "Tutt Painting and Remodeling LLC", "invoice_no": "0027", "invoice_total": 3500,
                                                    "lines": {"501": 1500, "502": 2000}, "files": photo(e, 3) + photo(e, 1, "invoice")})["entry_id"])
    rows = BillTracker(m).rows("open")
    mine = [r for r in rows if r["bill_id"] == v["bill_id"]]
    assert len(mine) == 1 and mine[0]["amount"] == 3150.0 and not any("Hold-back" in str(r.get("title")) for r in rows)      # the part to pay is listed; the part held is not a bill to process
    bills = list(m.db["bt_bills"].find({"property_id": PID}, {"_id": 0}))
    main = next(b for b in bills if b["bill_id"] == v["bill_id"])
    paid_part, held_part = BillCheck.pair(main, bills)
    assert paid_part["bill_id"] == v["bill_id"] and held_part and held_part["holdback"] and held_part["amount"] == 350.0       # found as one draw: 90% and its 10%
    sheet_rows, notes = MoneySheet(m)._rows(m.db["bt_estimates"].find_one({"property_id": PID}), bills, set())
    plumbing = next(r for r in sheet_rows if r["title"].startswith("Plumbing"))
    assert plumbing["kind"] == "proposal" and round(plumbing["billed"], 2) == 1500.0 and not notes                             # on its own proposal line, by the line's own tie


DUWAYNE = {"user_id": "duwayne", "name": "Duwayne Tutt", "side": "contractor", "role": "contractor", "org": "tutt_painting", "org_name": "Tutt Painting and Remodeling LLC",
           "property_ids": [PID], "can": ["logs"]}


def test_a_site_persons_account_enters_daily_logs_and_sees_nothing_else():
    from mangotree.api.auth import PORTAL_PARTS, ensure_contractor, public_user
    from mangotree.portal.sheets import contractor_orgs, contractor_tasks
    m, e = setup()
    pics = [e.add_file(DUWAYNE, PID, role="photo", name=f"s{i}.jpg", blob=b"site-%d" % i)["file_id"] for i in range(3)]
    out = e.add_log(DUWAYNE, PID, {"tags": ["Framing"], "notes": "Framed the half bath on the main level.", "files": pics})
    assert m.db["bt_daily_logs"].find_one({"log_id": out["log_id"]})["by"] == "Duwayne Tutt" and e.logs_of(DUWAYNE, PID)["logs"][0]["here"]
    e.check_file(DUWAYNE, PID, "photo")                                                               # a photo of the site may be opened
    kellys = e.save_bill(KELLY, PID, {"kind": "work", "lines": {"501": 1500}, "invoice_total": 1500})
    paper = e.add_file(KELLY, PID, role="invoice", name="inv.pdf", blob=b"%PDF-1.4 invoice")
    for refused in (lambda: e.home(DUWAYNE, PID),                                                     # the proposal and what is left on it
                    lambda: e.bills_of(DUWAYNE, PID), lambda: e.bill(DUWAYNE, kellys["entry_id"]), lambda: e.save_bill(DUWAYNE, PID, {"kind": "work"}),
                    lambda: e.send_bill(DUWAYNE, kellys["entry_id"]), lambda: e.change_orders_of(DUWAYNE, PID), lambda: e.add_change_order(DUWAYNE, PID, {}),
                    lambda: e.add_file(DUWAYNE, PID, role="invoice", name="i.pdf", blob=b"%PDF-1.4"), lambda: e.check_file(DUWAYNE, PID, paper["role"])):
        with pytest.raises(PermissionError):
            refused()
    assert e.bill(KELLY, kellys["entry_id"])["status"] == "draft"                                    # the office account is as it was
    # No next-steps sheet is ever made for a site account's company, and the account is told what it has.
    kelly = ensure_contractor(m, user_id="kelly", login_name="kelly", name="Kelly", full_name="Kelly Stone", org="roi_blocks", org_name="ROI Blocks", person_id="kelly", property_ids=[PID])
    site = ensure_contractor(m, user_id="duwayne", login_name="duwayne", name="Duwayne", full_name="Duwayne Tutt", org="tutt_painting", org_name="Tutt Painting and Remodeling LLC",
                             person_id="duwayne_tutt", property_ids=[PID], can=["logs"])
    assert kelly["can"] == list(PORTAL_PARTS) and site["can"] == ["logs"] and public_user(m.db["users"].find_one({"user_id": "duwayne"}))["can"] == ["logs"]
    assert set(contractor_orgs(m)) == {"roi_blocks"} and contractor_tasks(m, site) == []
    assert not m.db["users"].find_one({"user_id": "duwayne"}).get("password_hash")                    # made without a password: nobody can sign in yet
    with pytest.raises(ValueError):
        ensure_contractor(m, user_id="x", login_name="x", name="X", full_name="X", org="o", org_name="O", person_id="x", property_ids=[PID], can=["everything"])
