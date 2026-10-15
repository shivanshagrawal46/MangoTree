"use client";

/* Next five years: every ZIP ranked under the chosen scenario, the market
   ranges, the San Antonio and Texas fans, the model in five short rows, and
   the outside forecasters as numbers. */
import * as React from "react";
import { Descriptions, Segmented, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Pill } from "@/components/arv/bits";
import { AXIS, BaseDots, Delta, GRID, RangeChart, Section, Tip, fmtMonth, yearTick } from "./shared";
import { KIND_TONE, SCENARIO, money, type Aggregate, type Dataset, type Scenario, type ZipEntry } from "@/lib/notes";

const PROJ_DATES = ["2027-08", "2028-08", "2029-08", "2030-08", "2031-08"];
type Pick = Scenario | "expected";

function fan(a: Aggregate) {
  const rows: Record<string, any>[] = a.series.filter(([d]) => d >= "2019-01").map(([d, v]) => ({ date: d.slice(0, 7), value: v }));
  const last = rows[rows.length - 1]; last.base = last.value; last.bear = last.value; last.bull = last.value;
  PROJ_DATES.forEach((d, i) => rows.push({ date: d, base: a.proj!.base.values[i], bear: a.proj!.bear.values[i], bull: a.proj!.bull.values[i] }));
  return rows;
}
function Fan({ a, title }: { a: Aggregate; title: string }) {
  const rows = React.useMemo(() => fan(a), [a]);
  return (
    <div className="min-w-0">
      <div className="flex items-center justify-between text-[12.5px] mb-1"><span className="font-medium">{title}</span><span className="flex gap-1.5"><Tag color="error" className="!m-0 !text-[10.5px]">bear <Delta v={a.proj!.bear.cum} className="!text-[10.5px] !text-inherit" /></Tag><Tag color="success" className="!m-0 !text-[10.5px]">base <Delta v={a.proj!.base.cum} className="!text-[10.5px] !text-inherit" /></Tag><Tag color="processing" className="!m-0 !text-[10.5px]">bull <Delta v={a.proj!.bull.cum} className="!text-[10.5px] !text-inherit" /></Tag></span></div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={rows} margin={{ left: 0, right: 16, top: 10, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="date" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={yearTick} interval={0} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={56} domain={["auto", "auto"]} tickFormatter={(v) => money(v, true)} />
          <Tooltip content={<Tip title={fmtMonth} />} />
          <ReferenceLine x="2026-08" stroke="var(--line-strong)" label={{ value: "today", position: "insideTopRight", fontSize: 10, fill: "var(--fg-faint)" }} />
          {a.fair && <ReferenceLine y={a.fair} stroke="var(--fg-faint)" strokeDasharray="2 4" label={{ value: "fair", position: "insideBottomLeft", fontSize: 10, fill: "var(--fg-muted)" }} />}
          <Line type="monotone" dataKey="value" name="Zillow value" stroke="var(--fg)" strokeWidth={2} dot={false} isAnimationActive={false} />
          {(["bull", "base", "bear"] as Scenario[]).map((s) => <Line key={s} type="monotone" dataKey={s} name={SCENARIO[s].label} stroke={SCENARIO[s].color} strokeWidth={s === "base" ? 2.4 : 1.6} strokeDasharray={SCENARIO[s].dash} dot={{ r: 2.5 }} isAnimationActive={false} />)}
          <Legend wrapperStyle={{ fontSize: 11.5 }} iconType="plainline" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Outlook({ d, onPickZip }: { d: Dataset; onPickZip?: (zip: string) => void }) {
  const [pick, setPick] = React.useState<Pick>("base");
  const val = (z: ZipEntry) => (pick === "expected" ? z.expected : z.proj[pick].cum);
  const ranked = [...d.zips].sort((a, b) => val(b) - val(a)).map((z) => ({ zip: z.zip, name: `${z.zip} ${z.name}`, v: Math.round(val(z) * 1000) / 10, kind: z.kind }));
  const color = pick === "expected" ? "var(--fg)" : SCENARIO[pick].color;
  const get = (l: string) => d.aggregates.find((a) => a.label === l)!;
  const sa = get("San Antonio"), tx = get("Texas"), hou = get("Houston"), dfw = get("Dallas-Fort Worth");
  const ranges = [{ name: "Portfolio ZIPs", ...d.summary.outlook }, ...[sa, hou, dfw, tx].map((a) => ({ name: a.label, bear: a.proj!.bear.cum, base: a.proj!.base.cum, bull: a.proj!.bull.cum }))];
  const cols: ColumnsType<ZipEntry> = [
    { title: "ZIP", dataIndex: "zip", width: 180, render: (z, r) => <button className="text-left hover:underline" onClick={() => onPickZip?.(z)}><span className="font-medium">{z}</span> <span className="text-muted">{r.name}</span></button> },
    { title: "Kind", dataIndex: "kind", width: 104, render: (k) => <Pill tone={KIND_TONE[k as ZipEntry["kind"]]}>{k}</Pill>, filters: ["builder belt", "established", "premium", "inner city", "rural"].map((k) => ({ text: k, value: k })), onFilter: (v, r) => r.kind === v },
    { title: "Now", key: "now", align: "right", width: 80, render: (_, r) => <span className="tnum">{money(r.by_year["2026"], true)}</span> },
    { title: "Vs fair", key: "over", align: "right", width: 76, render: (_, r) => <Delta v={r.over} />, sorter: (a, b) => a.over - b.over },
    { title: "Zillow 12 mo", key: "z", align: "right", width: 96, render: (_, r) => <Delta v={r.zhvf["2027-08-31"] / 100} />, sorter: (a, b) => a.zhvf["2027-08-31"] - b.zhvf["2027-08-31"] },
    { title: "Bear", key: "be", align: "right", width: 72, render: (_, r) => <Delta v={r.proj.bear.cum} />, sorter: (a, b) => a.proj.bear.cum - b.proj.bear.cum },
    { title: "Base", key: "b", align: "right", width: 100, render: (_, r) => <span><Delta v={r.proj.base.cum} /> <span className="text-muted tnum text-[11px]">{money(r.proj.base.values[4], true)}</span></span>, sorter: (a, b) => a.proj.base.cum - b.proj.base.cum, defaultSortOrder: "descend" },
    { title: "Bull", key: "bu", align: "right", width: 72, render: (_, r) => <Delta v={r.proj.bull.cum} />, sorter: (a, b) => a.proj.bull.cum - b.proj.bull.cum },
    { title: "Expected", key: "e", align: "right", width: 86, render: (_, r) => <Delta v={r.expected} />, sorter: (a, b) => a.expected - b.expected },
    { title: "Notes", key: "n", align: "right", width: 90, render: (_, r) => r.notes.length ? <span className="tnum">{r.notes.length} · {money(r.balance, true)}</span> : <span className="text-faint">—</span>, sorter: (a, b) => a.balance - b.balance },
  ];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-3" title="Every ZIP ranked" sub="Change in typical value, August 2026 to August 2031. Click a bar to open the ZIP."
          right={<Segmented size="small" value={pick} onChange={(v) => setPick(v as Pick)} options={[{ label: "Base", value: "base" }, { label: "Bear", value: "bear" }, { label: "Bull", value: "bull" }, { label: "Expected", value: "expected" }]} />}>
          <ResponsiveContainer width="100%" height={Math.max(300, ranked.length * 28)}>
            <BarChart data={ranked} layout="vertical" margin={{ left: 0, right: 48, top: 4, bottom: 4 }} barCategoryGap={5}>
              <CartesianGrid horizontal={false} stroke={GRID} />
              <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={(v) => v + "%"} domain={["auto", "auto"]} />
              <YAxis type="category" dataKey="name" width={215} tick={{ ...AXIS, fontSize: 11.5, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} interval={0} />
              <Tooltip content={<Tip fmt={(v: number) => (v > 0 ? "+" : "") + v.toFixed(1) + "%"} />} cursor={{ fill: "var(--bg-sunken)" }} />
              <ReferenceLine x={0} stroke="var(--line-strong)" />
              <Bar dataKey="v" name={pick === "expected" ? "Expected" : SCENARIO[pick].label} radius={[0, 5, 5, 0]} onClick={(e: any) => onPickZip?.(e?.payload?.zip)} cursor="pointer" isAnimationActive={false}>
                {ranked.map((r, i) => <Cell key={i} fill={r.v >= 0 ? color : "var(--critical)"} fillOpacity={0.85} />)}
                <LabelList dataKey="v" position="right" formatter={(v: any) => (Number(v) > 0 ? "+" : "") + Number(v).toFixed(1) + "%"} style={{ fontSize: 11, fill: "var(--fg-muted)" }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Section>
        <div className="lg:col-span-2 space-y-4">
          <Section title="Markets, bear to bull" sub="Bar from bear to bull; base case on the right.">
            <RangeChart rows={ranges} height={200} />
            <BaseDots rows={ranges} />
          </Section>
          <Section title="How the numbers are made">
            <Descriptions size="small" column={1} colon={false} labelStyle={{ width: 92, fontSize: 11.5 }} contentStyle={{ fontSize: 12 }} items={[
              { key: "1", label: "Year 1", children: "Zillow's 12-month ZIP forecast, less 1.5 pts for the rate jump (bear −4, bull +0.5)." },
              { key: "2", label: "Years 2 to 5", children: "3% a year San Antonio and Houston, 2.5% Dallas (bear −3, 0, +2, +3; bull +4, +5, +4.5, +4)." },
              { key: "3", label: "Fair value", children: "2019 value + inflation. Above it loses up to 2 pts a year; below it gains up to 1." },
              { key: "4", label: "Supply", children: "0.5 to 1.5 pts a year off builder-belt ZIPs in years 2 and 3." },
              { key: "5", label: "Expected", children: "50% base · 30% bear · 20% bull." },
            ]} />
          </Section>
        </div>
      </div>

      <Section title="San Antonio and Texas, the three paths">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6"><Fan a={sa} title="San Antonio metro" /><Fan a={tx} title="Texas" /></div>
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Section className="lg:col-span-3" title="The table">
          <Table<ZipEntry> size="small" rowKey="zip" dataSource={d.zips} columns={cols} pagination={false} scroll={{ x: 960 }} />
        </Section>
        <Section className="lg:col-span-2" title="Outside forecasts, next 12 months">
          <Table size="small" pagination={false} rowKey="who" dataSource={[
            { who: "Zillow", when: "Sep 2026", what: "San Antonio", v: "−0.7%" }, { who: "Zillow", when: "Sep 2026", what: "Houston · Dallas", v: "−0.2% · −0.3%" }, { who: "Zillow", when: "Sep 2026", what: "United States", v: "+1.4%" },
            { who: "Texas A&M TRERC", when: "Sep 2026", what: "Texas median", v: "+1.8%" }, { who: "Reventure", when: "2025", what: "Texas (score 37/100)", v: "−4.0%" }, { who: "Reventure", when: "2025", what: "San Antonio score", v: "35/100" },
            { who: "Dallas Fed", when: "Aug 2026", what: "San Antonio median, y/y", v: "−1.4%" }, { who: "SABOR", when: "Aug 2026", what: "Months of supply", v: "5.9" },
          ]} columns={[{ title: "Who", dataIndex: "who", render: (v, r: any) => <span><span className="font-medium">{v}</span> <span className="text-[11px] text-muted">{r.when}</span></span> }, { title: "What", dataIndex: "what" }, { title: "", dataIndex: "v", align: "right", render: (v) => <span className={`tnum font-medium ${String(v).startsWith("−") ? "text-critical" : String(v).startsWith("+") ? "text-good" : ""}`}>{v}</span> }]} />
        </Section>
      </div>
    </div>
  );
}
