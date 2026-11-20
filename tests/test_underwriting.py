"""Underwriting: the rules that decide a deal and the locks that protect RKB in a
foreclosure. All arithmetic and all permissions, no network and no model.

The figures in the first tests are RKB's own: the 308 E 21st St conditional loan
commitment of 19 Nov 2026 (note $120,304.86 on a value of $235,000 = 51.2%; with
twelve months' interest at 18% and two points, 61.4%).
"""
from __future__ import annotations

import mongomock
import pytest

from mangotree.underwriting import checklist as C, engine as E, reader, secret, states as S, store as st


class FakeMongo:
    def __init__(self):
        self.db = mongomock.MongoClient().db


RAKESH = {"user_id": "rakesh", "name": "Rakesh Sir", "role": "ceo"}
MANJU = {"user_id": "manjunath", "name": "Manju Sir", "role": "operations"}
SHIV = {"user_id": "shivansh", "name": "Shivansh", "role": "team"}
WES = {"user_id": "wes", "name": "Wes", "role": "contractor", "side": "contractor", "org": "roi"}

E21 = {"address": "308 E 21st St, Baltimore, MD 21218", "state": "MD", "county": "Baltimore City", "arv": 235000, "as_is_value": 40000,
       "owner_kind": "llc", "owner_name": "510 A Holdings LLC", "sqft_above": 1375, "sqft_below": 450, "measured": True, "scope": "full_gut",
       "builder_cost": 83743.10, "contingency": 9304.78, "roi_at_closing": 6813.12, "contract_price": 120304.86,
       "rate": 18, "points": 2, "term_months": 12, "interest_free_months": 0, "arv_analysis_id": "arv_x",
       "closing_costs": 0, "has_mortgage": False, "owner_lives_there": False}


# ------------------------------------------------------------------ the 65% rule
def test_21st_st_matches_the_commitment_letter():
    a = E.assess(E21)
    r = a["rule_65"]
    assert round(a["money"]["principal"] / 235000 * 100, 1) == 51.2
    # The 65% does not count the note. It counts RKB's own money: 90% of the builder's cost, 2 points and
    # 14% for six months on that, and what RKB pays ROI Blocks at closing (rule of 2026-11-20).
    m = a["money"]
    assert m["funded"] == pytest.approx(83743.10 * 0.9, abs=0.01) and m["rkb_points"] == pytest.approx(1507.38, abs=0.01) and m["rkb_interest"] == pytest.approx(5275.82, abs=0.01)
    assert m["roi_at_closing"] == pytest.approx((120304.86 - 83743.10 - 1507.38 - 5275.82) / 4, abs=0.01) == pytest.approx(7444.64, abs=0.01)
    assert m["commission"]["at_sale"] == m["roi_at_closing"] and m["commission"]["margin"] == pytest.approx(29778.56, abs=0.02)
    assert r["exposure"] == pytest.approx(75368.79 + 1507.38 + 5275.82 + 7444.64, abs=0.02) and round(r["ratio"] * 100, 1) == 38.1
    assert r["passed"] and a["kind"] == "A" and a["lien_position"] == "first"
    lines = {l["key"]: l["amount"] for l in a["money"]["lines"]}
    assert lines["draws"] == pytest.approx(75368.79, abs=0.01) and lines["holdback"] == pytest.approx(8374.31, abs=0.01)
    assert lines["tax"] == pytest.approx(1205.0)          # Baltimore City, $5 per $500 of $120,304.86 = 241 units


def test_a_mortgage_that_stays_counts_and_the_same_65_applies_in_second_position():
    f = dict(E21, contract_price=0, has_mortgage=True, liens=[{"holder": "Senior Bank", "balance": 60000, "status": "current", "monthly_payment": 500}])
    a = E.assess(f)
    assert a["kind"] == "B" and a["lien_position"] == "second"
    assert a["rule_65"]["limit"] == 0.65
    assert a["rule_65"]["exposure"] == pytest.approx(60000 + a["money"]["at_risk"], abs=0.01)      # the mortgage that stays still counts
    over = dict(f, liens=[{"holder": "Senior Bank", "balance": 90000, "status": "current"}])
    b = E.assess(over)
    assert b["verdict"] == E.DOES_NOT_FIT and "65% rule" in b["reasons"][0] and "over by" in b["reasons"][0]
    assert b["rule_65"]["room"] < 0 and b["rule_65"]["exposure"] == pytest.approx(90000 + b["money"]["at_risk"], abs=0.01)


def test_the_note_and_its_terms_do_not_move_the_65_rule():
    base = E.assess(E21)["rule_65"]
    other_terms = E.assess(dict(E21, rate=14, points=3, term_months=9, interest_free_months=6, first_six_inside_note=True))["rule_65"]
    assert other_terms["exposure"] == base["exposure"]
    # a bigger note moves it only through ROI Blocks' quarter of the bigger margin
    bigger = E.assess(dict(E21, contract_price=130304.86))
    assert bigger["rule_65"]["exposure"] == pytest.approx(base["exposure"] + 10000 / 4, abs=0.02)
    # what RKB funds is what moves it
    dearer = E.assess(dict(E21, builder_cost=93743.10))["rule_65"]
    assert dearer["exposure"] > base["exposure"]


def test_roi_blocks_commission_a_quarter_at_closing_and_a_quarter_at_the_sale():
    c = E.assess(E21)["money"]["commission"]
    assert c["retail"] == 120304.86 and c["at_closing"] == c["at_sale"] == pytest.approx(7444.64, abs=0.01) and c["to_roi"] == pytest.approx(14889.28, abs=0.02)
    # money in the note that is not the renovation (a mortgage paid off, missed payments) is not margin
    liens = [{"holder": "SPS", "balance": 108023.17, "status": "current", "arrears": 5945.32, "monthly_payment": 1486.33},
             {"holder": "Aqua", "balance": 47699.26, "status": "current", "payoff_at_closing": True}]
    m = E.money(dict(E21, builder_cost=50483.40, contract_price=113625.66, has_mortgage=True, liens=liens))
    assert m["commission"]["retail"] == pytest.approx(59981.08, abs=0.01) and m["roi_at_closing"] == pytest.approx((59981.08 - 50483.40 - 908.70 - 3180.45) / 4, abs=0.01)
    # RKB's money in the deal is the four parts and nothing else; a payoff entered on the file is said beside the tests, not counted
    assert m["at_risk"] == pytest.approx(50483.40 * 0.9 * 1.09 + m["roi_at_closing"], abs=0.02)
    said = E.assess(dict(E21, arv=340000, builder_cost=50483.40, contract_price=113625.66, has_mortgage=True, scope="moderate", liens=liens))
    assert said["rule_65"]["exposure"] == pytest.approx(108023.17 + m["at_risk"], abs=0.02)
    assert any("is also paid at closing" in n and "$53,645" in n and "not counted" in n for n in said["notices"])
    # a note that leaves no margin pays ROI Blocks nothing, and says so
    thin = E.assess(dict(E21, contract_price=90000, contingency=0))
    assert thin["money"]["roi_at_closing"] == 0 and any("nothing is paid to ROI Blocks" in n for n in thin["notices"])
    # the amount is worked out, never taken from what somebody typed
    assert E.money(dict(E21, roi_at_closing=99999))["roi_at_closing"] == pytest.approx(7444.64, abs=0.01)
    assert "Amount paid to ROI Blocks at closing (0 if none)" not in E.details_missing({k: v for k, v in E21.items() if k != "roi_at_closing"})


# ------------------------------------------------------------------ kinds and stops
def test_owner_living_in_the_house_stops_the_deal():
    a = E.assess(dict(E21, owner_kind="individual", owner_lives_there=True))
    assert a["verdict"] == E.DOES_NOT_FIT and "still lives in the house" in a["reasons"][0]
    assert E.assess(dict(E21, owner_kind="individual", owner_lives_there=False))["verdict"] == E.FITS


def test_a_mortgage_in_default_must_be_paid_off():
    late = dict(E21, contract_price=0, liens=[{"holder": "Lakeview", "balance": 50000, "status": "foreclosure"}])
    a = E.assess(late)
    assert a["kind"] == "C" and a["verdict"] == E.DOES_NOT_FIT and "paid off at closing" in a["reasons"][0]
    paid = dict(E21, contract_price=0, as_is_value=80000, liens=[{"holder": "Lakeview", "balance": 20000, "status": "foreclosure", "payoff_at_closing": True}])
    b = E.assess(paid)
    assert b["kind"] == "C" and b["lien_position"] == "first" and b["verdict"] == E.FITS
    assert any(l["key"] == "payoff" and l["amount"] == 20000 for l in b["money"]["lines"])


def test_a_second_mortgage_rejects_the_deal_automatically():
    first = {"holder": "SPS", "balance": 60000, "status": "current", "monthly_payment": 500}
    second = {"holder": "Aqua Finance", "balance": 20000, "status": "current"}
    base = dict(E21, contract_price=0, builder_cost=40000, contingency=4000, as_is_value=200000, arv=340000, scope="moderate", has_mortgage=True)
    one = E.assess(dict(base, liens=[first]))
    assert one["verdict"] == E.FITS and one["lien_position"] == "second" and not one["rejected_second_mortgage"]
    two = E.assess(dict(base, liens=[first, second]))
    assert two["verdict"] == E.DOES_NOT_FIT and two["rejected_second_mortgage"] and two["headline"] == "Rejected: the house has a second mortgage."
    assert "second mortgage (SPS and Aqua Finance)" in two["reasons"][0] and "rejected" in two["reasons"][0]
    assert two["rule_65"]["passed"] and two["foreclosure"]["passed"]          # the numbers would pass: it is the rule that rejects it
    # paid off and released at closing: RKB stands behind one mortgage only
    paid = E.assess(dict(base, liens=[first, dict(second, payoff_at_closing=True)]))
    assert paid["verdict"] == E.FITS and any("must be paid off and released at closing" in n for n in paid["notices"])
    E.SECOND_MORTGAGE_OK_IF_PAID_OFF = False
    try:
        assert E.assess(dict(base, liens=[first, dict(second, payoff_at_closing=True)]))["verdict"] == E.DOES_NOT_FIT
    finally:
        E.SECOND_MORTGAGE_OK_IF_PAID_OFF = True


def test_a_state_rkb_has_not_lent_in_runs_on_the_worst_case_and_keeps_the_file_shut():
    f = dict(E21, state="PA", county=None)
    a = E.assess(f)
    assert a["new_state"] and a["verdict"] == E.FITS and a["foreclosure"]["worst_case"]
    assert a["foreclosure"]["finished"]["months"] == 14 and a["foreclosure"]["finished"]["foreclosure_cost"] == 12000
    assert any("has not lent in" in n for n in a["notices"]) and E.details_missing(f) == []
    g = C.gates(C.build(f, a), a, details_missing=[])
    assert g["step"] == 2 and [x["open"] for x in g["steps"]] == [True, True, False, False, False, False]


def test_a_late_mortgage_that_stays_is_rakesh_sirs_decision_and_the_note_must_cover_what_rkb_pays():
    late = {"holder": "SPS", "balance": 108023.17, "status": "late_more", "monthly_payment": 1486.33}
    base = dict(E21, owner_kind="individual", arv=340000, as_is_value=300000, builder_cost=50483.40, contingency=5048.34, scope="moderate", roi_at_closing=2374.42,
                has_mortgage=True, rate=14, interest_free_months=6, first_six_inside_note=True)
    no_cure = E.assess(dict(base, contract_price=70000, liens=[late]))
    assert no_cure["verdict"] == E.DOES_NOT_FIT and "brought up to date at closing" in no_cure["reasons"][0]
    cure = E.assess(dict(base, contract_price=70000, liens=[dict(late, arrears=5945.32)]))
    assert cure["verdict"] == E.EXCEPTION and "Rakesh Sir's decision" in cure["headline"] and "more than 30 days late" in cure["notices"][0]
    assert any(l["key"] == "arrears" and l["amount"] == 5945.32 for l in cure["money"]["lines"])
    assert any(i["key"] == "exception" for i in C.build(dict(base, contract_price=70000, liens=[dict(late, arrears=5945.32)]), cure))
    # a note smaller than what RKB pays out is refused, whatever the ratios say
    short = E.assess(dict(base, contract_price=59981.08, liens=[dict(late, arrears=5945.32)]))
    assert short["verdict"] == E.DOES_NOT_FIT and any("hold a note for only $59,981" in r for r in short["reasons"])
    foreclosing = E.assess(dict(base, contract_price=70000, liens=[dict(late, status="foreclosure", arrears=5945.32)]))
    assert foreclosing["verdict"] == E.DOES_NOT_FIT


def test_cash_to_the_owner_always_goes_to_rakesh_sir():
    a = E.assess(dict(E21, cash_to_owner=5000, contract_price=0))
    assert a["kind"] == "D" and a["verdict"] == E.EXCEPTION


def test_missing_essentials_is_incomplete_not_a_refusal():
    a = E.assess({"address": "x"})
    assert a["verdict"] == E.INCOMPLETE and len(a["missing"]) == 4


# ------------------------------------------------------------------ the foreclosure test
def test_foreclosure_test_finished_and_closing_day():
    fc = E.assess(E21)["foreclosure"]
    assert fc["finished"]["after_selling"] == pytest.approx(235000 * 0.85 * 0.92)
    assert fc["finished"]["foreclosure_cost"] == 7500 and fc["finished"]["months"] == 5
    assert fc["finished"]["passed"] and fc["passed"]
    assert fc["finished"]["rkb_out"] == pytest.approx(89596.63, abs=0.02)                   # RKB's money in the deal, not the note and not the full builder's cost


def test_the_foreclosure_test_has_one_case_at_arv_and_the_value_today_is_never_used():
    # "one without any work done then the RKB funded is nearly zero so leave that case; take only the case
    # when construction is complete or mid way, at ARV" (2026-11-20).
    no_value = {k: v for k, v in E21.items() if k != "as_is_value"}
    a = E.assess(no_value)
    assert "closing_day" not in a["foreclosure"] and a["foreclosure"]["finished"]["value"] == 235000
    assert a["verdict"] == E.FITS and E.details_missing(no_value) == []
    # a house worth almost nothing today does not move the answer, with or without a mortgage on it
    assert E.assess(dict(E21, as_is_value=1000))["verdict"] == E.FITS
    behind = dict(no_value, arv=330000, contract_price=150000, scope="moderate", builder_cost=60000, contingency=6000, has_mortgage=True,
                  liens=[{"holder": "Senior Bank", "balance": 60000, "status": "current", "monthly_payment": 500}])
    b = E.assess(behind)
    assert b["verdict"] == E.FITS and E.details_missing(behind) == [] and E.assess(dict(behind, as_is_value=1000))["verdict"] == E.FITS
    assert not any("Closing day" in n for n in b["notices"])


def test_senior_mortgage_grows_while_the_foreclosure_runs():
    f = dict(E21, contract_price=0, state="FL", county=None, liens=[{"holder": "Bank", "balance": 50000, "status": "current", "monthly_payment": 400}])
    fin = E.assess(f)["foreclosure"]["finished"]
    assert fin["months"] == 14 and fin["mortgage_growth"] == 400 * 14 and fin["mortgage_ahead"] == 50000


# ------------------------------------------------------------------ the budget test
def test_budget_test_flags_a_thin_budget_for_a_person_to_look_at():
    a = E.assess(E21)       # $83,743 over 1,825 sq ft is about $46 a foot, under a Maryland gut job's range
    assert not a["budget"]["passed"] and a["budget"]["per_sqft"] == pytest.approx(45.89, abs=0.01)
    assert a["verdict"] == E.FITS and a["person_checks"][0]["who"] == "manjunath"
    ok = E.assess(dict(E21, scope="moderate"))
    assert ok["budget"]["passed"] and not ok["person_checks"]


def test_budget_test_wants_ten_percent_contingency_and_a_measured_house():
    a = E.assess(dict(E21, scope="moderate", contingency=2000, measured=False))
    assert len(a["budget"]["looks"]) == 2


# ------------------------------------------------------------------ the states
def test_recording_tax_by_state():
    assert S.recording_tax("MD", "Baltimore County", 100000)["amount"] == 500.0
    assert S.recording_tax("MD", "Prince George's County", 100000)["amount"] == 550.0
    assert S.recording_tax("MD", "Somewhere", 100000)["sure"] is False
    assert S.recording_tax("VA", None, 100000)["amount"] == pytest.approx(333.33)
    assert S.recording_tax("DC", None, 100000)["amount"] == 1100.0
    assert S.recording_tax("FL", None, 100000)["amount"] == 550.0


def test_state_rate_and_point_limits():
    p = lambda **k: S.pricing_problems(**{**dict(state="MD", lien="first", owner_kind="individual", rate=18, points=2, term_months=12), **k})
    assert p() == []
    assert p(lien="second", rate=25)                                   # Maryland second lien: 24% at most
    assert p(state="DC") and not p(state="DC", owner_kind="llc")        # DC: one point on a person's home
    assert p(state="DC", owner_kind="llc", rate=25)                    # DC: 24% for everyone
    assert p(state="FL")                                               # 18% + 2 points over a year = 20%
    assert not p(state="FL", interest_free_months=6)                   # 9% + 2% = 11%
    assert p(state="VA", lien="second", points=6) and not p(state="VA", lien="second")
    assert "push the end payment back" in S.required_terms(state="MD", lien="second", owner_kind="individual")[0]
    assert E.assess(dict(E21, state="FL", county=None))["verdict"] == E.DOES_NOT_FIT


# ------------------------------------------------------------------ the lists
def _keys(facts):
    return {i["key"]: i for i in C.build(facts)}


def test_the_list_follows_the_owner_the_state_and_the_mortgage():
    llc = _keys(E21)
    assert "llc_authority" in llc and "guarantor" in llc and "estate_authority" not in llc and "payoff_permission" not in llc
    estate = _keys(dict(E21, owner_kind="estate", state="VA", county=None))
    assert "estate_authority" in estate and "heirs sign" in estate["estate_authority"]["why"] and "trustee" in estate
    second = _keys(dict(E21, owner_kind="individual", contract_price=0, liens=[{"holder": "Bank", "balance": 30000, "status": "current"}]))
    assert {"mortgage_statement", "payoff_permission", "request_for_notice", "owners_all"} <= set(second)
    fl = _keys(dict(E21, state="FL", county=None, interest_free_months=6))
    assert "notice_of_commencement" in fl and fl["recording"]["label"] == "The mortgage is recorded"
    c = _keys(dict(E21, contract_price=0, liens=[{"holder": "L", "balance": 20000, "status": "foreclosure", "payoff_at_closing": True}]))
    assert "payoff_letters" in c and "foreclosure_status" in c


def test_every_paper_names_the_lender_exactly():
    k = _keys(E21)
    assert any("RKB Consulting Group, Inc." in c for c in k["commitment"]["checks"])
    assert any("$0.00" in c for c in k["settlement"]["checks"])
    assert any("24 Oak Lane, Roslyn Heights, NY 11577" in c for c in k["insurance"]["checks"])
    assert any("7.2 and 7.3" in c for c in k["contract"]["checks"])


def test_compulsory_and_optional_lines():
    k = _keys(E21)
    assert {key for key, i in k.items() if not i["required"]} == set(C.OPTIONAL)
    for key in ("identity", "recording", "original_note", "commitment", "insurance", "llc_authority", "no_lien", "measurement", "contract"):
        assert k[key]["required"]
    for key in ("identity", "recording", "original_note", "commitment", "insurance", "llc_authority", "no_lien"):
        assert k[key]["foreclosure"]
    assert k["identity"]["verify"] == "rkb" and k["identity"]["needs_file"] is False


def test_page_one_says_exactly_what_is_still_missing():
    assert E.details_missing(E21) == []
    assert any("County" in m for m in E.details_missing(dict(E21, county="")))
    assert "Whether there is a mortgage on the house" in E.details_missing({k: v for k, v in E21.items() if k != "has_mortgage"})
    assert any("lender" in m for m in E.details_missing(dict(E21, has_mortgage=True)))
    assert len(E.details_missing({})) >= 15


# ------------------------------------------------------------------ the file, the people and the locks
def _file(facts=None):
    s = st.UwStore(FakeMongo())
    return s, st.create(s, facts or E21, WES)


def _finish(doc, when):
    for i in doc["items"]:
        if i["when"] == when and i["required"] and i["status"] != C.OK:
            i["values"] = {**{f["key"]: "x" for f in i["fields"]}, "_confirmed": True}     # what a person would have filled in
            i["status"] = C.OK
    return st.refresh(doc)


def _steps(doc):
    return [(x["done"], x["open"]) for x in doc["gates"]["steps"]]


def test_a_new_file_stands_on_step_three_and_later_steps_are_locked():
    s, doc = _file()
    assert doc["assessment"]["verdict"] == E.FITS and doc["gates"]["step"] == 3
    assert _steps(doc) == [(True, True), (True, True), (False, True), (False, True), (False, False), (False, False)]
    assert st.item_of(doc, "arv")["status"] == C.OK and s.get(doc["file_id"])["created_side"] == "contractor"
    half = st.create(s, {"address": "1 A St"}, WES)
    assert half["gates"]["step"] == 1 and [x["open"] for x in half["gates"]["steps"]] == [True, False, False, False, False, False]


def test_optional_lines_never_hold_the_file():
    s, doc = _file()
    _finish(doc, C.FILE)
    assert doc["gates"]["file_complete"] and {w["key"] for w in doc["gates"]["optional_open"]} == set(C.OPTIONAL)
    assert doc["gates"]["step"] == 4


def test_a_contractor_given_underwriting_works_as_rkb_does_except_the_tick_and_the_signature():
    # "show him the RKB confirm as well ... give him everything that we are giving to Rakesh sir in underwriting" (2026-11-20)
    assert st.CONTRACTOR_FULL_ACCESS is True and not st.is_contractor(WES)
    s, doc = _file()
    key = next(i["key"] for i in doc["items"] if i["status"] != C.OK and i["needs_file"] and not i["fields"] and i["key"] not in C.ONLY)
    st.confirm(doc, key, WES, "seen at the title company")                      # the route asks for RKB's password before this
    assert st.item_of(doc, key)["status"] == C.OK and st.item_of(doc, key)["confirmed"]["by"] == "wes"
    st.mark(doc, key, "rejected", WES, "wrong paper")                           # he can send a line back
    st.make_documents(doc, WES)
    st.edit_document(doc, doc["documents"][0]["key"], doc["documents"][0]["blocks"] + [{"t": "p", "text": "Added by Wes."}], WES)
    assert doc["documents"][0]["edited"]
    with pytest.raises(st.Refused):
        st.approve_document(doc, doc["documents"][0]["key"], WES)               # the tick is Rakesh Sir's alone
    with pytest.raises(st.Refused):
        st.sign(doc, WES)                                                       # so is the signature
    with pytest.raises(st.Refused):
        st.sign(doc, SHIV)                                                      # for every login, RKB's own included
    assert st.delete(doc, WES)["deleted"]["by"] == "wes"
    assert doc["created_side"] == "contractor"                                  # the history still says who he is


def test_roi_blocks_fills_and_uploads_but_never_confirms_approves_or_signs(monkeypatch):
    monkeypatch.setattr(st, "CONTRACTOR_FULL_ACCESS", False)                    # the limited view, kept behind one switch
    s, doc = _file()
    st.fill(doc, "owner_contact", {"mailing_address": "1 Main St", "phone": "555", "email": "a@b.c", "junk": "x"}, WES)
    assert st.item_of(doc, "owner_contact")["status"] == C.OK and "junk" not in st.item_of(doc, "owner_contact")["values"]
    st.add_file(doc, "commitment", {"sha256": "abc", "filename": "commitment.pdf"}, WES)
    assert st.item_of(doc, "commitment")["status"] == C.RECEIVED
    doc["documents"] = [{"key": "note", "title": "Promissory note", "version": 1, "blocks": []}]
    for call in (lambda: st.confirm(doc, "commitment", WES), lambda: st.mark(doc, "commitment", C.REJECTED, WES, "no"), lambda: st.fill(doc, "identity", {"confirmed_by": "me"}, WES),
                 lambda: st.fill(doc, "recording", {"reference": "1/2"}, WES), lambda: st.sign(doc, WES), lambda: st.approve_document(doc, "note", WES),
                 lambda: st.edit_document(doc, "note", [], WES)):
        with pytest.raises(st.Refused):
            call()


def test_rkb_confirms_any_line_with_or_without_a_paper_and_it_is_written_down():
    s, doc = _file()
    with pytest.raises(st.Refused):                       # a plain "mark ok" does not exist any more
        st.mark(doc, "commitment", C.OK, SHIV)
    st.confirm(doc, "commitment", SHIV, "Held in Rakesh Sir's email of 22 Oct")
    item = st.item_of(doc, "commitment")
    assert item["status"] == C.OK and item["confirmed"]["by"] == "shivansh" and item["confirmed"]["without_paper"] is True
    assert [w["key"] for w in doc["gates"]["confirmed_on_rkbs_word"]] == ["commitment"]
    assert any("Confirmed by RKB without a paper" in e["what"] for e in doc["events"])
    # over a reading that failed: allowed, and said
    st.add_file(doc, "insurance", {"sha256": "ins", "filename": "acord.pdf"}, WES)
    st.add_reading(doc, "insurance", "ins", reader.tidy({"right_paper": True, "checks": []}, st.item_of(doc, "insurance")["checks"]))
    assert st.item_of(doc, "insurance")["status"] == C.REJECTED
    st.confirm(doc, "insurance", SHIV)
    assert st.item_of(doc, "insurance")["confirmed"]["over_a_failed_reading"] and not st.item_of(doc, "insurance")["confirmed"]["without_paper"]
    # sending a line back takes the confirmation away
    st.mark(doc, "commitment", C.REJECTED, SHIV, "wrong lender name after all")
    assert "confirmed" not in st.item_of(doc, "commitment") and doc["gates"]["confirmed_on_rkbs_word"] == []


def test_identity_is_confirmed_by_rkb_and_no_number_is_kept():
    s, doc = _file()
    with pytest.raises(st.Refused):                       # who holds the papers, and since when, is the record itself
        st.confirm(doc, "identity", SHIV)
    st.fill(doc, "identity", {"confirmed_by": "Kayla, 123-45-6789", "confirmed_on": "2026-11-20", "people": "George Parker"}, SHIV)
    assert "123-45-6789" not in st.item_of(doc, "identity")["values"]["confirmed_by"]
    assert st.item_of(doc, "identity")["status"] == C.OPEN          # filling the fields is not the confirmation
    st.confirm(doc, "identity", SHIV)
    assert st.item_of(doc, "identity")["status"] == C.OK and st.item_of(doc, "identity")["confirmed"]["without_paper"] is False


def test_the_confirmation_password_is_checked_counted_and_never_kept():
    m = FakeMongo()
    assert secret.check(m, "anything", SHIV) == {"ok": False, "why": "RKB's confirmation password has not been set yet."}
    secret.set_password(m, "a-long-test-phrase", "test")
    stored = m.db["settings"].find_one({"_id": secret.KEY})["value"]
    assert "a-long-test-phrase" not in stored and secret.is_set(m)
    assert secret.check(m, "a-long-test-phrase", SHIV)["ok"]
    for _ in range(secret.MAX_TRIES):
        assert not secret.check(m, "wrong", SHIV)["ok"]
    assert "Too many" in secret.check(m, "a-long-test-phrase", SHIV)["why"]      # locked, even with the right one
    assert secret.check(m, "a-long-test-phrase", MANJU)["ok"]                    # another person is not locked
    with pytest.raises(ValueError):
        secret.set_password(m, "short", "test")


def test_the_reading_passes_a_line_or_sends_it_back_with_the_reason():
    s, doc = _file()
    st.add_file(doc, "commitment", {"sha256": "abc", "filename": "commitment.pdf"}, WES)
    checks = st.item_of(doc, "commitment")["checks"]
    bad = reader.tidy({"what_it_is": "Title commitment", "right_paper": True, "figures": [], "also_noticed": [],
                       "checks": [{"n": 1, "result": "not_met", "quote": "RKB Consulting LLC", "note": "Must read RKB Consulting Group, Inc."}]}, checks)
    assert not bad["passed"] and bad["checks"][1]["result"] == reader.NOT_SHOWN      # a check the model skipped never passes
    st.add_reading(doc, "commitment", "abc", bad)
    item = st.item_of(doc, "commitment")
    assert item["status"] == C.REJECTED and "RKB Consulting Group, Inc." in item["reason"]
    st.add_file(doc, "commitment", {"sha256": "def", "filename": "commitment-2.pdf"}, WES)
    good = reader.tidy({"right_paper": True, "checks": [{"n": i, "result": "met", "quote": "q", "note": ""} for i in range(1, len(checks) + 1)]}, checks)
    st.add_reading(doc, "commitment", "def", good)
    assert st.item_of(doc, "commitment")["status"] == C.OK and "confirmed" not in st.item_of(doc, "commitment")
    # a line RKB confirms is not passed by a reading alone
    st.add_file(doc, "licence", {"sha256": "lic", "filename": "mhic.pdf"}, WES)
    lic = st.item_of(doc, "licence")["checks"]
    st.add_reading(doc, "licence", "lic", reader.tidy({"right_paper": True, "checks": [{"n": i, "result": "met", "quote": "q", "note": ""} for i in range(1, len(lic) + 1)]}, lic))
    assert st.item_of(doc, "licence")["status"] == C.RECEIVED


def test_the_reader_never_returns_an_identity_number():
    out = reader.tidy({"what_it_is": "W-9 for 123-45-6789", "right_paper": True, "checks": [{"n": 1, "result": "met", "quote": "account 123456789012", "note": ""}],
                       "figures": [{"label": "TIN", "value": "123 45 6789"}]}, ["Tax number present"])
    assert "6789" not in str(out)


DOCS = [{"key": "commitment_letter", "title": "Conditional loan commitment", "version": 1, "blocks": [{"t": "p", "text": "x"}], "early": True},
        {"key": "note", "title": "Promissory note", "version": 1, "blocks": [{"t": "p", "text": "x"}]}]


def test_documents_are_ticked_by_rakesh_sir_and_an_edit_takes_the_tick_away():
    import copy
    s, doc = _file()
    doc["documents"] = copy.deepcopy(DOCS)
    with pytest.raises(st.Refused):
        st.approve_document(doc, "note", SHIV)
    with pytest.raises(st.Refused):                                    # the note is filled from the file, and the file is not complete
        st.approve_document(doc, "note", RAKESH)
    st.approve_document(doc, "commitment_letter", RAKESH)              # the commitment letter may go out early
    _finish(doc, C.FILE)
    st.approve_document(doc, "note", RAKESH)
    assert doc["gates"]["documents"] == {"total": 2, "approved": 2} and doc["gates"]["ready_to_sign"] and doc["gates"]["step"] == 5
    st.edit_document(doc, "note", [{"t": "p", "text": "corrected"}], SHIV)
    d = st.document_of(doc, "note")
    assert d["version"] == 2 and d["versions"][0]["blocks"] == [{"t": "p", "text": "x"}] and not d["approved"].get("at")
    assert not doc["gates"]["ready_to_sign"] and doc["gates"]["step"] == 4
    with pytest.raises(st.Refused) as e:
        st.sign(doc, RAKESH)
    assert "Every document must be approved first: 1 of 2" in str(e.value)


def test_review_and_sign_opens_only_when_every_step_before_it_is_complete():
    import copy
    s, doc = _file()
    doc["documents"] = copy.deepcopy(DOCS)
    with pytest.raises(st.Refused) as e:
        st.sign(doc, RAKESH)
    assert "The file is not complete" in str(e.value)
    _finish(doc, C.FILE)
    assert _steps(doc)[4] == (False, False)                              # the file is done, the documents are not: still locked
    for d in ("commitment_letter", "note"):
        st.approve_document(doc, d, RAKESH)
    assert _steps(doc)[4] == (False, True) and _steps(doc)[5] == (False, False)
    with pytest.raises(st.Refused):
        st.sign(doc, SHIV)
    st.sign(doc, RAKESH)
    assert doc["signed"]["by"] == "rakesh" and round(doc["signed"]["ratio"] * 100, 1) == 38.1 and doc["gates"]["step"] == 6
    assert not doc["gates"]["can_fund"]                                  # recording, no-work and the no-lien covenant still hold the money
    assert {w["key"] for w in doc["gates"]["waiting_closing"]} == {"no_work", "no_lien", "recording"}
    _finish(doc, C.CLOSING)
    assert doc["gates"]["can_fund"] and not doc["gates"]["foreclosure_file"]["complete"]     # the original note and the recorded copy are still to come back
    _finish(doc, C.AFTER)
    assert doc["gates"]["foreclosure_file"]["complete"] and all(x["done"] for x in doc["gates"]["steps"])


def test_a_change_to_the_facts_voids_the_ticks_and_the_signature():
    import copy
    s, doc = _file()
    doc["documents"] = copy.deepcopy(DOCS)
    _finish(doc, C.FILE)
    for d in ("commitment_letter", "note"):
        st.approve_document(doc, d, RAKESH)
    st.sign(doc, RAKESH)
    st.set_facts(doc, {"builder_cost": 99000}, WES)
    assert not doc["signed"].get("at") and "Builder's cost" in doc["signed"]["voided_because"]
    assert doc["gates"]["documents"]["approved"] == 0 and doc["gates"]["step"] == 4
    what = [e["what"] for e in doc["events"]]
    assert any("Builder's cost changed from 83,743.1 to 99,000" in w for w in what) and any("moved by more than 7%" in w for w in what)
    assert any("must be made and approved again" in w for w in what)


def test_the_value_and_the_square_footage_can_be_edited_and_the_arv_files_figure_is_kept():
    s = st.UwStore(FakeMongo())
    from_arv = {"arv": 240000, "sqft_above": 1500, "address": "308 E 21st St, Baltimore, MD 21218", "state": "MD"}
    doc = st.create(s, {k: v for k, v in E21.items() if k not in ("arv", "sqft_above")}, WES, from_arv=from_arv)
    assert doc["facts"]["arv"] == 240000 and doc["edited"] == []
    st.set_facts(doc, {"arv": 235000, "sqft_above": 1375}, SHIV)
    assert {e["field"]: (e["arv_file"], e["now"]) for e in doc["edited"]} == {"arv": (240000, 235000), "sqft_above": (1500, 1375)}
    assert any("Value after repair changed from 240,000 to 235,000" in e["what"] for e in doc["events"])


def test_the_budget_look_is_manju_sirs_and_an_exception_is_rakesh_sirs():
    s, doc = _file()
    assert st.item_of(doc, "budget_look")["party"] == C.RKB
    st.fill(doc, "budget_look", {"answer": "Yes: the roof and windows are sound"}, MANJU)
    with pytest.raises(st.Refused):
        st.confirm(doc, "budget_look", SHIV)
    st.confirm(doc, "budget_look", MANJU)
    d = st.create(s, dict(E21, cash_to_owner=5000, contract_price=0), WES)
    assert d["assessment"]["verdict"] == E.EXCEPTION and st.item_of(d, "exception")["hard"]
    st.fill(d, "exception", {"decision": "Approved at $5,000"}, RAKESH)
    with pytest.raises(st.Refused):
        st.confirm(d, "exception", MANJU)
    st.confirm(d, "exception", RAKESH)


def test_facts_from_an_arv_file():
    arv = {"analysis_id": "arv_1", "geocode": {"formatted": "3308 Oakfield Ave, Baltimore, MD 21207", "state": "MD", "county": "Baltimore", "county_fips": "24510"},
           "subject": {"sqft": 1701, "beds": 3, "baths": 2.0, "occupancy": "owner"}, "analysis": {"arv_opinion": 350000, "as_is_opinion": 200000},
           "valuation": {"arv": 340700}, "rehab": {"scope": "moderate"}}
    f = st.facts_from_arv(arv)
    assert f["county"] == "Baltimore City" and f["arv"] == 350000 and f["as_is_value"] == 200000 and f["sqft_above"] == 1701 and f["owner_lives_there"] is True


def test_only_rkb_deletes_a_file_and_nothing_is_erased(monkeypatch):
    monkeypatch.setattr(st, "CONTRACTOR_FULL_ACCESS", False)                    # in the limited view a contractor cannot delete
    s, doc = _file()
    s.save(doc)
    with pytest.raises(st.Refused):
        st.delete(doc, WES)
    assert [r["file_id"] for r in s.list()] == [doc["file_id"]]
    s.save(st.delete(doc, SHIV))
    assert s.list() == []                                                 # off the list
    kept = s.get(doc["file_id"])                                          # still in the database, with who and when
    assert kept["deleted"]["by"] == "shivansh" and kept["facts"]["address"] == E21["address"] and kept["items"] and kept["events"][-1]["what"] == "Deleted the file"
