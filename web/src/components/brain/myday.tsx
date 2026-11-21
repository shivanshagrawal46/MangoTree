"use client";

/* My day — one person's own work, and nothing else.

   Laid out as an admin page (2026-11-21): flat notices for what came in from the sites (new daily
   logs, new bills), one line on what the last daily check settled, one row of plain numbers, then
   one table per kind of work, never mixed:
     · Replies    — a written draft ("Review draft") or a reply still to write ("Mark done").
     · Decisions  — overdue first, then by due date.
     · To do      — the same.
   (JP Sir and Manjunath Sir also have "Bills and site checks".) Five rows of each show; "View all"
   opens the rest in place. A click on a row's words opens it in place: the whole line, its detail,
   and the email or call it came from.

   The team is NOT here. It has its own page (TeamPage, Rakesh Sir only): he sees each
   person's list, gives a task, sends a reminder — and cannot mark another person's work
   done. Only the owner can; the server refuses anyone else. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, ArrowRight, Check, ChevronDown, Info, Plus, Send, X } from "lucide-react";
import { api } from "@/lib/api";
import { useEvidence } from "@/components/evidence";
import { Button, Card, Dialog, DialogContent, Input, Select, Skeleton, Textarea } from "@/components/ui";
import { DraftCard } from "@/components/brain/draft";
import type { Alerts } from "@/components/brain/followups";
import { cn } from "@/lib/utils";
import { errText, type DayBoard, type DayCard, type DayCheck, type Draft, type TeamRow } from "@/lib/brain";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const daysSince = (iso?: string | null) => (iso ? Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)) : null);
const dayWord = (n: number) => `${n} day${n === 1 ? "" : "s"}`;
/* A due date is a calendar day (kept at 00:00 UTC). It is compared and shown as that day, on the firm's clock
   (US Eastern) — never run through the browser's time zone, which would show it a day early in the US. */
const dueDay = (c: DayCard) => (c.due ? String(c.due).slice(0, 10) : "");
const todayET = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
const dueToday = (c: DayCard) => !!c.due && dueDay(c) === todayET();
const daysLate = (c: DayCard) => (c.due ? Math.round((Date.parse(todayET()) - Date.parse(dueDay(c))) / 86_400_000) : 0);
const dueText = (c: DayCard) => (c.due ? `due ${Number(dueDay(c).slice(8, 10))} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(dueDay(c).slice(5, 7)) - 1]}` : "");

function useActions(onOpenDraft: (id: string) => void) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["brain"] });
  const done = async (c: DayCard) => {
    // The row leaves at once; the list comes back from the server right after.
    qc.setQueryData<DayBoard>(["brain", "myday"], (cur) => cur ? { ...cur, now: cur.now.filter((x) => x.key !== c.key), later: cur.later.filter((x) => x.key !== c.key) } : cur);
    try {
      await api.patch(`/brain/properties/${c.property_id}/items/${c.item_id}`, { status: "done" });
      toast.success("Done", { action: { label: "Undo", onClick: async () => { await api.patch(`/brain/properties/${c.property_id}/items/${c.item_id}`, { status: "open" }); refresh(); } } });
    } catch (e) { toast.error(errText(e)); }
    refresh();
  };
  return { done, openDraft: onOpenDraft, refresh };
}

/* ───────────────────────── small pieces ───────────────────────── */

function Chip({ tone, children }: { tone: "late" | "warn" | "plain"; children: React.ReactNode }) {
  return <span className={cn("ml-1.5 inline-block align-[1px] rounded px-1.5 py-px text-[10.5px] font-semibold whitespace-nowrap",
    tone === "late" ? "bg-critical-soft text-critical" : tone === "warn" ? "bg-high-soft text-high" : "bg-sunken text-muted")}>{children}</span>;
}

/* What a row shows when it is opened: the detail written with it, its dates, and the email or call it came from. */
function More({ c, shown }: { c: DayCard; shown: string }) {
  const evidence = useEvidence();
  const facts = [
    c.due ? (c.overdue ? `was ${dueText(c)}` : dueText(c)) : "",
    c.assigned_by ? `given by ${c.assigned_by_name || "Rakesh Sir"}` : "",
    c.amount != null ? usd(c.amount) : "",
  ].filter(Boolean);
  const sha = c.source_sha || "";
  const nothing = !c.detail && c.title === shown && !(c.stale && c.why);
  return (
    <div className="mx-4 mb-3 rounded-lg bg-sunken/60 px-3.5 py-2.5 text-[13px] leading-relaxed">
      {c.title !== shown && <div className="font-semibold">{c.title}</div>}
      {c.detail && <div className="whitespace-pre-line text-fg/85">{c.detail}</div>}
      {c.stale && c.why && <div className="mt-1 text-high"><b className="font-semibold">Why read it again:</b> {c.why}</div>}
      {nothing && <div className="text-muted">Nothing more was written with this one.</div>}
      {(facts.length > 0 || sha) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted">
          {facts.length > 0 && <span>{facts.join(" · ")}</span>}
          {sha && (sha.startsWith("granola-")
            ? <Link href={`/calls?open=${sha.slice(8)}`} className="font-semibold text-accent hover:underline">Open the call it came from →</Link>
            : <button type="button" className="font-semibold text-accent hover:underline" onClick={() => evidence.open({ sha })}>Open the email it came from →</button>)}
        </div>
      )}
    </div>
  );
}

/* One row of any list: a bold line, a grey line under it, one thing on the right.
   A click on the words opens the row in place: the whole line, and under it what ``More`` shows. */
function Row({ title, chips, where, pid, note, right, c }: { title: string; chips?: React.ReactNode; where?: string; pid?: string; note?: React.ReactNode; right: React.ReactNode; c?: DayCard }) {
  const [open, setOpen] = React.useState(false);
  return (
    <li className="border-t border-line">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5">
        <div className="min-w-0 cursor-pointer" onClick={() => setOpen((v) => !v)}>
          <button type="button" aria-expanded={open} title={open ? "Close" : "Click to read it in full"} className="flex w-full cursor-pointer items-start gap-1.5 text-left">
            <span className={cn("min-w-0 flex-1 text-[13.5px] font-semibold", open ? "leading-snug" : "truncate")}>{title}{chips}</span>
            <ChevronDown size={14} className={cn("mt-[3px] shrink-0 text-faint transition-transform", open && "rotate-180")} />
          </button>
          <div className={cn("text-[12px] text-muted", open ? "mt-0.5 leading-snug" : "truncate")}>
            {where && (pid ? <Link href={`/property/${pid}`} onClick={(e) => e.stopPropagation()} className="font-semibold text-fg hover:text-accent">{where}</Link> : <span>{where}</span>)}
            {where && note ? " · " : ""}{note}
          </div>
        </div>
        <div className="shrink-0">{right}</div>
      </div>
      {open && c && <More c={c} shown={title} />}
    </li>
  );
}

const where = (c: DayCard) => ({ where: c.property_id === "_general" ? "General" : c.property, pid: c.property_id === "_general" ? undefined : c.property_id });

function lateChip(c: DayCard) {
  if (!c.overdue) return dueToday(c) ? <Chip tone="late">due today</Chip> : null;
  const n = daysLate(c);
  return <Chip tone="late">{n > 0 ? `${dayWord(n)} late` : "late"}</Chip>;
}

/* Late first, then by due date, then in the order the day was planned. */
function byUrgency(cards: DayCard[]) {
  const t = (c: DayCard) => (c.due ? Date.parse(dueDay(c)) : Number.MAX_SAFE_INTEGER);
  return cards.map((c, i) => ({ c, i })).sort((x, y) => Number(!!y.c.overdue) - Number(!!x.c.overdue) || t(x.c) - t(y.c) || x.i - y.i).map((x) => x.c);
}

/* ───────────────────────── Today: my own work ─────────────────────────
   Laid out as an admin page (2026-11-21, after two looser layouts were turned down): flat notices
   for what came in from the sites, one row of plain numbers, then one table per kind of work —
   replies, decisions, things to do — never mixed. Colour is used for state only: red for overdue,
   amber for due today. */

const ROWS = 5;                                   // rows of a table before "View all"
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dueShort = (c: DayCard) => (c.due ? `${Number(dueDay(c).slice(8, 10))} ${MONTHS[Number(dueDay(c).slice(5, 7)) - 1]}` : "");
const raisedShort = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "America/New_York" }) : "");
const count = (x: number, one: string, many: string) => `${x} ${x === 1 ? one : many}`;
const BTN = "inline-flex h-[26px] items-center gap-1 whitespace-nowrap rounded-[5px] border px-2.5 text-[12px] font-medium transition-colors disabled:opacity-50";
const BTN_PLAIN = cn(BTN, "border-line-strong bg-elev text-fg hover:bg-sunken");
const BTN_MAIN = cn(BTN, "border-accent bg-accent text-white hover:opacity-90");

function State({ tone, children }: { tone?: "red" | "amber" | "green"; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[12.5px]", tone === "red" ? "font-medium text-critical" : tone === "amber" ? "font-medium text-high" : "text-fg/80")}>
      <i className={cn("h-[7px] w-[7px] shrink-0 rounded-full", tone === "red" ? "bg-critical" : tone === "amber" ? "bg-high" : tone === "green" ? "bg-good" : "bg-line-strong")} />
      {children}
    </span>
  );
}

/* Where a row stands against its date: overdue, due today, due in so many days; a reply with no date says how long it has waited. */
function Standing({ c }: { c: DayCard }) {
  if (c.overdue) { const n = daysLate(c); return <State tone="red">{n > 0 ? `Overdue ${dayWord(n)}` : "Overdue"}</State>; }
  if (dueToday(c)) return <State tone="amber">Due today</State>;
  if (c.due) { const n = -daysLate(c); return <State>{n === 1 ? "Due tomorrow" : `Due in ${dayWord(n)}`}</State>; }
  if (c.type === "reply") { const w = daysSince(c.received); return <State>{w ? `Waiting ${dayWord(w)}` : "Came in today"}</State>; }
  if (c.days) return <State>{dayWord(c.days)} old</State>;
  return <span className="text-faint">—</span>;
}

type Col = { head: string; width?: number; right?: boolean; cell: (c: DayCard) => React.ReactNode };

function TableRow({ c, cols, lead }: { c: DayCard; cols: Col[]; lead: (c: DayCard) => string }) {
  const [open, setOpen] = React.useState(false);
  const text = lead(c);
  return (
    <>
      <tr className="group border-t border-line hover:bg-sunken/40">
        <td className="h-[38px] max-w-0 cursor-pointer pl-4 pr-3" onClick={() => setOpen((v) => !v)} title={open ? "Close" : "Click to read it in full"}>
          <div className="flex items-center gap-1.5">
            <span className={cn("min-w-0 text-[13px] font-medium", open ? "whitespace-normal py-2 leading-snug" : "truncate")}>{text}</span>
            {c.assigned_by && <span className="shrink-0 rounded-[3px] bg-sunken px-1.5 text-[11px] text-muted">from {c.assigned_by_name || "Rakesh Sir"}</span>}
            <ChevronDown size={13} className={cn("shrink-0 text-faint opacity-0 transition group-hover:opacity-100", open && "rotate-180 opacity-100")} />
          </div>
        </td>
        {cols.map((col) => <td key={col.head || "act"} className={cn("truncate px-3 text-[12.5px] text-fg/80", col.right && "pl-3 pr-4 text-right")}>{col.cell(c)}</td>)}
      </tr>
      {open && <tr><td colSpan={cols.length + 1} className="bg-sunken/25 pt-2.5"><More c={c} shown={text} /></td></tr>}
    </>
  );
}

/* One kind of work as a table: its name and count, column heads, five rows, and the rest on request. */
function WorkTable({ title, hint, lead, leadHead, cards, cols, empty }: { title: string; hint: string; lead: (c: DayCard) => string; leadHead: string; cards: DayCard[]; cols: Col[]; empty: string }) {
  const [all, setAll] = React.useState(false);
  const rows = all ? cards : cards.slice(0, ROWS);
  return (
    <section className="mt-3.5 overflow-hidden rounded-md border border-line bg-elev">
      <div className="flex items-center justify-between gap-3 px-4 py-2.5">
        <h2 className="flex items-center gap-2 text-[14px] font-semibold">
          {title}
          <span className="rounded-[3px] bg-sunken px-1.5 py-px text-[12px] font-medium text-muted tnum">{cards.length}</span>
          <span className="hidden text-[12.5px] font-normal text-muted sm:inline">{hint}</span>
        </h2>
        {cards.length > ROWS && <button type="button" onClick={() => setAll((v) => !v)} className="text-[12.5px] font-medium text-accent hover:underline">{all ? "Show fewer" : "View all"}</button>}
      </div>
      {cards.length === 0 ? <div className="border-t border-line px-4 py-5 text-[13px] text-muted">{empty}</div> : (
        <table className="w-full table-fixed border-collapse">
          <colgroup><col />{cols.map((col) => <col key={col.head || "act"} style={{ width: col.width }} />)}</colgroup>
          <thead>
            <tr className="border-t border-line bg-sunken/50 text-left text-[11.5px] font-medium text-muted">
              <th className="py-1.5 pl-4 pr-3 font-medium">{leadHead}</th>
              {cols.map((col) => <th key={col.head || "act"} className={cn("px-3 py-1.5 font-medium", col.right && "pr-4 text-right")}>{col.head}</th>)}
            </tr>
          </thead>
          <tbody>{rows.map((c) => <TableRow key={c.key} c={c} cols={cols} lead={lead} />)}</tbody>
        </table>
      )}
      {cards.length > ROWS && (
        <div className="flex items-center justify-between border-t border-line bg-sunken/50 px-4 py-1.5 text-[12px] text-muted">
          <span>Showing {rows.length} of {cards.length} · overdue first, then by due date</span>
          <button type="button" onClick={() => setAll((v) => !v)} className="font-medium text-accent hover:underline">{all ? "Show fewer" : `View all ${cards.length}`}</button>
        </div>
      )}
    </section>
  );
}

/* What came in from the sites, as flat notices above everything: new daily logs, new bills.
   A notice a person has closed stays closed (on this browser) until Buildertrend brings something new. */
const SEEN_KEY = "mt_today_seen";
function Notices() {
  const q = useQuery({ queryKey: ["brain", "alerts"], queryFn: () => api.get<Alerts>("/brain/alerts"), staleTime: 60_000, refetchInterval: 180_000 });
  // Read once, in the browser. (On the server there is no data yet, so nothing is drawn there either way.)
  const [seen, setSeen] = React.useState<Record<string, string>>(() => {
    try { return typeof window === "undefined" ? {} : JSON.parse(window.localStorage.getItem(SEEN_KEY) || "{}"); } catch { return {}; }
  });
  const a = q.data;
  if (!a) return null;
  const logs = a.new_logs.reduce((n, l) => n + l.count, 0);
  const fresh = a.in_review.bills.filter((b) => b.new);
  const sig = { logs: `${a.read_at || ""}:${logs}`, bills: fresh.map((b) => b.bill_id).sort().join(",") };
  const close = (k: "logs" | "bills") => { const next = { ...seen, [k]: sig[k] }; setSeen(next); try { window.localStorage.setItem(SEEN_KEY, JSON.stringify(next)); } catch { /* private mode: closed for this visit only */ } };
  const showLogs = logs > 0 && seen.logs !== sig.logs;
  const showBills = fresh.length > 0 && seen.bills !== sig.bills;
  if (!showLogs && !showBills) return null;
  const read = a.read_at ? new Date(a.read_at).toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "2-digit" }) : "";
  const bar = "grid grid-cols-[16px_minmax(0,1fr)_auto_auto] items-center gap-2.5 rounded-[4px] border border-l-[3px] px-3 py-[7px] text-[13px]";
  const shortName = (p: string) => p.replace(/^\d+\s+/, "");
  return (
    <div className="mt-4 grid gap-1.5">
      {showLogs && (
        <div className={cn(bar, "border-money-in/25 border-l-money-in bg-money-in/[0.045]")}>
          <Info size={15} className="text-money-in" />
          <div className="truncate">
            <b className="font-semibold">{count(logs, "new daily log", "new daily logs")}</b>
            <span className="ml-2 text-fg/75">
              {a.new_logs.map((l, i) => <React.Fragment key={l.property_id}>{i > 0 && " · "}<Link href={`/property/${l.property_id}?tab=site`} className="hover:text-money-in hover:underline">{shortName(l.property)} ({l.count})</Link></React.Fragment>)}
              {read && <span className="text-muted"> · Buildertrend read {read} ET</span>}
            </span>
          </div>
          <span />
          <button type="button" aria-label="Close this notice" onClick={() => close("logs")} className="grid h-5 w-5 place-items-center rounded text-faint hover:bg-sunken hover:text-fg"><X size={13} /></button>
        </div>
      )}
      {showBills && (
        <div className={cn(bar, "border-high/25 border-l-high bg-high-soft/60")}>
          <AlertTriangle size={15} className="text-high" />
          <div className="truncate">
            <b className="font-semibold">{count(fresh.length, "new bill waiting for approval", "new bills waiting for approval")}</b>
            <span className="ml-2 text-fg/75">
              {fresh.map((b, i) => <React.Fragment key={b.bill_id}>{i > 0 && " · "}<Link href={`/bills/${b.bill_id}`} className="hover:text-high hover:underline">{shortName(b.property)} <span className="tnum">{usd(b.amount)}</span></Link></React.Fragment>)}
              <span className="text-muted"> · {count(a.in_review.count, "bill", "bills")} waiting in all, <span className="tnum">{usd(a.in_review.amount)}</span></span>
            </span>
          </div>
          <Link href="/bills" className="whitespace-nowrap text-[12.5px] font-medium text-high hover:underline">Review bills</Link>
          <button type="button" aria-label="Close this notice" onClick={() => close("bills")} className="grid h-5 w-5 place-items-center rounded text-faint hover:bg-sunken hover:text-fg"><X size={13} /></button>
        </div>
      )}
    </div>
  );
}

/* What the last daily run settled: one line, and the to-dos it took off the list on request. */
function CheckLine({ k }: { k: DayCheck }) {
  const [open, setOpen] = React.useState(false);
  const when = new Date(k.at).toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "2-digit" });
  const e = k.emails;
  return (
    <div className="mt-3 rounded-md border border-line bg-elev px-4 py-2 text-[12.5px] text-muted">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <b className="font-semibold text-fg">Last check: {when} ET</b>
        {e && (e.read === 0 ? <span>No new email.</span>
          : <span>{count(e.read, "new email", "new emails")} read: {count(e.reply_needed, "needs", "need")} a reply, {count(e.no_reply_needed, "needs", "need")} none{e.found_answered > 0 ? `; ${count(e.found_answered, "waiting reply was", "waiting replies were")} found already sent` : ""}.</span>)}
        <span>Your to-dos: {k.n_closed > 0 ? `${k.n_closed} taken off` : "none taken off"}, <b className="font-semibold text-fg tnum">{k.left}</b> left.</span>
        {k.closed.length > 0 && <button type="button" onClick={() => setOpen((v) => !v)} className="font-medium text-accent hover:underline">{open ? "Hide" : "See which"}</button>}
      </div>
      {open && (
        <ul className="mt-1.5 space-y-1 border-t border-line pt-1.5">
          {k.closed.map((c, i) => (
            <li key={i}><span className="font-medium text-fg">{c.title}</span> · {c.property}<span> — {c.note || c.how}</span></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Figure({ label, value, hint, red }: { label: string; value: number; hint: string; red?: boolean }) {
  return (
    <div className="min-w-0 border-l border-line px-4 py-2.5 first:border-l-0">
      <div className="text-[12px] text-muted">{label}</div>
      <div className={cn("text-[22px] font-semibold leading-tight tracking-tight tnum", red && value > 0 && "text-critical")}>{value}</div>
      <div className="truncate text-[12px] text-muted">{hint}</div>
    </div>
  );
}

export function MyDay() {
  const q = useQuery({ queryKey: ["brain", "myday"], queryFn: () => api.get<DayBoard>("/brain/myday"), placeholderData: (prev) => prev, refetchInterval: 60_000 });
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const openDraft = async (id: string) => { try { setDraft(await api.get<Draft>(`/brain/drafts/${id}`)); } catch (e) { toast.error(errText(e)); } };
  const a = useActions(openDraft);
  if (q.isLoading || !q.data) return <div className="mt-5 space-y-3.5"><Skeleton className="h-16" /><Skeleton className="h-64" /><Skeleton className="h-64" /></div>;
  const d = q.data;
  const cards = [...d.now, ...d.later];
  const drafted = (c: DayCard) => c.type === "reply" && !!c.draft_id;
  // A reply is a written draft, or a to-do whose whole point is to answer someone. Overdue first, then what is written and ready to send.
  const replyRank = (c: DayCard) => (c.overdue ? 0 : drafted(c) ? 1 : 2);
  const replies = byUrgency(cards.filter((c) => c.type === "reply" || (c.type === "todo" && c.kind === "reply"))).map((c, i) => ({ c, i })).sort((x, y) => replyRank(x.c) - replyRank(y.c) || x.i - y.i).map((x) => x.c);
  const decisions = byUrgency(cards.filter((c) => c.type === "todo" && c.kind === "decision"));
  const todos = byUrgency(cards.filter((c) => c.type === "todo" && c.kind !== "decision" && c.kind !== "reply"));
  const work = cards.filter((c) => c.type === "bill" || c.type === "check");
  const late = cards.filter((c) => c.overdue);
  const week = (list: DayCard[]) => list.filter((c) => c.due && !c.overdue && daysLate(c) >= -7).length;
  const lateBy = [[late.filter((c) => replies.includes(c)).length, "reply", "replies"], [late.filter((c) => decisions.includes(c)).length, "decision", "decisions"],
    [late.filter((c) => todos.includes(c)).length, "to-do", "to-dos"], [late.filter((c) => work.includes(c)).length, "bill or check", "bills and checks"]] as const;

  const done = (c: DayCard): React.ReactNode => <button type="button" className={BTN_PLAIN} onClick={() => a.done(c)}><Check size={12} /> Mark done</button>;
  const property: Col = { head: "Property", width: 178, cell: (c) => (c.property_id === "_general" ? "General" : <Link href={`/property/${c.property_id}`} className="hover:text-accent hover:underline">{c.property}</Link>) };
  const raised: Col = { head: "Raised", width: 84, cell: (c) => <span className="tnum">{raisedShort(c.opened_at) || "—"}</span> };
  const due: Col = { head: "Due", width: 84, cell: (c) => <span className="tnum">{dueShort(c) || "—"}</span> };
  const standing: Col = { head: "Status", width: 142, cell: (c) => <Standing c={c} /> };
  const replyCols: Col[] = [property, due, standing,
    { head: "Draft", width: 148, cell: (c) => (!drafted(c) ? <span className="text-muted">Not drafted</span> : c.stale ? <State tone="amber">Changed, read again</State> : <State tone="green">Ready</State>) },
    { head: "", width: 128, right: true, cell: (c) => (drafted(c)
      ? <button type="button" className={c.stale ? BTN_PLAIN : BTN_MAIN} onClick={() => c.draft_id && a.openDraft(c.draft_id)}>{c.stale ? "Read again" : "Review draft"}</button>
      : done(c)) }];
  const todoCols: Col[] = [property, raised, due, standing, { head: "", width: 128, right: true, cell: done }];
  const workCols: Col[] = [property,
    { head: "Amount", width: 110, cell: (c) => <span className="tnum">{c.amount != null ? usd(c.amount) : "—"}</span> },
    standing,
    { head: "", width: 128, right: true, cell: (c) => <Link href={c.href} className={BTN_PLAIN}>Open <ArrowRight size={12} /></Link> }];
  const replyLead = (c: DayCard) => (drafted(c) ? `${c.from_name || "Reply"} — ${c.subject || c.title}` : c.title);

  return (
    <>
      <Notices />
      {d.check && <CheckLine k={d.check} />}

      <div className={cn("mt-3 grid grid-cols-2 overflow-hidden rounded-md border border-line bg-elev", work.length > 0 ? "lg:grid-cols-5" : "lg:grid-cols-4")}>
        <Figure label="Replies" value={replies.length} hint={`${replies.filter(drafted).length} drafted · ${replies.filter((c) => !drafted(c)).length} not drafted`} />
        <Figure label="Decisions" value={decisions.length} hint={week(decisions) ? `${week(decisions)} due this week` : "none due this week"} />
        <Figure label="To do" value={todos.length} hint={`${week(todos) ? `${week(todos)} due this week` : "none due this week"} · ${d.done_today.length} done today`} />
        {work.length > 0 && <Figure label="Bills and site checks" value={work.length} hint={`${count(work.filter((c) => c.type === "bill").length, "bill", "bills")} · ${count(work.filter((c) => c.type === "check").length, "site check", "site checks")}`} />}
        <Figure label="Overdue" value={late.length} red hint={late.length ? lateBy.filter(([n]) => n > 0).map(([n, one, many]) => `${n} ${n === 1 ? one : many}`).join(" · ") : "nothing is overdue"} />
      </div>

      <WorkTable title="Replies" hint="People waiting to hear from you" leadHead="Subject" lead={replyLead} cards={replies} cols={replyCols} empty="Nobody is waiting for a reply from you." />
      {work.length > 0 && <WorkTable title="Bills and site checks" hint="From Buildertrend" leadHead="Item" lead={(c) => c.title} cards={work} cols={workCols} empty="No bill or site check is waiting." />}
      <WorkTable title="Decisions" hint="Waiting for your decision" leadHead="Decision" lead={(c) => c.title} cards={decisions} cols={todoCols} empty="No decision is waiting for you." />
      <WorkTable title="To do" hint="Things for you to do or have done" leadHead="Task" lead={(c) => c.title} cards={todos} cols={todoCols} empty="Nothing is waiting for you. New things appear here as emails, calls and bills come in." />

      <Dialog open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent wide className="max-h-[90vh] overflow-y-auto" title="Reply">
          {draft && <DraftCard draft={draft} onChange={(next) => { if (next.status !== "ready") { setDraft(null); a.refresh(); } }} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

/* ───────────────────────── Team: Rakesh Sir's view of the others ───────────────────────── */

/* Rakesh Sir gives someone a task. */
function GiveTask({ people, to, onClose }: { people: DayBoard["people"]; to: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const props = useQuery({ queryKey: ["properties"], queryFn: () => api.get<{ property_id: string; address: string }[]>("/properties"), staleTime: 5 * 60_000 });
  const [who, setWho] = React.useState(to || people[0]?.person || "");
  const [title, setTitle] = React.useState("");
  const [detail, setDetail] = React.useState("");
  const [due, setDue] = React.useState("");
  const [pid, setPid] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const name = people.find((p) => p.person === who)?.name || "them";
  const give = async () => {
    setBusy(true);
    try {
      await api.post("/brain/tasks", { to: who, title: title.trim(), detail: detail.trim(), due: due || null, property_id: pid || null });
      toast.success(`Task given to ${name}. An email is on its way to them.`);
      qc.invalidateQueries({ queryKey: ["brain"] });
      onClose();
    } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  return (
    <div className="space-y-4">
      <div>
        <div className="text-xs font-medium text-muted mb-1.5">Who</div>
        <div className="flex flex-wrap gap-2">
          {people.map((p) => (
            <button key={p.person} type="button" onClick={() => setWho(p.person)} className={cn("h-9 px-3.5 rounded-xl border text-sm transition", who === p.person ? "border-accent bg-accent-soft text-accent font-medium" : "border-line text-muted hover:border-line-strong")}>{p.name}</button>
          ))}
        </div>
      </div>
      <div>
        <div className="text-xs font-medium text-muted mb-1.5">What should {name} do?</div>
        <Input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Send Wes the signed Durham change order" maxLength={160} />
      </div>
      <div>
        <div className="text-xs font-medium text-muted mb-1.5">Details <span className="text-faint font-normal">(optional)</span></div>
        <Textarea value={detail} onChange={(e) => setDetail(e.target.value)} rows={3} placeholder="Anything they need to know to finish it" maxLength={600} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs font-medium text-muted mb-1.5">By when <span className="text-faint font-normal">(optional)</span></div>
          <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
        </div>
        <div>
          <div className="text-xs font-medium text-muted mb-1.5">Property <span className="text-faint font-normal">(optional)</span></div>
          <Select value={pid} onChange={(e) => setPid(e.target.value)}>
            <option value="">No single property</option>
            {(props.data || []).map((p) => <option key={p.property_id} value={p.property_id}>{p.address}</option>)}
          </Select>
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-1">
        <div className="text-xs text-faint">It goes on {name}&rsquo;s list at once, and an email is sent to them from your mailbox.</div>
        <Button variant="primary" className="shrink-0 whitespace-nowrap" onClick={give} disabled={busy || title.trim().length < 4}>{busy ? "Giving…" : "Give task"}</Button>
      </div>
    </div>
  );
}

function PersonCard({ r, onGive, onRemind }: { r: TeamRow; onGive: () => void; onRemind: (c: DayCard) => void }) {
  const [open, setOpen] = React.useState(false);
  const all = [...r.now, ...r.later];
  const n = (t: DayCard["type"]) => all.filter((c) => c.type === t).length;
  const parts = [[n("todo"), "to-do", "to-dos"], [n("bill"), "bill", "bills"], [n("check"), "site check", "site checks"], [n("reply"), "reply", "replies"]] as const;
  const total = r.left + r.done_today;
  const rows = byUrgency(all).slice(0, open ? all.length : 3);
  return (
    <Card className="overflow-hidden self-start">
      <div className="flex items-baseline justify-between px-4 pt-3">
        <h2 className="text-[15px] font-semibold tracking-tight">{r.name}</h2>
        {r.late > 0 ? <Chip tone="late">{r.late} late</Chip> : <Chip tone="plain">{r.left === 0 ? "all clear" : "on time"}</Chip>}
      </div>
      <div className="px-4 pt-0.5 text-[12px] text-muted">
        <b className="text-[20px] font-semibold text-fg tnum">{r.left}</b> left · {parts.filter(([k]) => k > 0).map(([k, one, many]) => `${k} ${k === 1 ? one : many}`).join(" · ") || "nothing waiting"} · {r.done_today} done today
      </div>
      <div className="mx-4 mt-2 mb-3 h-[5px] overflow-hidden rounded-full bg-sunken"><div className="h-full rounded-full bg-accent" style={{ width: `${total ? Math.round((100 * r.done_today) / total) : 0}%` }} /></div>
      <ul>
        {rows.map((c) => (
          <Row key={c.key} c={c} title={c.title} {...where(c)} chips={lateChip(c)}
            note={c.due && !c.overdue && !dueToday(c) ? dueText(c) : c.amount != null ? usd(c.amount) : undefined}
            right={<button type="button" onClick={() => onRemind(c)} className="text-[12px] font-semibold text-accent hover:underline">Remind</button>} />
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-t border-line bg-sunken/30 px-4 py-2.5 text-[12.5px] font-semibold text-accent">
        {all.length > 3 && <button type="button" onClick={() => setOpen((v) => !v)} className="hover:underline">{open ? "Show fewer" : `See all ${all.length} →`}</button>}
        <button type="button" onClick={onGive} className="inline-flex items-center gap-1 hover:underline"><Plus size={12} /> Give a task</button>
      </div>
      <div className="border-t border-line px-4 py-2 text-center text-[11.5px] text-faint">Only {r.name} can mark these done.</div>
    </Card>
  );
}

export function TeamPage() {
  const q = useQuery({ queryKey: ["brain", "myday"], queryFn: () => api.get<DayBoard>("/brain/myday"), placeholderData: (prev) => prev, refetchInterval: 60_000 });
  const [give, setGive] = React.useState<{ to: string | null } | null>(null);
  const [remind, setRemind] = React.useState<{ r: TeamRow; c: DayCard } | null>(null);
  const [busy, setBusy] = React.useState(false);
  if (q.isLoading || !q.data) return <div className="mt-5 grid gap-3.5 lg:grid-cols-3"><Skeleton className="h-72" /><Skeleton className="h-72" /><Skeleton className="h-72" /></div>;
  const d = q.data;
  if (!d.team) return <div className="mt-6 text-[13.5px] text-muted">The team page is for Rakesh Sir. Your own work is on <Link href="/" className="text-accent hover:underline">Today</Link>.</div>;
  const send = async () => {
    if (!remind) return;
    setBusy(true);
    try {
      const out = await api.post<{ status?: string }>("/brain/tasks/remind", { to: remind.r.person, key: remind.c.key });
      toast.success(out.status === "duplicate" ? `${remind.r.name} was already reminded of this today.` : `Reminder sent to ${remind.r.name}.`);
      setRemind(null);
    } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  return (
    <>
      <div className="mt-5 grid gap-3.5 lg:grid-cols-3">
        {d.team.map((r) => <PersonCard key={r.person} r={r} onGive={() => setGive({ to: r.person })} onRemind={(c) => setRemind({ r, c })} />)}
      </div>
      <Dialog open={!!give} onOpenChange={(o) => !o && setGive(null)}>
        <DialogContent title="Give a task" description="It lands on their list, and they get an email from you.">
          {give && <GiveTask people={d.people} to={give.to} onClose={() => setGive(null)} />}
        </DialogContent>
      </Dialog>
      <Dialog open={!!remind} onOpenChange={(o) => !o && setRemind(null)}>
        <DialogContent title={`Remind ${remind?.r.name || ""}`} description="An email goes to them from your mailbox, asking where this stands. Nothing on their list changes.">
          {remind && (
            <div className="space-y-4">
              <div className="rounded-xl border border-line bg-sunken/50 px-3.5 py-3 text-[13.5px]"><div className="font-semibold">{remind.c.title}</div><div className="mt-0.5 text-[12px] text-muted">{remind.c.property}</div></div>
              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setRemind(null)}>Cancel</Button>
                <Button variant="primary" onClick={send} disabled={busy}><Send size={13} /> {busy ? "Sending…" : "Send reminder"}</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
