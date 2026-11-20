"""The fixed list each person owes, and the locks.

A list is built from four answers: the kind of deal, the state, who owns the
house, and what mortgage sits on it. Nobody is asked for anything that is not on
their list.

Every line is **compulsory** or **optional**. A compulsory line holds the file:
nothing moves to the documents until every one of them is done. An optional line
is asked for because it helps (photos, comparable sales), and never holds anything.

A line is done in one of three ways: the paper is uploaded and the system's
reading passes every check; its fields are filled in; or a person at RKB confirms
it. RKB may confirm any line, with or without a paper, and every confirmation
asks for RKB's confirmation password and is written to the log with the person's
name, so Rakesh Sir sees at review exactly which lines rest on RKB's word.

``foreclosure`` marks what a foreclosure will ask for. On Euclid the borrower's
Social Security number and date of birth were never collected, and the filing
waited four months for the military-service search.

Identity numbers are never stored here. The attorney's office holds them; this
file records only who confirmed it and when.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import engine as E
from . import states as S

OWNER, ROI, CONTRACTOR, TITLE, INSURANCE, ATTORNEY, RKB = "owner", "roi", "contractor", "title", "insurance", "attorney", "rkb"
PARTY_LABEL = {OWNER: "Owner", ROI: "Wes · ROI Blocks", CONTRACTOR: "Contractor", TITLE: "Title company", INSURANCE: "Insurance agent",
               ATTORNEY: "RKB's attorney", RKB: "RKB"}
PARTY_ORDER = (OWNER, ROI, CONTRACTOR, TITLE, INSURANCE, ATTORNEY, RKB)

#: When an item is due. ``file`` gates clear to close; ``closing`` gates the first
#: dollar; ``after`` completes the foreclosure file once the papers come back.
FILE, CLOSING, AFTER = "file", "closing", "after"

OPEN, RECEIVED, OK, REJECTED = "open", "received", "ok", "rejected"
DONE = (OK,)

#: The short name a line carries in a list. The full sentence (``label``) says exactly what is asked and is shown when the line is opened.
NAMES = {
    "owner_contact": "Owner's contact details", "occupancy": "Occupancy statement and proof of address", "owners_all": "All owners and marital status",
    "estate_authority": "Estate papers", "trust_authority": "Trust papers", "llc_authority": "Company papers", "guarantor": "Personal guarantor",
    "mortgage_statement": "Mortgage statement", "payoff_permission": "Permission to ask the mortgage lender", "listing": "Listing agent",
    "contract": "Customer agreement", "budget": "Proposal and builder's cost", "measurement": "Signed measurement", "photos": "Photos", "comps": "Comparable sales",
    "schedule": "Completion date and draw schedule", "related": "Related-party statement", "no_work": "No work started",
    "licence": "Contractor's licence", "liability": "Liability insurance", "workers_comp": "Workers' compensation", "scope": "Signed scope of work", "w9": "Form W-9",
    "no_lien": "No-lien covenant", "commitment": "Title commitment", "searches": "Judgment and bankruptcy search", "taxes": "Tax and water certificate",
    "payoff_letters": "Payoff letters", "foreclosure_status": "Old lender's foreclosure status", "settlement": "Settlement statement",
    "cpl": "Closing protection letter and wire details", "endorsements": "Construction-loan endorsement", "recording": "Deed of trust recorded",
    "notice_of_commencement": "Notice of commencement", "request_for_notice": "Request for notice of sale", "original_note": "Original note to the custodian",
    "recorded_copy": "Recorded deed of trust and loan policy", "insurance": "Property insurance", "identity": "Identity papers with the attorney",
    "arv": "ARV file", "trustee": "Virginia trustee", "budget_look": "Manju Sir's answer on the budget", "exception": "Rakesh Sir's decision",
}

#: Asked for because they help; they never hold the file.
OPTIONAL = ("photos", "comps", "related", "listing", "workers_comp", "endorsements", "w9")

#: Items only a person at RKB may mark done. ROI Blocks compiles the file; RKB verifies it.
RKB_VERIFIES = ("identity", "recording", "licence", "trustee", "budget_look", "exception", "original_note", "request_for_notice",
                "notice_of_commencement", "cpl", "guarantor", "owners_all")
#: Who at RKB answers an item that is one named person's call.
ONLY = {"budget_look": "manjunath", "exception": "rakesh"}

LENDER_NAME = "RKB Consulting Group, Inc."
LENDER_CLAUSE = "RKB Consulting Group, Inc., its successors and/or assigns (ISAOA/ATIMA), 24 Oak Lane, Roslyn Heights, NY 11577"


def _item(key: str, party: str, label: str, why: str, *, when: str = FILE, file: bool = True, hard: bool = False, foreclosure: bool = False,
          checks: Optional[List[str]] = None, fields: Optional[List[Dict[str, str]]] = None, confirm: Optional[str] = None) -> Dict[str, Any]:
    # Who makes an item "ok": the system's reading of the uploaded paper; a person at
    # RKB (never the party who supplied it); or simply every field being filled.
    verify = "rkb" if key in RKB_VERIFIES else ("reading" if file else "filled")
    return {"key": key, "party": party, "label": label, "why": why, "when": when, "needs_file": file, "hard": hard, "foreclosure": foreclosure,
            "checks": checks or [], "fields": fields or [], "confirm": confirm, "verify": verify, "status": OPEN}


def _f(key: str, label: str, kind: str = "text") -> Dict[str, str]:
    return {"key": key, "label": label, "type": kind}


def build(facts: Dict[str, Any], assessment: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    a = assessment or E.assess(facts)
    state = (facts.get("state") or "").upper()
    st = S.state_of(state) or {}
    owner = facts.get("owner_kind") or S.INDIVIDUAL
    kind = a["kind"]
    liens = a["money"]["liens"]
    staying = [l for l in liens if not l["payoff_at_closing"]]
    paid_off = [l for l in liens if l["payoff_at_closing"]]
    instrument = st.get("instrument", "Deed of Trust")
    register = st.get("licence_register", "the state contractor licence")
    address = facts.get("address") or "the property"
    out: List[Dict[str, Any]] = []
    add = out.append

    # ------------------------------------------------------------- the owner
    add(_item("owner_contact", OWNER, "Where the owner can be reached", "Every notice in a default goes to this address, not to the empty house.", file=False, foreclosure=True,
              fields=[_f("mailing_address", "Current mailing address (not the property)"), _f("phone", "Phone"), _f("email", "Email")]))
    add(_item("occupancy", OWNER, "Nobody lives in the house: signed occupancy statement and proof of the owner's own address",
              "Our loan cannot be made on a home someone lives in. A signature alone is not proof.", hard=True,
              checks=["A document dated within 60 days shows the owner living at an address other than %s (a licence, a utility bill, a lease)" % address,
                      "The occupancy statement gives the date the owner moved out and says no one has a right to live in the house"]))
    if owner == S.INDIVIDUAL:
        add(_item("owners_all", OWNER, "Every owner on the deed, and marital status", "Everyone on title signs the %s, or it does not bind their share." % instrument, file=False, hard=True, foreclosure=True,
                  fields=[_f("owners", "Full legal name of every owner on the deed"), _f("marital_status", "Marital status of each"), _f("spouse", "Spouse's full name, if married")]))
    elif owner == S.ESTATE:
        note = {"MD": "In Maryland a personal representative may mortgage; the letters must be current and carry no restriction.",
                "VA": "In Virginia a personal representative has no power over the house unless the will gives it: otherwise the heirs sign.",
                "DC": "In DC, a court order or the title insurer's written agreement to insure the personal representative's signature.",
                "FL": "In Florida, the will must give the power to mortgage or the court must authorise it."}.get(state, "")
        add(_item("estate_authority", OWNER, "Estate: letters of administration, the will, and proof the estate is open",
                  "The person signing must have the power to put a lien on the house. " + note, hard=True, foreclosure=True,
                  checks=["Letters of administration name the person who will sign and are still in force", "The estate is open (not closed) on the date of the letters or a later court record",
                          "Nothing in the letters or the will limits the power to mortgage the house", "Every heir is named, or the will is on file"]))
    elif owner == S.TRUST:
        add(_item("trust_authority", OWNER, "Trust: the trust instrument and a certificate of trust",
                  "The trustee needs the power to encumber, not only to sell. On Old Brentford the first paper gave the power to sell only.", hard=True, foreclosure=True,
                  checks=["The trustee who will sign is the current trustee", "The trust gives the trustee power to mortgage or encumber real property, in those words or their equal",
                          "Whether the person who made the trust is living, and whether the trust can be revoked"]))
    elif owner == S.LLC:
        add(_item("llc_authority", OWNER, "Company: articles, operating agreement, certificate of good standing, signed resolution, W-9",
                  "The company must exist, be in good standing, and have authorised this loan.", hard=True, foreclosure=True,
                  checks=["Articles of organization and the operating agreement with every amendment", "Certificate of good standing dated within 30 days",
                          "A resolution signed by every member authorising the loan and naming who signs", "Nothing in the operating agreement limits the power to mortgage",
                          "Form W-9 in the company's name with its EIN"]))
        add(_item("guarantor", OWNER, "Personal guaranty: who guarantees the loan", "A company with one house has nothing else to collect from.", file=False, hard=True, foreclosure=True,
                  fields=[_f("guarantor_name", "Guarantor's full legal name"), _f("guarantor_address", "Home address")]))
    if liens:
        add(_item("mortgage_statement", OWNER, "Latest statement for every mortgage on the house", "The balance and whether it is paid up to date decide the kind of deal.", hard=True,
                  checks=["Statement dated within 30 days", "Unpaid principal balance", "Next payment due date and any amount past due", "Lender's name and loan number"]))
    if staying:
        add(_item("payoff_permission", OWNER, "Signed permission for RKB to ask the mortgage lender for the balance at any time",
                  "RKB sits behind this mortgage. If the owner stops paying it, RKB must know at once and be able to pay it.", foreclosure=True,
                  checks=["Signed by every borrower on the mortgage", "Names the lender and loan number", "Has no end date before the loan is repaid"]))
    add(_item("listing", OWNER, "Listing agent named, and the owner's agreement to list when the work is done", "The loan is repaid from the sale.",
              checks=["Listing agreement, or a signed letter naming the agent", "The owner agrees to list within 10 business days of completion and to keep it listed"]))

    # ------------------------------------------------------------- Wes · ROI Blocks
    contract_checks = ["The property address is exactly %s" % address, "The owner named is the owner on the deed",
                       "No clause gives ROI Blocks a lien or deed of trust, and none bars other financing (old sections 7.2 and 7.3)",
                       "The dispute clause names %s law and courts" % (st.get("name") or "the property's state"),
                       "The contract price is stated and matches the note amount"]
    contract_checks += list(st.get("contract_must_have") or [])
    add(_item("contract", ROI, "Signed customer agreement on the current template", "The note is given for the contract price; the contract must not give anyone else a lien.", hard=True, checks=contract_checks))
    add(_item("budget", ROI, "Proposal, and the builder's cost line by line", "Draws are measured against the builder's cost, never against the price to the owner.",
              checks=["Every line of the proposal has a cost; no line shows $0 for work the proposal says is included", "Contingency is on its own line with no markup",
                      "Permit fees are a line, or the sheet says who pays them", "The total equals the builder's cost entered in the quick check"]))
    add(_item("measurement", ROI, "Signed measurement with a sketch: square feet above grade and below grade, beds and baths",
              "The value, the insurance and the budget all rest on the area.", hard=True,
              checks=["Signed and dated by whoever measured", "Above-grade and below-grade areas given separately", "Bedrooms and bathrooms after the work"]))
    add(_item("photos", ROI, "Photos today: every room, basement, roof, mechanicals, outside", "The starting condition, before a dollar is spent.",
              checks=["Dated", "Kitchen, every bathroom, basement, roof, furnace or boiler, electrical panel, water heater, each side of the outside"]))
    add(_item("comps", ROI, "Closed comparable sales", "Our ARV stands on sold prices, not asking prices.",
              checks=["At least three closed sales", "Within about a mile and six months", "Renovated, of similar size"]))
    add(_item("schedule", ROI, "Completion date, draw schedule, and who pulls the permits", "A stalled job earns interest against a house worth less than when it started.", file=False,
              fields=[_f("completion_date", "Work complete by", "date"), _f("permits_by", "Permits pulled by"), _f("draws", "Number of draws")]))
    add(_item("related", ROI, "Related-party statement", "Who owns the contractor, and who is paid what from this deal.", file=False,
              fields=[_f("statement", "Any tie between ROI Blocks, the contractor, the agent and the owner; or 'none'")]))
    add(_item("no_work", ROI, "No work has started and no materials are on site", "Work before the %s is recorded can put a contractor's lien ahead of RKB. %s" % (instrument.lower(), st.get("mechanics_lien", "")),
              when=CLOSING, file=False, hard=True, confirm="ROI Blocks and the contractor each confirm, on the day of recording, that no work has been done and nothing delivered."))

    # ------------------------------------------------------------- the contractor
    add(_item("licence", CONTRACTOR, "Exact legal name and licence number: " + register, "An unlicensed contractor's contract may be void, and with it the price our note stands on.", hard=True,
              fields=[_f("legal_name", "Exact legal name"), _f("licence_number", "Licence number"), _f("checked_on_register", "Looked up on the state register on", "date"), _f("checked_by", "Looked up by")],
              checks=["Licence is active and not expired", "The name on the licence is the name on the contract"]))
    add(_item("liability", CONTRACTOR, "General liability certificate naming RKB", "An injury or damage on site must not land on the lender.",
              checks=["$1,000,000 each occurrence and $2,000,000 aggregate, or more", "%s is named as additional insured" % LENDER_NAME, "In force through the completion date"]))
    add(_item("workers_comp", CONTRACTOR, "Workers' compensation, or the state's written exemption", "Required by law where the contractor has employees.",
              checks=["Certificate in force, or the exemption document"]))
    add(_item("scope", CONTRACTOR, "Scope of work signed by the contractor (Schedule 1)", "The contractor agrees the money finishes the job.", hard=True,
              checks=["Signed by the contractor and by the owner", "Areas, bedrooms and bathrooms agree with the measurement"]))
    add(_item("w9", CONTRACTOR, "Form W-9", "RKB pays the contractor directly.", checks=["Name matches the licence", "Tax number present"]))
    add(_item("no_lien", CONTRACTOR, "No-lien and subordination covenant signed by ROI Blocks and the contractor",
              "Whatever they are owed stands behind RKB's lien. Signed before the %s is released for recording." % instrument.lower(), when=CLOSING, hard=True, foreclosure=True,
              checks=["Signed by ROI Blocks and by the contractor, notarized", "Says any lien or claim of theirs is behind RKB's %s, including later draws" % instrument.lower()]))

    # ------------------------------------------------------------- the title company
    position = "second" if staying else "first"
    title_checks = ["Proposed insured reads exactly: %s, its successors and/or assigns" % LENDER_NAME,
                    "Amount of insurance is at least the note amount", "Our lien is insured in %s position" % position,
                    "The owner shown is the person or company signing", "Every requirement in Schedule B-I is listed with who clears it"]
    if state == "MD":
        title_checks.append("Ground rent: none, or redeemed and insured as fee simple")
    add(_item("commitment", TITLE, "Title commitment", "It says who owns the house, what is against it, and that our lien can be insured where we expect.", hard=True, foreclosure=True, checks=title_checks))
    add(_item("searches", TITLE, "Judgment, tax-lien and bankruptcy search on every signer", "A bankruptcy or a judgment against the signer can sit ahead of us or stop the loan.", hard=True,
              checks=["Run on every person signing and on the company, if any", "Clear, or every item paid at closing", "No open bankruptcy"]))
    add(_item("taxes", TITLE, "Property tax, water and city-lien certificate", "Taxes and city charges come ahead of every lender.", hard=True,
              checks=["No tax sale and no tax-sale certificate outstanding", "Taxes, water and city charges paid, or paid on the settlement statement"]))
    if paid_off:
        add(_item("payoff_letters", TITLE, "Payoff letter for every mortgage being paid off, good through closing", "The old mortgage must be released, not just paid.", hard=True, foreclosure=True,
                  checks=["Good-through date on or after the closing date", "Names the loan and the amount to the day", "The settlement statement pays exactly this amount"]))
    if kind == E.KIND_C:
        add(_item("foreclosure_status", TITLE, "Written status of the old lender's foreclosure, with any sale date", "RKB must close before a sale date, with time to spare.", hard=True,
                  checks=["From the lender or its foreclosure attorney", "Says whether a sale is set, and when"]))
    add(_item("settlement", TITLE, "Settlement statement for RKB's approval", "No loan money goes to the owner, and every fee is one we agreed.", hard=True,
              checks=["Lender is %s" % LENDER_NAME, "Cash to the borrower is $0.00", "Recording tax and recording fees are shown", "Every payoff matches its payoff letter"]))
    add(_item("cpl", TITLE, "Closing protection letter and the agent's wire instructions", "The insurer stands behind the settlement agent who holds our money.",
              checks=["Closing protection letter addressed to %s" % LENDER_NAME, "Wire instructions on the agent's letterhead"],
              fields=[_f("wire_confirmed_by_phone", "Wire details confirmed by phone with", "text"), _f("wire_confirmed_on", "On", "date")]))
    add(_item("endorsements", TITLE, "Construction-loan endorsement agreed in writing", "Later draws must be insured in the same position as the first dollar.",
              checks=["The insurer's written agreement to issue the construction-loan (ALTA 32 family) endorsement, and what it asks of RKB"]))
    add(_item("recording", TITLE, "The %s is recorded" % instrument.lower(), "No money moves, and no work starts, until this is confirmed.", when=CLOSING, file=False, hard=True, foreclosure=True,
              fields=[_f("recorded_on", "Recorded on", "date"), _f("reference", "Book and page, or instrument number"), _f("confirmed_by", "Confirmed by (at the title company)")]))
    if state == "FL":
        add(_item("notice_of_commencement", TITLE, "Notice of commencement recorded after the mortgage", "In Florida the order is mortgage, then the notice, then money. Any other order can put contractors ahead of RKB.",
                  when=CLOSING, hard=True, foreclosure=True, fields=[_f("recorded_on", "Recorded on", "date"), _f("reference", "Official records book and page")]))
    if state == "MD" and staying:
        add(_item("request_for_notice", TITLE, "Request for notice of sale recorded", "If the first lender forecloses, RKB is told in time to act.", when=AFTER, file=False, foreclosure=True,
                  fields=[_f("recorded_on", "Recorded on", "date"), _f("reference", "Book and page")]))
    add(_item("original_note", TITLE, "Original signed note sent to RKB's custodian", "A foreclosure needs the original note. A copy is not enough.", when=AFTER, file=False, foreclosure=True,
              fields=[_f("sent_on", "Sent on", "date"), _f("tracking", "Courier tracking number"), _f("received_by", "Received by")]))
    add(_item("recorded_copy", TITLE, "Recorded %s and the final loan policy" % instrument.lower(), "The recorded instrument and the policy are the first two papers of any foreclosure.", when=AFTER, foreclosure=True,
              checks=["Recording stamp with book and page or instrument number", "Loan policy names %s, in %s position, for at least the note amount" % (LENDER_NAME, position)]))

    # ------------------------------------------------------------- insurance
    add(_item("insurance", INSURANCE, "Insurance declarations page and the mortgagee endorsement", "If the house burns, the cheque comes to RKB.", hard=True, foreclosure=True,
              checks=["Named insured is the owner on the deed", "Mortgagee and lender's loss payee, on the one policy, reads exactly: " + LENDER_CLAUSE,
                      "The policy allows the house to be vacant and under renovation", "Dwelling limit is at least RKB's note amount",
                      "The policy runs to the maturity date or later and the premium is paid", "A declarations page, not only a certificate (ACORD) or a binder"]))

    # ------------------------------------------------------------- the attorney
    who = "every signer" + (" and the guarantor" if owner == S.LLC else "")
    add(_item("identity", ATTORNEY, "Social Security number, date of birth and photo ID of %s are with RKB's attorney" % who,
              "A foreclosure cannot be filed without them (the military-service search). On Euclid they were never collected.", file=False, hard=True, foreclosure=True,
              confirm="Given directly to the attorney's office. Do not type or upload the numbers here.",
              fields=[_f("confirmed_by", "Confirmed by (name at the attorney's office)"), _f("confirmed_on", "On", "date"), _f("people", "For which people")]))

    # ------------------------------------------------------------- RKB
    add(_item("arv", RKB, "ARV file", "The value every test rests on.", file=False, fields=[_f("analysis_id", "ARV file")]))
    if state == "VA":
        t = st.get("trustee") or {}
        item = _item("trustee", RKB, "A Virginia trustee is named", "Virginia requires the trustee to be a Virginia resident or company. A trustee from another state can undo a sale. "
                     "Our recorded Virginia deeds of trust name %s." % (t.get("name") or "no one yet"), file=False, hard=True, foreclosure=True,
                     fields=[_f("trustee_name", "Trustee"), _f("trustee_address", "Virginia address")])
        item["values"] = {"trustee_name": t.get("name") or "", "trustee_address": t.get("address") or ""}   # offered, not confirmed: a person at RKB still marks it
        add(item)
    if a.get("person_checks"):
        add(_item("budget_look", RKB, "Manju Sir: can this budget finish the work?", " ".join(a["budget"]["looks"]), file=False,
                  fields=[_f("answer", "Yes or no, and why")]))
    if a["verdict"] == E.EXCEPTION:
        add(_item("exception", RKB, "Rakesh Sir's decision on the exception", a["headline"], file=False, hard=True, fields=[_f("decision", "Approved or not, and on what terms")]))
    # A state whose sheet Claude wrote and RKB approved: the papers that state adds.
    extra = {}
    for l in S.EXTRA_LINES.get(state) or []:
        item = _item(l["key"], l["party"], l["label"], l.get("why") or "", when=l["when"], file=bool(l.get("needs_file")), foreclosure=bool(l.get("foreclosure")), checks=l.get("checks") or [],
                     fields=[] if l.get("needs_file") else [_f("record", "What was done, by whom and when")])
        item["added_for_state"] = st.get("name") or state
        extra[l["key"]] = l
        add(item)
    for i in out:
        i["required"] = bool(extra[i["key"]].get("required", True)) if i["key"] in extra else i["key"] not in OPTIONAL
        if i["key"] in extra:
            i["name"] = extra[i["key"]]["name"]
            continue
        i["name"] = NAMES.get(i["key"], i["label"])
        if instrument == "Mortgage":
            i["name"] = i["name"].replace("Deed of trust recorded", "Mortgage recorded").replace("Recorded deed of trust", "Recorded mortgage")
    return out


def merge(old: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rebuild the list after the facts change, keeping everything people already did.
    An item that no longer applies is dropped; a new one starts open."""
    by_key = {i["key"]: i for i in old or []}
    out = []
    for item in new:
        prev = by_key.get(item["key"])
        if prev:
            for k in ("status", "values", "files", "reading", "reason", "history", "updated_at", "updated_by", "confirmed"):
                if k in prev:
                    item[k] = prev[k]
        out.append(item)
    return out


def set_status(item: Dict[str, Any], status: str, *, by: str, reason: Optional[str] = None) -> None:
    if status == REJECTED and not (reason or "").strip():
        raise ValueError("a reason is needed")
    now = datetime.now(timezone.utc)
    item.setdefault("history", []).append({"at": now, "by": by, "from": item.get("status"), "to": status, "reason": reason})
    item["status"] = status
    item["reason"] = reason if status == REJECTED else None
    if status != OK:
        item.pop("confirmed", None)
    item["updated_at"], item["updated_by"] = now, by


def _left(items: List[Dict[str, Any]], when: str) -> List[Dict[str, Any]]:
    return [i for i in items if i["when"] == when and i.get("required", True) and i.get("status") not in DONE]


STEPS = ("Property and deal", "Quick check", "The file", "Documents", "Review and sign", "Closing")


def gates(items: List[Dict[str, Any]], assessment: Dict[str, Any], *, details_missing: Optional[List[str]] = None,
          documents: Optional[Dict[str, int]] = None, signed: bool = False) -> Dict[str, Any]:
    """Where the file stands on the six steps, and exactly what holds each one.

    1 Property and deal: every basic fact entered.   2 Quick check: the rules pass.
    3 The file: every compulsory line done.           4 Documents: every one ticked by Rakesh Sir.
    5 Review and sign: signed.                        6 Closing: recorded, and the papers back.

    Review and sign opens only when steps 1 to 4 are complete; closing only after the signature."""
    def names(xs):
        return [{"key": i["key"], "party": i["party"], "name": i.get("name") or i["label"], "label": i["label"], "status": i.get("status")} for i in xs]
    docs = documents or {"total": 0, "approved": 0}
    file_left, closing_left, after_left = _left(items, FILE), _left(items, CLOSING), _left(items, AFTER)
    # A state RKB has not lent in holds the file shut until its rules are written and approved.
    verdict_ok = assessment.get("verdict") in (E.FITS, E.EXCEPTION) and not assessment.get("new_state")
    done = [not (details_missing or [])]
    done.append(done[0] and verdict_ok)
    done.append(done[1] and not file_left)
    done.append(done[2] and docs["total"] > 0 and docs["approved"] == docs["total"])
    done.append(done[3] and signed)
    done.append(done[4] and not closing_left and not after_left)
    step = next((n + 1 for n, d in enumerate(done) if not d), 6)
    opens = [True, done[0], done[1], done[1], done[3], done[4]]
    kit = [i for i in items if i.get("foreclosure")]
    kit_left = [i for i in kit if i.get("status") != OK]
    by_party: Dict[str, Dict[str, int]] = {}
    for i in items:
        if i["when"] != FILE:
            continue
        p = by_party.setdefault(i["party"], {"total": 0, "done": 0, "compulsory_left": 0})
        p["total"] += 1
        p["done"] += 1 if i.get("status") in DONE else 0
        p["compulsory_left"] += 1 if i.get("required", True) and i.get("status") not in DONE else 0
    counts = {s: sum(1 for i in items if i["when"] == FILE and i.get("status") == s) for s in (OPEN, RECEIVED, OK, REJECTED)}
    on_word = [i for i in items if (i.get("confirmed") or {}).get("without_paper")]
    return {"step": step, "steps": [{"n": n + 1, "name": STEPS[n], "done": done[n], "open": opens[n]} for n in range(6)],
            "verdict_ok": verdict_ok, "details_missing": details_missing or [], "file_complete": done[2], "documents": docs,
            "ready_to_sign": done[3], "signed": signed, "can_fund": done[4] and not closing_left,
            "waiting_file": names(file_left), "waiting_closing": names(closing_left), "waiting_after": names(after_left),
            "optional_open": names([i for i in items if not i.get("required", True) and i.get("status") not in DONE]),
            "counts": counts, "confirmed_on_rkbs_word": names(on_word),
            "foreclosure_file": {"total": len(kit), "held": len(kit) - len(kit_left), "missing": names(kit_left), "complete": not kit_left},
            "by_party": by_party}
