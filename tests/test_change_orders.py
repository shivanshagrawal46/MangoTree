"""Buildertrend's Change Orders list: that each change order is stored with its approval as
Buildertrend words it, that a bill line is tied to its change order by Buildertrend's own line id
and never by its name, that an approved one is shown as a fact and not as a warning, and that a
change order that is not approved (or that Buildertrend does not name) makes a person look."""
from datetime import datetime, timedelta, timezone

import mongomock

from mangotree.brain.billcheck import BillCheck
from mangotree.brain.buildertrend import Buildertrend
from mangotree.brain.store import BrainStore


class FakeMongo:
    def __init__(self):
        self.db = mongomock.MongoClient().db
        self.artifacts = self.db["artifacts"]


def _co(i, number, title, status, text, cost, **more):
    return {"id": i, "jobId": 45, "number": number, "title": title, "status": status, "rawStatus": 3 if status == "Approved" else 1, "statusText": text,
            "builderCost": cost, "ownerPrice": cost * 1.2, "added": "2026-11-11T08:44:49.047", "lines": [{"id": i * 10, "title": title, "unitCost": cost, "quantity": 1}], **more}


PULL = {
    "pulled_at": "2026-11-19T12:00:00Z", "job_ids": [45],
    "change_orders": [
        _co(1, "0001", "Roof", "Approved", "Approved by Wes Stone on Oct 14, 2026", 3600.0, approvedBy="Wes Stone", dateApproved="2026-10-14T10:00:00"),
        _co(2, "0002", "New HVAC system", "Pending", "Pending since Nov 18, 2026", 10850.0),
        {**_co(3, "0003", "Something on a job we do not hold", "Approved", "Approved by A on Nov 1, 2026", 10.0), "jobId": 999},
    ],
    "po_lines": [{"poLineId": 7001, "poId": 70, "coId": 1}, {"poLineId": 7002, "poId": 70, "coId": 2}],
}


def _setup(monkeypatch):
    BrainStore._indexed = True
    m = FakeMongo()
    from mangotree.brain import moneysheet
    monkeypatch.setattr(moneysheet.snapshot, "numbers", lambda store, pid, file: {"main": [], "extra": {}, "in_workbooks": False})
    bc = BillCheck(m, BrainStore(m))
    m.db["bt_jobs"].insert_one({"job_id": 45, "property_id": "durham_st"})
    return bc, m


def _bill(m, bill_id, lines):
    m.db["bt_bills"].insert_one({"bill_id": bill_id, "job_id": 45, "property_id": "durham_st", "title": "Draw %d" % bill_id, "number": "B%d" % bill_id,
                                 "amount": sum(l["amount"] for l in lines), "status": "In review", "in_review": True, "holdback": False,
                                 "invoice_date": datetime.now(timezone.utc) - timedelta(days=1), "line_items": lines})


def _line(title, unit, po, amount):
    return {"title": title, "cost_code": "72", "unit_cost": unit, "variance": "72 - Customer change order", "po_line_id": po, "amount": amount}


def test_the_list_is_stored_with_buildertrends_own_words_and_the_tie_to_bill_lines(monkeypatch):
    bc, m = _setup(monkeypatch)
    r = bc.bt.import_change_orders(PULL)
    assert (r["change_orders"], r["approved"], r["tied_to_bill_lines"], r["unmatched_jobs"]) == (2, 1, 2, [999])
    roof = m.db["bt_change_orders"].find_one({"co_id": 1})
    assert (roof["property_id"], roof["approved"], roof["status_text"], roof["approved_by"], roof["po_line_ids"]) == ("durham_st", True, "Approved by Wes Stone on Oct 14, 2026", "Wes Stone", [7001])
    assert m.db["bt_change_orders"].find_one({"co_id": 2})["approved"] is False
    assert set(bc.bt.change_order_by_po_line("durham_st")) == {7001, 7002}


def test_a_change_order_deleted_in_buildertrend_is_removed_here(monkeypatch):
    bc, m = _setup(monkeypatch)
    bc.bt.import_change_orders(PULL)
    r = bc.bt.import_change_orders({**PULL, "change_orders": PULL["change_orders"][:1]})
    assert r["removed"] == 1 and [c["co_id"] for c in bc.bt.change_orders_of("durham_st")] == [1]


def test_an_approved_change_order_is_a_fact_on_the_bill_line_not_a_warning(monkeypatch):
    bc, m = _setup(monkeypatch)
    bc.bt.import_change_orders(PULL)
    _bill(m, 7, [_line("Roof Framing", 3600.0, 7001, 1800.0)])            # the names differ: the tie is the line id
    line = bc.draw(7)["lines"][0]
    assert line["kind"] == "change_order" and line["flags"] == []
    assert (line["change_order"]["number"], line["change_order"]["status_text"]) == ("0001", "Approved by Wes Stone on Oct 14, 2026")
    assert "hange order" not in bc.draw(7)["message_for_wes"]


def test_a_change_order_that_is_not_approved_makes_a_person_look(monkeypatch):
    bc, m = _setup(monkeypatch)
    bc.bt.import_change_orders(PULL)
    _bill(m, 8, [_line("New HVAC system", 10850.0, 7002, 5000.0)])
    line = bc.draw(8)["lines"][0]
    assert [f["kind"] for f in line["flags"]] == ["co_not_approved"] and "Pending since Nov 18, 2026" in line["flags"][0]["text"]
    assert line["state"] == "check"


def test_a_change_order_line_buildertrend_names_no_change_order_for_keeps_its_warning(monkeypatch):
    bc, m = _setup(monkeypatch)
    bc.bt.import_change_orders(PULL)
    _bill(m, 9, [_line("Roof", 3600.0, 7999, 100.0)])                       # same name as change order 0001, another line id
    line = bc.draw(9)["lines"][0]
    assert line["change_order"] is None and [f["kind"] for f in line["flags"]] == ["change_order"]


def test_the_money_sheet_lists_every_change_order_with_what_is_billed_under_it(monkeypatch):
    bc, m = _setup(monkeypatch)
    bc.bt.import_change_orders(PULL)
    _bill(m, 7, [_line("Roof Framing", 3600.0, 7001, 1800.0)])
    _bill(m, 8, [_line("Roof Framing", 3600.0, 7001, 600.0)])
    s = bc.sheet.build("durham_st")
    assert [(c["number"], c["status"], c["billed"]) for c in s["change_orders"]] == [("0001", "Approved", 2400.0), ("0002", "Pending", 0.0)]
    row = next(r for r in s["rows"] if r["kind"] == "change_order")
    assert row["change_order"]["number"] == "0001"


def test_the_follow_up_for_a_change_order_goes_when_buildertrend_shows_it_approved(monkeypatch):
    from mangotree.brain import followlist
    bc, m = _setup(monkeypatch)
    monkeypatch.setattr(followlist, "name_of", lambda pid: pid)
    m.db["property_files"].insert_one({"property_id": "durham_st", "stage": {"stage": "construction", "recovery": False}})
    for bill_id, line in ((7, _line("Roof Framing", 3600.0, 7001, 1800.0)), (8, _line("New HVAC system", 10850.0, 7002, 5000.0)), (9, _line("Extra door", 400.0, 7999, 400.0))):
        m.db["bt_bills"].insert_one({"bill_id": bill_id, "job_id": 45, "property_id": "durham_st", "title": "Draw %d" % bill_id, "amount": line["amount"], "status": "Paid",
                                     "holdback": False, "invoice_date": datetime(2026, 11, 2), "line_items": [line]})
    fu = followlist.FollowUps(m, BrainStore(m))
    today = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    before = [i for i in fu.build(today=today) if i["kind"] == "change_order"]
    assert len(before) == 3                                                # nothing read from Buildertrend's list yet: every one asks for its paper
    bc.bt.import_change_orders(PULL)
    after = {i["title"].split(" — ")[0]: i for i in fu.build(today=today) if i["kind"] == "change_order"}
    assert set(after) == {"Change order: New HVAC system", "Change order: Extra door"}      # the approved Roof one is gone
    assert "not approved: Pending since Nov 18, 2026" in after["Change order: New HVAC system"]["detail"]
    assert "signed by the homeowner" in after["Change order: Extra door"]["detail"]
