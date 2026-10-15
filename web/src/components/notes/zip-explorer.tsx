"use client";

/* Section 2: one ZIP at a time. Pick a ZIP: its history since 2015, the fair
   value marker, the three five-year paths, FHFA's annual changes as bars, the
   facts, and the notes secured there. A compare mode overlays up to five ZIPs
   rebased to August 2021. */
import * as React from "react";
import { Descriptions, Segmented, Select, Tag } from "antd";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Pill } from "@/components/arv/bits";
import { AXIS, Delta, GRID, Section, Tip, fmtMonth, yearTick } from "./shared";
import { KIND_TONE, SCENARIO, merge, money, pct, rebase, type Dataset, type Scenario, type ZipEntry } from "@/lib/notes";

const PROJ_DATES = ["2027-08", "2028-08", "2029-08", "2030-08", "2031-08"];
const PALETTE = ["var(--accent)", "var(--info)", "var(--high)", "var(--money-in)", "var(--critical)"];

function zipRows(z: ZipEntry, agg?: ZipEntry | null) {
  const rows: Record<string, any>[] = z.series.filter(([d]) => d >= "2015-01").map(([d, v]) => ({ date: d.slice(0, 7), value: v }));
  const last = rows[rows.length - 1];
  last.base = last.value; last.bear = last.value; last.bull = last.value;
  PROJ_DATES.forEach((d, i) => rows.push({ date: d, base: z.proj.base.values[i], bear: z.proj.bear.values[i], bull: z.proj.bull.values[i] }));
  return rows;
}

export function ZipExplorer({ d, initial }: { d: Dataset; initial?: string }) {
  const [mode, setMode] = React.useState<"one" | "compare">("one");
  const [zip, setZip] = React.useState(initial || d.zips[0].zip);
  const [cmp, setCmp] = React.useState<string[]>(["78245", "78230", "78132", "75189"].filter((z) => d.zips.some((e) => e.zip === z)));
  const z = d.zips.find((e) => e.zip === zip) || d.zips[0];
  const sa = d.aggregates.find((a) => a.label === "San Antonio")!;
  const rows = React.useMemo(() => zipRows(z), [z]);
  const fhfa = Object.entries(z.fhfa).map(([y, v]) => ({ year: y, fhfa: v, zillow: z.by_year[String(Number(y) + 1)] && z.by_year[y] ? (z.by_year[String(Number(y))] / z.by_year[String(Number(y) - 1)] - 1) * 100 : null }));
  const options = d.zips.map((e) => ({ value: e.zip, label: `${e.zip} · ${e.name}`, kind: e.kind }));
  const cmpRows = React.useMemo(() => merge(Object.fromEntries([...cmp.map((c) => { const e = d.zips.find((x) => x.zip === c)!; return [`${e.zip} ${e.name}`, rebase(e.series, "2021-08")]; }), ["San Antonio metro", rebase(sa.series, "2021-08")]]), "2016-01"), [cmp, d.zips, sa]);

  return (
    <div className="space-y-4">
      <Section title={mode === "one" ? `${z.zip} · ${z.name}` : "Compare ZIPs"} sub={mode === "one" ? z.blurb : "Up to five ZIPs, August 2021 = 100."}
        right={<div className="flex items-center gap-2 flex-wrap justify-end">
          <Segmented size="small" value={mode} onChange={(v) => setMode(v as any)} options={[{ label: "One ZIP", value: "one" }, { label: "Compare", value: "compare" }]} />
          {mode === "one"
            ? <Select id="notes-zip" size="middle" value={zip} onChange={setZip} options={options} showSearch optionFilterProp="label" style={{ width: 260 }} optionRender={(o) => <span className="flex items-center justify-between gap-2">{o.label}<Pill tone={KIND_TONE[(o.data as any).kind as ZipEntry["kind"]]}>{(o.data as any).kind}</Pill></span>} />
            : <Select id="notes-zip-cmp" mode="multiple" maxCount={5} size="middle" value={cmp} onChange={setCmp} options={options} showSearch optionFilterProp="label" style={{ minWidth: 360 }} placeholder="Pick ZIPs" />}
        </div>}>
        {mode === "one" ? (
          <>
            <div className="flex flex-wrap gap-2 mb-2 text-[12px]">
              <Pill tone={KIND_TONE[z.kind]}>{z.kind}</Pill><Tag className="!m-0">{z.county}</Tag><Tag className="!m-0">{z.metro} metro</Tag>
              {z.notes.length > 0 && <Tag color="green" className="!m-0">{z.notes.length} note{z.notes.length > 1 ? "s" : ""} · {money(z.balance, true)}</Tag>}
            </div>
            <ResponsiveContainer width="100%" height={330}>
              <LineChart data={rows} margin={{ left: 0, right: 16, top: 12, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke={GRID} />
                <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} />
                <YAxis tick={AXIS} axisLine={false} tickLine={false} width={56} domain={["auto", "auto"]} tickFormatter={(v) => money(v, true)} />
                <Tooltip content={<Tip title={fmtMonth} />} />
                <ReferenceLine y={z.fair} stroke="var(--fg-faint)" strokeDasharray="2 4" label={{ value: `fair value ${money(z.fair, true)}`, position: "insideBottomLeft", fontSize: 10, fill: "var(--fg-muted)" }} />
                <ReferenceLine x={z.peak_date} stroke="var(--line-strong)" strokeDasharray="3 3" label={{ value: `peak ${money(z.peak, true)}`, position: "insideTopLeft", fontSize: 10, fill: "var(--fg-faint)" }} />
                <ReferenceLine x="2026-08" stroke="var(--line-strong)" />
                <Line type="monotone" dataKey="value" name="Zillow value" stroke="var(--fg)" strokeWidth={2.2} dot={false} isAnimationActive={false} />
                {(["bull", "base", "bear"] as Scenario[]).map((s) => <Line key={s} type="monotone" dataKey={s} name={SCENARIO[s].label} stroke={SCENARIO[s].color} strokeWidth={s === "base" ? 2.4 : 1.6} strokeDasharray={SCENARIO[s].dash} dot={{ r: 2.5 }} isAnimationActive={false} connectNulls={false} />)}
                <Legend wrapperStyle={{ fontSize: 11.5 }} iconType="plainline" />
              </LineChart>
            </ResponsiveContainer>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-3">
              <div className="lg:col-span-2">
                <Descriptions size="small" column={{ xs: 1, sm: 2, md: 3 }} bordered items={[
                  { key: "v", label: "Value Aug 2026", children: <b className="tnum">{money(z.by_year["2026"])}</b> },
                  { key: "y", label: "1 year", children: <Delta v={z.yoy} /> },
                  { key: "5", label: "5 years", children: <Delta v={z.chg5} /> },
                  { key: "10", label: "10 years", children: <Delta v={z.chg10} /> },
                  { key: "p", label: `From peak (${z.peak_date})`, children: <Delta v={z.drawdown} /> },
                  { key: "f", label: "Vs fair value", children: <span><Delta v={z.over} /> <span className="text-muted">({money(z.fair, true)})</span></span> },
                  { key: "z", label: "Zillow 12-mo forecast", children: <Delta v={z.zhvf["2027-08-31"] / 100} /> },
                  { key: "fh", label: "FHFA 2025", children: z.fhfa["2025"] !== undefined ? <Delta v={z.fhfa["2025"] / 100} /> : <span className="text-faint">no index</span> },
                  { key: "r", label: "Rent · gross yield", children: z.rent ? <span className="tnum">{money(z.rent)}/mo · {pct(z.gross_yield, false)}</span> : <span className="text-faint">no rent index</span> },
                  { key: "b", label: "Base to 2031", children: <span><Delta v={z.proj.base.cum} /> <span className="text-muted">→ {money(z.proj.base.values[4], true)}</span></span> },
                  { key: "be", label: "Bear / Bull", children: <span><Delta v={z.proj.bear.cum} /> / <Delta v={z.proj.bull.cum} /></span> },
                  { key: "e", label: "Expected (50/30/20)", children: <Delta v={z.expected} /> },
                ]} />
                {z.notes.length > 0 && <div className="text-[12px] text-muted mt-2">Notes secured here: {z.notes.join(", ")}.</div>}
              </div>
              <div>
                <div className="text-[11px] uppercase tracking-wider text-muted font-medium mb-1">Annual change, FHFA vs Zillow</div>
                {fhfa.length ? (
                  <ResponsiveContainer width="100%" height={170}>
                    <BarChart data={fhfa} margin={{ left: 0, right: 4, top: 4, bottom: 0 }} barGap={2}>
                      <CartesianGrid vertical={false} stroke={GRID} />
                      <XAxis dataKey="year" tick={AXIS} axisLine={false} tickLine={false} />
                      <YAxis tick={AXIS} axisLine={false} tickLine={false} width={34} tickFormatter={(v) => v + "%"} />
                      <Tooltip content={<Tip fmt={(v: number) => v.toFixed(1) + "%"} />} cursor={{ fill: "var(--bg-sunken)" }} />
                      <ReferenceLine y={0} stroke="var(--line-strong)" />
                      <Bar dataKey="fhfa" name="FHFA (official)" radius={[3, 3, 0, 0]}>{fhfa.map((r, i) => <Cell key={i} fill={r.fhfa >= 0 ? "var(--accent)" : "var(--critical)"} />)}</Bar>
                      <Bar dataKey="zillow" name="Zillow (Aug to Aug)" radius={[3, 3, 0, 0]} fill="var(--fg-faint)" />
                    </BarChart>
                  </ResponsiveContainer>
                ) : <div className="h-[170px] grid place-items-center text-[12px] text-faint text-center px-4">FHFA publishes no index for this ZIP: too few mortgage sales.</div>}
              </div>
            </div>
          </>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={360}>
              <LineChart data={cmpRows} margin={{ left: 0, right: 16, top: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke={GRID} />
                <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} />
                <YAxis tick={AXIS} axisLine={false} tickLine={false} width={40} domain={["auto", "auto"]} />
                <Tooltip content={<Tip fmt={(v: number) => v.toFixed(1)} title={fmtMonth} />} />
                <ReferenceLine y={100} stroke="var(--line-strong)" />
                {cmp.map((c, i) => { const e = d.zips.find((x) => x.zip === c)!; return <Line key={c} type="monotone" dataKey={`${e.zip} ${e.name}`} stroke={PALETTE[i % PALETTE.length]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />; })}
                <Line type="monotone" dataKey="San Antonio metro" stroke="var(--fg-faint)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} connectNulls />
                <Legend wrapperStyle={{ fontSize: 11.5 }} iconType="plainline" />
              </LineChart>
            </ResponsiveContainer>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-3">
              {cmp.map((c, i) => { const e = d.zips.find((x) => x.zip === c)!; return (
                <button key={c} onClick={() => { setZip(c); setMode("one"); }} className="text-left rounded-xl border border-line px-3 py-2 hover:bg-sunken/60 transition">
                  <div className="text-[11px] uppercase tracking-wider text-muted font-medium flex items-center gap-1.5"><i className="w-2.5 h-2.5 rounded-full" style={{ background: PALETTE[i % PALETTE.length] }} />{e.zip}</div>
                  <div className="text-[13px] font-semibold">{e.name}</div>
                  <div className="text-[11.5px] text-muted tnum">5 yrs <Delta v={e.chg5} /> · peak <Delta v={e.drawdown} /> · base <Delta v={e.proj.base.cum} /></div>
                </button>
              ); })}
            </div>
          </>
        )}
      </Section>
    </div>
  );
}
