import json, math, datetime as dt
import openpyxl
d = json.load(open("extract.json"))
ZIPS = ["78064","78101","78245","78247","78216","78233","78225","78212","78133","78266","78132","78230","78201","78109","78250","77061","75189"]
def at(s, dd):
    ks = sorted(k for k in s if k <= dd); return s[ks[-1]] if ks else None
def cagr(a,b,yrs): return (b/a)**(1/yrs)-1
out = {}
print("ZIP | Aug of each year 2016..2026 | peak | drawdown | 2016-19 CAGR | trend-extrap 2026 | above trend % | price/rent | rent 5y%")
for z in ZIPS:
    e = d["zip_zhvi"][z]; s = e["series"]
    yrs = {y: at(s, "%d-08-31" % y) for y in range(2016, 2027)}
    pk = max(s.items(), key=lambda kv: kv[1]); now = yrs[2026]
    c1619 = cagr(yrs[2016], yrs[2019], 3)
    # pre-pandemic trend: 2014-2019 CAGR extrapolated from 2019 to Aug 2026 (7 yrs)
    v2014 = at(s, "2014-08-31"); c1419 = cagr(v2014, yrs[2019], 5)
    trend = yrs[2019] * (1 + c1419) ** 7
    zori = d["zip_zori"].get(z); rent = rent5 = None
    if zori:
        rs = zori["series"]; rent = rs[max(rs)]; r21 = at(rs, "2021-08-31"); rent5 = (rent / r21 - 1) if r21 else None
    fc = d["zip_zhvf"][z]
    out[z] = {"city": e["meta"]["City"], "county": e["meta"]["CountyName"], "metro": e["meta"]["Metro"], "by_year": yrs, "peak_date": pk[0], "peak": pk[1],
              "drawdown": now/pk[1]-1, "yoy": now/yrs[2025]-1, "chg5": now/yrs[2021]-1, "chg10": now/yrs[2016]-1, "cagr1419": c1419, "trend2026": trend, "above_trend": now/trend-1,
              "rent": rent, "rent_chg5": rent5, "gross_yield": (rent*12/now) if rent else None, "zhvf": {k: float(v) for k, v in fc.items() if k[:2]=="20"},
              "series_monthly": {k: v for k, v in s.items() if k >= "2015-01-01"}}
    print(z, e["meta"]["City"], [round(yrs[y]/1000) for y in range(2016,2027)], pk[0][:7], "%.1f%%" % (out[z]["drawdown"]*100), "%.1f%%" % (c1419*100), round(trend/1000), "%.1f%%" % (out[z]["above_trend"]*100), rent and round(now/(rent*12),1), rent5 and "%.0f%%" % (rent5*100))
# metros / state
for key, names in (("metro_zhvi", ["San Antonio, TX","Houston, TX","Dallas, TX","United States"]), ("state_zhvi", ["Texas","Florida","Arizona","California","North Carolina","Tennessee","Georgia"])):
    for n in names:
        s = d[key][n]["series"]; yrs = {y: at(s, "%d-08-31" % y) for y in range(2014, 2027)}; pk = max(s.items(), key=lambda kv: kv[1])
        c1419 = cagr(yrs[2014], yrs[2019], 5); trend = yrs[2019]*(1+c1419)**7
        out[key+":"+n] = {"by_year": yrs, "peak_date": pk[0], "peak": pk[1], "drawdown": yrs[2026]/pk[1]-1, "cagr1419": c1419, "trend2026": trend, "above_trend": yrs[2026]/trend-1,
                          "series_monthly": {k: v for k, v in s.items() if k >= "2015-01-01"}}
        print(key, n, [round(yrs[y]/1000) for y in range(2016,2027)], "peak", pk[0][:7], "dd %.1f%%" % ((yrs[2026]/pk[1]-1)*100), "trend %.1f%%/yr above %.1f%%" % (c1419*100, (yrs[2026]/trend-1)*100))
for n in ["San Antonio, TX","Houston, TX","Dallas, TX","United States"]:
    for k in ("metro_inventory","metro_doz","metro_pricecut"):
        s = d[k][n]["series"]; last = max(s)
        out[k+":"+n] = {"last": last, "now": s[last], "yr_ago": at(s, last[:4].replace(last[:4], str(int(last[:4])-1))+last[4:]), "2019": at(s, "2019-08-31"), "2022": at(s, "2022-08-31"), "series_monthly": s}
        print(k, n, last, s[last], "yr ago", out[k+":"+n]["yr_ago"], "2019", out[k+":"+n]["2019"], "2022", out[k+":"+n]["2022"])
# portfolio
wb = openpyxl.load_workbook(r"C:\Users\SHIVANSH AGRAWAL\Desktop\MangoTree\Note_Portfolio_Report_HK HOMES.xlsx", data_only=True)
ws = wb.worksheets[0]; hdr = next([c for c in r if c] for r in ws.iter_rows(values_only=True) if r and r[0] == "Property Address")
notes = []
for r in ws.iter_rows(min_row=1, values_only=True):
    if not r[0] or not isinstance(r[2], (int, float)): continue
    n = dict(zip([h.strip() for h in hdr], r[:len(hdr)])); addr = n["Property Address"]; z = addr.strip()[-5:]
    n["zip"] = z; n["city"] = addr.split(",")[-1].strip().rsplit(" ", 2)[0]
    n["cur_ltv"] = n["Balance"] / n["EST. VALUE"] if n["EST. VALUE"] else None
    pt = dt.datetime.strptime(n["Interest Paid-To"], "%m/%d/%y"); nd = dt.datetime.strptime(n["Next Due"], "%m/%d/%y")
    n["months_behind"] = max(0, round((dt.datetime(2026, 9, 30) - nd).days / 30.4))  # report appears to be as of late Sept 2026
    n["due_full"] = n["Due in Full"]; n["orig"] = n["Date Originated"].date().isoformat(); n["ltv_at_orig"] = n["LTV"]
    z_e = out.get(z); n["zip_now"] = z_e and z_e["by_year"][2026]; n["zip_at_orig"] = z_e and at(d["zip_zhvi"][z]["series"], n["orig"][:7]+"-31")
    n["zip_chg_since_orig"] = (n["zip_now"]/n["zip_at_orig"]-1) if n["zip_at_orig"] else None
    n["implied_value_now"] = n["Sales Price"] * (1 + n["zip_chg_since_orig"]) if n["zip_chg_since_orig"] is not None else None
    for k in list(n):
        if isinstance(n[k], (dt.datetime, dt.date)): n[k] = n[k].isoformat()
    notes.append(n)
tb = sum(n["Balance"] for n in notes); war = sum(n["Balance"]*n["Interest Rate"] for n in notes)/tb
print("notes", len(notes), "balance", round(tb), "WAR %.2f" % war, "payments/mo", round(sum(n["Payment"] for n in notes)), "est value", sum(n["EST. VALUE"] for n in notes), "agg LTV %.1f%%" % (tb/sum(n["EST. VALUE"] for n in notes)*100))
for n in notes:
    print(n["zip"], n["Property Address"][:32].ljust(32), "rate", n["Interest Rate"], "bal", round(n["Balance"]), "est", n["EST. VALUE"], "LTV %.0f%%" % (n["cur_ltv"]*100), "behind", n["months_behind"], "due", n["due_full"], "orig", n["orig"], "zip since orig %+.0f%%" % (n["zip_chg_since_orig"]*100), "implied", n["implied_value_now"] and round(n["implied_value_now"]))
json.dump({"zips": out, "notes": notes}, open("analysis.json", "w"), default=str)
