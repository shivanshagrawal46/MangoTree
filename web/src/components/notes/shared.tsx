"use client";

/* Shared pieces for the Notes analysis screens, on Ant Design: section card,
   chart tooltip, axis styles, delta numbers, the verdict meter and the
   scenario range chart. Recharts on MangoTree's CSS variables so dark mode
   follows. */
import * as React from "react";
import { Card, Tag } from "antd";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from "recharts";
import { cn } from "@/lib/utils";
import { money, pct, type Scenario } from "@/lib/notes";

export const AXIS = { fontSize: 10.5, fill: "var(--fg-faint)" };
export const GRID = "var(--line)";

export function Tip({ active, payload, label, fmt, title }: any) {
  if (!active || !payload?.length) return null;
  const f = fmt || ((v: number) => money(v));
  return (
    <div className="rounded-xl border border-line bg-elev px-3 py-2 text-xs shadow-[var(--shadow)] min-w-[160px]">
      <div className="font-semibold mb-1">{title ? title(label) : label}</div>
      {payload.filter((p: any) => p.value !== null && p.value !== undefined).map((p: any) => (
        <div key={p.dataKey} className="flex justify-between gap-4 leading-5">
          <span className="text-muted flex items-center gap-1.5"><i className="inline-block w-2 h-2 rounded-full" style={{ background: p.stroke || p.fill || p.color }} />{p.name || p.dataKey}</span>
          <span className="tnum font-medium">{f(p.value, p)}</span>
        </div>
      ))}
    </div>
  );
}

export function Delta({ v, digits = 1, className }: { v?: number | null; digits?: number; className?: string }) {
  if (v === null || v === undefined || isNaN(v)) return <span className={cn("text-faint", className)}>—</span>;
  return <span className={cn("tnum font-medium", v > 0.0005 ? "text-good" : v < -0.0005 ? "text-critical" : "text-muted", className)}>{pct(v, true, digits)}</span>;
}

/* A section is an antd Card: title, one-line caption, controls on the right. */
export function Section({ title, sub, right, children, className, bodyClass }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string; bodyClass?: string }) {
  return (
    <Card variant="outlined" className={cn("!shadow-[var(--shadow-sm)]", className)} styles={{ body: { padding: 0 } }}>
      <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-2 flex-wrap">
        <div className="min-w-0"><h3 className="text-[14px] font-semibold tracking-tight">{title}</h3>{sub && <p className="text-[12px] text-muted mt-0.5 max-w-[80ch]">{sub}</p>}</div>
        {right && <div className="shrink-0">{right}</div>}
      </div>
      <div className={cn("px-5 pb-5", bodyClass)}>{children}</div>
    </Card>
  );
}

export const fmtMonth = (d: string) => { const [y, m] = d.split("-"); return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m) - 1] + " " + y; };
export const yearTick = (d: string) => (d.endsWith("-01") ? d.slice(0, 4) : "");

/* ------------------------------------------------------------ verdict meter */
export type Level = 1 | 2 | 3 | 4 | 5;
const LEVEL_COLOR = ["", "var(--critical)", "var(--high)", "var(--fg-faint)", "var(--accent)", "var(--good)"];
export function Meter({ level, labels }: { level: Level; labels: [string, string] }) {
  return (
    <div>
      <div className="flex gap-1">{[1, 2, 3, 4, 5].map((i) => <i key={i} className="h-2 flex-1 rounded-sm" style={{ background: i === level ? LEVEL_COLOR[level] : "var(--bg-sunken)", outline: i === level ? "none" : "1px solid var(--line)" }} />)}</div>
      <div className="flex justify-between text-[10px] text-faint mt-1"><span>{labels[0]}</span><span>{labels[1]}</span></div>
    </div>
  );
}
export function Verdict({ title, value, level, labels, sub, tone }: { title: string; value: React.ReactNode; level: Level; labels: [string, string]; sub?: React.ReactNode; tone?: string }) {
  return (
    <Card size="small" variant="outlined" className="!shadow-[var(--shadow-sm)]" styles={{ body: { padding: "12px 14px" } }}>
      <div className="flex items-center justify-between gap-2"><span className="text-[10.5px] uppercase tracking-wider text-muted font-medium">{title}</span>{tone && <Tag color={tone} className="!m-0 !text-[10.5px]">{labels[level >= 4 ? 1 : level <= 2 ? 0 : 1]}</Tag>}</div>
      <div className="text-[20px] font-semibold tnum tracking-tight mt-0.5">{value}</div>
      {sub && <div className="text-[11.5px] text-muted mb-2 leading-snug">{sub}</div>}
      <Meter level={level} labels={labels} />
    </Card>
  );
}

/* --------------------------------------------------- scenario range chart */
export type RangeRow = { name: string; bear: number; base: number; bull: number; expected?: number };
export function RangeChart({ rows, height, onClick }: { rows: RangeRow[]; height?: number; onClick?: (name: string) => void }) {
  const data = rows.map((r) => ({ name: r.name, range: [Math.round(r.bear * 1000) / 10, Math.round(r.bull * 1000) / 10], base: Math.round(r.base * 1000) / 10, bear: Math.round(r.bear * 1000) / 10, bull: Math.round(r.bull * 1000) / 10 }));
  return (
    <ResponsiveContainer width="100%" height={height || Math.max(160, rows.length * 34 + 40)}>
      <BarChart data={data} layout="vertical" margin={{ left: 4, right: 40, top: 4, bottom: 4 }} barCategoryGap={8}>
        <CartesianGrid horizontal={false} stroke={GRID} />
        <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} tickFormatter={(v) => v + "%"} domain={["auto", "auto"]} />
        <YAxis type="category" dataKey="name" width={150} tick={{ ...AXIS, fontSize: 11.5, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} interval={0} />
        <Tooltip cursor={{ fill: "var(--bg-sunken)" }} content={({ active, payload }: any) => active && payload?.length ? (
          <div className="rounded-xl border border-line bg-elev px-3 py-2 text-xs shadow-[var(--shadow)]"><div className="font-semibold mb-1">{payload[0].payload.name}</div>
            <div className="flex justify-between gap-4"><span className="text-critical">Bear</span><span className="tnum">{payload[0].payload.bear > 0 ? "+" : ""}{payload[0].payload.bear}%</span></div>
            <div className="flex justify-between gap-4"><span className="text-accent">Base</span><span className="tnum font-medium">{payload[0].payload.base > 0 ? "+" : ""}{payload[0].payload.base}%</span></div>
            <div className="flex justify-between gap-4"><span className="text-money-in">Bull</span><span className="tnum">{payload[0].payload.bull > 0 ? "+" : ""}{payload[0].payload.bull}%</span></div></div>) : null} />
        <ReferenceLine x={0} stroke="var(--line-strong)" />
        <Bar dataKey="range" radius={4} fill="var(--accent)" fillOpacity={0.22} isAnimationActive={false} onClick={(e: any) => onClick?.(e?.payload?.name)} cursor={onClick ? "pointer" : undefined}>
          <LabelList dataKey="bull" position="right" formatter={(v: any) => (Number(v) > 0 ? "+" : "") + Number(v).toFixed(0) + "%"} style={{ fontSize: 10.5, fill: "var(--money-in)" }} />
          <LabelList dataKey="bear" position="insideLeft" formatter={(v: any) => (Number(v) > 0 ? "+" : "") + Number(v).toFixed(0) + "%"} style={{ fontSize: 10.5, fill: "var(--critical)" }} offset={-34} />
        </Bar>
        <Bar dataKey="base" barSize={4} fill="var(--accent)" isAnimationActive={false} hide />
      </BarChart>
    </ResponsiveContainer>
  );
}
/* Base-case markers drawn as a scatter over the range chart need a shared axis; the simpler route used
   above hides the base bar and shows it in the tooltip, plus a dot column on the right: */
export function BaseDots({ rows, onClick }: { rows: RangeRow[]; onClick?: (name: string) => void }) {
  return (
    <div className="grid gap-[2px]" style={{ gridTemplateRows: `repeat(${rows.length}, 1fr)` }}>
      {rows.map((r) => (
        <button key={r.name} onClick={() => onClick?.(r.name)} className="flex items-center justify-between gap-3 text-[12px] px-2 rounded-lg hover:bg-sunken/60 min-h-[30px]">
          <span className="text-muted truncate">{r.name}</span>
          <span className="flex items-center gap-2 tnum shrink-0"><span className="text-critical">{pct(r.bear, true, 0)}</span><span className="font-semibold text-fg px-1.5 rounded-md bg-accent-soft text-accent">{pct(r.base, true, 0)}</span><span className="text-money-in">{pct(r.bull, true, 0)}</span></span>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------ sparkline (tiny) */
export function Spark({ pts, color = "var(--accent)", height = 36, fair }: { pts: [string, number][]; color?: string; height?: number; fair?: number }) {
  const vals = pts.map((p) => p[1]); const min = Math.min(...vals, fair || Infinity); const max = Math.max(...vals, fair || -Infinity);
  const w = 120, h = height; const x = (i: number) => (i / (pts.length - 1)) * w; const y = (v: number) => h - ((v - min) / (max - min || 1)) * (h - 4) - 2;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const pk = vals.indexOf(max);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" aria-hidden="true">
      {fair && <line x1={0} x2={w} y1={y(fair)} y2={y(fair)} stroke="var(--fg-faint)" strokeDasharray="2 3" strokeWidth={1} />}
      <path d={d} fill="none" stroke={color} strokeWidth={1.8} vectorEffect="non-scaling-stroke" />
      {pk >= 0 && pk < pts.length && <circle cx={x(pk)} cy={y(max)} r={2} fill="var(--fg-faint)" />}
      <circle cx={w} cy={y(vals[vals.length - 1])} r={2.4} fill={color} />
    </svg>
  );
}
