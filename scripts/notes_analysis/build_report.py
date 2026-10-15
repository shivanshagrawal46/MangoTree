import json, csv, datetime as dt, html
A = json.load(open("analysis.json")); Z = A["zips"]; NOTES = A["notes"]
FH = json.load(open("fhfa_zip5.json"))
ZIPS = ["78064","78101","78245","78247","78216","78233","78225","78212","78133","78266","78132","78230","78201","78109","78250","77061","75189"]
def rd(path):
    rows = list(csv.reader(open(path)))[1:]
    return [(r[0], float(r[1])) for r in rows if len(r) > 1 and r[1] not in ("", ".")]
MORT = rd("fred_MORTGAGE30US.csv"); TXHPI = rd("fred_TXSTHPI.csv"); SAHPI = rd("fred_SAHPI.csv"); FF = rd("fred_FEDFUNDS.csv"); CPI = dict(rd("fred_CPI.csv"))
FAIR_MULT = CPI["2026-08-01"] / CPI["2019-08-01"]   # 1.305: prices that only kept pace with inflation/incomes since Aug 2019
METRO_OF = {"77061": "Houston", "75189": "Dallas"}
SUPPLY_PEN = {"78245": 1.0, "78109": 1.0, "78233": 0.7, "78064": 0.5, "75189": 1.5, "78133": 0.5, "78132": 0.5, "78250": 0.3}
ANCHOR = {"San Antonio": 3.0, "Houston": 3.0, "Dallas": 2.5}
def clamp(x, lo, hi): return max(lo, min(hi, x))
def project(now, v2019, zhvf12, metro, pen):
    fair = v2019 * FAIR_MULT; over = now / fair - 1
    rev = clamp(-0.3 * over * 100, -2.0, 1.0)   # a ZIP above its income-based fair value loses up to 2 pts a year; one below it gains at most 1, since cheap areas stay cheap
    a = ANCHOR[metro]
    paths = {
        "base": [zhvf12 - 1.5, a - 1.0 + rev - pen, a + rev - pen * 0.5, a + 0.5 + rev, a + 1.0 + rev],
        "bear": [zhvf12 - 4.0, -3.0 + rev - pen, 0.0 + rev - pen * 0.5, 2.0 + rev, 3.0 + rev],
        "bull": [zhvf12 + 0.5, 4.0 + rev - pen * 0.5, 5.0 + rev, 4.5 + rev, 4.0 + rev],
    }
    out = {}
    for k, p in paths.items():
        v = now; vals = []
        for g in p: v *= 1 + g / 100; vals.append(v)
        out[k] = {"path": p, "values": vals, "cum": vals[-1] / now - 1}
    out["expected"] = 0.5 * out["base"]["cum"] + 0.3 * out["bear"]["cum"] + 0.2 * out["bull"]["cum"]
    out["fair"] = fair; out["over"] = over
    return out
for z in ZIPS:
    e = Z[z]; metro = METRO_OF.get(z, "San Antonio")
    e["metro_key"] = metro
    e["proj"] = project(e["by_year"]["2026"], e["by_year"]["2019"], e["zhvf"]["2027-08-31"], metro, SUPPLY_PEN.get(z, 0))
    e["fhfa"] = {str(r["Year"]): float(r["Annual Change (%)"]) for r in FH.get(z, []) if r["Annual Change (%)"] not in (None, ".") and int(r["Year"]) >= 2016}
for key, metro, fc in (("metro_zhvi:San Antonio, TX", "San Antonio", -0.7), ("metro_zhvi:Houston, TX", "Houston", -0.2), ("metro_zhvi:Dallas, TX", "Dallas", -0.3), ("state_zhvi:Texas", "San Antonio", -0.4)):
    e = Z[key]; e["proj"] = project(e["by_year"]["2026"], e["by_year"]["2019"], fc, metro, 0.3)
# notes: status, stressed LTVs
for n in NOTES:
    nd = dt.datetime.strptime(n["Next Due"], "%m/%d/%y")
    REPORT = dt.datetime(2026, 8, 31); behind = 0; d = nd
    while d <= REPORT:
        behind += 1; m = d.month + 1; y = d.year + (m > 12); m = 1 if m > 12 else m
        d = d.replace(year=y, month=m, day=min(d.day, 28))
    n["behind"] = behind
    n["status"] = "current" if behind == 0 else ("%d payment%s behind" % (behind, "s" if behind > 1 else ""))
    p = Z[n["zip"]]["proj"]
    n["ltv_2031_base"] = n["Balance"] / (n["EST. VALUE"] * (1 + p["base"]["cum"])) if n["EST. VALUE"] else None
    n["ltv_2031_bear"] = n["Balance"] / (n["EST. VALUE"] * (1 + p["bear"]["cum"])) if n["EST. VALUE"] else None
    n["ltv_stress10"] = n["Balance"] / (n["EST. VALUE"] * 0.9) if n["EST. VALUE"] else None
    n["refi_gap"] = n["Interest Rate"] - 7.40
tb = sum(n["Balance"] for n in NOTES); tv = sum(n["EST. VALUE"] for n in NOTES)
S = {"n": len(NOTES), "balance": tb, "value": tv, "ltv": tb / tv, "war": sum(n["Balance"] * n["Interest Rate"] for n in NOTES) / tb, "pay": sum(n["Payment"] for n in NOTES),
     "late": [n for n in NOTES if n["behind"] > 0], "balloons": [n for n in NOTES if int(n["Due in Full"].split("/")[-1]) <= 2030],
     "high_ltv": [n for n in NOTES if n["cur_ltv"] and n["cur_ltv"] >= 0.78]}
S["late_bal"] = sum(n["Balance"] for n in S["late"])
# portfolio-weighted ZIP outlook
w = {}
for n in NOTES: w[n["zip"]] = w.get(n["zip"], 0) + n["Balance"]
S["wexp"] = {k: sum(w[z] * Z[z]["proj"][k]["cum"] for z in w) / tb for k in ("base", "bear", "bull")}
S["w5y"] = sum(w[z] * Z[z]["chg5"] for z in w) / tb
S["wyoy"] = sum(w[z] * Z[z]["yoy"] for z in w) / tb
json.dump({"zips": Z, "notes": NOTES, "summary": {k: v for k, v in S.items() if k not in ("late", "balloons", "high_ltv")}}, open("report_data.json", "w"), default=str)
print("FAIR_MULT %.3f" % FAIR_MULT)
print("portfolio: 5y %.1f%% yoy %.1f%% | 5y ahead base %.1f%% bear %.1f%% bull %.1f%%" % (S["w5y"]*100, S["wyoy"]*100, S["wexp"]["base"]*100, S["wexp"]["bear"]*100, S["wexp"]["bull"]*100))
for z in ZIPS:
    p = Z[z]["proj"]; print(z, Z[z]["city"].ljust(14), "fair %6.0f over %+5.1f%% | base %+5.1f%% bear %+5.1f%% bull %+5.1f%% exp %+5.1f%% | path %s" % (p["fair"], p["over"]*100, p["base"]["cum"]*100, p["bear"]["cum"]*100, p["bull"]["cum"]*100, p["expected"]*100, [round(x,1) for x in p["base"]["path"]]))
for k in ("metro_zhvi:San Antonio, TX", "metro_zhvi:Houston, TX", "metro_zhvi:Dallas, TX", "state_zhvi:Texas"):
    p = Z[k]["proj"]; print(k, "over %+5.1f%% base %+5.1f%% bear %+5.1f%% bull %+5.1f%%" % (p["over"]*100, p["base"]["cum"]*100, p["bear"]["cum"]*100, p["bull"]["cum"]*100))
print("late", [(n["Property Address"][:20], n["status"], round(n["Balance"])) for n in S["late"]], round(S["late_bal"]))
print("balloons", [(n["Property Address"][:20], n["Due in Full"], round(n["Balance"])) for n in S["balloons"]])
print("high ltv", [(n["Property Address"][:20], round(n["cur_ltv"]*100)) for n in S["high_ltv"]])
print("mort last", MORT[-1], "ff last", FF[-1], "txhpi", TXHPI[-1], TXHPI[-5], "sa", SAHPI[-1], SAHPI[-5])
