"""Package the notes analysis into one dataset JSON the API serves and the web
Notes section renders. Run from the scratch folder that holds report_data.json
and the FRED CSVs; writes mangotree/notes/datasets/<id>.json.

    python scripts/notes_analysis/make_dataset.py <scratch dir> [out path]
"""
import csv
import json
import os
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "..", "..", "mangotree", "notes", "datasets", "hk_homes_2026_10_16.json")
R = json.load(open(os.path.join(SRC, "report_data.json")))
Z = R["zips"]; N = R["notes"]; S = R["summary"]
ZIPS = ["78064", "78101", "78245", "78247", "78216", "78233", "78225", "78212", "78133", "78266", "78132", "78230", "78201", "78109", "78250", "77061", "75189"]
AREA = {
    "78064": ("Pleasanton", "Atascosa County, 35 miles south of San Antonio. Small oil-and-ranch town; thin market with few sales a month.", "rural"),
    "78101": ("Adkins", "Rural east Bexar County; acreage homesteads. One of two portfolio ZIPs still rising on the year.", "rural"),
    "78245": ("Far West SA", "Alamo Ranch and Potranco corridor; the metro's biggest new-build belt. Three notes sit here.", "builder belt"),
    "78247": ("North Central SA", "1980s to 1990s subdivisions between US 281 and Loop 1604; established, mid-price.", "established"),
    "78216": ("Airport / North Central", "Older 1960s and 1970s housing near the airport; peaked late, in May 2024.", "established"),
    "78233": ("Northeast SA", "Live Oak and Windcrest edge; entry-level stock that competes with new builds on I-35.", "builder belt"),
    "78225": ("Southwest inner city", "Lowest price point in the portfolio; Zillow shows the steepest fall from peak. FHFA publishes no index here (too few sales).", "inner city"),
    "78212": ("Monte Vista / Tobin Hill", "Historic core north of downtown. The ZIP index tracks mid-price homes; the two Howard St collateral houses sit far above it.", "inner city"),
    "78133": ("Canyon Lake", "Comal County lake and second-home market; the most rate-sensitive buyer pool in the set.", "premium"),
    "78266": ("Garden Ridge / Universal City", "Comal County, higher-end acreage; peaked June 2024 and has given back little.", "premium"),
    "78132": ("New Braunfels west", "Hill Country side of one of the fastest-growing counties in the country; large pandemic run-up.", "premium"),
    "78230": ("Northwest SA", "Medical Center and Shavano area; established and steady. Two notes here.", "established"),
    "78201": ("Near West / Beacon Hill", "Older 1920s to 1950s housing and flip activity. Zillow and FHFA disagree sharply here; thin data.", "inner city"),
    "78109": ("Converse", "Northeast builder belt off I-10 and Loop 1604; highest rent yield in the portfolio.", "builder belt"),
    "78250": ("Great Northwest SA", "1980s to 2000s subdivisions off Bandera Road and Loop 1604; mid-price.", "established"),
    "77061": ("Houston, Hobby area", "Southeast Houston near Hobby Airport; older stock, peaked June 2024. One 12% investor note.", "established"),
    "75189": ("Royse City", "Far-east Dallas exurb across Rockwall and Hunt counties; builder-dominated, 7 months of supply in August.", "builder belt"),
}


def fred(name, start):
    return [[r[0], float(r[1])] for r in list(csv.reader(open(os.path.join(SRC, name))))[1:] if r[0] >= start and r[1] not in ("", ".")]


def series(d, start="2015-01-01"):
    return [[k, round(v)] for k, v in sorted(d.items()) if k >= start]


def proj(p):
    return {k: {"path": p[k]["path"], "values": [round(x) for x in p[k]["values"]], "cum": p[k]["cum"]} for k in ("base", "bear", "bull")}


notes_in = {}
for n in N:
    notes_in.setdefault(n["zip"], []).append(n)
zips = []
for z in ZIPS:
    e = Z[z]; p = e["proj"]
    zips.append({"zip": z, "name": AREA[z][0], "blurb": AREA[z][1], "kind": AREA[z][2], "city": e["city"], "county": e["county"], "metro": e["metro_key"],
                 "by_year": {k: round(v) for k, v in e["by_year"].items()}, "peak_date": e["peak_date"][:7], "peak": round(e["peak"]), "drawdown": e["drawdown"], "yoy": e["yoy"], "chg5": e["chg5"], "chg10": e["chg10"],
                 "rent": e.get("rent"), "rent_chg5": e.get("rent_chg5"), "gross_yield": e.get("gross_yield"), "zhvf": e["zhvf"], "fhfa": e["fhfa"],
                 "fair": round(p["fair"]), "over": p["over"], "proj": proj(p), "expected": p["expected"],
                 "series": series(e["series_monthly"]), "notes": [n["Property Address"].split(",")[0] for n in notes_in.get(z, [])], "balance": sum(n["Balance"] for n in notes_in.get(z, []))})


def agg(key, label, kind):
    e = Z[key]
    o = {"key": key, "label": label, "kind": kind, "by_year": {k: round(v) for k, v in e["by_year"].items()}, "peak_date": e["peak_date"][:7], "peak": round(e["peak"]), "drawdown": e["drawdown"],
         "series": series(e["series_monthly"]), "above_trend": e["above_trend"]}
    if "proj" in e:
        o["fair"] = round(e["proj"]["fair"]); o["over"] = e["proj"]["over"]; o["proj"] = proj(e["proj"])
    return o


aggs = [agg("metro_zhvi:San Antonio, TX", "San Antonio", "metro"), agg("metro_zhvi:Houston, TX", "Houston", "metro"), agg("metro_zhvi:Dallas, TX", "Dallas-Fort Worth", "metro"), agg("metro_zhvi:United States", "United States", "nation"),
        agg("state_zhvi:Texas", "Texas", "state"), agg("state_zhvi:Florida", "Florida", "state"), agg("state_zhvi:California", "California", "state"), agg("state_zhvi:Arizona", "Arizona", "state"),
        agg("state_zhvi:North Carolina", "North Carolina", "state"), agg("state_zhvi:Tennessee", "Tennessee", "state"), agg("state_zhvi:Georgia", "Georgia", "state")]
supply = {}
for m in ("San Antonio, TX", "Houston, TX", "Dallas, TX", "United States"):
    supply[m.replace(", TX", "")] = {k: {"now": Z[k2 + ":" + m]["now"], "yr_ago": Z[k2 + ":" + m]["yr_ago"], "y2019": Z[k2 + ":" + m]["2019"], "y2022": Z[k2 + ":" + m]["2022"],
                                         "series": [[d, v] for d, v in sorted(Z[k2 + ":" + m]["series_monthly"].items())]}
                                     for k, k2 in (("inventory", "metro_inventory"), ("days_to_pending", "metro_doz"), ("price_cuts", "metro_pricecut"))}
notes = []
for n in N:
    ltv = n["cur_ltv"]; by = int(n["Due in Full"].split("/")[-1])
    flags = []
    if ltv is not None and ltv > 1: flags.append("balance above value")
    elif ltv is not None and ltv >= 0.78: flags.append("thin equity")
    if by <= 2030: flags.append("balloon " + n["Due in Full"])
    if n["Property Address"].startswith("315 Howard"): flags.append("estimated value looks wrong: $750,000 sale against a $510,000 balance")
    notes.append({"address": n["Property Address"], "short": n["Property Address"].split(",")[0], "zip": n["zip"], "area": AREA[n["zip"]][0], "rate": n["Interest Rate"], "payment": n["Payment"], "balance": n["Balance"],
                  "orig_balance": n["Original Balance"], "sales_price": n["Sales Price"], "est_value": n["EST. VALUE"], "ltv": ltv, "ltv_at_orig": n["ltv_at_orig"], "originated": n["orig"], "due_in_full": n["Due in Full"], "balloon_year": by,
                  "next_due": n["Next Due"], "last_paid": n["Last Paid"], "paid_to": n["Interest Paid-To"], "ytd_interest": n["YTD Interest"], "ytd_principal": n["YTD Principal"], "note": n.get("Notes"), "behind": n["behind"], "status": n["status"],
                  "zip_since_note": n["zip_chg_since_orig"], "ltv_2031_base": n["ltv_2031_base"], "ltv_2031_bear": n["ltv_2031_bear"], "flags": flags})
late = [n for n in notes if n["behind"] > 0]
mort = fred("fred_MORTGAGE30US.csv", "2016-01-01"); ff = fred("fred_FEDFUNDS.csv", "2016-01-01")
D = {
    "id": "hk_homes_2026_10_16", "title": "H & K Homes note portfolio", "as_of": "2026-10-16", "source_file": "Note_Portfolio_Report_HK HOMES.xlsx", "data_through": "2026-08-31",
    "summary": {"notes": S["n"], "balance": S["balance"], "value": S["value"], "ltv": S["ltv"], "war": S["war"], "payment": S["pay"], "late_n": len(late), "late_balance": sum(n["balance"] for n in late),
                "w5y": S["w5y"], "wyoy": S["wyoy"], "outlook": S["wexp"], "mortgage_now": mort[-1][1], "mortgage_date": mort[-1][0], "mortgage_yr_ago": 6.27, "fed_funds": "3.75 to 4.00%",
                "balloons": [{"short": n["short"], "due": n["due_in_full"], "balance": n["balance"], "rate": n["rate"]} for n in notes if n["balloon_year"] <= 2030]},
    "zips": zips, "aggregates": aggs, "supply": supply,
    "rates": {"mortgage30": mort, "fed_funds": ff, "forecasts": [
        {"who": "Fannie Mae", "when": "14 Oct 2026", "q4_2026": 7.4, "y2027": 7.5, "note": "Raised from 6.8% and 6.7% a month earlier"},
        {"who": "MBA", "when": "Oct 2026", "q4_2026": 7.3, "y2027": 7.15, "note": "7.2% through Q3 2027, 7.1% in Q4"},
        {"who": "Texas A&M TRERC", "when": "10 Sep 2026", "q4_2026": None, "y2027": 6.5, "note": "6% to 7% by August 2027; written before the Fed hike"},
        {"who": "This analysis", "when": "16 Oct 2026", "q4_2026": 7.4, "y2027": 7.2, "note": "7% to 7.5% through most of 2027, drifting to 6.5% in 2028"}],
        "investor": [{"product": "30-year DSCR rental loan", "range": "7.0% to 8.0%", "note": "Quoted 6.1% to 7.4% in September before the move"},
                     {"product": "Conventional investor mortgage", "range": "8.0% to 8.3%", "note": "About 0.75 pt over owner-occupied"},
                     {"product": "Texas hard money / fix-and-flip", "range": "9% to 13.5% + 1 to 3 pts", "note": "6 to 18 month terms, 65% to 75% of ARV"},
                     {"product": "This portfolio", "range": "8%% to 12%%, weighted %.2f%%" % S["war"], "note": "Seller-financed; most borrowers could not get bank credit"}],
        "events": [["2022-03", "Fed starts hiking"], ["2023-10", "Mortgage peak 7.79%"], ["2024-09", "First Fed cut"], ["2026-01", "Low of 6.06%"], ["2026-09", "Fed hikes to 3.75 to 4%"]]},
    "hpi": {"texas": fred("fred_TXSTHPI.csv", "2015-01-01"), "san_antonio": fred("fred_SAHPI.csv", "2015-01-01")},
    "cpi": {"2019-08": 256.036, "2021-08": 272.676, "2026-08": 334.131},
    "notes_table": notes,
    "model": {"year1": "Zillow 12-month ZIP forecast (September release, made at 6.7% rates) less 1.5 pts for the jump to 7.4%. Bear: less 4 pts. Bull: plus 0.5.",
              "later": "Anchor 3% a year for San Antonio and Houston, 2.5% for Dallas-Fort Worth (Texas incomes +5% a year, population +1.1 to 1.5%; about half reaches prices while payments stay high). Bear years 2 to 5: -3, 0, +2, +3. Bull: +4, +5, +4.5, +4.",
              "fair": "Fair value = August 2019 value x 1.305 (CPI change to August 2026). A ZIP above fair loses 0.3 pt per 1% of premium each year (max 2); one below gains at most 1.",
              "supply": "0.5 to 1.5 pts a year in years 2 and 3 where builders compete: Royse City most, then Far West, Converse, Northeast, Canyon Lake, New Braunfels, Pleasanton.",
              "weights": "Expected = 50% base, 30% bear, 20% bull."},
    "narrative": {
        "headline": "The last five years were a round trip. San Antonio is back at fair value but frozen by a 7.4% mortgage rate. Expect flat real values and modest dollar gains over the next five years.",
        "points": [
            "San Antonio rose 17% from August 2021 to its August 2022 peak, then fell 10%. Five-year gain 4.4% in dollars, about 15% down after inflation. Texas is up 10% over five years and down 1.4% on the year; the national index is up 20% and still rising.",
            "Had prices only kept pace with inflation since 2019 the typical San Antonio home would be $277,227; it is $276,747. The correction in real terms is done. The monthly payment on that home at 80% financing went from $770 in 2019 to $1,533 at 7.4%.",
            "Base case for San Antonio to 2031 is +10% cumulative; bear -3%; bull +18%. Texas overall is weaker at +4% base because Dallas-Fort Worth and Austin still carry a pandemic premium.",
            "The Fed raised on 16 September for the first time since 2023; the 30-year mortgage went from 6.7% to 7.40% in six weeks and Fannie Mae expects 7.5% through 2027. The 8% to 12% coupons will not be refinanced away, but every borrower who must sell or refinance faces a thin market.",
            "Portfolio LTV is 62%. Six notes sit at 78% or above; 917 Live Oak in Royse City is above 100%. 315 Howard St's estimated value looks like a data error. Seven notes had an August payment outstanding, five of them in ZIPs down 8% to 19% from peak."],
        "texas_up": ["Population still grows 1.1% to 1.5% a year; Texas added 2.0 million people from 2020 to 2024, three quarters of them migrants. Comal County +28% since 2020, Bexar +7.5%.",
                     "Texas payrolls are forecast +1.1% to +1.7% in the year to August 2027 against +0.4% to +0.8% nationally. Oil near $90 supports Houston and the Eagle Ford counties south of San Antonio.",
                     "San Antonio, Houston and most of the state outside the premium suburbs have already corrected to fair value."],
        "texas_down": ["Supply: Texas issued 15% of the nation's permits in 2024 and builders still hold the most standing inventory of any state; new homes now sell for less than existing ones nationally.",
                       "Migration has halved from its 2021 to 2022 pace; pandemic-boom metros are losing net movers.",
                       "Carrying costs: homeowners' insurance averages about $4,600 a year on a $300,000 house; San Antonio is raising its property tax rate for the first time in 33 years.",
                       "Rates: every point on the mortgage is roughly 10% of the payment; at 7.4% the typical buyer pays twice what a 2019 buyer paid for a home worth 30% more."],
        "loans": ["The coupons are safe from refinancing while mortgages stay above 7%: an 8% borrower saves nothing after costs, and most 10% borrowers hold these notes because a bank would not lend to them. If rates fall to 6%, the strongest borrowers (Beechwood, East Byrd, Hialeah, Morning Glory) refinance out first.",
                  "Balloons are the main rate exposure: 113 Crestline ($400,000 at 10%, 80% LTV) is due 6 March 2027; 315 and 421 Howard St ($1.55 million at 12%, interest-only) are due mid-2029. Plan extension terms now.",
                  "Delinquency clusters where values fell: five of the seven August arrears are in ZIPs down 8% to 19% from peak. Borrowers with little equity cannot sell their way out.",
                  "Note values move inversely with rates: buyers of seller-finance paper now want 10% to 12% yields, so the portfolio would sell near or below par. Hold to maturity.",
                  "A 10% fall in values moves the portfolio LTV from 62% to about 69% and puts six notes above 85%.",
                  "New notes written now should be at 70% LTV or less with a contractor-bid rehab budget, because there is no price growth to cover a thin margin."],
        "actions": ["Fix the sheet: 315 Howard St's estimated value, and the Summary tab (32 accounts, $7.26M) against the detail tab (22, $4.67M).",
                    "Open the 113 Crestline balloon conversation now; five months to a $400,000 maturity.",
                    "Work the seven August arrears this month, starting with 7947 Glennscott (two payments, 12%, Houston) and the three Far West notes.",
                    "Get broker opinions on the four thin-equity notes: Range Field, Menlo Blvd, Rock Creek Run, Live Oak (Royse City).",
                    "Underwrite new notes to the flat case: zero appreciation for two years, 3% after; 70% LTV cap.",
                    "Hold the paper; it would sell at a discount today.",
                    "Re-run in April when Zillow's forecast and the FHFA ZIP index refresh."],
        "outside": [{"who": "Zillow (Sep 2026)", "what": "San Antonio -0.7%, Houston -0.2%, Dallas -0.3% to August 2027; national +1.4%. Fourteen of the 17 ZIPs between -1.5% and -0.2%."},
                    {"who": "Texas A&M TRERC (10 Sep 2026)", "what": "Texas median +1.8% to $341,000 by summer 2027; sales +2.5%; permits flat at 149,000; rents flat near $2,200."},
                    {"who": "Reventure", "what": "San Antonio 35/100 and Texas 37/100 entering 2025 (below 45 means declining); Texas 17.7% overvalued, -4% forecast. Sep 2026 posts: national prices 25% above norms, demand at a 40-year low, North Dallas ZIPs down nearly 20%. Nothing new on San Antonio in 2026."},
                    {"who": "Dallas Fed (29 Sep 2026)", "what": "San Antonio median -1.4% on the year, 5.7 months of supply, apartment rents -2.5%, payrolls -0.4% year to date."},
                    {"who": "SABOR (Aug 2026)", "what": "Median $299,275, flat on the year; 16,940 active listings; 5.87 months of supply; 92.8% of list."}],
        "sources": ["Zillow Research ZHVI, ZHVF, ZORI and inventory, through 31 Aug 2026", "FHFA five-digit ZIP HPI (updated 31 Mar 2026) and quarterly Texas and San Antonio indexes via FRED", "Freddie Mac PMMS, effective fed funds and CPI-U via FRED",
                    "Texas A&M Real Estate Research Center forecast through summer 2027", "Dallas Fed San Antonio Economic Indicators, Sep 2026", "Fannie Mae and MBA October 2026 outlooks", "Reventure App blog and 2025 Texas commentary", "Census Vintage 2025 county estimates; lender DSCR and hard-money rate sheets"],
        "caveats": ["FHFA and Zillow disagree in the thin inner-city ZIPs (78201, 78225, 78212); 78225 has no FHFA index.", "Zillow's ZIP value is for mid-priced homes and does not describe the Howard St houses.", "The note sheet has no report date; payment status assumes end of August.", "Reventure's San Antonio and Texas scores are from 2025."]},
}
os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
json.dump(D, open(OUT, "w"), separators=(",", ":"))
print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")
