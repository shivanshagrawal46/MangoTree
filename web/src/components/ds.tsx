"use client";

/* The shared parts of the app's look (2026-11-26). The rules, taken from how the best
   admin software is built and kept with MangoTree's own colours:

     – one text size, 13px; 12px for a secondary line, 11px semibold for a label;
       16 / 20px for a title. Importance is weight and a lighter grey, not size or colour;
     – every gap is a multiple of 4px; a row and a control are 32px (24px small); a bar is 48px;
     – flat: white panes on the grey frame, 1px lines, corners of 4 or 8, no shadow on a page;
     – the green is the main action and the chosen item, nothing else; one main button a page;
     – a list is a table, and a row opens its own page.

   A screen is built from these parts and does not set sizes of its own. */

import * as React from "react";
import Link from "next/link";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------ the page's top bar */
export type Crumb = { label: React.ReactNode; href?: string };
/** 48px, stays on top while the page scrolls: where you are on the left, what you can do on the right. */
export function PageHead({ icon, crumbs, children }: { icon?: React.ReactNode; crumbs: Crumb[]; children?: React.ReactNode }) {
  return (
    <div className="sticky top-0 z-20 h-12 shrink-0 flex items-center gap-2 pl-4 pr-3 border-b border-line bg-elev">
      <div className="min-w-0 flex items-center gap-2 text-[13px] font-medium whitespace-nowrap">
        {icon && <span className="text-faint shrink-0">{icon}</span>}
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <React.Fragment key={i}>
              {i > 0 && <span className="text-faint/60">/</span>}
              {c.href && !last ? <Link href={c.href} className="text-faint hover:text-fg truncate">{c.label}</Link> : <span className={cn("truncate", last ? "text-fg" : "text-faint")}>{c.label}</span>}
            </React.Fragment>
          );
        })}
      </div>
      <div className="flex-1" />
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------------ button */
type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "outline" | "ghost" | "accent" | "danger"; size?: "md" | "sm" };
/** 32px (24px small). Outline is the usual one; accent is the page's one main action. */
export const Btn = React.forwardRef<HTMLButtonElement, BtnProps>(function Btn({ className, variant = "outline", size = "md", type = "button", ...p }, ref) {
  const v = {
    outline: "border border-line-strong/70 bg-elev text-muted hover:bg-hover hover:text-fg",
    ghost: "border border-transparent text-faint hover:bg-hover hover:text-fg",
    accent: "border border-accent bg-accent text-white hover:brightness-110",
    danger: "border border-critical/40 bg-elev text-critical hover:bg-critical-soft",
  }[variant];
  const s = size === "sm" ? "h-6 px-2 rounded text-[12px] gap-1" : "h-8 px-3 rounded-lg text-[13px] gap-1.5";
  return <button ref={ref} type={type} className={cn("inline-flex items-center justify-center font-medium whitespace-nowrap transition-colors disabled:opacity-50 disabled:pointer-events-none focus-visible:outline-none focus-visible:border-accent", v, s, className)} {...p} />;
});

/* --------------------------------------------------------------- tag, chip, label */
const TAG = { gray: "bg-normal-soft text-muted", green: "bg-good-soft text-good", amber: "bg-high-soft text-high", red: "bg-critical-soft text-critical", accent: "bg-accent-soft text-accent" } as const;
export type Tone = keyof typeof TAG;
/** A category or a result in a word or two. Grey when it carries no judgement. */
export function Tag({ tone = "gray", className, ...p }: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return <span className={cn("inline-flex items-center gap-1 h-5 px-2 rounded text-[12px] font-medium whitespace-nowrap", TAG[tone], className)} {...p} />;
}
/** A reference to another record (a property, a person): its small icon and its name. */
export function Chip({ icon, children, className }: { icon?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <span className={cn("inline-flex items-center gap-1.5 h-5 max-w-full pl-1 pr-1.5 rounded bg-hover text-fg", className)}>{icon && <span className="text-faint shrink-0">{icon}</span>}<span className="truncate">{children}</span></span>;
}
/** The small label over a figure or a group. */
export function Label({ className, ...p }: React.HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("text-[11px] font-semibold text-faint", className)} {...p} />;
}

/* ------------------------------------------------------------- views and figures */
/** The 40px bar under the top bar: the views of the list on the left, a note on the right. */
export function Views({ children }: { children: React.ReactNode }) {
  return <div className="h-10 shrink-0 flex items-center gap-0.5 px-2 border-b border-line">{children}</div>;
}
export function View({ active, count, children, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean; count?: React.ReactNode }) {
  return (
    <button type="button" className={cn("h-6 px-2 rounded flex items-center gap-1.5 text-[13px] font-medium transition-colors", active ? "bg-hover text-fg" : "text-faint hover:text-fg")} {...p}>
      {children}{count != null && <span className="font-normal text-faint tnum">{count}</span>}
    </button>
  );
}
/** A row of plain figures, divided by lines. */
export function Figures({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("grid grid-cols-2 lg:grid-cols-4 border-b border-line", className)}>{children}</div>;
}
export function Figure({ label, value, sub, tone, children }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string; children?: React.ReactNode }) {
  return (
    <div className="min-w-0 px-4 py-3 border-r border-line last:border-r-0">
      <Label>{label}</Label>
      <div className={cn("mt-1 text-[20px] leading-tight font-semibold tnum", tone)}>{value}</div>
      {children}
      {sub && <div className="mt-0.5 text-[12px] text-muted truncate">{sub}</div>}
    </div>
  );
}

/* ----------------------------------------------------------------------- tables */
/** 32px rows; a header cell and a body cell. Each row owns its bottom line. */
export const TH = "h-8 px-2 text-left text-[12px] font-medium text-faint bg-subtle border-b border-r border-line last:border-r-0 whitespace-nowrap overflow-hidden text-ellipsis";
export const TD = "h-8 px-2 text-[13px] text-muted border-b border-r border-line/80 last:border-r-0 overflow-hidden text-ellipsis whitespace-nowrap";
/** The header's small icon and its word, kept on one line. */
export function Col({ icon, children }: { icon?: React.ReactNode; children: React.ReactNode }) {
  return <span className="inline-flex items-center gap-1.5 align-middle">{icon}{children}</span>;
}

/* ------------------------------------------------------------------- tick box */
/** A 16px box for a step that is done or not. `next` marks the step the record is waiting on. */
export function Tick({ checked, onChange, next, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; next?: boolean; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={label} title={label} disabled={disabled} onClick={(e) => { e.stopPropagation(); onChange(!checked); }}
      className={cn("h-4 w-4 shrink-0 rounded border grid place-items-center transition-colors disabled:opacity-50",
        checked ? "bg-good border-good text-white" : next ? "bg-elev border-accent hover:bg-accent-soft" : "bg-elev border-line-strong/70 hover:border-accent")}>
      {checked && <Check size={11} strokeWidth={3} />}
    </button>
  );
}

/* --------------------------------------------------------------- outlined card */
/** A block on a page: one outline, a 40px title row, no shadow. */
export function Block({ title, note, right, children, className }: { title?: React.ReactNode; note?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-lg border border-line overflow-hidden bg-elev", className)}>
      {title && (
        <div className="min-h-10 pl-4 pr-2 py-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-line">
          <span className="text-[13px] font-semibold">{title}</span>
          {note && <span className="text-[12px] text-faint">{note}</span>}
          <span className="flex-1" />
          {right}
        </div>
      )}
      {children}
    </section>
  );
}
