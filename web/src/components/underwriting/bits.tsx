"use client";

/* The few pieces every underwriting page is built from, so the seven pages read as
   one: a page head that says what holds the next step, a status that is a mark
   and a word (never a button), the Needed / Optional / Done tag, a key/value row,
   and the money table. */

import * as React from "react";
import { cn } from "@/lib/utils";
import type { LineState } from "@/lib/uw";

export type Tone = LineState | "locked" | "pass" | "fail" | "look";

const WORD: Record<Tone, string> = { needed: "Needed", check: "Being checked", back: "Sent back", done: "Done", confirmed: "Confirmed by RKB", locked: "Cannot start yet", pass: "Pass", fail: "Fail", look: "To look at" };
const MARK: Record<Tone, string> = { needed: "", check: "…", back: "!", done: "✓", confirmed: "✓", locked: "", pass: "✓", fail: "!", look: "…" };
const TEXT: Record<Tone, string> = { needed: "text-muted", check: "text-high", back: "text-critical font-semibold", done: "text-fg", confirmed: "text-info", locked: "text-faint", pass: "text-fg", fail: "text-critical font-semibold", look: "text-high" };
const DOT: Record<Tone, string> = {
  needed: "border-[1.5px] border-faint", check: "bg-high text-white", back: "bg-critical text-white", done: "bg-good text-white", confirmed: "bg-info text-white",
  locked: "border-[1.5px] border-dashed border-faint", pass: "bg-good text-white", fail: "bg-critical text-white", look: "bg-high text-white",
};

export function Status({ tone, children, className }: { tone: Tone; children?: React.ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-[12.5px]", TEXT[tone], className)}>
      <i className={cn("not-italic h-3.5 w-3.5 rounded-full grid place-items-center text-[10px] font-bold shrink-0", DOT[tone])}>{MARK[tone]}</i>
      {children ?? WORD[tone]}
    </span>
  );
}

/* The tag on a line of the file, and on a question of pages 1 and 3 (asked for on 2026-11-21):
   red "Needed" while a compulsory thing is open, green "Optional" for what never holds the file,
   green "Done" once the paper is uploaded and verified. Sent back is a filled red, so it cannot
   be mistaken for a line nobody has touched yet. */
export type TagKind = LineState | "optional";
const TAG: Record<TagKind, { word: string; cls: string; mark?: string }> = {
  needed: { word: "Needed", cls: "bg-critical-soft text-critical border-critical/40" },
  optional: { word: "Optional", cls: "bg-good-soft text-good border-good/40" },
  check: { word: "Being checked", cls: "bg-high-soft text-high border-high/40", mark: "…" },
  back: { word: "Sent back", cls: "bg-critical text-white border-critical", mark: "!" },
  done: { word: "Done", cls: "bg-good text-white border-good", mark: "✓" },
  confirmed: { word: "Done · confirmed by RKB", cls: "bg-good text-white border-good", mark: "✓" },
};

export function Tag({ kind, children, className }: { kind: TagKind; children?: React.ReactNode; className?: string }) {
  const t = TAG[kind];
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 h-[22px] text-[11.5px] font-semibold leading-none", t.cls, className)}>
      {t.mark && <i className="not-italic font-bold">{t.mark}</i>}{children ?? t.word}
    </span>
  );
}

export function PageHead({ n, title, line, holds }: { n: number; title: string; line: string; holds?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-2 mt-4 mb-2.5 px-0.5">
      <div className="min-w-0">
        <h2 className="text-[17px] font-bold tracking-tight">{n} · {title}</h2>
        <p className="text-[13px] text-muted mt-0.5 max-w-[72ch]">{line}</p>
      </div>
      {holds && <div className="text-[12px] text-muted text-right">{holds}</div>}
    </div>
  );
}

export function Panel({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("bg-elev border border-line rounded-xl", className)} {...p} />;
}

export function SectionHead({ title, right }: { title: React.ReactNode; right?: React.ReactNode }) {
  return <div className="flex items-center justify-between gap-3 px-3.5 py-2.5 border-b border-line"><h3 className="text-[13.5px] font-semibold flex items-center gap-2">{title}</h3>{right}</div>;
}

export function KV({ k, children, className }: { k: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("grid grid-cols-1 sm:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] gap-x-2 gap-y-0.5 px-3.5 py-2 border-b border-line last:border-b-0 items-center text-[13px]", className)}>
      <span className="text-muted">{k}</span>
      <span className="font-medium flex flex-wrap items-center gap-x-2.5 gap-y-1 min-w-0 tnum">{children}</span>
    </div>
  );
}

export function Lab({ children }: { children: React.ReactNode }) {
  return <span className="text-[11px] uppercase tracking-[.05em] text-faint font-semibold">{children}</span>;
}

export function LockBar({ children }: { children: React.ReactNode }) {
  return <div className="bg-sunken rounded-lg px-2.5 py-1.5 mb-2.5 text-[12px] text-muted">{children}</div>;
}

export function MoneyTable({ rows, total }: { rows: { label: string; amount: string }[]; total?: { label: string; amount: string } }) {
  return (
    <table className="w-full text-[13px]">
      <tbody>
        {rows.map((r) => <tr key={r.label} className="border-b border-line last:border-b-0"><td className="py-1.5 pr-3">{r.label}</td><td className="py-1.5 text-right whitespace-nowrap tnum">{r.amount}</td></tr>)}
        {total && <tr className="font-bold border-t border-fg"><td className="py-1.5 pr-3">{total.label}</td><td className="py-1.5 text-right whitespace-nowrap tnum">{total.amount}</td></tr>}
      </tbody>
    </table>
  );
}

export const th = "text-[11px] uppercase tracking-[.05em] text-faint font-semibold text-left px-3 py-2 border-b border-line whitespace-nowrap";
export const td = "px-3 h-11 border-b border-line align-middle text-[13px]";
export const linkBtn = "text-accent font-semibold hover:underline disabled:opacity-50";
export const input = "h-8 w-full rounded-lg border border-line bg-elev px-2.5 text-[13px] placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-accent/30 focus:border-accent/50";
