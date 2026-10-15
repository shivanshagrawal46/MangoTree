import json, csv, html, re
D = json.load(open("report_data.json")); Z = D["zips"]; N = D["notes"]; S = D["summary"]
ZIPS = ["78064","78101","78245","78247","78216","78233","78225","78212","78133","78266","78132","78230","78201","78109","78250","77061","75189"]
AREA = {
 "78064": ("Pleasanton", "Atascosa County, 35 miles south of San Antonio. Small oil-and-ranch town; thin market with few sales a month."),
 "78101": ("Adkins", "Rural east Bexar County; acreage homesteads. One of two portfolio ZIPs still rising on the year."),
 "78245": ("Far West SA", "Alamo Ranch and Potranco corridor; the metro's biggest new-build belt. Three notes sit here."),
 "78247": ("North Central SA", "1980s to 1990s subdivisions between US 281 and Loop 1604; established, mid-price."),
 "78216": ("Airport / North Central", "Older 1960s and 1970s housing near the airport; peaked late, in May 2024."),
 "78233": ("Northeast SA", "Live Oak and Windcrest edge; entry-level stock that competes with new builds on I-35."),
 "78225": ("Southwest inner city", "Lowest price point in the portfolio; Zillow shows the steepest fall from peak. FHFA publishes no index here (too few sales)."),
 "78212": ("Monte Vista / Tobin Hill", "Historic core north of downtown. The ZIP index tracks mid-price homes; the two Howard St collateral houses sit far above it."),
 "78133": ("Canyon Lake", "Comal County lake and second-home market; the most rate-sensitive buyer pool in the set."),
 "78266": ("Garden Ridge / Universal City", "Comal County, higher-end acreage; peaked June 2024 and has given back little."),
 "78132": ("New Braunfels west", "Hill Country side of one of the fastest-growing counties in the country; large pandemic run-up."),
 "78230": ("Northwest SA", "Medical Center and Shavano area; established and steady. Two notes here."),
 "78201": ("Near West / Beacon Hill", "Older 1920s to 1950s housing and flip activity. Zillow and FHFA disagree sharply here; thin data."),
 "78109": ("Converse", "Northeast builder belt off I-10 and Loop 1604; highest rent yield in the portfolio."),
 "78250": ("Great Northwest SA", "1980s to 2000s subdivisions off Bandera Road and Loop 1604; mid-price."),
 "77061": ("Houston, Hobby area", "Southeast Houston near Hobby Airport; older stock, peaked June 2024. One 12% investor note."),
 "75189": ("Royse City", "Far-east Dallas exurb across Rockwall and Hunt counties; builder-dominated, 7 months of supply in August."),
}
def money(x, k=False):
    if x is None: return "—"
    return "$%.0fK" % (x/1000) if k else "${:,.0f}".format(x)
def pct(x, s=True, d=1):
    if x is None: return "—"
    return ("%+." + str(d) + "f%%") % (x*100) if s else ("%." + str(d) + "f%%") % (x*100)
def cls(x): return "pos" if x is not None and x > 0.0005 else ("neg" if x is not None and x < -0.0005 else "")
def tdp(x, d=1, s=True): return '<td class="num %s">%s</td>' % (cls(x), pct(x, s, d))
notes_in = {}
for n in N: notes_in.setdefault(n["zip"], []).append(n)
YEARS = (2016, 2019, 2021, 2022, 2024, 2025, 2026)
rows = []
for z in ZIPS:
    e = Z[z]; by = e["by_year"]
    rows.append("<tr><th>%s<small>%s</small></th>" % (z, AREA[z][0]) + "".join('<td class="num">%s</td>' % money(by[str(y)], True) for y in YEARS) +
                tdp(e["chg5"]) + tdp(e["yoy"]) + '<td class="num">%s</td>' % e["peak_date"][:7] + tdp(e["drawdown"]) + "</tr>")
for key, label in (("metro_zhvi:San Antonio, TX", "San Antonio metro"), ("metro_zhvi:Houston, TX", "Houston metro"), ("metro_zhvi:Dallas, TX", "Dallas–Fort Worth"), ("state_zhvi:Texas", "Texas"), ("metro_zhvi:United States", "United States")):
    e = Z[key]; by = e["by_year"]
    rows.append('<tr class="agg"><th>%s</th>' % label + "".join('<td class="num">%s</td>' % money(by[str(y)], True) for y in YEARS) +
                tdp(by["2026"]/by["2021"]-1) + tdp(by["2026"]/by["2025"]-1) + '<td class="num">%s</td>' % e["peak_date"][:7] + tdp(e["drawdown"]) + "</tr>")
T_HIST = "".join(rows)
rows = []
for z in ZIPS:
    e = Z[z]; p = e["proj"]; ns = notes_in.get(z, []); fh = e["fhfa"].get("2025")
    rows.append("<tr><th>%s<small>%s</small></th>" % (z, AREA[z][0]) + '<td class="num">%s</td>' % money(e["by_year"]["2026"]) + tdp(e["yoy"]) + tdp(e["drawdown"]) +
                '<td class="num %s">%s</td>' % (cls(fh), ("%+.1f%%" % fh) if fh is not None else "n/a") + tdp(e["zhvf"]["2027-08-31"]/100) + tdp(p["over"]) +
                '<td class="num">%s</td>' % (("%.1f%%" % (e["gross_yield"]*100)) if e.get("gross_yield") else "—") +
                '<td class="num">%d · %s</td>' % (len(ns), money(sum(n["Balance"] for n in ns), True)) + "</tr>")
T_NOW = "".join(rows)
rows = []
for z in ZIPS:
    e = Z[z]; p = e["proj"]; now = e["by_year"]["2026"]
    rows.append("<tr><th>%s<small>%s</small></th>" % (z, AREA[z][0]) + '<td class="num">%s</td>' % money(now, True) +
                tdp(p["base"]["cum"]) + '<td class="num">%s</td>' % money(p["base"]["values"][-1], True) + tdp(p["bear"]["cum"]) + tdp(p["bull"]["cum"]) + tdp(p["expected"]) +
                '<td class="num path">%s</td>' % " · ".join("%+.1f" % x for x in p["base"]["path"]) + "</tr>")
T_OUT = "".join(rows)
rows = []
for n in sorted(N, key=lambda n: -n["Balance"]):
    e = Z[n["zip"]]; flags = []
    if n["cur_ltv"] and n["cur_ltv"] > 1.0: flags.append("balance above value")
    elif n["cur_ltv"] and n["cur_ltv"] >= 0.78: flags.append("thin equity")
    if int(n["Due in Full"].split("/")[-1]) <= 2030: flags.append("balloon " + n["Due in Full"])
    if n["Property Address"].startswith("315 Howard"): flags.append("estimated value looks wrong (sale price $750,000 against a $510,000 balance)")
    st = '<span class="pill %s">%s</span>' % ("bad" if n["behind"] >= 2 else "warn" if n["behind"] == 1 else "ok", n["status"])
    rows.append("<tr><th>%s<small>%s · %s</small></th>" % (html.escape(n["Property Address"].split(",")[0]), n["zip"], AREA[n["zip"]][0]) +
                '<td class="num">%.1f%%</td>' % n["Interest Rate"] + '<td class="num">%s</td>' % money(n["Balance"]) + '<td class="num">%s</td>' % money(n["EST. VALUE"]) +
                '<td class="num %s">%s</td>' % ("neg" if n["cur_ltv"] > 0.9 else "", pct(n["cur_ltv"], False, 0)) + tdp(e["yoy"]) + tdp(n["zip_chg_since_orig"]) +
                '<td class="num">%s / %s</td>' % (pct(n["ltv_2031_base"], False, 0), pct(n["ltv_2031_bear"], False, 0)) + "<td>%s</td>" % st + '<td class="flags">%s</td>' % "; ".join(flags) + "</tr>")
T_NOTES = "".join(rows)
def rebase(series, start="2016-08-31", base="2021-08-31"):
    b = series[base]; return [[k, round(v / b * 100, 2)] for k, v in sorted(series.items()) if k >= start]
def fred(path, start):
    return [[r[0], float(r[1])] for r in list(csv.reader(open(path)))[1:] if r[0] >= start and r[1] not in ("", ".")]
CH = {
 "metro": {lbl: rebase(Z[k]["series_monthly"]) for k, lbl in (("metro_zhvi:San Antonio, TX", "San Antonio"), ("metro_zhvi:Houston, TX", "Houston"), ("metro_zhvi:Dallas, TX", "Dallas–Fort Worth"), ("state_zhvi:Texas", "Texas"), ("metro_zhvi:United States", "United States"))},
 "zips": {z: {"name": AREA[z][0], "series": [[k, v] for k, v in sorted(Z[z]["series_monthly"].items()) if k >= "2016-01-01"], "fair": Z[z]["proj"]["fair"], "proj": {s: Z[z]["proj"][s]["values"] for s in ("base", "bear", "bull")}} for z in ZIPS},
 "sa_proj": {s: Z["metro_zhvi:San Antonio, TX"]["proj"][s]["values"] for s in ("base", "bear", "bull")}, "sa_now": Z["metro_zhvi:San Antonio, TX"]["by_year"]["2026"],
 "sa_hist": [[k, v] for k, v in sorted(Z["metro_zhvi:San Antonio, TX"]["series_monthly"].items()) if k >= "2016-01-01"],
 "tx_proj": {s: Z["state_zhvi:Texas"]["proj"][s]["values"] for s in ("base", "bear", "bull")}, "tx_now": Z["state_zhvi:Texas"]["by_year"]["2026"],
 "tx_hist": [[k, v] for k, v in sorted(Z["state_zhvi:Texas"]["series_monthly"].items()) if k >= "2016-01-01"],
 "mort": fred("fred_MORTGAGE30US.csv", "2016-01-01"), "ff": fred("fred_FEDFUNDS.csv", "2016-01-01"),
 "txhpi": fred("fred_TXSTHPI.csv", "2015-01-01"), "sahpi": fred("fred_SAHPI.csv", "2015-01-01"),
 "inv": {lbl: [[k, v] for k, v in sorted(Z["metro_inventory:" + k2]["series_monthly"].items())] for k2, lbl in (("San Antonio, TX", "San Antonio"), ("Houston, TX", "Houston"), ("Dallas, TX", "Dallas–Fort Worth"))},
}
sa = Z["metro_zhvi:San Antonio, TX"]; tx = Z["state_zhvi:Texas"]; us = Z["metro_zhvi:United States"]
inv_sa = Z["metro_inventory:San Antonio, TX"]; doz_sa = Z["metro_doz:San Antonio, TX"]; pc_sa = Z["metro_pricecut:San Antonio, TX"]
late = [n for n in N if n["behind"] > 0]; late_bal = sum(n["Balance"] for n in late)
def pmt(P, r, n=360): m = r/12; return P*m/(1-(1+m)**-n)
p19 = pmt(sa["by_year"]["2019"]*0.8, 0.0357); p26 = pmt(sa["by_year"]["2026"]*0.8, 0.074); p26_6 = pmt(sa["by_year"]["2026"]*0.8, 0.06)
CPI_5 = 334.131/272.676
V = {
 "N": str(S["n"]), "BAL": money(S["balance"]), "WAR": "%.2f%%" % S["war"], "LTV": pct(S["ltv"], False, 0), "PAY": money(S["pay"]), "VAL": money(S["value"]),
 "LATE_N": str(len(late)), "LATE_BAL": money(late_bal), "W5Y": pct(S["w5y"]), "WYOY": pct(S["wyoy"]), "BASE": pct(S["wexp"]["base"]), "BEAR": pct(S["wexp"]["bear"]), "BULL": pct(S["wexp"]["bull"]),
 "SA_NOW": money(sa["by_year"]["2026"]), "SA_FAIR": money(sa["proj"]["fair"]), "SA_5Y": pct(sa["by_year"]["2026"]/sa["by_year"]["2021"]-1), "SA_YOY": pct(sa["by_year"]["2026"]/sa["by_year"]["2025"]-1), "SA_DD": pct(sa["drawdown"]), "SA_PEAK": money(sa["peak"]),
 "SA_REAL5": pct(sa["by_year"]["2026"]/sa["by_year"]["2021"]/CPI_5-1), "TX_REAL5": pct(tx["by_year"]["2026"]/tx["by_year"]["2021"]/CPI_5-1),
 "TX_NOW": money(tx["by_year"]["2026"]), "TX_5Y": pct(tx["by_year"]["2026"]/tx["by_year"]["2021"]-1), "TX_YOY": pct(tx["by_year"]["2026"]/tx["by_year"]["2025"]-1), "TX_DD": pct(tx["drawdown"]), "TX_10Y": pct(tx["by_year"]["2026"]/tx["by_year"]["2016"]-1),
 "US_5Y": pct(us["by_year"]["2026"]/us["by_year"]["2021"]-1), "US_YOY": pct(us["by_year"]["2026"]/us["by_year"]["2025"]-1),
 "SA_OVER": pct(sa["proj"]["over"]), "TX_OVER": pct(tx["proj"]["over"]), "SA_BASE": pct(sa["proj"]["base"]["cum"]), "SA_BEAR": pct(sa["proj"]["bear"]["cum"]), "SA_BULL": pct(sa["proj"]["bull"]["cum"]),
 "TX_BASE": pct(tx["proj"]["base"]["cum"]), "TX_BEAR": pct(tx["proj"]["bear"]["cum"]), "TX_BULL": pct(tx["proj"]["bull"]["cum"]),
 "HOU_BASE": pct(Z["metro_zhvi:Houston, TX"]["proj"]["base"]["cum"]), "DFW_BASE": pct(Z["metro_zhvi:Dallas, TX"]["proj"]["base"]["cum"]),
 "INV_SA": "{:,.0f}".format(inv_sa["now"]), "INV_SA19": "{:,.0f}".format(inv_sa["2019"]), "INV_SA_CHG": pct(inv_sa["now"]/inv_sa["2019"]-1, True, 0), "DOZ_SA": "%d" % doz_sa["now"], "DOZ_SA19": "%d" % doz_sa["2019"], "PC_SA": pct(pc_sa["now"], False, 0),
 "P19": money(p19), "P26": money(p26), "P26_6": money(p26_6), "SA19": money(sa["by_year"]["2019"]),
 "CHART_DATA": json.dumps(CH, separators=(",", ":")), "ZIPDESC": json.dumps({z: AREA[z][1] for z in ZIPS}),
 "T_HIST": T_HIST, "T_NOW": T_NOW, "T_OUT": T_OUT, "T_NOTES": T_NOTES,
}
tpl = open("template.html", encoding="utf-8").read()
for k, v in V.items(): tpl = tpl.replace("{{" + k + "}}", v)
left = re.findall(r"\{\{[A-Z0-9_]+\}\}", tpl); assert not left, left
open("hk_notes_outlook.html", "w", encoding="utf-8").write(tpl)
print("ok", len(tpl)//1024, "KB", "late", len(late), money(late_bal), "p19", money(p19), "p26", money(p26), "p26_6", money(p26_6))
