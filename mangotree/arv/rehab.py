"""Rehab cost estimate: scope tiers in dollars per square foot of living area,
a regional multiplier by state, and the line-item template from the MAO Rehab
Calculator so a budget can be entered the way the team already does it.

The tier figures are 2026 national mid-points for investor-grade finishes
(not owner-occupant custom work) and are only a starting point: a figure the
person enters per line always wins, and the model is asked to challenge the
tier against the described condition.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: name -> (low $/sqft, mid $/sqft, high $/sqft, what it covers)
SCOPE_TIERS: Dict[str, Dict[str, Any]] = {
    "cosmetic": {"label": "Cosmetic refresh", "low": 15, "mid": 26, "high": 40,
                 "covers": "Paint, flooring, lighting and plumbing fixtures, hardware, landscaping clean-up, minor repairs; kitchen and baths kept."},
    "moderate": {"label": "Moderate renovation", "low": 35, "mid": 55, "high": 80,
                 "covers": "Cosmetic scope plus new kitchen (cabinets, counters, appliances) and bath remodels, some windows/doors, one major system (roof, HVAC or water heater)."},
    "full_gut": {"label": "Full gut renovation", "low": 70, "mid": 100, "high": 145,
                 "covers": "Down to studs: all systems (electrical, plumbing, HVAC), roof, windows, insulation, drywall, kitchen, baths, exterior; layout changes."},
    "addition": {"label": "Gut plus addition / structural", "low": 150, "mid": 200, "high": 300,
                 "covers": "Full gut plus added square footage, structural work, foundation or basement dig-out; permits and architect drawings assumed."},
}

#: Construction cost relative to the national average by state, 2026 (RSMeans /
#: city-cost-index style, residential renovation). Metro premiums inside a state
#: (NoVA vs rural VA; NYC vs upstate) are handled by the model's critique and
#: by the optional location multiplier.
STATE_COST_INDEX: Dict[str, float] = {
    "AL": 0.86, "AK": 1.24, "AZ": 0.94, "AR": 0.85, "CA": 1.26, "CO": 1.01, "CT": 1.12, "DE": 1.03, "DC": 1.14, "FL": 0.95,
    "GA": 0.89, "HI": 1.32, "ID": 0.94, "IL": 1.08, "IN": 0.92, "IA": 0.93, "KS": 0.90, "KY": 0.90, "LA": 0.88, "ME": 0.97,
    "MD": 1.03, "MA": 1.18, "MI": 0.97, "MN": 1.05, "MS": 0.84, "MO": 0.95, "MT": 0.96, "NE": 0.91, "NV": 1.03, "NH": 1.01,
    "NJ": 1.17, "NM": 0.90, "NY": 1.22, "NC": 0.89, "ND": 0.95, "OH": 0.95, "OK": 0.86, "OR": 1.06, "PA": 1.04, "RI": 1.10,
    "SC": 0.88, "SD": 0.91, "TN": 0.88, "TX": 0.91, "UT": 0.96, "VT": 0.99, "VA": 0.96, "WA": 1.09, "WV": 0.92, "WI": 0.99, "WY": 0.93,
}

#: The MAO Rehab Calculator's repair lines, in its order. Each carries a share of
#: a tier budget so a tier estimate can be spread into lines the person then edits.
LINE_ITEMS: List[Dict[str, Any]] = [
    {"key": "general_conditions", "label": "General conditions", "share": {"cosmetic": 0.06, "moderate": 0.06, "full_gut": 0.06, "addition": 0.06}},
    {"key": "demolition", "label": "Demolition", "share": {"cosmetic": 0.02, "moderate": 0.04, "full_gut": 0.05, "addition": 0.05}},
    {"key": "structural_concrete", "label": "Structural / concrete", "share": {"cosmetic": 0.0, "moderate": 0.01, "full_gut": 0.04, "addition": 0.12}},
    {"key": "concrete_flatwork", "label": "Concrete & flatwork", "share": {"cosmetic": 0.0, "moderate": 0.01, "full_gut": 0.02, "addition": 0.03}},
    {"key": "kitchen", "label": "Kitchen", "share": {"cosmetic": 0.06, "moderate": 0.16, "full_gut": 0.11, "addition": 0.08}},
    {"key": "siding", "label": "Siding / exterior", "share": {"cosmetic": 0.02, "moderate": 0.04, "full_gut": 0.05, "addition": 0.05}},
    {"key": "decking_patios", "label": "Decking and patios", "share": {"cosmetic": 0.01, "moderate": 0.02, "full_gut": 0.02, "addition": 0.02}},
    {"key": "roofing", "label": "Roofing", "share": {"cosmetic": 0.0, "moderate": 0.07, "full_gut": 0.05, "addition": 0.05}},
    {"key": "exterior_doors_windows", "label": "Exterior doors & windows", "share": {"cosmetic": 0.02, "moderate": 0.06, "full_gut": 0.06, "addition": 0.05}},
    {"key": "garage_doors", "label": "Garage doors", "share": {"cosmetic": 0.0, "moderate": 0.01, "full_gut": 0.01, "addition": 0.01}},
    {"key": "landscaping", "label": "Landscaping", "share": {"cosmetic": 0.05, "moderate": 0.02, "full_gut": 0.02, "addition": 0.02}},
    {"key": "floors_stairs", "label": "Floors and stairs", "share": {"cosmetic": 0.0, "moderate": 0.01, "full_gut": 0.02, "addition": 0.02}},
    {"key": "framing_drywall", "label": "Framing & drywall", "share": {"cosmetic": 0.03, "moderate": 0.05, "full_gut": 0.10, "addition": 0.12}},
    {"key": "cabinets_countertops", "label": "Cabinets & countertops", "share": {"cosmetic": 0.04, "moderate": 0.06, "full_gut": 0.05, "addition": 0.04}},
    {"key": "doors_trim", "label": "Doors & trim", "share": {"cosmetic": 0.04, "moderate": 0.04, "full_gut": 0.04, "addition": 0.03}},
    {"key": "carpet_resilient", "label": "Carpet & resilient flooring", "share": {"cosmetic": 0.08, "moderate": 0.04, "full_gut": 0.02, "addition": 0.02}},
    {"key": "hardwood", "label": "Hardwood flooring", "share": {"cosmetic": 0.10, "moderate": 0.06, "full_gut": 0.05, "addition": 0.04}},
    {"key": "tiling", "label": "Tiling", "share": {"cosmetic": 0.04, "moderate": 0.05, "full_gut": 0.04, "addition": 0.03}},
    {"key": "painting", "label": "Painting", "share": {"cosmetic": 0.22, "moderate": 0.08, "full_gut": 0.05, "addition": 0.04}},
    {"key": "appliances", "label": "Appliances", "share": {"cosmetic": 0.06, "moderate": 0.04, "full_gut": 0.03, "addition": 0.02}},
    {"key": "plumbing", "label": "Plumbing", "share": {"cosmetic": 0.05, "moderate": 0.05, "full_gut": 0.07, "addition": 0.06}},
    {"key": "hvac", "label": "HVAC", "share": {"cosmetic": 0.02, "moderate": 0.04, "full_gut": 0.06, "addition": 0.05}},
    {"key": "electrical", "label": "Electrical", "share": {"cosmetic": 0.06, "moderate": 0.03, "full_gut": 0.06, "addition": 0.06}},
    {"key": "doors_locks", "label": "Door hardware and locks", "share": {"cosmetic": 0.02, "moderate": 0.01, "full_gut": 0.01, "addition": 0.01}},
    {"key": "other", "label": "Other", "share": {"cosmetic": 0.0, "moderate": 0.0, "full_gut": 0.01, "addition": 0.02}},
]

DEFAULT_ADDERS = {"location_multiplier": 1.0, "permit_pct": 2.0, "oh_and_p_pct": 0.0, "contingency_pct": 10.0}


def state_multiplier(state: Optional[str]) -> float:
    return STATE_COST_INDEX.get((state or "").upper(), 1.0)


def tier_estimate(scope: str, sqft: float, state: Optional[str], level: str = "mid") -> Dict[str, Any]:
    t = SCOPE_TIERS.get(scope) or SCOPE_TIERS["moderate"]
    rate = float(t[level if level in ("low", "mid", "high") else "mid"])
    mult = state_multiplier(state)
    subtotal = rate * mult * float(sqft or 0)
    return {"scope": scope, "label": t["label"], "rate_per_sqft": rate, "state_multiplier": mult, "sqft": sqft,
            "subtotal": round(subtotal), "range": {"low": round(t["low"] * mult * (sqft or 0)), "high": round(t["high"] * mult * (sqft or 0))},
            "covers": t["covers"]}


def spread_lines(scope: str, subtotal: float) -> List[Dict[str, Any]]:
    """A tier budget spread into the calculator's lines by typical share."""
    out = []
    for li in LINE_ITEMS:
        share = li["share"].get(scope, 0.0)
        out.append({"key": li["key"], "label": li["label"], "amount": round(subtotal * share), "estimated": True})
    # rounding residue goes to "other" so the lines sum to the subtotal
    diff = round(subtotal) - sum(x["amount"] for x in out)
    out[-1]["amount"] += diff
    return out


def budget(rehab: Dict[str, Any], *, sqft: float, state: Optional[str]) -> Dict[str, Any]:
    """Resolve a rehab input into a full budget.

    ``rehab`` carries ``mode`` ('tier' | 'lines' | 'lump'), ``scope``, ``level``,
    ``lines`` (list of {key, label, amount}), ``lump_sum`` and ``adders``.
    """
    mode = (rehab or {}).get("mode") or "tier"
    scope = (rehab or {}).get("scope") or "moderate"
    level = (rehab or {}).get("level") or "mid"
    adders = {**DEFAULT_ADDERS, **((rehab or {}).get("adders") or {})}
    est = tier_estimate(scope, sqft, state, level)
    if mode == "lump" and (rehab or {}).get("lump_sum") is not None:
        subtotal = float(rehab["lump_sum"] or 0)
        lines = spread_lines(scope, subtotal)
        basis = "Lump sum entered by the user, spread into lines by typical share."
    elif mode == "lines" and (rehab or {}).get("lines"):
        lines = [{"key": l.get("key") or ("line_%d" % i), "label": l.get("label") or l.get("key") or "Line", "amount": round(float(l.get("amount") or 0)), "estimated": False}
                 for i, l in enumerate(rehab["lines"])]
        subtotal = float(sum(l["amount"] for l in lines))
        basis = ("Line items from the contractor bid on file: %s." % rehab["source"]) if (rehab or {}).get("source") else "Line items entered by the user."
    else:
        subtotal = float(est["subtotal"])
        lines = spread_lines(scope, subtotal)
        basis = "%s at $%s/sqft x %.2f state multiplier x %s sqft (tier estimate; edit any line)." % (est["label"], est["rate_per_sqft"], est["state_multiplier"], int(sqft or 0))
    loc = float(adders.get("location_multiplier") or 1.0)
    located = subtotal * loc
    permit = located * float(adders.get("permit_pct") or 0) / 100
    ohp = located * float(adders.get("oh_and_p_pct") or 0) / 100
    contingency = located * float(adders.get("contingency_pct") or 0) / 100
    total = located + permit + ohp + contingency
    return {"mode": mode, "scope": scope, "level": level, "basis": basis, "tier": est, "lines": lines, "adders": adders,
            "source": (rehab or {}).get("source") if mode == "lines" else None, "source_doc_id": (rehab or {}).get("source_doc_id") if mode == "lines" else None,
            "subtotal": round(subtotal), "location_adjustment": round(located - subtotal), "permit_cost": round(permit),
            "oh_and_p": round(ohp), "contingency": round(contingency), "total_adders": round(permit + ohp + contingency + (located - subtotal)),
            "total": round(total), "per_sqft": round(total / sqft, 1) if sqft else None}
