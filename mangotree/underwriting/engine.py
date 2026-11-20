"""The three tests, the kind of deal and the verdict. Arithmetic only.

The rules, as decided on 2026-11-20:

* **The 65% rule.** Every mortgage that stays on the house, plus the note RKB takes,
  plus RKB's points and the interest the note earns over its term, may not pass
  65% of the after-repair value. The same 65% applies in first and in second
  position.
* **The foreclosure test.** If the house never sells: the value marked down 15%,
  less 8% selling cost, less the mortgage ahead of RKB and what it grows by while
  the foreclosure runs, less that state's foreclosure cost and holding cost, must
  still cover every dollar RKB has paid out. It is asked twice: once for the
  finished house, once for closing day, before any work is done.
* **The budget test.** The builder's cost per square foot against the usual range
  for that scope in that state, and a contingency of at least 10% on its own line.
  A budget outside the range is not refused; one person looks and says yes or no.
* **No second mortgage.** RKB may stand behind one mortgage, never behind two.

A deal is one of four kinds, decided by who owns the house, whether anyone lives
in it, and the mortgage on it. A person living in the house they own stops the
deal: our terms cannot be used on someone's own home.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from mangotree.arv.rehab import SCOPE_TIERS, STATE_COST_INDEX

from . import states as S

LIMIT = 0.65                 # of ARV
MARKDOWN = 0.15              # value lost in a forced sale
SELLING_COST = 0.08          # commission, transfer tax, settlement
FUNDED_SHARE = 0.90          # of each draw; the rest is held to the sale
MIN_CONTINGENCY = 0.10       # of the builder's cost of work
SENIOR_RATE_GUESS = 0.07     # a year, when the senior lender's payment is not known
REVIEW_CHANGE = 0.07         # a verified figure this far from the one the check ran on re-opens the check

#: No second mortgage (2026-11-20, "the property should not have the second mortgage if it has
#: then automatically reject the deal"). RKB may sit behind ONE mortgage, never behind two.
#: A house that would still carry two mortgages ahead of RKB is rejected, with no exception
#: route. When the second one is paid off and released at closing, RKB is behind one mortgage
#: only and the deal goes on (Rakesh Sir on Michele Dr, 19 Nov 2026: "Aqua must be paid off and
#: released at closing; I won't close behind it"). Set this to False to reject every house that
#: has a second mortgage today, paid off or not.
SECOND_MORTGAGE_OK_IF_PAID_OFF = True

DEFAULT_RATE = 18.0
DEFAULT_POINTS = 2.0
DEFAULT_TERM = 12

LIEN_CURRENT, LIEN_LATE_30, LIEN_LATE_MORE, LIEN_FORECLOSURE = "current", "late_30", "late_more", "foreclosure"
LIEN_STATUS_LABEL = {LIEN_CURRENT: "Paid up to date", LIEN_LATE_30: "Up to 30 days late", LIEN_LATE_MORE: "More than 30 days late", LIEN_FORECLOSURE: "In foreclosure"}

KIND_A, KIND_B, KIND_C, KIND_D = "A", "B", "C", "D"
KIND_LABEL = {KIND_A: "First lien", KIND_B: "Second lien", KIND_C: "Pay off the first", KIND_D: "Guaranteed price or cash to the owner"}

FITS, EXCEPTION, DOES_NOT_FIT, INCOMPLETE = "fits", "exception", "does_not_fit", "incomplete"

#: What the quick check cannot run without.
ESSENTIAL = (("state", "the state"), ("arv", "the after-repair value"), ("builder_cost", "the builder's cost"), ("owner_kind", "who owns the house"))


def _n(v: Any) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _money(v: float) -> str:
    return "${:,.0f}".format(round(v))


def _liens(facts: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for l in facts.get("liens") or []:
        bal = _n(l.get("balance"))
        if bal <= 0 and not (l.get("holder") or "").strip():
            continue
        status = l.get("status") if l.get("status") in LIEN_STATUS_LABEL else LIEN_CURRENT
        out.append({"holder": (l.get("holder") or "").strip() or "Lender not named", "balance": bal, "status": status,
                    "payoff_at_closing": bool(l.get("payoff_at_closing")), "monthly_payment": _n(l.get("monthly_payment")), "arrears": _n(l.get("arrears"))})
    return out


def kind_of(facts: Dict[str, Any]) -> str:
    liens = _liens(facts)
    if _n(facts.get("cash_to_owner")) > 0 or facts.get("guaranteed_price"):
        return KIND_D
    if any(l["status"] in (LIEN_LATE_MORE, LIEN_FORECLOSURE) for l in liens):
        return KIND_C
    if any(not l["payoff_at_closing"] for l in liens):
        return KIND_B
    return KIND_A


def lien_position(facts: Dict[str, Any]) -> str:
    return S.SECOND if any(not l["payoff_at_closing"] for l in _liens(facts)) else S.FIRST


def money(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Sources and uses: what RKB pays out, what the note is for, what sits ahead."""
    liens = _liens(facts)
    work = _n(facts.get("builder_cost"))
    contingency = _n(facts.get("contingency"))
    roi = _n(facts.get("roi_at_closing"))
    closing = _n(facts.get("closing_costs"))
    cash_owner = _n(facts.get("cash_to_owner"))
    payoffs = sum(l["balance"] for l in liens if l["payoff_at_closing"])
    arrears = sum(l["arrears"] for l in liens if not l["payoff_at_closing"])
    staying = sum(l["balance"] for l in liens if not l["payoff_at_closing"])
    before_tax = work + contingency + roi + closing + payoffs + arrears + cash_owner
    contract = _n(facts.get("contract_price"))
    principal = contract if contract > 0 else before_tax
    tax = S.recording_tax(facts.get("state") or "", facts.get("county"), principal)
    if contract <= 0:
        principal += tax["amount"]      # with no contract price entered, the note is every dollar RKB pays, the recording tax included
    lines = [
        {"key": "draws", "label": "Work, paid in draws at 90%", "amount": round(work * FUNDED_SHARE, 2)},
        {"key": "holdback", "label": "Held back 10%, paid to the contractor at the sale", "amount": round(work * (1 - FUNDED_SHARE), 2)},
        {"key": "contingency", "label": "Contingency, released only on approved change orders", "amount": contingency},
        {"key": "roi", "label": "Paid to ROI Blocks at closing", "amount": roi},
        {"key": "closing", "label": "Closing costs (title, settlement, recording)", "amount": closing},
        {"key": "tax", "label": "Recording tax: " + tax["basis"], "amount": tax["amount"], "note": tax.get("note")},
        {"key": "payoff", "label": "Mortgage paid off at closing", "amount": payoffs},
        {"key": "arrears", "label": "Missed payments on the mortgage, brought up to date", "amount": arrears},
        {"key": "owner", "label": "Cash to the owner", "amount": cash_owner},
    ]
    lines = [l for l in lines if l["amount"] > 0]
    cash_out = round(sum(l["amount"] for l in lines), 2)
    day_one = round(roi + closing + tax["amount"] + payoffs + arrears + cash_owner, 2)
    return {"lines": lines, "cash_out": cash_out, "day_one": day_one, "principal": round(principal, 2), "principal_is_contract": contract > 0,
            "staying": round(staying, 2), "liens": liens, "work": work, "contingency": contingency, "tax_sure": tax["sure"]}


def rule_65(facts: Dict[str, Any], m: Dict[str, Any]) -> Dict[str, Any]:
    arv = _n(facts.get("arv"))
    rate = _n(facts.get("rate")) or DEFAULT_RATE
    points = _n(facts.get("points")) if facts.get("points") not in (None, "") else DEFAULT_POINTS
    term = int(_n(facts.get("term_months")) or DEFAULT_TERM)
    free = int(_n(facts.get("interest_free_months")))
    inside = bool(facts.get("first_six_inside_note"))
    charged = max(0, term - free)
    interest = m["principal"] * rate / 100.0 * charged / 12.0
    fee = 0.0 if inside else m["principal"] * points / 100.0
    exposure = m["staying"] + m["principal"] + fee + interest
    limit = arv * LIMIT
    per_dollar = 1.0 + (0.0 if inside else points / 100.0) + rate / 100.0 * charged / 12.0
    most = max(0.0, (limit - m["staying"]) / per_dollar) if arv > 0 else 0.0
    ratio = exposure / arv if arv > 0 else None
    return {"passed": bool(arv > 0 and exposure <= limit + 0.005), "ratio": ratio, "limit": LIMIT, "exposure": round(exposure, 2), "limit_amount": round(limit, 2),
            "room": round(limit - exposure, 2), "most_note": round(most, 2),
            "parts": [{"label": "Mortgage that stays ahead of RKB", "amount": m["staying"]},
                      {"label": "RKB's note" + (" (the contract price)" if m["principal_is_contract"] else " (what RKB pays out)"), "amount": m["principal"]},
                      {"label": "RKB's points, %.2g%%" % points + (" (already inside the note)" if inside else ""), "amount": round(fee, 2)},
                      {"label": "Interest at %.4g%% for %d months" % (rate, charged), "amount": round(interest, 2)}],
            "rate": rate, "points": points, "term_months": term, "interest_free_months": free, "interest": round(interest, 2), "fee": round(fee, 2)}


def _forced_sale(value: float, m: Dict[str, Any], st: Optional[Dict[str, Any]], facts: Dict[str, Any], *, occupied: bool) -> Dict[str, Any]:
    fc = (st or {}).get("foreclosure") or S.WORST_CASE
    months = int(fc.get("months_owner_occupied" if occupied else "months") or 0)
    marked = value * (1 - MARKDOWN)
    net = marked * (1 - SELLING_COST)
    carry = 0.0
    guessed = False
    for l in m["liens"]:
        if l["payoff_at_closing"]:
            continue
        if l["monthly_payment"] > 0:
            carry += l["monthly_payment"] * months
        else:
            carry += l["balance"] * SENIOR_RATE_GUESS / 12.0 * months
            guessed = True
    holding = _n(facts.get("monthly_holding")) * months
    cost = float(fc.get("cost") or 0)
    left = net - m["staying"] - carry - holding - cost
    return {"value": round(value, 2), "marked_down": round(marked, 2), "after_selling": round(net, 2), "mortgage_ahead": m["staying"], "mortgage_growth": round(carry, 2),
            "mortgage_growth_guessed": guessed, "holding": round(holding, 2), "foreclosure_cost": cost, "months": months, "left_for_rkb": round(left, 2)}


def foreclosure_test(facts: Dict[str, Any], m: Dict[str, Any]) -> Dict[str, Any]:
    st = S.state_of(facts.get("state"))
    arv = _n(facts.get("arv"))
    occupied = bool(facts.get("anyone_lives_there") or facts.get("owner_lives_there"))
    done = _forced_sale(arv, m, st, facts, occupied=occupied)
    done.update({"rkb_out": m["cash_out"], "passed": done["left_for_rkb"] >= m["cash_out"] - 0.005,
                 "cover": (done["left_for_rkb"] / m["cash_out"]) if m["cash_out"] > 0 else None})
    as_is = _n(facts.get("as_is_value"))
    if as_is > 0:
        day = _forced_sale(as_is, m, st, facts, occupied=occupied)
        day.update({"rkb_out": m["day_one"], "passed": day["left_for_rkb"] >= m["day_one"] - 0.005, "checked": True,
                    "cover": (day["left_for_rkb"] / m["day_one"]) if m["day_one"] > 0 else None})
    else:
        day = {"checked": False, "passed": None, "rkb_out": m["day_one"],
               "why": "No value for the house as it stands today. Run the as-is value in the ARV file and this is checked."}
    passed = done["passed"] and (day["passed"] is not False)
    fc = (st or {}).get("foreclosure") or S.WORST_CASE
    return {"passed": bool(passed), "finished": done, "closing_day": day, "state_route": fc.get("route"), "estimate": bool(fc.get("estimate")),
            "worst_case": st is None}


def budget_test(facts: Dict[str, Any], m: Dict[str, Any]) -> Dict[str, Any]:
    above, below = _n(facts.get("sqft_above")), _n(facts.get("sqft_below"))
    area = above + below
    work = m["work"]
    looks: List[str] = []
    scope = facts.get("scope") if facts.get("scope") in SCOPE_TIERS else None
    psf = work / area if area > 0 and work > 0 else None
    low = high = None
    if not facts.get("measured"):
        looks.append("The area has not been measured. A signed measurement with a sketch is on Wes's list.")
    if psf is None:
        looks.append("Area or builder's cost is missing, so cost per square foot cannot be checked.")
    elif scope:
        idx = STATE_COST_INDEX.get((facts.get("state") or "").upper(), 1.0)
        tier = SCOPE_TIERS[scope]
        low, high = round(tier["low"] * idx), round(tier["high"] * idx)
        if psf < low:
            looks.append("$%.0f a square foot is under the usual $%d to $%d for a %s in this state. Can this budget finish the work?" % (psf, low, high, tier["label"].lower()))
        elif psf > high:
            looks.append("$%.0f a square foot is above the usual $%d to $%d for a %s in this state. Which lines carry the difference?" % (psf, low, high, tier["label"].lower()))
    else:
        looks.append("The scope (cosmetic, moderate, full gut, addition) is not chosen, so there is no range to compare with.")
    need = work * MIN_CONTINGENCY
    if work > 0 and m["contingency"] + 0.005 < need:
        looks.append("Contingency is %s; at least 10%% of the work (%s) should sit on its own line." % (_money(m["contingency"]), _money(need)))
    return {"passed": not looks, "looks": looks, "per_sqft": round(psf, 2) if psf is not None else None, "range": [low, high] if low else None,
            "area": area, "scope": scope, "contingency_share": (m["contingency"] / work) if work > 0 else None}


def assess(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the quick check shows: kind, the three tests, the verdict and why."""
    facts = facts or {}
    missing = [label for key, label in ESSENTIAL if not facts.get(key) or (key in ("arv", "builder_cost") and _n(facts.get(key)) <= 0)]
    st = S.state_of(facts.get("state"))
    new_state = bool((facts.get("state") or "").strip()) and not st
    kind = kind_of(facts)
    position = lien_position(facts)
    m = money(facts)
    r65 = rule_65(facts, m)
    fc = foreclosure_test(facts, m)
    bud = budget_test(facts, m)
    owner = facts.get("owner_kind")

    stops: List[str] = []
    if owner == S.INDIVIDUAL and facts.get("owner_lives_there"):
        stops.append("The owner is a person who still lives in the house. A loan at our rate with one payment at the end cannot be made on a person's own home. "
                     "The owner must have moved out, with proof, before this can go further.")
    staying_liens = [l for l in m["liens"] if not l["payoff_at_closing"]]
    rejected_second = len(staying_liens) >= 2 or (len(m["liens"]) >= 2 and not SECOND_MORTGAGE_OK_IF_PAID_OFF)
    if rejected_second:
        names = " and ".join(l["holder"] for l in (staying_liens if len(staying_liens) >= 2 else m["liens"])[:3])
        stops.append("The house has a second mortgage (%s). RKB does not lend behind a second mortgage: this deal is rejected." % names)
    cured: List[str] = []
    for l in m["liens"]:
        if l["payoff_at_closing"] or l["status"] not in (LIEN_LATE_MORE, LIEN_FORECLOSURE):
            continue
        if l["status"] == LIEN_LATE_MORE and l["arrears"] > 0:
            # Behind by more than 30 days, but every missed payment is paid at closing with RKB's money
            # (Michele Dr, 19 Nov 2026). The July guideline calls this ineligible, so it is never
            # automatic: it goes to Rakesh Sir as an exception.
            cured.append("%s is more than 30 days late. Its missed payments (%s) are brought up to date at closing with RKB's money and added to the loan." % (l["holder"], _money(l["arrears"])))
            continue
        stops.append("%s is %s. RKB does not sit behind a mortgage in default: it is paid off at closing%s or the deal does not go."
                     % (l["holder"], LIEN_STATUS_LABEL[l["status"]].lower(), ", or its missed payments are entered and brought up to date at closing," if l["status"] == LIEN_LATE_MORE else ""))
    if m["principal_is_contract"] and m["cash_out"] > m["principal"] + 0.005:
        stops.append("RKB would pay out %s but hold a note for only %s. The note must cover every dollar RKB pays." % (_money(m["cash_out"]), _money(m["principal"])))
    pricing = S.pricing_problems(state=facts.get("state") or "", lien=position, owner_kind=owner or "", rate=r65["rate"], points=r65["points"],
                                 term_months=r65["term_months"], interest_free_months=r65["interest_free_months"], principal=m["principal"]) if st else []

    reasons: List[str] = []
    if not missing:
        if not r65["passed"]:
            reasons.append("65%% rule: what stands against the house is %s, which is %.1f%% of the %s value. The limit is %s. The most the note can be is %s."
                           % (_money(r65["exposure"]), (r65["ratio"] or 0) * 100, _money(_n(facts.get("arv"))), _money(r65["limit_amount"]), _money(r65["most_note"])))
        if not fc["finished"]["passed"]:
            reasons.append("Foreclosure test: if the finished house had to be sold in a foreclosure, %s would be left for RKB against %s paid out."
                           % (_money(fc["finished"]["left_for_rkb"]), _money(fc["finished"]["rkb_out"])))
        if fc["closing_day"].get("passed") is False:
            reasons.append("Foreclosure test, closing day: before any work is done, a forced sale of the house as it stands leaves %s for RKB against %s paid out on closing day."
                           % (_money(fc["closing_day"]["left_for_rkb"]), _money(fc["closing_day"]["rkb_out"])))
        reasons.extend(pricing)
    reasons = stops + reasons

    notices: List[str] = []
    if new_state:
        notices.append("%s is a state RKB has not lent in. The tests above use the slowest and costliest foreclosure we know (%d months, %s). "
                       "The file and the documents open once this state's rules have been written and approved."
                       % ((facts.get("state") or "").upper(), S.WORST_CASE["months"], _money(S.WORST_CASE["cost"])))
    if st and owner == S.INDIVIDUAL:
        notices.append(st["lender_licence"])
    notices.extend(S.required_terms(state=facts.get("state") or "", lien=position, owner_kind=owner or "") if st else [])
    if len(m["liens"]) >= 2 and not rejected_second:
        gone = [l["holder"] for l in m["liens"] if l["payoff_at_closing"]]
        notices.append("The house has more than one mortgage today. %s must be paid off and released at closing, so that RKB stands behind one mortgage only." % " and ".join(gone))
    if fc["finished"].get("mortgage_growth_guessed"):
        notices.append("The senior lender's monthly payment is not entered; its growth during a foreclosure is estimated at 7% a year.")
    if not m["tax_sure"] and st:
        notices.append("The recording tax is an estimate until the county is confirmed.")

    if missing:
        verdict = INCOMPLETE
    elif reasons:
        verdict = DOES_NOT_FIT
    elif kind == KIND_D or cured:
        verdict = EXCEPTION
    else:
        verdict = FITS
    notices = cured + notices
    headline = {
        INCOMPLETE: "Still needed: " + ", ".join(missing) + ".",
        DOES_NOT_FIT: "Rejected: the house has a second mortgage." if rejected_second else "Does not fit RKB's rules as entered.",
        EXCEPTION: ("The numbers pass, but the mortgage ahead of RKB is more than 30 days late. Bringing it up to date at closing is Rakesh Sir's decision." if cured and kind != KIND_D
                    else "The numbers pass, but money to the owner or a promised price is never standard. Rakesh Sir decides."),
        FITS: "Fits RKB's rules. %s of room under the 65%% limit." % _money(r65["room"]),
    }[verdict]
    return {"verdict": verdict, "headline": headline, "reasons": reasons, "notices": notices, "missing": missing,
            "rejected_second_mortgage": rejected_second, "new_state": new_state,
            "kind": kind, "kind_label": KIND_LABEL[kind], "lien_position": position, "state": (facts.get("state") or "").upper() or None,
            "state_name": (st or {}).get("name") or ((facts.get("state") or "").upper() or None), "money": m, "rule_65": r65, "foreclosure": fc, "budget": bud,
            "person_checks": ([{"who": "manjunath", "what": "Can this budget finish the work?", "why": bud["looks"]}] if bud["looks"] and not missing else [])}


#: The figures a verified document can move. If one moves by more than REVIEW_CHANGE
#: from what the quick check ran on, the check is run again and the file says so.
WATCHED = ("arv", "builder_cost", "contract_price", "closing_costs", "roi_at_closing")


def moved(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for k in WATCHED:
        a, b = _n(before.get(k)), _n(after.get(k))
        if a > 0 and abs(b - a) / a > REVIEW_CHANGE:
            out.append({"field": k, "was": a, "now": b, "change": (b - a) / a})
    la = sum(l["balance"] for l in _liens(before) if not l["payoff_at_closing"])
    lb = sum(l["balance"] for l in _liens(after) if not l["payoff_at_closing"])
    if la > 0 and abs(lb - la) / la > REVIEW_CHANGE:
        out.append({"field": "mortgage_ahead", "was": la, "now": lb, "change": (lb - la) / la})
    return out


def details_missing(facts: Dict[str, Any]) -> List[str]:
    """Page one: every basic fact of the property and the deal. Nothing here is a
    paper; it is what a person knows or the ARV file already holds. An empty list
    means page one is complete and the quick check can be read."""
    f = facts or {}
    need: List[str] = []

    def want(ok: bool, label: str) -> None:
        if not ok:
            need.append(label)
    want(bool((f.get("address") or "").strip()), "Property address")
    want(len((f.get("state") or "").strip()) == 2, "State")
    if (f.get("state") or "").upper() == "MD":
        want(bool((f.get("county") or "").strip()), "County (Maryland recording tax is by county)")
    want(_n(f.get("arv")) > 0, "Value after repair (ARV)")
    want(_n(f.get("as_is_value")) > 0, "Value as it stands today")
    want(_n(f.get("sqft_above")) > 0, "Square feet above grade")
    want(f.get("sqft_below") not in (None, ""), "Square feet below grade (0 if none)")
    want(f.get("measured") in (True, False), "Whether the area has been measured")
    want(f.get("owner_kind") in S.OWNER_KINDS, "Who owns the house")
    want(bool((f.get("owner_name") or "").strip()), "Owner's name as on the deed")
    want(f.get("owner_lives_there") in (True, False), "Whether the owner lives in the house")
    want(f.get("has_mortgage") in (True, False), "Whether there is a mortgage on the house")
    if f.get("has_mortgage"):
        liens = _liens(f)
        want(bool(liens), "The mortgage: lender and balance")
        for l in liens:
            want(l["balance"] > 0 and l["holder"] != "Lender not named", "The mortgage: lender's name and balance")
    want(_n(f.get("builder_cost")) > 0, "Builder's cost of the work")
    want(f.get("contingency") not in (None, ""), "Contingency (0 if none)")
    want(f.get("scope") in SCOPE_TIERS, "Scope of the work")
    want(_n(f.get("contract_price")) > 0, "Contract price to the owner (the note)")
    want(f.get("roi_at_closing") not in (None, ""), "Amount paid to ROI Blocks at closing (0 if none)")
    want(f.get("closing_costs") not in (None, ""), "Closing costs, estimated (0 if not known yet)")
    return list(dict.fromkeys(need))
