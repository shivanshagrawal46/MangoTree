"use client";

/* Five-year history: the index chart with toggles and zoom, a grid of ZIP
   sparklines (one tile per ZIP, sorted by the chosen metric), the from-peak
   comparison, and the official FHFA index. The table is behind a toggle. */
import * as React from "react";
import { Segmented, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Brush, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Pill } from "@/components/arv/bits";
import { AXIS, Delta, GRID, Section, Spark, Tip, fmtMonth, yearTick } from "./shared";
import { KIND_TONE, merge, money, pct, rebase, type Dataset, type ZipEntry } from "@/lib/notes";

const COLORS: Record<string, string> = { "San Antonio": "var(--accent)", Houston: "var(--info)", "Dallas-Fort Worth": "var(--high)", Texas: "var(--fg)", "United States": "var(--fg-faint)" };
type Metric = "chg5" | "yoy" | "drawdown" | "chg10";
const METRIC: Record<Metric, string> = { chg5: "5 years", yoy: "1 year", drawdown: "From peak", chg10: "10 years" };

export function History({ d, pickZip }: { d: Dataset; pickZip: (z: string) => void }) {
  const [mode, setMode] = React.useState<"index" | "dollars">("index");
  const [metric, setMetric] = React.useState<Metric>("chg5");
  const [show, setShow] = React.useState<"tiles" | "table">("tiles");
  const [on, setOn] = React.useState<Record<string, boolean>>({ "San Antonio": true, Houston: true, "Dallas-Fort Worth": true, Texas: true, "United States": true });
  const aggs = d.aggregates.filter((a) => a.kind !== "state" || a.label === "Texas");
  const rows = React.useMemo(() => merge(Object.fromEntries(aggs.map((a) => [a.label, mode === "index" ? rebase(a.series, "2021-08") : a.series])), "2016-01"), [aggs, mode]);
  const toggle = (e: any) => { const k = e.dataKey || e.value; setOn((s) => ({ ...s, [k]: !s[k] })); };
  const sorted = [...d.zips].sort((a, b) => b[metric] - a[metric]);
  const peakSA = d.aggregates.find((a) => a.label === "San Antonio")!;
  const cols: ColumnsType<ZipEntry> = [
    { title: "ZIP", dataIndex: "zip", width: 170, render: (z, r) => <button className="text-left" onClick={() => pickZip(z)}><span className="font-medium">{z}</span> <span className="text-muted">{r.name}</span></button> },
    ...[2016, 2019, 2021, 2022, 2024, 2025, 2026].map((y) => ({ title: `Aug ${y}`, key: y, align: "right" as const, width: 84, render: (_: any, r: ZipEntry) => <span className="tnum">{money(r.by_year[String(y)], true)}</span>, sorter: (a: ZipEntry, b: ZipEntry) => a.by_year[String(y)] - b.by_year[String(y)] })),
    { title: "5 yrs", key: "c5", align: "right", width: 76, render: (_, r) => <Delta v={r.chg5} />, sorter: (a, b) => a.chg5 - b.chg5, defaultSortOrder: "descend" },
    { title: "1 yr", key: "c1", align: "right", width: 72, render: (_, r) => <Delta v={r.yoy} />, sorter: (a, b) => a.yoy - b.yoy },
    { title: "From peak", key: "dd", align: "right", width: 90, render: (_, r) => <span><Delta v={r.drawdown} /> <span className="text-faint text-[11px]">{r.peak_date}</span></span>, sorter: (a, b) => a.drawdown - b.drawdown },
  ];

  return (
    <div className="space-y-4">
      <Section title="Texas and its metros against the country" sub="Zillow typical home value. Click a legend entry to hide it; drag the bar to zoom."
        right={<Segmented size="small" value={mode} onChange={(v) => setMode(v as any)} options={[{ label: "Aug 2021 = 100", value: "index" }, { label: "Dollars", value: "dollars" }]} />}>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={rows} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} minTickGap={0} />
            <YAxis tick={AXIS} axisLine={false} tickLine={false} width={56} domain={["auto", "auto"]} tickFormatter={(v) => (mode === "index" ? String(v) : money(v, true))} />
            <Tooltip content={<Tip fmt={(v: number) => (mode === "index" ? v.toFixed(1) : money(v))} title={fmtMonth} />} />
            <ReferenceLine x={peakSA.peak_date} stroke="var(--line-strong)" strokeDasharray="3 3" label={{ value: "TX peak", position: "insideTopLeft", fontSize: 10, fill: "var(--fg-faint)" }} />
            {aggs.map((a) => <Line key={a.label} type="monotone" dataKey={a.label} stroke={COLORS[a.label]} strokeWidth={a.label === "San Antonio" ? 2.5 : 1.6} dot={false} hide={!on[a.label]} strokeDasharray={a.label === "United States" ? "4 3" : undefined} isAnimationActive={false} connectNulls />)}
            <Legend wrapperStyle={{ fontSize: 11.5, cursor: "pointer" }} iconType="plainline" onClick={toggle} />
            <Brush dataKey="date" height={22} stroke="var(--line-strong)" fill="var(--bg-sunken)" travellerWidth={8} tickFormatter={(x: string) => x.slice(0, 4)} />
          </LineChart>
        </ResponsiveContainer>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3">
          {aggs.map((a) => (
            <button key={a.label} onClick={() => toggle({ dataKey: a.label })} className={`text-left rounded-xl border px-3 py-2 transition ${on[a.label] ? "border-line bg-sunken/40" : "border-dashed border-line opacity-60"}`}>
              <div className="text-[11px] uppercase tracking-wider text-muted font-medium flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-full" style={{ background: COLORS[a.label] }} />{a.label}</div>
              <div className="text-[15px] font-semibold tnum mt-0.5">{money(a.by_year["2026"])}</div>
              <div className="text-[11.5px] text-muted tnum">5y <Delta v={a.by_year["2026"] / a.by_year["2021"] - 1} /> · 1y <Delta v={a.by_year["2026"] / a.by_year["2025"] - 1} /> · peak <Delta v={a.drawdown} /></div>
            </button>
          ))}
        </div>
      </Section>

      <Section title="Every ZIP since 2015" sub="One tile per ZIP, sorted by the chosen number. Dotted line is fair value (2019 plus inflation), the dot is the peak. Click a tile to open it."
        right={<div className="flex items-center gap-2"><Segmented size="small" value={metric} onChange={(v) => setMetric(v as Metric)} options={Object.entries(METRIC).map(([v, l]) => ({ value: v, label: l }))} /><Segmented size="small" value={show} onChange={(v) => setShow(v as any)} options={[{ label: "Tiles", value: "tiles" }, { label: "Table", value: "table" }]} /></div>}>
        {show === "tiles" ? (
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
            {sorted.map((z) => (
              <button key={z.zip} onClick={() => pickZip(z.zip)} className="text-left rounded-xl border border-line px-3 pt-2.5 pb-2 hover:border-accent/60 hover:bg-accent-soft/20 transition min-w-0">
                <div className="flex items-center justify-between gap-2"><span className="text-[12.5px] font-medium truncate">{z.zip} <span className="text-muted font-normal">{z.name}</span></span><Delta v={z[metric]} className="text-[13px]" /></div>
                <div className="mt-1"><Spark pts={z.series.filter(([dt]) => dt >= "2015-01")} fair={z.fair} color={z[metric] >= 0 ? "var(--accent)" : "var(--critical)"} /></div>
                <div className="flex items-center justify-between mt-1 text-[11px] text-muted tnum"><span>{money(z.by_year["2026"], true)}</span><Pill tone={KIND_TONE[z.kind]}>{z.kind}</Pill>{z.notes.length ? <span>{z.notes.length} note{z.notes.length > 1 ? "s" : ""}</span> : <span />}</div>
              </button>
            ))}
          </div>
        ) : (
          <Table<ZipEntry> size="small" rowKey="zip" dataSource={d.zips} columns={cols} pagination={false} scroll={{ x: 1000 }} />
        )}
      </Section>

      <Section title="Official repeat-sales index" sub="FHFA all-transactions index, quarterly (includes refinance appraisals, so it lags). Texas still edging up; San Antonio flat since mid-2022.">
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={merge({ Texas: d.hpi.texas, "San Antonio": d.hpi.san_antonio })} margin={{ left: 0, right: 12, top: 8, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke={GRID} />
            <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} />
            <YAxis tick={AXIS} axisLine={false} tickLine={false} width={44} domain={["auto", "auto"]} />
            <Tooltip content={<Tip fmt={(v: number) => v.toFixed(1)} title={(l: string) => "Quarter from " + fmtMonth(l)} />} />
            <Line type="monotone" dataKey="Texas" stroke="var(--fg)" strokeWidth={1.6} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="San Antonio" stroke="var(--accent)" strokeWidth={2.2} dot={false} isAnimationActive={false} />
            <Legend wrapperStyle={{ fontSize: 11.5 }} iconType="plainline" />
          </LineChart>
        </ResponsiveContainer>
        <div className="flex gap-2 mt-1"><Tag className="!m-0">Texas Q2 2026 {pct(d.hpi.texas.at(-1)![1] / d.hpi.texas.at(-5)![1] - 1)} y/y</Tag><Tag className="!m-0">San Antonio {pct(d.hpi.san_antonio.at(-1)![1] / d.hpi.san_antonio.at(-5)![1] - 1)} y/y</Tag></div>
      </Section>
    </div>
  );
}
