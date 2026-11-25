"""The underwriting file: one document per deal in ``uw_files``.

The document is the whole file: the facts, the last assessment, every line with
what people did to it, the documents, the signature and the log. Everything
derived (the assessment, the list, the six steps) is rebuilt from the facts on
every change, so what the screen shows can never drift from what was entered.

Who may do what:

* ROI Blocks (a contractor login granted the ``underwriting`` tool) works only on
  the files it started: it enters the facts, fills its fields and uploads papers.
  It can never confirm a line, approve a document or sign.
* RKB staff may confirm any line, with or without a paper. Every confirmation
  asks for RKB's confirmation password (checked by the route) and is logged with
  the person's name. Two lines are one named person's call (the budget look, an
  exception).
* Rakesh Sir ticks each document as approved, then signs, once. He may edit a
  document before ticking it. A change to the facts after that voids the ticks
  and the signature and says so in the log.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import checklist as C
from . import engine as E

SIGNER = "rakesh"

FACT_KEYS = ("address", "state", "county", "arv", "as_is_value", "arv_analysis_id", "sqft_above", "sqft_below", "measured", "beds", "baths", "scope",
             "owner_kind", "owner_name", "owner_lives_there", "anyone_lives_there", "liens", "builder_cost", "contingency", "contract_price",
             "roi_at_closing", "closing_costs", "cash_to_owner", "guaranteed_price", "rate", "points", "term_months", "interest_free_months",
             "first_six_inside_note", "monthly_holding", "contractor_name", "licence_holder", "title_company", "closing_date", "property_id", "has_mortgage",
             # what the documents print and no test needs
             "tax_id", "legal_description", "borrower_address", "signer_name", "guarantor_name", "contractor_licence", "loan_number", "minimum_interest_months",
             "completion_date", "special_conditions",
             # what the 21st St set had to be corrected for by hand (25 Nov 2026): the date of ROI Blocks' agreement,
             # every member of a company, and the date of the deed the owner holds under
             "contract_date", "members", "deed_date", "deed_reference",
             # how interest is charged (chosen on every file), and the signer's title as the company's papers give it
             "interest", "signer_title")


class Refused(Exception):
    """The person may not do this, or the file is not ready for it. The message is shown as it is."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: A contractor login that has been given the underwriting tool (Wes, ROI Blocks) works in it as an RKB
#: login does: every file, RKB confirm (still behind RKB's password), sending a line back, editing a
#: document, deleting a file, having a new state written. Asked for on 2026-11-20: "show him the RKB
#: confirm as well ... give him everything that we are giving to Rakesh sir in underwriting".
#: Two things stay with Rakesh Sir alone, for every login: the tick on a document and the signature
#: (SIGNER). Set this to False to go back to the limited view: own files only, no confirm, no delete,
#: no document editing.
CONTRACTOR_FULL_ACCESS = True


def is_contractor(user: Dict[str, Any]) -> bool:
    """A contractor login that is LIMITED in underwriting. With CONTRACTOR_FULL_ACCESS nobody is."""
    return user.get("side") == "contractor" and not CONTRACTOR_FULL_ACCESS


def clean_facts(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: raw[k] for k in FACT_KEYS if k in (raw or {})}
    if "state" in out and isinstance(out["state"], str):
        out["state"] = out["state"].strip().upper()[:2]
    liens = []
    for l in out.get("liens") or []:
        if isinstance(l, dict):
            liens.append({k: l.get(k) for k in ("holder", "balance", "status", "payoff_at_closing", "monthly_payment", "arrears")})
    if "liens" in out:
        out["liens"] = liens
    if out.get("has_mortgage") is False:
        out["liens"] = []           # "no mortgage" clears any lender typed earlier
    if "licence_holder" in out and out["licence_holder"] not in C.LICENCE_HOLDERS:
        out["licence_holder"] = None        # "Choose" left as it was
    if "interest" in out:
        if out["interest"] in E.INTEREST_PLANS:
            out["interest_free_months"] = E.INTEREST_PLANS[out["interest"]][0]      # the one number the tests and the papers read
            if out["interest_free_months"]:
                out["minimum_interest_months"] = 0                                 # no minimum interest beside an interest-free period
        else:
            out.pop("interest")             # "Choose" left as it was
    elif out.get("interest_free_months") not in (None, ""):
        out["interest"] = E.interest_plan({"interest_free_months": out["interest_free_months"]})     # the months entered directly: the plan follows
    return out


def facts_from_arv(arv: Dict[str, Any]) -> Dict[str, Any]:
    """What an ARV file already knows, so nothing is typed twice."""
    geo, subj, an, val = arv.get("geocode") or {}, arv.get("subject") or {}, arv.get("analysis") or {}, arv.get("valuation") or {}
    county = geo.get("county")
    if geo.get("county_fips") == "24510":
        county = "Baltimore City"
    elif geo.get("county_fips") == "24005":
        county = "Baltimore County"
    facts: Dict[str, Any] = {"arv_analysis_id": arv.get("analysis_id"), "address": geo.get("formatted") or subj.get("address"), "state": geo.get("state"), "county": county,
                             "arv": an.get("arv_opinion") or val.get("arv"), "as_is_value": an.get("as_is_opinion"), "sqft_above": subj.get("sqft_after") or subj.get("sqft"),
                             "beds": subj.get("beds_after") or subj.get("beds"), "baths": subj.get("baths_after") or subj.get("baths"),
                             "scope": (arv.get("rehab") or {}).get("scope"), "property_id": arv.get("property_id")}
    if subj.get("occupancy") == "owner":
        facts["owner_lives_there"] = True
    return {k: v for k, v in facts.items() if v not in (None, "")}


class UwStore:
    def __init__(self, mongo):
        self.coll = mongo.db["uw_files"]
        self.coll.create_index("file_id", unique=True, name="ux_uw_id")
        self.coll.create_index([("updated_at", -1)], name="ix_uw_updated")

    @staticmethod
    def new_id() -> str:
        return "uw_" + uuid.uuid4().hex[:12]

    def get(self, file_id: str) -> Optional[Dict[str, Any]]:
        return self.coll.find_one({"file_id": file_id}, {"_id": 0})

    def save(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["updated_at"] = _now()
        self.coll.replace_one({"file_id": doc["file_id"]}, {k: v for k, v in doc.items() if k != "_id"}, upsert=True)
        return doc

    def list(self, *, created_by: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        q: Dict[str, Any] = {"closed": {"$ne": True}}
        if created_by:
            q["created_by"] = created_by
        keep = {"_id": 0, "file_id": 1, "facts.address": 1, "facts.state": 1, "facts.arv": 1, "facts.owner_kind": 1, "assessment.verdict": 1, "assessment.kind": 1,
                "assessment.kind_label": 1, "assessment.headline": 1, "assessment.rejected_second_mortgage": 1, "assessment.new_state": 1, "assessment.rule_65.ratio": 1, "assessment.money.principal": 1, "gates": 1, "signed": 1,
                "created_by_name": 1, "created_side": 1, "created_at": 1, "updated_at": 1}
        return list(self.coll.find(q, keep).sort("updated_at", -1).limit(limit))


# ------------------------------------------------------------------ the file
def log(doc: Dict[str, Any], user: Dict[str, Any], what: str, **extra: Any) -> None:
    doc.setdefault("events", []).append({"at": _now(), "by": user.get("user_id"), "by_name": user.get("name"), "side": user.get("side") or "rkb", "what": what, **extra})
    doc["events"] = doc["events"][-400:]


def delete(doc: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Take a file off the list (RKB only). Nothing is erased: the record, its papers and
    its history stay in the database, marked with who deleted it and when."""
    if is_contractor(user):
        raise Refused("Only RKB can delete a file.")
    doc["closed"] = True
    doc["deleted"] = {"at": _now(), "by": user.get("user_id"), "by_name": user.get("name")}
    log(doc, user, "Deleted the file")
    return doc


def refresh(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild everything derived: the assessment, the list, the locks."""
    facts = doc.get("facts") or {}
    if facts.get("interest") not in E.INTEREST_PLANS and E.interest_plan(facts):
        facts["interest"] = E.interest_plan(facts)      # a file from before the choice existed: said by its months
    a = E.assess(facts)
    items = C.merge(doc.get("items") or [], C.build(facts, a))
    for item in items:
        _auto(item, facts)
    doc["assessment"] = a
    doc["items"] = items
    docs = doc.get("documents") or []
    doc["gates"] = C.gates(items, a, details_missing=E.details_missing(facts), more_missing=E.more_missing(facts),
                           documents={"total": len(docs), "approved": sum(1 for d in docs if (d.get("approved") or {}).get("at"))},
                           signed=bool((doc.get("signed") or {}).get("at")))
    doc["edited"] = edited_from_arv(doc)
    return doc


#: Figures the ARV file supplies. A person may change them on page one; the file keeps
#: what the ARV file said so the change stays visible.
FROM_ARV = ("arv", "as_is_value", "sqft_above", "beds", "baths")


def edited_from_arv(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    src, facts = doc.get("from_arv") or {}, doc.get("facts") or {}
    return [{"field": k, "arv_file": src[k], "now": facts.get(k)} for k in FROM_ARV if k in src and facts.get(k) not in (None, "") and str(facts.get(k)) != str(src[k])]


#: What page one already holds is offered on the line that asks for it, so nothing is typed twice.
#: line -> {field on the line: fact}
FROM_FACTS = {"owner_contact": {"mailing_address": "borrower_address"}, "guarantor": {"guarantor_name": "guarantor_name"},
              "licence": {"legal_name": "contractor_name", "licence_number": "contractor_licence"}, "schedule": {"completion_date": "completion_date"}}


def _auto(item: Dict[str, Any], facts: Dict[str, Any]) -> None:
    """Items the system itself can settle."""
    for field, fact in (FROM_FACTS.get(item["key"]) or {}).items():
        if facts.get(fact) not in (None, "") and not str((item.get("values") or {}).get(field) or "").strip():
            item.setdefault("values", {})[field] = str(facts[fact])
    if item["key"] == "arv":
        linked = bool(facts.get("arv_analysis_id")) and E._n(facts.get("arv")) > 0
        # No ARV file linked: the value on page one was entered by hand, and a person at RKB confirms it.
        item.setdefault("values", {})["analysis_id"] = facts.get("arv_analysis_id") or "No ARV file linked: the value was entered by hand"
        if linked and item.get("status") != C.OK:
            item["status"] = C.OK
        elif not linked and item.get("status") == C.OK and not item.get("confirmed"):
            item["status"] = C.OPEN
    elif item.get("verify") == "filled" and not item.get("needs_file") and item.get("status") in (C.OPEN, C.RECEIVED, C.OK) and not item.get("confirmed"):
        vals = item.get("values") or {}
        full = bool(item.get("fields")) and all(str(vals.get(f["key"]) or "").strip() for f in item["fields"])
        if item.get("confirm"):
            full = full or bool(vals.get("_confirmed"))
        item["status"] = C.OK if full else C.OPEN


def create(store: UwStore, facts: Dict[str, Any], user: Dict[str, Any], from_arv: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = {"file_id": store.new_id(), "facts": clean_facts({**(from_arv or {}), **facts}), "from_arv": dict(from_arv or {}), "items": [], "documents": [], "events": [],
           "created_at": _now(), "created_by": user.get("user_id"),
           "created_by_name": user.get("name"), "created_side": user.get("side") or "rkb", "created_org": user.get("org"), "created_org_name": user.get("org_name")}
    log(doc, user, "Started the file")
    return store.save(refresh(doc))


LABELS = {"arv": "Value after repair", "as_is_value": "Value today", "sqft_above": "Square feet above grade", "sqft_below": "Square feet below grade",
          "builder_cost": "Builder's cost", "contingency": "Contingency", "contract_price": "Contract price (the note)", "roi_at_closing": "Paid to ROI Blocks at closing",
          "closing_costs": "Closing costs", "rate": "Rate", "points": "Points", "term_months": "Term", "interest_free_months": "Interest-free months",
          "owner_kind": "Who owns the house", "owner_name": "Owner's name", "owner_lives_there": "Owner lives in the house", "has_mortgage": "Mortgage on the house",
          "liens": "The mortgage", "state": "State", "county": "County", "scope": "Scope", "measured": "Area measured", "cash_to_owner": "Cash to the owner",
          "licence_holder": "Whose licence the work is done under", "contractor_name": "Contractor", "contractor_licence": "Contractor's licence number",
          "contract_date": "Customer agreement signed on", "members": "Members of the company", "deed_date": "Date of the owner's deed", "completion_date": "Work complete by",
          "closing_date": "Closing date", "title_company": "Title company", "interest": "How interest is charged", "signer_title": "Signer's title",
          "minimum_interest_months": "Minimum interest (months)"}


def set_facts(doc: Dict[str, Any], facts: Dict[str, Any], user: Dict[str, Any], from_arv: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Change the facts of page one. Anything may be changed, including the value
    after repair and the square footage; every change is logged with the old and the new."""
    before = copy.deepcopy(doc.get("facts") or {})
    was = (doc.get("assessment") or {}).get("verdict")
    if from_arv:
        doc["from_arv"] = dict(from_arv)
    doc["facts"] = {**before, **clean_facts({**(from_arv or {}), **facts})}
    moved = E.moved(before, doc["facts"])
    changed = [k for k in doc["facts"] if doc["facts"].get(k) != before.get(k)]
    if not changed:
        return refresh(doc)
    # A line that was filled from a fact follows the fact when it changes; what a person typed on the line stays.
    for item in doc.get("items") or []:
        for field, fact in (FROM_FACTS.get(item["key"]) or {}).items():
            vals = item.get("values") or {}
            if fact in changed and doc["facts"].get(fact) not in (None, "") and str(vals.get(field) or "").strip() == str(before.get(fact) or "").strip():
                item.setdefault("values", {})[field] = str(doc["facts"][fact])
    for k in changed:
        if k in LABELS and k != "liens":
            log(doc, user, "%s changed from %s to %s" % (LABELS[k], _show(before.get(k)), _show(doc["facts"].get(k))), field=k)
        elif k == "liens":
            log(doc, user, "The mortgage details were changed", field=k)
    what = ", ".join(LABELS.get(k, k) for k in changed)
    if (doc.get("signed") or {}).get("at"):
        doc["signed"] = {"voided_at": _now(), "voided_because": "The facts changed after the signature: " + what}
        log(doc, user, "The signature no longer stands: the facts changed (%s)" % what)
    ticked = [d for d in doc.get("documents") or [] if (d.get("approved") or {}).get("at")]
    if ticked:
        for d in ticked:
            d["approved"] = {"voided_at": _now(), "voided_because": "The facts changed after it was approved: " + what}
        log(doc, user, "%d approved document(s) must be made and approved again: the facts changed (%s)" % (len(ticked), what))
    refresh(doc)
    rewrite_documents(doc)
    now = doc["assessment"]["verdict"]
    if moved:
        log(doc, user, "Figures moved by more than 7%; the tests were run again", moved=moved)
    if now != was:
        log(doc, user, "Quick check: %s" % doc["assessment"]["headline"])
    return doc


def _show(v: Any) -> str:
    if v in (None, ""):
        return "blank"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, str) and v in C.LICENCE_HOLDERS:
        return C.LICENCE_HOLDERS[v]
    if isinstance(v, str) and v in E.INTEREST_PLANS:
        return E.INTEREST_PLANS[v][1]
    if isinstance(v, (int, float)) and abs(v) >= 1000:
        return "{:,.2f}".format(v).rstrip("0").rstrip(".")
    return str(v)


def item_of(doc: Dict[str, Any], key: str) -> Dict[str, Any]:
    for i in doc.get("items") or []:
        if i["key"] == key:
            return i
    raise Refused("no such item on this file")


def fill(doc: Dict[str, Any], key: str, values: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Enter a line's fields. Anyone on the file may enter them; only RKB may enter
    the fields of a line RKB confirms (a recording reference, the attorney's confirmation)."""
    item = item_of(doc, key)
    if is_contractor(user) and item.get("verify") == "rkb":
        raise Refused("RKB confirms this item.")
    allowed = {f["key"] for f in item.get("fields") or []} | ({"_confirmed"} if item.get("confirm") else set())
    vals = dict(item.get("values") or {})
    for k, v in (values or {}).items():
        if k in allowed:
            vals[k] = v if isinstance(v, bool) else str(v or "").strip()[:500]
    if key == "identity":
        from .reader import scrub
        vals = {k: scrub(v) for k, v in vals.items()}   # a number typed here by mistake is not kept
    item["values"] = vals
    item["updated_at"], item["updated_by"] = _now(), user.get("user_id")
    log(doc, user, "Filled in: %s" % item["label"], item=key)
    refresh(doc)
    rewrite_documents(doc)        # a name or a licence number entered on a line is printed in the documents
    return doc


def mark(doc: Dict[str, Any], key: str, status: str, user: Dict[str, Any], reason: Optional[str] = None) -> Dict[str, Any]:
    """A person at RKB sends a line back with the reason, or reopens it. Making a line
    done is ``confirm`` and asks for the password."""
    if is_contractor(user):
        raise Refused("Only RKB can do this.")
    if status not in (C.REJECTED, C.OPEN):
        raise Refused("A line is made done by RKB's confirmation, with the password.")
    item = item_of(doc, key)
    try:
        C.set_status(item, status, by=user.get("user_id") or "", reason=reason)
    except ValueError as exc:
        raise Refused(str(exc))
    log(doc, user, "%s: %s%s" % ("Sent back" if status == C.REJECTED else "Reopened", item["label"], (" (" + reason + ")") if reason else ""), item=key)
    return refresh(doc)


def confirm(doc: Dict[str, Any], key: str, user: Dict[str, Any], note: Optional[str] = None) -> Dict[str, Any]:
    """RKB confirms a line: it is done on RKB's word. The route has already checked the
    confirmation password. Works with or without a paper; a line that is made of fields
    (a recording reference, who at the attorney's office holds the identity papers) still
    needs those fields, because they are the record itself."""
    if is_contractor(user):
        raise Refused("Only RKB can confirm a line.")
    item = item_of(doc, key)
    only = C.ONLY.get(key)
    if only and user.get("user_id") not in (only, SIGNER):
        raise Refused("This one is for %s." % ("Manju Sir" if only == "manjunath" else "Rakesh Sir"))
    if not item.get("needs_file"):
        vals = item.get("values") or {}
        blank = [f["label"] for f in item.get("fields") or [] if not str(vals.get(f["key"]) or "").strip()]
        if blank:
            raise Refused("Fill in first: " + "; ".join(blank))
    passed = bool((item.get("reading") or {}).get("passed"))
    without_paper = bool(item.get("needs_file")) and not item.get("files")
    C.set_status(item, C.OK, by=user.get("user_id") or "")
    item["confirmed"] = {"by": user.get("user_id"), "by_name": user.get("name"), "at": _now(), "note": (note or "").strip()[:300] or None,
                         "without_paper": without_paper, "over_a_failed_reading": bool(item.get("files")) and item.get("needs_file") and not passed}
    how = "without a paper" if without_paper else ("over the system's reading" if item["confirmed"]["over_a_failed_reading"] else "")
    log(doc, user, "Confirmed by RKB%s: %s%s" % ((" " + how) if how else "", item["label"], (" (" + item["confirmed"]["note"] + ")") if item["confirmed"]["note"] else ""), item=key)
    return refresh(doc)


def defer(doc: Dict[str, Any], key: str, user: Dict[str, Any], note: Optional[str] = None, due: Optional[str] = None, undo: bool = False) -> Dict[str, Any]:
    """RKB allows a line to come in after closing, as an exception for this loan (on 21st St the signed
    measurement, 24 Nov 2026). The route has already checked the confirmation password. The line stops
    holding the file and is chased on the closing page instead; the commitment letter says so in words,
    and Rakesh Sir sees it at review. ``undo`` puts the line back among the conditions of closing."""
    if is_contractor(user):
        raise Refused("Only RKB can do this.")
    item = item_of(doc, key)
    if undo:
        if not item.get("deferred"):
            return doc
        item.pop("deferred")
        item["when"] = C.FILE
        log(doc, user, "A condition of closing again: %s" % item["label"], item=key)
    else:
        if key not in C.MAY_FOLLOW or item.get("foreclosure"):
            raise Refused("This line cannot be left until after closing: the loan or a foreclosure rests on it.")
        if item.get("status") in C.DONE:
            raise Refused("This line is already done.")
        item["deferred"] = {"by": user.get("user_id"), "by_name": user.get("name"), "at": _now(), "note": (note or "").strip()[:300] or None, "due": (due or "").strip()[:10] or None}
        log(doc, user, "Allowed after closing, as an exception for this loan: %s%s" % (item["label"], (" (" + item["deferred"]["note"] + ")") if item["deferred"]["note"] else ""), item=key)
    refresh(doc)
    rewrite_documents(doc)      # the commitment letter lists it as an exception, not as a condition
    return doc


def add_file(doc: Dict[str, Any], key: str, rec: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    item = item_of(doc, key)
    if not item.get("needs_file"):
        raise Refused("This item takes no paper.")
    item.setdefault("files", []).append(rec)
    if item.get("status") in (C.OPEN, C.REJECTED):
        item["status"] = C.RECEIVED
        item["reason"] = None
    item["updated_at"], item["updated_by"] = _now(), user.get("user_id")
    log(doc, user, "Uploaded %s for: %s" % (rec.get("filename"), item["label"]), item=key)
    return refresh(doc)


def add_reading(doc: Dict[str, Any], key: str, sha: str, reading: Optional[Dict[str, Any]], error: Optional[str] = None) -> Dict[str, Any]:
    """The system's reading of one upload. All checks met makes the item ok, unless it is
    one RKB verifies; anything else sends it back with the reason in the paper's words."""
    from . import reader
    item = item_of(doc, key)
    for f in item.get("files") or []:
        if f.get("sha256") == sha:
            f["reading"] = reading
            f["error"] = error
            f["read_at"] = _now()
    system = {"user_id": "system", "name": "The system", "side": "rkb"}
    if error or not reading:
        log(doc, system, "Could not read the paper for: %s (%s)" % (item["label"], error or "no reading"), item=key)
        return refresh(doc)
    item["reading"] = {"sha256": sha, "passed": reading["passed"], "at": _now()}
    if reading["passed"]:
        if item.get("verify") == "reading":
            C.set_status(item, C.OK, by="system")
            log(doc, system, "Read and passed: %s" % item["label"], item=key)
        else:
            if item.get("status") == C.REJECTED:      # sent back on an earlier reading; this one passed, so it waits for RKB again
                C.set_status(item, C.RECEIVED, by="system")
            log(doc, system, "Read and passed; RKB confirms this line: %s" % item["label"], item=key)
    else:
        why = reader.reason_for(reading)
        C.set_status(item, C.REJECTED, by="system", reason=why)
        log(doc, system, "Sent back: %s (%s)" % (item["label"], why), item=key)
    return refresh(doc)


def make_documents(doc: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Write the document set from the file. Opens once the quick check passes: the
    commitment letter can go out at once, and the rest fill in as the file fills."""
    from . import documents as D
    refresh(doc)
    if not doc["gates"]["verdict_ok"]:
        raise Refused("The documents are written once the quick check passes.")
    if doc["gates"]["more_missing"]:
        raise Refused("The documents are written once the remaining details are in: " + "; ".join(doc["gates"]["more_missing"][:5]))
    first = not doc.get("documents")
    doc["documents"] = D.refresh(doc)
    log(doc, user, "%s the %d documents" % ("Wrote" if first else "Rewrote", len(doc["documents"])))
    return refresh(doc)


def rewrite_documents(doc: Dict[str, Any]) -> None:
    """Keep documents that already exist in step with the file. A document that was
    ticked and whose text changes loses its tick, and the log says which."""
    if not doc.get("documents"):
        return
    from . import documents as D
    before = {d["key"]: bool((d.get("approved") or {}).get("at")) for d in doc["documents"]}
    doc["documents"] = D.refresh(doc)
    lost = [d["title"] for d in doc["documents"] if before.get(d["key"]) and not (d.get("approved") or {}).get("at")]
    if lost:
        doc.setdefault("events", []).append({"at": _now(), "by": "system", "by_name": "The system", "side": "rkb",
                                             "what": "Rewritten from the file and to be approved again: " + "; ".join(lost)})
    refresh(doc)


def document_of(doc: Dict[str, Any], key: str) -> Dict[str, Any]:
    for d in doc.get("documents") or []:
        if d["key"] == key:
            return d
    raise Refused("no such document on this file")


def edit_document(doc: Dict[str, Any], key: str, blocks: List[Dict[str, Any]], user: Dict[str, Any]) -> Dict[str, Any]:
    """Rakesh Sir (or RKB staff) corrects a document's wording. An edit makes a new
    version and takes away the tick: an edited document is approved again."""
    if is_contractor(user):
        raise Refused("Only RKB can edit a document.")
    d = document_of(doc, key)
    d.setdefault("versions", []).append({"version": d.get("version", 1), "blocks": d.get("blocks"), "saved_at": d.get("saved_at"), "saved_by": d.get("saved_by")})
    d["versions"] = d["versions"][-20:]
    d["blocks"], d["version"] = blocks, int(d.get("version", 1)) + 1
    d["edited"], d["saved_at"], d["saved_by"] = True, _now(), user.get("name")
    if (d.get("approved") or {}).get("at"):
        d["approved"] = {"voided_at": _now(), "voided_because": "Edited after it was approved"}
    log(doc, user, "Edited: %s (version %d)" % (d["title"], d["version"]), document=key)
    return refresh(doc)


def restore_document(doc: Dict[str, Any], key: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Drop a hand correction and write the document from the file again."""
    if is_contractor(user):
        raise Refused("Only RKB can do this.")
    d = document_of(doc, key)
    if not d.get("edited"):
        return doc
    d.setdefault("versions", []).append({"version": d.get("version", 1), "blocks": d.get("blocks"), "saved_at": d.get("saved_at"), "saved_by": d.get("saved_by")})
    d["edited"] = False
    d["approved"] = {}
    log(doc, user, "Correction taken back; written again from the file: %s" % d["title"], document=key)
    rewrite_documents(doc)
    return doc


def approve_document(doc: Dict[str, Any], key: str, user: Dict[str, Any], approved: bool = True) -> Dict[str, Any]:
    if user.get("user_id") != SIGNER:
        raise Refused("Rakesh Sir approves the documents.")
    d = document_of(doc, key)
    refresh(doc)
    if approved and not doc["gates"]["file_complete"] and not d.get("early"):
        raise Refused("The file is not complete yet: this document is filled from it.")
    d["approved"] = {"at": _now(), "by": user["user_id"], "by_name": user.get("name"), "version": d.get("version", 1)} if approved else {}
    log(doc, user, "%s: %s" % ("Approved" if approved else "Approval taken back", d["title"]), document=key)
    return refresh(doc)


def sign(doc: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    if user.get("user_id") != SIGNER:
        raise Refused("Rakesh Sir signs.")
    refresh(doc)
    g = doc["gates"]
    if not g["ready_to_sign"]:
        if g["details_missing"]:
            raise Refused("Page one is not complete: " + "; ".join(g["details_missing"][:5]))
        if not g["verdict_ok"]:
            raise Refused("The quick check does not pass: " + doc["assessment"]["headline"])
        if g["more_missing"]:
            raise Refused("The remaining details are not complete: " + "; ".join(g["more_missing"][:5]))
        if g["waiting_file"]:
            raise Refused("The file is not complete: " + "; ".join(w["label"] for w in g["waiting_file"][:5]))
        raise Refused("Every document must be approved first: %d of %d are." % (g["documents"]["approved"], g["documents"]["total"]))
    doc["signed"] = {"at": _now(), "by": user["user_id"], "by_name": user.get("name"), "verdict": doc["assessment"]["verdict"],
                     "ratio": doc["assessment"]["rule_65"]["ratio"], "principal": doc["assessment"]["money"]["principal"],
                     "on_rkbs_word": [w["label"] for w in g["confirmed_on_rkbs_word"]]}
    log(doc, user, "Signed: clear to close")
    return refresh(doc)
