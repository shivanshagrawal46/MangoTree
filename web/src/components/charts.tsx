"use client";

import * as React from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { money } from "@/lib/utils";

const COST = ["funding", "construction"];
const BACK = ["payment", "payoff"];

function Tip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-line bg-elev px-3 py-2 text-xs shadow-[var(--shadow)]">
      <div className="font-semibold mb-1">{label}</div>
      {payload.filter((p: any) => p.value).map((p: any) => <div key={p.dataKey} className="flex justify-between gap-4"><span className="capitalize text-muted">{String(p.dataKey).replace("_", " ")}</span><span className="tnum font-medium">{money(p.value)}</span></div>)}
    </div>
  );
}

export function MoneyFlow({ series, height = 220 }: { series: any[]; height?: number }) {
  const data = series.map((s) => ({
    month: s.month,
    in: COST.reduce((a, k) => a + (s[k] || 0), 0),
    back: BACK.reduce((a, k) => a + (s[k] || 0), 0),
  }));
  if (!data.length) return <div className="h-[220px] grid place-items-center text-xs text-faint">No dated money events yet.</div>;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
        <defs>
          <linearGradient id="gIn" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--money-in)" stopOpacity={0.35} /><stop offset="100%" stopColor="var(--money-in)" stopOpacity={0} /></linearGradient>
          <linearGradient id="gBack" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--money-back)" stopOpacity={0.35} /><stop offset="100%" stopColor="var(--money-back)" stopOpacity={0} /></linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="var(--line)" />
        <XAxis dataKey="month" tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} tickFormatter={(v) => money(v, true)} width={54} />
        <Tooltip content={<Tip />} />
        <Area type="monotone" dataKey="in" name="Money out (funding, construction)" stroke="var(--money-in)" fill="url(#gIn)" strokeWidth={2} />
        <Area type="monotone" dataKey="back" name="Money back (payments, payoffs)" stroke="var(--money-back)" fill="url(#gBack)" strokeWidth={2} />
        <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function ByType({ byType, height = 200 }: { byType: Record<string, number>; height?: number }) {
  const data = Object.entries(byType).filter(([, v]) => v > 0).map(([k, v]) => ({ name: k.replace("_", " "), value: v, cost: COST.includes(k), back: BACK.includes(k) })).sort((a, b) => b.value - a.value);
  if (!data.length) return null;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16, top: 4, bottom: 4 }}>
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" width={92} tick={{ fontSize: 11, fill: "var(--fg-muted)" }} axisLine={false} tickLine={false} />
        <Tooltip content={<Tip />} cursor={{ fill: "var(--bg-sunken)" }} />
        <Bar dataKey="value" radius={[0, 6, 6, 0]}>{data.map((d, i) => <Cell key={i} fill={d.back ? "var(--money-back)" : d.cost ? "var(--money-in)" : "var(--fg-faint)"} />)}</Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function PortfolioBars({ rows, height = 260 }: { rows: { name: string; in: number; back: number }[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ left: 0, right: 8, top: 8, bottom: 0 }} barGap={2}>
        <CartesianGrid vertical={false} stroke="var(--line)" />
        <XAxis dataKey="name" tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} interval={0} angle={-20} textAnchor="end" height={48} />
        <YAxis tick={{ fontSize: 10, fill: "var(--fg-faint)" }} axisLine={false} tickLine={false} tickFormatter={(v) => money(v, true)} width={54} />
        <Tooltip content={<Tip />} cursor={{ fill: "var(--bg-sunken)" }} />
        <Bar dataKey="in" name="Out" fill="var(--money-in)" radius={[6, 6, 0, 0]} />
        <Bar dataKey="back" name="Back" fill="var(--money-back)" radius={[6, 6, 0, 0]} />
        <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Donut({ done, total, size = 64, label }: { done: number; total: number; size?: number; label?: string }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  const data = [{ v: done }, { v: Math.max(total - done, 0.0001) }];
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <PieChart width={size} height={size}>
        <Pie data={data} dataKey="v" innerRadius={size / 2 - 7} outerRadius={size / 2} startAngle={90} endAngle={-270} stroke="none">
          <Cell fill="var(--good)" /><Cell fill="var(--line)" />
        </Pie>
      </PieChart>
      <div className="absolute text-center leading-none"><div className="text-xs font-semibold tnum">{pct}%</div>{label && <div className="text-[9px] text-faint mt-0.5">{label}</div>}</div>
    </div>
  );
}
