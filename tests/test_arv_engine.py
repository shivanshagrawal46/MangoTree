"""The ARV engine's arithmetic must never silently change: comp scoring and
selection, the adjustment lines, reconciliation, confidence, the MAO math and
the rehab budget — all deterministic, all exercised here without any network
or model call."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from mangotree.arv import engine, rehab
from mangotree.arv.providers import dedupe, new_comp


def _d(months_ago: float) -> str:
    return (datetime.now() - timedelta(days=months_ago * 30.4375)).date().isoformat()


SUBJECT = {"address": "513 Allison St NW, Washington, DC 20011", "lat": 38.9440, "lon": -77.0210, "property_type": "single_family",
           "beds": 4, "baths": 3.0, "sqft": 2000, "lot_sqft": 3000, "year_built": 1925, "garage_spaces": 0, "basement": "finished", "pool": None,
           "condition_after": "C2"}


def _comps():
    base = dict(source="manual", property_type="single_family", lat=38.945, lon=-77.022, garage_spaces=0, basement="finished", pool=None)
    return [
        new_comp(address="A", sale_price=900000, sale_date=_d(2), beds=4, baths=3.5, sqft=2100, lot_sqft=3100, year_built=1925, condition="C2", **base),
        new_comp(address="B", sale_price=820000, sale_date=_d(5), beds=4, baths=3.0, sqft=1900, lot_sqft=2900, year_built=1930, condition="C3", **base),
        new_comp(address="C", sale_price=760000, sale_date=_d(8), beds=3, baths=2.0, sqft=1750, lot_sqft=3000, year_built=1920, condition="C4", **base),
        new_comp(address="D", sale_price=950000, sale_date=_d(3), beds=5, baths=3.5, sqft=2300, lot_sqft=3200, year_built=1928, condition="C2", **base),
        new_comp(address="E (too far)", sale_price=700000, sale_date=_d(1), beds=4, baths=3.0, sqft=2000, lot_sqft=3000, year_built=1925, condition="C3",
                 **{**base, "lat": 38.99, "lon": -77.10}),
        new_comp(address="F (stale)", sale_price=600000, sale_date=_d(30), beds=4, baths=3.0, sqft=2000, lot_sqft=3000, year_built=1925, condition="C3", **base),
        new_comp(address="G (condo)", sale_price=500000, sale_date=_d(2), beds=2, baths=2.0, sqft=1100, year_built=2005, **{**base, "property_type": "condo"}),
    ]


MARKET = {"zhvi": {"zip": "20011", "change_12m_pct": 3.6, "monthly_rate_pct": 0.295}}


def test_selection_filters_and_scores():
    comps = _comps()
    out = engine.value(SUBJECT, comps, MARKET, None, {"radius_mi": 1.0, "months": 12, "target": 6})
    sel = [c["address"] for c in comps if c["selected"]]
    assert set(sel) == {"A", "B", "C", "D"}
    far = next(c for c in comps if c["address"].startswith("E"))
    assert any("beyond" in f for f in far["fails"])
    stale = next(c for c in comps if c["address"].startswith("F"))
    assert any("older" in f for f in stale["fails"])
    condo = next(c for c in comps if c["address"].startswith("G"))
    assert any("property type" in f for f in condo["fails"])
    assert out["arv"] and out["arv_low"] <= out["arv"] <= out["arv_high"]
    assert out["confidence"]["grade"] in "ABCDE"


def test_adjustments_move_toward_subject():
    comps = _comps()
    engine.value(SUBJECT, comps, MARKET, None, {})
    c = next(c for c in comps if c["address"] == "C")   # 3/2, 1750 sqft, C4, 8 months old
    lines = {l["key"]: l["amount"] for l in c["adjustment"]["lines"]}
    assert lines["gla"] > 0            # subject is bigger
    assert lines["beds"] > 0 and lines["baths"] > 0
    assert lines["condition"] > 0      # subject as repaired C2 vs comp C4
    assert lines["time"] > 0           # rising market
    assert c["adjustment"]["adjusted_price"] == 760000 + c["adjustment"]["net"]
    d = next(c for c in comps if c["address"] == "D")   # bigger, 5 bed: negative GLA and bed lines
    dl = {l["key"]: l["amount"] for l in d["adjustment"]["lines"]}
    assert dl["gla"] < 0 and dl["beds"] < 0


def test_excluded_and_forced_comps():
    comps = _comps()
    comps[0]["included"] = False           # A excluded by the user
    comps[5]["included"] = True            # F (stale) forced in
    engine.value(SUBJECT, comps, MARKET, None, {})
    assert not comps[0]["selected"]
    assert comps[5]["selected"] and comps[5].get("forced")


def test_widens_when_too_few():
    comps = _comps()[4:6]   # only the far one and the stale one
    out = engine.value(SUBJECT, comps, MARKET, None, {"radius_mi": 1.0, "months": 12})
    assert out["selection"]["notes"], "expected a widening note"


def test_avm_blend_and_divergence():
    comps = _comps()
    close = engine.value(SUBJECT, comps, MARKET, {"source": "Test AVM", "value": 880000}, {})
    assert "blended" in (close["reconciliation"]["avm_note"] or "")
    comps = _comps()
    far = engine.value(SUBJECT, comps, MARKET, {"source": "Test AVM", "value": 500000}, {})
    assert "not blended" in (far["reconciliation"]["avm_note"] or "")


def test_underwrite_matches_calculator_logic():
    # MAO Rehab Calculator example: ARV 800,000, repairs 204,000 (200,000 + 2% permits), 70% rule -> 356,000
    a = {"purchase_price": 400000, "months_held": 8, "rule_pct": 70, "finance": True, "ltc_pct": 100, "ltarv_pct": 90, "rate_pct": 7.5, "points_pct": 0, "loan_fees": 0,
         "property_taxes_annual": 12480, "insurance_annual": 3600, "utilities_monthly": 50, "maintenance_monthly": 50, "commission_pct": 6.0, "transfer_tax_pct": 0,
         "sell_title_insurance": 150, "second_appraisal": 900, "home_warranty": 0, "sell_brokerage": 0, "buy_title_search": 500, "buy_title_insurance": 2000,
         "buy_brokerage": 0, "buy_inspection": 0, "buy_appraisal": 0}
    uw = engine.underwrite(800000, 204000, a, state="DC", sqft=2000)
    assert uw["mao"]["rule"] == 356000
    assert uw["buying"]["total"] == 2500
    assert uw["holding"]["total"] == 11520          # (12480/12 + 300 + 50 + 50) * 8
    assert uw["selling"]["total"] == 48000 + 150 + 900
    d = uw["deal"]
    assert d["purchase_price"] == 400000
    assert d["total_project_cost"] == 400000 + 204000 + d["fixed_costs"]
    assert d["profit"] == 800000 - d["total_project_cost"]
    assert uw["mao"]["profit_target"] > 0
    assert len(uw["sensitivity"]) == 5 and len(uw["sensitivity"][0]["cells"]) == 5
    assert any(c["key"] == "ltarv" for c in uw["checks"])


def test_mao_by_profit_target_leaves_target_profit():
    a = {"months_held": 6, "profit_target_pct": 15, "profit_target_min": 0, "finance": False, "property_taxes_annual": 6000, "transfer_tax_pct": 1.0}
    uw = engine.underwrite(600000, 80000, a, state="DC")
    at_target = engine.underwrite(600000, 80000, {**a, "purchase_price": uw["mao"]["profit_target"]}, state="DC")
    assert abs(at_target["deal"]["profit"] - 90000) < 200


def test_rehab_budget_modes():
    tier = rehab.budget({"mode": "tier", "scope": "moderate", "level": "mid"}, sqft=2000, state="DC")
    assert tier["subtotal"] == round(55 * 1.14 * 2000)
    assert sum(l["amount"] for l in tier["lines"]) == tier["subtotal"]
    assert tier["total"] == round(tier["subtotal"] * (1 + 0.02 + 0.10))
    lump = rehab.budget({"mode": "lump", "lump_sum": 200000, "scope": "full_gut", "adders": {"permit_pct": 2, "contingency_pct": 0}}, sqft=2000, state="DC")
    assert lump["subtotal"] == 200000 and lump["total"] == 204000
    lines = rehab.budget({"mode": "lines", "lines": [{"key": "kitchen", "label": "Kitchen", "amount": 30000}, {"key": "roofing", "label": "Roofing", "amount": 12000}],
                          "adders": {"permit_pct": 0, "contingency_pct": 0}}, sqft=1500, state="FL")
    assert lines["subtotal"] == 42000 and lines["total"] == 42000


def test_dedupe_prefers_closed_over_listed():
    subj = {"lat": 38.9, "lon": -77.0}
    a = new_comp(source="rentcast", address="100 Main St, Washington, DC", sale_price=500000, price_basis="listed", sqft=1500)
    b = new_comp(source="web", address="100 Main St, Washington, DC", sale_price=490000, price_basis="closed", sale_date="2026-05-01")
    out = dedupe([a, b], subj)
    assert len(out) == 1 and out[0]["price_basis"] == "closed" and out[0]["sale_price"] == 490000
    assert out[0]["sqft"] == 1500   # merged from the other record


def test_size_filter_relaxes_instead_of_returning_nothing():
    """A composite living area (several buildings) must not empty the grid: the
    size limit relaxes to 60% and then drops, with a note, before giving up."""
    big = dict(SUBJECT, sqft=8900)
    comps = _comps()
    out = engine.select(big, comps, radius=1.0, months=12, target=6)
    assert out["qualified"] >= 3
    assert any("size filter" in n for n in out["notes"])
    # the size difference still costs fit points
    assert all(c["similarity"] < 80 for c in out["selected"])
