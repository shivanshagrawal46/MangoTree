"use client";

/* The top of a property, kept to two thin rows so the chat has the screen:
   the address with the progress of the deal beside it, then the tabs with the
   three parties. */

import * as React from "react";
import { Check, Gavel, Tag } from "lucide-react";
import { cn, fmtDate } from "@/lib/utils";
import type { PersonOnDeal, PropertyFile, Stage } from "@/lib/brain";

export type TabKey = "overview" | "emails" | "documents" | "files" | "timeline" | "site" | "bills" | "sheet" | "arv" | "permits" | "money" | "people";
export const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" }, { key: "emails", label: "Emails" }, { key: "documents", label: "Documents" }, { key: "files", label: "Files" },
  { key: "timeline", label: "Timeline" }, { key: "site", label: "Site work" }, { key: "bills", label: "Bills" }, { key: "sheet", label: "Money sheet" }, { key: "arv", label: "ARV" },
  { key: "permits", label: "Permits" }, { key: "money", label: "Money" }, { key: "people", label: "People" },
];

const SHORT: Record<string, string> = { closing: "Closed", construction: "Construction", construction_complete: "Complete", listed: "Listed", under_contract: "Under contract", sold_repaid: "Sold & repaid" };

/* "Sharon Yvonne Peters (owner; guarantor). Borrower of record: …" -> the name. */
export function shortName(value: string) {
  const name = value.split(/\s\(|;|\s[-–—]\s|\.\s|,\s/)[0].trim();
  return name.length > 34 ? name.slice(0, 32) + "…" : name;
}

export function daysFrom(date?: string | null) {
  if (!date) return null;
  const d = new Date(date);
  return Number.isNaN(d.getTime()) ? null : Math.round((Date.now() - d.getTime()) / 86_400_000);
}

export function timeLeft(date?: string | null) {
  const n = daysFrom(date);
  if (n == null) return null;
  if (n > 0) return { text: `${n}d overdue`, long: `${n} day${n === 1 ? "" : "s"} overdue`, late: true };
  if (n === 0) return { text: "due today", long: "due today", late: false };
  return { text: `${-n}d left`, long: -n >= 60 ? `about ${Math.round(-n / 30)} months left` : `${-n} day${n === -1 ? "" : "s"} left`, late: false };
}

export function recoveryLabel(s: Stage) {
  return /foreclos/i.test(`${s.recovery_note || ""} ${s.one_line || ""}`) ? "Foreclosure" : "Legal recovery";
}

/* Closed -> Construction -> Complete -> Listed -> Under contract -> Sold & repaid, on one line.
   Done steps are ticked, the step the deal is on is the solid pill, the rest wait in outline. */
export function Stepper({ s }: { s: Stage }) {
  const build = timeLeft(s.construction_expected);
  const sale = timeLeft(s.sale_expected);
  const note = (key: string): string => {
    if (key === "construction") return [s.construction_percent != null ? `${s.construction_percent}%` : "", s.construction_expected ? `due ${fmtDate(s.construction_expected, "d MMM")}` : "no due date", build?.text || ""].filter(Boolean).join(" · ");
    if (key === "closing") return "not closed yet";
    if (key === "listed" || key === "under_contract") return s.sale_expected ? `closing ${fmtDate(s.sale_expected, "d MMM")}${sale ? " · " + sale.text : ""}` : "";
    return "";
  };
  return (
    <ol className="flex items-center min-w-0" title={s.one_line || undefined}>
      {s.steps.map((st, i) => {
        const label = SHORT[st.key] || st.label;
        const extra = st.state === "current" ? note(st.key) : "";
        return (
          <React.Fragment key={st.key}>
            {i > 0 && <span className={cn("h-[2px] w-5 xl:w-7 shrink-0 rounded-full", st.state === "ahead" ? "bg-line-strong/70" : s.recovery && st.state === "current" ? "bg-critical" : "bg-accent")} />}
            {st.state === "current" ? (
              <li className={cn("h-7 pl-1.5 pr-3 rounded-full inline-flex items-center gap-1.5 text-[12px] font-semibold text-white whitespace-nowrap shadow-[var(--shadow-sm)]", s.recovery ? "bg-critical" : "bg-accent")}>
                <span className="h-4 w-4 rounded-full bg-white/25 grid place-items-center"><span className="h-1.5 w-1.5 rounded-full bg-white" /></span>
                {label}{extra && <span className="font-normal text-white/85">· {extra}</span>}
              </li>
            ) : st.state === "done" ? (
              <li className="inline-flex items-center gap-1.5 text-[12px] text-fg/80 whitespace-nowrap" title={st.key === "closing" && s.closing_date ? `Closed ${fmtDate(s.closing_date, "d MMM yyyy")}` : undefined}>
                <span className="h-[18px] w-[18px] rounded-full bg-accent text-white grid place-items-center"><Check size={11} strokeWidth={3} /></span>{label}
              </li>
            ) : (
              <li className="inline-flex items-center gap-1.5 text-[12px] text-faint whitespace-nowrap">
                <span className="h-[18px] w-[18px] rounded-full border-[1.5px] border-line-strong grid place-items-center text-[9.5px] font-semibold">{i + 1}</span>{label}
              </li>
            )}
          </React.Fragment>
        );
      })}
    </ol>
  );
}

function Party({ label, people }: { label: string; people: PersonOnDeal[] }) {
  const p = people[0];
  return (
    <div className="flex items-baseline gap-1.5 min-w-0" title={people.map((x) => x.value).join("\n") || undefined}>
      <span className="text-[9.5px] font-semibold uppercase tracking-[0.1em] text-faint shrink-0">{label}</span>
      {p ? <span className="text-[12px] font-medium text-fg truncate max-w-[168px]">{shortName(p.value)}</span> : <span className="text-[12px] text-faint">not on record</span>}
    </div>
  );
}

export function PropertyHeader({ f, tab, onTab, counts }: { f: PropertyFile; tab: TabKey; onTab: (t: TabKey) => void; counts?: Partial<Record<TabKey, number>> }) {
  const s = f.stage || null;
  const people = f.people || [];
  const role = (r: string) => people.filter((p) => p.role === r);
  const [street, ...rest] = (f.address || "").split(", ");
  const tabs = f.is_general ? TABS.slice(0, 1) : TABS;
  return (
    <header className="shrink-0 border-b border-line bg-elev">
      <div className="h-11 px-5 flex items-center gap-5">
        <div className="flex items-baseline gap-2 min-w-0 shrink">
          <h1 className="text-[15px] font-semibold tracking-tight truncate">{street}</h1>
          <span className="text-[12px] text-muted truncate hidden md:inline">{rest.join(", ") || (f.is_general ? "Everything not about one active property" : "")}</span>
        </div>
        {s?.recovery && <span className="h-[22px] px-2 rounded-md bg-critical-soft text-critical border border-critical/30 text-[11px] font-semibold inline-flex items-center gap-1 shrink-0"><Gavel size={11} /> {recoveryLabel(s)}</span>}
        {s?.on_market && (
          <span title={s.listed_basis || undefined} className="h-[22px] px-2 rounded-md bg-accent-soft text-accent border border-accent/25 text-[11px] font-semibold inline-flex items-center gap-1 shrink-0 whitespace-nowrap">
            <Tag size={11} />
            {s.days_on_market != null
              ? <>{s.days_on_market} {s.days_on_market === 1 ? "day" : "days"} on market<span className="font-normal text-accent/80 hidden xl:inline"> · listed {fmtDate(s.listed_date, "d MMM yyyy")}</span></>
              : <>On market<span className="font-normal text-accent/80"> · listing date not on record</span></>}
          </span>
        )}
        {s && <div className="ml-auto hidden lg:block min-w-0 overflow-hidden"><Stepper s={s} /></div>}
      </div>
      <div className="h-[34px] px-3 flex items-center justify-between gap-4 border-t border-line/70">
        <nav className="flex h-full gap-0.5 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
          {tabs.map((t) => {
            const n = counts?.[t.key] || 0;
            return (
              <button key={t.key} type="button" onClick={() => onTab(t.key)}
                className={cn("relative h-full px-2.5 text-[12.5px] whitespace-nowrap transition flex items-center gap-1.5", tab === t.key ? "text-fg font-semibold" : "text-muted hover:text-fg")}>
                {t.label}
                {n > 0 && <span className={cn("text-[9.5px] font-semibold rounded-full px-1 h-[15px] min-w-[15px] grid place-items-center", tab === t.key ? "bg-accent text-white" : "bg-sunken text-muted")}>{n}</span>}
                {tab === t.key && <span className="absolute left-1.5 right-1.5 bottom-0 h-[2px] rounded-full bg-accent" />}
              </button>
            );
          })}
        </nav>
        {!f.is_general && (
          <div className="hidden xl:flex items-center gap-5 pr-2 shrink-0">
            <Party label="Homeowner" people={role("Homeowner / borrower")} />
            <Party label="Contractor" people={role("Contractor")} />
            <Party label="Title" people={role("Title company")} />
          </div>
        )}
      </div>
    </header>
  );
}
