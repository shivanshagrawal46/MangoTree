"use client";

/* Small pieces shared by the ARV screens, built on Ant Design and themed in
   `app/(app)/arv/layout.tsx`: fields that commit on blur, a segmented control,
   check-tags, folded sections, the value range bar, the confidence dial, and
   the tab strip. The two drawings (RangeBar, Gauge) stay hand-made SVG. */
import * as React from "react";
import { Collapse, InputNumber, Segmented, Select, Tabs, Tag } from "antd";
import { cn, money } from "@/lib/utils";
import { GRADE_TONE, type Confidence } from "@/lib/arv";

/* ---------------------------------------------------------------- tones */
export const TONE_COLOR: Record<string, string> = { neutral: "default", accent: "green", critical: "volcano", high: "gold", good: "green", info: "geekblue" };

/* A pill tag in MangoTree's tones. */
export function Pill({ tone = "neutral", children, className, title }: { tone?: "neutral" | "accent" | "critical" | "high" | "good" | "info"; children: React.ReactNode; className?: string; title?: string }) {
  return <Tag color={TONE_COLOR[tone]} variant="filled" className={cn("!m-0 !rounded-full !text-[11px] !font-medium !inline-flex !items-center !gap-1 !leading-[18px]", className)} title={title}>{children}</Tag>;
}

/* ARV on a line: the engine's range as a band, the point value as a marker,
   the analyst's opinion and any AVM as thin ticks so disagreement is visible. */
export function RangeBar({ low, high, value, marks, compLow, compHigh, className }: {
  low?: number | null; high?: number | null; value?: number | null; compLow?: number | null; compHigh?: number | null;
  marks?: { label: string; value?: number | null; tone?: string }[]; className?: string;
}) {
  const pts = [low, high, value, compLow, compHigh, ...(marks || []).map((m) => m.value)].filter((x): x is number => typeof x === "number" && !isNaN(x));
  if (!pts.length) return null;
  const min = Math.min(...pts), max = Math.max(...pts);
  const span = Math.max(max - min, 1);
  const pad = span * 0.08;
  const x = (v: number) => ((v - (min - pad)) / (span + 2 * pad)) * 100;
  return (
    <div className={cn("relative h-14", className)}>
      {typeof compLow === "number" && typeof compHigh === "number" && (
        <div className="absolute top-[22px] h-1 rounded-full bg-line" style={{ left: `${x(compLow)}%`, width: `${x(compHigh) - x(compLow)}%` }} title={`Adjusted comps ${money(compLow)} – ${money(compHigh)}`} />
      )}
      {typeof low === "number" && typeof high === "number" && (
        <div className="absolute top-[18px] h-3 rounded-full bg-accent/25 border border-accent/40" style={{ left: `${x(low)}%`, width: `${Math.max(x(high) - x(low), 0.8)}%` }} />
      )}
      {typeof value === "number" && (
        <div className="absolute top-[12px] -translate-x-1/2 flex flex-col items-center" style={{ left: `${x(value)}%` }}>
          <div className="h-6 w-[3px] rounded bg-accent" />
          <div className="text-[10px] font-semibold tnum text-accent mt-0.5 whitespace-nowrap">{money(value, true)}</div>
        </div>
      )}
      {clusterMarks((marks || []).filter((m) => typeof m.value === "number") as { label: string; value: number; tone?: string }[], x).map((g, i) => (
        <React.Fragment key={i}>
          {g.marks.map((m, j) => (
            <div key={j} className="absolute top-[18px] -translate-x-1/2" style={{ left: `${x(m.value)}%` }} title={`${m.label} ${money(m.value)}`}>
              <div className={cn("h-3 w-[2px] rounded", m.tone ? m.tone.replace("text-", "bg-") : "bg-faint")} />
            </div>
          ))}
          <div className={cn("absolute top-[6px] -translate-x-1/2 text-[9px] uppercase tracking-wide whitespace-nowrap", g.marks.length === 1 ? g.marks[0].tone || "text-muted" : "text-muted")} style={{ left: `${g.x}%` }}
            title={g.marks.map((m) => `${m.label} ${money(m.value)}`).join(" · ")}>
            {g.marks.map((m, j) => <span key={j} className={m.tone}>{j > 0 ? " · " : ""}{m.label}</span>)}
          </div>
        </React.Fragment>
      ))}
      {typeof low === "number" && <div className="absolute top-[34px] text-[10px] tnum text-muted -translate-x-1/2" style={{ left: `${x(low)}%` }}>{money(low, true)}</div>}
      {typeof high === "number" && <div className="absolute top-[34px] text-[10px] tnum text-muted -translate-x-1/2" style={{ left: `${x(high)}%` }}>{money(high, true)}</div>}
    </div>
  );
}

function clusterMarks(marks: { label: string; value: number; tone?: string }[], x: (v: number) => number): { x: number; marks: { label: string; value: number; tone?: string }[] }[] {
  const sorted = [...marks].sort((a, b) => a.value - b.value);
  const groups: { x: number; marks: typeof marks }[] = [];
  for (const m of sorted) {
    const last = groups[groups.length - 1];
    if (last && Math.abs(x(m.value) - x(last.marks[last.marks.length - 1].value)) < 9) { last.marks.push(m); last.x = last.marks.reduce((a, k) => a + x(k.value), 0) / last.marks.length; }
    else groups.push({ x: x(m.value), marks: [m] });
  }
  return groups;
}

/* A half dial, 0-100, coloured by grade. */
export function Gauge({ conf, size = 120 }: { conf?: Confidence | null; size?: number }) {
  const score = Math.max(0, Math.min(100, conf?.score ?? 0));
  const r = size / 2 - 8, cx = size / 2, cy = size / 2;
  const arc = (from: number, to: number) => {
    const a0 = Math.PI * (1 - from / 100), a1 = Math.PI * (1 - to / 100);
    const x0 = cx + r * Math.cos(a0), y0 = cy - r * Math.sin(a0), x1 = cx + r * Math.cos(a1), y1 = cy - r * Math.sin(a1);
    return `M ${x0} ${y0} A ${r} ${r} 0 0 1 ${x1} ${y1}`;
  };
  const tone = { A: "var(--good)", B: "var(--accent)", C: "var(--high)", D: "var(--critical)", E: "var(--critical)", F: "var(--critical)" }[conf?.grade || "F"] || "var(--fg-faint)";
  return (
    <div className="relative" style={{ width: size, height: size / 2 + 22 }}>
      <svg width={size} height={size / 2 + 8} viewBox={`0 0 ${size} ${size / 2 + 8}`}>
        <path d={arc(0, 100)} stroke="var(--line)" strokeWidth={10} fill="none" strokeLinecap="round" />
        {score > 0 && <path d={arc(0, score)} stroke={tone} strokeWidth={10} fill="none" strokeLinecap="round" />}
      </svg>
      <div className="absolute inset-x-0 top-[26%] text-center">
        <div className="text-2xl font-semibold tracking-tight tnum leading-none">{conf?.grade || "—"}</div>
        <div className="text-[10px] text-muted mt-0.5">{conf ? `${conf.score}/100 · ±${conf.band_pct ?? "—"}%` : "no value yet"}</div>
      </div>
    </div>
  );
}

export function GradeBadge({ conf }: { conf?: Confidence | null }) {
  if (!conf) return <Pill>no value</Pill>;
  return <Pill tone={GRADE_TONE[conf.grade] || "neutral"}>{conf.label || conf.grade} · {conf.score}</Pill>;
}

/* A card section heading with an optional action on the right. */
export function H({ children, right, className }: { children: React.ReactNode; right?: React.ReactNode; className?: string }) {
  return <div className={cn("flex items-center justify-between gap-3 mb-2.5", className)}><div className="text-[13px] font-semibold tracking-tight text-fg">{children}</div>{right}</div>;
}

/* A section that opens on demand: a strong one-line header, a short preview when closed. */
export function Disclosure({ title, preview, badge, defaultOpen, children, className }: { title: React.ReactNode; preview?: React.ReactNode; badge?: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode; className?: string }) {
  const [open, setOpen] = React.useState(!!defaultOpen);
  return (
    <Collapse ghost activeKey={open ? ["1"] : []} onChange={(k) => setOpen((Array.isArray(k) ? k : [k]).length > 0)} className={cn("!rounded-xl !border !border-line !bg-elev", className)}
      items={[{ key: "1", label: (
        <span className="flex items-center gap-3 min-w-0">
          <span className="text-[13px] font-semibold tracking-tight truncate">{title}</span>
          {badge}
          {!open && preview && <span className="hidden md:block text-[11.5px] text-muted truncate max-w-[55%] ml-auto">{preview}</span>}
        </span>
      ), children }]} />
  );
}

/* A number field that keeps its own text while typing and commits on blur or
   Enter — so a server recompute never fights the keyboard. */
export function NumField({ label, value, onCommit, suffix, prefix, step, min, max, placeholder, hint, className, compact }: {
  label?: string; value?: number | null; onCommit: (v: number | null) => void; suffix?: string; prefix?: string; step?: number; min?: number; max?: number;
  placeholder?: string; hint?: string; className?: string; compact?: boolean;
}) {
  const [draft, setDraft] = React.useState<number | null>(value ?? null);
  const [focus, setFocus] = React.useState(false);
  const [seen, setSeen] = React.useState(value);
  if (value !== seen) { setSeen(value); if (!focus) setDraft(value ?? null); }
  const commit = () => { const v = draft ?? null; if (v !== (value ?? null)) onCommit(v); };
  return (
    <label className={cn("block min-w-0", className)}>
      {label && <span className={cn("block font-medium text-fg/85 mb-1", compact ? "text-[11px]" : "text-[12.5px]")}>{label}</span>}
      <InputNumber value={draft} onChange={(v) => setDraft(v === null || v === undefined ? null : Number(v))} onFocus={() => setFocus(true)} onBlur={() => { setFocus(false); commit(); }}
        onPressEnter={(e) => (e.target as HTMLInputElement).blur()} prefix={prefix} suffix={suffix} step={step} min={min} max={max} placeholder={placeholder} controls={false}
        size={compact ? "small" : "middle"} className={cn("!w-full tnum", compact && "arv-compact")} formatter={(v) => (v === undefined || v === null || String(v) === "" ? "" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 4 }))}
        parser={(t) => (t ? Number(String(t).replace(/[,$%\s]/g, "")) : (null as any))} />
      {hint && <span className="block text-[10px] text-muted mt-0.5">{hint}</span>}
    </label>
  );
}

export function SelectField({ label, value, onChange, options, className, compact }: { label?: string; value: string; onChange: (v: string) => void; options: [string, string][]; className?: string; compact?: boolean }) {
  return (
    <label className={cn("block min-w-0", className)}>
      {label && <span className={cn("block font-medium text-fg/85 mb-1", compact ? "text-[11px]" : "text-[12.5px]")}>{label}</span>}
      <Select value={value} onChange={(v) => onChange(v)} options={options.map(([v, l]) => ({ value: v, label: l }))} size={compact ? "small" : "middle"} className="!w-full" popupMatchSelectWidth={false} />
    </label>
  );
}

export function Row({ label, value, sub, strong, tone }: { label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode; strong?: boolean; tone?: string }) {
  return (
    <div className={cn("flex items-baseline justify-between gap-3 py-1.5 border-b border-line/60 last:border-0", strong && "font-semibold")}>
      <div className="min-w-0"><div className="text-[12.5px]">{label}</div>{sub && <div className="text-[10.5px] text-muted">{sub}</div>}</div>
      <div className={cn("tnum text-[12.5px] whitespace-nowrap", tone)}>{value}</div>
    </div>
  );
}

export const CONDITION_OPTIONS: [string, string][] = [["C1", "C1 · New"], ["C2", "C2 · Renovated to the studs, like new"], ["C3", "C3 · Well kept, mostly updated"],
  ["C4", "C4 · Dated but functional"], ["C5", "C5 · Deferred maintenance, needs work"], ["C6", "C6 · Not habitable"]];

/* A toggle chip: selected or not. Used for known issues, planned work, prefill offers. */
export function Chip({ on, onClick, children, tone = "accent", className, title }: { on?: boolean; onClick?: () => void; children: React.ReactNode; tone?: "accent" | "critical" | "high" | "good"; className?: string; title?: string }) {
  const onCls = { accent: "!border-accent !bg-accent-soft !text-accent", critical: "!border-critical !bg-critical-soft !text-critical", high: "!border-high !bg-high-soft !text-high", good: "!border-good !bg-good-soft !text-good" }[tone];
  return (
    <span title={title} className="inline-flex">
      <Tag.CheckableTag checked={!!on} onChange={() => onClick?.()} className={cn("!m-0 !h-8 !px-3 !rounded-full !border !text-[12px] !font-medium !inline-flex !items-center !gap-1.5 !leading-[30px] transition-all", on ? onCls : "!border-line !bg-elev !text-muted hover:!border-line-strong hover:!text-fg", className)}>
        {children}
      </Tag.CheckableTag>
    </span>
  );
}

/* A segmented control for short exclusive choices (2-5 options). */
export function Seg<T extends string>({ value, onChange, options, className, size = "md" }: { value: T; onChange: (v: T) => void; options: [T, React.ReactNode][]; className?: string; size?: "sm" | "md" }) {
  return <Segmented value={value} onChange={(v) => onChange(v as T)} options={options.map(([v, l]) => ({ value: v, label: l }))} size={size === "sm" ? "small" : "middle"} className={className} />;
}

/* A labelled slot for any control, with an optional source tag (document / records). */
export function Field({ label, children, hint, source, className }: { label: React.ReactNode; children: React.ReactNode; hint?: React.ReactNode; source?: string | null; className?: string }) {
  return (
    <div className={cn("min-w-0", className)}>
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-[12.5px] font-medium text-fg/85">{label}</span>
        {source && <span className="text-[10px] text-accent truncate max-w-[55%]" title={`From ${source}`}>from {source}</span>}
      </div>
      {children}
      {hint && <div className="text-[10.5px] text-muted mt-0.5">{hint}</div>}
    </div>
  );
}

/* A thin progress meter, 0-1, with a label on the right. */
export function Meter({ value, label, tone = "bg-accent", className }: { value: number; label?: React.ReactNode; tone?: string; className?: string }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 flex-1 rounded-full bg-sunken overflow-hidden"><div className={cn("h-full rounded-full transition-all duration-500", tone)} style={{ width: `${pct}%` }} /></div>
      {label !== undefined && <div className="text-[11px] tnum text-muted shrink-0">{label}</div>}
    </div>
  );
}

/* A compact stat tile: label, big number, one line under it. */
export function Tile({ label, value, sub, tone, className, onClick }: { label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode; tone?: string; className?: string; onClick?: () => void }) {
  const Comp: any = onClick ? "button" : "div";
  return (
    <Comp onClick={onClick} className={cn("rounded-xl border border-line bg-elev p-3 text-left min-w-0", onClick && "hover:border-line-strong transition", className)}>
      <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">{label}</div>
      <div className={cn("text-[19px] font-semibold tracking-tight tnum mt-0.5 leading-tight", tone)}>{value}</div>
      {sub && <div className="text-[11px] text-muted mt-0.5 truncate">{sub}</div>}
    </Comp>
  );
}

/* A pass / fail / unknown check line. */
export function CheckLine({ ok, children, detail }: { ok: boolean | null; children: React.ReactNode; detail?: React.ReactNode }) {
  return (
    <li className="flex items-start gap-2 text-[12.5px]">
      <span className={cn("mt-[3px] h-4 w-4 rounded-full grid place-items-center shrink-0 text-[10px] font-bold", ok === true ? "bg-good-soft text-good" : ok === false ? "bg-critical-soft text-critical" : "bg-sunken text-faint")}>{ok === true ? "✓" : ok === false ? "!" : "·"}</span>
      <span className="min-w-0"><span>{children}</span>{detail && <span className="text-muted"> {detail}</span>}</span>
    </li>
  );
}

/* The tab strip under the hero: antd Tabs with an icon and a count per tab. */
export function TabStrip({ value, onChange, tabs, className }: { value: string; onChange: (v: string) => void; className?: string;
  tabs: { key: string; label: string; icon?: React.ReactNode; count?: number | string | null; busy?: boolean }[] }) {
  return (
    <Tabs activeKey={value} onChange={onChange} className={cn("arv-tabs", className)} size="middle" tabBarStyle={{ marginBottom: 0 }}
      items={tabs.map((t) => ({ key: t.key, label: (
        <span className="inline-flex items-center gap-1.5">
          {t.icon && <span className={cn(t.key === value ? "text-accent" : "text-faint")}>{t.icon}</span>}
          {t.label}
          {t.count !== undefined && t.count !== null && <span className={cn("h-5 min-w-[20px] px-1.5 rounded-full grid place-items-center text-[10.5px] font-semibold tnum", t.key === value ? "bg-accent-soft text-accent" : "bg-sunken text-muted")}>{t.count}</span>}
          {t.busy && <span className="h-3 w-3 rounded-full border-2 border-accent border-t-transparent animate-spin" />}
        </span>
      ) }))} />
  );
}

/* Two or three values on one scale, each with its range as a band and its point as a tick. */
export function Compare({ rows, className }: { rows: { label: string; value?: number | null; low?: number | null; high?: number | null; tone: string; sub?: string }[]; className?: string }) {
  const nums = rows.flatMap((r) => [r.value, r.low, r.high]).filter((x): x is number => typeof x === "number" && !isNaN(x));
  if (!nums.length) return null;
  const min = Math.min(...nums), max = Math.max(...nums), span = Math.max(max - min, 1), pad = span * 0.06;
  const x = (v: number) => ((v - (min - pad)) / (span + 2 * pad)) * 100;
  return (
    <div className={cn("space-y-2.5", className)}>
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[64px_1fr_auto] items-center gap-3">
          <div className="text-[11px] font-medium text-muted uppercase tracking-wide">{r.label}</div>
          <div className="relative h-2.5">
            <div className="absolute inset-y-0 left-0 right-0 rounded-full bg-sunken" />
            {typeof r.low === "number" && typeof r.high === "number" && <div className={cn("absolute inset-y-0 rounded-full opacity-30", r.tone)} style={{ left: `${x(r.low)}%`, width: `${Math.max(x(r.high) - x(r.low), 1)}%` }} />}
            {typeof r.value === "number" && <div className={cn("absolute top-[-3px] h-4 w-[3px] rounded -translate-x-1/2", r.tone)} style={{ left: `${x(r.value)}%` }} />}
          </div>
          <div className="text-right"><div className="text-[13px] font-semibold tnum leading-tight">{money(r.value, true)}</div>{(typeof r.low === "number" || r.sub) && <div className="text-[10.5px] text-muted tnum leading-tight">{r.sub || `${money(r.low, true)} – ${money(r.high, true)}`}</div>}</div>
        </div>
      ))}
    </div>
  );
}

/* A one-line key/value inside a hero segment. */
export function Mini({ k, v, tone }: { k: React.ReactNode; v: React.ReactNode; tone?: string }) {
  return <div className="flex items-baseline justify-between gap-3 text-[11.5px]"><span className="text-muted truncate">{k}</span><span className={cn("tnum font-medium shrink-0", tone)}>{v}</span></div>;
}

/* "3 more" as a quiet pill. */
export function MorePill({ n, open, onClick }: { n: number; open: boolean; onClick: () => void }) {
  return <button onClick={onClick} className="mt-2 inline-flex h-6 px-2.5 items-center rounded-full border border-line text-[11px] text-muted hover:border-accent hover:text-accent transition">{open ? "fewer" : `${n} more`}</button>;
}

export function fileSize(n?: number) { if (!n) return "—"; if (n < 1024) return `${n} B`; if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`; return `${(n / 1024 / 1024).toFixed(1)} MB`; }
