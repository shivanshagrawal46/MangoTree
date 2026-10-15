import csv, json, sys
ZIPS = ["78064","78101","78245","78247","78216","78233","78225","78212","78133","78266","78132","78230","78201","78109","78250","77061","75189"]
METROS = ["San Antonio, TX","Houston, TX","Dallas, TX","United States"]
def load(path, key, wanted):
    out = {}
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.reader(f); header = next(r)
        dates = [h for h in header if len(h)==10 and h[4]=='-']
        first = header.index(dates[0]); ki = header.index(key)
        for row in r:
            k = row[ki].zfill(5) if key=="RegionName" and path.startswith("zip") else row[ki]
            if k in wanted:
                meta = dict(zip(header[:first], row[:first]))
                series = {d: float(v) for d, v in zip(dates, row[first:first+len(dates)]) if v}
                out[k] = {"meta": meta, "series": series}
    return out
def loadf(path, key, wanted):
    out = {}
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            k = row[key].zfill(5) if path.startswith("zip") else row[key]
            if k in wanted: out[k] = row
    return out
data = {
 "zip_zhvi": load("zip_zhvi.csv","RegionName",set(ZIPS)),
 "zip_zori": load("zip_zori.csv","RegionName",set(ZIPS)),
 "zip_zhvf": loadf("zip_zhvf.csv","RegionName",set(ZIPS)),
 "metro_zhvi": load("metro_zhvi.csv","RegionName",set(METROS)),
 "metro_zhvf": loadf("metro_zhvf.csv","RegionName",set(METROS)),
 "metro_inventory": load("metro_inventory.csv","RegionName",set(METROS)),
 "metro_doz": load("metro_doz.csv","RegionName",set(METROS)),
 "metro_pricecut": load("metro_pricecut.csv","RegionName",set(METROS)),
 "state_zhvi": load("state_zhvi.csv","RegionName",{"Texas","Florida","California","United States","Arizona","North Carolina","Tennessee","Georgia"}),
}
json.dump(data, open("extract.json","w"))
# quick summary
def at(s, d):  # value at or before date d
    ks = sorted(k for k in s if k <= d); return s[ks[-1]] if ks else None
print("ZIP ZHVI: last date", max(next(iter(data["zip_zhvi"].values()))["series"]))
for z in ZIPS:
    e = data["zip_zhvi"].get(z)
    if not e: print(z, "MISSING"); continue
    s = e["series"]; last = max(s); v = s[last]
    pk = max(s.items(), key=lambda kv: kv[1])
    fc = data["zip_zhvf"].get(z, {})
    zori = data["zip_zori"].get(z); rent = zori and zori["series"][max(zori["series"])]
    print(z, e["meta"]["City"], e["meta"]["CountyName"], "| 2016:%.0f 2019:%.0f 2021:%.0f 2022mid:%.0f peak:%s %.0f now:%.0f yoy:%.1f%% 5y:%.1f%% | fc:%s | rent %s" % (
        at(s,"2016-09-30"), at(s,"2019-09-30"), at(s,"2021-09-30"), at(s,"2022-06-30"), pk[0][:7], pk[1], v,
        (v/at(s,"2025-09-30")-1)*100, (v/at(s,"2021-09-30")-1)*100, [fc.get(k) for k in list(fc)[-3:]], rent))
for m in METROS:
    s = data["metro_zhvi"][m]["series"]; last=max(s); v=s[last]; pk=max(s.items(), key=lambda kv: kv[1])
    inv = data["metro_inventory"].get(m,{}).get("series",{}); doz=data["metro_doz"].get(m,{}).get("series",{}); pc=data["metro_pricecut"].get(m,{}).get("series",{})
    print(m, "now %.0f yoy %.1f%% 5y %.1f%% peak %s %.0f drawdown %.1f%% | inv now %s vs 2019 %s | doz %s | pricecut %s | fc %s" % (
        v,(v/at(s,"2025-09-30")-1)*100,(v/at(s,"2021-09-30")-1)*100,pk[0][:7],pk[1],(v/pk[1]-1)*100,
        inv and inv[max(inv)], inv and at(inv,"2019-09-30"), doz and doz[max(doz)], pc and pc[max(pc)], list(data["metro_zhvf"][m].items())[-3:]))
for st,e in data["state_zhvi"].items():
    s=e["series"]; last=max(s); v=s[last]; pk=max(s.items(), key=lambda kv: kv[1])
    print(st, "now %.0f yoy %.1f%% 5y %.1f%% 10y %.1f%% peak %s drawdown %.1f%%" % (v,(v/at(s,"2025-09-30")-1)*100,(v/at(s,"2021-09-30")-1)*100,(v/at(s,"2016-09-30")-1)*100,pk[0][:7],(v/pk[1]-1)*100))
