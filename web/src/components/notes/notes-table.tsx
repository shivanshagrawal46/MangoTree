"use client";

/* Note by note: statistics, the LTV ladder (every note as a bar, coloured by
   payment status, with the stress slider moving all values), the sortable
   table with expandable rows, and the balance-against-LTV scatter. */
import * as React from "react";
import { Alert, Segmented, Slider, Statistic, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { Pill } from "@/components/arv/bits";
import { AXIS, Delta, GRID, Section } from "./shared";
import { KIND_TONE, money, pct, type Dataset, type NoteRow } from "@/lib/notes";

const STATUS_TONE = (n: NoteRow) => (n.behind >= 2 ? "critical" : n.behind === 1 ? "high" : "good") as "critical" | "high" | "good";
const STATUS_COLOR = (n: NoteRow) => (n.behind >= 2 ? "var(--critical)" : n.behind === 1 ? "var(--high)" : "var(--accent)");

export function NotesTable({ d, onPickZip }: { d: Dataset; onPickZip?: (zip: string) => void }) {
  const [stress, setStress] = React.useState(0);
  const [view, setView] = React.useState<"all" | "watch">("all");
  const zipOf = (z: string) => d.zips.find((e) => e.zip === z)!;
  const sltv = (n: NoteRow) => (n.est_value ? n.balance / (n.est_value * (1 + stress / 100)) : null);
  const rows = d.notes_table.filter((n) => view === "all" || n.behind > 0 || (n.ltv || 0) >= 0.78 || n.balloon_year <= 2030);
  const tb = d.summary.balance; const tv = d.summary.value * (1 + stress / 100);
  const over85 = d.notes_table.filter((n) => (sltv(n) || 0) > 0.85).length;
  const ladder = d.notes_table.filter((n) => !n.short.startsWith("315 Howard")).map((n) => ({ name: n.short, ltv: Math.round((sltv(n) || 0) * 100), n })).sort((a, b) => b.ltv - a.ltv);
  const cols: ColumnsType<NoteRow> = [
    { title: "Property", dataIndex: "short", width: 240, render: (v, r) => <div><div className="font-medium">{v}</div><button className="text-[11px] text-muted hover:underline" onClick={() => onPickZip?.(r.zip)}>{r.zip} · {r.area}</button></div>, sorter: (a, b) => a.short.localeCompare(b.short) },
    { title: "Rate", dataIndex: "rate", align: "right", width: 62, render: (v) => <span className="tnum">{v.toFixed(1)}%</span>, sorter: (a, b) => a.rate - b.rate },
    { title: "Balance", dataIndex: "balance", align: "right", width: 100, render: (v) => <span className="tnum">{money(v)}</span>, sorter: (a, b) => a.balance - b.balance, defaultSortOrder: "descend" },
    { title: "Est. value", dataIndex: "est_value", align: "right", width: 100, render: (v) => <span className="tnum">{money(v)}</span>, sorter: (a, b) => a.est_value - b.est_value },
    { title: stress ? `LTV at ${stress > 0 ? "+" : ""}${stress}%` : "LTV", key: "ltv", align: "right", width: 86, render: (_, r) => { const v = sltv(r); return <span className={`tnum font-medium ${v !== null && v > 1 ? "text-critical" : v !== null && v > 0.85 ? "text-high" : ""}`}>{pct(v, false, 0)}</span>; }, sorter: (a, b) => (sltv(a) || 0) - (sltv(b) || 0) },
    { title: "ZIP 1 yr", key: "zy", align: "right", width: 76, render: (_, r) => <Delta v={zipOf(r.zip).yoy} />, sorter: (a, b) => zipOf(a.zip).yoy - zipOf(b.zip).yoy },
    { title: "ZIP since note", key: "zs", align: "right", width: 100, render: (_, r) => <Delta v={r.zip_since_note} />, sorter: (a, b) => (a.zip_since_note || 0) - (b.zip_since_note || 0) },
    { title: "LTV 2031 base / bear", key: "l31", align: "right", width: 126, render: (_, r) => <span className="tnum">{pct(r.ltv_2031_base, false, 0)} / {pct(r.ltv_2031_bear, false, 0)}</span> },
    { title: "Payments", key: "st", width: 140, render: (_, r) => <Pill tone={STATUS_TONE(r)}>{r.status}</Pill>, filters: [{ text: "current", value: 0 }, { text: "1 behind", value: 1 }, { text: "2+ behind", value: 2 }], onFilter: (v, r) => (v === 2 ? r.behind >= 2 : r.behind === v) },
    { title: "Due in full", dataIndex: "due_in_full", align: "right", width: 96, render: (v, r) => <span className={`tnum ${r.balloon_year <= 2030 ? "text-high font-medium" : "text-muted"}`}>{v}</span>, sorter: (a, b) => a.balloon_year - b.balloon_year },
    { title: "Flags", dataIndex: "flags", render: (f: string[]) => f.length ? <div className="flex flex-wrap gap-1">{f.map((x) => <Tag key={x} className="!m-0 !text-[11px]" color={x.startsWith("balance") || x.startsWith("estimated") ? "error" : x.startsWith("balloon") ? "warning" : "default"}>{x}</Tag>)}</div> : <span className="text-faint">—</span> },
  ];
  const scatter = d.notes_table.filter((n) => n.ltv !== null && n.ltv < 1.5).map((n) => ({ x: Math.round((sltv(n) || 0) * 100), y: n.balance, z: n.rate, name: n.short, status: n.status, behind: n.behind }));
  const S = ({ title, value, sub, color }: any) => <div className="rounded-2xl border border-line bg-elev px-4 py-3"><Statistic title={title} value={value} valueStyle={{ fontSize: 22, color }} /><div className="text-[11px] text-muted leading-snug">{sub}</div></div>;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <S title="Notes" value={d.summary.notes} sub={money(d.summary.balance) + " outstanding"} />
        <S title="Monthly due" value={money(d.summary.payment)} sub={`weighted ${d.summary.war.toFixed(2)}%`} />
        <S title={stress ? `LTV at ${stress > 0 ? "+" : ""}${stress}%` : "Portfolio LTV"} value={pct(tb / tv, false, 0)} sub={`on ${money(tv, true)} of collateral`} color={tb / tv > 0.75 ? "var(--high)" : undefined} />
        <S title="Notes above 85%" value={over85} sub={stress ? `after a ${stress}% value move` : "at the sheet's values"} color={over85 > 3 ? "var(--critical)" : undefined} />
        <S title="August payment late" value={d.summary.late_n} sub={money(d.summary.late_balance, true) + " of balance"} color="var(--high)" />
        <S title="Balloons by 2030" value={d.summary.balloons.length} sub={money(d.summary.balloons.reduce((a, b) => a + b.balance, 0), true) + " · first " + d.summary.balloons[0]?.due} color="var(--high)" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-3" title="LTV ladder" sub="Every note's loan-to-value, coloured by payment status. Drag the slider to move all collateral values at once."
          right={<div className="flex items-center gap-2 text-[12px] text-muted w-[240px]">Values {stress > 0 ? "+" : ""}{stress}%<Slider id="notes-stress" className="flex-1 !m-0" value={stress} min={-30} max={20} step={5} onChange={setStress} marks={{ "-20": "-20", 0: "0", 10: "+10" }} /></div>}>
          <ResponsiveContainer width="100%" height={Math.max(300, ladder.length * 22)}>
            <BarChart data={ladder} layout="vertical" margin={{ left: 0, right: 40, top: 4, bottom: 4 }} barCategoryGap={4}>
              <CartesianGrid horizontal={false} stroke={GRID} />
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} domain={[0, 120]} tickFormatter={(v) => v + "%"} />
              <YAxis type="category" dataKey="name" width={150} tick={{ ...AXIS, fontSize: 11, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} interval={0} />
              <Tooltip cursor={{ fill: "var(--bg-sunken)" }} content={({ active, payload }: any) => active && payload?.length ? <div className="rounded-xl border border-line bg-elev px-3 py-2 text-xs shadow-[var(--shadow)]"><div className="font-semibold">{payload[0].payload.name}</div><div className="text-muted">LTV {payload[0].payload.ltv}% · {money(payload[0].payload.n.balance)} at {payload[0].payload.n.rate}% · {payload[0].payload.n.status}</div></div> : null} />
              <ReferenceLine x={85} stroke="var(--high)" strokeDasharray="4 3" label={{ value: "85%", position: "top", fontSize: 10, fill: "var(--high)" }} />
              <ReferenceLine x={100} stroke="var(--critical)" strokeDasharray="4 3" label={{ value: "100%", position: "top", fontSize: 10, fill: "var(--critical)" }} />
              <Bar dataKey="ltv" radius={[0, 4, 4, 0]} isAnimationActive={false} onClick={(e: any) => onPickZip?.(e?.payload?.n?.zip)} cursor="pointer">
                {ladder.map((r, i) => <Cell key={i} fill={STATUS_COLOR(r.n)} fillOpacity={r.ltv > 85 ? 0.95 : 0.7} />)}
                <LabelList dataKey="ltv" position="right" formatter={(v: any) => v + "%"} style={{ fontSize: 10.5, fill: "var(--fg-muted)" }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <div className="flex gap-3 text-[11px] text-muted mt-1"><span className="flex items-center gap-1"><i className="w-2.5 h-2.5 rounded-full bg-accent" />current</span><span className="flex items-center gap-1"><i className="w-2.5 h-2.5 rounded-full bg-high" />1 behind</span><span className="flex items-center gap-1"><i className="w-2.5 h-2.5 rounded-full bg-critical" />2+ behind</span><span>· 315 Howard St left out (value error)</span></div>
        </Section>
        <Section className="lg:col-span-2" title="Balance against LTV" sub="Dot size is the rate.">
          <ResponsiveContainer width="100%" height={300}>
            <ScatterChart margin={{ left: 0, right: 16, top: 12, bottom: 4 }}>
              <CartesianGrid stroke={GRID} />
              <XAxis type="number" dataKey="x" name="LTV" unit="%" tick={AXIS} axisLine={false} tickLine={false} domain={[0, 120]} />
              <YAxis type="number" dataKey="y" name="Balance" tick={AXIS} axisLine={false} tickLine={false} width={52} tickFormatter={(v) => money(v, true)} />
              <ZAxis type="number" dataKey="z" range={[40, 260]} name="Rate" />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} content={({ active, payload }: any) => active && payload?.length ? <div className="rounded-xl border border-line bg-elev px-3 py-2 text-xs shadow-[var(--shadow)]"><div className="font-semibold">{payload[0].payload.name}</div><div className="text-muted">LTV {payload[0].payload.x}% · {money(payload[0].payload.y)} · {payload[0].payload.z}% · {payload[0].payload.status}</div></div> : null} />
              <ReferenceLine x={85} stroke="var(--high)" strokeDasharray="4 3" />
              <ReferenceLine x={100} stroke="var(--critical)" strokeDasharray="4 3" />
              <Scatter data={scatter} isAnimationActive={false}>{scatter.map((s, i) => <Cell key={i} fill={s.behind >= 2 ? "var(--critical)" : s.behind === 1 ? "var(--high)" : "var(--accent)"} fillOpacity={0.8} />)}</Scatter>
            </ScatterChart>
          </ResponsiveContainer>
        </Section>
      </div>

      <Section title="Every note" right={<Segmented size="small" value={view} onChange={(v) => setView(v as any)} options={[{ label: `All ${d.notes_table.length}`, value: "all" }, { label: "Watch list", value: "watch" }]} />}>
        <Table<NoteRow> size="small" rowKey="address" dataSource={rows} columns={cols} pagination={false} scroll={{ x: 1250 }}
          rowClassName={(r) => (r.behind >= 2 ? "bg-critical-soft/40" : "")}
          expandable={{ expandedRowRender: (r) => {
            const z = zipOf(r.zip);
            return (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 text-[12.5px] py-1">
                <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 tnum"><span className="text-muted">Originated</span><span>{r.originated}</span><span className="text-muted">Original balance</span><span>{money(r.orig_balance)}</span><span className="text-muted">Sale price</span><span>{money(r.sales_price)}</span><span className="text-muted">LTV at origination</span><span>{pct(r.ltv_at_orig, false, 0)}</span><span className="text-muted">Payment</span><span>{money(r.payment)}</span><span className="text-muted">Interest paid to</span><span>{r.paid_to}</span><span className="text-muted">Last paid · next due</span><span>{r.last_paid} · {r.next_due}</span><span className="text-muted">YTD interest / principal</span><span>{money(r.ytd_interest)} / {money(r.ytd_principal)}</span>{r.note && <><span className="text-muted">Sheet note</span><span>{r.note}</span></>}</div>
                <div><div className="flex items-center gap-2 mb-1"><span className="font-medium">{r.zip} {z.name}</span><Pill tone={KIND_TONE[z.kind]}>{z.kind}</Pill></div>
                  <div className="flex flex-wrap gap-1.5"><Tag className="!m-0">value {money(z.by_year["2026"], true)}</Tag><Tag className="!m-0">1 yr <Delta v={z.yoy} /></Tag><Tag className="!m-0">peak <Delta v={z.drawdown} /></Tag><Tag className="!m-0">vs fair <Delta v={z.over} /></Tag><Tag className="!m-0">base 2031 <Delta v={z.proj.base.cum} /></Tag><Tag className="!m-0">bear <Delta v={z.proj.bear.cum} /></Tag>{z.gross_yield ? <Tag className="!m-0">rent yield {pct(z.gross_yield, false)}</Tag> : null}</div>
                  <p className="text-muted mt-2 leading-snug">{z.blurb}</p></div>
              </div>
            );
          } }} />
      </Section>
      <Alert type="warning" showIcon title="Fix the sheet before relying on the LTV column" description={<span className="text-[12.5px]">315 Howard St: $253,920 value against a $750,000 sale. Summary tab: 32 accounts and $7.26M; detail tab: 22 and $4.67M. No report date; payment status assumes end of August.</span>} />
    </div>
  );
}
