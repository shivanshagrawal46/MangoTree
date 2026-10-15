"use client";

/* Rates and loans: statistics, the mortgage and Fed chart with turning
   points, the forecast and investor-pricing tables, the payment calculator,
   and the refinance incentive per note against a rate the user sets. */
import * as React from "react";
import { InputNumber, Slider, Statistic, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Bar, BarChart, Brush, CartesianGrid, Cell, LabelList, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS, Delta, GRID, Section, Tip, fmtMonth, yearTick } from "./shared";
import { merge, money, pct, pmt, type Dataset, type NoteRow } from "@/lib/notes";

const MEANS = [["Coupons safe", "No refinancing while mortgages stay above 7%", "success"], ["Balloons", "$1.95M due by 2029; plan extensions now", "warning"], ["Arrears", "5 of 7 late notes sit in ZIPs down 8% to 19%", "error"], ["Note value", "Buyers want 10% to 12% yields: hold, do not sell", "processing"], ["New notes", "70% LTV cap, flat-value underwriting", "default"]];

export function Rates({ d }: { d: Dataset }) {
  const rows = React.useMemo(() => merge({ "30-year mortgage": d.rates.mortgage30, "Fed funds": d.rates.fed_funds }, "2016-01"), [d.rates]);
  const sa = d.aggregates.find((a) => a.label === "San Antonio")!;
  const [price, setPrice] = React.useState<number>(sa.by_year["2026"]);
  const [rate, setRate] = React.useState<number>(d.summary.mortgage_now);
  const [down, setDown] = React.useState<number>(20);
  const loan = price * (1 - down / 100); const pay = pmt(loan, rate / 100); const pay19 = pmt(sa.by_year["2019"] * 0.8, 0.0357);
  const [mkt, setMkt] = React.useState<number>(d.summary.mortgage_now);
  const fc = d.rates.forecasts.map((f) => ({ name: f.who, v: f.y2027, q4: f.q4_2026 }));
  const refi: ColumnsType<NoteRow> = [
    { title: "Note", dataIndex: "short", render: (v, r) => <span><span className="font-medium">{v}</span> <span className="text-muted text-[11px]">{r.zip}</span></span> },
    { title: "Rate", dataIndex: "rate", align: "right", width: 64, render: (v) => <span className="tnum">{v.toFixed(1)}%</span>, sorter: (a, b) => a.rate - b.rate },
    { title: "Balance", dataIndex: "balance", align: "right", width: 96, render: (v) => <span className="tnum">{money(v, true)}</span>, sorter: (a, b) => a.balance - b.balance },
    { title: "LTV", dataIndex: "ltv", align: "right", width: 60, render: (v) => <span className="tnum">{pct(v, false, 0)}</span>, sorter: (a, b) => (a.ltv || 0) - (b.ltv || 0) },
    { title: `Gap`, key: "gap", align: "right", width: 84, render: (_, r) => <span className={`tnum font-medium ${r.rate - mkt >= 2 ? "text-critical" : r.rate - mkt >= 1 ? "text-high" : "text-muted"}`}>{(r.rate - mkt > 0 ? "+" : "") + (r.rate - mkt).toFixed(2)}</span>, sorter: (a, b) => a.rate - b.rate, defaultSortOrder: "descend" },
    { title: "Saves / mo", key: "s", align: "right", width: 90, render: (_, r) => { const s = pmt(r.balance, r.rate / 100) - pmt(r.balance, mkt / 100); return <span className="tnum">{s > 0 ? money(s) : "—"}</span>; } },
    { title: "Leaves first?", key: "b", width: 110, render: (_, r) => { const k = (r.ltv || 0) <= 0.6 && r.behind === 0 && r.rate - mkt >= 2 ? ["likely", "error"] : (r.ltv || 0) > 0.8 || r.behind > 0 ? ["unlikely", "success"] : ["maybe", "warning"]; return <Tag color={k[1]} className="!m-0 !text-[10.5px]">{k[0]}</Tag>; } },
  ];
  const S = ({ title, value, suffix, sub, color }: any) => <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title={title} value={value} suffix={suffix} valueStyle={{ fontSize: 22, color }} /><div className="text-[11px] text-muted leading-snug">{sub}</div></div>;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <S title="30-year mortgage" value={d.summary.mortgage_now.toFixed(2)} suffix="%" color="var(--critical)" sub={<>a year ago {d.summary.mortgage_yr_ago}%</>} />
        <S title="Fed funds" value="3.75–4.00" suffix="%" sub="hiked 16 Sep; one more expected Dec" />
        <S title="Fannie Mae 2027" value="7.5" suffix="%" sub="MBA 7.1% to 7.3%" />
        <S title="Portfolio rate" value={d.summary.war.toFixed(2)} suffix="%" color="var(--accent)" sub={<>gap <Delta v={(d.summary.war - d.summary.mortgage_now) / 100} digits={2} /> to market</>} />
        <S title="DSCR rental loan" value="7–8" suffix="%" sub="hard money 9% to 13.5%" />
        <S title="Payment, typical SA home" value={money(pmt(sa.by_year["2026"] * 0.8, d.summary.mortgage_now / 100))} sub={<>{money(pay19)} in 2019</>} />
      </div>

      <Section title="Mortgage rate and the Fed, 2016 to this week" sub="Freddie Mac 30-year fixed and the effective Fed funds rate. Drag to zoom.">
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={rows} margin={{ left: 0, right: 16, top: 16, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} />
            <YAxis tick={AXIS} axisLine={false} tickLine={false} width={40} domain={[0, 8.5]} tickFormatter={(v) => v + "%"} />
            <Tooltip content={<Tip fmt={(v: number) => v.toFixed(2) + "%"} title={fmtMonth} />} />
            {d.rates.events.map(([date, label], i) => <ReferenceLine key={date} x={date} stroke="var(--line-strong)" strokeDasharray="3 3" label={{ value: label, position: "top", dy: i % 2 ? 14 : 0, fontSize: 10, fill: "var(--fg-muted)" }} />)}
            <Line type="monotone" dataKey="30-year mortgage" stroke="var(--critical)" strokeWidth={2.2} dot={false} isAnimationActive={false} connectNulls />
            <Line type="stepAfter" dataKey="Fed funds" stroke="var(--info)" strokeWidth={1.8} dot={false} isAnimationActive={false} connectNulls />
            <Legend wrapperStyle={{ fontSize: 11.5 }} iconType="plainline" />
            <Brush dataKey="date" height={22} stroke="var(--line-strong)" fill="var(--bg-sunken)" travellerWidth={8} tickFormatter={(v: string) => v.slice(0, 4)} />
          </LineChart>
        </ResponsiveContainer>
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Section title="Where the 30-year rate goes in 2027">
          <ResponsiveContainer width="100%" height={170}>
            <BarChart data={fc} layout="vertical" margin={{ left: 0, right: 40, top: 4, bottom: 4 }} barCategoryGap={8}>
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} domain={[5, 8]} tickFormatter={(v) => v + "%"} />
              <YAxis type="category" dataKey="name" width={110} tick={{ ...AXIS, fontSize: 11.5, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} interval={0} />
              <Tooltip content={<Tip fmt={(v: number) => v.toFixed(2) + "%"} />} cursor={{ fill: "var(--bg-sunken)" }} />
              <ReferenceLine x={d.summary.mortgage_now} stroke="var(--critical)" strokeDasharray="3 3" label={{ value: "now", position: "top", fontSize: 10, fill: "var(--critical)" }} />
              <Bar dataKey="v" name="2027" radius={[0, 4, 4, 0]} isAnimationActive={false}>{fc.map((f, i) => <Cell key={i} fill={f.name === "This analysis" ? "var(--accent)" : "var(--info)"} />)}<LabelList dataKey="v" position="right" formatter={(v: any) => Number(v).toFixed(1) + "%"} style={{ fontSize: 11, fill: "var(--fg-muted)" }} /></Bar>
            </BarChart>
          </ResponsiveContainer>
        </Section>
        <Section title="Payment calculator" sub="A buyer of the typical San Antonio home, against 2019 at 3.57%.">
          <div className="grid grid-cols-1 gap-2">
            <label className="text-[11.5px] text-muted">Price<InputNumber id="notes-price" size="small" className="!w-full" value={price} min={50000} max={3000000} step={5000} onChange={(v) => setPrice(Number(v) || 0)} formatter={(v) => `$${Number(v).toLocaleString()}`} parser={(v) => Number(String(v).replace(/[^0-9]/g, ""))} /></label>
            <label className="text-[11.5px] text-muted">Down {down}% · Rate {rate.toFixed(2)}%<Slider id="notes-rate" value={rate} min={3} max={12} step={0.125} onChange={setRate} marks={{ 3.57: "2019", 6: "6%", [d.summary.mortgage_now]: "now", 10: "10%" }} /></label>
            <Slider id="notes-down" value={down} min={0} max={50} step={5} onChange={setDown} tooltip={{ formatter: (v) => `${v}% down` }} />
          </div>
          <div className="grid grid-cols-3 gap-2 mt-2 text-center">
            <div className="rounded-xl bg-sunken/60 py-2"><div className="text-[10px] uppercase tracking-wider text-muted">A month</div><div className="text-[17px] font-semibold tnum">{money(pay)}</div></div>
            <div className="rounded-xl bg-sunken/60 py-2"><div className="text-[10px] uppercase tracking-wider text-muted">2019</div><div className="text-[17px] font-semibold tnum">{money(pay19)}</div></div>
            <div className="rounded-xl bg-sunken/60 py-2"><div className="text-[10px] uppercase tracking-wider text-muted">Change</div><div className="text-[17px] font-semibold"><Delta v={pay / pay19 - 1} digits={0} className="!text-[17px]" /></div></div>
          </div>
        </Section>
        <Section title="What it means for the loans">
          <div className="space-y-2">{MEANS.map(([k, v, c]) => <div key={k} className="flex items-start gap-2 text-[12.5px]"><Tag color={c} className="!m-0 !text-[11px] w-[96px] text-center shrink-0">{k}</Tag><span className="text-muted leading-snug">{v}</span></div>)}</div>
        </Section>
      </div>

      <Section title="Refinance incentive, note by note" sub="Gap between each note's rate and a market rate you set; who leaves first if rates fall."
        right={<div className="flex items-center gap-3 text-[12px] text-muted w-[260px]">Market {mkt.toFixed(2)}%<Slider id="notes-mkt" className="flex-1 !m-0" value={mkt} min={4} max={10} step={0.125} onChange={setMkt} /></div>}>
        <Table<NoteRow> size="small" rowKey="address" dataSource={d.notes_table} columns={refi} pagination={false} scroll={{ x: 700 }} />
      </Section>
    </div>
  );
}
