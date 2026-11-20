"""The loan documents written from an underwriting file: which documents a deal gets,
what each state changes in them, that nothing is printed blank without being listed,
and that a tick survives only while the text it was given to is unchanged."""
from __future__ import annotations

import json

import mongomock
import pytest

from mangotree.underwriting import checklist as C, documents as D, store as st


class FakeMongo:
    def __init__(self):
        self.db = mongomock.MongoClient().db


RAKESH = {"user_id": "rakesh", "name": "Rakesh Sir", "role": "ceo"}
SHIV = {"user_id": "shivansh", "name": "Shivansh", "role": "team"}
WES = {"user_id": "wes", "name": "Wes", "role": "contractor", "side": "contractor"}

E21 = {"address": "308 E 21st St, Baltimore, MD 21218", "state": "MD", "county": "Baltimore City", "arv": 235000, "as_is_value": 40000,
       "owner_kind": "llc", "owner_name": "510 A Holdings LLC", "sqft_above": 1375, "sqft_below": 450, "measured": True, "scope": "full_gut", "beds": 3, "baths": 2.5,
       "builder_cost": 83743.10, "contingency": 9304.78, "roi_at_closing": 6813.12, "contract_price": 120304.86, "rate": 18, "points": 2, "term_months": 12,
       "interest_free_months": 0, "arv_analysis_id": "arv_x", "closing_costs": 0, "has_mortgage": False, "owner_lives_there": False}


def _file(**facts):
    s = st.UwStore(FakeMongo())
    return s, st.create(s, dict(E21, **facts), WES)


def _text(d):
    return json.dumps(d["blocks"])


def _by(docs):
    return {d["key"]: d for d in docs}


def test_amounts_in_words_and_the_loan_number():
    assert D.words(133215.59) == "ONE HUNDRED THIRTY-THREE THOUSAND TWO HUNDRED FIFTEEN AND 59/100 DOLLARS"
    assert D.words(120304.86) == "ONE HUNDRED TWENTY THOUSAND THREE HUNDRED FOUR AND 86/100 DOLLARS"
    assert D.words(31070.39) == "THIRTY-ONE THOUSAND SEVENTY AND 39/100 DOLLARS" and D.words(1000000) == "ONE MILLION AND 00/100 DOLLARS"
    assert D.loan_number("308 E 21st St, Baltimore, MD 21218") == "308E21stSt-0001"


def test_the_maryland_company_set_is_the_thirteen_documents_of_the_21st_st_package():
    s, doc = _file()
    docs = D.build(doc)
    assert [d["key"] for d in docs] == ["commitment_letter", "note", "security", "guaranty", "resolution", "affidavit", "occupancy", "correction", "acknowledgment",
                                        "draw_agreement", "amendment", "assignment", "instructions"]
    assert [d["key"] for d in docs if d["early"]] == ["commitment_letter"]
    by = _by(docs)
    note = _text(by["note"])
    assert "ONE HUNDRED TWENTY THOUSAND THREE HUNDRED FOUR AND 86/100 DOLLARS ($120,304.86)" in note
    assert "$10,827.44" in note and "$2,406.10" in note and "There is no interest-free period" in note          # the terms of the 19 Nov commitment letter
    sec = _text(by["security"])
    assert "assent to the passage of a decree" in sec and "Real Property Article \\u00a77-102" in sec and "AFFIDAVIT OF CONSIDERATION" in sec
    assert "Brian T. Gallagher" in sec and "First Lien" in sec
    assert "3 BUSINESS DAYS" in _text(by["amendment"])                                                           # Maryland's lien notice on the contract
    assert "RKB Consulting Group, Inc." in _text(by["instructions"]) and "$0.00 proceeds" in _text(by["instructions"])
    assert all("Marmon" in d["basis"] for d in docs)


def test_nothing_is_printed_blank_without_being_listed():
    s, doc = _file()
    note = _by(D.build(doc))["note"]
    assert "closing date" in note["missing"] and "[closing date]" in _text(note)
    full = dict(E21, closing_date="2026-12-01", tax_id="12-13-3813-060", borrower_address="510 Archer Street, Baltimore, MD 21230", signer_name="George Parker",
                guarantor_name="George Parker", contractor_name="Tutt Construction LLC", contractor_licence="MHIC 12345", title_company="Smart Settlements LLC",
                legal_description="BEING KNOWN AS LOT 060", completion_date="2027-03-01")
    s2, doc2 = _file(**full)
    docs = D.build(doc2)
    assert all(d["missing"] == [] for d in docs), [(d["key"], d["missing"]) for d in docs if d["missing"]]
    note2 = _text(_by(docs)["note"])
    import re
    assert "December 1, 2026" in note2 and "November 30, 2027" in note2
    assert not [d["key"] for d in docs if re.search(r"\[[a-z][^\]]{3,80}\]", _text(d))]                          # no "[something missing]" left anywhere in the set
    assert "%s" not in note2 and "%d" not in note2


def test_values_entered_on_a_line_of_the_file_are_printed_in_the_documents():
    s, doc = _file()
    st.make_documents(doc, WES)
    assert "guarantor's full legal name" in st.document_of(doc, "guaranty")["missing"]
    st.fill(doc, "guarantor", {"guarantor_name": "George Parker", "guarantor_address": "510 Archer Street"}, SHIV)
    g = st.document_of(doc, "guaranty")
    assert "guarantor's full legal name" not in g["missing"] and "George Parker, individually" in _text(g) and g["version"] == 2


def test_what_each_state_changes_in_the_security_instrument():
    def sec(state, **f):
        s, doc = _file(state=state, county=None if state != "MD" else "Baltimore City", **f)
        return _by(D.build(doc))
    va = sec("VA")
    t = _text(va["security"])
    assert va["security"]["title"] == "Deed of Trust with loan terms rider" and "CREDIT LINE DEED OF TRUST" in t
    assert "Potomac Title Group Services" in t and "EXEMPTIONS WAIVED" in t and "55.1-321" in t and "assent to the passage" not in t
    assert "43-21" in _text(va["assignment"]) and "Not yet used at a closing" in va["security"]["basis"]
    dc = _text(sec("DC")["security"])
    assert "42-815" in dc and "mediation" in dc
    fl = sec("FL", interest_free_months=6)
    t = _text(fl["security"])
    assert fl["security"]["title"] == "Mortgage with loan terms rider" and "697.04" in t and "697.07" in t and "Trustee" not in t.replace("Trustee of", "")
    assert "This instrument was prepared by" in t and "homestead" in t.lower()
    assert "notice_of_commencement" in fl and "713.13" in _text(fl["notice_of_commencement"])
    assert "usury" in _text(fl["note"]).lower()
    assert "(2) the Notice of Commencement" in _text(fl["instructions"])


def test_the_owner_changes_which_documents_and_what_they_say():
    s, doc = _file(owner_kind="estate", owner_name="Danny Little, Personal Representative of the Estate of Wilbert L. Ruffin", interest_free_months=6)
    by = _by(D.build(doc))
    assert "guaranty" not in by and "resolution" not in by
    assert "solely in the capacity of Personal Representative" in _text(by["note"]) and "Interest-Free Period" in _text(by["note"])
    assert "letters of administration" in _text(by["affidavit"])
    s, doc = _file(owner_kind="trust", owner_name="Jacqueline Miller, Trustee of The Evan M. Duncan Revocable Trust")
    by = _by(D.build(doc))
    assert "trust_certificate" in by and "power to borrow money and to mortgage and encumber" in _text(by["trust_certificate"])
    s, doc = _file(owner_kind="individual", owner_name="Thelma B. Chapman")
    by = _by(D.build(doc))
    assert "guaranty" not in by and "trust_certificate" not in by and "Thelma B. Chapman" in _text(by["note"])


def test_a_second_lien_names_the_mortgage_ahead_and_adds_the_authorization():
    lien = {"holder": "SPS", "balance": 60000, "status": "current", "monthly_payment": 500}
    s, doc = _file(owner_kind="individual", owner_name="Clifton L. Hawkins, Jr.", has_mortgage=True, liens=[lien], contract_price=50000, builder_cost=40000,
                   contingency=4000, roi_at_closing=0, arv=340000, as_is_value=220000, scope="moderate", interest_free_months=6)
    by = _by(D.build(doc))
    sec = _text(by["security"])
    assert "Second Lien" in sec and "26. Senior Lien." in sec and "SPS (unpaid balance about $60,000.00)" in sec and "second mortgagee" in sec
    assert "senior_authorization" in by and "SPS" in _text(by["senior_authorization"])
    assert "postpone the Maturity Date one time for a period of six (6) months" in _text(by["note"])          # Maryland's rule for a second lien on a person's house
    assert "Second lien, behind SPS only" in _text(by["instructions"])


def test_the_commitment_letter_lists_exactly_the_compulsory_lines_still_open():
    s, doc = _file()
    letter = _text(_by(D.build(doc))["commitment_letter"])
    assert "Title commitment" in letter and "Identity papers with the attorney" in letter and "Photos" not in letter      # optional lines are not conditions
    for i in doc["items"]:
        if i["when"] == C.FILE and i["required"]:
            i["status"] = C.OK
    assert "Every condition of the file has been satisfied" in _text(_by(D.build(doc))["commitment_letter"])


def test_documents_are_written_once_the_quick_check_passes_and_follow_the_file():
    s = st.UwStore(FakeMongo())
    half = st.create(s, {"address": "1 A St"}, WES)
    with pytest.raises(st.Refused):
        st.make_documents(half, WES)
    s, doc = _file()
    st.make_documents(doc, WES)
    assert doc["gates"]["documents"] == {"total": 13, "approved": 0}
    st.approve_document(doc, "commitment_letter", RAKESH)
    assert doc["gates"]["documents"]["approved"] == 1
    st.set_facts(doc, {"contract_price": 121000}, WES)
    assert doc["gates"]["documents"]["approved"] == 0 and "$121,000.00" in _text(st.document_of(doc, "note"))
    assert st.document_of(doc, "note")["version"] == 2


def test_a_correction_by_hand_is_kept_until_it_is_taken_back():
    s, doc = _file()
    st.make_documents(doc, WES)
    blocks = list(st.document_of(doc, "occupancy")["blocks"])
    blocks[2] = {"t": "p", "text": "I, the undersigned, certify to Lender as follows, in my own words:"}
    st.edit_document(doc, "occupancy", blocks, SHIV)
    st.set_facts(doc, {"closing_date": "2026-12-01"}, WES)
    occ = st.document_of(doc, "occupancy")
    assert occ["edited"] and occ["blocks"][2]["text"].endswith("in my own words:") and occ["behind_the_file"]      # his wording stays; the page says the file moved on
    assert "December 1, 2026" in _text(st.document_of(doc, "note"))                                                # the others follow the file
    with pytest.raises(st.Refused):
        st.restore_document(doc, "occupancy", WES)
    st.restore_document(doc, "occupancy", SHIV)
    occ = st.document_of(doc, "occupancy")
    assert not occ.get("edited") and "December 1, 2026" in _text(occ) and len(occ["versions"]) == 2


def test_a_state_sheet_written_by_claude_works_like_a_built_in_state_once_approved():
    from mangotree.underwriting import engine as E, newstate, states as S
    raw = {"name": "Pennsylvania", "instrument": "Mortgage", "instrument_must_have": ["Maximum principal for future advances (42 Pa.C.S. 8143)"], "trustee_rule": "",
           "foreclosure": {"route": "A court case, after an Act 91 notice", "months": 10, "months_owner_occupied": 14, "cost": 9000, "steps": ["Complaint", "Judgment", "Sheriff's sale"]},
           "redemption": "None after the sheriff's deed", "licence_register": "Pennsylvania Home Improvement Contractor registration (HICPA)", "lender_licence": "x", "rate_limits": ["y"],
           "recording_tax": {"basis": "no mortgage tax; recording fee only", "percent_of_amount": 0}, "mechanics_lien": "z", "contract_must_have": ["HIC registration number"], "notes": [],
           "text": {"law": "the Commonwealth of Pennsylvania", "records": "the Office of the Recorder of Deeds of {county}, Pennsylvania", "notary": "COMMONWEALTH OF PENNSYLVANIA, COUNTY OF ______, ss:",
                    "courts": "the state or federal courts located in Pennsylvania", "lien_law": "Contractor waives its lien under the Mechanics' Lien Law of 1963.",
                    "remedy": [{"lead": "Foreclosure.", "text": "Lender may foreclose this Security Instrument by action in mortgage foreclosure under Pa.R.C.P. 1141 et seq."}],
                    "advances": "Under 42 Pa.C.S. 8143 this Security Instrument secures future advances up to MAXIMUM_PRINCIPAL."},
           "extra_lines": [{"name": "Stipulation against liens", "label": "A stipulation against liens filed with the prothonotary before work begins.", "why": "49 P.S. 1402.", "party": "contractor",
                            "when": "closing", "needs_file": True, "required": True, "foreclosure": False, "checks": ["Filed before any work", "Names the property"]},
                           {"name": "", "label": "dropped: no name"}],
           "sources": [], "unsure": ["foreclosure cost"]}
    sheet = newstate.tidy(raw)
    assert [l["key"] for l in sheet["extra_lines"]] == ["state_1"] and sheet["foreclosure"]["estimate"] is True

    class _Rows:                                                          # the person approving may drop an extra line
        def __init__(self): self.row = {"code": "ZZ", "status": "proposed", "sheet": newstate.tidy(raw)}
        def find_one(self, *a, **k): return dict(self.row, sheet=dict(self.row["sheet"]))
        def replace_one(self, q, row, **k): self.row = row

    class _Db:
        def __init__(self): self.db = {"uw_states": _Rows()}
    try:
        kept = newstate.approve("ZZ", _Db(), {"user_id": "rakesh", "name": "Rakesh Sir"}, drop=["state_1"])
        assert kept["status"] == "approved" and kept["sheet"]["extra_lines"] == [] and kept["dropped_lines"] == ["Stipulation against liens"] and S.EXTRA_LINES["ZZ"] == []
    finally:
        S.STATES.pop("ZZ", None); S.EXTRA_LINES.pop("ZZ", None); D.STATE_TEXT.pop("ZZ", None)
    facts = dict(E21, state="PA", county="Allegheny County")
    assert E.assess(facts)["new_state"]                                   # proposed only: nothing changes
    try:
        newstate.register("PA", sheet)
        a = E.assess(facts)
        assert not a["new_state"] and a["state_name"] == "Pennsylvania" and a["foreclosure"]["finished"]["months"] == 10 and a["foreclosure"]["finished"]["foreclosure_cost"] == 9000
        items = {i["key"]: i for i in C.build(facts, a)}
        assert items["state_1"]["name"] == "Stipulation against liens" and items["state_1"]["required"] and items["state_1"]["added_for_state"] == "Pennsylvania"
        assert items["recording"]["name"] == "Mortgage recorded"
        s, doc = _file(state="PA", county="Allegheny County")
        by = _by(D.build(doc))
        sec = _text(by["security"])
        assert "Pa.R.C.P. 1141" in sec and "secures future advances up to $120,304.86" in sec and "the Commonwealth of Pennsylvania" in sec and "assent to the passage" not in sec
        assert "Mechanics' Lien Law of 1963" in _text(by["assignment"]) and "state sheet Claude wrote" in by["security"]["basis"]
        newstate.register("MD", sheet)                                    # a built-in state is never overwritten
        assert S.STATES["MD"]["name"] == "Maryland"
    finally:
        S.STATES.pop("PA", None); S.EXTRA_LINES.pop("PA", None); D.STATE_TEXT.pop("PA", None)


def test_no_document_shows_the_builders_cost_or_what_rkb_funds():
    """The note is the homeowner's number. The builder's cost, the 90% RKB funds, the retainage, the
    contingency and ROI Blocks' share are between RKB and ROI Blocks (2026-11-20)."""
    s, doc = _file()
    docs = D.build(doc)
    m = doc["assessment"]["money"]
    hidden = [D.usd(x) for x in (m["work"], m["funded"], round(m["work"] * 0.1, 2), m["contingency"], m["roi_at_closing"], m["cash_out"], m["at_risk"]) if x]
    for d in docs:
        t = _text(d)
        assert not [h for h in hidden if h in t], d["key"]
        assert "Total funding undertaking" not in t and "limit 65%" not in t
    assert D.usd(m["principal"]) in _text(_by(docs)["commitment_letter"]) and "Contract price" in _text(_by(docs)["draw_agreement"])


def test_every_document_becomes_a_word_file():
    s, doc = _file(state="FL", county="Hillsborough County", interest_free_months=6)
    for d in D.build(doc):
        blob = D.to_docx(d)
        assert blob[:2] == b"PK" and len(blob) > 8000, d["key"]
