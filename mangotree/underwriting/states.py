"""What each state changes.

Read from the state codes and Regulation Z on 2026-11-20 (see the research notes
kept with the project). Figures marked ``estimate`` are ours, not a statute: the
months a foreclosure takes and what it costs. They are settings, shown as
estimates on the screen, and are replaced by real figures as RKB's own
foreclosures give them (Euclid is the first).

Nothing here is a person's judgement. A rule either names its source or says it
is an estimate.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: Borrower kinds. An individual is a natural person signing for themselves.
INDIVIDUAL, ESTATE, TRUST, LLC = "individual", "estate", "trust", "llc"
OWNER_KINDS = (INDIVIDUAL, ESTATE, TRUST, LLC)
OWNER_LABEL = {INDIVIDUAL: "A person", ESTATE: "An estate", TRUST: "A trust", LLC: "A company (LLC)"}

FIRST, SECOND = "first", "second"

STATES: Dict[str, Dict[str, Any]] = {
    "MD": {
        "name": "Maryland",
        "trustee": {"name": "Brian T. Gallagher, Esq.", "address": "1906 Towne Center Blvd, Suite 265, Annapolis, MD 21401", "seen_on": "Marmon, Tahona, Durham"},
        "instrument": "Deed of Trust",
        "instrument_must_have": [
            "Power of sale and assent to the passing of a decree (Real Property 7-105)",
            "Obligatory advances and the maximum principal on the face (Real Property 7-102)",
            "Trustee is an individual; substitution of trustee permitted",
            "Printed names under signatures (Real Property 3-104)",
            "Affidavit of consideration signed by RKB",
        ],
        "foreclosure": {"route": "Sale under the court's supervision (order to docket, sale, ratification)",
                        "months": 5, "months_owner_occupied": 9, "cost": 7500, "estimate": True,
                        "steps": ["Notice of Intent to Foreclose, 45 days before filing", "No filing until 90 days after default",
                                  "Order to docket with the original note, the deed of trust, the debt affidavit and the military-service affidavit",
                                  "Sale advertised three weeks", "Court ratifies the sale"]},
        "licence_register": "Maryland Home Improvement Commission (MHIC) licence",
        "lender_licence": "A loan to a person for a personal purpose on a 1 to 4 unit dwelling needs a Maryland mortgage lender licence; there is no small-lender exemption, and an unlicensed lender may collect principal only (Financial Institutions 11-502, 11-523).",
        "contract_must_have": ["MHIC licence number on the contract", "On the first page, the notice that the contract creates a lien on the home, with the 3-business-day right to cancel, initialled (Business Regulation 8-501)"],
        "mechanics_lien": "A lien exists only once a court sets it; our deed of trust recorded before then is ahead.",
        "notes": ["A second lien on a home: 24% at most, no other points beyond the origination fee, and the borrower may push the end payment back once by six months (Commercial Law 12-404, 12-405)."],
    },
    "VA": {
        "name": "Virginia",
        "trustee": {"name": "Potomac Title Group Services", "address": "14950 Washington St, Suite 200, Haymarket, VA 20169", "seen_on": "the recorded deeds of trust of Tower Road and Narrow Gauge"},
        "instrument": "Deed of Trust",
        "instrument_must_have": [
            "Trustee is a Virginia resident or a company organised under Virginia or United States law (55.1-317)",
            "The short-form words: exemptions waived; subject to call upon default; renewal, extension or reinstatement permitted; substitution of trustee permitted; any trustee may act (55.1-325)",
            "THIS IS A CREDIT LINE DEED OF TRUST on the first page, with the maximum amount (55.1-318)",
            "RKB's address in the deed of trust, so notice of a senior sale reaches RKB (55.1-321)",
        ],
        "foreclosure": {"route": "Trustee's sale, no court", "months": 3, "months_owner_occupied": 4, "cost": 5000, "estimate": True,
                        "steps": ["Written notice of sale: 14 days, or 60 days when the owner lives there", "Newspaper advertisement", "Trustee's auction", "Accounting to the commissioner of accounts"]},
        "licence_register": "Virginia DPOR contractor licence (Class A, B or C)",
        "lender_licence": "No licence needed for up to three personal-purpose mortgage loans in any twelve months (6.2-1602). Count them.",
        "contract_must_have": ["Contractor's licence class and number"],
        "mechanics_lien": "Record before any work starts, and take the contractor's signed subordination (43-21).",
        "notes": ["A second lien on a home: loan fee of 5% at most, no other points (6.2-327)."],
    },
    "DC": {
        "name": "District of Columbia",
        "trustee": {"name": "Brian T. Gallagher, Esq.", "address": "1906 Towne Center Blvd, Suite 265, Annapolis, MD 21401", "seen_on": "Lane Pl"},
        "instrument": "Deed of Trust",
        "instrument_must_have": ["Power of sale (42-815)", "The maximum amount of future advances on the face"],
        "foreclosure": {"route": "Trustee's sale after a recorded mediation certificate, or a court case", "months": 12, "months_owner_occupied": 14, "cost": 10000, "estimate": True,
                        "steps": ["Notice of default with the mediation election form", "Mediation certificate recorded", "Notice of intention to foreclose, 30 days, to the owner and the Mayor", "Trustee's sale"]},
        "licence_register": "DC home improvement contractor licence (DLCP)",
        "lender_licence": "A loan to a person for a personal purpose on a dwelling needs a DC mortgage lender licence; there is no small-lender exemption (26-1102).",
        "contract_must_have": ["Home improvement contractor licence number: a contract with an unlicensed contractor who takes payment before finishing is void"],
        "mechanics_lien": "Record before any work starts, and search for a notice of lien before each draw (40-303.07).",
        "notes": ["24% at most, and only 1 point on a loan on the borrower's home (28-3301).",
                  "In 2024 the District made a renovation company release every deed of trust it held on home-improvement contracts. The lien is RKB's, never ROI Blocks'."],
    },
    "FL": {
        "name": "Florida",
        "instrument": "Mortgage",
        "instrument_must_have": [
            "The maximum principal for future advances, made within 20 years (697.04)",
            "The preparer's name and address on the instrument (695.26)",
            "The spouse's signature when the house is a homestead, even if the spouse is not on the deed",
            "Assignment of rents (697.07)",
        ],
        "foreclosure": {"route": "A court case", "months": 14, "months_owner_occupied": 18, "cost": 12000, "estimate": True,
                        "steps": ["Complaint and lis pendens, every junior lienholder joined", "Final judgment", "Clerk's sale", "Certificate of title"]},
        "licence_register": "Florida DBPR certified or registered contractor licence",
        "lender_licence": "A company lending to a person for a personal purpose on a dwelling needs a Florida mortgage lender licence (chapter 494). Calling a personal loan a business loan is itself a violation.",
        "contract_must_have": ["Contractor's licence number: an unlicensed contractor's contract cannot be enforced and carries no lien (489.128)"],
        "mechanics_lien": "The mortgage is recorded first, then the notice of commencement, and only then is money released (713.07, 713.13).",
        "notes": ["Interest and points together may not pass 18% a year on a loan up to $500,000 (687.03)."],
    },
}

#: Maryland recordation tax, dollars per $500 of the debt secured, fiscal 2026
#: (Department of Legislative Services, county and local tax rates).
MD_RECORDATION_PER_500: Dict[str, float] = {
    "baltimore city": 5.00, "baltimore county": 2.50, "anne arundel": 3.50, "howard": 2.50, "prince george's": 2.75,
    "frederick": 7.00, "charles": 7.00, "carroll": 6.50, "harford": 3.30, "montgomery": 4.45,
}
MD_RECORDATION_DEFAULT = 5.00

#: Extra lines of the file in a state whose sheet was written by Claude and approved by RKB
#: (see ``newstate``): state code -> list of lines. The four built-in states have theirs in ``checklist``.
EXTRA_LINES: Dict[str, List[Dict[str, Any]]] = {}

#: A state RKB has not lent in: until its own rules are written and approved, the
#: foreclosure test assumes the slowest and costliest foreclosure we know.
WORST_CASE: Dict[str, Any] = {"route": "Not set up yet: the slowest foreclosure we know is assumed", "months": 14, "months_owner_occupied": 18, "cost": 12000, "estimate": True}


def state_of(code: Optional[str]) -> Optional[Dict[str, Any]]:
    return STATES.get((code or "").strip().upper())


def _county_key(county: Optional[str]) -> str:
    c = (county or "").strip().lower().replace("’", "'")
    c = c.replace(" county", "") if c not in ("baltimore county",) else c
    return {"prince georges": "prince george's", "pg": "prince george's", "baltimore": "baltimore city"}.get(c, c)


def recording_tax(state: str, county: Optional[str], secured_amount: float) -> Dict[str, Any]:
    """Tax due when the security instrument is recorded, on the full amount secured."""
    s = (state or "").upper()
    amt = max(0.0, float(secured_amount or 0))
    if s == "MD":
        key = _county_key(county)
        rate = MD_RECORDATION_PER_500.get(key)
        known = rate is not None
        rate = rate if known else MD_RECORDATION_DEFAULT
        units = -(-amt // 500)   # each $500 or part of it
        return {"amount": round(units * rate, 2), "basis": "$%.2f per $500 (%s)" % (rate, (county or "county not given")),
                "sure": known, "note": None if known else "County not in our table: the highest common rate is used until the title company gives the figure."}
    if s == "VA":
        units = -(-amt // 100)
        return {"amount": round(units * 0.25 * (1 + 1 / 3.0), 2), "basis": "$0.25 per $100 plus the local third", "sure": True, "note": None}
    if s == "DC":
        return {"amount": round(amt * 0.011, 2), "basis": "1.1% of the debt", "sure": True, "note": "A home of up to five units may be exempt with the owner's affidavit; the title company confirms."}
    if s == "FL":
        units = -(-amt // 100)
        return {"amount": round(units * 0.35 + amt * 0.002, 2), "basis": "$0.35 per $100 plus 0.2% intangible tax", "sure": True, "note": None}
    custom = (STATES.get(s) or {}).get("recording_tax")
    if custom:
        return {"amount": round(amt * float(custom.get("percent_of_amount") or 0) / 100.0, 2), "basis": custom.get("basis") or "this state's recording tax", "sure": False,
                "note": "From this state's sheet, written by Claude and approved by RKB; the title company gives the exact figure."}
    return {"amount": 0.0, "basis": "state not set", "sure": False, "note": "No state: the recording tax is not counted yet."}


def pricing_problems(*, state: str, lien: str, owner_kind: str, rate: float, points: float, term_months: int,
                     interest_free_months: int = 0, principal: float = 0.0) -> List[str]:
    """Where the rate, points or end payment go past what that state allows for that
    lien position and that kind of borrower. An empty list means it fits."""
    s = (state or "").upper()
    out: List[str] = []
    person = owner_kind == INDIVIDUAL
    if s == "MD" and lien == SECOND and owner_kind != LLC:
        if rate > 24:
            out.append("Maryland allows 24%% at most on a second lien; this is %.2f%%." % rate)
        if points > 10:
            out.append("Maryland caps the origination fee on a second lien at 10% of the amount lent.")
    if s == "VA" and lien == SECOND and person and points > 5:
        out.append("Virginia caps the loan fee on a second lien at 5%%; this is %.2f points." % points)
    if s == "DC":
        if rate > 24:
            out.append("DC allows 24%% at most; this is %.2f%%." % rate)
        if person and points > 1:
            out.append("DC allows only 1 point on a loan to a person secured on their home; this is %.2f. A loan to a company for a business purpose is outside this limit." % points)
    if s == "FL" and principal <= 500000:
        years = max(term_months, 1) / 12.0
        charged_months = max(0, term_months - interest_free_months)
        yearly = (rate * charged_months / 12.0 + points) / years
        if yearly > 18.0001:
            out.append("Florida counts interest and points together and allows 18%% a year; this comes to %.2f%%." % yearly)
    return out


def required_terms(*, state: str, lien: str, owner_kind: str) -> List[str]:
    """Terms the loan papers must carry in that state for that lien position. These are
    not problems: they are what the document set writes in."""
    s = (state or "").upper()
    out: List[str] = []
    if s == "MD" and lien == SECOND and owner_kind != LLC:
        out.append("The borrower may push the end payment back once, by six months, with no new fee (Maryland second lien).")
        out.append("No interest taken in advance and no compounding (Maryland second lien).")
    if s == "FL":
        out.append("A usury savings clause, and no extension fee or default rate that would lift the yearly total past 18%.")
    if s == "VA":
        out.append("RKB's address on the face of the deed of trust.")
    return out
