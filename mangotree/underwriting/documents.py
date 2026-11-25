"""The loan documents, written from the file.

Every document is a list of blocks (title, terms table, heading, paragraph,
list, signature, notary, note). The same blocks are shown on the screen as a
page, corrected there by Rakesh Sir, and written out as a Word file. Nothing in
a document is typed twice: names, amounts and dates come from the file, and a
value the file does not hold yet is printed as ``[what is missing]`` and listed
under ``missing`` so nobody signs a blank.

The model is RKB's own Maryland set: the signed lender package of 3704 Marmon
Avenue (30 Oct 2026) and the 308 E 21st St commitment letter (19 Nov 2026). The
structure is the same in every state: the owner gives RKB a note for the
contract price; ROI Blocks assigns that price to RKB and releases the owner;
RKB pays for the work in draws; at the sale the settlement agent pays RKB in
full, and what RKB owes ROI Blocks and the contractor is RKB's own matter.

What changes by state is written in ``_security`` and ``STATE_TEXT``: the
instrument (deed of trust, or a mortgage in Florida), the remedy clause, the
future-advance clause, the trustee, the recording step and the statutes named.
The wording for Virginia, the District of Columbia and Florida was written from
those states' statutes as read on 2026-11-20 and has not been used at a closing
yet; each of those documents says so in its ``basis``.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from . import checklist as C
from . import engine as E
from . import states as S

LENDER = "RKB Consulting Group, Inc."
LENDER_FULL = "RKB Consulting Group, Inc., a New York corporation, its successors and/or assigns"
LENDER_ADDR = "24 Oak Lane, Roslyn Heights, NY 11577"
LENDER_SIGNER, LENDER_TITLE = "Rakesh Bhargava", "President and Authorized Signatory"
ROI, ROI_ADDR, ROI_SIGNER, ROI_TITLE = "ROI Blocks LLC", "15701 Victorias Crest Pl, Haymarket, VA 20169", "Wes Stone", "Founder and CEO"
CUSTODIAN = "Margarita Gonzalez, KC Wilson & Associates, 2601 Main St, Ste 330, Irvine, CA 92614"

#: The agreement between RKB and ROI Blocks on ROI's share. It is signed outside the closing and is
#: never a borrower document; wherever a paper points to it, it is named in full (308 E 21st St, 23 Nov 2026).
ALLOC = "Margin Allocation and Payoff Direction Agreement"
#: ROI Blocks' Customer Agreement template, as read on the signed 21st St agreement (18 Nov 2026):
#: 7.2 bars other financing, 7.3 gives ROI Blocks a lien, 10.10 is the arbitration clause. The
#: Amendment in the loan set deletes them by number, and the file's contract line checks the numbers.
ROI_LIEN_SECTIONS = "7.2 and 7.3"
ROI_ARBITRATION_SECTION = "10.10"
#: Lines of the file whose review notes are never printed in a paper the Borrower reads: they speak
#: of the Contractor's cost, the markup or RKB's own steps.
NOT_FOR_BORROWER = ("budget", "budget_look", "exception", "comps", "arv")
#: ``audience`` of a document that is between RKB, ROI Blocks and the Contractor: never in the Borrower's
#: or the title company's set.
NOT_BORROWER = "not_borrower"
_PRIVATE = re.compile(r"builder'?s cost|contractor'?s cost|mark-?up|margin|draw budget|management fee", re.I)
_ENTITY = re.compile(r"\b(LLC|L\.L\.C\.|Inc\.?|Incorporated|Corp\.?|Corporation|Company|Co\.|Ltd\.?|LP|LLP|PLLC)\b", re.I)
_TRADING = re.compile(r",?\s+(?:trading as|t/a|d/b/a|dba|doing business as)\s+", re.I)

_ONES = "ZERO ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN ELEVEN TWELVE THIRTEEN FOURTEEN FIFTEEN SIXTEEN SEVENTEEN EIGHTEEN NINETEEN".split()
_TENS = "ZERO TEN TWENTY THIRTY FORTY FIFTY SIXTY SEVENTY EIGHTY NINETY".split()


def _under_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " HUNDRED")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n > 0:
        out.append(_ONES[n])
    return " ".join(out)


def words(amount: float) -> str:
    """133215.59 -> ONE HUNDRED THIRTY-THREE THOUSAND TWO HUNDRED FIFTEEN AND 59/100 DOLLARS."""
    cents = int(round(amount * 100))
    whole, c = divmod(cents, 100)
    parts = []
    for size, name in ((1_000_000, "MILLION"), (1_000, "THOUSAND"), (1, "")):
        chunk, whole = divmod(whole, size)
        if chunk:
            parts.append(_under_1000(chunk) + (" " + name if name else ""))
    return "%s AND %02d/100 DOLLARS" % (" ".join(parts) or "ZERO", c)


def usd(v: Any) -> str:
    return "${:,.2f}".format(float(v or 0))


def _date(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def long_date(d: Optional[date]) -> str:
    return "%s %d, %d" % (d.strftime("%B"), d.day, d.year) if d else ""


def today() -> date:
    """The date a letter carries: the calendar day in New York, where RKB is, not the server's."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:       # no time-zone data on this machine
        return date.today()


def loan_number(address: str) -> str:
    first = (address or "").split(",")[0]
    slug = "".join(w[:1].upper() + w[1:] for w in re.sub(r"[^A-Za-z0-9 ]", "", first).split())
    return (slug or "Loan") + "-0001"


# ------------------------------------------------------------------ blocks
def Title(text: str, sub: Optional[str] = None) -> Dict[str, Any]:
    return {"t": "title", "text": text, "sub": sub}


def Terms(rows: List[List[str]]) -> Dict[str, Any]:
    return {"t": "terms", "rows": [[a, b] for a, b in rows if b not in (None, "")]}


def H(text: str) -> Dict[str, Any]:
    return {"t": "h", "text": text}


def P(text: str, lead: Optional[str] = None) -> Dict[str, Any]:
    return {"t": "p", "text": text, "lead": lead}


def L(items: List[str]) -> Dict[str, Any]:
    return {"t": "list", "items": [i for i in items if i]}


def Sign(party: str, lines: List[str]) -> Dict[str, Any]:
    return {"t": "sign", "party": party, "lines": lines}


def Notary(text: str) -> Dict[str, Any]:
    return {"t": "notary", "text": text}


def Note(text: str) -> Dict[str, Any]:
    return {"t": "note", "text": text}


BREAK = {"t": "break"}


# ------------------------------------------------------------------ what each state changes in the papers
STATE_TEXT: Dict[str, Dict[str, Any]] = {
    # lic_body / lic_short / lic_site: how the contractor's licence is named in a sentence, in a short
    # reference, and where it is looked up again the day before closing. book: how a recorded deed is cited.
    "MD": {"law": "the State of Maryland", "records": "the Land Records of {county}, Maryland", "notary": "STATE OF MARYLAND, CITY/COUNTY OF ____________________, TO WIT:",
           "lien_law": "To the fullest extent permitted by applicable law, any lien claim of Contractor shall be subordinate to Assignee's Deed of Trust.",
           "courts": "the state or federal courts located in Maryland",
           "lic_body": "the Maryland Home Improvement Commission", "lic_short": "MHIC", "lic_site": "the Maryland Department of Labor site", "book": "in Liber [__], Folio [__]"},
    "VA": {"law": "the Commonwealth of Virginia", "records": "the land records of {county}, Virginia", "notary": "COMMONWEALTH OF VIRGINIA, CITY/COUNTY OF ____________________, to-wit:",
           "lien_law": "Under §43-21 of the Code of Virginia, Contractor subordinates every lien it has or may acquire on the Property, including any mechanic's lien under Title 43, to the lien of Lender's Deed of Trust and to every advance made under it.",
           "courts": "the state or federal courts located in Virginia",
           "lic_body": "the Virginia Board for Contractors", "lic_short": "Virginia DPOR", "lic_site": "the Virginia DPOR licence lookup", "book": "in Deed Book [__], Page [__], or as Instrument No. [__]"},
    "DC": {"law": "the District of Columbia", "records": "the Recorder of Deeds of the District of Columbia", "notary": "DISTRICT OF COLUMBIA, ss:",
           "lien_law": "Contractor subordinates every lien it has or may acquire on the Property under D.C. Code §40-301.01 et seq. to the lien of Lender's Deed of Trust and to every advance made under it, and shall record no notice of mechanic's lien without thirty (30) days' prior written notice to Lender.",
           "courts": "the courts of the District of Columbia",
           "lic_body": "the District of Columbia Department of Licensing and Consumer Protection", "lic_short": "DC DLCP", "lic_site": "the DLCP licence lookup", "book": "as Instrument No. [__]"},
    "FL": {"law": "the State of Florida", "records": "the Official Records of {county}, Florida", "notary": "STATE OF FLORIDA, COUNTY OF ____________________",
           "lien_law": "Contractor acknowledges that Lender's Mortgage is recorded before the notice of commencement and that, under §713.07, Florida Statutes, every lien under Chapter 713 is inferior to it. Contractor shall deliver a waiver and release of lien in the statutory form with each payment.",
           "courts": "the state or federal courts located in Florida",
           "lic_body": "the Florida Department of Business and Professional Regulation", "lic_short": "Florida DBPR", "lic_site": "the Florida DBPR licence lookup", "book": "in Official Records Book [__], Page [__]"},
}

_ACK_TAIL = "\n\n______________________________\nNotary Public\nPrinted name: ______________________\nMy commission expires: ______________          [NOTARIAL SEAL]"


class Ctx:
    """Everything the documents print, taken from the file. A value that is not there
    yet comes back as [label] and is remembered in ``missing``."""

    def __init__(self, doc: Dict[str, Any]):
        self.doc = doc
        self.f = doc.get("facts") or {}
        self.a = doc.get("assessment") or E.assess(self.f)
        self.items = {i["key"]: i for i in doc.get("items") or []}
        self.missing: List[str] = []
        #: Blanks the title company fills at the closing table (the legal description, the deed's
        #: date). They are printed in brackets like any blank, but they do not hold Rakesh Sir's tick.
        self.for_title: List[str] = []
        f, a = self.f, self.a
        self.state = (f.get("state") or "").upper()
        self.st = S.state_of(self.state) or {}
        self.sx = STATE_TEXT.get(self.state, STATE_TEXT["MD"])
        self.state_name = self.st.get("name") or self.state
        self.instrument = self.st.get("instrument", "Deed of Trust")
        self.is_mortgage = self.instrument == "Mortgage"
        self.owner_kind = f.get("owner_kind") or S.INDIVIDUAL
        self.kind = a["kind"]
        self.second = a["lien_position"] == S.SECOND
        self.lien_word = "second" if self.second else "first"
        m, r = a["money"], a["rule_65"]
        self.principal = m["principal"]
        self.money, self.r = m, r
        self.rate, self.points, self.term, self.free = r["rate"], r["points"], r["term_months"], r["interest_free_months"]
        self.fee = round(self.principal * self.points / 100.0, 2)
        # A minimum-interest clause is written only when a person entered one, and never beside an
        # interest-free period (the two contradict each other). It is not assumed (21st St, 25 Nov 2026).
        self.min_months = 0 if self.free else int(E._n(f.get("minimum_interest_months")))
        self.min_interest = round(self.principal * self.rate / 100.0 * self.min_months / 12.0, 2)
        self.closing = _date(f.get("closing_date"))
        self.maturity = (self.closing.replace(year=self.closing.year + 1) - timedelta(days=1)) if self.closing else None
        self.free_until = None
        self.address = f.get("address") or self.need("property address")
        self.county = f.get("county") or ""
        self.loan_no = f.get("loan_number") or loan_number(f.get("address") or "")
        self.borrower = f.get("owner_name") or self.need("owner's name as on the deed")
        self.liens_staying = [l for l in m["liens"] if not l["payoff_at_closing"]]
        self.liens_paid = [l for l in m["liens"] if l["payoff_at_closing"]]

    # -- values
    def need(self, label: str) -> str:
        if label not in self.missing:
            self.missing.append(label)
        return "[%s]" % label

    def title_fill(self, label: str) -> str:
        if label not in self.for_title:
            self.for_title.append(label)
        return "[%s]" % label

    def raw(self, key: str, item: Optional[str] = None, field: Optional[str] = None) -> str:
        """A value from page one or, failing that, from a line of the file. Empty when neither holds it."""
        v = self.f.get(key)
        if v in (None, "") and item:
            v = ((self.items.get(item) or {}).get("values") or {}).get(field or key)
        return str(v).strip() if v not in (None, "") else ""

    def val(self, key: str, label: str, item: Optional[str] = None, field: Optional[str] = None) -> str:
        return self.raw(key, item, field) or self.need(label)

    def day(self, key: str, item: Optional[str] = None, field: Optional[str] = None) -> str:
        """A date as a paper prints it (February 28, 2027). Empty when it is not known."""
        return long_date(_date(self.raw(key, item, field)))

    # -- the contractor: a company, or a person under his or her own licence
    @property
    def contractor_person(self) -> Optional[str]:
        """The person's name when the licence is an individual's ("Duwayne Peirce Tutt, trading as
        Tutt Painting and Remodeling"): he or she signs individually, never for a company."""
        parts = _TRADING.split(self.raw("contractor_name", "licence", "legal_name"), maxsplit=1)
        return parts[0].strip() if len(parts) == 2 and parts[0].strip() and not _ENTITY.search(parts[0]) else None

    @property
    def contractor_ta(self) -> str:
        """The short form used above a signature line: Duwayne Peirce Tutt, t/a Tutt Painting and Remodeling."""
        if not self.contractor_person:
            return self.contractor
        return "%s, t/a %s" % (self.contractor_person, _TRADING.split(self.contractor, maxsplit=1)[1].strip())

    @property
    def lic_short(self) -> str:
        return self.sx.get("lic_short") or ""

    @property
    def lic_no(self) -> str:
        """MHIC licence No. 111574"""
        return ("%s licence No. %s" % (self.lic_short, self.licence)).strip()

    @property
    def contractor_cite(self) -> str:
        """Duwayne Peirce Tutt, t/a Tutt Painting and Remodeling (MHIC No. 111574)"""
        return "%s (%s)" % (self.contractor_ta, ("%s No. %s" % (self.lic_short, self.licence)).strip())

    def contractor_sign(self, cite: bool = False) -> Dict[str, Any]:
        if self.contractor_person:
            head = self.contractor_person.upper() + self.contractor_ta[len(self.contractor_person):]
            if cite:
                head += " (%s)" % ("%s No. %s" % (self.lic_short, self.licence)).strip()
            return Sign("CONTRACTOR: %s" % head, ["______________________________", "%s, individually" % self.contractor_person, "Date: ______________"])
        return Sign("CONTRACTOR: %s" % self.contractor.upper(),
                    ["By: ______________________________", "Printed name: ______________________", "Title: ______________________", "Date: ______________"])

    @property
    def members(self) -> List[str]:
        """Every member of the company, as the operating agreement names them."""
        return [x.strip() for x in re.split(r"[;\n]+", str(self.f.get("members") or "")) if x.strip()]

    @property
    def guarantor_addr(self) -> str:
        return self.val("guarantor_address", "Guarantor's home address", "guarantor", "guarantor_address")

    # -- acknowledgments
    def ack_company(self, person: str, title: str, company: str) -> Dict[str, Any]:
        if self.state != "MD":
            return Notary("%s\nThe foregoing instrument was acknowledged before me this ____ day of ______________, 20___, by %s, as %s of %s, on its behalf.%s" % (self.sx["notary"], person, title, company, _ACK_TAIL))
        return Notary("%s\nI HEREBY CERTIFY that on this ____ day of ______________, 20___, before me, the subscriber, a Notary Public of the State aforesaid, personally appeared %s, who acknowledged "
                      "himself/herself to be the %s of %s, and that he/she, as such %s, being authorized so to do, executed the foregoing instrument for the purposes therein contained by signing the "
                      "name of the company by himself/herself as %s.%s" % (self.sx["notary"], person, title, company, title, title, _ACK_TAIL))

    def ack_person(self, person: str, capacity: str = "") -> Dict[str, Any]:
        if self.state != "MD":
            return Notary("%s\nThe foregoing instrument was acknowledged before me this ____ day of ______________, 20___, by %s%s, who is personally known to me or has produced satisfactory "
                          "evidence of identity.%s" % (self.sx["notary"], person, capacity, _ACK_TAIL))
        return Notary("%s\nI HEREBY CERTIFY that on this ____ day of ______________, 20___, before me, the subscriber, a Notary Public of the State aforesaid, personally appeared %s%s, known to me "
                      "(or satisfactorily proven) to be the person whose name is subscribed to the foregoing instrument, and acknowledged that he/she executed the same %sfor the purposes therein "
                      "contained.%s" % (self.sx["notary"], person, capacity, "in that capacity " if capacity else "", _ACK_TAIL))

    def ack_owner(self) -> Dict[str, Any]:
        """The Borrower's acknowledgment on a recorded instrument: Maryland's full certificate there, the general form elsewhere."""
        if self.state != "MD":
            return self.notary()
        if self.owner_kind == S.LLC:
            return self.ack_company(self.signer, self.signer_title, self.borrower)
        if self.owner_kind == S.INDIVIDUAL:
            return self.ack_person(self.borrower)
        return self.ack_person(self.signer, ", %s of %s" % (self.signer_title, self.borrower))

    # The closing date is the title company's to give. Until it is entered the papers carry the blank,
    # and the set can still be read, approved and sent to the title company with it (as on 21st St).
    @property
    def closing_s(self) -> str:
        return long_date(self.closing) or self.title_fill("closing date")

    @property
    def maturity_s(self) -> str:
        return long_date(self.maturity) or self.title_fill("maturity date (the day before the first anniversary of closing)")

    @property
    def fee_line(self) -> str:
        """The Borrower's loan fee as a terms table states it. With no points on the file there is none:
        RKB's own points are then inside the Note principal and settled between RKB and ROI Blocks."""
        if self.fee:
            return "%s points = %s, earned in full at closing" % (_rate(self.points), usd(self.fee))
        return "None. No loan fee, origination fee or points are payable by Borrower."

    @property
    def tax_id(self) -> str:
        return self.val("tax_id", "tax ID of the property")

    @property
    def property_line(self) -> str:
        return "%s (Tax ID %s)" % (self.address, self.tax_id)

    @property
    def records(self) -> str:
        return self.sx["records"].format(county=self.county or self.need("county"))

    @property
    def borrower_full(self) -> str:
        if self.owner_kind == S.LLC:
            return "%s, a %s limited liability company" % (self.borrower, self.state_name if self.state != "DC" else "District of Columbia")
        return self.borrower

    @property
    def borrower_addr(self) -> str:
        return self.val("borrower_address", "owner's mailing address", "owner_contact", "mailing_address")

    @property
    def signer(self) -> str:
        if self.owner_kind == S.INDIVIDUAL:
            return self.borrower
        return self.val("signer_name", "name of the person who signs for the owner")

    @property
    def signer_title(self) -> str:
        """As the company's own papers and the title company give it. On 21st St our papers said
        "Managing Member" while the title company's said "Sole Member": the two must read the same at the table."""
        if self.owner_kind == S.INDIVIDUAL:
            return ""
        typed = self.raw("signer_title")
        if typed:
            return typed
        if self.owner_kind == S.LLC and len(self.members) == 1:
            return "Sole Member"
        return {S.LLC: "Managing Member", S.ESTATE: "Personal Representative", S.TRUST: "Trustee"}.get(self.owner_kind, "")

    @property
    def guarantor(self) -> str:
        return self.val("guarantor_name", "guarantor's full legal name", "guarantor", "guarantor_name")

    @property
    def contractor(self) -> str:
        return self.val("contractor_name", "contractor's exact legal name", "licence", "legal_name")

    @property
    def licence(self) -> str:
        return self.val("contractor_licence", "contractor's licence number", "licence", "licence_number")

    @property
    def title_co(self) -> str:
        return self.val("title_company", "title company")

    @property
    def trustee(self) -> str:
        t = self.st.get("trustee") or {}
        vals = (self.items.get("trustee") or {}).get("values") or {}
        name, addr = vals.get("trustee_name") or t.get("name"), vals.get("trustee_address") or t.get("address")
        return "%s, whose address is %s" % (name, addr) if name else self.need("trustee")

    @property
    def trustee_on_instrument(self) -> str:
        """As the deed of trust names the trustee. Maryland's is an individual, and the instrument says where he resides."""
        return self.trustee.replace(", whose address is", ", a resident of the State of Maryland, whose address is", 1) if self.state == "MD" else self.trustee

    @property
    def attorney(self) -> Dict[str, str]:
        """The Maryland attorney who certifies the deed of trust (Real Property 3-104): RKB's trustee there."""
        return self.st.get("attorney") or self.st.get("trustee") or {}

    @property
    def trustee_name(self) -> str:
        vals = (self.items.get("trustee") or {}).get("values") or {}
        return vals.get("trustee_name") or (self.st.get("trustee") or {}).get("name") or self.need("trustee")

    @property
    def interest_sentence(self) -> str:
        if self.free:
            return ("No interest accrues for the %s (%d) consecutive calendar months beginning on the date the %s securing this Note is recorded (the \"Interest-Free Period\"). "
                    "Beginning on the first day after the Interest-Free Period ends, simple interest accrues on the unpaid principal at the rate of %s percent (%s%%) per year, "
                    "computed on the actual number of days elapsed over a 365-day year, until the principal is paid in full, both before and after the Maturity Date, acceleration or judgment."
                    % (_small(self.free), self.free, self.instrument, _small(int(self.rate)), _rate(self.rate)))
        return ("Simple interest accrues on the full principal sum of %s at the rate of %s percent (%s%%) per year, computed on the actual number of days elapsed over a 365-day year, "
                "from the date the %s securing this Note is recorded among %s, whether or not Lender has then disbursed all of its funding undertaking, until the principal is paid in full, "
                "both before and after the Maturity Date, acceleration or judgment. There is no interest-free period."
                % (usd(self.principal), _small(int(self.rate)), _rate(self.rate), self.instrument, self.records))

    @property
    def terms_short(self) -> str:
        if self.free:
            return "no interest for %d calendar months after this Security Instrument is recorded and simple interest at %s%% per year thereafter" % (self.free, _rate(self.rate))
        return "simple interest at %s%% per year from the date this Security Instrument is recorded" % _rate(self.rate)

    @property
    def capacity(self) -> Optional[str]:
        if self.owner_kind == S.ESTATE:
            return ("%s executes this instrument solely in the capacity of Personal Representative of the estate named above, and not individually. Borrower represents that the letters of "
                    "administration are in full force and effect and that no will, order or agreement restricts the power to encumber the Property." % self.signer)
        if self.owner_kind == S.TRUST:
            return ("%s executes this instrument solely as Trustee of the trust named above, and not individually. Borrower represents that the trust is in existence, that the Trustee has the "
                    "power to mortgage and encumber the Property, and that no consent of any other person is required." % self.signer)
        if self.owner_kind == S.LLC:
            return ("Borrower is a limited liability company duly organized and in good standing. The person signing below is authorized by resolution of all of its members to execute "
                    "and deliver this instrument on its behalf.")
        return None

    def sig_block(self, seal: bool = True) -> Dict[str, Any]:
        s = " (SEAL)" if seal else ""
        if self.owner_kind == S.INDIVIDUAL:
            return Sign("BORROWER", ["______________________________%s" % s, self.borrower, "Date: ______________"])
        return Sign("BORROWER: %s" % self.borrower.upper(), ["By: ______________________________%s" % s, self.signer, self.signer_title, "Date: ______________"])

    def notary(self, sworn: bool = False) -> Dict[str, Any]:
        who = self.signer if self.owner_kind == S.INDIVIDUAL else "%s, as %s of %s" % (self.signer, self.signer_title, self.borrower)
        verb = "Subscribed and sworn to before me" if sworn else "The foregoing instrument was acknowledged before me"
        return Notary("%s\n%s this ____ day of ______________, 20___, by %s, who is personally known to me or has produced satisfactory evidence of identity.\n\n"
                      "______________________________\nNotary Public\nPrinted name: ______________________\nMy commission expires: ______________          [NOTARIAL SEAL]"
                      % (self.sx["notary"], verb, who))

    def foot(self) -> str:
        return "Loan No. %s" % self.loan_no


def _rate(r: float) -> str:
    return ("%.2f" % r).rstrip("0").rstrip(".")


def _small(n: int) -> str:
    w = _under_1000(n).lower()
    return w or "zero"


def _lender_sign(title: str = "LENDER") -> Dict[str, Any]:
    return Sign("%s: %s" % (title, LENDER.upper()), ["By: ______________________________", LENDER_SIGNER, LENDER_TITLE, "Date: ______________"])


def _roi_sign(title: str = "ROI BLOCKS LLC") -> Dict[str, Any]:
    return Sign(title, ["By: ______________________________", ROI_SIGNER, ROI_TITLE, "Date: ______________"])


# ------------------------------------------------------------------ 1. the commitment letter
def commitment(c: Ctx) -> List[Dict[str, Any]]:
    m, r, a = c.money, c.r, c.a
    arv = E._n(c.f.get("arv"))
    b: List[Dict[str, Any]] = [Title("CONDITIONAL LOAN COMMITMENT", "%s · %s" % (LENDER, LENDER_ADDR)),
         Terms([["Date", long_date(today())], ["To", "%s, %s" % (c.borrower_full, c.borrower_addr)],
                ["And to", ("%s, individually, as Guarantor" % c.guarantor) if c.owner_kind == S.LLC else ""],
                ["Copy", "%s, %s, %s; %s; %s" % (ROI_SIGNER, ROI_TITLE, ROI, c.contractor_cite, c.title_co)], ["Re", c.property_line]]),
         P("%s (\"Lender\") is prepared to make the loan described below to %s (\"Borrower\"), subject to and conditioned upon satisfaction of every condition in this letter, "
           "each of which must be satisfied in a manner acceptable to Lender in its sole discretion." % (LENDER, c.borrower)),
         P("This letter supersedes all prior discussions, term sheets and correspondence on this loan. It is not a commitment to fund. Lender's obligation to fund arises only when "
           "every condition below has been satisfied and Lender has confirmed that in writing."),
         H("1. LOAN TERMS"),
         Terms([["Borrower", c.borrower_full], ["Guarantor", ("%s, individually, unconditionally and continuing" % c.guarantor) if c.owner_kind == S.LLC else ""],
                ["Property", c.property_line],
                ["Note principal", "%s, the contract price under the %s Customer Agreement, assigned to Lender, with Borrower's Note given in substitution for it" % (usd(c.principal), ROI)],
                ["Purpose", "Renovation of the Property for resale. Business purpose. No proceeds to Borrower."],
                ["Interest rate", ("%s%% simple per annum on the full principal sum from the date the %s records, whether or not Lender has disbursed all of it. No periodic payments." % (_rate(c.rate), c.instrument))
                 if not c.free else ("No interest for the first %d months after the %s records; then %s%% simple per annum on the unpaid principal. No periodic payments." % (c.free, c.instrument, _rate(c.rate)))],
                ["Minimum interest", ("%s (%d months), payable even on an earlier payoff" % (usd(c.min_interest), c.min_months)) if c.min_months else ""],
                ["Loan fee", c.fee_line],
                ["Maturity", "The earlier of (a) sale or transfer of the Property, or (b) the day before the first anniversary of the Note"],
                ["Security", "%s-lien %s on the Property, with Loan Terms Rider, securing the full principal and all obligatory and protective advances%s"
                 % (c.lien_word.capitalize(), c.instrument, "; Continuing Personal Guaranty" if c.owner_kind == S.LLC else "")],
                ["Lien position", ("Second lien, behind %s only. No other lien may remain." % " and ".join(l["holder"] for l in c.liens_staying)) if c.second else "First and only lien."],
                ["Trustee", "" if c.is_mortgage else c.trustee_name], ["Settlement agent", c.title_co], ["Loan number", c.loan_no],
                ["Prepayment", "Permitted at any time" + (", subject to the minimum interest above" if c.min_months else "")], ["Late charge", "None"]]),
         # The builder's cost, what RKB funds and ROI Blocks' share are between RKB and ROI Blocks:
         # a paper the Borrower reads carries the Note principal only (2026-11-20).
         Terms([["Note principal / as-renovated value", "%.1f%%" % (100 * c.principal / arv) if arv else ""],
                ["As-renovated value used by Lender", usd(arv)]]),
         # The homeowner knows one number, the contract price. Not the figure of the builder's cost, and not the
         # fact that there is one: no paper the Borrower signs speaks of the Contractor's cost, a contingency or a
         # draw budget (said again on 25 Nov 2026: "they will only know the retail cost").
         P("This loan is made on the security of the Property."),
         H("2. CONDITIONS PRECEDENT")]
    # RKB's own lines (the ARV file, Manju Sir's look at the budget, an exception) are RKB's steps,
    # not conditions the Borrower satisfies: they are not printed in a letter the Borrower signs.
    # Nor is the line that asks ROI Blocks for the builder's cost: that is between RKB and ROI Blocks.
    open_lines = [i for i in c.doc.get("items") or [] if i["when"] == C.FILE and i.get("required", True) and i.get("status") != C.OK and i["party"] != C.RKB
                  and i["key"] not in NOT_FOR_BORROWER]
    n = 0
    for party in C.PARTY_ORDER:
        rows = [i for i in open_lines if i["party"] == party]
        if not rows:
            continue
        b.append(P("", lead="From %s" % C.PARTY_LABEL[party].replace(" · ", " / ")))
        b.append(L([_condition(c, i) for i in rows]))
        n += len(rows)
    if not n:
        b.append(P("Every condition of the file has been satisfied as of the date of this letter."))
    for extra in c.f.get("special_conditions") or []:
        b.append(P(str(extra)))
    later = [i for i in c.doc.get("items") or [] if i.get("deferred")]
    b += [H("3. CLOSING, DISBURSEMENT AND DRAWS"),
          P("Borrower must sign personally. No power of attorney may be used for the Note%s. Borrower must receive a copy of every document signed." % (", the %s or the Guaranty" % c.instrument if c.owner_kind == S.LLC else " or the %s" % c.instrument), lead="3.1 Signing."),
          P("The final settlement statement must show $0.00 proceeds to Borrower. Renovation funds do not pass through settlement. Lender retains and disburses them directly.", lead="3.2 No proceeds to Borrower."),
          P("No advance of any kind will be made, and no work may begin and no materials may be delivered, until the %s has recorded and the settlement agent has confirmed the recording "
            "reference to Lender in writing. %s" % (c.instrument, c.st.get("mechanics_lien", "")), lead="3.3 Recording before any advance."),
          P("Each draw request must be accompanied by the Contractor's itemized request, photographs, partial lien releases from the Contractor and from every subcontractor and "
            "supplier paid from the previous draw, and Lender's satisfactory inspection. Lender funds 90% of each approved draw. The remaining 10% is retained and released at "
            "settlement of the sale of the Property, against final lien releases.", lead="3.4 Draws."),
          P("Lender funds only after approving the final settlement statement and confirming wire instructions by telephone with the settlement agent. Wire instructions received by email "
            "will not be relied upon without that call.", lead="3.5 Wire."),
          P("Within two business days of closing, scanned copies of all signed documents to Lender, and the original Note%s overnighted to Lender's custodian: %s."
            % (" and original Guaranty" if c.owner_kind == S.LLC else "", CUSTODIAN), lead="3.6 Originals.")]
    # A line RKB has allowed to come in after closing (on 21st St, the signed measurement): said in the
    # letter in so many words, so that nobody reads its absence from the conditions as a waiver.
    for k, i in enumerate(later, start=7):
        due = long_date(_date((i.get("deferred") or {}).get("due")))
        b.append(P("As an exception for this loan, the following is not a condition of closing: %s. %s shall deliver it to Lender %s."
                   % (_lower_first(i["label"]), _WHO_DELIVERS.get(i["party"], "Borrower"), ("on or before %s" % due) if due else "after closing"), lead="3.%d %s." % (k, i.get("name") or i["label"])))
    b += [H("4. GENERAL"),
          P("This commitment expires thirty (30) days after its date unless the loan has closed or Lender has extended it in writing.", lead="4.1 Expiration."),
          P("Lender has no obligation to advance any sum until every condition has been satisfied and Lender has confirmed that in writing. Conditions may be waived only by Lender in writing.", lead="4.2 Not an obligation to fund."),
          P("Lender may withdraw this commitment if, before closing, there is any material adverse change in the Property, in the scope or price of the Work, in the state of title, or in the insurance, "
            "or if any information given to Lender proves materially inaccurate.", lead="4.3 Material adverse change; accuracy."),
          P("Borrower may not assign this commitment. Lender may assign it, and the loan, without Borrower's consent. %s and the Contractor receive this letter for information; neither is a party "
            "to the loan or a beneficiary of this commitment." % ROI, lead="4.4 Assignment; no third-party beneficiaries."),
          P("This commitment is governed by the laws of %s." % c.sx["law"], lead="4.5 Governing law."),
          _lender_sign(),
          P("The undersigned accepts this Conditional Loan Commitment and agrees to satisfy each of its conditions.", lead="ACCEPTED AND AGREED"), c.sig_block(seal=False)]
    if c.owner_kind == S.LLC:
        b.append(Sign("GUARANTOR", ["______________________________", "%s, individually" % c.guarantor, "Date: ______________"]))
    b += [P("%s confirms that it assigns the whole %s contract price to Lender and releases Borrower from paying it to %s, that it will look solely to Lender's draws for the amounts "
            "Lender undertakes to fund, and that its own compensation is governed by the %s between %s and Lender." % (ROI, usd(c.principal), ROI, ALLOC, ROI), lead="ACKNOWLEDGED"),
          _roi_sign(), c.contractor_sign(cite=True)]
    return b


_WHO_DELIVERS = {C.OWNER: "Borrower", C.ROI: ROI, C.CONTRACTOR: "The Contractor", C.TITLE: "The settlement agent", C.INSURANCE: "Borrower's insurance agent", C.ATTORNEY: "Lender's attorney"}


def _lower_first(s: str) -> str:
    return (s[:1].lower() + s[1:]) if s[:1].isupper() and not s[:2].isupper() else s


def _eastern_day(v: Any) -> str:
    """The calendar day in New York of a stored moment, as a paper prints it."""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return long_date(_date(v))
    if isinstance(v, datetime):
        try:
            from datetime import timezone
            from zoneinfo import ZoneInfo
            return long_date((v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(ZoneInfo("America/New_York")).date())
        except Exception:
            return long_date(v.date())
    return long_date(_date(v))


def _condition(c: Ctx, i: Dict[str, Any]) -> str:
    """One condition of the commitment: what is asked and, once something has come in, what was
    received and exactly what is still needed. Nothing about the Contractor's cost is repeated here."""
    name = i.get("name") or i["label"]
    head = ("%s. %s" % (name, i["label"])) if i["label"] != name else name
    if i["key"] in NOT_FOR_BORROWER:
        return head
    files = i.get("files") or []
    last = files[-1] if files else {}
    reading = last.get("reading") or {}
    got = _eastern_day(last.get("uploaded_at")) if last else ""
    got = (" " + got) if got else ""
    # The letter speaks the rules as they stand today. A check the reading made and the paper met is
    # settled; one it did not meet is repeated in the reading's own words; one the rules have gained since
    # the paper was read is asked for as it is written; one the rules have dropped is not mentioned.
    still: List[str] = []
    if reading and reading.get("right_paper", True):
        seen = {(r.get("check") or ""): r for r in reading.get("checks") or []}
        for check in i.get("checks") or []:
            r = seen.get(check)
            if r and r.get("result") == "met":
                continue
            text = ((r or {}).get("note") or "").strip() or check
            # On the agreement, the section numbers are RKB's own cross-check for the Amendment, and a check
            # the old reading never made is covered by the sentence about the Amendment below.
            if i["key"] == "contract" and (not r or check.startswith("Section ")):
                continue
            if not _PRIVATE.search("%s %s" % (check, text)):
                still.append(text if text.endswith((".", "?", "!")) else text + ".")
    if i["key"] == "contract" and (files or c.day("contract_date")):
        # The signed agreement still gives ROI Blocks a lien and sends disputes to arbitration. That is
        # cured by the Amendment signed at closing, not by asking for the agreement again.
        signed = " and ".join(x for x in (("by %s" % c.raw("signer_name")) if c.raw("signer_name") and c.owner_kind != S.INDIVIDUAL else "", ("on %s" % c.day("contract_date")) if c.day("contract_date") else "") if x)
        return ("%s Received: Customer Agreement signed%s, contract price %s. Still needed: the Amendment to Customer Agreement in this loan set, signed at closing by Owner and %s. "
                "It deletes Sections %s, the fee provisions and the arbitration clause in Section %s.%s"
                % (name + ".", (" " + signed.replace(" and on ", " on ")) if signed else "", usd(c.principal), ROI, ROI_LIEN_SECTIONS, ROI_ARBITRATION_SECTION, (" Also still needed: " + " ".join(still)) if still else ""))
    if files and reading and not reading.get("right_paper", True):
        return "%s Received%s: this is not the paper asked for%s." % (head + ("" if head.endswith(".") else "."), got, (" (it is %s)" % str(reading.get("what_it_is")).rstrip(".")) if reading.get("what_it_is") else "")
    if i.get("status") == C.REJECTED:
        if still:
            return "%s Received%s. Still needed: %s" % (head + ("" if head.endswith(".") else "."), got, " ".join(still))
        if i.get("reason") and not reading and not _PRIVATE.search(i["reason"]):
            return "%s Returned: %s" % (head + ("" if head.endswith(".") else "."), i["reason"])
        return "%s Received%s, not yet accepted by Lender." % (head + ("" if head.endswith(".") else "."), got)
    if i.get("status") == C.RECEIVED and files:
        return "%s Received%s; under review by Lender." % (head + ("" if head.endswith(".") else "."), got)
    vals = i.get("values") or {}
    have = [f for f in i.get("fields") or [] if str(vals.get(f["key"]) or "").strip()]
    if have and len(have) < len(i.get("fields") or []):
        shown = ["%s%s" % (f["label"], (" " + long_date(_date(vals[f["key"]]))) if f.get("type") == "date" and _date(vals[f["key"]]) else "") for f in have]
        return "%s Received: %s. Still needed: %s." % (head + ("" if head.endswith(".") else "."), "; ".join(shown), "; ".join(_lower_first(f["label"]) for f in i["fields"] if f not in have))
    return head


# ------------------------------------------------------------------ 2. the note
def note(c: Ctx) -> List[Dict[str, Any]]:
    b = [Title("PROMISSORY NOTE", "Business-Purpose Loan · Secured by %s" % c.instrument),
         Terms([["Date", c.closing_s], ["State", c.state_name], ["Property", c.property_line], ["Principal", usd(c.principal)], ["Loan No.", c.loan_no]]),
         H("1. BORROWER'S PROMISE TO PAY"),
         P("FOR VALUE RECEIVED, %s (\"Borrower\"), promises to pay to the order of %s (\"Lender\"; Lender and any holder of this Note, the \"Note Holder\"), the principal sum of %s (%s), "
           "together with interest as provided below." % (c.borrower_full, LENDER_FULL, words(c.principal), usd(c.principal))),
         P("The value received by Borrower consists of (a) the assignment to Lender by %s (\"ROI Blocks\") of the contract price of %s owed by Borrower under the Customer Agreement between Borrower "
           "and ROI Blocks for the renovation of the Property, as amended on the date of this Note (the \"Customer Agreement\"), and ROI Blocks' release of Borrower from any obligation to pay that "
           "contract price to ROI Blocks, and (b) Lender's undertaking to pay for the renovation work at the Property on Borrower's behalf. This Note is given in substitution for, and in satisfaction of, "
           "Borrower's obligation to pay the contract price under the Customer Agreement." % (ROI, usd(c.principal))),
         P("If the renovation work under the Customer Agreement is not completed by ROI Blocks or its contractor, the principal due under this Note shall be the total of all amounts actually paid by "
           "Lender for the renovation work, for insurance, taxes and preservation of the Property, and as protective advances, but not more than %s. The Note Holder's books and records shall be "
           "prima facie evidence of such amounts." % usd(c.principal), lead="Principal adjustment."),
         H("2. INTEREST"), P(c.interest_sentence)]
    if c.free:
        b.append(P("If the Note Holder accelerates this Note under Section 4 because of a default, interest at the rate of %s%% per year accrues from the date of acceleration even if the "
                   "Interest-Free Period has not ended." % _rate(c.rate)))
    if c.min_months:
        b.append(P("Interest of not less than %s, being %s (%d) months' interest, is payable even if this Note is paid in full earlier." % (usd(c.min_interest), _small(c.min_months), c.min_months), lead="Minimum interest."))
    if c.fee:
        b.append(P("A loan fee of %s percent (%s%%) of the principal sum, %s, is earned in full at closing." % (_small(int(c.points)), _rate(c.points), usd(c.fee)), lead="Loan fee."))
    else:
        b.append(P("No loan fee, origination fee or points are payable by Borrower under this Note.", lead="No loan fee."))
    if c.state == "FL":
        b.append(P("It is the intent of the parties that this Note comply with the usury laws of the State of Florida. If the interest, fees and charges contracted for, charged or received under this Note "
                   "would ever exceed the maximum lawful rate, they shall be reduced to that maximum, and any excess received shall be applied to principal or refunded to Borrower. No extension fee "
                   "or default rate shall be charged that would cause the yearly total to exceed that maximum.", lead="Usury savings."))
    b += [H("3. PAYMENT"),
          P("No monthly or other periodic payments are required. The entire unpaid principal, all accrued interest, and all other sums due under this Note and the %s are due and payable in full on the "
            "earlier of (a) the closing of any sale, conveyance or other transfer of all or any part of the Property or any interest in it, or (b) %s (the \"Maturity Date\").%s"
            % (c.instrument, c.maturity_s, " If the Property is sold and this Note is paid in full during the Interest-Free Period, no interest is due." if c.free and not c.min_months else "")),
          P("Payments shall be made in lawful money of the United States in immediately available funds to %s at %s, or at another place the Note Holder designates in writing. Payments shall be applied "
            "first to costs and expenses due under Section 4(D), then to accrued interest, and then to principal." % (LENDER, LENDER_ADDR))]
    if c.state == "MD" and c.second and c.owner_kind != S.LLC:
        b.append(P("Borrower may, by written request delivered to the Note Holder before the Maturity Date, postpone the Maturity Date one time for a period of six (6) months. No fee or charge of any kind "
                   "is payable for the postponement, and the terms of this Note continue unchanged during it. No interest is taken in advance and interest is not compounded.", lead="Borrower's right to postpone maturity."))
    b += [H("4. DEFAULT AND REMEDIES"),
          P("No late charge is payable under this Note.", lead="(A) No late charge."),
          P("Borrower will be in default if (i) any amount due under this Note is not paid when due; (ii) Borrower is in default under the %s or the Construction Draw Agreement of even date, and the "
            "default is not cured within any cure period stated there; (iii) any statement made by or for Borrower to Lender in connection with this loan was materially false when made; or (iv) the "
            "Property, or any interest in it or in Borrower, is transferred without payment of this Note in full%s." % (c.instrument, "; or (v) Borrower fails to pay when due any sum secured by a lien "
            "on the Property that is senior to the %s" % c.instrument if c.second else ""), lead="(B) Events of default."),
          P("Except for nonpayment at the Maturity Date or upon a sale or transfer of the Property, which requires no notice, the Note Holder shall give Borrower written notice of a default and at least "
            "ten (10) days to cure it. If the default is not cured, the Note Holder may require Borrower to pay immediately the full unpaid principal, all accrued interest, and all other sums due. Even "
            "if the Note Holder does not do so at the time of a default, the Note Holder may do so later.", lead="(C) Notice and acceleration."),
          P("Borrower shall pay all of the Note Holder's costs and expenses of enforcing this Note and the %s, including reasonable attorneys' fees, %scosts of foreclosure, to the extent not prohibited "
            "by applicable law." % (c.instrument, "" if c.is_mortgage else "trustees' commissions and expenses, and "), lead="(D) Costs and expenses."),
          H("5. SECURITY"),
          P("This Note is secured by a %s of even date (with its Loan Terms Rider) from Borrower %sfor the benefit of Lender, encumbering the Property%s. The %s describes how and under what conditions "
            "Borrower may be required to pay immediately all amounts owed under this Note." % (c.instrument, "" if c.is_mortgage else "to %s, Trustee, " % c.trustee_name,
            ", and by the Continuing Personal Guaranty of %s of even date" % c.guarantor if c.owner_kind == S.LLC else "", c.instrument)),
          H("6. PREPAYMENT"), P("Borrower may prepay all or any part of the principal at any time without penalty%s. Partial prepayments do not postpone the Maturity Date." % (", subject to the minimum interest in Section 2" if c.min_months else "")),
          H("7. BUSINESS PURPOSE"),
          P("Borrower represents that the loan evidenced by this Note is made for a business and commercial purpose, namely the renovation of a vacant, non-owner-occupied property for resale, and not "
            "for personal, family or household purposes."),
          H("8. WAIVERS"), P("Borrower and all endorsers, sureties and guarantors waive presentment, demand, protest, notice of dishonor and notice of protest."),
          H("9. NOTICES"),
          P("Notices to Borrower shall be delivered by hand, by nationally recognized overnight courier, or by certified mail to %s, or to another address Borrower designates in writing, with a copy to the "
            "Property address. Notices to the Note Holder shall be sent the same way to the address in Section 3." % c.borrower_addr)]
    if c.capacity:
        b += [H("10. CAPACITY OF BORROWER"), P(c.capacity)]
    b += [H("%d. GOVERNING LAW; JURY WAIVER; ASSIGNMENT" % (11 if c.capacity else 10)),
          P("This Note is governed by the laws of %s. The Note Holder may sell, assign or transfer this Note without notice to Borrower. If any provision of this Note is held unenforceable, the "
            "remaining provisions remain in effect. BORROWER AND THE NOTE HOLDER KNOWINGLY AND VOLUNTARILY WAIVE TRIAL BY JURY IN ANY ACTION ARISING OUT OF THIS NOTE." % c.sx["law"]),
          P("IN WITNESS WHEREOF, Borrower has executed this Note under seal as of the date first written above."), c.sig_block(),
          Note("[SIGN ORIGINAL ONLY · DO NOT RECORD · ORIGINAL TO LENDER'S CUSTODIAN]")]
    return b


# ------------------------------------------------------------------ 3. the security instrument
def _remedy(c: Ctx) -> List[Dict[str, Any]]:
    """Acceleration and sale, as that state does it."""
    cure = ("Upon Borrower's breach of any covenant or agreement in the Note or this Security Instrument, Lender shall give Borrower notice specifying (a) the default, (b) the action required to cure it, "
            "and (c) a date, not less than 30 days from the date the notice is given, by which the default must be cured; provided that no such notice or cure period is required for failure to pay the "
            "Note on the Maturity Date or for acceleration under Section 15. If the default is not cured by the date specified, Lender at its option may declare all sums secured by this Security "
            "Instrument immediately due and payable without further demand and may invoke ")
    fees = " Lender shall be entitled to collect all expenses incurred in pursuing these remedies, including reasonable attorneys' fees%s and costs of title evidence."
    if c.state not in ("MD", "VA", "DC", "FL") and c.sx.get("remedy"):
        # a state whose sheet Claude wrote and RKB approved: its own remedy wording
        how = "foreclosure of this Security Instrument by judicial proceeding" if c.is_mortgage else "the power of sale"
        return [P(cure + how + " and any other remedies permitted by Applicable Law." + fees % ("" if c.is_mortgage else ", trustees' commissions,"), lead="19. Acceleration; Remedies.")] + \
               [P(str(x.get("text") or ""), lead=str(x.get("lead") or "")) for x in c.sx["remedy"]]
    if c.state == "MD":
        return [P(cure + "the power of sale, the assent to decree, and any other remedies permitted by Applicable Law." + fees % ", trustees' commissions,", lead="19. Acceleration; Remedies; Power of Sale; Assent to Decree."),
                P("Borrower hereby authorizes and empowers Trustee, upon Lender's request after a default, to sell the Property, or any part of it, at public auction, after giving such notice of the time, place "
                  "and terms of sale as is required by Real Property Article §§7-105 et seq. of the Annotated Code of Maryland and Title 14, Chapter 200 of the Maryland Rules, as amended, and to convey the "
                  "Property to the purchaser or purchasers by deed, without warranty.", lead="Power of sale."),
                P("Borrower hereby assents to the passage of a decree for the sale of the Property by the Circuit Court for %s, or any other court having jurisdiction, in accordance with the Maryland Rules, "
                  "and agrees that such sale may be made by Trustee or any substitute trustee appointed hereunder." % (c.county or c.need("county")), lead="Assent to decree."),
                P("Lender or its designee may purchase the Property at any sale. The proceeds of any sale shall be applied (a) to the expenses of the sale, including a commission to Trustee as allowed by the "
                  "Maryland Rules and reasonable attorneys' fees; (b) to taxes and other charges having priority over this Security Instrument; (c) to all sums secured by this Security Instrument; and (d) any "
                  "balance to the person or persons legally entitled to it. Lender's affidavit or statement of debt shall be prima facie evidence of the amounts due.")]
    if c.state == "VA":
        return [P(cure + "the power of sale and any other remedies permitted by Applicable Law." + fees % ", trustees' commissions,", lead="19. Acceleration; Remedies; Power of Sale."),
                P("This Security Instrument is to be construed as provided in §§55.1-320 through 55.1-325 of the Code of Virginia, and the following provisions are incorporated with the meaning given them "
                  "there: EXEMPTIONS WAIVED. SUBJECT TO CALL UPON DEFAULT. RENEWAL, EXTENSION OR REINSTATEMENT PERMITTED. SUBSTITUTION OF TRUSTEE PERMITTED. ANY TRUSTEE MAY ACT. ADVERTISEMENT REQUIRED: "
                  "ONCE A WEEK FOR TWO SUCCESSIVE WEEKS. INSURANCE REQUIRED: full insurable value.", lead="Statutory provisions."),
                P("Upon Lender's request after a default, Trustee shall give notice of sale as required by §55.1-321 of the Code of Virginia, shall advertise the sale as stated above in a newspaper having "
                  "general circulation in the city or county where the Property lies, and shall sell the Property at public auction to the highest bidder, in one or more parcels, and convey it by "
                  "trustee's deed with special warranty of title. Lender or its designee may purchase at the sale. The proceeds shall be applied as provided in §55.1-324 of the Code of Virginia: to the "
                  "expenses of sale, including a trustee's commission of five percent (5%) of the gross sale price and reasonable attorneys' fees; to taxes and prior liens; to all sums secured by this "
                  "Security Instrument; and any balance to the persons legally entitled to it.", lead="Power of sale.")]
    if c.state == "DC":
        return [P(cure + "the power of sale and any other remedies permitted by Applicable Law." + fees % ", trustees' commissions,", lead="19. Acceleration; Remedies; Power of Sale."),
                P("Upon Lender's request after a default, and after compliance with D.C. Code §42-815 et seq., including any notice of default, mediation and notice of intention to foreclose that Applicable "
                  "Law requires for the Property, Trustee shall sell the Property at public auction to the highest bidder at the time and place and upon the terms stated in the notice of sale, in one or "
                  "more parcels, and shall convey it by trustee's deed without warranty. Lender or its designee may purchase at the sale. The proceeds shall be applied to the expenses of sale, including "
                  "a trustee's commission and reasonable attorneys' fees; to taxes and prior liens; to all sums secured by this Security Instrument; and any balance to the persons legally entitled to it.", lead="Power of sale.")]
    return [P(cure + "foreclosure of this Security Instrument by judicial proceeding and any other remedies permitted by Applicable Law." + fees % "", lead="19. Acceleration; Remedies; Foreclosure."),
            P("Lender may foreclose this Security Instrument in the circuit court of the county where the Property lies. In any such action Lender shall be entitled to the appointment of a receiver of the "
              "Property without notice and without regard to the adequacy of the security, and to recover its reasonable attorneys' fees and costs, including those of any appeal.", lead="Judicial foreclosure."),
            P("Borrower absolutely assigns to Lender all rents, issues and profits of the Property as further security. Under §697.07, Florida Statutes, this assignment is enforceable upon Borrower's default "
              "and Lender's written demand, without the appointment of a receiver.", lead="Assignment of rents.")]


def _advances(c: Ctx) -> Dict[str, Any]:
    amt = usd(c.principal)
    tail = " All such advances have the same priority as if made on the date this Security Instrument is recorded."
    if c.state not in ("MD", "VA", "DC", "FL") and c.sx.get("advances"):
        return P(str(c.sx["advances"]).replace("MAXIMUM_PRINCIPAL", amt), lead="22. Advances; Maximum Principal.")
    if c.state == "MD":
        return P("Pursuant to Real Property Article §7-102 of the Annotated Code of Maryland, this Security Instrument secures advances to be made by Lender under the Construction Draw Agreement for the "
                 "renovation of the Property, which Lender is obligated to make subject to the conditions stated in that agreement, and secures protective advances under Sections 4 and 7. The maximum "
                 "principal amount secured, exclusive of interest and protective advances, is %s." % amt + tail, lead="22. Obligatory Advances; Maximum Principal.")
    if c.state == "VA":
        return P("THIS IS A CREDIT LINE DEED OF TRUST within the meaning of §55.1-318 of the Code of Virginia. It secures advances to be made by Lender under the Construction Draw Agreement, which Lender is "
                 "obligated to make subject to the conditions stated in that agreement, and protective advances under Sections 4 and 7. The maximum aggregate amount of principal to be secured at any one "
                 "time is %s. The name and address of the noteholder to whom notices may be mailed or delivered under §55.1-318 are: %s, %s." % (amt, LENDER, LENDER_ADDR) + tail,
                 lead="22. Credit Line Deed of Trust; Future Advances.")
    if c.state == "DC":
        return P("This Security Instrument secures advances to be made by Lender under the Construction Draw Agreement, which Lender is obligated to make subject to the conditions stated in that agreement, "
                 "and protective advances under Sections 4 and 7. The maximum principal amount secured, exclusive of interest and protective advances, is %s." % amt + tail,
                 lead="22. Obligatory Advances; Maximum Principal.")
    return P("Under §697.04, Florida Statutes, this Security Instrument secures not only the indebtedness existing on its date but also future advances, whether obligatory or made at Lender's option, "
             "made within twenty (20) years from its date, to the same extent as if made on the date of its execution. The total unpaid principal secured at any one time shall not exceed %s, plus "
             "interest and any disbursements made for the payment of taxes, levies or insurance on the Property, with interest on those disbursements." % usd(c.principal * 2),
             lead="22. Future Advances; Maximum Principal.")


def security(c: Ctx) -> List[Dict[str, Any]]:
    inst = c.instrument.upper()
    grant = ("Borrower mortgages, grants and conveys to Lender" if c.is_mortgage else
             "Borrower irrevocably grants and conveys to Trustee, in trust, with power of sale%s," % (" and with assent to the passage of a decree" if c.state == "MD" else ""))
    title = Title(("CREDIT LINE " if c.state == "VA" else "") + inst, "%s Lien · Business-Purpose Loan · Secures %s Advances" % (c.lien_word.capitalize(), "Future" if c.is_mortgage else "Obligatory"))
    ret = Note("After recording return to: %s, %s  ·  Tax ID No. %s  ·  Title file: %s  ·  Loan No. %s" % (LENDER, LENDER_ADDR, c.tax_id, c.title_co, c.loan_no))
    if c.state == "MD" and c.attorney.get("name"):
        # Maryland's first page: who prepared it, where it goes back, the recorder's space, then the title.
        head = [Note("Prepared by: %s, %s" % (c.attorney["name"], c.attorney.get("address") or "")), ret, Note("[Space Above This Line For Recording Data]"), title]
    else:
        head = [title, ret]
    if c.state == "VA":
        head.append(Note("THIS IS A CREDIT LINE DEED OF TRUST. Maximum aggregate principal: %s. Noteholder for notices: %s, %s." % (usd(c.principal), LENDER, LENDER_ADDR)))
    if c.is_mortgage:
        head.append(P("THIS MORTGAGE (this \"Security Instrument\") is made as of %s by %s (\"Borrower\"), as mortgagor, whose address is %s, to %s, whose address is %s (\"Lender\"), as mortgagee."
                      % (c.closing_s, c.borrower_full, c.borrower_addr, LENDER_FULL, LENDER_ADDR)))
    else:
        head.append(P("THIS DEED OF TRUST (this \"Security Instrument\") is made as of %s by %s (\"Borrower\"), as grantor, to %s (\"Trustee\"), as trustee, for the benefit of %s, whose address is %s "
                      "(\"Lender\"), as beneficiary." % (c.closing_s, c.borrower_full, c.trustee_on_instrument, LENDER_FULL, LENDER_ADDR)))
    b = head + [
        H("DEFINITIONS"),
        P("\"Note\" means the Promissory Note of even date signed by Borrower and payable to the order of Lender in the principal amount of %s (%s), with %s, payable in full on the earlier of a sale or "
          "transfer of the Property or %s." % (usd(c.principal), words(c.principal), c.terms_short, c.maturity_s), lead="(A)"),
        P("\"Loan\" means the debt evidenced by the Note, plus interest, all advances made by Lender under this Security Instrument and the Construction Draw Agreement, and all other sums due under the "
          "Note and this Security Instrument, plus interest.", lead="(B)"),
        P("\"Construction Draw Agreement\" means the Construction Draw Agreement of even date among Borrower, Lender and %s governing the renovation of the Property." % ROI, lead="(C)"),
        P("\"Riders\" means the Loan Terms Rider attached as Exhibit B, which is incorporated into and amends this Security Instrument.", lead="(D)"),
        P("\"Applicable Law\" means all controlling federal, state and local statutes, regulations, ordinances and rules having the effect of law, and final, non-appealable judicial opinions.", lead="(E)"),
        P("\"Miscellaneous Proceeds\" means any compensation, settlement, award of damages or proceeds paid by a third party (other than insurance proceeds under Section 4) for damage to or destruction "
          "of the Property, condemnation or other taking of the Property, conveyance in lieu of condemnation, or misrepresentation of or omission as to the value or condition of the Property.", lead="(F)"),
        P("\"Successor in Interest of Borrower\" means any party that has taken title to the Property, whether or not that party has assumed Borrower's obligations.", lead="(G)"),
        H("TRANSFER OF RIGHTS IN THE PROPERTY"),
        P("This Security Instrument secures to Lender: (i) repayment of the Loan, including the principal of %s evidenced by the Note, with interest, and all renewals, extensions and modifications of the "
          "Note; (ii) all advances made by Lender under the Construction Draw Agreement, which advances are obligatory; (iii) all sums advanced by Lender under Sections 4 and 7 to protect the Property and "
          "this Security Instrument, with interest at the Note rate; and (iv) the performance of Borrower's covenants and agreements under this Security Instrument and the Note. For this purpose, %s the "
          "following property located in %s, %s, and more particularly described in Exhibit A attached hereto and made a part hereof, which has the address of %s (the \"Property Address\")."
          % (usd(c.principal), grant, c.county or c.need("county"), c.state_name, c.address)),
        P("TOGETHER WITH all improvements now or hereafter erected on the property, all materials delivered to the property for incorporation into the improvements, and all easements, appurtenances and "
          "fixtures now or hereafter a part of the property. All replacements and additions are also covered by this Security Instrument. All of the foregoing is referred to as the \"Property.\""),
        P("BORROWER COVENANTS that Borrower is lawfully seised of the estate hereby conveyed, has the right to grant and convey the Property, and that the Property is unencumbered except for %s. Borrower "
          "warrants specially the title to the Property and will execute such further assurances as may be requisite."
          % ("the Senior Lien described in Section 26 and encumbrances of record approved by Lender" if c.second else "encumbrances of record approved by Lender")),
        H("COVENANTS"),
        P("Borrower shall pay when due the principal of, and interest on, the debt evidenced by the Note and all other sums secured by this Security Instrument. No periodic payments are required. No "
          "offset or claim which Borrower might have against Lender or %s shall relieve Borrower from making payments due under the Note and this Security Instrument." % ROI, lead="1. Payment of Debt."),
        P("All payments and proceeds received by Lender shall be applied first to costs and expenses of collection and protective advances, then to accrued interest, and then to principal.", lead="2. Application of Payments."),
        P("Borrower shall pay when due all taxes, assessments, water and sewer charges, fines and impositions attributable to the Property which can attain priority over this Security Instrument. Borrower "
          "shall promptly discharge any lien which has priority over, or is asserted to have priority over, this Security Instrument, including any mechanics' or materialmen's lien, unless Borrower "
          "(a) agrees in writing to the payment of the obligation secured by the lien in a manner acceptable to Lender, (b) contests the lien in good faith by legal proceedings which operate to prevent "
          "enforcement of the lien, or (c) obtains from the lien holder an agreement satisfactory to Lender subordinating the lien to this Security Instrument. Within 10 days after Lender gives Borrower "
          "notice of such a lien, Borrower shall satisfy it or take one of these actions.", lead="3. Charges; Liens."),
        P("Borrower shall keep the improvements now existing or hereafter erected on the Property insured against loss by fire, vandalism, hazards included within \"extended coverage\" and any other "
          "hazards Lender requires, including coverage while the Property is vacant and under renovation (by vacancy endorsement or builder's risk coverage), in the amounts and for the periods Lender "
          "requires. All policies shall include a standard mortgagee clause naming %s, its successors and/or assigns (ISAOA/ATIMA), as %s mortgagee and loss payee. Lender may hold the policies and "
          "renewal certificates. If Borrower fails to maintain the required coverage, Lender may obtain coverage at Borrower's expense. Amounts disbursed by Lender under this Section 4 become additional "
          "debt secured by this Security Instrument, bear interest at the Note rate from the date of disbursement, and are payable on demand." % (LENDER, c.lien_word), lead="4. Property Insurance."),
        P("In the event of loss, Borrower shall give prompt notice to the carrier and Lender, and Lender may make proof of loss. Insurance proceeds shall be applied to restoration of the Property if "
          "restoration is economically feasible and Lender's security is not lessened, with Lender holding and disbursing the proceeds as work is completed; otherwise the proceeds shall be applied to the "
          "sums secured by this Security Instrument, whether or not then due, with any excess paid to Borrower. If Borrower abandons the Property, or does not respond within 30 days to Lender's notice "
          "that the carrier has offered to settle a claim, Lender may negotiate and settle the claim. Borrower assigns to Lender Borrower's rights to insurance proceeds up to the amount unpaid "
          "under the Note and this Security Instrument."),
        P("Borrower shall not destroy, damage or impair the Property, allow it to deteriorate, or commit waste. Borrower shall cause the renovation of the Property to be carried out by properly licensed "
          "contractors, with all required permits, in a good and workmanlike manner, and in accordance with the Construction Draw Agreement. Lender and its agents may enter and inspect the Property, "
          "including its interior, at reasonable times.", lead="5. Preservation, Maintenance and Protection of the Property; Inspections; Renovation."),
        P("Borrower shall be in default if Borrower, or any person acting at Borrower's direction or with Borrower's knowledge, gave materially false, misleading or inaccurate information to Lender, or "
          "failed to provide material information, in connection with the Loan, including information concerning Borrower's authority, the occupancy of the Property, and the business purpose of the Loan.",
          lead="6. Borrower's Representations."),
        P("If (a) Borrower fails to perform any covenant in this Security Instrument, (b) there is a legal proceeding that might significantly affect Lender's interest in the Property or rights under this "
          "Security Instrument (such as a proceeding in bankruptcy or probate, for condemnation or forfeiture, or for enforcement of a lien which may attain priority over this Security Instrument), or "
          "(c) Borrower or %s abandons the Property or the renovation, then Lender may do and pay for whatever is reasonable or appropriate to protect Lender's interest in the Property, including paying "
          "any sums secured by a prior lien, appearing in court, paying reasonable attorneys' fees, securing the Property (changing locks, boarding windows, winterizing, eliminating code violations), "
          "and completing or stabilizing the renovation. Lender has no duty to take any such action. Any amounts disbursed by Lender under this Section 7 become additional debt of Borrower secured by "
          "this Security Instrument, bear interest at the Note rate from the date of disbursement, and are payable upon notice from Lender requesting payment." % ROI,
          lead="7. Protection of Lender's Interest in the Property."),
        P("All Miscellaneous Proceeds are assigned to and shall be paid to Lender, and shall be applied to restoration of the Property or to the sums secured by this Security Instrument in the same "
          "manner as insurance proceeds under Section 4. Borrower shall be in default if any action or proceeding, civil or criminal, is begun that in Lender's judgment could result in forfeiture of the "
          "Property or other material impairment of Lender's interest.", lead="8. Assignment of Miscellaneous Proceeds; Forfeiture."),
        P("Extension of the time for payment or modification of the sums secured by this Security Instrument shall not release the liability of Borrower or any Successor in Interest of Borrower. Any "
          "forbearance by Lender in exercising any right or remedy, including acceptance of payments from third persons or in amounts less than the amount then due, shall not be a waiver of or preclude "
          "the exercise of any right or remedy.", lead="9. Borrower Not Released; Forbearance by Lender Not a Waiver."),
        P("The covenants and agreements of this Security Instrument bind and benefit the successors and assigns of Lender and bind Borrower and any Successor in Interest of Borrower. If ownership of the "
          "Property becomes vested in a person other than Borrower, Lender may, without notice to Borrower, deal with that successor with reference to this Security Instrument and the debt it secures in "
          "the same manner as with Borrower, without releasing Borrower's liability.", lead="10. Successors and Assigns Bound; Ownership."),
        P("Lender may charge Borrower fees for services performed in connection with Borrower's default and for protecting Lender's interest in the Property, including attorneys' fees and property "
          "inspection and valuation fees. If any interest or other loan charge is finally determined to exceed the limit permitted by Applicable Law, the charge shall be reduced to the permitted limit "
          "and any excess collected shall be refunded or credited to principal.", lead="11. Loan Charges."),
        P("All notices under this Security Instrument must be in writing. Notice to Borrower is given when mailed by first-class mail or actually delivered to Borrower at %s, with a copy to the Property "
          "Address, or to a substitute address Borrower designates by notice to Lender. Notice to Lender is given when actually received by Lender at %s or another address Lender designates. "
          "If a notice required by this Security Instrument is also required by Applicable Law, the Applicable Law requirement satisfies the requirement here."
          % (c.borrower_addr, LENDER_ADDR), lead="12. Notices."),
        P("This Security Instrument is governed by federal law and the laws of %s. If any provision of this Security Instrument or the Note conflicts with Applicable Law, the conflict shall not affect "
          "other provisions which can be given effect without the conflicting provision. The singular includes the plural, and the word \"may\" gives sole discretion without obligation." % c.sx["law"],
          lead="13. Governing Law; Severability; Construction."),
        P("Borrower shall be given one copy of the Note and of this Security Instrument.", lead="14. Borrower's Copy."),
        P("If all or any part of the Property or any interest in it is sold, conveyed or transferred (including by contract for deed%s), the entire sum secured by this Security Instrument is immediately "
          "due and payable, and the settlement agent for that transfer is authorized and directed to pay Lender in full from the proceeds. If the transfer occurs without payment in full, Lender may "
          "invoke any remedy permitted by this Security Instrument without further notice or demand, except as required by Applicable Law."
          % (" or by distribution from the estate" if c.owner_kind == S.ESTATE else " or by transfer of any interest in Borrower" if c.owner_kind in (S.LLC, S.TRUST) else ""),
          lead="15. Transfer of the Property; Due on Sale."),
        P("Borrower shall have the right to have enforcement of this Security Instrument discontinued at any time before the earlier of (a) the period allowed by Applicable Law, or (b) the sale of the "
          "Property%s, if Borrower (i) pays Lender all sums which would then be due under the Note and this Security Instrument as if no acceleration had occurred, (ii) cures any other default, and "
          "(iii) pays all expenses incurred in enforcing this Security Instrument, including reasonable attorneys' fees. This right to reinstate does not apply in the case of acceleration under Section "
          "15 or after the Maturity Date of the Note." % (" under the power of sale or decree" if c.state == "MD" else "" if c.is_mortgage else " under the power of sale"),
          lead="16. Borrower's Right to Reinstate After Acceleration."),
        P("The Note or a partial interest in the Note, together with this Security Instrument, may be sold or assigned one or more times without prior notice to Borrower.", lead="17. Sale of Note."),
        P("Borrower shall not cause or permit the presence, use, disposal, storage or release of any hazardous substances on or in the Property, other than small quantities generally recognized as "
          "appropriate to normal residential construction and maintenance, and shall promptly give Lender written notice of any governmental or private claim, investigation or condition relating to "
          "hazardous substances affecting the Property and take all remedial action required by environmental law. Lead-based paint, asbestos and mold encountered in the renovation shall be handled in "
          "accordance with Applicable Law.", lead="18. Hazardous Substances."),
        H("ADDITIONAL COVENANTS"),
    ] + _remedy(c)
    b.append(P("Upon payment of all sums secured by this Security Instrument, Lender shall %s. Borrower shall pay any recordation costs and any fee permitted by Applicable Law."
               % ("record a satisfaction of this Security Instrument" if c.is_mortgage else "request Trustee to release this Security Instrument, and Trustee shall execute a release"), lead="20. Release."))
    if not c.is_mortgage:
        b.append(P("Lender may at any time, with or without cause, remove Trustee and appoint one or more substitute trustees by an instrument recorded among %s. Without conveyance of the Property, each "
                   "substitute trustee shall succeed to all title, power and duties conferred upon Trustee by this Security Instrument and Applicable Law. Any one of several trustees may act alone." % c.records,
                   lead="21. Substitute Trustee."))
    else:
        b.append(P("Borrower represents that the Property is not the homestead of Borrower or of any member of Borrower's family, is not contiguous to any such homestead, and that Borrower resides at the "
                   "address stated above. If Borrower is a married individual, Borrower's spouse joins in this Security Instrument solely to subject any homestead and marital interest in the Property to "
                   "its lien, and does not thereby become liable on the Note.", lead="21. Homestead."))
    b.append(_advances(c))
    b.append(P("Borrower represents and agrees that the Loan is made for business and commercial purposes, to renovate the Property for resale; that no part of the Loan is for personal, family or household "
               "purposes; and that the Property is vacant and is not and will not be occupied as a residence by Borrower%s while the Loan is outstanding."
               % (" or by any heir or beneficiary of the estate" if c.owner_kind == S.ESTATE else " or by any member, beneficiary or affiliate of Borrower" if c.owner_kind in (S.LLC, S.TRUST) else ""),
               lead="23. Business Purpose; Non-Owner Occupancy."))
    b.append(P(c.capacity or "Borrower executes this Security Instrument in Borrower's own right as the owner of the Property, and represents that no other person holds any interest in it.", lead="24. Capacity of Borrower."))
    b.append(P("The Loan Terms Rider attached as Exhibit B is executed by Borrower, recorded together with this Security Instrument, and incorporated into it. If there is any conflict between the Rider and "
               "the printed text of this Security Instrument, the Rider controls.", lead="25. Riders."))
    if c.second:
        senior = " and ".join("%s (unpaid balance about %s)" % (l["holder"], usd(l["balance"])) for l in c.liens_staying)
        b.append(P("This Security Instrument is subject and subordinate only to the lien held by %s (the \"Senior Lien\"), and to no other lien. Borrower shall pay every sum due under the Senior Lien when "
                   "due and perform every covenant in it; shall not increase its principal, extend it or modify it without Lender's written consent; and shall give Lender a copy of any notice received "
                   "under it within three (3) days. A default under the Senior Lien is a default under this Security Instrument. Lender may, without obligation, pay any sum due under the Senior Lien "
                   "or cure any default under it, and every amount so paid is a protective advance under Section 7. Borrower irrevocably authorizes the holder of the Senior Lien to give Lender, at any "
                   "time, the balance, payment status and payoff of the Senior Lien and a copy of every notice of default or sale." % senior, lead="26. Senior Lien."))
    b += [Note("NOTICE: THE DEBT SECURED HEREBY IS SUBJECT TO CALL IN FULL OR THE TERMS THEREOF BEING MODIFIED IN THE EVENT OF SALE OR CONVEYANCE OF THE PROPERTY CONVEYED."),
          P("BY SIGNING BELOW, Borrower accepts and agrees to the terms and covenants contained in this Security Instrument and in the Loan Terms Rider executed by Borrower and recorded with it. WITNESS "
            "the hand and seal of Borrower."), c.sig_block(), c.ack_owner()]
    if c.state == "MD":
        b += [BREAK, Title("AFFIDAVIT OF CONSIDERATION"),
              P("The undersigned, an authorized officer of %s, the secured party named in the foregoing Deed of Trust, hereby certifies under the penalties of perjury that the consideration recited in the "
                "foregoing Deed of Trust is true and bona fide as therein set forth, and that the actual sum of money advanced at the closing transaction by the secured party was $______________ (closing "
                "costs funded through settlement only; no cash to Borrower), the balance of the principal amount being secured as obligatory advances to be made under the Construction Draw Agreement and "
                "in consideration of the assignment of the renovation contract price as recited in the Note." % LENDER), _lender_sign(),
              # Maryland takes a deed of trust for record only with an attorney's certificate that he prepared it
              # (or a party's own). RKB's is its Maryland attorney's, as on the signed Marmon instrument.
              P(""), H("ATTORNEY CERTIFICATION"),
              P("I HEREBY CERTIFY that the within instrument was prepared by me or under my supervision, and that I am an attorney admitted to practice before the Supreme Court of Maryland."),
              Sign("", ["______________________________", c.attorney.get("name") or c.need("Maryland attorney who certifies the deed of trust"), c.attorney.get("address") or "", "Date: ______________"])]
    elif c.state == "FL":
        b.append(Note("This instrument was prepared by: %s, %s, %s." % (LENDER_SIGNER, LENDER, LENDER_ADDR)))
    else:
        b.append(Note("Prepared by %s, %s, a party to this instrument." % (LENDER, LENDER_ADDR)))
    # Exhibit A is the title company's: the legal description from its commitment, and the deed the Borrower holds under.
    holder = c.borrower if c.owner_kind != S.ESTATE else c.title_fill("name of the owner as on the deed")
    b += [BREAK, Title("EXHIBIT A", "Legal Description"),
          P(c.raw("legal_description") or c.title_fill("Legal description to be inserted by %s from the title commitment" % (c.raw("title_company") or "the title company"))),
          P("The improvements thereon being known as %s. Tax ID No. %s." % (c.address, c.tax_id)),
          P("BEING the same property conveyed to %s by deed dated %s and recorded among %s %s." % (holder, c.day("deed_date") or c.title_fill("deed date"), c.records,
                                                                                               ("in " + c.raw("deed_reference")) if c.raw("deed_reference") else c.sx.get("book") or "in Book [__], Page [__]")),
          BREAK, Title("EXHIBIT B", "LOAN TERMS RIDER · recorded with and incorporated into the %s dated %s" % (c.instrument, c.closing_s)),
          Terms([["Property", c.property_line], ["Borrower", c.borrower_full], ["Lender" + ("" if c.is_mortgage else " / Trustee"), LENDER + ("" if c.is_mortgage else " / " + c.trustee_name)],
                 ["Principal amount", "%s (maximum principal secured, exclusive of interest and protective advances)" % usd(c.principal)]]),
          P(("No interest accrues for the %s (%d) consecutive calendar months beginning on the date the %s is recorded." % (_small(c.free), c.free, c.instrument)) if c.free else
            "There is no interest-free period. Interest accrues from the date the %s is recorded." % c.instrument, lead="1. Interest-free period."),
          P("Simple interest accrues on the %s at %s percent (%s%%) per year, actual/365, until paid in full, before and after maturity, acceleration or judgment.%s"
            % ("unpaid principal after the interest-free period" if c.free else "full principal amount", _small(int(c.rate)), _rate(c.rate),
               (" Minimum interest of %s is payable on any earlier payoff." % usd(c.min_interest)) if c.min_months else ""), lead="2. Interest rate."),
          P("No monthly or other periodic payments of principal or interest are required. References in the %s to periodic payments do not apply." % c.instrument, lead="3. No periodic payments."),
          P("All sums secured are due and payable in full on the earlier of the closing of any sale or transfer of the Property or %s." % c.maturity_s, lead="4. Maturity."),
          P("No late charge is payable.", lead="5. No late charge."),
          P("The %s secures %s advances under the Construction Draw Agreement of even date and protective advances, as provided in Section 22." % (c.instrument, "future" if c.is_mortgage else "obligatory"),
            lead="6. Advances."),
          P("This Rider controls over any conflicting provision of the printed %s." % c.instrument, lead="7. Control."), c.sig_block()]
    if c.state == "MD":
        b.append(c.ack_owner())      # the Rider is recorded with the Deed of Trust and carries the same acknowledgment
    return b


# ------------------------------------------------------------------ 4, 5. the company's papers
def guaranty(c: Ctx) -> List[Dict[str, Any]]:
    return [Title("CONTINUING PERSONAL GUARANTY"),
            Terms([["Date", c.closing_s], ["Guarantor", "%s, individually" % c.guarantor], ["Guarantor's address", c.guarantor_addr], ["Borrower", c.borrower_full], ["Lender", "%s, %s" % (LENDER_FULL, LENDER_ADDR)],
                   ["Loan", "%s Promissory Note and %s-lien %s of even date on %s" % (usd(c.principal), c.lien_word, c.instrument, c.address)], ["Loan No.", c.loan_no]]),
            P("To induce Lender to make the Loan to Borrower, and for other good and valuable consideration, the receipt and sufficiency of which Guarantor acknowledges, Guarantor absolutely, unconditionally "
              "and irrevocably guarantees to Lender the full and punctual payment when due, whether at maturity, by acceleration or otherwise, of every sum now or hereafter owing under the Note, the %s and "
              "the Construction Draw Agreement, including principal, interest, %sprotective advances and costs of collection (the \"Obligations\"), and the performance of every covenant of "
              "Borrower in those documents." % (c.instrument, "the loan fee, " if c.fee else ""), lead="1. Guaranty."),
            P("This is a guaranty of payment and not of collection. It is a continuing guaranty and remains in force until every Obligation has been paid in full. Guarantor's liability is primary and "
              "direct. Lender may enforce this Guaranty against Guarantor without first proceeding against Borrower, foreclosing on the Property or exhausting any other remedy.", lead="2. Nature of the guaranty."),
            P("Guarantor's liability is not released or reduced by any extension, renewal, modification or forbearance of the Obligations; by any release, substitution or impairment of the Property or other "
              "security; by any release of Borrower or any other person; by Borrower's bankruptcy, dissolution or lack of authority; by any defense of Borrower other than payment in full; or by Lender's "
              "failure to give any notice or to perfect or preserve any lien.", lead="3. Guarantor's liability is not affected."),
            P("Guarantor waives presentment, demand, protest, notice of acceptance, notice of default and every other notice; waives any right to require Lender to marshal assets; and, until the Obligations "
              "are paid in full, waives every right of subrogation, reimbursement and contribution against Borrower.", lead="4. Waivers."),
            P("Guarantor represents that Guarantor has a direct financial interest in Borrower and in the Loan, has read the Note and the %s, and has had the opportunity to consult counsel. Guarantor shall "
              "pay Lender's reasonable attorneys' fees and costs of enforcing this Guaranty." % c.instrument, lead="5. Representations; costs."),
            P("If any payment on the Obligations is rescinded or must be returned by Lender, this Guaranty is reinstated as to that payment as though it had not been made.", lead="6. Reinstatement."),
            P("This Guaranty binds Guarantor and Guarantor's heirs and personal representatives and benefits Lender and its successors and assigns. It is governed by the laws of %s. GUARANTOR AND LENDER "
              "KNOWINGLY AND VOLUNTARILY WAIVE TRIAL BY JURY IN ANY ACTION ARISING OUT OF THIS GUARANTY." % c.sx["law"], lead="7. General."),
            P("Notices to Guarantor shall be delivered by hand, by nationally recognized overnight courier, or by certified mail to Guarantor's address stated above, or to another address Guarantor "
              "designates in writing.", lead="8. Notices."),
            Sign("GUARANTOR", ["______________________________ (SEAL)", "%s, individually" % c.guarantor, "Date: ______________"]),
            Notary("%s\nThe foregoing instrument was acknowledged before me this ____ day of ______________, 20___, by %s, individually, who is personally known to me or has produced satisfactory evidence "
                   "of identity.\n\n______________________________\nNotary Public\nMy commission expires: ______________          [NOTARIAL SEAL]" % (c.sx["notary"], c.guarantor)),
            Note("[SIGN ORIGINAL ONLY · DO NOT RECORD · ORIGINAL TO LENDER'S CUSTODIAN]")]


def resolution(c: Ctx) -> List[Dict[str, Any]]:
    # Every member signs, each before a notary. The names come from the operating agreement; until
    # they are entered the paper carries the blank and two signature blocks.
    names = c.members
    sole = len(names) == 1
    who = (names[0] if sole else ", ".join(names[:-1]) + " and " + names[-1]) if names else c.need("member names")
    all_of = "the sole member" if sole else "all of the members"

    def member(head: str, name: str) -> List[Dict[str, Any]]:
        return [Sign(head, ["______________________________", "Printed name: %s" % name, "Sole Member" if sole else "Member", "Date: ______________"]),
                Notary("%s\nThe foregoing instrument was acknowledged before me this ____ day of ______________, 20___, by %s, as %s of %s, who is personally known to me or has produced "
                       "satisfactory evidence of identity.%s" % (c.sx["notary"], name, "the sole member" if sole else "a member", c.borrower, _ACK_TAIL))]
    signers = names or [c.need("member name")] * 2
    blocks: List[Dict[str, Any]] = []
    for n, name in enumerate(signers):
        blocks += member("SOLE MEMBER" if sole else "MEMBERS (ALL OF THEM)" if n == 0 else "MEMBER", name)
    return [Title("AUTHORIZING RESOLUTION AND CERTIFICATE OF AUTHORITY", c.borrower),
            P("The undersigned, %s, being %s of %s (the \"Company\"), certify and resolve as follows:" % (who, all_of, c.borrower_full)),
            P("The Company is duly organized, validly existing and in good standing. Its articles of organization and operating agreement, with every amendment, have been delivered to Lender and are in full "
              "force. Nothing in them restricts the Company's power to borrow or to mortgage or encumber its property.", lead="1. Existence."),
            P("The Company is authorized to borrow from %s on a Promissory Note in the principal amount of %s, to secure it by a %s-lien %s on %s, and to enter into the Construction Draw Agreement, the "
              "Amendment to Customer Agreement and every other document Lender requires for the loan (Loan No. %s)." % (LENDER, usd(c.principal), c.lien_word, c.instrument, c.address, c.loan_no),
              lead="2. The loan is authorized."),
            P("%s, as %s, acting alone, is authorized to sign and deliver those documents on the Company's behalf, in the form that person approves, the signature being conclusive evidence of the approval."
              % (c.signer, c.signer_title), lead="3. Who signs."),
            P("Lender and the title insurer may rely on this certificate until Lender receives written notice of any change, signed by %s." % all_of, lead="4. Reliance.")] + blocks


# ------------------------------------------------------------------ 6 to 9. the owner's statements
def affidavit(c: Ctx) -> List[Dict[str, Any]]:
    auth = {S.ESTATE: "I am the duly appointed Personal Representative of the estate that owns the Property, under letters of administration that are in full force and effect and have not been revoked, "
                      "restricted or limited. A certified copy, dated within 30 days, has been delivered to %s. No will, court order or agreement restricts my power to mortgage or encumber the "
                      "Property, and I am signing this affidavit and the loan documents solely in that capacity.",
            S.TRUST: "I am the currently acting Trustee of the trust that owns the Property. The trust is in existence and gives me the power to mortgage and encumber the Property. A certification of "
                     "trust has been delivered to %s, and I am signing solely as Trustee.",
            S.LLC: "I am the %s of %s, which owns the Property, and I am authorized by resolution of all of its members to sign this affidavit and the loan documents. The Company's organizational "
                   "documents and a current certificate of good standing have been delivered to %%s." % (c.signer_title, c.borrower),
            S.INDIVIDUAL: "I am the owner of the Property. No other person holds any interest in it, and no one else's signature is needed to mortgage it. Evidence of title has been delivered to %s."}[c.owner_kind]
    return [Title("BUSINESS-PURPOSE AND NON-OWNER-OCCUPANCY AFFIDAVIT"),
            Terms([["Property", c.property_line], ["Borrower", c.borrower_full], ["Lender", "%s, ISAOA/ATIMA, %s" % (LENDER, LENDER_ADDR)],
                   ["Loan", "%s Promissory Note and %s-lien %s dated %s" % (usd(c.principal), c.lien_word, c.instrument, c.closing_s)], ["Loan No.", c.loan_no]]),
            P("I, %s, being first duly sworn, state under the penalties of perjury as follows:" % c.signer),
            P(auth % c.title_co, lead="1. Authority."),
            P("The loan is being obtained solely to finance the renovation of the Property so that it can be sold. No part of the loan is for personal, family or household purposes. I understand that the "
              "loan is a business-purpose loan and is not subject to the consumer-credit disclosure requirements or right of rescission that apply to consumer loans secured by a dwelling.", lead="2. Business purpose."),
            P("The Property is vacant. It is not my residence and not the residence of %s, and none of them intends to occupy it. No one holds a lease or any other right "
              "to occupy the Property. The Property will be listed for sale when the renovation is complete."
              % ("any member, manager or affiliate of Borrower" if c.owner_kind == S.LLC else "any owner, heir, beneficiary or member"), lead="3. Non-owner occupancy."),
            P("No loan proceeds are being paid to me or to Borrower. Lender will pay for the renovation work on Borrower's behalf under the Construction Draw Agreement.", lead="4. No cash to Borrower."),
            P("Borrower has contracted with %s for the renovation under a Customer Agreement, as amended on the date of this affidavit, for a contract price of %s. %s has assigned that contract price to "
              "Lender, and Borrower's Promissory Note to Lender replaces Borrower's obligation to pay %s." % (ROI, usd(c.principal), ROI, ROI), lead="5. Renovation contract."),
            P("Neither Borrower nor I, individually, is a debtor in any pending bankruptcy case, and I know of no lawsuit, lien claim or claim of any heir, member or creditor that affects the Property, "
              "except as disclosed in writing to %s." % c.title_co, lead="6. No bankruptcy or litigation."),
            P("I make this affidavit knowing that Lender, %s and the title insurer will rely on it in making and insuring the loan. I will notify Lender in writing immediately if any statement above ceases "
              "to be true before the loan is paid in full." % c.title_co, lead="7. Reliance."),
            c.sig_block(), c.notary(sworn=True)]


def occupancy(c: Ctx) -> List[Dict[str, Any]]:
    return [Title("OCCUPANCY CERTIFICATION", "Non-Owner-Occupied Property"),
            Terms([["Property", c.address], ["Owner / Borrower", c.borrower_full], ["Lender", "%s, its successors and/or assigns" % LENDER], ["Loan No.", c.loan_no], ["Closing date", c.closing_s]]),
            P("I, the undersigned Owner / Borrower, certify to Lender as follows:"),
            # A company never lived in the house: its statement runs from the day it took title.
            L([("No person has lived at the Property since Borrower acquired it on %s." % (c.day("deed_date") or c.title_fill("date of Borrower's deed"))) if c.owner_kind == S.LLC else
               "No owner lives at the Property. The last person to live there moved out on or about ____________________ (date).",
               "The Property is vacant and is not the principal residence of any owner. No one is living at the Property, and no one has a lease or any other right to occupy it.",
               "No owner intends to move back into the Property. The Property is being repaired so that it can be listed and sold.",
               "The owner's current mailing address is: %s." % c.borrower_addr,
               "I will tell Lender in writing if anyone moves into the Property before the loan is paid in full."]),
            P("I understand that Lender is relying on this certification in making the loan. The statements above are true to the best of my knowledge."),
            c.sig_block(seal=False), c.notary(sworn=True)]


def correction(c: Ctx) -> List[Dict[str, Any]]:
    return [Title("CORRECTION AGREEMENT AND LIMITED POWER OF ATTORNEY", "For Clerical and Non-Material Closing Corrections"),
            Terms([["Property", c.address], ["Owner / Borrower", c.borrower_full], ["Agent", c.title_co], ["Lender", LENDER], ["Closing date", c.closing_s]]),
            P("The undersigned owner and/or borrower (\"Principal\") agrees to cooperate in correcting typographical, clerical, scrivener's, recording, indexing, formatting or other non-material errors or "
              "omissions discovered in the documents executed in connection with the above-referenced transaction."),
            P("For that limited purpose only, Principal appoints Agent, and its authorized employees or closing representatives, as Principal's attorney-in-fact solely to correct, complete, initial or "
              "execute documents to cure such non-material errors or omissions, including documents required by a recorder, title insurer or lender to place the closing documents in recordable or "
              "administratively complete form."),
            P("This authority may not be used to change any material or economic term of the transaction. Without Principal's separate written consent, this Limited Power of Attorney may not be used to "
              "increase the interest rate, extend or shorten the loan term, increase the principal balance, add a new fee or charge, change the property being encumbered, alter vesting or ownership, change "
              "the identity of a borrower or obligor, or otherwise modify the parties' substantive rights or obligations."),
            P("If this Limited Power of Attorney is exercised, Principal shall be provided a copy of each document corrected, completed, initialed or executed under this authority within a reasonable time "
              "after the correction is made. This Limited Power of Attorney automatically terminates one hundred twenty (120) days after the Closing Date. It does not authorize the attorney-in-fact to "
              "receive funds on behalf of Principal or to enter into a new transaction."),
            c.sig_block(seal=False), Sign("WITNESSES", ["______________________________", "______________________________"]), c.notary()]


def acknowledgment(c: Ctx) -> List[Dict[str, Any]]:
    terms = ("no interest accrues for %s (%d) calendar months after the %s is recorded; after that, simple interest accrues at %s%% per year" % (_small(c.free), c.free, c.instrument, _rate(c.rate))) if c.free \
        else ("simple interest accrues at %s%% per year on the full principal from the date the %s is recorded%s" % (_rate(c.rate), c.instrument, ", with minimum interest of %s" % usd(c.min_interest) if c.min_months else ""))
    return [Title("ACKNOWLEDGMENT OF PAYMENT TERMS, LISTING COVENANTS AND LATE FEE WAIVER"),
            Terms([["Property", c.address], ["Owner / Borrower", c.borrower_full], ["Contractor", "%s (Customer Agreement, as amended %s)" % (ROI, c.closing_s)], ["Lender", LENDER], ["Date", c.closing_s]]),
            H("PAYMENT TERMS"),
            P("The renovation contract price under the Customer Agreement is %s. %s has assigned that contract price to Lender. The Owner's only obligation to pay for the renovation is the %s Promissory "
              "Note to Lender, secured by a %s on the Property. The Owner owes nothing directly to %s for the renovation." % (usd(c.principal), ROI, usd(c.principal), c.instrument, ROI), lead="1."),
            P("Under the Note: %s; %s; no monthly payments are due; and all amounts are due at the earlier of the sale or transfer of the Property or %s. No late charge "
              "is payable under the Note." % (terms, ("a loan fee of %s is earned at closing" % usd(c.fee)) if c.fee else "no loan fee or points are payable by the Owner", c.maturity_s), lead="2."),
            P("Any late fee, interest or other charge under the original Customer Agreement, including any per-30-day late fee and any 180-day payment term, is waived in full and deleted. No such fee will "
              "be charged to the Owner or collected from the sale proceeds.", lead="3."),
            H("LISTING AND SALE COVENANTS"),
            P("The Owner will list the Property for sale on the Multiple Listing Service through a licensed real estate broker within ten (10) business days after the renovation is complete, and will keep "
              "it continuously listed until it is sold.", lead="4."),
            P("The Owner will not reject a bona fide offer at or above the list price recommended by the listing broker without Lender's consent, which will not be unreasonably withheld.", lead="5."),
            P("At settlement of the sale, the Owner directs the settlement agent to pay Lender's payoff in full before any distribution to the Owner.", lead="6."),
            P("By signing below, the Owner/Borrower acknowledges having read, understood and agreed to the payment terms and covenants above, and having received copies of the Note, the %s and the Customer "
              "Agreement amendment." % c.instrument), c.sig_block(seal=False), _roi_sign("ACKNOWLEDGED: ROI BLOCKS LLC")]


# ------------------------------------------------------------------ 10 to 12. the three-party papers
def draw_agreement(c: Ctx) -> List[Dict[str, Any]]:
    expires = c.day("licence_expires", "licence", "expires_on")
    return [Title("CONSTRUCTION DRAW AGREEMENT", "Renovation of %s" % c.address),
            Terms([["Date", c.closing_s], ["Owner / Borrower", "%s (\"Borrower\")" % c.borrower_full], ["Lender", "%s, ISAOA/ATIMA, %s (\"Lender\")" % (LENDER, LENDER_ADDR)],
                   ["Project manager", "%s, %s (\"ROI Blocks\")" % (ROI, ROI_ADDR)], ["Performing contractor", "%s, %s (\"Contractor\")" % (c.contractor, c.lic_no)],
                   # The Contractor's priced scope is not part of this paper: the Borrower signs it, and the Borrower sees the
                   # contract price only. The priced scope, signed by the Contractor alone, goes with Schedule 2.
                   ["Scope of work", "Schedule 1 (the \"Work\")"], ["Contract price", "%s, assigned by ROI Blocks to Lender" % usd(c.principal)],
                   ["Target completion", c.day("completion_date", "schedule", "completion_date") or c.need("completion date")]]),
            H("1. FUNDING AND DIRECTION TO PAY"),
            P("Lender agrees to pay for the Work on Borrower's behalf, in draws against completed work, under this Agreement. Borrower irrevocably authorizes and directs Lender to make those payments to ROI "
              "Blocks, the Contractor, or directly to subcontractors and suppliers, as Lender determines.", lead="1.1"),
            P("Each payment by Lender under this Agreement is an obligatory advance secured by the %s of even date. Borrower's repayment obligation is stated in the Promissory Note of even date (the "
              "\"Note\"). No payment under this Agreement increases the Note principal above %s, other than protective advances under the %s." % (c.instrument, usd(c.principal), c.instrument), lead="1.2"),
            P("No loan proceeds are paid to Borrower, and no renovation funds pass through settlement.", lead="1.3"),
            H("2. CONDITIONS BEFORE THE FIRST DRAW"),
            P("The %s is recorded as a %s lien and Lender holds the title commitment or loan policy insuring it. No work has begun and no materials have been delivered before that recording."
              % (c.instrument, c.lien_word), lead="2.1"),
            P("A dwelling-fire or builder's risk policy is in force with a vacancy and renovation endorsement, naming %s, ISAOA/ATIMA, as %s mortgagee and loss payee." % (LENDER, c.lien_word), lead="2.2"),
            P("Lender holds a certificate of general liability insurance for the Contractor naming %s, ISAOA/ATIMA, as additional insured and referencing the Property, and evidence of workers' compensation "
              "coverage or a written exemption." % LENDER, lead="2.3"),
            P("The Contractor's licence (%s) is verified by Lender, and the Contractor confirms in writing that it will pull all required permits and use licensed electrical, plumbing and HVAC trades.%s"
              % (c.st.get("licence_register", "the state contractor licence"),
                 (" Contractor's licence expires %s. If the Work is not complete by that date, no further draw will be funded until Lender receives evidence of renewal." % expires) if expires else ""), lead="2.4"),
        ] + ([P("The notice of commencement has been recorded after the Mortgage and a certified copy is posted at the Property, as required by §713.13, Florida Statutes.", lead="2.5")] if c.state == "FL" else []) + [
            H("3. DRAW PROCEDURE"),
            P("ROI Blocks submits each draw request to Lender with the Contractor's invoice for completed work measured against Schedule 1, photographs, and signed partial lien releases from the Contractor "
              "and every subcontractor and supplier paid from the previous draw.", lead="3.1"),
            P("Lender funds ninety percent (90%) of each approved draw. The remaining ten percent (10%) is retained by Lender and paid directly to the "
              "Contractor at settlement of the sale of the Property, against final lien releases from the Contractor and from every subcontractor and supplier.", lead="3.2"),
            P("Lender or its inspector may verify the work before funding. Lender will fund approved draws within five (5) business days after approval.", lead="3.3"),
            P("Lender may withhold or reduce a draw if the work is not complete or not in accordance with the scope or applicable codes; if lien releases are missing; if a required permit or inspection is "
              "outstanding; if required insurance is not in force; if a lien or claim has been filed against the Property; or if Borrower or ROI Blocks is in default under the Note, the %s, or this "
              "Agreement." % c.instrument, lead="3.4"),
            H("4. CHANGE ORDERS"),
            P("No change to the scope or price of the Work is effective unless set out in a written change order signed by Borrower, ROI Blocks and Lender. "
              "Cost overruns not covered by a signed change order are the sole responsibility of ROI Blocks. No change order increases the amount Borrower owes under the Note "
              "unless Borrower signs a written modification of the Note."),
            H("5. ROI BLOCKS' UNDERTAKINGS"),
            P("ROI Blocks shall cause the Work to be completed by the target completion date, in a good and workmanlike manner, by properly licensed contractors, and in compliance with all permits and codes.", lead="5.1"),
            P("ROI Blocks shall promptly pay, from the draws it receives, all contractors, subcontractors and suppliers for work and materials furnished, and shall deliver lien releases as required by Section 3.", lead="5.2"),
            P("ROI Blocks shall not record, file or assert any lien, memorandum or claim against the Property, and shall indemnify and hold Borrower and Lender harmless from any lien or claim by any person "
              "claiming through ROI Blocks or the Contractor.", lead="5.3"),
            H("6. DEFAULT; LENDER'S REMEDIES"),
            P("If ROI Blocks or the Contractor abandons the Work, fails to perform, or is in default, Lender may stop funding, may take over and complete or stabilize the Work using another licensed "
              "contractor, and may pay any amounts necessary to protect the Property. Amounts Lender pays to complete or protect the Property are protective advances secured by the %s. In that event "
              "Borrower's principal obligation is determined under the Principal Adjustment provision of the Note." % c.instrument),
            H("7. PAYOFF AT SALE"),
            P("At settlement of the sale of the Property, the settlement agent shall pay Lender the full payoff under the Note and %s before any other distribution to Borrower. Any amounts owed by Lender to "
              "ROI Blocks or the Contractor are Lender's obligation under the %s of even date between Lender and ROI Blocks and are not an additional charge to Borrower." % (c.instrument, ALLOC)),
            H("8. GENERAL"),
            P("This Agreement is a loan document. As between Borrower and Lender, it controls over any conflicting provision of the Customer Agreement. It is governed by the laws of %s and may be signed in "
              "counterparts, including by electronic signature." % c.sx["law"]),
            c.sig_block(seal=False), _roi_sign(), c.contractor_sign(),
            _lender_sign(), BREAK, Title("SCHEDULE 1", "Scope of Work"),
            # No cost figure here: the Contractor's cost and the draw budget are between Lender, ROI Blocks
            # and the Contractor. The Borrower's one number is the contract price, which is the Note (2026-11-20).
            Terms([["Contract price", usd(c.principal)], ["Measured area", "%s sq ft above grade, %s sq ft below grade" % (_num(c.f.get("sqft_above")), _num(c.f.get("sqft_below")))],
                   ["Bedrooms and bathrooms after the Work", "%s bedrooms, %s bathrooms" % (c.f.get("beds") or "[ ]", c.f.get("baths") or "[ ]")]]),
            P("The Work is the work described in ROI Blocks' proposal under the Customer Agreement, for the contract price stated above. The schedule of draws is kept "
              "between Lender, ROI Blocks and the Contractor. It does not change the contract price or what Borrower owes under the Note.")]


def schedule_2(c: Ctx) -> List[Dict[str, Any]]:
    """The draw budget: the one paper that states the Contractor's cost. It is between RKB, ROI Blocks
    and the Contractor, and is never signed by, shown to or sent with the papers of the Borrower."""
    m = c.money
    cost = round(m["work"] + m["contingency"], 2)
    draws = round(cost * E.FUNDED_SHARE, 2)
    address = c.raw("contractor_address", "licence", "address")
    return [Title("SCHEDULE 2", "Draw Budget · Construction Draw Agreement for %s" % c.address),
            Note("NOT A BORROWER DOCUMENT · Between Lender, ROI Blocks and the Contractor only"),
            Terms([["Date", c.closing_s], ["Property", c.property_line], ["Lender", "%s, %s" % (LENDER, LENDER_ADDR)], ["Project manager", "%s, %s" % (ROI, ROI_ADDR)],
                   ["Contractor", "%s, %s%s" % (c.contractor, (address + ", ") if address else "", c.lic_no)]]),
            P("This Schedule 2 is the schedule of draws referred to in Schedule 1 of the Construction Draw Agreement of even date. It states the Contractor's cost of the Work and the draw budget. "
              "The scope of work with the Contractor's prices, signed by the Contractor, is attached to it."),
            Terms([["Contractor's cost of the Work", usd(cost) + (", including contingency of %s" % usd(m["contingency"]) if m["contingency"] else "")],
                   ["Draws", "%d%% of the Contractor's cost of completed work, up to %s" % (round(100 * E.FUNDED_SHARE), usd(draws))],
                   ["Retainage", "%d%% = %s, paid to the Contractor at the sale of the Property, against final lien releases" % (round(100 * (1 - E.FUNDED_SHARE)), usd(cost - draws))]]),
            P("Draws and the retainage are made as provided in Section 3 of the Construction Draw Agreement, measured on the Contractor's cost of the completed work. Any contingency is released only "
              "on Lender's prior written approval. This Schedule 2 is signed by Lender, ROI Blocks and the "
              "Contractor only. It is not a borrower document, is not signed by or delivered to the Borrower, and does not change the contract price or what the Borrower owes under the Note."),
            _lender_sign(), _roi_sign(), c.contractor_sign()] + \
        ([] if (c.items.get("scope") or {}).get("status") == C.OK else [Note("[The attached scope of work must be signed by %s before closing.]" % (c.contractor_person or "the Contractor"))])


def _num(v: Any) -> str:
    return "{:,.0f}".format(E._n(v))


def amendment(c: Ctx) -> List[Dict[str, Any]]:
    b = [Title("AMENDMENT TO CUSTOMER AGREEMENT", c.address)]
    signed_on = c.day("contract_date") or c.need("date of Customer Agreement")
    # Maryland's home-improvement notice and three-day right to cancel are for a person's own contract.
    # A business-purpose loan to a company carries no rescission language at all (21st St, 23 Nov 2026).
    if c.state == "MD" and c.owner_kind != S.LLC:
        b += [P("THIS CONTRACT CREATES A MORTGAGE OR LIEN AGAINST YOUR PROPERTY TO SECURE PAYMENT AND MAY CAUSE A LOSS OF YOUR PROPERTY IF YOU FAIL TO PAY THE AMOUNT AGREED UPON. YOU HAVE THE RIGHT TO "
                "CONSULT AN ATTORNEY. YOU HAVE THE RIGHT TO RESCIND THIS CONTRACT WITHIN 3 BUSINESS DAYS AFTER THE DATE YOU SIGN IT BY NOTIFYING THE CONTRACTOR IN WRITING THAT YOU ARE RESCINDING THE "
                "CONTRACT.", lead="NOTICE:"),
              P("Owner's initials acknowledging this notice: ________   Date: ______________"),
              P("Written notice of rescission may be given to %s, %s, with a copy to %s, %s, and to %s. Lender will not record the Deed of Trust or make any advance until the rescission period has expired "
                "without a rescission." % (ROI, ROI_ADDR, LENDER, LENDER_ADDR, c.title_co))]
    body = c.sx.get("lic_body")
    b += [P("This Amendment is made %s between %s, %s (\"ROI Blocks\"), and %s (\"Owner\"), and amends the Customer Agreement between them dated %s (the \"Agreement\"). %s (\"Lender\") joins to consent and as an "
            "intended third-party beneficiary." % (c.closing_s, ROI, ROI_ADDR, c.borrower_full, signed_on, LENDER)),
          H("AMENDMENTS"),
          P("Every reference in the Agreement to the property is to %s. The owner and customer under the Agreement is %s." % (c.property_line, c.borrower_full), lead="1. Property and parties."),
          # ROI Blocks' Customer Agreement is its signed proposal: one paper, one date.
          P("The contract price for the work is %s, as stated in ROI Blocks' proposal dated %s." % (usd(c.principal), signed_on), lead="2. Contract price."),
          P("ROI Blocks has assigned the contract price to Lender. Owner's obligation to pay the contract price is satisfied in full by Owner's execution and delivery to Lender of a Promissory Note for %s "
            "secured by a %s on the Property. ROI Blocks releases Owner from any obligation to pay ROI Blocks directly, and shall look solely to Lender for payment under the %s of even date between ROI "
            "Blocks and Lender." % (usd(c.principal), c.instrument, ALLOC), lead="3. Payment."),
          P("All provisions of the Agreement imposing late fees, interest, a 180-day payment term, or any fee measured from the recording of a lien are deleted.", lead="4. Fees deleted."),
          P("Sections %s of the Agreement are deleted in their entirety. Every provision of the Agreement granting ROI Blocks a lien on, security interest in, or right to record against the Property, barring "
            "other financing, or requiring Owner's consent to such a lien, is deleted. ROI Blocks shall not record or file any lien, memorandum or claim against the Property. ROI Blocks consents to the "
            "%s-priority %s in favor of Lender." % (ROI_LIEN_SECTIONS, c.lien_word, c.instrument), lead="5. Lien provisions deleted."),
          P(("The work shall be performed by %s, %s licence No. %s, or another contractor licensed by %s and approved in writing by Lender, under all required %s permits."
             % (c.contractor, body[4:] if body.startswith("the ") else body, c.licence, body, c.county or c.state_name)) if body else
            ("The work shall be performed by %s, %s no. %s, or another contractor licensed in %s and approved in writing by Lender, under all required permits."
             % (c.contractor, c.st.get("licence_register", "contractor licence"), c.licence, c.state_name)), lead="6. Contractor and licensing."),
          P("The Agreement is governed by the laws of %s. Any arbitration clause, including Section %s of the Agreement, and any reference to the law of another country or state, is deleted. Any dispute "
            "between Owner and ROI Blocks shall be resolved in %s. Nothing in the Agreement limits Lender's rights under the Note or %s, including foreclosure."
            % (c.sx["law"], ROI_ARBITRATION_SECTION, c.sx["courts"], c.instrument), lead="7. Governing law and disputes."),
          P("The work shall be funded under the Construction Draw Agreement of even date among Owner, ROI Blocks and Lender, which controls over the Agreement on funding, draws, change orders and payment.",
            lead="8. Construction Draw Agreement."),
          P("Except as amended here, the Agreement remains in full force. If there is any conflict between this Amendment and the Agreement, this Amendment controls.", lead="9. Ratification."),
          Sign("OWNER: %s" % c.borrower.upper(), ["By: ______________________________", c.signer, c.signer_title, "Date: ______________"]), _roi_sign(), _lender_sign("CONSENTED TO")]
    return b


def assignment(c: Ctx) -> List[Dict[str, Any]]:
    person = c.contractor_person
    return [Title("ASSIGNMENT OF CONTRACT RECEIVABLE, RELEASE AND LIEN COVENANT", c.address),
            P("This Assignment is made %s by %s (\"Assignor\") to %s (\"Assignee\"), with the joinder of %s, %s (\"Contractor\")." % (c.closing_s, ROI, LENDER_FULL, c.contractor, c.lic_no)),
            H("RECITALS"),
            P("Under a Customer Agreement, as amended on the date of this Assignment (the \"Customer Agreement\"), %s (\"Owner\") is obligated to pay Assignor a contract price of %s (the \"Receivable\") for "
              "the renovation of %s (the \"Property\")." % (c.borrower_full, usd(c.principal), c.address), lead="A."),
            P("Assignee is funding the renovation under a Construction Draw Agreement of even date and has agreed with Assignor on the payment of Assignor's compensation under the %s of even date (the "
              "\"Allocation Agreement\"). Owner is delivering to Assignee a Promissory Note for %s secured by a %s on the Property in substitution for the Receivable."
              % (ALLOC, usd(c.principal), c.instrument), lead="B."),
            H("ASSIGNMENT AND RELEASE"),
            P("For value received, consisting of Assignee's funding undertaking and the payments provided in the Allocation Agreement, the receipt and sufficiency of which Assignor acknowledges, Assignor "
              "assigns, transfers and sets over to Assignee all of Assignor's right, title and interest in the Receivable and in all rights and remedies securing or relating to the Receivable, including "
              "any right to claim a lien on the Property.", lead="1."),
            P("Assignor represents that the Receivable is owed in full, has not been paid, assigned, pledged or encumbered, and is not subject to any defense or setoff known to Assignor.", lead="2."),
            P("Effective upon Owner's delivery of the Note and %s to Assignee, Assignor releases Owner from any obligation to pay the Receivable to Assignor and acknowledges that the Note replaces it."
              % c.instrument, lead="3."),
            H("LIEN COVENANTS"),
            P("Assignor shall not record, file or assert any lien, memorandum or claim against the Property. Assignor shall indemnify, defend and hold Assignee and Owner harmless from any lien or claim "
              "asserted by any contractor, subcontractor or supplier claiming through Assignor.", lead="4."),
            P("Contractor joins to acknowledge that it is performing the work under contract with Assignor and is paid through draws funded by Assignee. Contractor confirms that, as of the date the %s is "
              "recorded, it has performed no work and delivered no materials to the Property. Contractor shall deliver a partial lien release with each draw for all work paid to date, and shall give "
              "Assignee at least thirty (30) days' written notice and an opportunity to cure before filing or recording any lien claim against the Property. %s" % (c.instrument, c.sx["lien_law"]), lead="5."),
            P("This Assignment is governed by the laws of %s and binds and benefits the parties' successors and assigns." % c.sx["law"], lead="6."),
            _roi_sign("ASSIGNOR: ROI BLOCKS LLC"), c.ack_company(ROI_SIGNER, ROI_TITLE, ROI),
            c.contractor_sign(),
            # A contractor who holds the licence in his or her own name signs and is acknowledged as a person, with no company title.
            c.ack_person(person) if person else c.ack_company("______________________", "______________________", c.contractor),
            _lender_sign("ACCEPTED: ASSIGNEE"),
            Sign("ACKNOWLEDGED BY OWNER", ["______________________________", c.signer, "Date: ______________"]) if c.owner_kind == S.INDIVIDUAL else
            Sign("ACKNOWLEDGED BY OWNER: %s" % c.borrower.upper(), ["By: ______________________________", c.signer, c.signer_title, "Date: ______________"])]


# ------------------------------------------------------------------ 13. to the title company
def instructions(c: Ctx, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    record = {"MD": "Record among %s: the Deed of Trust with Exhibits A and B. Prepare the Land Instrument Intake Sheet and collect recordation tax on the full principal amount."
                    " No deed is to be recorded unless Lender has agreed in writing.",
              "VA": "Record among %s: the Credit Line Deed of Trust with Exhibits A and B. Collect recordation tax on the maximum amount stated on its face. Lender's address must appear on the instrument.",
              "DC": "Record with %s: the Deed of Trust with Exhibits A and B, with the security-interest recordation tax return and any exemption affidavit the Property qualifies for.",
              "FL": "Record in %s, in this order and no other: (1) the Mortgage with Exhibits A and B, with documentary stamp tax and non-recurring intangible tax paid on the principal amount; "
                    "(2) the Notice of Commencement. No construction money moves and no work starts before both are recorded."}.get(c.state, "Record among %s the security instrument with Exhibits A and B.") % c.records
    # What the title company has signed at its table. The commitment is signed before; Schedule 2 and the
    # Allocation Agreement are between RKB, ROI Blocks and the Contractor and never go to the title company.
    signed = [d for d in docs if d["key"] not in ("instructions", "commitment_letter") and d.get("audience") != NOT_BORROWER]

    def line(d: Dict[str, Any]) -> str:
        if d["key"] == "security" and c.state == "MD":
            return "%s: %s. Attorney Certification signed by %s; Affidavit of Consideration signed by Lender." % (d["title"], d["signers"], c.attorney.get("name") or "Lender's Maryland attorney")
        if d["key"] == "assignment":
            return "Assignment of Contract Receivable, Release and Lien Covenant: %s" % d["signers"]
        return "%s: %s" % (d["title"], d["signers"])
    # What the notary has the Borrower initial: the terms a borrower most often says later he was not told.
    points = (["no interest accrues for the first %s (%d) months after the %s is recorded, and after that interest accrues at %s%% per year on the unpaid principal until it is paid"
               % (_small(c.free), c.free, c.instrument, _rate(c.rate))] if c.free else
              ["interest accrues on the full %s from the date the %s is recorded, whether or not advanced" % (usd(c.principal), c.instrument)]) + \
        (["the minimum interest is %s" % usd(c.min_interest)] if c.min_months else []) + \
        (["%s personally guarantees the whole loan" % c.guarantor] if c.owner_kind == S.LLC else [])
    lettered = ["(%s) %s: ______" % ("abc"[n], p) for n, p in enumerate(points)]
    initials = (" The notary is also to confirm that the Borrower understands each of the following, and the Borrower initials each point: %s."
                % ("; ".join(lettered[:-1]) + ("; and " if len(lettered) > 1 else "") + lettered[-1])) if lettered else ""
    recheck = (" Lender has re-verified on %s, no earlier than the day before closing, that %s is active." % (c.sx["lic_site"], c.lic_no)) if c.sx.get("lic_site") else ""
    return [Title("LENDER CLOSING INSTRUCTIONS", "%s to %s · %s" % (LENDER, c.title_co, c.closing_s)),
            P("Lender's name must appear in every document, in the commitment and on the policy as \"%s\", a New York corporation. Any other spelling, abbreviation or entity form is to be corrected before "
              "closing." % LENDER, lead="Please note:"),
            Terms([["Lender", "%s, %s" % (LENDER_FULL, LENDER_ADDR)], ["Settlement agent", c.title_co], ["Loan number", c.loan_no], ["Borrower", "%s, %s" % (c.borrower_full, c.borrower_addr)],
                   ["Guarantor", ("%s, individually" % c.guarantor) if c.owner_kind == S.LLC else ""], ["Property", c.property_line], ["Note principal", usd(c.principal)],
                   ["Interest", c.terms_short.replace("this Security Instrument", "the " + c.instrument)], ["Loan fee", c.fee_line],
                   ["Note date / maturity", "%s / earlier of sale or %s" % (c.closing_s, c.maturity_s)],
                   ["Lien position", ("Second lien, behind %s only." % " and ".join(l["holder"] for l in c.liens_staying)) if c.second else "First lien. No other lien may remain."],
                   ["Trustee", "" if c.is_mortgage else c.trustee]]),
            H("1. CONDITIONS BEFORE SIGNING"),
            L(["The title commitment is updated and re-issued with the proposed insured reading \"%s, its successors and/or assigns\", in an amount not less than %s, insuring a %s lien, with every "
               "Schedule B-I requirement satisfied and title brought forward to the closing date with no new liens, judgments, mechanics' lien notices or conveyances." % (LENDER, usd(c.principal), c.lien_word),
               "The construction-loan endorsement (ALTA 32 family) is committed in writing. Because Lender disburses directly and not through your office, confirm which form the insurer will issue and "
               "what it requires of Lender.",
               "Judgment, tax-lien and bankruptcy searches are run on the day of closing against every signer%s, with the results sent to Lender before funding. The owner's affidavit is signed."
               % (" and against the company" if c.owner_kind == S.LLC else ""),
               "Taxes, water and municipal charges are paid or collected on the settlement statement. There is no tax sale and no tax-sale certificate outstanding.",
               ("Payoff letters good through the closing date are in the file for: %s. Each is paid in full and released." % ", ".join(l["holder"] for l in c.liens_paid)) if c.liens_paid else "",
               ("The senior lien of %s remains. Its current statement is in the file and it is not in default." % " and ".join(l["holder"] for l in c.liens_staying)) if c.second else "",
               "Lender has confirmed in writing that it holds the signed scope, the insurance, the contractor's licence and insurance, and that no work has begun and no materials have been delivered." + recheck]),
            H("2. DOCUMENTS TO BE SIGNED"),
            L([line(d) for d in signed]),
            P("Use only the execution versions sent by Lender. The Note%s are signed in wet ink; the originals go to Lender's custodian and are not recorded." % (" and the Guaranty" if c.owner_kind == S.LLC else "")),
            H("3. SIGNING REQUIREMENTS"),
            L(["Every signer must sign personally. No power of attorney may be used for the Note%s." % (", the %s or the Guaranty" % c.instrument if c.owner_kind == S.LLC else " or the %s" % c.instrument),
               "The notary is to confirm before signing that the Borrower has read, or had read to him or her, the Note and the %s, and understands the principal amount of %s, the interest%s, "
               "and that the whole amount is due at the sale or at maturity.%s" % (c.instrument, usd(c.principal), (", the loan fee of %s" % usd(c.fee)) if c.fee else "", initials),
               "The Borrower must be given a copy of every document signed.",
               "The Social Security number, date of birth and government-issued photo identification of every signer%s go directly to Lender's attorney. They are not retained in the settlement file "
               "and are not sent to Lender by email." % (" and of the guarantor" if c.owner_kind == S.LLC else "")]),
            H("4. DISBURSEMENT"),
            # The title company once prepared this file for the amount RKB funds, with a lender fee charged to
            # the Borrower (21st St, 24 Nov 2026). The statement follows the Note, and the Borrower brings nothing.
            L(["The loan amount on the settlement statement, in the title commitment and on the policy is the Note principal of %s. Recordation tax is collected on that amount." % usd(c.principal),
               "No loan proceeds are to be paid to the Borrower. The final settlement statement must show $0.00 proceeds to the Borrower.",
               "" if c.fee else "No lender loan fee, origination fee or points are to appear on the settlement statement, and nothing is due from the Borrower at closing: the closing costs are paid from Lender's wire.",
               "Lender funds through settlement only the closing costs%s shown on the final settlement statement approved by Lender in writing. Provide the final statement at least one business day before "
               "closing." % (" and payoffs" if c.liens_paid else ""),
               "Lender retains and disburses the renovation funds itself, directly, after closing. Confirm in writing that you will act on no instruction from the Borrower, %s or the Contractor to release "
               "any sum on this file." % ROI,
               "Lender wires only after approving the final settlement statement and confirming wire instructions by telephone with your office. Wire instructions received by email are not relied upon "
               "without that call."]),
            H("5. INSURANCE"),
            P("A dwelling-fire or builder's risk policy must be in force on the Property before closing, in the Borrower's name, naming %s, both as %s mortgagee under a standard mortgagee clause and as "
              "lender's loss payee on a single policy, permitting vacancy and renovation, with a term running to the Maturity Date and the premium paid. Lender requires the declarations page and the "
              "mortgagee endorsement, not a certificate alone." % (C.LENDER_CLAUSE, c.lien_word)),
            H("6. RECORDING AND POST-CLOSING"),
            L([record,
               "Confirm the recording to Lender by email on the day it occurs, with the recording reference. No advance will be made, and no work may start, before Lender has that confirmation.",
               ("Record the request for notice of sale so that Lender is told of any foreclosure of the senior lien." if c.second and c.state == "MD" else ""),
               "Within two business days, send Lender scanned copies of all signed documents, and overnight the original Note%s to Lender's custodian: %s." % (" and original Guaranty" if c.owner_kind == S.LLC else "", CUSTODIAN),
               "Send the recorded %s and the final loan policy, with the construction-loan endorsement, to the custodian when available." % c.instrument]),
            P("Please confirm receipt and acceptance of these instructions in writing before closing. Any condition in these instructions may be waived only by Lender in writing. If any instruction here "
              "conflicts with the title commitment or with any other instruction you hold, do not close: contact Lender first."),
            _lender_sign(), Sign("ACCEPTED: %s" % c.title_co.upper(), ["By: ______________________________", "Printed name: ______________________", "Title: ______________________", "Date: ______________"])]


# ------------------------------------------------------------------ the extra papers some deals need
def senior_authorization(c: Ctx) -> List[Dict[str, Any]]:
    rows = [["Senior lender", l["holder"]] for l in c.liens_staying]
    return [Title("AUTHORIZATION TO RELEASE MORTGAGE INFORMATION", "and Notice of Junior Lien"),
            Terms(rows + [["Property", c.address], ["Borrower", c.borrower_full], ["Junior lender", "%s, %s" % (LENDER, LENDER_ADDR)], ["Loan No.", c.loan_no]]),
            P("The undersigned borrower authorizes and directs the senior lender named above, and its servicer, to give %s, at any time until this authorization is withdrawn in writing by %s, the unpaid "
              "balance, the payment status, a payoff statement and a reinstatement figure for the loan secured on the Property, and a copy of every notice of default, acceleration or sale." % (LENDER, LENDER)),
            P("%s holds a %s on the Property that is junior to the senior lender's lien. The senior lender is asked to send to %s, at the address above, a copy of any notice of default or of sale that it "
              "sends to the borrower." % (LENDER, c.instrument, LENDER)),
            P("This authorization has no expiry date and remains in force until the loan from %s is paid in full. A copy has the same force as the original." % LENDER),
            c.sig_block(seal=False)]


def trust_certificate(c: Ctx) -> List[Dict[str, Any]]:
    return [Title("CERTIFICATION OF TRUST"),
            Terms([["Trust", c.borrower], ["Trustee signing", c.signer], ["Property", c.address], ["Lender", LENDER], ["Loan No.", c.loan_no]]),
            P("The undersigned Trustee certifies to Lender and to the title insurer as follows:"),
            L(["The trust named above exists, has not been revoked, modified or amended in any manner that would cause these statements to be incorrect, and holds title to the Property.",
               "The undersigned is the currently acting Trustee and is the only signature required to bind the trust.",
               "The trust instrument gives the Trustee the power to borrow money and to mortgage and encumber real property of the trust, including the Property, without the consent of any beneficiary or other person.",
               "Whether the trust is revocable, and the name of any person holding the power to revoke it: ________________________________.",
               "The trust's taxpayer identification number has been given to the settlement agent."]),
            P("Lender may rely on this certification without reviewing the trust instrument. Excerpts of the trust instrument naming the Trustee and stating the Trustee's powers are attached."),
            c.sig_block(seal=False), c.notary(sworn=True)]


def notice_of_commencement(c: Ctx) -> List[Dict[str, Any]]:
    return [Title("NOTICE OF COMMENCEMENT", "§713.13, Florida Statutes · to be recorded AFTER the Mortgage and before any work"),
            P("The undersigned hereby gives notice that improvement will be made to certain real property, and in accordance with Chapter 713, Florida Statutes, the following information is provided in "
              "this Notice of Commencement."),
            Terms([["1. Description of property", "See the legal description in the Mortgage of even date; street address %s" % c.address],
                   ["2. General description of improvement", "Interior and exterior renovation of a single-family dwelling"],
                   ["3. Owner", "%s, %s. Interest in property: fee simple." % (c.borrower_full, c.borrower_addr)], ["4. Contractor", c.contractor], ["5. Surety", "None"],
                   ["6. Lender", "%s, %s" % (LENDER, LENDER_ADDR)],
                   ["7. Persons designated by Owner to receive notices", "%s, %s" % (LENDER, LENDER_ADDR)],
                   ["8. Expiration date", "One (1) year from the date of recording, unless a different date is specified: ______________"]]),
            P("WARNING TO OWNER: ANY PAYMENTS MADE BY THE OWNER AFTER THE EXPIRATION OF THE NOTICE OF COMMENCEMENT ARE CONSIDERED IMPROPER PAYMENTS UNDER CHAPTER 713, PART I, SECTION 713.13, FLORIDA STATUTES, "
              "AND CAN RESULT IN YOUR PAYING TWICE FOR IMPROVEMENTS TO YOUR PROPERTY. A NOTICE OF COMMENCEMENT MUST BE RECORDED AND POSTED ON THE JOB SITE BEFORE THE FIRST INSPECTION. IF YOU INTEND TO "
              "OBTAIN FINANCING, CONSULT WITH YOUR LENDER OR AN ATTORNEY BEFORE COMMENCING WORK OR RECORDING YOUR NOTICE OF COMMENCEMENT."),
            c.sig_block(seal=False), c.notary(sworn=True)]


# ------------------------------------------------------------------ the set
def build(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The whole set for this file: which documents, in what order, written from the file."""
    c = Ctx(doc)
    basis = "RKB's signed Maryland set (Marmon, 30 Oct 2026)" if c.state == "MD" else \
        ("Written from %s's statutes as read on 20 Nov 2026, on the frame of RKB's Maryland set. Not yet used at a closing." % c.state_name if c.state in ("VA", "DC", "FL") else
         "Written on the frame of RKB's Maryland set, with %s's wording from the state sheet Claude wrote and RKB approved. Not yet used at a closing." % c.state_name)
    plan: List[Any] = [("commitment_letter", "Conditional loan commitment", "Owner%s; ROI Blocks and the contractor acknowledge" % (", guarantor" if c.owner_kind == S.LLC else ""), commitment, True),
                       ("note", "Promissory note", "Owner. Original to RKB's custodian; not recorded", note, False),
                       ("security", "%s with loan terms rider" % c.instrument, "Owner, notarized. Recorded", security, False)]
    if c.owner_kind == S.LLC:
        plan += [("guaranty", "Continuing personal guaranty", "Guarantor, personally, notarized", guaranty, False),
                 ("resolution", "Authorizing resolution", "Every member of the company, notarized", resolution, False)]
    if c.owner_kind == S.TRUST:
        plan.append(("trust_certificate", "Certification of trust", "Trustee, sworn", trust_certificate, False))
    plan += [("affidavit", "Business-purpose and non-owner-occupancy affidavit", "Owner, sworn before a notary", affidavit, False),
             ("occupancy", "Occupancy certification", "Owner, notarized", occupancy, False),
             ("correction", "Correction agreement and limited power of attorney", "Owner, two witnesses and a notary", correction, False),
             ("acknowledgment", "Acknowledgment of payment terms and listing covenants", "Owner and ROI Blocks", acknowledgment, False),
             ("draw_agreement", "Construction draw agreement with Schedule 1", "Owner, ROI Blocks, contractor and RKB", draw_agreement, False),
             ("amendment", "Amendment to ROI Blocks customer agreement", "Owner and ROI Blocks; RKB consents", amendment, False),
             ("assignment", "Assignment of contract receivable, release and lien covenant",
              "ROI Blocks and Contractor%s, notarized; Owner acknowledges; RKB accepts. Signed before the %s is released for recording." % ((" (%s)" % c.contractor_person) if c.contractor_person else "", c.instrument),
              assignment, False)]
    if c.second:
        plan.append(("senior_authorization", "Authorization to the first mortgage lender", "Owner", senior_authorization, False))
    if c.state == "FL":
        plan.append(("notice_of_commencement", "Notice of commencement", "Owner, sworn. Recorded after the mortgage", notice_of_commencement, False))
    out: List[Dict[str, Any]] = []

    def made(key: str, title: str, signers: str, cc: Ctx, blocks: List[Dict[str, Any]], early: bool = False, audience: Optional[str] = None) -> Dict[str, Any]:
        d = {"key": key, "title": title, "signers": signers, "early": early, "blocks": blocks, "missing": cc.missing, "for_title": cc.for_title, "basis": basis, "version": 1, "foot": cc.foot(), "state": c.state}
        if audience:
            d["audience"] = audience
        return d
    for key, title, signers, fn, early in plan:
        cc = Ctx(doc)
        out.append(made(key, title, signers, cc, fn(cc), early))
    cc = Ctx(doc)
    out.append(made("instructions", "Lender closing instructions", "RKB; the title company accepts", cc, instructions(cc, out)))
    # Last, and apart: the draw budget. It is the only paper with the Contractor's cost on it.
    cc = Ctx(doc)
    out.append(made("schedule_2", "Schedule 2 - Draw budget", "RKB, ROI Blocks and the contractor only. Not the Borrower", cc, schedule_2(cc), audience=NOT_BORROWER))
    return out


def refresh(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Write the set again from the file as it now stands. A document Rakesh Sir has
    corrected by hand keeps his wording (and says its figures may be out of date when the
    facts have changed since); every other one is rewritten. A tick is kept only when the
    rewritten text is exactly what was ticked."""
    old = {d["key"]: d for d in doc.get("documents") or []}
    out = []
    for d in build(doc):
        prev = old.get(d["key"])
        if prev and prev.get("edited"):
            keep = dict(prev)
            keep["missing_now"] = d["missing"]
            keep["behind_the_file"] = prev.get("made_from") != _stamp(doc)
            out.append(keep)
            continue
        d["made_from"] = _stamp(doc)
        if prev:
            d["version"] = prev.get("version", 1) + (0 if prev.get("blocks") == d["blocks"] else 1)
            d["versions"] = prev.get("versions") or []
            if prev.get("blocks") == d["blocks"] and (prev.get("approved") or {}).get("at"):
                d["approved"] = prev["approved"]
        out.append(d)
    return out


def _stamp(doc: Dict[str, Any]) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(doc.get("facts") or {}, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ Word
def to_docx(document: Dict[str, Any]) -> bytes:
    """One document as a Word file: Times New Roman 11, one-inch margins, a terms table
    with ruled cells, numbered sections with bold leads, signature blocks kept together,
    and a footer with the loan number and the page number."""
    import io
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    d = Document()
    for s in d.sections:
        s.top_margin = s.bottom_margin = Inches(1)
        s.left_margin = s.right_margin = Inches(1.1)
    base = d.styles["Normal"]
    base.font.name, base.font.size = "Times New Roman", Pt(11)
    base.element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    base.paragraph_format.space_after = Pt(6)
    base.paragraph_format.line_spacing = 1.08

    def field(run, code: str) -> None:
        for kind, text in (("begin", None), (None, code), ("end", None)):
            if kind:
                el = OxmlElement("w:fldChar")
                el.set(qn("w:fldCharType"), kind)
            else:
                el = OxmlElement("w:instrText")
                el.set(qn("xml:space"), "preserve")
                el.text = text
            run._r.append(el)

    foot = d.sections[0].footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = foot.add_run("%s   ·   %s   ·   Page " % (document.get("foot") or "", document.get("title") or ""))
    r.font.size = Pt(8.5)
    pr = foot.add_run()
    pr.font.size = Pt(8.5)
    field(pr, "PAGE")

    def para(text: str = "", *, bold_lead: Optional[str] = None, align=None, size: Optional[float] = None, bold: bool = False, keep: bool = False, space: Optional[float] = None):
        p = d.add_paragraph()
        if align is not None:
            p.alignment = align
        if bold_lead:
            lr = p.add_run(bold_lead + (" " if text else ""))
            lr.bold = True
        if text:
            run = p.add_run(text)
            run.bold = bold
            if size:
                run.font.size = Pt(size)
        if keep:
            p.paragraph_format.keep_with_next = True
        if space is not None:
            p.paragraph_format.space_after = Pt(space)
        return p

    for b in document.get("blocks") or []:
        t = b.get("t")
        if t == "title":
            para(b["text"], align=WD_ALIGN_PARAGRAPH.CENTER, size=14, bold=True, keep=True, space=2)
            if b.get("sub"):
                para(b["sub"], align=WD_ALIGN_PARAGRAPH.CENTER, size=10, space=10)
        elif t == "terms":
            rows = b.get("rows") or []
            if not rows:
                continue
            tbl = d.add_table(rows=len(rows), cols=2)
            tbl.style = "Table Grid"
            tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
            for i, (k, v) in enumerate(rows):
                c0, c1 = tbl.rows[i].cells
                c0.width, c1.width = Inches(1.9), Inches(4.4)
                c0.paragraphs[0].add_run(str(k)).bold = True
                c1.paragraphs[0].add_run(str(v))
                for cell in (c0, c1):
                    cell.paragraphs[0].paragraph_format.space_after = Pt(2)
                    for run in cell.paragraphs[0].runs:
                        run.font.size = Pt(10)
            para("", space=4)
        elif t == "h":
            para(b["text"], bold=True, keep=True, space=3)
        elif t == "p":
            para(b.get("text") or "", bold_lead=b.get("lead"), align=WD_ALIGN_PARAGRAPH.JUSTIFY)
        elif t == "list":
            for n, item in enumerate(b.get("items") or [], start=1):
                p = para(item, bold_lead="%d." % n, align=WD_ALIGN_PARAGRAPH.JUSTIFY, space=3)
                p.paragraph_format.left_indent = Inches(0.3)
                p.paragraph_format.first_line_indent = Inches(-0.25)
        elif t == "sign":
            para("", space=6)
            para(b.get("party") or "", bold=True, keep=True, space=10)
            lines = b.get("lines") or []
            for i, line in enumerate(lines):
                para(line, keep=i < len(lines) - 1, space=2)
        elif t == "notary":
            para("ACKNOWLEDGMENT", bold=True, keep=True, space=3)
            for line in (b.get("text") or "").split("\n"):
                para(line, space=2)
        elif t == "note":
            para(b["text"], align=WD_ALIGN_PARAGRAPH.CENTER, size=9, space=8)
        elif t == "break":
            d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()
