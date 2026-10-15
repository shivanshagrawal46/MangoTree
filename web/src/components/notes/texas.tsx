"use client";

/* Texas at state level: statistics, the state comparison on a switchable
   metric, supply in the three metros, the bear-to-bull ranges, and the
   drivers as short tagged lines. */
import * as React from "react";
import { Segmented, Statistic, Tag } from "antd";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS, BaseDots, Delta, GRID, RangeChart, Section, Tip, fmtMonth, yearTick } from "./shared";
import { merge, pct, type Dataset } from "@/lib/notes";

type Metric = "chg5" | "yoy" | "chg10" | "drawdown" | "above_trend";
const METRIC: Record<Metric, string> = { chg5: "5 years", yoy: "1 year", chg10: "10 years", drawdown: "From peak", above_trend: "Vs 2014-19 trend" };
type Sup = "inventory" | "days_to_pending" | "price_cuts";
const SUP: Record<Sup, { label: string; fmt: (v: number) => string }> = { inventory: { label: "Homes for sale", fmt: (v) => Math.round(v).toLocaleString() }, days_to_pending: { label: "Days to pending", fmt: (v) => Math.round(v) + " days" }, price_cuts: { label: "Price cuts", fmt: (v) => (v * 100).toFixed(0) + "%" } };
const SUPCOL: Record<string, string> = { "San Antonio": "var(--accent)", Houston: "var(--info)", Dallas: "var(--high)" };
const UP = [["Population", "+1.1% to +1.5% a year; Comal County +28% since 2020"], ["Jobs", "+1.1% to +1.7% next year, twice the US pace"], ["Oil", "near $90; supports Houston and the Eagle Ford"], ["Prices", "San Antonio and Houston already at fair value"]];
const DOWN = [["Supply", "15% of US permits; most standing inventory of any state"], ["Migration", "half the 2021-22 pace"], ["Insurance", "about $4,600 a year on a $300K house"], ["Property tax", "San Antonio's first rate rise in 33 years"], ["Rates", "7.4%: payment doubled since 2019 on a home worth 30% more"]];

export function Texas({ d }: { d: Dataset }) {
  const [metric, setMetric] = React.useState<Metric>("chg5");
  const [sup, setSup] = React.useState<Sup>("inventory");
  const states = d.aggregates.filter((a) => a.kind !== "metro");
  const get = (l: string) => d.aggregates.find((a) => a.label === l)!;
  const tx = get("Texas"), us = get("United States"), sa = get("San Antonio"), hou = get("Houston"), dfw = get("Dallas-Fort Worth");
  const val = (a: typeof tx) => metric === "chg5" ? a.by_year["2026"] / a.by_year["2021"] - 1 : metric === "yoy" ? a.by_year["2026"] / a.by_year["2025"] - 1 : metric === "chg10" ? a.by_year["2026"] / a.by_year["2016"] - 1 : metric === "drawdown" ? a.drawdown : a.above_trend;
  const bars = states.map((a) => ({ name: a.label, v: Math.round(val(a) * 1000) / 10 })).sort((a, b) => b.v - a.v);
  const cpi5 = d.cpi["2026-08"] / d.cpi["2021-08"];
  const supRows = React.useMemo(() => merge(Object.fromEntries(Object.entries(d.supply).filter(([k]) => k !== "United States").map(([k, m]) => [k, m[sup].series]))), [d.supply, sup]);
  const saSup = d.supply["San Antonio"];
  const ranges = [tx, sa, hou, dfw].map((a) => ({ name: a.label, bear: a.proj!.bear.cum, base: a.proj!.base.cum, bull: a.proj!.bull.cum }));
  const tone = (v: number) => ({ valueStyle: { color: v > 0 ? "var(--good)" : v < 0 ? "var(--critical)" : "var(--fg)", fontSize: 22 } });

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title="Texas typical home" value={tx.by_year["2026"]} prefix="$" precision={0} valueStyle={{ fontSize: 22 }} /><div className="text-[11px] text-muted">peak {tx.peak_date}</div></div>
        <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title="1 year" value={(tx.by_year["2026"] / tx.by_year["2025"] - 1) * 100} precision={1} suffix="%" {...tone(tx.by_year["2026"] / tx.by_year["2025"] - 1)} /><div className="text-[11px] text-muted">US <Delta v={us.by_year["2026"] / us.by_year["2025"] - 1} /></div></div>
        <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title="5 years" value={(tx.by_year["2026"] / tx.by_year["2021"] - 1) * 100} precision={1} suffix="%" {...tone(1)} /><div className="text-[11px] text-muted">real <Delta v={tx.by_year["2026"] / tx.by_year["2021"] / cpi5 - 1} /></div></div>
        <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title="10 years" value={(tx.by_year["2026"] / tx.by_year["2016"] - 1) * 100} precision={0} suffix="%" {...tone(1)} /><div className="text-[11px] text-muted">US <Delta v={us.by_year["2026"] / us.by_year["2016"] - 1} digits={0} /></div></div>
        <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title="From peak" value={tx.drawdown * 100} precision={1} suffix="%" {...tone(-1)} /><div className="text-[11px] text-muted">US at a new high</div></div>
        <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title="Vs fair value" value={(tx.over ?? 0) * 100} precision={1} suffix="%" valueStyle={{ fontSize: 22, color: "var(--high)" }} /><div className="text-[11px] text-muted">San Antonio {pct(sa.over ?? 0)}</div></div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-2" title="Texas against the migration states" right={<Segmented size="small" value={metric} onChange={(v) => setMetric(v as Metric)} options={Object.entries(METRIC).map(([v, l]) => ({ value: v, label: l }))} />}>
          <ResponsiveContainer width="100%" height={290}>
            <BarChart data={bars} layout="vertical" margin={{ left: 0, right: 48, top: 4, bottom: 4 }} barCategoryGap={6}>
              <CartesianGrid horizontal={false} stroke={GRID} />
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={(v) => v + "%"} domain={["auto", "auto"]} />
              <YAxis type="category" dataKey="name" width={104} tick={{ ...AXIS, fontSize: 11.5, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} interval={0} />
              <Tooltip content={<Tip fmt={(v: number) => (v > 0 ? "+" : "") + v.toFixed(1) + "%"} />} cursor={{ fill: "var(--bg-sunken)" }} />
              <ReferenceLine x={0} stroke="var(--line-strong)" />
              <Bar dataKey="v" name={METRIC[metric]} radius={[0, 5, 5, 0]} isAnimationActive={false}>
                {bars.map((b, i) => <Cell key={i} fill={b.name === "Texas" ? "var(--accent)" : b.name === "United States" ? "var(--fg-faint)" : b.v >= 0 ? "var(--info)" : "var(--critical)"} />)}
                <LabelList dataKey="v" position="right" formatter={(v: any) => (Number(v) > 0 ? "+" : "") + Number(v).toFixed(1) + "%"} style={{ fontSize: 11, fill: "var(--fg-muted)" }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Section>
        <Section className="lg:col-span-3" title="Supply in the three metros" right={<Segmented size="small" value={sup} onChange={(v) => setSup(v as Sup)} options={Object.entries(SUP).map(([v, m]) => ({ value: v, label: m.label }))} />}>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={supRows} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} />
              <YAxis tick={AXIS} axisLine={false} tickLine={false} width={52} domain={["auto", "auto"]} tickFormatter={(v) => (sup === "inventory" ? Math.round(v / 1000) + "K" : sup === "price_cuts" ? Math.round(v * 100) + "%" : String(v))} />
              <Tooltip content={<Tip fmt={SUP[sup].fmt} title={fmtMonth} />} />
              {Object.keys(d.supply).filter((k) => k !== "United States").map((k) => <Line key={k} type="monotone" dataKey={k} stroke={SUPCOL[k]} strokeWidth={k === "San Antonio" ? 2.4 : 1.6} dot={false} isAnimationActive={false} connectNulls />)}
              <Legend wrapperStyle={{ fontSize: 11.5 }} iconType="plainline" />
            </LineChart>
          </ResponsiveContainer>
          <div className="grid grid-cols-3 gap-3 mt-2">
            {(["inventory", "days_to_pending", "price_cuts"] as Sup[]).map((k) => (
              <div key={k} className="rounded-xl border border-line px-3 py-2"><div className="text-[10.5px] uppercase tracking-wider text-muted font-medium">SA · {SUP[k].label}</div><div className="text-[16px] font-semibold tnum mt-0.5">{SUP[k].fmt(saSup[k].now)} <Delta v={saSup[k].now / saSup[k].y2019 - 1} digits={0} className="text-[11.5px]" /></div><div className="text-[11px] text-muted tnum">vs 2019: {SUP[k].fmt(saSup[k].y2019)}</div></div>
            ))}
          </div>
        </Section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-3" title="Next five years, bear to bull" sub="Base case: Texas +4%, roughly 1% a year. Bull needs 6% mortgages by end 2027; no major forecaster expects that.">
          <div className="grid grid-cols-1 md:grid-cols-[1fr_240px] gap-3 items-center"><RangeChart rows={ranges} height={180} /><BaseDots rows={ranges} /></div>
        </Section>
        <Section className="lg:col-span-2" title="What moves it">
          <div className="space-y-1.5">
            {UP.map(([k, v]) => <div key={k} className="flex items-center gap-2 text-[12.5px]"><ArrowUpRight size={14} className="text-good shrink-0" /><Tag color="success" className="!m-0 !text-[11px] w-[92px] text-center">{k}</Tag><span className="text-muted">{v}</span></div>)}
            {DOWN.map(([k, v]) => <div key={k} className="flex items-center gap-2 text-[12.5px]"><ArrowDownRight size={14} className="text-critical shrink-0" /><Tag color="error" className="!m-0 !text-[11px] w-[92px] text-center">{k}</Tag><span className="text-muted">{v}</span></div>)}
          </div>
        </Section>
      </div>
    </div>
  );
}
