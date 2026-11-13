"use client";

/* One bill, checked line by line.

   The contractor asks to be paid for certain lines of the proposal. This page puts
   that one request in front of the person who decides:
     – the accountant's check, worked out by arithmetic: which proposal line, its
       budget, billed before, this bill, what is left; hold-back; do the lines add up;
     – the engineer's check: what the contractor's own logs and photos show for each
       line (an AI reading, marked as such — not an inspection).
   Lines that need a look come first. A person marks a line OK or Hold, and that
   stands over the reading. Approving the bill is the same "Manju Sir approved" tick
   as on the Bills page. Nothing is sent from here. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Check, ChevronRight, Copy, MessageSquareText, Minus, Pause, RefreshCw, X } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Button, Card, Dialog, DialogContent, Skeleton } from "@/components/ui";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText } from "@/lib/brain";

type Verdict = "shown" | "said" | "partly" | "nothing" | "against";
type Evidence = { verdict: Verdict; basis: string; log_dates: string[]; photo_ids: number[]; ask: string };
type Decision = { decision?: "ok" | "hold" | null; note?: string; by_name?: string; at?: string };
type Line = {
  key: string; kind: "proposal" | "change_order" | "other"; group: string; title: string; cost_code: string;
  budget: number; before: number; this: number; after: number; left: number; percent_after: number | null; held: number; pay_now: number;
  flags: { kind: string; text: string }[]; evidence: Evidence | null; decision: Decision | null; suggest: "ok" | "check" | null; state: "ok" | "check" | "hold" | null;
};
type Sum = { count: number; amount: number };
type Mark = { at?: string; by_name?: string } | null | undefined;
type Draw = {
  bill_id: number; property_id: string; property: string; title: string; number?: string; pay_to?: string; bill_date?: string | null; status?: string; in_review: boolean;
  created_by?: string; gross: number; pay_now: number; held: number; holdback_bill?: string | null;
  lines: Line[]; job: { budget: number; before: number; this: number; after: number; left: number; paid: number; has_proposal: boolean };
  checks: { key: string; ok: boolean | null; text: string }[];
  summary: { ok: Sum; check: Sum; unread: Sum; photo: Sum; log_only: Sum };
  reading: { at?: string; points?: string[]; photos_shown?: number; photos_posted?: number; logs_read?: number; last_log?: string | null; newer_logs?: boolean } | null;
  steps: Record<string, Mark>; draws: { bill_id: number; title: string; number?: string; date?: string | null; status?: string; amount: number; in_review: boolean }[];
  message_for_wes: string; can: { lines: boolean };
};

const usd = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const amt = (n?: number | null) => (n == null || Math.abs(n) < 0.005 ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const et = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) + " ET" : "");
const EYEBROW = "text-[10px] font-semibold uppercase tracking-[0.1em] text-faint";
const COLS = "grid-cols-[18px_minmax(170px,1fr)_78px_78px_86px_92px_minmax(280px,2.1fr)_118px]";

const VERDICT: Record<Verdict, { label: string; tone: string }> = {
  shown: { label: "Shown in a photo", tone: "bg-good-soft text-good" },
  said: { label: "In a log only, no photo", tone: "bg-sunken text-muted" },
  partly: { label: "Partly supported", tone: "bg-high-soft text-high" },
  nothing: { label: "Not in the record", tone: "bg-high-soft text-high" },
  against: { label: "The record says otherwise", tone: "bg-critical-soft text-critical" },
};
const STATUS_TONE = (s?: string) => (s === "Paid" ? "bg-good-soft text-good" : s === "In review" ? "bg-info-soft text-info" : "bg-high-soft text-high");

function StateIcon({ state }: { state: Line["state"] }) {
  if (state === "ok") return <span title="OK" className="mt-[1px] grid place-items-center h-[18px] w-[18px] rounded-full bg-good-soft text-good"><Check size={11} strokeWidth={3} /></span>;
  if (state === "hold") return <span title="On hold" className="mt-[1px] grid place-items-center h-[18px] w-[18px] rounded-full bg-critical-soft text-critical"><Pause size={10} strokeWidth={3} /></span>;
  if (state === "check") return <span title="Needs a look" className="mt-[1px] grid place-items-center h-[18px] w-[18px] rounded-full bg-high-soft text-high"><AlertTriangle size={10} strokeWidth={2.6} /></span>;
  return <span title="Not read yet" className="mt-[1px] grid place-items-center h-[18px] w-[18px] rounded-full bg-sunken text-faint"><Minus size={10} strokeWidth={3} /></span>;
}

/* How far the proposal line is used up: what was billed before, and this bill on top. */
function LineBar({ l }: { l: Line }) {
  if (!l.budget) return null;
  const w = (n: number) => `${Math.min(100, Math.max(0, (n / l.budget) * 100))}%`;
  const over = l.after > l.budget + 0.01;
  return (
    <div className="mt-1 h-1.5 w-full rounded-full bg-sunken overflow-hidden flex" title={`Billed before ${usd(l.before)} · this bill ${usd(l.this)} · budget ${usd(l.budget)}`}>
      <div className="h-full bg-slate-400" style={{ width: w(l.before) }} />
      <div className={cn("h-full", over ? "bg-critical" : "bg-accent")} style={{ width: w(l.this) }} />
    </div>
  );
}

function LineRow({ l, can, onDecide, onZoom }: { l: Line; can: boolean; onDecide: (key: string, body: { decision?: string; note?: string }) => void; onZoom: (id: number) => void }) {
  const [noting, setNoting] = React.useState(false);
  const [note, setNote] = React.useState(l.decision?.note || "");
  const ev = l.evidence;
  const mine = l.decision?.decision || null;
  const saveNote = () => { setNoting(false); if (note.trim() !== (l.decision?.note || "")) onDecide(l.key, { note: note.trim() }); };
  return (
    <div className={cn("grid gap-x-3 px-4 py-2.5 items-start border-b border-line/60 last:border-0 hover:bg-sunken/40 transition-colors", COLS)}>
      <StateIcon state={l.state} />
      <div className="min-w-0">
        <div className="text-[13px] font-medium leading-snug">{l.title}</div>
        <div className="text-[11px] text-faint truncate" title={`${l.group} · ${l.cost_code}`}>{l.group}</div>
      </div>
      <div className="text-right text-[12.5px] tnum text-muted">{amt(l.budget)}</div>
      <div className="text-right text-[12.5px] tnum text-muted">{amt(l.before)}</div>
      <div className="text-right text-[13px] tnum font-semibold">{amt(l.this)}</div>
      <div className="text-[11.5px] tnum">
        <div className="flex justify-between"><span className={cn("font-medium", l.percent_after != null && l.percent_after > 100 && "text-critical")}>{l.percent_after == null ? "—" : `${l.percent_after}%`}</span><span className="text-faint">{l.left > 0.005 ? `${amt(l.left)} left` : l.kind === "other" ? "" : "used up"}</span></div>
        <LineBar l={l} />
      </div>
      <div className="min-w-0">
        {l.flags.map((f) => <div key={f.kind} className="mb-1 text-[12px] text-critical leading-snug">{f.text}</div>)}
        {ev ? (
          <>
            <span className={cn("inline-flex items-center h-[19px] px-1.5 rounded text-[10.5px] font-semibold", VERDICT[ev.verdict].tone)}>{VERDICT[ev.verdict].label}</span>
            <div className="mt-1 text-[12.5px] leading-snug text-fg/90">{ev.basis}</div>
            {ev.photo_ids.length > 0 && (
              <div className="mt-1.5 flex gap-1.5">
                {ev.photo_ids.slice(0, 5).map((id) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={id} src={`/api/brain/site/photo/${id}`} alt="" loading="lazy" onClick={() => onZoom(id)} className="h-11 w-11 object-cover rounded-md border border-line bg-sunken cursor-zoom-in hover:opacity-90" />
                ))}
              </div>
            )}
          </>
        ) : l.flags.length === 0 ? <span className="text-[12px] text-faint">Not checked against the logs and photos yet.</span> : null}
        {l.decision?.note && !noting && <div className="mt-1 text-[12px] text-muted"><span className="text-faint">Note{l.decision.by_name ? ` · ${l.decision.by_name.replace(/ Sir$/, "")}` : ""}:</span> {l.decision.note}</div>}
        {noting && (
          <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} onBlur={saveNote} onKeyDown={(e) => { if (e.key === "Enter") saveNote(); if (e.key === "Escape") { setNote(l.decision?.note || ""); setNoting(false); } }}
            placeholder="Why, or what to ask for" className="mt-1.5 h-7 w-full rounded-md border border-line bg-bg px-2 text-[12.5px] outline-none focus:border-accent" />
        )}
      </div>
      <div className="flex flex-col items-end gap-1">
        {can ? (
          <div className="inline-flex rounded-lg border border-line bg-sunken p-0.5">
            <button type="button" onClick={() => onDecide(l.key, { decision: mine === "ok" ? "" : "ok" })} title={mine === "ok" ? "Take the mark off" : "This line is fine to pay"}
              className={cn("h-6 px-2.5 rounded-md text-[11.5px] font-semibold transition", mine === "ok" ? "bg-good text-white" : "text-muted hover:text-fg")}>OK</button>
            <button type="button" onClick={() => onDecide(l.key, { decision: mine === "hold" ? "" : "hold" })} title={mine === "hold" ? "Take the mark off" : "Do not approve this line yet"}
              className={cn("h-6 px-2.5 rounded-md text-[11.5px] font-semibold transition", mine === "hold" ? "bg-critical text-white" : "text-muted hover:text-fg")}>Hold</button>
          </div>
        ) : <span className="text-[11.5px] text-muted">{mine === "ok" ? "Marked OK" : mine === "hold" ? "On hold" : ""}</span>}
        <div className="text-[10.5px] text-faint text-right leading-tight">
          {mine ? <>{l.decision?.by_name?.replace(/ Sir$/, "")} · {et(l.decision?.at)}</> : l.suggest ? "suggested by the check" : ""}
        </div>
        {can && !noting && <button type="button" onClick={() => setNoting(true)} className="text-[11px] text-accent hover:underline">{l.decision?.note ? "Edit note" : "Add note"}</button>}
      </div>
    </div>
  );
}

function Section({ title, tone, lines, children }: { title: string; tone: string; lines: Line[]; children: React.ReactNode }) {
  if (lines.length === 0) return null;
  return (
    <>
      <div className="flex items-center gap-2 px-4 py-1.5 bg-sunken/70 border-b border-line/70">
        <span className={cn("h-2 w-2 rounded-full", tone)} />
        <span className="text-[11.5px] font-semibold">{title}</span>
        <span className="text-[11.5px] text-muted tnum">{lines.length} line{lines.length === 1 ? "" : "s"} · {usd(lines.reduce((a, l) => a + l.this, 0))}</span>
      </div>
      {children}
    </>
  );
}

export function BillCheckPage({ billId }: { billId: string }) {
  const qc = useQueryClient();
  const key = ["brain", "bill-check", billId];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<Draw>(`/brain/bills/${billId}/check`), placeholderData: (p) => p });
  const [reading, setReading] = React.useState(false);
  const [zoom, setZoom] = React.useState<number | null>(null);
  const [msg, setMsg] = React.useState(false);
  const [ask, setAsk] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  if (q.isError) return <div className="px-6 py-10 text-[13px] text-muted">That bill could not be opened: {errText(q.error)} <Link href="/bills" className="text-accent hover:underline">Back to Bills</Link></div>;
  if (!q.data) return <div className="px-6 py-6 max-w-[1280px] mx-auto space-y-3"><Skeleton className="h-12 w-96" /><Skeleton className="h-28" /><Skeleton className="h-80" /></div>;
  const d = q.data;
  const can = d.can.lines;
  const approved = d.steps?.manju_approved;
  const open = d.lines.filter((l) => l.state === "check" || l.state === "hold");
  const fine = d.lines.filter((l) => l.state === "ok");
  const unread = d.lines.filter((l) => l.state === null);
  const openSum = open.reduce((a, l) => a + l.this, 0);
  const pct = (n: number) => (d.job.budget > 0 ? `${Math.min(100, Math.max(0, (n / d.job.budget) * 100))}%` : "0%");

  const decide = async (lineKey: string, body: { decision?: string; note?: string }) => {
    try { qc.setQueryData(key, await api.post<Draw>(`/brain/bills/${billId}/check/line`, { key: lineKey, ...body })); } catch (e) { toast.error(errText(e)); }
  };
  const read = () => {
    setReading(true);
    api.post<{ job_id: string }>(`/brain/bills/${billId}/check/read`).then(({ job_id }) => {
      subscribeJob(job_id, (e) => {
        if (e.kind === "error") toast.error(e.data?.error || "The check could not be run");
        if (e.kind === "result" && e.data?.none) toast.message("The check could not be run on this bill.");
        if (e.kind === "result" && e.data?.kept) toast.message(e.data.why || "The earlier check was kept.", { duration: 9000 });
      },
        () => { setReading(false); qc.invalidateQueries({ queryKey: key }); });
    }).catch((e) => { toast.error(errText(e)); setReading(false); });
  };
  const approve = async (done: boolean) => {
    setBusy(true);
    try {
      await api.post(`/brain/bills/${d.bill_id}/steps`, { step: "manju_approved", done });
      toast.success(done ? "Bill approved. It now shows as “Manju Sir approved” on the Bills page." : "Approval taken off.");
      qc.invalidateQueries({ queryKey: ["brain"] });
    } catch (e) { toast.error(errText(e)); }
    setBusy(false); setAsk(false);
  };
  const row = (l: Line) => <LineRow key={l.key} l={l} can={can} onDecide={decide} onZoom={setZoom} />;

  return (
    <div className="px-6 py-5 max-w-[1280px] mx-auto">
      {/* ───────── which bill ───────── */}
      <div className="flex items-center gap-1 text-[12px] text-muted">
        <Link href="/bills" className="hover:text-fg hover:underline">Bills</Link><ChevronRight size={12} className="text-faint" />
        <Link href={`/property/${d.property_id}?tab=bills`} className="hover:text-fg hover:underline">{d.property}</Link>
      </div>
      <div className="mt-1.5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <h1 className="text-[20px] font-semibold tracking-tight truncate">{d.title}</h1>
            <span className={cn("inline-flex items-center h-[22px] px-2 rounded-md text-[11.5px] font-semibold whitespace-nowrap", STATUS_TONE(d.status))}>{d.status} in Buildertrend</span>
          </div>
          <div className="mt-0.5 text-[12.5px] text-muted">{d.number}{d.pay_to ? ` · pay to ${d.pay_to.replace(/\s+/g, " ")}` : ""}{d.bill_date ? ` · bill date ${fmtDate(d.bill_date, "d MMM yyyy")}` : ""}{d.created_by ? ` · entered by ${d.created_by}` : ""}</div>
        </div>
        <div className="flex items-center gap-2">
          {d.message_for_wes && <Button size="sm" className="h-8" onClick={() => setMsg(true)}><MessageSquareText size={13} /> Message for Wes</Button>}
          {approved ? (
            <div className="flex items-center gap-2 h-8 pl-2.5 pr-1 rounded-lg bg-good-soft text-good text-[12.5px] font-medium">
              <Check size={13} strokeWidth={3} /> Approved{approved.by_name ? ` by ${approved.by_name.replace(/ Sir$/, "")}` : ""} · {et(approved.at)}
              {can && <button type="button" disabled={busy} onClick={() => approve(false)} className="h-6 px-2 rounded-md text-[11.5px] text-muted hover:text-fg hover:bg-elev">Undo</button>}
            </div>
          ) : can && <Button variant="primary" size="sm" className="h-8" disabled={busy} onClick={() => (open.length || unread.length ? setAsk(true) : approve(true))}><Check size={13} strokeWidth={3} /> Approve bill</Button>}
        </div>
      </div>

      {/* ───────── the request in four figures ───────── */}
      <Card className="mt-4">
        <div className="grid grid-cols-2 lg:grid-cols-4 divide-y lg:divide-y-0 lg:divide-x divide-line/70">
          <div className="px-5 py-3.5">
            <div className={EYEBROW}>This bill asks for</div>
            <div className="mt-1 text-[24px] leading-none font-semibold tracking-tight tnum">{usd(d.gross)}</div>
            <div className="mt-1.5 text-[12px] text-muted tnum">Pay now {usd(d.pay_now)}{d.held > 0 && <> · held back {usd(d.held)}</>}</div>
          </div>
          <div className="px-5 py-3.5">
            <div className={EYEBROW}>Supported by the record</div>
            <div className="mt-1 text-[24px] leading-none font-semibold tracking-tight tnum text-good">{d.reading || fine.length ? usd(d.summary.ok.amount) : "—"}</div>
            <div className="mt-1.5 text-[12px] text-muted">{!d.reading && !fine.length ? "not checked yet" : <>{d.summary.ok.count} line{d.summary.ok.count === 1 ? "" : "s"}{d.summary.log_only.count > 0 && <> · {d.summary.log_only.count} on a log only, no photo</>}</>}</div>
          </div>
          <div className="px-5 py-3.5">
            <div className={EYEBROW}>To check</div>
            <div className={cn("mt-1 text-[24px] leading-none font-semibold tracking-tight tnum", open.length ? "text-high" : "text-fg")}>{d.reading || open.length ? usd(d.summary.check.amount) : "—"}</div>
            <div className="mt-1.5 text-[12px] text-muted">{!d.reading && !open.length ? "not checked yet" : open.length ? <>{open.length} line{open.length === 1 ? "" : "s"} need a look</> : "nothing open"}</div>
          </div>
          <div className="px-5 py-3.5">
            <div className={EYEBROW}>The job after this bill</div>
            <div className="mt-1 flex items-baseline gap-2"><span className="text-[24px] leading-none font-semibold tracking-tight tnum">{d.job.budget > 0 ? `${Math.round((d.job.after / d.job.budget) * 100)}%` : "—"}</span><span className="text-[12px] text-muted">billed</span></div>
            <div className="mt-2 h-1.5 w-full rounded-full bg-sunken overflow-hidden flex"><div className="h-full bg-slate-400" style={{ width: pct(d.job.before) }} /><div className="h-full bg-accent" style={{ width: pct(d.job.this) }} /></div>
            <div className="mt-1.5 text-[12px] text-muted tnum">{usd(d.job.after)} of {usd(d.job.budget)} · {usd(d.job.paid)} paid</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1 px-5 py-2 border-t border-line/70 bg-sunken/40 rounded-b-[var(--radius)]">
          {d.checks.map((c) => (
            <span key={c.key} className={cn("inline-flex items-center gap-1.5 text-[12px]", c.ok === false ? "text-critical font-medium" : c.ok === null ? "text-faint" : "text-muted")}>
              {c.ok === true ? <Check size={12} strokeWidth={3} className="text-good" /> : c.ok === false ? <X size={12} strokeWidth={3} /> : <Minus size={12} />}{c.text}
            </span>
          ))}
        </div>
      </Card>

      {/* ───────── line by line ───────── */}
      <Card className="mt-3 overflow-x-auto">
        <div className="min-w-[1080px]">
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3 pb-2.5">
            <div>
              <div className="text-[13.5px] font-semibold tracking-tight">Line by line</div>
              <div className="text-[11.5px] text-muted">
                {d.reading ? <>Checked against the contractor&apos;s logs and photos {ago(d.reading.at)} · {d.reading.logs_read} logs{d.reading.last_log ? ` to ${fmtDate(d.reading.last_log, "d MMM")}` : ""} · {d.reading.photos_shown} of {d.reading.photos_posted} photos looked at</>
                  : "Not checked against the contractor's logs and photos yet."}
                {d.reading?.newer_logs && <span className="text-high font-medium"> · a newer log has come in since</span>}
              </div>
            </div>
            {can && <Button size="sm" variant={d.reading ? "secondary" : "primary"} disabled={reading} onClick={read}><RefreshCw size={12} className={cn(reading && "animate-spin")} /> {reading ? "Reading the logs and photos…" : d.reading ? "Check again" : "Check against logs and photos"}</Button>}
          </div>
          <div className={cn("grid gap-x-3 px-4 py-1.5 border-y border-line text-[9.5px] font-semibold uppercase tracking-[0.09em] text-faint items-end", COLS)}>
            <div /><div>Line of the proposal</div><div className="text-right">Budget</div><div className="text-right">Billed before</div><div className="text-right">This bill</div><div>After this bill</div><div>What the record shows</div><div className="text-right">Decision</div>
          </div>
          {d.lines.length === 0 && <div className="px-4 py-8 text-center text-[13px] text-muted">This bill&apos;s lines have not been read from Buildertrend yet.</div>}
          <Section title="To check" tone="bg-high" lines={open}>{open.map(row)}</Section>
          <Section title="Not checked yet" tone="bg-line-strong" lines={unread}>{unread.map(row)}</Section>
          <Section title="Supported" tone="bg-good" lines={fine}>{fine.map(row)}</Section>
        </div>
      </Card>

      {/* ───────── also in the record · the other bills of the job ───────── */}
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card className="px-4 py-3">
          <div className="text-[13px] font-semibold tracking-tight">Also in the record</div>
          {d.reading?.points?.length ? (
            <ol className="mt-1.5 space-y-1.5">
              {d.reading.points.map((p, i) => <li key={i} className="flex gap-2 text-[12.5px] leading-snug"><span className="mt-[1px] grid place-items-center h-[17px] w-[17px] shrink-0 rounded-full bg-sunken text-[10.5px] font-semibold text-muted">{i + 1}</span><span>{p}</span></li>)}
            </ol>
          ) : <div className="mt-1 text-[12.5px] text-muted">{d.reading ? "Nothing else in the logs bears on this bill." : "Run the check to see what else the logs say."}</div>}
        </Card>
        <Card className="px-4 py-3">
          <div className="text-[13px] font-semibold tracking-tight">Bills on this job</div>
          <div className="mt-1.5 -mx-1.5">
            {d.draws.map((b) => (
              <Link key={b.bill_id} href={`/bills/${b.bill_id}`} className={cn("flex items-center gap-2 px-1.5 py-1 rounded-md text-[12.5px] hover:bg-sunken", b.bill_id === d.bill_id && "bg-accent-soft/60")}>
                <span className="min-w-0 flex-1 truncate font-medium">{b.title}</span>
                <span className="text-faint tnum">{fmtDate(b.date, "d MMM")}</span>
                <span className="w-[86px] text-right tnum">{usd(b.amount)}</span>
                <span className={cn("w-[124px] shrink-0 text-center h-[19px] leading-[19px] rounded text-[10.5px] font-semibold", STATUS_TONE(b.status))}>{b.status}</span>
              </Link>
            ))}
          </div>
        </Card>
      </div>
      <p className="mt-3 text-[11px] text-faint leading-relaxed max-w-4xl">
        The amounts are worked out from the proposal and the bills in Buildertrend. “What the record shows” is an AI reading of the contractor&apos;s own daily logs and photos. It is not an inspection, and it only suggests:
        a person&apos;s OK or Hold on a line is what stands. The check is made by itself after every read of Buildertrend, for each bill not yet approved.
        Approving the bill ticks “Manju Sir approved” on the Bills page; no email is sent by it.
      </p>

      <Dialog open={zoom != null} onOpenChange={(o) => { if (!o) setZoom(null); }}>
        <DialogContent wide title="Photo from the contractor's log">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {zoom != null && <img src={`/api/brain/site/photo/${zoom}`} alt="" className="max-h-[78vh] mx-auto rounded-lg" />}
        </DialogContent>
      </Dialog>

      <Dialog open={msg} onOpenChange={setMsg}>
        <DialogContent wide title="Message for Wes" description="The lines not accepted, and what to ask for. Nothing is sent from here: copy it, and it goes out once Rakesh Sir approves.">
          <pre className="whitespace-pre-wrap rounded-xl border border-line bg-sunken/50 px-3.5 py-3 text-[12.5px] leading-relaxed font-sans max-h-[52vh] overflow-y-auto">{d.message_for_wes}</pre>
          <div className="mt-3 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setMsg(false)}>Close</Button>
            <Button variant="primary" onClick={() => { navigator.clipboard.writeText(d.message_for_wes).then(() => toast.success("Copied.")).catch(() => toast.error("Could not copy. Select the text and copy it.")); }}><Copy size={13} /> Copy</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={ask} onOpenChange={(o) => { if (!o && !busy) setAsk(false); }}>
        <DialogContent title="Approve the whole bill?">
          <p className="text-[13px] leading-relaxed">
            {open.length > 0 && <><b className="font-semibold">{open.length} line{open.length === 1 ? "" : "s"}, {usd(openSum)}</b>, {open.length === 1 ? "is" : "are"} still to check or on hold. </>}
            {unread.length > 0 && <>{unread.length} line{unread.length === 1 ? " has" : "s have"} not been checked against the logs and photos. </>}
            Approving marks the whole bill of {usd(d.gross)} as approved.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" disabled={busy} onClick={() => setAsk(false)}>Cancel</Button>
            <Button variant="primary" disabled={busy} onClick={() => approve(true)}>{busy ? "Approving…" : "Approve anyway"}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
