"use client";

/* The market around the subject from free public sources, plus what the
   public record says about the house. Each block names its source; a source
   that did not answer is shown as a gap, never as a zero. */
import * as React from "react";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Statistic } from "antd";
import { Droplets, Landmark, Percent, TrendingDown, TrendingUp, Users } from "lucide-react";
import { Badge, Card } from "@/components/arv/ui";
import { cn, money, fmtDate } from "@/lib/utils";
import { num, pct, type AnalysisDoc } from "@/lib/arv";
import { H, Row } from "./bits";

function Tip({ active, payload, label, fmt }: any) {
  if (!active || !payload?.length) return null;
  return <div className="rounded-xl border border-line bg-elev px-3 py-2 text-xs shadow-[var(--shadow)]"><div className="font-semibold mb-0.5">{label}</div><div className="tnum">{fmt(payload[0].value)}</div></div>;
}

export function MarketTab({ doc }: { doc: AnalysisDoc }) {
  const m = doc.market;
  const z = m?.zhvi;
  const r = m?.mortgage;
  const f = m?.flood;
  const a = m?.acs;
  const rec = doc.records;
  const up = (z?.change_12m_pct ?? 0) >= 0;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card className="p-4">
        <H right={z ? <Badge tone={up ? "good" : "critical"}>{up ? <TrendingUp size={11} /> : <TrendingDown size={11} />} {pct(z.change_12m_pct)} / 12 mo</Badge> : <Badge>unavailable</Badge>}>Home values · ZIP {doc.geocode?.zip || "—"}</H>
        {z ? (
          <>
            <div className="flex items-end justify-between gap-3 mb-2">
              <div><Statistic value={money(z.value)} /><div className="text-[11px] text-muted">typical home, {fmtDate(z.as_of, "MMM yyyy")}{z.metro ? ` · ${z.metro}` : ""}</div></div>
              <div className="text-right text-[11px] text-muted tnum">3 mo {pct(z.change_3m_pct)} · 36 mo {pct(z.change_36m_pct)}<br />time adjustment {z.monthly_rate_pct}%/mo</div>
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={z.history} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                <defs><linearGradient id="gZ" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--accent)" stopOpacity={0.35} /><stop offset="100%" stopColor="var(--accent)" stopOpacity={0} /></linearGradient></defs>
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} minTickGap={40} tickFormatter={(v) => String(v).slice(0, 7)} />
                <YAxis tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} tickFormatter={(v) => money(v, true)} width={56} domain={["auto", "auto"]} />
                <Tooltip content={<Tip fmt={(v: number) => money(v)} />} />
                <Area type="monotone" dataKey="value" stroke="var(--accent)" fill="url(#gZ)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
            <div className="text-[10.5px] text-faint mt-1">{z.source}. This is the index the engine walks each sale forward with.</div>
          </>
        ) : <div className="text-sm text-muted">Zillow&apos;s ZIP index did not load for this ZIP. The engine assumed 0.25%/month.</div>}
      </Card>

      <Card className="p-4">
        <H right={r ? <Badge tone="info"><Percent size={11} /> {r.rate_pct}% · {fmtDate(r.as_of, "MMM d")}</Badge> : <Badge>unavailable</Badge>}>30-year fixed mortgage rate</H>
        {r ? (
          <>
            <div className="text-[11px] text-muted mb-2">A year ago {r.year_ago_pct}%. Rates set the buyer pool at resale: a point higher cuts what a buyer at the same payment can pay by about 10%.</div>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={r.history} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--line)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} minTickGap={40} tickFormatter={(v) => String(v).slice(0, 7)} />
                <YAxis tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} tickFormatter={(v) => `${v}%`} width={40} domain={["auto", "auto"]} />
                <Tooltip content={<Tip fmt={(v: number) => `${v}%`} />} />
                <Line type="monotone" dataKey="rate" stroke="var(--info)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
            <div className="text-[10.5px] text-faint mt-1">{r.source}</div>
          </>
        ) : <div className="text-sm text-muted">FRED did not answer.</div>}
      </Card>

      <Card className="p-4">
        <H right={f ? <Badge tone={f.sfha ? "critical" : "good"}><Droplets size={11} /> zone {f.zone}</Badge> : <Badge>unavailable</Badge>}>Flood</H>
        {f ? <div className="text-sm">{f.note}{f.subtype ? <span className="text-muted"> · {f.subtype}</span> : null}<div className="text-[10.5px] text-faint mt-1">{f.source}</div></div> : <div className="text-sm text-muted">FEMA&apos;s flood layer did not answer for this point. Check the FIRM panel before lending.</div>}
      </Card>

      <Card className="p-4">
        <H right={a ? <Badge tone="info"><Users size={11} /> census tract</Badge> : <Badge>unavailable</Badge>}>Neighbourhood · {a?.name || doc.geocode?.county || "—"}</H>
        {a ? (
          <div className="grid grid-cols-2 gap-x-6">
            <Row label="Median home value" value={money(a.median_home_value)} />
            <Row label="Median household income" value={money(a.median_household_income)} />
            <Row label="Owner-occupied" value={pct(a.owner_share_pct)} />
            <Row label="Vacancy" value={pct(a.vacancy_pct)} />
            <Row label="Median year built" value={a.median_year_built ?? "—"} />
            <Row label="Population" value={num(a.population)} />
            <div className="col-span-2 text-[10.5px] text-faint mt-1">{a.source} · {a.vintage}. The subject&apos;s ARV {doc.valuation?.arv && a.median_home_value ? `is ${num(doc.valuation.arv / a.median_home_value, 1)}× the tract median — ` : ""}a renovated house far above its tract&apos;s median takes longer to sell.</div>
          </div>
        ) : <div className="text-sm text-muted">Census ACS did not answer for this tract.</div>}
      </Card>

      <Card className="p-4 xl:col-span-2">
        <H right={<Badge tone={rec ? "good" : "neutral"}><Landmark size={11} /> {rec ? "RentCast public record" : "no record source"}</Badge>}>What the record says about the house</H>
        {rec ? (
          <div className="grid gap-x-6 md:grid-cols-3">
            <Row label="Type · beds · baths" value={`${(rec.property_type || "—").replace("_", " ")} · ${rec.beds ?? "—"} · ${rec.baths ?? "—"}`} />
            <Row label="Living area · lot" value={`${num(rec.sqft)} · ${num(rec.lot_sqft)} sqft`} />
            <Row label="Built · stories" value={`${rec.year_built ?? "—"} · ${rec.stories ?? "—"}`} />
            <Row label="Last sale" value={rec.last_sale_price ? `${money(rec.last_sale_price)} · ${fmtDate(rec.last_sale_date)}` : "—"} />
            <Row label="Assessed value" value={rec.assessed_value ? `${money(rec.assessed_value)} (${rec.assessed_year})` : "—"} />
            <Row label="Property tax" value={rec.tax_annual ? `${money(rec.tax_annual)} / yr (${rec.tax_year})` : "—"} />
            <Row label="Garage · basement · pool" value={`${rec.garage_spaces ?? "—"} · ${rec.basement || "—"} · ${rec.pool === true ? "yes" : rec.pool === false ? "no" : "—"}`} />
            <Row label="Owner occupied" value={rec.owner_occupied === true ? "yes" : rec.owner_occupied === false ? "no" : "—"} />
            <Row label="Zoning · HOA" value={`${rec.zoning || "—"} · ${rec.hoa_monthly ? money(rec.hoa_monthly) + "/mo" : "none"}`} />
            {rec.sale_history?.length > 1 && <div className="md:col-span-3 text-[11px] text-muted mt-1">Sale history: {rec.sale_history.map((s: any) => `${fmtDate(s.date, "MMM yyyy")} ${money(s.price, true)}`).join(" → ")}</div>}
            {doc.subject.filled_from_records?.length ? <div className="md:col-span-3 text-[10.5px] text-faint mt-1">Filled from the record: {doc.subject.filled_from_records.join(", ")}. What you typed always wins.</div> : null}
          </div>
        ) : <div className="text-sm text-muted">No public-record source is configured (set RENTCAST_API_KEY for records, comparables and an AVM; the free tier covers 50 lookups a month). The analysis used what was entered.</div>}
      </Card>
      {m?.gaps?.length ? <div className={cn("xl:col-span-2 text-[11px] text-faint")}>Gaps: {m.gaps.join(" · ")}</div> : null}
    </div>
  );
}
