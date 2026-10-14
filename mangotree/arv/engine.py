"""The deterministic valuation engine: sales comparison the way an appraiser
does it on Form 1004, then the investor math from the MAO Rehab Calculator.

Nothing here calls a model. Every adjustment is a line with its rate and the
reason, so the screen can show *why* a comp was moved and the analyst model
can argue with a specific number rather than with a total.

Pipeline
  score()        every candidate sale gets a similarity score 0-100 and the
                 reasons it lost points (distance, age of sale, size, rooms…)
  select()       hard filters (radius, months, type, size band), then the best
                 N, widening the net when too few qualify — and saying so
  adjust()       each comp is adjusted to the subject AS REPAIRED: time/market,
                 GLA, bedrooms, baths, garage, basement, pool, lot, age,
                 condition. Gross and net adjustment percentages are reported
                 against the 25% / 15% guideline and heavy comps lose weight
  reconcile()    weighted indicated value, range, price-per-sqft cross-check,
                 AVM blend when one is available, bracketing check
  confidence()   0-100 from count, similarity, dispersion, adjustment size,
                 data quality; mapped to a grade and a +/- band
  underwrite()   fixed costs (buying, holding, selling, financing), rehab with
                 adders, MAO by the 70% rule and by profit target, profit, ROI,
                 capital stack, LTARV / LTC checks, and a sensitivity grid
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from statistics import median, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .geocode import haversine_miles
from . import rehab as rehab_mod

# Fannie Mae condition and quality ratings (UAD). Each step on the C scale is
# worth a share of value; a renovated subject is C2, a tired-but-livable comp C4.
CONDITION_STEP_PCT = 4.0
CONDITION_RANK = {"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5, "C6": 6}
CONDITION_LABEL = {"C1": "New / never occupied", "C2": "Recently renovated, like new", "C3": "Well maintained, limited updates",
                   "C4": "Adequate, some deferred maintenance", "C5": "Obvious deferred maintenance, needs repairs", "C6": "Substantial damage / uninhabitable"}
PROPERTY_TYPES = ("single_family", "townhouse", "condo", "multi_2_4", "manufactured")

#: The classic 15% net / 25% gross heuristic. Fannie Mae dropped it as a rule in
#: December 2014 (adjustments must simply reflect the market), but reviewers,
#: lenders and Collateral Underwriter still read a comp above these lines as
#: "less comparable", so it is reported as a soft warning and lowers weight.
NET_GUIDE, GROSS_GUIDE = 15.0, 25.0


# ---------------------------------------------------------------- helpers
def _f(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _months_between(d: Optional[str], ref: Optional[datetime] = None) -> Optional[float]:
    if not d:
        return None
    try:
        dt = datetime.fromisoformat(str(d)[:10])
    except ValueError:
        return None
    ref = ref or datetime.now()
    return max(0.0, (ref - dt).days / 30.4375)


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _wmedian(values: Sequence[float], weights: Sequence[float]) -> float:
    pairs = sorted(zip(values, weights))
    total = sum(weights)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0] if pairs else 0.0


def _wmean(values: Sequence[float], weights: Sequence[float]) -> float:
    t = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / t if t else 0.0


def _wstd(values: Sequence[float], weights: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _wmean(values, weights)
    t = sum(weights)
    return math.sqrt(sum(w * (v - m) ** 2 for v, w in zip(values, weights)) / t) if t else 0.0


# ----------------------------------------------------------------- scoring
def score(subject: Dict[str, Any], comp: Dict[str, Any], *, radius: float, months: int, gla_limit: Optional[float] = 0.40) -> Dict[str, Any]:
    """Similarity 0-100 with itemised deductions. Hard failures are listed in
    ``fails`` (the comp is excluded unless the net is widened)."""
    pts = 100.0
    why: List[Dict[str, Any]] = []
    fails: List[str] = []

    def dock(n: float, reason: str):
        nonlocal pts
        if n > 0:
            pts -= n
            why.append({"points": round(n, 1), "reason": reason})

    dist = comp.get("distance_mi")
    if dist is None and comp.get("lat") is not None and subject.get("lat") is not None:
        dist = haversine_miles(subject["lat"], subject["lon"], comp["lat"], comp["lon"])
        comp["distance_mi"] = round(dist, 2)
    if dist is not None:
        if dist > radius:
            fails.append("%.2f mi away (beyond %.1f mi)" % (dist, radius))
        dock(_clip(dist / max(radius, 0.1) * 18, 0, 30), "%.2f miles from the subject" % dist)
    else:
        dock(8, "no coordinates; distance unknown")

    mo = comp.get("months_since_sale")
    if mo is None:
        mo = _months_between(comp.get("sale_date"))
        comp["months_since_sale"] = round(mo, 1) if mo is not None else None
    if mo is not None:
        if mo > months:
            fails.append("sold %.0f months ago (older than %d months)" % (mo, months))
        dock(_clip(mo / max(months, 1) * 14, 0, 24), "sold %.0f months ago" % mo)
    else:
        dock(10, "sale date unknown")

    s_sqft, c_sqft = _f(subject.get("sqft")), _f(comp.get("sqft"))
    if s_sqft and c_sqft:
        d = abs(c_sqft - s_sqft) / s_sqft
        if gla_limit is not None and d > gla_limit:
            fails.append("living area differs by %.0f%% (over %.0f%%)" % (d * 100, gla_limit * 100))
        dock(_clip(d * 60, 0, 22), "living area differs by %.0f%%" % (d * 100))
    elif not c_sqft:
        dock(10, "living area unknown")

    if subject.get("beds") is not None and comp.get("beds") is not None:
        d = abs(float(subject["beds"]) - float(comp["beds"]))
        dock(min(d * 5, 12), "%d bedroom difference" % d if d else "")
    if subject.get("baths") is not None and comp.get("baths") is not None:
        d = abs(float(subject["baths"]) - float(comp["baths"]))
        dock(min(d * 4, 10), "%.1f bath difference" % d if d else "")

    sy, cy = _f(subject.get("year_built")), _f(comp.get("year_built"))
    if sy and cy:
        d = abs(sy - cy)
        dock(_clip((d - 10) * 0.3, 0, 8) if d > 10 else 0, "built %d years apart" % d)

    st, ct = subject.get("property_type"), comp.get("property_type")
    if st and ct and st != ct:
        if {st, ct} <= {"single_family", "townhouse"}:
            dock(8, "%s vs %s" % (ct.replace("_", " "), st.replace("_", " ")))
        else:
            fails.append("different property type (%s)" % ct.replace("_", " "))
            dock(25, "different property type")

    sl, cl = _f(subject.get("lot_sqft")), _f(comp.get("lot_sqft"))
    if sl and cl:
        d = abs(cl - sl) / sl
        dock(_clip((d - 0.25) * 10, 0, 6) if d > 0.25 else 0, "lot size differs by %.0f%%" % (d * 100))

    if comp.get("source") == "web":
        dock(6, "found by web search; figures unverified against a listing feed")
    if comp.get("price_basis") == "listed":
        dock(5, "asking price, not a closed sale price")
    elif comp.get("price_basis") == "estimated":
        dock(12, "an estimate, not a sale")
    if comp.get("source") == "manual" and not comp.get("sale_price"):
        fails.append("no sale price")
    if not _f(comp.get("sale_price")):
        fails.append("no sale price")

    return {"score": round(_clip(pts, 0, 100), 1), "deductions": [w for w in why if w["reason"]], "fails": fails}


def select(subject: Dict[str, Any], comps: List[Dict[str, Any]], *, radius: float, months: int, target: int, minimum: int = 3) -> Dict[str, Any]:
    """Score everything, keep what passes, widen if necessary. Honours the user's
    include/exclude flags: an excluded comp never counts; a forced-in comp always does."""
    notes: List[str] = []
    r, m = radius, months
    gla_limit: Optional[float] = 0.40

    def rescore():
        for c in comps:
            sc = score(subject, c, radius=r, months=m, gla_limit=gla_limit)
            c["similarity"] = sc["score"]
            c["deductions"] = sc["deductions"]
            c["fails"] = sc["fails"]
        return [c for c in comps if not c["fails"] and c.get("included", True) is not False]

    for attempt in range(4):
        passing = rescore()
        # Aim for a full grid (up to five) before settling for the minimum of three.
        if len(passing) >= min(target, 5) or attempt == 3 or (attempt >= 1 and len(passing) >= minimum):
            break
        r, m = round(r * 1.5, 2), int(m * 1.5)
        notes.append("Only %d sale(s) qualified within %.1f mi / %d months; widened to %.1f mi / %d months." % (len(passing), r / 1.5, int(m / 1.5), r, m))
    # Still short: the size filter is what usually empties a rural or unusual file. Relax it in two steps
    # rather than return no value, and say so — the person should check the subject's living area.
    if len(passing) < minimum and any("living area differs" in f for c in comps for f in c["fails"]):
        for limit in (0.60, None):
            gla_limit = limit
            passing = rescore()
            notes.append("No sale within %s of the subject's living area; the size filter was %s. Check the subject's living area (a composite figure for several buildings will do this)."
                         % ("40%" if limit == 0.60 else "60%", "relaxed to 60%" if limit == 0.60 else "dropped (size still costs fit points)"))
            if len(passing) >= minimum:
                break
    forced = [c for c in comps if c.get("included") is True and c["fails"]]
    for c in forced:
        c["forced"] = True
    pool = sorted(passing + forced, key=lambda c: -c["similarity"])
    chosen = pool[:max(target, minimum)]
    for c in comps:
        c["selected"] = c in chosen
    return {"selected": chosen, "radius_used": r, "months_used": m, "notes": notes, "candidates": len(comps), "qualified": len(passing)}


# -------------------------------------------------------------- adjustments
def _rates(subject: Dict[str, Any], comps: List[Dict[str, Any]], market: Dict[str, Any]) -> Dict[str, Any]:
    """Adjustment rates derived from the selected sales themselves, bounded by
    appraisal practice, so a $200k market and a $2m market get sensible dollars."""
    prices = [_f(c.get("sale_price")) for c in comps if _f(c.get("sale_price"))]
    ppsf = [_f(c["sale_price"]) / _f(c["sqft"]) for c in comps if _f(c.get("sale_price")) and _f(c.get("sqft"))]
    med_price = median(prices) if prices else _f(subject.get("avm_value")) or 300000.0
    med_ppsf = median(ppsf) if ppsf else (med_price / _f(subject.get("sqft"), 1500.0))
    # GLA: paired-sales regression across the comps (price on sqft), clipped to
    # 25-45% of the median $/sqft — the land and the core of the house are not
    # bought by the foot, only the marginal space is (practitioner range $25-110).
    gla_rate = med_ppsf * 0.35
    pts = [(_f(c["sqft"]), _f(c["sale_price"])) for c in comps if _f(c.get("sqft")) and _f(c.get("sale_price"))]
    if len(pts) >= 4:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx > 0:
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            if slope > 0:
                gla_rate = _clip(slope, med_ppsf * 0.25, med_ppsf * 0.45)
    zh = (market or {}).get("zhvi") or {}
    monthly = zh.get("monthly_rate_pct")
    if monthly is None:
        monthly = 0.25  # flat-to-slow market default when no index is available
    return {
        "median_sale_price": round(med_price), "median_ppsf": round(med_ppsf, 2),
        "gla_per_sqft": round(gla_rate, 2), "gla_basis": "paired-sales slope across selected comps, bounded to 25-45% of median $/sqft" if len(pts) >= 4 else "35% of median $/sqft (too few comps for a regression)",
        # Practitioner ranges (2026): full bath $3-5k (more in high-price metros),
        # half bath $1.5-2k, garage $3-5k a bay, bedrooms mostly captured by GLA,
        # finished basement 50-75% of the GLA rate, pool local and often small.
        "bedroom": round(_clip(med_price * 0.01, 2000, 10000)), "bath_full": round(_clip(med_price * 0.012, 3000, 12000)),
        "bath_half": round(_clip(med_price * 0.006, 1500, 6000)), "garage_space": round(_clip(med_price * 0.01, 3000, 10000)),
        "basement_finished": round(_clip(med_price * 0.04, 8000, 40000)), "basement_unfinished": round(_clip(med_price * 0.015, 4000, 15000)),
        "pool": round(_clip(med_price * 0.02, 5000, 30000)), "lot_per_sqft": round(_clip(med_price / 100000.0, 0.5, 25.0), 2),
        "age_per_year_pct": 0.25, "condition_step_pct": CONDITION_STEP_PCT,
        "market_monthly_pct": monthly, "market_basis": ("Zillow ZHVI %s: %+.1f%% over 12 months" % (zh.get("zip"), zh.get("change_12m_pct"))) if zh.get("change_12m_pct") is not None else "no index for this ZIP; 0.25%/month assumed",
    }


def adjust(subject: Dict[str, Any], comp: Dict[str, Any], rates: Dict[str, Any]) -> Dict[str, Any]:
    price = _f(comp.get("sale_price")) or 0.0
    lines: List[Dict[str, Any]] = []

    def line(key: str, label: str, amount: float, why: str, rate: Any = None):
        if abs(amount) >= 1:
            lines.append({"key": key, "label": label, "amount": round(amount), "why": why, "rate": rate})

    mo = comp.get("months_since_sale")
    if mo:
        amt = price * (rates["market_monthly_pct"] / 100.0) * mo
        line("time", "Time / market conditions", amt, "%.0f months since sale at %.2f%%/month (%s)" % (mo, rates["market_monthly_pct"], rates["market_basis"]), rates["market_monthly_pct"])
    s_sqft, c_sqft = _f(subject.get("sqft")), _f(comp.get("sqft"))
    if s_sqft and c_sqft:
        line("gla", "Living area", (s_sqft - c_sqft) * rates["gla_per_sqft"], "%+d sqft at $%s/sqft (%s)" % (s_sqft - c_sqft, rates["gla_per_sqft"], rates["gla_basis"]), rates["gla_per_sqft"])
    if subject.get("beds") is not None and comp.get("beds") is not None:
        d = float(subject["beds"]) - float(comp["beds"])
        line("beds", "Bedrooms", d * rates["bedroom"], "%+d bedroom(s) at $%s each" % (d, rates["bedroom"]), rates["bedroom"])
    if subject.get("baths") is not None and comp.get("baths") is not None:
        d = float(subject["baths"]) - float(comp["baths"])
        full, half = math.trunc(d), round((d - math.trunc(d)) * 2) / 2
        amt = full * rates["bath_full"] + (half / 0.5) * rates["bath_half"]
        line("baths", "Bathrooms", amt, "%+.1f bath(s): $%s per full, $%s per half" % (d, rates["bath_full"], rates["bath_half"]), rates["bath_full"])
    sg, cg = _f(subject.get("garage_spaces")), _f(comp.get("garage_spaces"))
    if sg is not None and cg is not None:
        line("garage", "Garage / parking", (sg - cg) * rates["garage_space"], "%+d space(s) at $%s" % (sg - cg, rates["garage_space"]), rates["garage_space"])
    sb, cb = (subject.get("basement") or "unknown"), (comp.get("basement") or "unknown")
    if sb != "unknown" and cb != "unknown" and sb != cb:
        val = {"none": 0, "unfinished": rates["basement_unfinished"], "finished": rates["basement_finished"]}
        line("basement", "Basement", val.get(sb, 0) - val.get(cb, 0), "subject %s vs comp %s" % (sb, cb))
    sp, cp = subject.get("pool"), comp.get("pool")
    if sp is not None and cp is not None and bool(sp) != bool(cp):
        line("pool", "Pool", (rates["pool"] if sp else -rates["pool"]), "subject %s a pool, comp %s" % ("has" if sp else "lacks", "does" if cp else "does not"), rates["pool"])
    sl, cl = _f(subject.get("lot_sqft")), _f(comp.get("lot_sqft"))
    if sl and cl and abs(sl - cl) / sl > 0.15:
        amt = _clip((sl - cl) * rates["lot_per_sqft"], -0.10 * price, 0.10 * price)
        line("lot", "Lot size", amt, "%+d sqft of lot at $%s/sqft, capped at 10%% of price" % (sl - cl, rates["lot_per_sqft"]), rates["lot_per_sqft"])
    sy, cy = _f(subject.get("year_built")), _f(comp.get("year_built"))
    if sy and cy and abs(sy - cy) > 5:
        pct = _clip((sy - cy) * rates["age_per_year_pct"], -10, 10)
        line("age", "Age (effective)", price * pct / 100.0, "%+d years at %.2f%%/yr, capped at 10%%" % (sy - cy, rates["age_per_year_pct"]), rates["age_per_year_pct"])
    s_cond = subject.get("condition_after") or "C2"
    c_cond = comp.get("condition") or "C3"
    if s_cond in CONDITION_RANK and c_cond in CONDITION_RANK and s_cond != c_cond:
        steps = CONDITION_RANK[c_cond] - CONDITION_RANK[s_cond]
        line("condition", "Condition (as repaired)", price * steps * rates["condition_step_pct"] / 100.0,
             "subject as repaired %s (%s) vs comp %s (%s): %+d step(s) at %.0f%% each" % (s_cond, CONDITION_LABEL[s_cond], c_cond, CONDITION_LABEL.get(c_cond, ""), steps, rates["condition_step_pct"]), rates["condition_step_pct"])
    for extra in comp.get("manual_adjustments") or []:
        amt = _f(extra.get("amount"))
        if amt:
            line("manual:" + str(extra.get("label") or "other"), extra.get("label") or "Manual adjustment", amt, extra.get("why") or "entered by the user")

    net = sum(l["amount"] for l in lines)
    gross = sum(abs(l["amount"]) for l in lines)
    net_pct = (net / price * 100) if price else 0.0
    gross_pct = (gross / price * 100) if price else 0.0
    flags = []
    if abs(net_pct) > NET_GUIDE:
        flags.append("net adjustment %.0f%% is above the %d%% heuristic" % (abs(net_pct), NET_GUIDE))
    if gross_pct > GROSS_GUIDE:
        flags.append("gross adjustment %.0f%% is above the %d%% heuristic" % (gross_pct, GROSS_GUIDE))
    return {"lines": lines, "net": round(net), "gross": round(gross), "net_pct": round(net_pct, 1), "gross_pct": round(gross_pct, 1),
            "adjusted_price": round(price + net), "adjusted_ppsf": round((price + net) / c_sqft, 2) if c_sqft else None, "flags": flags}


# ------------------------------------------------------------- reconcile
def reconcile(subject: Dict[str, Any], comps: List[Dict[str, Any]], rates: Dict[str, Any], avm: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sel = [c for c in comps if c.get("selected") and c.get("adjustment")]
    if not sel:
        return {"indicated_value": None, "error": "No comparable sale qualified. Add comps by hand or widen the search."}
    weights, values = [], []
    # A sale far off the pack's $/sqft is usually an as-is or distressed sale (or
    # a data error) the condition rating did not capture: keep it, halve it, say so.
    ppsf_all = [_f(c["sale_price"]) / _f(c["sqft"]) for c in sel if _f(c.get("sqft")) and _f(c.get("sale_price"))]
    ppsf_med = median(ppsf_all) if ppsf_all else None
    for c in sel:
        a = c["adjustment"]
        mo = c.get("months_since_sale") or 0
        w = (c["similarity"] / 100.0) ** 2 * (1.0 - _clip(a["gross_pct"] / 100.0, 0, 0.6)) * (1.0 / (1.0 + mo / 12.0))
        if ppsf_med and _f(c.get("sqft")) and _f(c.get("sale_price")):
            dev = _f(c["sale_price"]) / _f(c["sqft"]) / ppsf_med - 1
            if abs(dev) > 0.35:
                w *= 0.5
                flag = "sold at %+.0f%% of the pack's $/sqft: likely as-is/distressed or a data error; weight halved" % (dev * 100)
                if flag not in a["flags"]:
                    a["flags"].append(flag)
        if c.get("source") == "web":
            w *= 0.75
        if c.get("price_basis") == "listed":
            w *= 0.7
        elif c.get("price_basis") == "estimated":
            w *= 0.4
        if c.get("forced"):
            w *= 0.6
        c["weight_raw"] = w
        weights.append(w)
        values.append(float(a["adjusted_price"]))
    tw = sum(weights) or 1.0
    for c, w in zip(sel, weights):
        c["weight"] = round(w / tw * 100, 1)
    wm = _wmean(values, weights)
    wmed = _wmedian(values, weights)
    sd = _wstd(values, weights)
    lo, hi = min(values), max(values)
    s_sqft = _f(subject.get("sqft"))
    ppsf_vals = [c["adjustment"]["adjusted_ppsf"] for c in sel if c["adjustment"].get("adjusted_ppsf")]
    ppsf_ind = (_wmedian(ppsf_vals, [c["weight_raw"] for c in sel if c["adjustment"].get("adjusted_ppsf")]) * s_sqft) if (ppsf_vals and s_sqft) else None
    sca = wm * 0.7 + wmed * 0.3
    # Bracketing: the subject should sit inside the comps' range of size and of
    # adjusted value; an un-bracketed subject is extrapolation and says so.
    sizes = [_f(c.get("sqft")) for c in sel if _f(c.get("sqft"))]
    bracket = {"size": bool(s_sqft and sizes and min(sizes) <= s_sqft <= max(sizes)),
               "value": bool(lo <= sca <= hi), "unadjusted": bool(min(_f(c["sale_price"]) for c in sel) <= sca <= max(_f(c["sale_price"]) for c in sel))}
    avm_value = _f((avm or {}).get("value"))
    blend_note = None
    final = sca
    if avm_value:
        diff = (avm_value - sca) / sca
        if abs(diff) <= 0.15:
            final = sca * 0.8 + avm_value * 0.2
            blend_note = "AVM %s is within %.0f%% of the sales comparison; blended at 20%% weight." % (avm.get("source", "AVM"), abs(diff) * 100)
        else:
            blend_note = "AVM %s differs by %+.0f%% from the sales comparison; not blended, flagged for review." % (avm.get("source", "AVM"), diff * 100)
    # The range is one weighted standard deviation either side (at least 3%):
    # the band a careful reader should expect the market to land in.
    band = max(sd, final * 0.03)
    return {
        "indicated_value": round(final, -2), "sales_comparison": round(sca, -2), "weighted_mean": round(wm, -2), "weighted_median": round(wmed, -2),
        "range_low": round(final - band, -2), "range_high": round(final + band, -2), "comp_low": round(lo, -2), "comp_high": round(hi, -2),
        "std_dev": round(sd), "cv_pct": round(sd / wm * 100, 1) if wm else None,
        "ppsf_indication": round(ppsf_ind, -2) if ppsf_ind else None, "ppsf_used": round(_wmedian(ppsf_vals, [c["weight_raw"] for c in sel if c["adjustment"].get("adjusted_ppsf")]), 2) if ppsf_vals else None,
        "avm": avm, "avm_note": blend_note, "bracketing": bracket, "comps_used": len(sel),
        "value_per_sqft": round(final / s_sqft, 2) if s_sqft else None,
    }


def confidence(subject: Dict[str, Any], comps: List[Dict[str, Any]], rec: Dict[str, Any], selection: Dict[str, Any]) -> Dict[str, Any]:
    sel = [c for c in comps if c.get("selected")]
    if not sel or rec.get("indicated_value") is None:
        return {"score": 0, "grade": "F", "band_pct": None, "reasons": ["no usable comparable sales"]}
    pts = 0.0
    reasons = []
    n = len(sel)
    pts += min(n, 6) / 6 * 25
    reasons.append("%d comparable sales used" % n + ("" if n >= 4 else " (fewer than 4 weakens the indication)"))
    sim = sum(c["similarity"] for c in sel) / n
    pts += sim / 100 * 25
    reasons.append("average similarity %.0f/100" % sim)
    cv = rec.get("cv_pct") or 0
    disp = _clip(1 - cv / 20.0, 0, 1)
    pts += disp * 20
    reasons.append("adjusted values spread %.1f%% (coefficient of variation)" % cv)
    gross = sum(c["adjustment"]["gross_pct"] for c in sel) / n
    pts += _clip(1 - gross / 40.0, 0, 1) * 15
    reasons.append("average gross adjustment %.0f%%" % gross + (" (above the 25% heuristic)" if gross > GROSS_GUIDE else ""))
    src = {"rentcast": 1.0, "attom": 1.0, "manual": 0.85, "web": 0.6}
    q = sum(src.get(c.get("source"), 0.7) for c in sel) / n
    pts += q * 10
    reasons.append("data quality %.0f%% (%s)" % (q * 100, ", ".join(sorted({str(c.get("source")) for c in sel}))))
    pts += 5 if rec.get("bracketing", {}).get("value") and rec.get("bracketing", {}).get("size") else 0
    if not rec.get("bracketing", {}).get("size"):
        reasons.append("subject size is not bracketed by the comps")
    if selection.get("notes"):
        pts -= 5
        reasons.append("search was widened to find enough sales")
    score_ = round(_clip(pts, 0, 100))
    grade = "A" if score_ >= 85 else "B" if score_ >= 70 else "C" if score_ >= 55 else "D" if score_ >= 40 else "E"
    band = {"A": 4, "B": 7, "C": 11, "D": 16, "E": 22}[grade]
    return {"score": score_, "grade": grade, "band_pct": band, "reasons": reasons,
            "label": {"A": "High confidence", "B": "Good confidence", "C": "Moderate confidence", "D": "Low confidence", "E": "Very low confidence"}[grade]}


def value(subject: Dict[str, Any], comps: List[Dict[str, Any]], market: Dict[str, Any], avm: Optional[Dict[str, Any]], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Sales comparison end to end. Mutates ``comps`` in place with scores,
    adjustments and weights, and returns the valuation block."""
    from .config import COMP_MONTHS, COMP_RADIUS_MILES, COMP_TARGET
    radius = _f((settings or {}).get("radius_mi"), COMP_RADIUS_MILES)
    months = int(_f((settings or {}).get("months"), COMP_MONTHS))
    target = int(_f((settings or {}).get("target"), COMP_TARGET))
    selection = select(subject, comps, radius=radius, months=months, target=target)
    rates = _rates(subject, selection["selected"], market)
    for c in comps:
        c["adjustment"] = adjust(subject, c, rates) if c.get("selected") else None
    rec = reconcile(subject, comps, rates, avm)
    conf = confidence(subject, comps, rec, selection)
    return {"selection": {k: v for k, v in selection.items() if k != "selected"}, "rates": rates, "reconciliation": rec, "confidence": conf,
            "arv": rec.get("indicated_value"), "arv_low": rec.get("range_low"), "arv_high": rec.get("range_high"),
            "computed_at": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------ underwriting
#: Seller-paid transfer / recordation taxes by state, percent of price (2026,
#: typical; counties vary). Used as a selling-cost default the user can override.
TRANSFER_TAX_PCT = {"DC": 1.45, "MD": 1.0, "VA": 0.1, "FL": 0.7, "PA": 1.0, "NY": 0.4, "NJ": 1.0, "DE": 1.5, "WA": 1.28, "CA": 0.11,
                    "IL": 0.15, "MA": 0.456, "CT": 1.0, "NH": 0.75, "VT": 1.25, "MI": 0.86, "MN": 0.33, "WI": 0.3, "NC": 0.2, "SC": 0.37,
                    "GA": 0.1, "TN": 0.37, "CO": 0.01, "NV": 0.51, "HI": 0.1, "OK": 0.15, "AR": 0.33, "AL": 0.1, "KY": 0.1, "WV": 0.33,
                    "OH": 0.3, "IA": 0.16, "NE": 0.225, "SD": 0.1, "ME": 0.22, "RI": 0.46}

DEFAULT_ASSUMPTIONS: Dict[str, Any] = {
    "purchase_price": None, "months_held": 8, "rule_pct": 70.0, "profit_target_pct": 15.0, "profit_target_min": 25000,
    # buying costs (MAO Rehab Calculator averages)
    "buy_brokerage": 300, "buy_inspection": 400, "buy_appraisal": 500, "buy_title_search": 300, "buy_title_insurance": 150, "buy_other": 0,
    # holding, per year unless stated
    "property_taxes_annual": None, "insurance_annual": 3600, "utilities_monthly": 250, "hoa_monthly": 0, "maintenance_monthly": 50, "holding_other_monthly": 0,
    # selling
    "commission_pct": 5.0, "seller_assist_pct": 0.0, "sell_brokerage": 300, "home_warranty": 450, "sell_title_insurance": 150, "second_appraisal": 0, "transfer_tax_pct": None, "sell_other": 0,
    # financing (hard money, interest-only)
    "finance": True, "ltc_pct": 85.0, "ltarv_pct": 70.0, "rate_pct": 10.5, "points_pct": 2.0, "loan_fees": 1500, "rehab_financed_pct": 100.0,
}


def underwrite(arv: Optional[float], rehab_total: float, assumptions: Dict[str, Any], *, state: Optional[str], sqft: Optional[float] = None,
               market: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    a = {**DEFAULT_ASSUMPTIONS, **{k: v for k, v in (assumptions or {}).items() if v is not None or k == "purchase_price"}}
    arv0 = _f(arv) or 0.0
    rehab0 = _f(rehab_total) or 0.0
    months = _f(a.get("months_held"), 8.0) or 8.0
    taxes_set = _f(a.get("property_taxes_annual"))
    transfer_pct = _f(a.get("transfer_tax_pct"))
    if transfer_pct is None:
        transfer_pct = TRANSFER_TAX_PCT.get((state or "").upper(), 0.5)
    buying = {"brokerage": _f(a["buy_brokerage"], 0), "inspection": _f(a["buy_inspection"], 0), "appraisal": _f(a["buy_appraisal"], 0),
              "title_search": _f(a["buy_title_search"], 0), "title_insurance": _f(a["buy_title_insurance"], 0), "other": _f(a["buy_other"], 0)}
    buying["total"] = sum(buying.values())

    def holding_for(arv_x: float) -> Dict[str, Any]:
        # about 1.1% of value a year nationally when no tax bill is entered
        taxes_annual = taxes_set if taxes_set is not None else arv_x * 0.011
        h = {"months": months, "property_taxes": taxes_annual / 12 * months, "insurance": _f(a["insurance_annual"], 0) / 12 * months,
             "utilities": _f(a["utilities_monthly"], 0) * months, "hoa": _f(a["hoa_monthly"], 0) * months, "maintenance": _f(a["maintenance_monthly"], 0) * months,
             "other": _f(a["holding_other_monthly"], 0) * months, "taxes_annual_used": taxes_annual, "taxes_basis": "entered" if taxes_set is not None else "1.1% of ARV assumed"}
        h["monthly"] = sum(h[k] for k in ("property_taxes", "insurance", "utilities", "hoa", "maintenance", "other")) / months
        h["total"] = h["monthly"] * months
        return h

    def selling_for(arv_x: float) -> Dict[str, Any]:
        s = {"commission": arv_x * _f(a["commission_pct"], 0) / 100, "seller_assist": arv_x * _f(a["seller_assist_pct"], 0) / 100, "transfer_tax": arv_x * transfer_pct / 100,
             "brokerage": _f(a["sell_brokerage"], 0), "home_warranty": _f(a["home_warranty"], 0), "title_insurance": _f(a["sell_title_insurance"], 0),
             "second_appraisal": _f(a["second_appraisal"], 0), "other": _f(a["sell_other"], 0), "transfer_tax_pct": transfer_pct}
        s["total"] = sum(v for k, v in s.items() if k != "transfer_tax_pct")
        return s

    def financing_for(price: float, arv_x: float, rehab_x: float) -> Dict[str, Any]:
        if not a.get("finance"):
            return {"loan": 0.0, "interest": 0.0, "points": 0.0, "fees": 0.0, "total": 0.0, "ltarv_pct": 0.0, "ltc_pct": 0.0, "binding": "cash"}
        cost_basis = price + rehab_x * _f(a["rehab_financed_pct"], 100) / 100
        by_ltc = cost_basis * _f(a["ltc_pct"], 0) / 100
        by_ltarv = arv_x * _f(a["ltarv_pct"], 0) / 100
        loan = max(0.0, min(by_ltc, by_ltarv))
        interest = loan * _f(a["rate_pct"], 0) / 100 * months / 12
        points = loan * _f(a["points_pct"], 0) / 100
        fees = _f(a["loan_fees"], 0)
        return {"loan": loan, "interest": interest, "points": points, "fees": fees, "total": interest + points + fees,
                "ltarv_pct": (loan / arv_x * 100) if arv_x else 0.0, "ltc_pct": (loan / cost_basis * 100) if cost_basis else 0.0,
                "binding": "LTARV cap" if by_ltarv < by_ltc else "LTC cap"}

    def deal_at(price: float, arv_x: float, rehab_x: float) -> Dict[str, Any]:
        price = max(price, 0.0)
        fin = financing_for(price, arv_x, rehab_x)
        hold, sell = holding_for(arv_x), selling_for(arv_x)
        fixed = buying["total"] + hold["total"] + sell["total"] + fin["total"]
        total_cost = price + rehab_x + fixed
        profit = arv_x - total_cost
        cash_needed = total_cost - fin["loan"]
        fin_out = {k: (round(v, 1) if k in ("ltarv_pct", "ltc_pct") else (round(v) if isinstance(v, float) else v)) for k, v in fin.items()}
        return {"purchase_price": round(price), "fixed_costs": round(fixed), "financing": fin_out, "holding_total": round(hold["total"]), "selling_total": round(sell["total"]),
                "total_project_cost": round(total_cost), "profit": round(profit), "profit_pct_of_arv": round(profit / arv_x * 100, 1) if arv_x else None,
                "profit_per_month": round(profit / months) if months else None, "cash_needed": round(cash_needed),
                "cash_roi_pct": round(profit / cash_needed * 100, 1) if cash_needed > 0 else None,
                "annualized_roi_pct": round(profit / cash_needed * 100 * 12 / months, 1) if cash_needed > 0 and months else None,
                "capital": {"debt": round(fin["loan"]), "equity": round(cash_needed), "total": round(total_cost)}}

    rule_pct = _f(a["rule_pct"], 70.0)
    mao_rule = rule_pct / 100 * arv0 - rehab0
    # MAO by profit target: the price at which profit equals the target. Fixed
    # costs depend on the price through financing, so solve by fixed-point iteration.
    target = max(arv0 * _f(a["profit_target_pct"], 0) / 100, _f(a["profit_target_min"], 0))
    p = max(mao_rule, 0.0)
    for _ in range(40):
        d = deal_at(p, arv0, rehab0)
        p_next = max(p + (d["profit"] - target), 0.0)
        if abs(p_next - p) < 25:
            p = p_next
            break
        p = p_next
    mao_target = p
    price = _f(a.get("purchase_price"))
    chosen = price if price is not None else min(mao_rule, mao_target)
    deal = deal_at(chosen, arv0, rehab0)
    grid: List[Dict[str, Any]] = []
    for arv_d in (-10, -5, 0, 5, 10):
        row = {"arv_delta_pct": arv_d, "cells": []}
        for reh_d in (-20, -10, 0, 10, 20):
            dd = deal_at(chosen, arv0 * (1 + arv_d / 100), rehab0 * (1 + reh_d / 100))
            row["cells"].append({"rehab_delta_pct": reh_d, "profit": dd["profit"], "profit_pct": dd["profit_pct_of_arv"]})
        grid.append(row)
    hold0, sell0 = holding_for(arv0), selling_for(arv0)
    return {
        "assumptions": a, "arv": round(arv0), "rehab_total": round(rehab0),
        "buying": {k: round(v) for k, v in buying.items()},
        "holding": {k: (round(v) if isinstance(v, float) and k != "months" else v) for k, v in hold0.items()},
        "selling": {k: (round(v) if k != "transfer_tax_pct" else v) for k, v in sell0.items()},
        "mao": {"rule": round(mao_rule), "rule_pct": rule_pct, "profit_target": round(mao_target), "target_profit": round(target),
                "recommended": round(min(mao_rule, mao_target)), "basis": "the lower of the %.0f%% rule and the price that leaves the target profit" % rule_pct},
        "deal": deal, "price_basis": "purchase price entered" if price is not None else "recommended MAO (no purchase price entered)",
        "checks": _checks(arv0, rehab0, deal, a), "sensitivity": grid,
        "value_per_sqft": round(arv0 / sqft, 2) if sqft and arv0 else None, "rehab_per_sqft": round(rehab0 / sqft, 1) if sqft else None,
    }


def _checks(arv: float, rehab_total: float, deal: Dict[str, Any], a: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    fin = deal.get("financing") or {}
    if a.get("finance"):
        out.append({"key": "ltarv", "label": "Loan-to-ARV", "value": fin.get("ltarv_pct"), "limit": _f(a["ltarv_pct"]), "ok": (fin.get("ltarv_pct") or 0) <= _f(a["ltarv_pct"], 0) + 0.05,
                    "note": "Lenders cap the loan at %.0f%% of ARV; binding constraint here: %s." % (_f(a["ltarv_pct"], 0), fin.get("binding"))})
    pct = deal.get("profit_pct_of_arv")
    out.append({"key": "margin", "label": "Profit as % of ARV", "value": pct, "limit": _f(a["profit_target_pct"]), "ok": (pct or 0) >= _f(a["profit_target_pct"], 0),
                "note": "Flippers and lenders look for 10-15% of ARV after every cost; below 10% a 5% miss on ARV erases the deal."})
    rr = (rehab_total / arv * 100) if arv else 0
    out.append({"key": "rehab_ratio", "label": "Rehab as % of ARV", "value": round(rr, 1), "limit": 35, "ok": rr <= 35,
                "note": "Above about 35% the project carries construction risk that comps cannot price; above 50% it is a build, not a flip."})
    roi = deal.get("annualized_roi_pct")
    out.append({"key": "roi", "label": "Annualized cash ROI", "value": roi, "limit": 25, "ok": (roi or 0) >= 25,
                "note": "Hard-money flips are typically underwritten to 25%+ annualized on cash in."})
    return out
