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
    s, doc = _file(minimum_interest_months=6)        # interest from day one with a minimum, as RKB's letter of 19 Nov 2026 had it: written only when entered
    docs = D.build(doc)
    assert [d["key"] for d in docs] == ["commitment_letter", "note", "security", "guaranty", "resolution", "affidavit", "occupancy", "correction", "acknowledgment",
                                        "draw_agreement", "amendment", "assignment", "instructions", "schedule_2"]
    assert [d["key"] for d in docs if d["early"]] == ["commitment_letter"]
    assert [d["key"] for d in docs if d.get("audience") == D.NOT_BORROWER] == ["schedule_2"]                     # the draw budget is not part of the closing set
    by = _by(docs)
    note = _text(by["note"])
    assert "ONE HUNDRED TWENTY THOUSAND THREE HUNDRED FOUR AND 86/100 DOLLARS ($120,304.86)" in note
    assert "$10,827.44" in note and "$2,406.10" in note and "There is no interest-free period" in note          # the terms of the 19 Nov commitment letter
    sec = _text(by["security"])
    assert "assent to the passage of a decree" in sec and "Real Property Article \\u00a77-102" in sec and "AFFIDAVIT OF CONSIDERATION" not in sec   # struck by RKB's attorney, 24 Nov 2026
    assert "Brian T. Gallagher" in sec and "First Lien" in sec
    assert "3 BUSINESS DAYS" not in _text(by["amendment"]) and "rescission period" not in _text(by["amendment"])   # a business loan to a company carries no rescission language
    s1, person = _file(owner_kind="individual", owner_name="Thelma B. Chapman")
    assert "3 BUSINESS DAYS" in _text(_by(D.build(person))["amendment"])                                        # Maryland's lien notice on a person's own contract
    assert "RKB Consulting Group, Inc." in _text(by["instructions"]) and "$0.00 proceeds" in _text(by["instructions"])
    assert all("Marmon" in d["basis"] for d in docs)


def test_nothing_is_printed_blank_without_being_listed():
    s, doc = _file()
    by0 = _by(D.build(doc))
    note = by0["note"]
    # The closing date is the title company's to give: the blank is printed and listed, apart from what RKB's own file still owes.
    # The Note itself is dated in ink at signing (RKB's attorney, 24 Nov 2026): a line, not a blank for anybody to fill before.
    assert "closing date" in by0["draw_agreement"]["for_title"] and "closing date" not in by0["draw_agreement"]["missing"] and "[closing date]" in _text(by0["draw_agreement"])
    assert "[closing date]" not in _text(note) and "______________________, 20" in _text(note)
    assert "tax ID of the property" in note["missing"]
    full = dict(E21, closing_date="2026-12-01", tax_id="12-13-3813-060", borrower_address="510 Archer Street, Baltimore, MD 21230", signer_name="George Parker",
                guarantor_name="George Parker", contractor_name="Tutt Construction LLC", contractor_licence="12345", title_company="Smart Settlements LLC",
                legal_description="BEING KNOWN AS LOT 060", completion_date="2027-03-01", contract_date="2026-11-18", members="George Parker", deed_date="2026-08-14")
    s2, doc2 = _file(**full)
    st.fill(doc2, "guarantor", {"guarantor_name": "George Parker", "guarantor_address": "510 Archer Street, Baltimore, MD 21230"}, SHIV)
    docs = D.build(doc2)
    assert all(d["missing"] == [] and d["for_title"] == [] for d in docs), [(d["key"], d["missing"], d["for_title"]) for d in docs if d["missing"] or d["for_title"]]
    assert "March 1, 2027" in _text(_by(docs)["draw_agreement"]) and "2027-03-01" not in _text(_by(docs)["draw_agreement"])   # a date is printed as a paper prints it
    note2 = _text(_by(docs)["note"])
    import re
    # Maturity in words from the Note's signing, never a computed date (RKB's attorney, 24 Nov 2026)
    assert "December 1, 2026" in note2 and "November 30, 2027" not in note2 and "the day before the first one-year anniversary of the execution of this Note" in note2
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
    assert doc["gates"]["documents"] == {"total": 14, "approved": 0}
    st.approve_document(doc, "commitment_letter", RAKESH)
    assert doc["gates"]["documents"]["approved"] == 1
    st.set_facts(doc, {"contract_price": 121000}, WES)
    assert doc["gates"]["documents"]["approved"] == 0 and "$121,000.00" in _text(st.document_of(doc, "note"))
    assert st.document_of(doc, "note")["version"] == 2


def test_a_correction_by_hand_is_kept_until_it_is_taken_back(monkeypatch):
    monkeypatch.setattr(st, "CONTRACTOR_FULL_ACCESS", False)                    # this test also covers the limited contractor view
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
        if d.get("audience") == D.NOT_BORROWER:
            continue
        t = _text(d)
        assert not [h for h in hidden if h in t], d["key"]
        assert "Total funding undertaking" not in t and "limit 65%" not in t
        # nor the words: the homeowner knows the contract price and nothing of a builder's cost behind it (25 Nov 2026)
        low = t.lower().replace("builder's risk", "")
        assert not [w for w in ("builder's cost", "contractor's cost", "cost of the work", "cost of completed work", "cost of the completed work", "contingency", "draw budget", "markup", "margin ")
                    if w in low.replace("margin allocation", "")], d["key"]
    assert D.usd(m["principal"]) in _text(_by(docs)["commitment_letter"]) and "Contract price" in _text(_by(docs)["draw_agreement"])
    # The one paper that carries the contractor's cost is Schedule 2: marked, signed without the Borrower, and never listed for the title company.
    s2 = _text(_by(docs)["schedule_2"])
    assert "NOT A BORROWER DOCUMENT" in s2 and "$93,047.88, including contingency of $9,304.78" in s2 and "up to $83,743.09" in s2 and "10% = $9,304.79" in s2
    assert "BORROWER:" not in s2 and "Schedule 2" not in _text(_by(docs)["instructions"]).replace("SCHEDULE 2", "")


TUTT = dict(contractor_name="Duwayne Peirce Tutt, trading as Tutt Painting and Remodeling", contractor_licence="111574", licence_holder="sub", signer_name="George Parker",
            guarantor_name="George Parker", title_company="Smart Settlements LLC", tax_id="12-13-3813-060", borrower_address="510 Archer Street, Baltimore, MD 21230")


def test_what_was_corrected_by_hand_on_21st_st_is_written_by_the_generator():
    """The corrections of 23 and 24 Nov 2026 on 308 E 21st St, each of which the set now carries on its own."""
    s, doc = _file(**dict(TUTT, contract_date="2026-11-18", completion_date="2027-02-28"))
    by = _by(D.build(doc))
    sec = _text(by["security"])
    assert sec.index("Prepared by: Brian T. Gallagher, Esq.") < sec.index("[Space Above This Line For Recording Data]") < sec.index("DEED OF TRUST")
    assert "a resident of the State of Maryland" not in sec and "ATTORNEY CERTIFICATION" in sec and "This instrument was prepared by RKB" not in sec   # trustee by name only (24 Nov 2026)
    assert sec.count("I HEREBY CERTIFY that on this") == 2 and "under the power of sale or decree" in sec          # the instrument and its rider, in Maryland's full form
    assert "Lender may negotiate and settle the claim" in sec and "the Applicable Law requirement satisfies the requirement here" in sec
    assert "BEING the same property conveyed to 510 A Holdings LLC by deed dated [deed date]" in sec and "Liber [__], Folio [__]" in sec
    assert "[Legal description to be inserted by Smart Settlements LLC from the title commitment]" in sec
    am = _text(by["amendment"])
    assert "dated November 18, 2026 (the" in am and "Sections 7.2 and 7.3 of the Agreement are deleted in their entirety" in am and "including Section 10.10 of the Agreement" in am
    assert "Maryland Home Improvement Commission licence No. 111574" in am and "under all required Baltimore City permits" in am
    for key in ("amendment", "assignment", "draw_agreement", "commitment_letter"):
        t = _text(by[key])
        assert "Margin Allocation and Payoff Direction Agreement" in t and "separate agreement" not in t, key
    a = _text(by["assignment"])
    assert by["assignment"]["title"] == "Contractor lien covenant and subordination agreement" and "9-113" not in a and "subordinate to Lender's Deed of Trust" in a
    assert "CONTRACTOR: DUWAYNE PEIRCE TUTT, t/a Tutt Painting and Remodeling" in a and "Duwayne Peirce Tutt, individually" in a      # he signs as a person, not for a company
    assert "personally appeared Duwayne Peirce Tutt, known to me" in a and "ACKNOWLEDGED BY OWNER: 510 A HOLDINGS LLC" in a
    dr = _text(by["draw_agreement"])
    assert "MHIC licence No. 111574" in dr and "February 28, 2027" in dr and "and by Borrower" not in dr
    # the contractor's priced scope, signed by him alone, goes with Schedule 2; the paper the Borrower signs points to ROI Blocks' proposal
    assert "The Work is the work described in ROI Blocks' proposal under the Customer Agreement" in dr and "scope of work must be signed" not in dr
    assert "[The attached scope of work must be signed by Duwayne Peirce Tutt before closing.]" in _text(by["schedule_2"])
    ins = _text(by["instructions"])
    assert "Attorney Certification signed by Brian T. Gallagher, Esq." in ins and "Affidavit of Consideration" not in ins and "Esq.." not in ins
    assert "(a) interest accrues on the full $120,304.86 from the date the Deed of Trust is recorded, whether or not advanced: ______; and (b) George Parker personally guarantees the whole loan: ______." in ins
    assert "minimum interest" not in ins.lower() and "inimum interest" not in _text(by["note"])                       # no minimum interest unless a person entered one
    assert "re-verified on the Maryland Department of Labor site, no earlier than the day before closing, that MHIC licence No. 111574 is active" in ins
    cl = _text(by["commitment_letter"])
    assert "Duwayne Peirce Tutt, t/a Tutt Painting and Remodeling (MHIC No. 111574)" in cl and "As-renovated value used by Lender" in cl
    for d in by.values():
        assert "RKB CONSULTING GROUP, INC.: RKB CONSULTING GROUP, INC." not in _text(d), d["key"]                  # the lender's name was printed twice
    assert "any member, manager or affiliate of Borrower" in _text(by["affidavit"])
    assert "No person has lived at the Property since Borrower acquired it on [date of Borrower's deed]." in _text(by["occupancy"])
    g = _text(by["guaranty"])
    assert "Guarantor's address" in g and "8. Notices." in g
    # the licence's expiry date, once it is on the licence line, is a condition of the later draws
    st.fill(doc, "licence", {"expires_on": "2027-06-04"}, SHIV)
    assert "Contractor's licence expires June 4, 2027." in _text(_by(D.build(doc))["draw_agreement"])


def test_a_loan_with_no_points_charges_the_borrower_no_fee_anywhere():
    """21st St, 24 Nov 2026: RKB's 2 points are inside the Note (they come out of ROI Blocks' margin), so no paper
    charges the Borrower a loan fee and the title company is told to show none."""
    s, doc = _file(**dict(TUTT, points=0, deed_date="2026-05-11", deed_reference="Liber 29133, Folio 436"))
    by = _by(D.build(doc))
    for d in by.values():
        t = _text(d)
        assert "$2,406.10" not in t and "earned in full at closing" not in t and "the loan fee" not in t, d["key"]
    assert "No loan fee, origination fee or points are payable by Borrower under this Note." in _text(by["note"])
    assert "None. No loan fee, origination fee or points are payable by Borrower." in _text(by["commitment_letter"])
    ins = _text(by["instructions"])
    assert "is the Note principal of $120,304.86. Recordation tax is collected on that amount." in ins
    assert "No lender loan fee, origination fee or points are to appear on the settlement statement, and nothing is due from the Borrower at closing" in ins
    assert "by deed dated May 11, 2026 and recorded among the Land Records of Baltimore City, Maryland in Liber 29133, Folio 436." in _text(by["security"])
    s, doc = _file(**TUTT)                                                       # with points on the file the fee is stated as before
    assert "2 points = $2,406.10, earned in full at closing" in _text(_by(D.build(doc))["commitment_letter"])


def test_the_terms_as_they_were_finally_sent_for_21st_st():
    """Six months interest-free and then 18%, as the signed Marmon note; no fee to the owner; the signer's title as the title company has it."""
    s, doc = _file(**dict(TUTT, interest="free_6", minimum_interest_months=6, points=0, members="George Parker, III", signer_name="George Parker, III"))
    assert doc["facts"]["interest_free_months"] == 6 and doc["facts"]["minimum_interest_months"] == 0 and doc["assessment"]["rule_65"]["points"] == 0
    by = _by(D.build(doc))
    note = _text(by["note"])
    assert "No interest shall accrue on the principal sum for the first 180 days of this loan, beginning on the date of execution of this Note" in note
    assert "If the Property is sold and this Note is paid in full during the Interest-Free Period, no interest is due." in note
    assert "No loan fee, origination fee or points are payable by Borrower under this Note." in note
    for d in by.values():
        t = _text(d)
        assert "inimum interest" not in t and "There is no interest-free period" not in t and "earned in full at closing" not in t, d["key"]
    assert "(a) no interest accrues for the 180 days after execution of the Note, and after that interest accrues at 18% per year on the unpaid principal until it is paid: ______; and (b) George Parker personally guarantees the whole loan: ______." in _text(by["instructions"])
    sec = _text(by["security"])
    assert "George Parker, III, who acknowledged himself/herself to be the Sole Member of 510 A Holdings LLC" in sec and "Managing Member" not in sec
    s, doc = _file(**dict(TUTT, signer_title="Authorized Member", points=0))
    assert "Authorized Member" in _text(_by(D.build(doc))["security"])
    # the title company's papers are checked against the note, not against what RKB funds
    k = {i["key"]: i for i in doc["items"]}
    assert any("$120,304.86" in c and "not the smaller amount RKB funds" in c for c in k["commitment"]["checks"])
    assert any("The loan amount shown is the note amount, $120,304.86" == c for c in k["settlement"]["checks"])
    assert any("No lender loan fee" in c for c in k["settlement"]["checks"]) and any("Nothing is due from the borrower" in c for c in k["settlement"]["checks"])


def test_every_member_of_the_company_signs_the_resolution_before_a_notary():
    s, doc = _file(**TUTT)
    r = _by(D.build(doc))["resolution"]
    assert "member names" in r["missing"] and "The undersigned, [member names], being all of the members of" in _text(r)
    s, doc = _file(**dict(TUTT, members="George Parker; Ann Parker"))
    r = _by(D.build(doc))["resolution"]
    t = _text(r)
    assert r["missing"] == [] and "The undersigned, George Parker and Ann Parker, being all of the members of" in t
    assert t.count("as a member of 510 A Holdings LLC") == 2 and "Printed name: Ann Parker" in t
    s, doc = _file(**dict(TUTT, members="George Parker"))
    t = _text(_by(D.build(doc))["resolution"])
    assert "being the sole member of" in t and "SOLE MEMBER" in t and "all of the members" not in t


def test_the_commitment_says_what_came_in_and_what_is_still_needed():
    s, doc = _file(**dict(TUTT, contract_date="2026-11-18"))
    items = {i["key"]: i for i in doc["items"]}
    assert any("Signed by Duwayne Peirce Tutt personally" in c for c in items["scope"]["checks"]) and not any("owner" in c for c in items["scope"]["checks"])   # the contractor alone signs the scope
    assert not any("right to cancel" in c for c in items["contract"]["checks"])
    cert = {"sha256": "a" * 64, "filename": "Tutt COI.pdf", "uploaded_at": "2026-11-23T16:28:00+00:00"}
    st.add_file(doc, "liability", dict(cert), SHIV)
    reading = {"what_it_is": "certificate", "right_paper": True, "passed": False, "checks": [
        {"n": n, "check": c, "result": "met" if n in (1, 4) else "not_met", "quote": "", "note": "" if n in (1, 4) else ("RKB is the certificate holder only. Name it as additional insured." if n == 2 else "No property is named")}
        for n, c in enumerate(items["liability"]["checks"], start=1)]}
    st.add_reading(doc, "liability", "a" * 64, reading)
    st.add_file(doc, "budget", {"sha256": "b" * 64, "filename": "proposal.xlsx", "uploaded_at": "2026-11-23T16:28:00+00:00"}, SHIV)
    st.add_reading(doc, "budget", "b" * 64, {"what_it_is": "proposal", "right_paper": True, "passed": False, "checks": [
        {"n": 1, "check": items["budget"]["checks"][0], "result": "not_met", "quote": "", "note": "Contingency carries a 25% markup on the builder's cost of $9,304.78."}]})
    t = _text(_by(D.build(doc))["commitment_letter"])
    assert "Received November 23, 2026. Still needed: RKB is the certificate holder only. Name it as additional insured. No property is named." in t
    assert "Received: Customer Agreement signed by George Parker on November 18, 2026, contract price $120,304.86. Still needed: the Amendment to Customer Agreement" in t
    assert "markup" not in t and "$9,304.78" not in t and "Manju Sir" not in t and '"lead": "From RKB"}' not in t  # nothing of the cost, and no step of RKB's own, in a letter the Borrower signs
    assert "Workers' compensation" in t                                                                             # asked for before closing since 24 Nov 2026


def test_rkb_can_allow_a_line_after_closing_as_an_exception():
    s, doc = _file(**TUTT)
    st.make_documents(doc, WES)
    assert "measurement" in [w["key"] for w in doc["gates"]["waiting_file"]]
    with pytest.raises(st.Refused):
        st.defer(doc, "commitment", SHIV)                                       # the title commitment is what the lien stands on
    with pytest.raises(st.Refused):
        st.defer(doc, "insurance", SHIV)
    st.defer(doc, "measurement", SHIV, note="Rakesh Sir, 24 Nov")
    g = doc["gates"]
    assert "measurement" not in [w["key"] for w in g["waiting_file"]] and "measurement" in [w["key"] for w in g["waiting_after"]]
    assert [w["key"] for w in g["allowed_after_closing"]] == ["measurement"]
    t = _text(st.document_of(doc, "commitment_letter"))
    assert "3.7 Signed measurement." in t and "As an exception for this loan" in t and "ROI Blocks LLC shall deliver it to Lender after closing." in t
    assert "Signed measurement. Signed measurement with a sketch" not in t      # it is no longer among the conditions
    st.defer(doc, "measurement", SHIV, undo=True)
    assert "measurement" in [w["key"] for w in doc["gates"]["waiting_file"]] and "3.7" not in _text(st.document_of(doc, "commitment_letter"))


def test_every_document_becomes_a_word_file():
    s, doc = _file(state="FL", county="Hillsborough County", interest_free_months=6)
    for d in D.build(doc):
        blob = D.to_docx(d)
        assert blob[:2] == b"PK" and len(blob) > 8000, d["key"]


def test_the_loan_structure_rkbs_attorney_finished_on_21st_st():
    """Brian T. Gallagher's set of 24 Nov 2026: a loan the owner repays, not an assignment of ROI Blocks' contract price;
    interest-free days counted from the Note's signing; maturity in words; the instruments dated at signing."""
    s, doc = _file(**dict(TUTT, interest="free_6", points=0))
    by = _by(D.build(doc))
    for d in by.values():
        t = _text(d)
        for gone in ("has assigned", "assigns the whole", "in substitution", "Receivable", "Principal adjustment", "Principal Adjustment", "Assignor", "Assignee",
                     "AFFIDAVIT OF CONSIDERATION", "calendar months", "third-party beneficiary", "maturity date (", "a resident of the State of Maryland"):
            assert gone not in t, (d["key"], gone)
    note = _text(by["note"])
    assert "SIGN ORIGINAL ONLY" not in note                 # the Guaranty keeps its marking; RKB's attorney left it there
    assert "This Note evidences a loan made by Lender to Borrower, and Borrower is unconditionally obligated to repay the principal, interest and all other sums due under this Note" in note
    assert "(b) the day before the first one-year anniversary of the execution of this Note (the \\\"Maturity Date\\\")" in note
    sec = _text(by["security"])
    assert "to Brian T. Gallagher, Esq. (\\\"Trustee\\\")" in sec and "with no interest for the first 180 days following execution of the Note" in sec
    assert "No offset or claim which Borrower might have against Lender shall relieve" in sec and "(c) Borrower abandons the Property" in sec
    assert "No interest accrues for the first 180 days following execution of the Note." in sec and "ATTORNEY CERTIFICATION" in sec
    assert "made as of ______________________, 20" in sec and "Prepared by: Brian T. Gallagher, Esq." in sec
    am = _text(by["amendment"])
    assert "joins to consent. " in am or am.count("joins to consent.") == 1
    assert "The contract price shall be paid on Owner's behalf from the proceeds of Lender's loan to Owner" in am and "shall not demand payment of the contract price directly from Owner" in am
    lc = _text(by["assignment"])
    assert "CONTRACTOR LIEN COVENANT AND SUBORDINATION AGREEMENT" in lc and "to induce Lender to make its loan to Owner" in lc and "ACCEPTED: LENDER: RKB CONSULTING GROUP, INC." in lc
    assert "Contractor Lien Covenant and Subordination Agreement: ROI Blocks and Contractor" in _text(by["instructions"])
    assert "Lender agrees to advance the proceeds of its loan to Borrower to pay for the Work" in _text(by["draw_agreement"])
    assert "The contract price will be paid from the loan proceeds disbursed by Lender" in _text(by["affidavit"])
    assert "Owner is unconditionally obligated to repay that loan" in _text(by["acknowledgment"])
    cl = _text(by["commitment_letter"])
    assert "No interest for the first 180 days after execution of the Note" in cl and "will not demand payment of the contract price directly from Borrower" in cl
    # three months free is 90 days; interest from day one keeps its own sentence
    s, doc = _file(**dict(TUTT, interest="free_3", points=0))
    assert "first 90 days of this loan" in _text(_by(D.build(doc))["note"])
