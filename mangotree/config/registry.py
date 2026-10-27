"""Identity, mailbox and property registries — the deterministic ground truth.

Mirrors docs/06-BUSINESS-CONTEXT-MEMORY.md. Nothing here is guessed at runtime:
resolution tries these tables first and only then falls back to AI, then to a
human review queue.

Key invariants
--------------
* People own addresses; addresses never own themselves. All attribution keys on
  ``person_id`` so Rakesh's Gmail and his ``rakesh@mtreh.com`` send-as alias
  resolve to one person, one identity.
* ``Side`` separates US (RKB) from THEM (external). An email is business-relevant
  only when both sides appear — that single rule implements the "no internal-only
  mail" policy.
* Property aliases carry the street numbers the folder names omit. 904 and 910
  Bayshore are deliberately distinct entries and must never merge.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Optional, Sequence, Set


# --------------------------------------------------------------------------
# people & mailboxes
# --------------------------------------------------------------------------
class Side(str, Enum):
    RKB = "rkb"            # us — the lender
    EXTERNAL = "external"  # counterparties: builder, homeowner, realtor, title, legal


class Org(str, Enum):
    RKB = "RKB Consulting Group"
    ROI_BLOCKS = "ROI Blocks LLC"
    LP_REMODEL = "LP Remodeling (Listing Prophet LLC dba Listing Profit LLC)"
    TITLE = "Title Company"
    HOMEOWNER = "Homeowner"
    REALTOR = "Realtor"
    ARCHITECT = "Architect"
    CONTRACTOR = "Contractor"
    LEGAL = "Legal Counsel"
    ACCOUNTING = "Accounting / CPA"
    FINANCE = "Finance / Mortgage Banking"
    GOVERNMENT = "Government / Tax Authority"
    SERVICER = "Lender / Loan Servicer"


@dataclass(frozen=True)
class Person:
    person_id: str
    display_name: str
    side: Side
    org: Org
    role: str
    addresses: Sequence[str]
    #: addresses this person may *send as* even though the mail lives elsewhere
    send_as: Sequence[str] = field(default_factory=tuple)
    active: bool = True
    notes: str = ""

    @property
    def all_addresses(self) -> List[str]:
        return sorted({a.lower() for a in (*self.addresses, *self.send_as)})


PEOPLE: List[Person] = [
    # ---------------- RKB — us ----------------
    Person(
        person_id="rakesh",
        display_name="Rakesh Bhargava (Rakesh Sir)",
        side=Side.RKB,
        org=Org.RKB,
        role="CEO / Founder — final decision maker",
        addresses=("rakesh@mtreh.com", "rakesh.bhargava@gmail.com"),
        send_as=("rakesh@mtreh.com",),
        notes=(
            "Sends business mail from the Gmail account using the rakesh@mtreh.com "
            "'send mail as' dropdown. Those messages live ONLY in Gmail's SENT label; "
            "Outlook can never return them. Direction is decided folder-first."
        ),
    ),
    Person(
        person_id="jp",
        display_name="Jaspreet Pahwa (JP Sir)",
        side=Side.RKB,
        org=Org.RKB,
        role="Accountant — approval/review authority",
        addresses=("jp@mtreh.com",),
    ),
    Person(
        person_id="manjunath",
        display_name="Manjunath (Manjunath Sir)",
        side=Side.RKB,
        org=Org.RKB,
        role="Civil Work Advisor — verifies work done, approves bills",
        addresses=("manjunath@mtreh.com",),
    ),
    Person(
        person_id="neha",
        display_name="Neha Jha",
        side=Side.RKB,
        org=Org.RKB,
        role="RKB team",
        addresses=("neha@mtreh.com",),
    ),
    # ---------------- ROI Blocks / LP Remodeling — the builder ----------------
    Person(
        person_id="wes",
        display_name="Wes Stone",
        side=Side.EXTERNAL,
        org=Org.ROI_BLOCKS,
        role="Owner/Manager — primary counterparty (also contractor via LP Remodeling)",
        addresses=("wes@roiblocks.com", "wes@lpremodel.com"),
    ),
    Person(
        person_id="kelly",
        display_name="Kelly Stone",
        side=Side.EXTERNAL,
        org=Org.LP_REMODEL,
        role="Construction (Wes's brother)",
        addresses=("kelly@lpremodel.com",),
    ),
    Person(
        person_id="panos",
        display_name="Panos Evangelatos",
        side=Side.EXTERNAL,
        org=Org.ROI_BLOCKS,
        role="Marketing & Growth Development",
        addresses=("panos@roiblocks.com",),
    ),
    Person(
        person_id="alicia",
        display_name="Alicia Bardwell",
        side=Side.EXTERNAL,
        org=Org.LP_REMODEL,
        role="Transaction Coordinator / Bookkeeper",
        addresses=("alicia@lpremodel.com", "alicia@roiblocks.com"),
        active=False,
        notes="Departed. History is perishable — backfilled first.",
    ),
    # ---------------- Title ----------------
    Person(
        person_id="marti_watson",
        display_name="Marti Watson",
        side=Side.EXTERNAL,
        org=Org.TITLE,
        role="Title company (Potomac)",
        addresses=("marti@closewithpotomac.com",),
    ),
    Person(
        person_id="rikki_woodall",
        display_name="Rikki J. Woodall",
        side=Side.EXTERNAL,
        org=Org.TITLE,
        role="Title company (KVS Title)",
        addresses=("rwoodall@kvstitle.com",),
    ),
    # ---------------- Deal-side externals ----------------
    Person(
        person_id="endy_diaz",
        display_name="Endy Diaz",
        side=Side.EXTERNAL,
        org=Org.CONTRACTOR,
        role="Contractor — Cornerstone Remodeling VA",
        addresses=("endy@cornerstoneremodelingva.com",),
    ),
    Person(
        person_id="david_gonzalez",
        display_name="David Gonzalez",
        side=Side.EXTERNAL,
        org=Org.CONTRACTOR,
        role="Contractor — Carpentry KVC",
        addresses=("carpentrykvc@gmail.com",),
    ),
    Person(
        person_id="rob_smith",
        display_name="Rob Smith",
        side=Side.EXTERNAL,
        org=Org.REALTOR,
        role="Realtor",
        addresses=("robsellsdmv@gmail.com",),
    ),
    Person(
        person_id="kim_gallihugh",
        display_name="Kim Gallihugh",
        side=Side.EXTERNAL,
        org=Org.REALTOR,
        role="Realtor — Century 21 NM",
        addresses=("kim.gallihugh@c21nm.com",),
    ),
    Person(
        person_id="meki_cross",
        display_name="Meki Cross",
        side=Side.EXTERNAL,
        org=Org.REALTOR,
        role="Realtor",
        addresses=("mekicross@gmail.com",),
    ),
    Person(
        person_id="ali_parva",
        display_name="Ali Parva",
        side=Side.EXTERNAL,
        org=Org.ARCHITECT,
        role="Architect — AP Architecture LLC",
        addresses=("a.parva@aparchllc.com",),
    ),
    Person(
        person_id="charlene_fields",
        display_name="Charlene Fields",
        side=Side.EXTERNAL,
        org=Org.HOMEOWNER,
        role="Homeowner — 2401 Ridge Road",
        addresses=("cfields971@gmail.com",),
    ),
    Person(
        person_id="seidah_armstrong",
        display_name="Seidah Armstrong",
        side=Side.EXTERNAL,
        org=Org.HOMEOWNER,
        role="Homeowner — 14376 Tower Road",
        addresses=("sweetinfo@thevines.farm",),
    ),
    Person(
        person_id="john_armstrong",
        display_name="John Armstrong",
        side=Side.EXTERNAL,
        org=Org.HOMEOWNER,
        role="Homeowner — 14376 Tower Road",
        addresses=("jarmstrong808@gmail.com",),
    ),
    # Named by the admin as homeowners but with no address supplied. Registered
    # anyway so the deal roster is complete and analysis knows who owns these
    # properties. With no address they contribute no mail-matching signal, and
    # anything they send arrives as an unknown sender until an address is added
    # — which is the honest failure mode, rather than guessing one.
    Person(
        person_id="jason_tennstedt",
        display_name="Jason Tennstedt",
        side=Side.EXTERNAL,
        org=Org.HOMEOWNER,
        role="Homeowner — 24333 Narrow Guage",
        addresses=(),
        notes="No email address on file. Mail from him will not auto-match.",
    ),
    Person(
        person_id="tisha_elliott",
        display_name="Tisha Elliott",
        side=Side.EXTERNAL,
        org=Org.HOMEOWNER,
        role="Homeowner — 4251 Lane Pl",
        addresses=(),
        notes="No email address on file. Mail from her will not auto-match.",
    ),
    # ---------------- approved from the discovery queue ----------------
    # Admin-approved 2026-08-31. Was skipped 8 times as skip_unknown_external
    # while sending exactly the material this system exists to analyse: the
    # 18 Jun 2026 message to Rakesh (cc JP, Sharon Martin) carried the Varnum
    # payoff statement, construction accounting, permit history and project
    # summary as one package.
    Person(
        person_id="jerome_sanders",
        display_name="Jerome (Jerry) Sanders",
        side=Side.EXTERNAL,
        org=Org.FINANCE,
        role="1 Shield Financial (Florida) — referral/advisory contact for 910 Bayshore Dr (Terra Ceia)",
        addresses=("jerry@1shieldfinancial.com",),
        notes=(
            "Registered 2026-09-09 (admin directive): his mail was passing the intake rule as "
            "'no signal'. Tied to 910 Bayshore — the Sep 2026 'Florida Property Emergency — Terra "
            "Ceia' thread. Earlier threads with him (note investing, mortgage guidance, 2025) are "
            "general advisory and file as business material."
        ),
    ),
    Person(
        person_id="bill_leroy",
        display_name="Bill Leroy",
        side=Side.EXTERNAL,
        org=Org.FINANCE,
        role="Conduit Bankers — payoff statements, construction accounting, permit history",
        addresses=("bill@conduitbankers.com",),
        notes="Approved from discovery queue 2026-08-31. Varnum payoff/accounting packages.",
    ),
    # ------------------------------------------------------------------
    # Batch approved 2026-08-31 from the discovery queue. Every party below was
    # writing about one of the 15 properties and was being skipped as an unknown
    # external, which is how a filed lawsuit against RKB and a missed answer
    # deadline stayed outside the system. Counts are skipped-message counts at
    # time of approval.
    # ------------------------------------------------------------------
    # ---- Gallagher Law (g-e-law.com) — DC counsel: tax appeals, foreclosure,
    # litigation defence. Largest single counterparty in the skipped set.
    Person(
        person_id="brian_gallagher",
        display_name="Brian T. Gallagher, Esq.",
        side=Side.EXTERNAL,
        org=Org.LEGAL,
        role="Counsel — Gallagher Law; also named Trustee on RKB deeds of trust",
        addresses=("bgallagher@g-e-law.com",),
        send_as=("gallagher@briantgallagherlaw.com",),
        notes=(
            "31 skipped messages. Varnum vacant-property tax appeal, DC Superior "
            "Court case 2026-CAB-000806 where RKB was served as defendant, and the "
            "607 K Street tax refund. Address of record: 1906 Towne Center Blvd, "
            "Suite 265, Annapolis, MD 21401."
        ),
    ),
    Person(
        person_id="n_doyle",
        display_name="N. Doyle",
        side=Side.EXTERNAL,
        org=Org.LEGAL,
        role="Counsel — Gallagher Law",
        addresses=("ndoyle@g-e-law.com",),
        notes=(
            "9 skipped messages, all property-specific: Euclid St NOI and corrected "
            "assignment of DOT, Chita Ct borrower non-response, and the 31 Jul 2026 "
            "notice that RTLF-DC IIB LLC filed suit against 1512 Varnum Street NW LLC."
        ),
    ),
    Person(
        person_id="c_nattans",
        display_name="C. Nattans",
        side=Side.EXTERNAL,
        org=Org.LEGAL,
        role="Counsel — Gallagher Law",
        addresses=("cnattans@g-e-law.com",),
        notes="5 skipped messages: Chita Ct notice of intent to foreclose, Euclid St NOI.",
    ),
    Person(
        person_id="k_madden",
        display_name="K. Madden",
        side=Side.EXTERNAL,
        org=Org.LEGAL,
        role="Counsel — Gallagher Law",
        addresses=("kmadden@g-e-law.com",),
        notes="4 skipped messages: Euclid St NOI, Chita Ct status.",
    ),
    # ---- Quinn Legal (FL) — Briardale and 910 Bayshore enforcement.
    Person(
        person_id="e_quinn",
        display_name="E. Quinn",
        side=Side.EXTERNAL,
        org=Org.LEGAL,
        role="Counsel — Quinn Legal, P.A. (Florida enforcement)",
        addresses=("equinn@quinnlegal.com",),
        notes=(
            "5 skipped messages. Briardale demand for the original $58,183.55 "
            "contract amount; opened a new matter for lis pendens and default "
            "notice on 910 Bayshore Dr. 19321 US Highway 19 North. "
            "Correspondence here is attorney work product — treat as privileged."
        ),
    ),
    Person(
        person_id="seth_sayles",
        display_name="Seth Sayles",
        side=Side.EXTERNAL,
        org=Org.LEGAL,
        role="Counsel — Sayles Legal",
        addresses=("seth@sayleslegal.com",),
        send_as=("seth@saylesatlaw.com",),
        notes="Varnum SSL 2697/0008 first-mortgagee redemption rights.",
    ),
    Person(
        person_id="wendy_layton",
        display_name="Wendy (R.S. Layton PC)",
        side=Side.EXTERNAL,
        org=Org.LEGAL,
        role="Counsel — R.S. Layton PC; Tower Road payoff authorisations",
        addresses=("wendy@rslaytonpc.com",),
        notes=(
            "6 skipped messages, all Tower Road / Armstrong payoff authorisation. "
            "Introduced via Rob Smith's referral."
        ),
    ),
    # ---- Title / recording.
    Person(
        person_id="m_gonzalez",
        display_name="M. Gonzalez",
        side=Side.EXTERNAL,
        org=Org.TITLE,
        role="KC Wilson & Associates — assignment of DOT recording",
        addresses=("mgonzalez@kcwilson.com",),
        send_as=("mgonzalez@kcwilsonassociates.com",),
        notes=(
            "9 skipped messages spanning Euclid St, 9th St NW, 607 K St and the "
            "910 Bayshore ALTA lender's policy. Handles wet-signed corrected "
            "assignments — the recording trail for RKB's liens."
        ),
    ),
    # ---- Accounting.
    Person(
        person_id="sharon_martin",
        display_name="Sharon Martin",
        side=Side.EXTERNAL,
        org=Org.ACCOUNTING,
        role="CPA (Advance CPA) — Varnum tax protest, payoff balances, insurance",
        addresses=("advancecpa@gmail.com",),
        notes=(
            "10 skipped messages, every one naming Varnum. Escalated Builder's Risk "
            "Insurance across four messages to 'Final Notice' with no resolution "
            "visible. Also the personal guaranty and current loan payoff balance. "
            "Likely the 'Guarantee Sharon.pdf' in the Varnum folder."
        ),
    ),
    # ---- Realtor / consultant.
    Person(
        person_id="charlene_jones",
        display_name="Charlene Jones",
        side=Side.EXTERNAL,
        org=Org.REALTOR,
        role="Realtor — Keller Williams; Chita Ct listing and site visits",
        addresses=("charlenejones@kw.com",),
        notes=(
            "5 skipped messages, all Chita Ct: Monday site visit with Wes and David, "
            "and 'Seller Concessions: No Exceptions Without My Approval'. Distinct "
            "person from Charlene Fields (Ridge Road homeowner) — do not merge."
        ),
    ),
    Person(
        person_id="jessica_lovelive",
        display_name="Jessica (Love Live DC)",
        side=Side.EXTERNAL,
        org=Org.REALTOR,
        role="DC permit research / ADU feasibility",
        addresses=("jessica@lovelivedc.com",),
        notes="Varnum DC DOB permit lookup and garage ADU potential.",
    ),
    # ---- Government. Correspondence with a tax authority is a matter of record
    # and belongs in the archive verbatim.
    Person(
        person_id="gwen_bass_dc",
        display_name="Gwen Bass (DC Government)",
        side=Side.EXTERNAL,
        org=Org.GOVERNMENT,
        role="DC OTR — vacant property tax / mortgagee correspondence",
        addresses=("gwen.bass@dc.gov",),
        notes="Varnum SSL 2697/0008; ACH payment of $81,523.04 confirmation.",
    ),
    Person(
        person_id="patricia_watson_dc",
        display_name="Patricia Watson (DC Government)",
        side=Side.EXTERNAL,
        org=Org.GOVERNMENT,
        role="DC OTR — vacant property tax / mortgagee correspondence",
        addresses=("patricia.watson@dc.gov",),
        notes="Varnum SSL 2697/0008; ACH payment of $81,523.04 confirmation.",
    ),
    # ---- Senior-lien payoff desks. Low volume, high consequence: a payoff quote
    # defines RKB's exposure behind a senior lien and expires on a stated date.
    Person(
        person_id="usbank_payoff",
        display_name="US Bank — Mortgage Payoff Desk",
        side=Side.EXTERNAL,
        org=Org.SERVICER,
        role="Senior lienholder payoff statements",
        addresses=("mortgage.payoff@usbank.com",),
        notes="Chita Ct payoff statement request.",
    ),
    Person(
        person_id="navyfederal_payoff",
        display_name="Navy Federal — Payoff Desk",
        side=Side.EXTERNAL,
        org=Org.SERVICER,
        role="Senior lienholder payoff statements (HELOC)",
        addresses=("payoff@navyfederal.org",),
        notes="Chita Ct HELOC #8032999206, borrower Charis Jones.",
    ),
]

#: address -> Person (lower-cased)
ADDRESS_INDEX: Dict[str, Person] = {
    addr: p for p in PEOPLE for addr in p.all_addresses
}

#: Domains that always belong to us. Used to catch RKB staff who are not yet
#: enumerated above (e.g. a new @mtreh.com hire) so their mail is still correctly
#: treated as internal rather than as an unknown external counterparty.
RKB_DOMAINS = {"mtreh.com"}

#: Mailboxes we hold credentials for and ingest end-to-end.
INGESTED_MAILBOXES = {
    "rakesh.bhargava@gmail.com": {
        "provider": "gmail",
        "person_id": "rakesh",
        "status": "active",
        "note": "Carries rakesh@mtreh.com send-as traffic AND personal mail.",
    },
    # Outlook lands tomorrow:
    # "rakesh@mtreh.com": {"provider": "outlook", "person_id": "rakesh", "status": "planned"},
}


def person_for_address(address: str) -> Optional[Person]:
    return ADDRESS_INDEX.get((address or "").strip().lower())


def side_for_address(address: str) -> Optional[Side]:
    """RKB / EXTERNAL / None(unknown). Domain rule catches unlisted RKB staff."""
    addr = (address or "").strip().lower()
    if not addr:
        return None
    person = ADDRESS_INDEX.get(addr)
    if person is not None:
        return person.side
    domain = addr.rsplit("@", 1)[-1]
    if domain in RKB_DOMAINS:
        return Side.RKB
    return None


# --------------------------------------------------------------------------
# properties
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Property:
    property_id: str
    canonical_address: str
    city: str
    state: str
    postal: str
    #: every way humans refer to it — folder names, doc prefixes, shorthand
    aliases: Sequence[str]
    #: on-disk folder under DISK_CORPUS_ROOT, if any
    disk_folder: Optional[str] = None
    status: str = "hold"
    #: Which deal structure governs this property (admin-defined 2026-08-31).
    #: The two structures carry different economics and different paperwork, so a
    #: finding that is normal under one can be a breach under the other. Analysis
    #: must never reason about a property without knowing which applies.
    deal_type: str = "new_deal"
    notes: str = ""


PROPERTIES: List[Property] = [
    Property(
        property_id="9th_st_nw",
        canonical_address="3731 9th St NW",
        city="Washington", state="DC", postal="20010",
        aliases=("3731 9th St", "3731 9th Street", "9th St NW", "9th Street NW", "3731 9th"),
        disk_folder="9th St NW Washington DC 20010",
    ),
    Property(
        property_id="allison_st",
        canonical_address="513 Allison St NW",
        city="Washington", state="DC", postal="20011",
        aliases=("513 Allison", "Allison St", "Allison Street", "Allison"),
        disk_folder="513 Allison St. NW, Washington, DC 20011",
    ),
    Property(
        property_id="50th_pl",
        canonical_address="844 50th Pl NE",
        city="Washington", state="DC", postal="20019",
        aliases=("844 50th Pl", "844 50th Place", "50th Pl NE", "50th St", "430 Monroe LLC"),
        disk_folder="844 50th Pl. NE, Washington, DC 20019",
        notes="Owner entity: 430 Monroe LLC",
    ),
    Property(
        property_id="bayshore_904",
        canonical_address="904 Bayshore Dr",
        city="Terra Ceia", state="FL", postal="34250",
        aliases=("904 Bayshore", "904 Bayshore Dr", "904 Bayshore Drive"),
        disk_folder="904 Bayshore Dr, Terra Ceia, FL 34250",
        notes="DISTINCT from 910 Bayshore. Never merge — same street, two loans.",
    ),
    Property(
        property_id="varnum",
        canonical_address="1512 Varnum St NW",
        city="Washington", state="DC", postal="20011",
        aliases=("1512 Varnum", "Varnum St", "Varnum Street", "1512 Varnum Street NW LLC", "Varnum"),
        disk_folder="1512 Varnum Street NW LLC",
    ),
    Property(
        property_id="bayshore_910",
        canonical_address="910 Bayshore Dr",
        city="Terra Ceia", state="FL", postal="34250",
        aliases=("910 Bayshore", "910 Bayshore Dr", "910 Bayshore Drive"),
        disk_folder="Bayshore Dr., Terra Ceia, FL 34250",
        notes="DISTINCT from 904 Bayshore. Never merge — same street, two loans.",
    ),
    Property(
        property_id="briardale",
        canonical_address="14029 Briardale Ln",
        city="Tampa", state="FL", postal="33618",
        aliases=("14029 Briardale", "Briardale Ln", "Briardale Lane", "Briardale"),
        disk_folder="Briardale Ln., Tampa, FL 33618",
        notes="Contains privileged/legal material (Quinn Legal).",
    ),
    Property(
        property_id="chita_ct",
        canonical_address="2000 Chita Ct",
        city="Temple Hills", state="MD", postal="20748",
        aliases=("2000 Chita", "Chita Ct", "Chita Court", "Chita"),
        disk_folder="Chita Ct., Temple Hills, MD 20748",
        status="sold",
        notes="Demand letters, failed sale, video evidence. SOLD (admin 2026-10-08): no daily analysis.",
    ),
    Property(
        property_id="decatur_st",
        canonical_address="912 Decatur St NW",
        city="Washington", state="DC", postal="20011",
        aliases=("912 Decatur", "Decatur St", "Decatur Street", "Decatur"),
        disk_folder="Decatur St. NW, Washington, DC 20011",
    ),
    Property(
        property_id="euclid_st",
        canonical_address="5901 Euclid St",
        city="Cheverly", state="MD", postal="20785",
        aliases=("5901 Euclid", "Euclid St", "Euclid Street", "Euclid"),
        disk_folder="Euclid St., Cheverly, MD 20785",
        notes="Also references Elliott Pl. fronted-equity change order.",
    ),
    # ---- deal-side properties (no disk folder yet) ----
    Property(
        property_id="tower_road",
        canonical_address="14376 Tower Road",
        city="", state="", postal="",
        aliases=("14376 Tower", "Tower Road", "Tower Rd"),
        status="deal",
    ),
    Property(
        property_id="narrow_guage",
        canonical_address="24333 Narrow Guage",
        city="", state="", postal="",
        aliases=("24333 Narrow Guage", "Narrow Guage", "Narrow Gauge", "24333 Narrow Gauge"),
        status="deal",
        notes="Spelling varies: Guage / Gauge.",
    ),
    Property(
        property_id="ridge_road",
        canonical_address="2401 Ridge Road",
        city="", state="", postal="",
        aliases=("2401 Ridge", "Ridge Road", "Ridge Rd"),
        status="deal",
    ),
    Property(
        property_id="lane_pl",
        canonical_address="4251 Lane Pl",
        city="", state="", postal="",
        aliases=("4251 Lane", "Lane Pl", "Lane Place"),
        status="deal",
    ),
    # Added 2026-08-31 at Rakesh Sir's direction — the 15th property. Street
    # number, parties and posture below are read from the Aug 2026 mail thread,
    # not assumed: registering a property from a bare name is how a corpus ends
    # up with a wrong street number silently mis-filing every document.
    Property(
        property_id="tahona",
        canonical_address="8514 Tahona Dr",
        city="", state="", postal="",
        aliases=(
            "8514 Tahona", "8514 Tahona Dr", "8514 Tahona Drive",
            "Tahona Dr", "Tahona Drive", "Tahona",
        ),
        status="deal",
        notes=(
            "ACTIVE and unresolved as of Aug 2026. Estate deal: Rudolph Humes "
            "(deceased, surviving joint tenant) — estate holds title; Lorna Johnson "
            "signs as Personal Representative. Settlement: Potomac Title (Mari/Marti "
            "Watson). Title commitment First American #3-26-53653. Senior lien "
            "MidFirst/FHA first, HUD partial claim second, 2022 UCC unreleased — RKB's "
            "own position (second vs third) NOT yet confirmed in writing. "
            "RKB expressly refused to authorise the 13 Aug closing/wire: ALTA never "
            "delivered, ~$130,175 was structured as cash to the Estate instead of a "
            "controlled construction escrow with inspected draws. Open document "
            "defects: borrower named Randolph vs Rudolph Humes; Note maturity "
            "6 Aug 2027 vs DOT 13 Aug 2027; 2 points and Lorna Johnson's personal "
            "guaranty not reflected. Watch for a closing that proceeds without these "
            "cleared."
        ),
    ),
    # Added 2026-10-27 from JP's "LP Remodeling Hold Properties Summary.xlsx"
    # (figures to 30 Sep 2026): the two newest ROI Blocks deals. Street numbers
    # are JP's; aliases are number+street and the distinctive street name only.
    Property(
        property_id="durham_st",
        canonical_address="1631 North Durham St",
        city="Baltimore", state="MD", postal="21213",
        aliases=(
            "1631 North Durham", "1631 N Durham", "1631 N. Durham",
            "North Durham St", "N Durham St", "Durham St", "Durham Street",
        ),
        status="deal",
        notes=(
            "ROI Blocks deal. Invested 1 Sep 2026; builder cost $154,733.85; "
            "$25,000 funded to 30 Sep 2026 (fronted equity); closing fee $3,268; "
            "ROI commission $20,000; BuilderTrend invoice $156,649. Contact on JP's "
            "sheet: Duane Tutt. In progress."
        ),
    ),
    Property(
        property_id="old_brentford",
        canonical_address="6159 Old Brentford Ct",
        city="Alexandria", state="VA", postal="22310",
        aliases=(
            "6159 Old Brentford", "Old Brentford Ct", "Old Brentford Court",
            "Old Brentford", "Old Brantford",
        ),
        status="deal",
        notes=(
            "ROI Blocks deal. Invested 18 Sep 2026; builder cost $51,514.69; "
            "$16,329.17 funded to 30 Sep 2026; closing fee $3,173.70; ROI commission "
            "$3,000; BuilderTrend invoice $69,618.14. Completed, on market 14 Oct 2026 "
            "per JP."
        ),
    ),
]


# --------------------------------------------------------------------------
# SOLD deals — the Deal Intelligence corpus (admin directive 2026-10-19).
# Registered so their folders, emails and attachments are filed per property
# and the post-mortems can read a clean per-deal file. status="sold": out of
# every daily pass and off the sidebar (retrieve.config.SOLD_PROPERTY_IDS),
# in search, "Ask anything" and the Deal Intelligence passes. Addresses and
# dates are from "LP Remodeling Sold Properties Summary.xlsx"; folders from
# E:\LP Remodeling Projects\Sold Properties. Aliases are deliberately the
# number+street forms plus the distinctive street names — bare generic words
# ("Lake", "Golden", "Elliott" — also a Lane Pl borrower's surname) are not
# aliases, so a sold deal cannot claim a live property's mail.
# --------------------------------------------------------------------------
def _sold(pid: str, address: str, city: str, state: str, postal: str, folder: str, aliases, notes: str = "") -> Property:
    return Property(property_id=pid, canonical_address=address, city=city, state=state, postal=postal,
                    aliases=tuple(aliases), disk_folder=folder, status="sold", notes=notes)


SOLD_PROPERTIES: List[Property] = [
    _sold("bristol_pl", "10923 Bristol Pl", "Bealeton", "VA", "22712", "Z Closed - 10923 Bristol Pl., Bealeton, VA 22712",
          ("10923 Bristol Pl", "10923 Bristol Place", "10923 Bristol", "Bristol Pl", "Bristol Place"), "Sold. Invested 2024-04-18, received 2024-07-09."),
    _sold("baileys_ct", "15 Baileys Ct", "Silver Spring", "MD", "20906", "Z Closed - 15 Baileys Ct., Silver Spring, MD 20906",
          ("15 Baileys Ct", "15 Baileys Court", "15 Baileys", "Baileys Ct", "Baileys Court", "Baileys"), "Sold. Invested 2024-05-20, received 2024-07-31."),
    _sold("drew_st", "1015 Drew St", "Lantana", "FL", "33462", "Z Closed - 1015 Drew St., Lantana, FL 33462",
          ("1015 Drew St", "1015 Drew Street", "1015 Drew", "Drew St", "Drew Street"), "Sold. Invested 2024-06-03, received 2024-08-08."),
    _sold("canary_dr", "9483 Canary Dr", "Bel Alton", "MD", "20611", "Z Closed - 9483 Canary Dr., Bel Alton, MD 20611",
          ("9483 Canary Dr", "9483 Canary Drive", "9483 Canary", "Canary Dr", "Canary Drive"), "Sold. Invested 2024-06-25, received 2024-09-12."),
    _sold("farrell_ln", "20 Farrell Ln", "Fredericksburg", "VA", "22408", "Z Closed - 20 Farrell Ln., Fredericksburg, VA 22408",
          ("20 Farrell Ln", "20 Farrell Lane", "20 Farrell", "Farrell Ln", "Farrell Lane", "Farrell"), "Sold. Invested 2024-07-15, received 2024-10-04."),
    _sold("fort_donelson", "2105 Fort Donelson Ct", "Dumfries", "VA", "22026", "Z Closed - 2105 Fort Donelson Ct., Dumfries, VA 22026",
          ("2105 Fort Donelson Ct", "2105 Fort Donelson", "Fort Donelson Ct", "Fort Donelson"), "Sold. Invested 2024-07-19, received 2024-10-16."),
    _sold("dominion_reserve", "16600 Dominion Reserve Ct", "Broad Run", "VA", "20137", "Z Closed - 16600 Dominion Reserve Ct., Broad Run, VA 20137",
          ("16600 Dominion Reserve Ct", "16600 Dominion Reserve", "16600 Dominion", "Dominion Reserve Ct", "Dominion Reserve"), "Sold. Invested 2024-06-24, received 2024-11-26."),
    _sold("occoquan_forest", "6181 Occoquan Forest Dr", "Manassas", "VA", "20112", "Z Closed - 6181 Occoquan Forest Dr., Manassas, VA 20112",
          ("6181 Occoquan Forest Dr", "6181 Occoquan Forest", "6181 Occoquan", "Occoquan Forest Dr", "Occoquan Forest", "Occ Forest", "Occoquan"), "Sold. Invested 2024-09-10, received 2025-03-06."),
    _sold("hanson_oaks", "4080 Hanson Oaks Dr", "Hyattsville", "MD", "20784", "Z Closed - 4080 Hanson Oaks Dr., Hyattsville, MD 20784",
          ("4080 Hanson Oaks Dr", "4080 Hanson Oaks", "Hanson Oaks Dr", "Hanson Oaks"), "Sold. Invested 2024-12-17, received 2025-04-15."),
    _sold("metz_ct", "47 Metz Ct", "Germantown", "MD", "20874", "Z Closed - 47 Metz Ct., Germantown, MD 20874",
          ("47 Metz Ct", "47 Metz Court", "47 Metz", "Metz Ct", "Metz Court"), "Sold. Invested 2024-12-18, received 2025-04-21. NGTC IRA funding."),
    _sold("w_park_rd", "202 W Park Rd", "Stafford", "VA", "22554", "Z Closed - 202 W Park Rd, Stafford VA 22554",
          ("202 W Park Rd", "202 W Park Dr", "202 W Park", "202 West Park", "W Park Rd", "W Park Dr", "W Park"), "Sold. Invested 2025-03-06, received 2025-05-29. Summary shows $0 collected."),
    _sold("van_buren", "2115 Van Buren St", "Hyattsville", "MD", "20782", "Z Closed - 2115 Van Buren St., Hyattsville, MD 20782",
          ("2115 Van Buren St", "2115 Van Buren", "Van Buren St", "Van Buren"), "Sold. Invested 2024-06-11, received 2025-06-25 (379 days)."),
    _sold("woodland_dr", "940 Woodland Dr", "Palm Harbor", "FL", "34683", "Z Closed - 940 Woodland Dr., Palm Harbor, FL 34683",
          ("940 Woodland Dr", "940 Woodland Drive", "940 Woodland", "Woodland Dr", "Woodland Drive"), "Sold. Invested 2024-12-10, received 2025-10-03 (297 days); short $2,643."),
    _sold("keystone_ave", "11218 Keystone Ave", "Clinton", "MD", "20735", "Z Closed - 11218 Keystone Ave., Clinton, MD 20735",
          ("11218 Keystone Ave", "11218 Keystone", "Keystone Ave", "Keystone Avenue", "Keystone"), "Sold. Invested 2025-07-14, received 2025-10-29."),
    _sold("ready_ave", "5309 Ready Ave", "Baltimore", "MD", "21212", "Z Closed - 5309 Ready Ave., Baltimore, MD 21212",
          ("5309 Ready Ave", "5309 Ready Avenue", "5309 Ready", "Ready Ave", "Ready Avenue"), "Sold. Invested 2025-04-30, received 2025-12-05."),
    _sold("middlevale", "12912 Middlevale Ln", "Silver Spring", "MD", "20906", "Z Closed - 12912 Middlevale Ln., Silver Spring, MD 20906",
          ("12912 Middlevale Ln", "12912 Middlevale", "Middlevale Ln", "Middlevale Lane", "Middlevale"), "Sold. Invested 2025-06-12, received 2025-12-12."),
    _sold("beach_mill", "10618 Beach Mill Rd", "Great Falls", "VA", "22066", "Z Closed - 10618 Beach Mill Rd., Great Falls, VA 22066",
          ("10618 Beach Mill Rd", "10618 Beach Mill", "Beach Mill Rd", "Beach Mill Road", "Beach Mill"), "Sold. Invested 2024-12-27, received 2026-01-05 (374 days); short $92,903 per summary; demand letter Sep 2025."),
    _sold("bay_st", "1833 Bay St SE", "Washington", "DC", "20003", "Z Closed - 1833 Bay St. SE, Washington, DC 20003",
          ("1833 Bay St SE", "1833 Bay St", "1833 Bay Street", "1833 Bay", "Bay St SE", "Bay Street SE", "Bay St"), "Sold. Invested 2024-02-27, received 2026-01-07 (680 days); short $123,382 per summary; foreclosure/DAST, estate of Rawleigh Fitzgerald."),
    _sold("elliott_pl", "6412 Elliott Pl", "Hyattsville", "MD", "20783", "Z Closed - 6412 Elliott Pl., Hyattsville, MD 20783",
          ("6412 Elliott Pl", "6412 Elliott Place", "6412 Elliott", "Elliott Pl", "Elliott Place", "Elliot Pl", "Elliot Place"), "Sold. Invested 2025-03-07, received 2026-01-30 (329 days). Fronted-equity change order."),
    _sold("balboa_ave", "836 Balboa Ave", "Capitol Heights", "MD", "20743", "Z Closed - 836 Balboa Ave., Capitol Heights, MD 20743",
          ("836 Balboa Ave", "836 Balboa Avenue", "836 Balboa", "Balboa Ave", "Balboa Avenue", "Balboa"), "Sold. Invested 2025-04-24, received 2026-02-09 (291 days)."),
    _sold("golden_dr", "13206 Golden Dr", "Sumerduck", "VA", "22742", "Z Closed - 13206 Golden Dr., Summerduck, VA 22742",
          ("13206 Golden Dr", "13206 Golden Drive", "13206 Golden", "Golden Dr", "Golden Drive"), "Sold. Invested 2025-08-20, received 2026-02-09. NGTC IRA funding; borrower Brewster."),
    _sold("lake_dr", "116 Lake Dr", "Sterling", "VA", "20164", "Z Closed - 116 Lake Dr., Sterling, VA 20164",
          ("116 Lake Dr", "116 Lake Drive", "116 Lake", "Lake Dr Sterling", "Lake Drive Sterling"), "Sold. Invested 2025-07-28, received 2026-03-05."),
    _sold("k_st", "607 K St NE", "Washington", "DC", "20002", "ZClosed - K St. NE, Washington, DC 20002",
          ("607 K St NE", "607 K Street NE", "607 K St", "607 K Street", "607 K", "607K", "K St NE", "K Street NE", "K St"), "Sold. Invested 2024-04-24, received 2026-07-07 (804 days); short $74,958 per summary. Tax sale, vacant-tax exemption (Gallagher), homeowner Pamela Graham."),
    _sold("wood_st", "2820 Wood St", "Sarasota", "FL", "34237", None,
          ("2820 Wood St", "2820 Wood Street", "2820 Wood", "Wood St Sarasota", "Wood Street Sarasota", "Wood St"), "Sold — LP sold and kept cash. Invested 2024-08-30, received 2025-07-24; short $67,310 per summary. No disk folder."),
    _sold("linda_ln", "5715 Linda Ln", "Temple Hills", "MD", "20748", "Z Closed - 5715 Linda Ln., Temple Hills, MD 20748",
          ("5715 Linda Ln", "5715 Linda Lane", "5715 Linda", "Linda Ln", "Linda Lane"), "Sold — LP sold and kept cash. Invested 2024-11-21, received 2025-07-24; short $64,685 per summary. NGTC IRA funding."),
    _sold("redbird_ln", "10703 Redbird Ln", "Spotsylvania", "VA", "22553", "Z Closed - 10703 Redbird Ln., Spotsylvania, VA 22553",
          ("10703 Redbird Ln", "10703 Redbird Lane", "10703 Redbird", "Redbird Ln", "Redbird Lane", "Redbird"), "Sold — LP sold and kept cash. Invested 2025-02-13, received 2025-07-24; $0 collected per summary."),
    _sold("crimson_pl", "20428 Crimson Pl", "Leesburg", "VA", "20175", "Z Closed - 20428 Crimson Pl., Leesburg, VA 20175",
          ("20428 Crimson Pl", "20428 Crimson Place", "20428 Crimson", "Crimson Pl", "Crimson Place", "Crimson"), "Sold — LP sold and kept cash. Invested 2025-04-11, received 2025-07-24; $0 collected per summary."),
]
PROPERTIES.extend(SOLD_PROPERTIES)
SOLD_PROPERTY_IDS = frozenset(p.property_id for p in SOLD_PROPERTIES) | {"chita_ct", "9th_st_nw"}

#: Admin-defined 2026-08-31, verbatim from Rakesh Sir. Held as one explicit set
#: rather than scattered across the property constructors so it can be checked
#: against his instruction at a glance. Everything not listed here is a new deal.
OLD_DEAL_PROPERTY_IDS = frozenset({
    "varnum",
    "decatur_st",
    "50th_pl",
    "euclid_st",
    "chita_ct",
    "bayshore_910",
    "bayshore_904",
    "allison_st",
    "briardale",
})

DEAL_TYPES = ("old_deal", "new_deal")

PROPERTIES = [
    replace(p, deal_type="old_deal" if p.property_id in OLD_DEAL_PROPERTY_IDS else "new_deal")
    for p in PROPERTIES
]

_unknown = OLD_DEAL_PROPERTY_IDS - {p.property_id for p in PROPERTIES}
if _unknown:
    raise RuntimeError(f"OLD_DEAL_PROPERTY_IDS names unregistered properties: {sorted(_unknown)}")

PROPERTY_INDEX: Dict[str, Property] = {p.property_id: p for p in PROPERTIES}


def deal_type_for(property_id: str) -> Optional[str]:
    prop = PROPERTY_INDEX.get(property_id)
    return prop.deal_type if prop else None

#: property_id -> external people known to be on that deal (property-resolution hint)
PROPERTY_CONTACTS: Dict[str, List[str]] = {
    "tower_road": ["seidah_armstrong", "john_armstrong", "rob_smith", "kim_gallihugh", "endy_diaz"],
    "narrow_guage": ["jason_tennstedt", "rob_smith", "endy_diaz"],
    "ridge_road": ["charlene_fields", "meki_cross", "ali_parva", "david_gonzalez"],
    "lane_pl": ["tisha_elliott", "david_gonzalez"],
    # Not Jerome Sanders: a general adviser with ONE thread about 910 Bayshore.
    # Listed here on 2026-09-09 his hint plus thread inheritance pushed all 62
    # of his emails (IRA, note investing) onto the property over Opus's
    # correct "not a registered property" reading. The registry entry alone
    # gets his mail ingested; Opus places each email on its own content.
}


# --------------------------------------------------------------------------
# alias matching
# --------------------------------------------------------------------------
_STREET_SUFFIX = r"(?:st|street|dr|drive|pl|place|ln|lane|ct|court|rd|road|ave|avenue|way|ter|terrace)"

#: ordinals like "50th"/"9th" must not be swallowed by the number matcher
_NUM_TOKEN = r"\d{1,6}(?:st|nd|rd|th)?"


def normalize_text(text: str) -> str:
    """Lower-case, strip accents/punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _alias_pattern(alias: str) -> re.Pattern:
    """Word-boundary regex for an alias, tolerant of extra internal whitespace."""
    parts = [re.escape(tok) for tok in normalize_text(alias).split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b")


#: (property_id, compiled alias pattern, alias length in tokens) — longest first so
#: "904 Bayshore" wins over a bare "Bayshore" match.
ALIAS_PATTERNS: List[tuple] = sorted(
    (
        (prop.property_id, _alias_pattern(alias), len(normalize_text(alias).split()), alias)
        for prop in PROPERTIES
        for alias in (prop.canonical_address, *prop.aliases)
        if normalize_text(alias)
    ),
    key=lambda row: row[2],
    reverse=True,
)

#: Aliases that are dangerously generic on their own — a bare street name shared by
#: two properties. Matching one of these is NOT sufficient evidence by itself.
AMBIGUOUS_ALIASES = {"bayshore", "bayshore dr", "bayshore drive"}

#: Which properties each ambiguous street could mean.
#:
#: These strings are deliberately absent from any property's own alias list, so
#: nothing in the resolver can see them. That is right for *claiming* a property
#: — "Bayshore" cannot choose between 904 and 910 — but it left the segmenter
#: unable to see the mention at all, so a bare "Bayshore" sentence following a
#: Varnum paragraph silently inherited Varnum. Knowing a street is contested is
#: enough to refuse someone else's claim on the text, which is what this powers.
AMBIGUOUS_ALIAS_OWNERS: Dict[str, Set[str]] = {
    ambiguous: {
        prop.property_id
        for prop in PROPERTIES
        if any(
            set(ambiguous.split()).issubset(set(normalize_text(alias).split()))
            for alias in (prop.canonical_address, *prop.aliases)
        )
    }
    for ambiguous in AMBIGUOUS_ALIASES
}

_AMBIGUOUS_PATTERNS = [
    (ambiguous, re.compile(rf"\b{re.escape(ambiguous)}\b"))
    for ambiguous in sorted(AMBIGUOUS_ALIASES, key=len, reverse=True)
]


def ambiguous_streets_in(text: str) -> Set[str]:
    """Contested street names present in ``text``, whoever they belong to."""
    norm = normalize_text(text or "")
    if not norm:
        return set()
    return {name for name, pattern in _AMBIGUOUS_PATTERNS if pattern.search(norm)}


def properties_possibly_named_in(text: str) -> Set[str]:
    """Every property the text *might* concern, including contested streets.

    Distinct from :func:`properties_named_in`, which answers "which property is
    this filed under" and so must not guess between 904 and 910. This answers
    "who could this be about", which is the right question when deciding whether
    some *other* property may claim the text.
    """
    out = set(properties_named_in(text))
    for street in ambiguous_streets_in(text):
        out |= AMBIGUOUS_ALIAS_OWNERS.get(street, set())
    return out


def street_number_hints(text: str) -> List[str]:
    """Pull '904 Bayshore'-style number+street pairs out of free text."""
    norm = normalize_text(text)
    return re.findall(rf"\b({_NUM_TOKEN}\s+[a-z]+(?:\s+{_STREET_SUFFIX})?)\b", norm)


def properties_named_in(text: str) -> Set[str]:
    """Property ids whose alias appears in ``text``. Used on subject lines.

    Ambiguous aliases are skipped: a bare "Bayshore" cannot separate 904 from
    910, and filing mail under the wrong one of two live deals is a worse
    outcome than leaving it for the model to resolve from the body.
    """
    norm = normalize_text(text or "")
    if not norm:
        return set()
    return {
        property_id
        for property_id, pattern, _tokens, alias in ALIAS_PATTERNS
        if normalize_text(alias) not in AMBIGUOUS_ALIASES and pattern.search(norm)
    }
