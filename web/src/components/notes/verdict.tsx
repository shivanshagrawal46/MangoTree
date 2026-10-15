"use client";

/* The summary tab as a scorecard: six verdict meters, the then/now/next
   strip for San Antonio, the scenario ranges for every market, collateral
   and payment health as progress rings, and the action list as tags. */
import * as React from "react";
import { Progress, Tag, Timeline } from "antd";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, LabelList, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS, BaseDots, Delta, GRID, RangeChart, Section, Spark, Tip, Verdict, fmtMonth, yearTick } from "./shared";
import { money, pct, type Dataset } from "@/lib/notes";

export function Summary({ d, go, pickZip }: { d: Dataset; go: (t: any) => void; pickZip: (z: string) => void }) {
  const sa = d.aggregates.find((a) => a.label === "San Antonio")!; const tx = d.aggregates.find((a) => a.label === "Texas")!; const us = d.aggregates.find((a) => a.label === "United States")!;
  const hou = d.aggregates.find((a) => a.label === "Houston")!; const dfw = d.aggregates.find((a) => a.label === "Dallas-Fort Worth")!;
  const s = d.summary; const cpi5 = d.cpi["2026-08"] / d.cpi["2021-08"];
  const ranges = [
    { name: "Portfolio ZIPs", bear: s.outlook.bear, base: s.outlook.base, bull: s.outlook.bull },
    { name: "San Antonio", bear: sa.proj!.bear.cum, base: sa.proj!.base.cum, bull: sa.proj!.bull.cum },
    { name: "Houston", bear: hou.proj!.bear.cum, base: hou.proj!.base.cum, bull: hou.proj!.bull.cum },
    { name: "Dallas-Fort Worth", bear: dfw.proj!.bear.cum, base: dfw.proj!.base.cum, bull: dfw.proj!.bull.cum },
    { name: "Texas", bear: tx.proj!.bear.cum, base: tx.proj!.base.cum, bull: tx.proj!.bull.cum },
  ];
  const then = [
    { name: "Texas", v5: tx.by_year["2026"] / tx.by_year["2021"] - 1, v1: tx.by_year["2026"] / tx.by_year["2025"] - 1 },
    { name: "San Antonio", v5: sa.by_year["2026"] / sa.by_year["2021"] - 1, v1: sa.by_year["2026"] / sa.by_year["2025"] - 1 },
    { name: "Portfolio ZIPs", v5: s.w5y, v1: s.wyoy },
    { name: "United States", v5: us.by_year["2026"] / us.by_year["2021"] - 1, v1: us.by_year["2026"] / us.by_year["2025"] - 1 },
  ].map((r) => ({ ...r, v5: Math.round(r.v5 * 1000) / 10, v1: Math.round(r.v1 * 1000) / 10 }));
  const late = d.notes_table.filter((n) => n.behind > 0); const thin = d.notes_table.filter((n) => (n.ltv || 0) >= 0.78);
  const saRows = sa.series.filter(([dt]) => dt >= "2019-01").map(([dt, v]) => ({ date: dt.slice(0, 7), value: v }));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Verdict title="Last 5 years" value={<Delta v={sa.by_year["2026"] / sa.by_year["2021"] - 1} className="!text-[20px]" />} level={2} labels={["fell", "rose"]} sub={<>San Antonio; <Delta v={sa.by_year["2026"] / sa.by_year["2021"] / cpi5 - 1} /> after inflation</>} />
        <Verdict title="Price today" value="Fair" level={4} labels={["overpriced", "cheap"]} sub={<>San Antonio <Delta v={sa.over ?? 0} /> vs 2019 + inflation</>} />
        <Verdict title="Next 5 years" value={<Delta v={sa.proj!.base.cum} className="!text-[20px]" />} level={3} labels={["falls", "booms"]} sub={<>base case; bear <Delta v={sa.proj!.bear.cum} />, bull <Delta v={sa.proj!.bull.cum} /></>} />
        <Verdict title="Rates" value={s.mortgage_now.toFixed(2) + "%"} level={1} labels={["headwind", "tailwind"]} sub={<>30-year, up from {s.mortgage_yr_ago}% a year ago</>} />
        <Verdict title="Collateral" value={pct(s.ltv, false, 0) + " LTV"} level={4} labels={["thin", "sound"]} sub={<>{thin.length} of {s.notes} notes at 78% or more</>} />
        <Verdict title="Payments" value={`${s.late_n} late`} level={2} labels={["stressed", "clean"]} sub={<>{money(s.late_balance, true)} of balance; {s.balloons.length} balloons by 2030</>} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-3" title="San Antonio, then and now" sub="Typical home value. Peak August 2022; back at the inflation-adjusted 2019 level today." right={<button className="text-[12px] text-accent hover:underline" onClick={() => go("history")}>Full history →</button>}>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={saRows} margin={{ left: 0, right: 12, top: 10, bottom: 0 }}>
              <defs><linearGradient id="gSA" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--accent)" stopOpacity={0.3} /><stop offset="100%" stopColor="var(--accent)" stopOpacity={0} /></linearGradient></defs>
              <CartesianGrid vertical={false} stroke={GRID} />
              <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} />
              <YAxis tick={AXIS} axisLine={false} tickLine={false} width={52} domain={["auto", "auto"]} tickFormatter={(v) => money(v, true)} />
              <Tooltip content={<Tip title={fmtMonth} />} />
              <ReferenceLine y={sa.fair} stroke="var(--fg-faint)" strokeDasharray="2 4" label={{ value: "fair value", position: "insideBottomLeft", fontSize: 10, fill: "var(--fg-muted)" }} />
              <ReferenceLine x={sa.peak_date} stroke="var(--line-strong)" strokeDasharray="3 3" label={{ value: `peak ${money(sa.peak, true)}`, position: "insideTopLeft", fontSize: 10, fill: "var(--fg-faint)" }} />
              <Area type="monotone" dataKey="value" name="San Antonio" stroke="var(--accent)" fill="url(#gSA)" strokeWidth={2} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
          <div className="grid grid-cols-3 gap-3 mt-3 text-center">
            <div className="rounded-xl bg-sunken/60 py-2"><div className="text-[10.5px] uppercase tracking-wider text-muted">Aug 2021</div><div className="text-[16px] font-semibold tnum">{money(sa.by_year["2021"], true)}</div></div>
            <div className="rounded-xl bg-sunken/60 py-2"><div className="text-[10.5px] uppercase tracking-wider text-muted">Peak Aug 2022</div><div className="text-[16px] font-semibold tnum">{money(sa.peak, true)}</div></div>
            <div className="rounded-xl bg-accent-soft/60 py-2"><div className="text-[10.5px] uppercase tracking-wider text-muted">Aug 2026</div><div className="text-[16px] font-semibold tnum">{money(sa.by_year["2026"], true)} <Delta v={sa.drawdown} className="text-[12px]" /></div></div>
          </div>
        </Section>
        <Section className="lg:col-span-2" title="5 years and 1 year" sub="Change in typical value, dollars.">
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={then} margin={{ left: 0, right: 36, top: 4, bottom: 4 }} layout="vertical" barCategoryGap={10}>
              <CartesianGrid horizontal={false} stroke={GRID} />
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={(v) => v + "%"} />
              <YAxis type="category" dataKey="name" width={100} tick={{ ...AXIS, fontSize: 11.5, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} interval={0} />
              <Tooltip content={<Tip fmt={(v: number) => (v > 0 ? "+" : "") + v.toFixed(1) + "%"} />} cursor={{ fill: "var(--bg-sunken)" }} />
              <ReferenceLine x={0} stroke="var(--line-strong)" />
              <Bar dataKey="v5" name="5 years" fill="var(--accent)" radius={[0, 4, 4, 0]} isAnimationActive={false}><LabelList dataKey="v5" position="right" formatter={(v: any) => (Number(v) > 0 ? "+" : "") + v + "%"} style={{ fontSize: 10.5, fill: "var(--fg-muted)" }} /></Bar>
              <Bar dataKey="v1" name="1 year" fill="var(--fg-faint)" radius={[0, 4, 4, 0]} isAnimationActive={false}><LabelList dataKey="v1" position="right" formatter={(v: any) => (Number(v) > 0 ? "+" : "") + v + "%"} style={{ fontSize: 10.5, fill: "var(--fg-muted)" }} /></Bar>
            </BarChart>
          </ResponsiveContainer>
          <div className="flex gap-3 text-[11px] text-muted mt-1"><span className="flex items-center gap-1"><i className="w-2.5 h-2.5 rounded-sm bg-accent" />5 years</span><span className="flex items-center gap-1"><i className="w-2.5 h-2.5 rounded-sm bg-faint" />1 year</span></div>
        </Section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-3" title="Next five years, bear to bull" sub="Cumulative change to August 2031. Bar runs from bear to bull; base in the tooltip and on the right." right={<button className="text-[12px] text-accent hover:underline" onClick={() => go("outlook")}>Every ZIP →</button>}>
          <div className="grid grid-cols-1 md:grid-cols-[1fr_260px] gap-3 items-center">
            <RangeChart rows={ranges} height={210} />
            <BaseDots rows={ranges} />
          </div>
        </Section>
        <Section className="lg:col-span-2" title="The notes" sub="Collateral cover and payment health." right={<button className="text-[12px] text-accent hover:underline" onClick={() => go("notes")}>Note by note →</button>}>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div><Progress type="dashboard" percent={Math.round(s.ltv * 100)} size={88} strokeColor="var(--accent)" format={(p) => <span className="text-[15px] font-semibold">{p}%</span>} /><div className="text-[11px] text-muted mt-1">Loan to value</div></div>
            <div><Progress type="dashboard" percent={Math.round((late.length / s.notes) * 100)} size={88} strokeColor="var(--high)" format={() => <span className="text-[15px] font-semibold">{late.length}<span className="text-[11px] text-muted">/{s.notes}</span></span>} /><div className="text-[11px] text-muted mt-1">August payment late</div></div>
            <div><Progress type="dashboard" percent={Math.round((thin.length / s.notes) * 100)} size={88} strokeColor="var(--critical)" format={() => <span className="text-[15px] font-semibold">{thin.length}<span className="text-[11px] text-muted">/{s.notes}</span></span>} /><div className="text-[11px] text-muted mt-1">LTV 78% or more</div></div>
          </div>
          <div className="mt-3 space-y-1.5 text-[12px]">
            {s.balloons.map((b) => <div key={b.short} className="flex items-center justify-between gap-2"><span className="flex items-center gap-1.5"><Tag color="warning" className="!m-0 !text-[10.5px]">balloon</Tag>{b.short}</span><span className="tnum text-muted">{money(b.balance, true)} · {b.rate}% · due {b.due}</span></div>)}
          </div>
        </Section>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-3" title="Where the collateral ZIPs stand" sub="Change from each ZIP's peak (bar) and the base case to 2031 (dot). Click to open a ZIP.">
          <ResponsiveContainer width="100%" height={Math.max(300, d.zips.length * 30)}>
            <BarChart data={[...d.zips].sort((a, b) => b.drawdown - a.drawdown).map((z) => ({ zip: z.zip, name: `${z.zip} ${z.name}`, dd: Math.round(z.drawdown * 1000) / 10, base: Math.round(z.proj.base.cum * 1000) / 10 }))} layout="vertical" margin={{ left: 0, right: 40, top: 4, bottom: 4 }} barCategoryGap={4}>
              <CartesianGrid horizontal={false} stroke={GRID} />
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={(v) => v + "%"} />
              <YAxis type="category" dataKey="name" width={215} tick={{ ...AXIS, fontSize: 11, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} interval={0} />
              <Tooltip content={<Tip fmt={(v: number) => (v > 0 ? "+" : "") + v.toFixed(1) + "%"} />} cursor={{ fill: "var(--bg-sunken)" }} />
              <ReferenceLine x={0} stroke="var(--line-strong)" />
              <Bar dataKey="dd" name="From peak" radius={[4, 0, 0, 4]} isAnimationActive={false} onClick={(e: any) => pickZip(e?.payload?.zip)} cursor="pointer">{d.zips.map((_, i) => <Cell key={i} fill="var(--critical)" fillOpacity={0.7} />)}<LabelList dataKey="dd" position="left" formatter={(v: any) => v + "%"} style={{ fontSize: 10, fill: "var(--critical)" }} /></Bar>
              <Bar dataKey="base" name="Base to 2031" radius={[0, 4, 4, 0]} fill="var(--accent)" isAnimationActive={false} onClick={(e: any) => pickZip(e?.payload?.zip)} cursor="pointer"><LabelList dataKey="base" position="right" formatter={(v: any) => "+" + v + "%"} style={{ fontSize: 10, fill: "var(--accent)" }} /></Bar>
            </BarChart>
          </ResponsiveContainer>
        </Section>
        <Section className="lg:col-span-2" title="What to do">
          <Timeline items={[
            { color: "red", children: <span className="text-[12.5px]"><b>Fix the sheet.</b> 315 Howard St value; Summary tab totals.</span> },
            { color: "red", children: <span className="text-[12.5px]"><b>113 Crestline balloon</b>, $400K due 6 Mar 2027. Open the conversation now.</span> },
            { color: "orange", children: <span className="text-[12.5px]"><b>Work the 7 August arrears.</b> Start with 7947 Glennscott and the three Far West notes.</span> },
            { color: "orange", children: <span className="text-[12.5px]"><b>Broker opinions</b> on Range Field, Menlo Blvd, Rock Creek Run, Live Oak.</span> },
            { color: "green", children: <span className="text-[12.5px]"><b>Hold the paper.</b> 8% to 12% coupons would sell below par today.</span> },
            { color: "green", children: <span className="text-[12.5px]"><b>New notes at 70% LTV or less</b>, flat-value underwriting.</span> },
            { color: "gray", children: <span className="text-[12.5px]"><b>Re-run in April</b> when Zillow and FHFA refresh.</span> },
          ]} />
        </Section>
      </div>
    </div>
  );
}
