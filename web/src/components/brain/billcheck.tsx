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
   as on the Bills page: it asks first, and on yes an email tells JP Sir the bill is
   approved. "Approve and mark ready to pay" also ticks "Team reviewed" and "Ready to
   pay": it asks first, and on yes the ready-to-pay email to JP Sir goes at once (that
   one email, not the approval email as well), exactly as from the Bills page.

   The look follows components/ds.tsx (2026-11-26). */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, Check, CircleCheck, CircleMinus, CircleX, Copy, ExternalLink, FileText, MessageSquareText, Minus, Pause, Receipt, RefreshCw } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Dialog, DialogContent, Skeleton } from "@/components/ui";
import { Block, Btn, Figure, Figures, Label, PageHead, Tag, TH, type Tone } from "@/components/ds";
import { PropertyMark, usePropertyColours } from "@/components/property-mark";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText } from "@/lib/brain";
import { copyText } from "@/components/email-draft";

type Verdict = "shown" | "said" | "partly" | "nothing" | "against";
type Evidence = { verdict: Verdict; basis: string; log_dates: string[]; photo_ids: number[]; ask: string };
type Decision = { decision?: "ok" | "hold" | null; note?: string; by_name?: string; at?: string };
type Line = {
  key: string; kind: "proposal" | "change_order" | "other"; group: string; title: string; cost_code: string;
  budget: number; before: number; this: number; after: number; left: number; percent_after: number | null; held: number; pay_now: number;
  flags: { kind: string; text: string }[]; evidence: Evidence | null; decision: Decision | null; suggest: "ok" | "check" | null; state: "ok" | "check" | "hold" | null;
  /* What Buildertrend's Change Orders list says of the change order this line is billed under. */
  change_order?: { number: string; title: string; status: string; approved: boolean; status_text: string; builder_cost: number | null } | null;
};
type Sum = { count: number; amount: number };
type Seen = "installed" | "on_site" | "covered" | "similar" | "log_only" | "not_seen" | "not_a_material";
type PaperItem = { item: string; qty: number | null; unit_price: number | null; amount: number | null; seen?: Seen | null; where?: string; photo_ids?: number[] };
/* Where one material of a receipt stands in the contractor's logs and photos. */
const SEEN: Record<Seen, { label: string; tone: Tone }> = {
  installed: { label: "Seen installed", tone: "green" },
  on_site: { label: "On site, not installed", tone: "accent" },
  covered: { label: "Likely used, now covered", tone: "accent" },
  similar: { label: "Similar material seen", tone: "gray" },
  log_only: { label: "In a log only", tone: "gray" },
  not_seen: { label: "Not seen yet", tone: "amber" },
  not_a_material: { label: "", tone: "gray" },
};
const SEEN_ORDER: Seen[] = ["installed", "on_site", "covered", "similar", "log_only", "not_seen"];
type Paper = { file_id: number; title: string; kind: "receipt" | "invoice" | "payment_proof" | "photo" | "other"; says: string; total: number | null; items?: PaperItem[] };
const PAPER_KIND: Record<Paper["kind"], string> = { receipt: "Receipt", invoice: "Invoice", payment_proof: "Proof of payment", photo: "Photo", other: "File" };
type Mark = { at?: string; by_name?: string } | null | undefined;
type Draw = {
  bill_id: number; property_id: string; property: string; title: string; number?: string; pay_to?: string; bill_date?: string | null; status?: string; in_review: boolean;
  created_by?: string; gross: number; pay_now: number; held: number; holdback_bill?: string | null;
  lines: Line[]; job: { budget: number; before: number; this: number; after: number; left: number; paid: number; has_proposal: boolean };
  checks: { key: string; ok: boolean | null; text: string }[];
  summary: { ok: Sum; check: Sum; unread: Sum; photo: Sum; log_only: Sum };
  reading: { at?: string; points?: string[]; photos_shown?: number; photos_posted?: number; logs_read?: number; last_log?: string | null; newer_logs?: boolean; papers?: Paper[]; papers_unread?: string[] } | null;
  steps: Record<string, Mark>; draws: { bill_id: number; title: string; number?: string; date?: string | null; status?: string; amount: number; in_review: boolean }[];
  message_for_wes: string; can: { lines: boolean };
  mail_to?: Record<string, { to: string[]; cc: string[] }>; mail?: Record<string, MailState>;
};
type MailState = { status?: string; sent_at?: string | null; to?: string[]; error?: string | null };
type MailResult = { now?: "sent" | "already_sent" | "still_sending" | "not_accepted" | "failed"; status?: string; why?: string };
/* The three ticks "ready to pay" needs, in the order RKB makes them. */
const TO_READY = ["manju_approved", "team_reviewed", "ready_to_pay"] as const;
const names = (xs?: string[]) => (xs || []).join(" and ");

const usd = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const amt = (n?: number | null) => (n == null || Math.abs(n) < 0.005 ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const et = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) + " ET" : "");
/* A cell of the line-by-line table: the text of a line can run to several lines, so the row grows. */
const CELL = "px-2 py-2 align-top text-[13px] leading-normal border-b border-r border-line/80 last:border-r-0";

const VERDICT: Record<Verdict, { label: string; tone: Tone }> = {
  shown: { label: "Shown in a photo", tone: "green" },
  said: { label: "In a log only, no photo", tone: "gray" },
  partly: { label: "Partly supported", tone: "amber" },
  nothing: { label: "Not in the logs or photos", tone: "amber" },
  against: { label: "The record says otherwise", tone: "red" },
};
const STATUS_TONE = (s?: string): Tone => (s === "Paid" ? "green" : s === "In review" ? "gray" : "amber");

function StateIcon({ state }: { state: Line["state"] }) {
  if (state === "ok") return <span title="OK" className="mt-0.5 grid place-items-center h-4 w-4 shrink-0 rounded-full bg-good-soft text-good"><Check size={10} strokeWidth={3} /></span>;
  if (state === "hold") return <span title="On hold" className="mt-0.5 grid place-items-center h-4 w-4 shrink-0 rounded-full bg-critical-soft text-critical"><Pause size={9} strokeWidth={3} /></span>;
  if (state === "check") return <span title="Needs a look" className="mt-0.5 grid place-items-center h-4 w-4 shrink-0 rounded-full bg-high-soft text-high"><AlertTriangle size={9} strokeWidth={2.6} /></span>;
  return <span title="Not read yet" className="mt-0.5 grid place-items-center h-4 w-4 shrink-0 rounded-full bg-sunken text-faint"><Minus size={9} strokeWidth={3} /></span>;
}

/* How far the proposal line is used up: what was billed before, and this bill on top. */
function LineBar({ l }: { l: Line }) {
  if (!l.budget) return null;
  const w = (n: number) => `${Math.min(100, Math.max(0, (n / l.budget) * 100))}%`;
  const over = l.after > l.budget + 0.01;
  return (
    <div className="mt-1.5 h-1 w-full rounded-full bg-line overflow-hidden flex" title={`Billed before ${usd(l.before)} · this bill ${usd(l.this)} · budget ${usd(l.budget)}`}>
      <div className="h-full bg-faint/70" style={{ width: w(l.before) }} />
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
    <tr className="hover:bg-hover/50 transition-colors">
      <td className={cn(CELL, "pl-4")}>
        <div className="flex gap-2">
          <StateIcon state={l.state} />
          <div className="min-w-0">
            <div className="font-medium text-fg">{l.title}</div>
            <div className="text-[12px] text-faint truncate" title={`${l.group} · ${l.cost_code}`}>{l.group}</div>
          </div>
        </div>
      </td>
      <td className={cn(CELL, "text-right tnum text-muted")}>{amt(l.budget)}</td>
      <td className={cn(CELL, "text-right tnum text-muted")}>{amt(l.before)}</td>
      <td className={cn(CELL, "text-right tnum font-medium text-fg")}>{amt(l.this)}</td>
      <td className={cn(CELL, "tnum")}>
        <div className="flex justify-between gap-2 text-[12px]"><span className={cn("font-medium", l.percent_after != null && l.percent_after > 100 ? "text-critical" : "text-fg")}>{l.percent_after == null ? "—" : `${l.percent_after}%`}</span><span className="text-faint">{l.left > 0.005 ? `${amt(l.left)} left` : l.kind === "other" ? "" : "used up"}</span></div>
        <LineBar l={l} />
      </td>
      <td className={CELL}>
        {l.change_order?.approved && <div className="mb-1 text-[12px] text-good">Change order {l.change_order.number} · {l.change_order.status_text} (Buildertrend) · builder cost {usd(l.change_order.builder_cost)}</div>}
        {l.flags.map((f) => <div key={f.kind} className="mb-1 text-[12px] text-critical">{f.text}</div>)}
        {ev ? (
          <>
            <Tag tone={VERDICT[ev.verdict].tone}>{VERDICT[ev.verdict].label}</Tag>
            <div className="mt-1 text-muted">{ev.basis}</div>
            {ev.photo_ids.length > 0 && (
              <div className="mt-2 flex gap-1">
                {ev.photo_ids.slice(0, 5).map((id) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={id} src={`/api/brain/site/photo/${id}`} alt="" loading="lazy" onClick={() => onZoom(id)} className="h-10 w-10 object-cover rounded border border-line bg-sunken cursor-zoom-in hover:opacity-90" />
                ))}
              </div>
            )}
          </>
        ) : l.flags.length === 0 ? <span className="text-[12px] text-faint">Not checked against the logs and photos yet.</span> : null}
        {l.decision?.note && !noting && <div className="mt-1 text-[12px] text-muted"><span className="text-faint">Note{l.decision.by_name ? ` · ${l.decision.by_name.replace(/ Sir$/, "")}` : ""}:</span> {l.decision.note}</div>}
        {noting && (
          <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} onBlur={saveNote} onKeyDown={(e) => { if (e.key === "Enter") saveNote(); if (e.key === "Escape") { setNote(l.decision?.note || ""); setNoting(false); } }}
            placeholder="Why, or what to ask for" className="mt-2 h-8 w-full rounded-lg border border-line-strong/70 bg-elev px-2 text-[13px] outline-none focus:border-accent" />
        )}
      </td>
      <td className={cn(CELL, "text-right")}>
        {can ? (
          <div className="inline-flex gap-1">
            <Btn size="sm" onClick={() => onDecide(l.key, { decision: mine === "ok" ? "" : "ok" })} title={mine === "ok" ? "Take the mark off" : "This line is fine to pay"}
              className={cn(mine === "ok" && "bg-good border-good text-white hover:bg-good hover:text-white")}>OK</Btn>
            <Btn size="sm" onClick={() => onDecide(l.key, { decision: mine === "hold" ? "" : "hold" })} title={mine === "hold" ? "Take the mark off" : "Do not approve this line yet"}
              className={cn(mine === "hold" && "bg-critical border-critical text-white hover:bg-critical hover:text-white")}>Hold</Btn>
          </div>
        ) : <span className="text-[12px] text-muted">{mine === "ok" ? "Marked OK" : mine === "hold" ? "On hold" : ""}</span>}
        <div className="mt-1 text-[11px] text-faint leading-tight">
          {mine ? <>{l.decision?.by_name?.replace(/ Sir$/, "")} · {et(l.decision?.at)}</> : l.suggest ? "suggested by the check" : ""}
        </div>
        {can && !noting && <button type="button" onClick={() => setNoting(true)} className="mt-0.5 text-[12px] text-accent hover:underline">{l.decision?.note ? "Edit note" : "Add note"}</button>}
      </td>
    </tr>
  );
}

function Section({ title, tone, lines, children }: { title: string; tone: string; lines: Line[]; children: React.ReactNode }) {
  if (lines.length === 0) return null;
  return (
    <>
      <tr>
        <td colSpan={7} className="h-8 px-4 bg-subtle border-b border-line">
          <Label className={tone}>{title}</Label>
          <span className="ml-2 text-[12px] text-faint tnum">{lines.length} line{lines.length === 1 ? "" : "s"} · {usd(lines.reduce((a, l) => a + l.this, 0))}</span>
        </td>
      </tr>
      {children}
    </>
  );
}

/* The papers entered with the bill in Buildertrend (receipt, invoice, photos), as the check read them:
   what each says, whether its total is the bill's, and every line it lists. */
function Papers({ papers, unread, gross, onZoom }: { papers: Paper[]; unread: string[]; gross: number; onZoom: (id: number) => void }) {
  const [open, setOpen] = React.useState<number | null>(null);
  if (!papers.length && !unread.length) return null;
  return (
    <Block className="mt-4" title="Attached to this bill" note="The papers entered with the bill in Buildertrend, as the check read them. Click a name to open the file.">
      <ul className="divide-y divide-line">
        {papers.map((p) => {
          const priced = p.total != null && (p.kind === "receipt" || p.kind === "invoice");
          const same = priced && Math.abs((p.total as number) - gross) < 0.02;
          const items = p.items || [];
          const word = PAPER_KIND[p.kind].toLowerCase();
          const materials = items.filter((it) => it.seen && it.seen !== "not_a_material");
          const tally = SEEN_ORDER.map((k) => ({ k, n: materials.filter((it) => it.seen === k).length })).filter((x) => x.n > 0);
          return (
            <li key={p.file_id} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <FileText size={16} strokeWidth={1.6} className="shrink-0 text-faint" />
                <a href={`/api/brain/bill-files/${p.file_id}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] font-medium text-fg hover:underline">{p.title}<ExternalLink size={12} className="text-faint" /></a>
                <Tag>{PAPER_KIND[p.kind]}</Tag>
                {p.total != null && (
                  <span className="ml-auto inline-flex items-center gap-2 text-[13px] font-medium tnum">
                    {usd(p.total)}
                    {priced && <Tag tone={same ? "green" : "red"}>{same ? "same as the bill" : `the bill asks ${usd(gross)}`}</Tag>}
                  </span>
                )}
              </div>
              <p className="mt-1.5 max-w-4xl text-[13px] leading-relaxed text-muted">{p.says}</p>
              {materials.length > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px]">
                  <span className="font-medium text-fg">The {materials.length} material{materials.length === 1 ? "" : "s"} on this {word}, looked for in the logs and photos:</span>
                  {tally.map(({ k, n }) => <Tag key={k} tone={SEEN[k].tone}>{n} {SEEN[k].label.toLowerCase()}</Tag>)}
                </div>
              )}
              {items.length > 0 && (
                <button type="button" onClick={() => setOpen(open === p.file_id ? null : p.file_id)} className="mt-1.5 text-[12px] font-medium text-accent hover:underline">
                  {open === p.file_id ? "Hide" : "Show"} the {items.length} line{items.length === 1 ? "" : "s"} of this {word}
                </button>
              )}
              {open === p.file_id && (
                <div className="mt-2 overflow-x-auto rounded-lg border border-line">
                  <table className="w-full border-collapse text-[13px]">
                    <thead>
                      <tr>
                        <th className={TH}>Item, as written on the {word}</th><th className={cn(TH, "text-right")}>Qty</th>
                        <th className={cn(TH, "text-right")}>Unit price</th><th className={cn(TH, "text-right")}>Amount</th>
                        {materials.length > 0 && <th className={TH}>On the property (logs and photos)</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((it, i) => (
                        <tr key={i} className="align-top [&>td]:border-b [&>td]:border-line/80 last:[&>td]:border-b-0">
                          <td className="px-2 py-1.5 text-muted">{it.item}</td><td className="px-2 py-1.5 text-right tnum text-muted">{it.qty ?? "—"}</td>
                          <td className="px-2 py-1.5 text-right tnum text-muted">{it.unit_price == null ? "—" : usd(it.unit_price)}</td><td className="px-2 py-1.5 text-right tnum text-fg">{it.amount == null ? "—" : usd(it.amount)}</td>
                          {materials.length > 0 && (
                            <td className="px-2 py-1.5 min-w-[260px]">
                              {it.seen && it.seen !== "not_a_material" && (
                                <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
                                  <Tag tone={SEEN[it.seen].tone}>{SEEN[it.seen].label}</Tag>
                                  {(it.photo_ids || []).map((id) => (
                                    <button key={id} type="button" onClick={() => onZoom(id)} title="Open the photo" className="h-8 w-8 overflow-hidden rounded border border-line hover:border-accent">
                                      {/* eslint-disable-next-line @next/next/no-img-element */}
                                      <img src={`/api/brain/site/photo/${id}`} alt="" loading="lazy" className="h-full w-full object-cover" />
                                    </button>
                                  ))}
                                  {it.where && <span className="basis-full text-[12px] leading-snug text-muted">{it.where}</span>}
                                </div>
                              )}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                    {p.total != null && !items.some((it) => /total/i.test(it.item) && it.amount === p.total) && <tfoot><tr className="border-t border-line bg-subtle font-medium"><td className="px-2 py-1.5" colSpan={3}>Total on the {word}</td><td className="px-2 py-1.5 text-right tnum">{usd(p.total)}</td>{materials.length > 0 && <td />}</tr></tfoot>}
                  </table>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {unread.length > 0 && <div className="border-t border-line px-4 py-2 text-[12px] text-muted">Attached in Buildertrend but not read by the check: {unread.join("; ")}</div>}
    </Block>
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
  const [askPay, setAskPay] = React.useState(false);
  const colourOf = usePropertyColours();

  const head = <Receipt size={16} strokeWidth={1.6} />;
  if (q.isError) return <div><PageHead icon={head} crumbs={[{ label: "Bills", href: "/bills" }, { label: "Bill" }]} /><div className="px-6 py-8 text-[13px] text-muted">That bill could not be opened: {errText(q.error)} <Link href="/bills" className="text-accent hover:underline">Back to Bills</Link></div></div>;
  if (!q.data) return <div><PageHead icon={head} crumbs={[{ label: "Bills", href: "/bills" }, { label: "…" }]} /><div className="px-6 py-4 max-w-[1280px] space-y-4"><Skeleton className="h-8 w-96" /><Skeleton className="h-24" /><Skeleton className="h-72" /></div></div>;
  const d = q.data;
  const can = d.can.lines;
  const approved = d.steps?.manju_approved;
  const ready = d.steps?.ready_to_pay;
  const readyMail = d.mail?.ready_to_pay;
  const jp = names(d.mail_to?.ready_to_pay?.to) || "JP Sir";
  const approvedTo = names(d.mail_to?.manju_approved?.to) || "JP Sir";
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
      const res = await api.post<{ mail_result?: MailResult | null }>(`/brain/bills/${d.bill_id}/steps`, { step: "manju_approved", done });
      const m = res.mail_result?.now;
      if (!done) toast.success("Approval taken off.");
      else if (m === "sent") toast.success(`Bill approved. Email sent to ${approvedTo}.`);
      else if (m === "already_sent") toast.message(`Bill approved. The email to ${approvedTo} had already been sent.`);
      else if (m === "still_sending") toast.message(`Bill approved. The email to ${approvedTo} is being sent.`);
      else if (m === "not_accepted") toast.warning("Bill approved, but the email has NOT gone yet: Microsoft's mail service did not accept it just now. It is kept and retried every few minutes until it goes.", { duration: 12000 });
      else toast.message(`Bill approved. No email was sent${res.mail_result?.why ? `: ${res.mail_result.why}` : "."}`);
      qc.invalidateQueries({ queryKey: ["brain"] });
    } catch (e) { toast.error(errText(e)); }
    setBusy(false); setAsk(false);
  };
  /* Tick whatever is still missing of approved → team reviewed → ready to pay. The last tick sends the email to JP Sir. */
  const markReady = async () => {
    setBusy(true);
    try {
      let last: { mail_result?: MailResult | null } = {};
      for (const step of TO_READY) if (!d.steps?.[step]) last = await api.post<{ mail_result?: MailResult | null }>(`/brain/bills/${d.bill_id}/steps`, { step, done: true, quiet: step !== "ready_to_pay" });
      const m = last.mail_result?.now;
      if (m === "sent") toast.success(`Marked ready to pay. Email sent to ${jp}.`);
      else if (m === "already_sent") toast.message(`Marked ready to pay. This email had already been sent to ${jp}.`);
      else if (m === "still_sending") toast.message(`Marked ready to pay. The email to ${jp} is being sent.`);
      else if (m === "not_accepted") toast.warning("Marked ready to pay, but the email has NOT gone yet: Microsoft's mail service did not accept it just now. It is kept and retried every few minutes until it goes.", { duration: 12000 });
      else toast.error(`Marked ready to pay, but the email was not sent${last.mail_result?.why ? `: ${last.mail_result.why}` : "."}`);
    } catch (e) { toast.error(errText(e)); }
    qc.invalidateQueries({ queryKey: ["brain"] });
    setBusy(false); setAskPay(false);
  };
  const row = (l: Line) => <LineRow key={l.key} l={l} can={can} onDecide={decide} onZoom={setZoom} />;

  return (
    <div>
      {/* ───────── where this is, and what can be done ───────── */}
      <PageHead icon={head} crumbs={[{ label: "Bills", href: "/bills" }, { label: <span className="inline-flex items-center gap-1.5"><PropertyMark label={d.property} colour={colourOf(d.property_id)} />{d.property}</span>, href: `/property/${d.property_id}?tab=bills` }, { label: d.title }]}>
        {d.message_for_wes && <Btn className="shrink-0" onClick={() => setMsg(true)}><MessageSquareText size={14} strokeWidth={1.6} /> Message for Wes</Btn>}
        {can && !ready && d.pay_now > 0 && <Btn className="shrink-0" disabled={busy} onClick={() => setAskPay(true)}>{approved ? "Mark ready to pay" : "Approve and mark ready to pay"}</Btn>}
        {approved ? (
          <span className="shrink-0 inline-flex items-center gap-1.5 h-8 pl-3 pr-1.5 rounded-lg bg-good-soft text-good text-[13px] font-medium whitespace-nowrap">
            <Check size={14} strokeWidth={2.4} /> Approved{approved.by_name ? ` by ${approved.by_name.replace(/ Sir$/, "")}` : ""} · {et(approved.at)}
            {ready && <span>· Ready to pay {et(ready.at)}</span>}
            {can && !ready ? <button type="button" disabled={busy} onClick={() => approve(false)} className="h-6 px-2 rounded text-[12px] text-muted hover:text-fg hover:bg-elev">Undo</button> : <span className="w-1.5" />}
          </span>
        ) : can && <Btn variant="accent" className="shrink-0" disabled={busy} onClick={() => setAsk(true)}><Check size={14} strokeWidth={2.4} /> Approve bill</Btn>}
      </PageHead>

      <div className="px-6 pt-4 pb-8 max-w-[1280px]">
        {/* ───────── which bill ───────── */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-[20px] leading-snug font-semibold">{d.title}</h1>
          <Tag tone={STATUS_TONE(d.status)}>{d.status} in Buildertrend</Tag>
        </div>
        <div className="mt-1 text-[13px] text-faint">{d.number}{d.pay_to ? ` · pay to ${d.pay_to.replace(/\s+/g, " ")}` : ""}{d.bill_date ? ` · bill date ${fmtDate(d.bill_date, "d MMM yyyy")}` : ""}{d.created_by ? ` · entered by ${d.created_by}` : ""}</div>
        {ready && readyMail && (
          <div className={cn("mt-1 text-[12px]", readyMail.status === "sent" || readyMail.status === "replied" ? "text-good" : readyMail.status === "queued" && !readyMail.error ? "text-high" : "text-critical")}>
            {readyMail.status === "sent" || readyMail.status === "replied" ? `Ready-to-pay email sent to ${(readyMail.to || []).join(", ")} ${et(readyMail.sent_at)}`
              : readyMail.status === "queued" && !readyMail.error ? "The ready-to-pay email is being sent"
                : readyMail.status === "queued" ? "Ready-to-pay email NOT gone yet: Microsoft's mail service did not accept it; retrying every few minutes"
                  : readyMail.status ? "The ready-to-pay email could not be sent" : ""}
          </div>
        )}

        {/* ───────── the request in four figures, and the accountant's checks ───────── */}
        <Block className="mt-4">
          <Figures>
            <Figure label="This bill asks for" value={usd(d.gross)} sub={<span className="tnum">Pay now {usd(d.pay_now)}{d.held > 0 && <> · held back {usd(d.held)}</>}</span>} />
            <Figure label="Supported by the record" value={d.reading || fine.length ? usd(d.summary.ok.amount) : "—"}
              sub={!d.reading && !fine.length ? "not checked yet" : <>{d.summary.ok.count} line{d.summary.ok.count === 1 ? "" : "s"}{d.summary.log_only.count > 0 && <> · {d.summary.log_only.count} on a log only, no photo</>}</>} />
            <Figure label="To check" tone={open.length ? "text-high" : undefined} value={d.reading || open.length ? usd(d.summary.check.amount) : "—"}
              sub={!d.reading && !open.length ? "not checked yet" : open.length ? <>{open.length} line{open.length === 1 ? "" : "s"} need{open.length === 1 ? "s" : ""} a look</> : "nothing open"} />
            <Figure label="The job after this bill" value={<>{d.job.budget > 0 ? `${Math.round((d.job.after / d.job.budget) * 100)}%` : "—"} <span className="text-[12px] font-normal text-faint">billed</span></>}
              sub={<span className="tnum">{usd(d.job.after)} of {usd(d.job.budget)} · {usd(d.job.paid)} paid</span>}>
              <div className="mt-2 mb-1.5 h-1 w-full rounded-full bg-line overflow-hidden flex"><div className="h-full bg-faint/70" style={{ width: pct(d.job.before) }} /><div className="h-full bg-accent" style={{ width: pct(d.job.this) }} /></div>
            </Figure>
          </Figures>
          <div className="flex flex-wrap gap-x-6 gap-y-0.5 px-4 py-2">
            {d.checks.map((c) => (
              <span key={c.key} className={cn("inline-flex items-center gap-1.5 min-h-6 text-[13px]", c.ok === false ? "text-critical font-medium" : c.ok === null ? "text-faint" : "text-muted")}>
                {c.ok === true ? <CircleCheck size={14} strokeWidth={1.6} className="text-good shrink-0" /> : c.ok === false ? <CircleX size={14} strokeWidth={1.6} className="shrink-0" /> : <CircleMinus size={14} strokeWidth={1.6} className="shrink-0" />}{c.text}
              </span>
            ))}
          </div>
        </Block>

        <Papers papers={d.reading?.papers || []} unread={d.reading?.papers_unread || []} gross={d.gross} onZoom={setZoom} />

        {/* ───────── line by line ───────── */}
        <Block className="mt-4" title="Line by line"
          note={<>
            {d.reading ? <>checked against {d.reading.papers?.length ? `the ${d.reading.papers.length === 1 ? "attached " + PAPER_KIND[d.reading.papers[0].kind].toLowerCase() : d.reading.papers.length + " attached papers"}, ` : ""}the contractor&apos;s logs and photos {ago(d.reading.at)} · {d.reading.logs_read} logs{d.reading.last_log ? ` to ${fmtDate(d.reading.last_log, "d MMM")}` : ""} · {d.reading.photos_shown} of {d.reading.photos_posted} photos looked at</>
              : "not checked against the contractor's logs and photos yet"}
            {d.reading?.newer_logs && <span className="text-high font-medium"> · a newer log has come in since</span>}
          </>}
          right={can && <Btn size="sm" variant={d.reading ? "outline" : "accent"} disabled={reading} onClick={read}><RefreshCw size={12} className={cn(reading && "animate-spin")} /> {reading ? "Reading the logs and photos…" : d.reading ? "Check again" : "Check against logs and photos"}</Btn>}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1040px] table-fixed border-collapse">
              <colgroup><col style={{ width: 232 }} /><col style={{ width: 96 }} /><col style={{ width: 108 }} /><col style={{ width: 96 }} /><col style={{ width: 132 }} /><col /><col style={{ width: 132 }} /></colgroup>
              <thead>
                <tr><th className={cn(TH, "pl-4")}>Line of the proposal</th><th className={cn(TH, "text-right")}>Budget</th><th className={cn(TH, "text-right")}>Billed before</th><th className={cn(TH, "text-right")}>This bill</th><th className={TH}>After this bill</th><th className={TH}>What the record shows</th><th className={cn(TH, "text-right")}>Decision</th></tr>
              </thead>
              <tbody className="[&>tr:last-child>td]:border-b-0">
                {d.lines.length === 0 && <tr><td colSpan={7} className="h-8 px-4 text-[13px] text-faint">This bill&apos;s lines have not been read from Buildertrend yet.</td></tr>}
                <Section title="To check" tone="text-high" lines={open}>{open.map(row)}</Section>
                <Section title="Not checked yet" tone="text-faint" lines={unread}>{unread.map(row)}</Section>
                <Section title="Supported" tone="text-good" lines={fine}>{fine.map(row)}</Section>
              </tbody>
            </table>
          </div>
        </Block>

        {/* ───────── also in the record · the other bills of the job ───────── */}
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] items-start">
          <Block title="Also in the record">
            {d.reading?.points?.length ? (
              <ol className="divide-y divide-line">
                {d.reading.points.map((p, i) => <li key={i} className="flex gap-2 px-4 py-2 text-[13px] leading-relaxed text-muted"><span className="w-3 shrink-0 text-faint tnum">{i + 1}</span><span>{p}</span></li>)}
              </ol>
            ) : <div className="px-4 py-2 text-[13px] text-faint">{d.reading ? "Nothing else in the logs bears on this bill." : "Run the check to see what else the logs say."}</div>}
          </Block>
          <Block title="Bills on this job">
            <div className="divide-y divide-line">
              {d.draws.map((b) => (
                <Link key={b.bill_id} href={`/bills/${b.bill_id}`} className={cn("h-8 px-4 flex items-center gap-2 text-[13px] hover:bg-hover", b.bill_id === d.bill_id && "bg-accent-soft hover:bg-accent-soft")}>
                  <span className={cn("min-w-0 flex-1 truncate text-fg", b.bill_id === d.bill_id && "font-medium")}>{b.title}</span>
                  <span className="text-[12px] text-faint tnum">{fmtDate(b.date, "d MMM")}</span>
                  <span className="w-[84px] text-right tnum text-muted">{usd(b.amount)}</span>
                  <Tag tone={STATUS_TONE(b.status)} className="w-[124px] justify-center">{b.status}</Tag>
                </Link>
              ))}
            </div>
          </Block>
        </div>
        <p className="mt-4 text-[12px] text-faint leading-relaxed max-w-4xl">
          The amounts are worked out from the proposal and the bills in Buildertrend. “What the record shows” and “On the property” are an AI reading of the papers attached to the bill and the contractor&apos;s own daily logs and photos. It is not an inspection, and it only suggests:
          a person&apos;s OK or Hold on a line is what stands. The check is made by itself after every read of Buildertrend, for each bill not yet approved.
          “Approve bill” ticks “Manju Sir approved” on the Bills page and emails JP Sir that it is approved. “Approve and mark ready to pay” also ticks “Team reviewed” and “Ready to pay”, and emails JP Sir at once.
        </p>
      </div>

      <Dialog open={zoom != null} onOpenChange={(o) => { if (!o) setZoom(null); }}>
        <DialogContent wide className="rounded-lg p-5" title="Photo from the contractor's log">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {zoom != null && <img src={`/api/brain/site/photo/${zoom}`} alt="" className="max-h-[78vh] mx-auto rounded-lg" />}
        </DialogContent>
      </Dialog>

      <Dialog open={msg} onOpenChange={setMsg}>
        <DialogContent wide className="rounded-lg p-5" title="Message for Wes" description="The lines not accepted, and what to ask for. Nothing is sent from here: copy it, and it goes out once Rakesh Sir approves.">
          <pre className="whitespace-pre-wrap rounded-lg border border-line bg-subtle px-3 py-3 text-[13px] leading-relaxed font-sans max-h-[52vh] overflow-y-auto">{d.message_for_wes}</pre>
          <div className="mt-3 flex justify-end gap-2">
            <Btn variant="ghost" onClick={() => setMsg(false)}>Close</Btn>
            <Btn variant="accent" onClick={async () => { if (await copyText(d.message_for_wes)) toast.success("Copied."); else toast.error("Could not copy. Select the text and copy it."); }}><Copy size={14} strokeWidth={1.6} /> Copy</Btn>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={askPay} onOpenChange={(o) => { if (!o && !busy) setAskPay(false); }}>
        <DialogContent className="rounded-lg p-5" title={approved ? "Mark ready to pay and send the email?" : "Approve, mark ready to pay and send the email?"}>
          <div className="rounded-lg border border-line bg-subtle px-3 py-2 text-[13px]">
            <div className="font-medium">{d.title}</div>
            <div className="text-muted mt-0.5 tnum">{d.property} · pay now {usd(d.pay_now)}{d.held > 0 && <> · held back {usd(d.held)}</>} · bill date {fmtDate(d.bill_date, "d MMM yyyy")}</div>
          </div>
          {(open.length > 0 || unread.length > 0) && (
            <div className="mt-3 rounded-lg bg-high-soft text-high text-[13px] px-3 py-2 leading-snug">
              {open.length > 0 && <><b className="font-semibold">{open.length} line{open.length === 1 ? "" : "s"}, {usd(openSum)}</b>, {open.length === 1 ? "is" : "are"} still to check or on hold. </>}
              {unread.length > 0 && <>{unread.length} line{unread.length === 1 ? " has" : "s have"} not been checked against the logs and photos. </>}
              The whole bill will go to payment as it stands.
            </div>
          )}
          <p className="mt-3 text-[13px] leading-relaxed">
            This ticks {TO_READY.filter((s) => !d.steps?.[s]).map((s) => ({ manju_approved: "“Manju Sir approved”", team_reviewed: "“Team reviewed”", ready_to_pay: "“Ready to pay”" }[s])).join(", ")} in your name,
            and an email is sent now from <b className="font-semibold">rakesh@mtreh.com</b> to <b className="font-semibold">{jp}</b>
            {(d.mail_to?.ready_to_pay?.cc || []).length > 0 && <>, with a copy to {names(d.mail_to!.ready_to_pay.cc)}</>}, with this bill and everything on file about the property. The email cannot be taken back.
          </p>
          {(readyMail?.status === "sent" || readyMail?.status === "replied") && <p className="mt-2 text-[13px] text-high">This email was already sent once; it will not be sent again.</p>}
          <div className="mt-4 flex justify-end gap-2">
            <Btn variant="ghost" disabled={busy} onClick={() => setAskPay(false)}>Cancel — tick nothing</Btn>
            <Btn variant="accent" disabled={busy} onClick={markReady}>{busy ? "Sending…" : "Yes, tick and send"}</Btn>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={ask} onOpenChange={(o) => { if (!o && !busy) setAsk(false); }}>
        <DialogContent className="rounded-lg p-5" title="Approve the bill and send the email?">
          <p className="text-[13px] leading-relaxed">
            {open.length > 0 && <><b className="font-semibold">{open.length} line{open.length === 1 ? "" : "s"}, {usd(openSum)}</b>, {open.length === 1 ? "is" : "are"} still to check or on hold. </>}
            {unread.length > 0 && <>{unread.length} line{unread.length === 1 ? " has" : "s have"} not been checked against the logs and photos. </>}
            Approving marks the whole bill of {usd(d.gross)} as approved, and an email is sent now from <b className="font-semibold">rakesh@mtreh.com</b> to <b className="font-semibold">{approvedTo}</b> saying it is approved. The email cannot be taken back.
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <Btn variant="ghost" disabled={busy} onClick={() => setAsk(false)}>Cancel</Btn>
            <Btn variant="accent" disabled={busy} onClick={() => approve(true)}>{busy ? "Approving…" : "Yes, approve and send"}</Btn>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
