"use client";

/* Bills — every contractor bill RKB is handling, on one line each: the bill, the
   property, the bill date, and RKB's four steps as tick boxes. Each tick records
   when it was made and by whom, and the page shows how long the bill took. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Banknote, ChevronRight, Clock, Lock, Receipt } from "lucide-react";
import { api } from "@/lib/api";
import { Button, Card, Checkbox, Dialog, DialogContent, Skeleton } from "@/components/ui";
import { BillLines, usd } from "@/components/brain/side";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText, type Bill } from "@/lib/brain";

type Mark = { at: string; by?: string; by_name?: string } | null | undefined;
type MailState = { status?: string; send_after?: string | null; sent_at?: string | null; to?: string[]; cc?: string[]; error?: string | null };

/* Every time on this page is US Eastern, the same as in the emails the page sends. */
const et = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) + " ET" : "");
const etTime = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }) + " ET" : "");
type Row = {
  bill_id: number; property_id?: string; property: string; number?: string; title?: string; pay_to?: string; amount?: number | null;
  bill_date?: string | null; bill_moment?: string | null; mail?: Record<string, MailState>; bill_day?: string | null; due_day?: string | null; past_due?: boolean; buildertrend_status?: string; in_buildertrend?: boolean;
  approvers?: string[]; line_items?: Bill["line_items"]; created_by?: string; documents?: number; steps: Record<string, Mark>;
  comment?: { text: string; at?: string; by_name?: string } | null;
  check?: { lines: number; ok: { count: number; amount: number }; check: { count: number; amount: number }; unread: { count: number; amount: number }; read_at?: string | null } | null;
};
type MailResult = { now?: "sent" | "already_sent" | "still_sending" | "not_accepted" | "failed"; status?: string; why?: string };
/* The two ticks that send an email. The page asks first; on yes the box is ticked and the email goes at once. */
const SENDS: Record<string, string> = { ready_to_pay: "Ready to pay", paid: "Paid" };
const names = (xs?: string[]) => (xs || []).map((n) => n.replace(/ Stone$/, "")).join(" and ");

type Resp = { view: "open" | "paid"; steps: { key: string; label: string }[]; rows: Row[]; pulled_at?: string | null;
  mail_to?: Record<string, { to: string[]; cc: string[] }>;
  held_back?: { in_review: number; in_review_amount: number; held: number; held_amount: number } };

/* "3d 4h", "5h 20m", "12 min" — the time between two moments. */
function took(from?: string | null, to?: string | null) {
  if (!from || !to) return null;
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (Number.isNaN(ms) || ms < 0) return null;
  const min = Math.round(ms / 60_000), h = Math.floor(min / 60), d = Math.floor(h / 24);
  return d >= 1 ? `${d}d ${h % 24}h` : h >= 1 ? `${h}h ${min % 60}m` : `${Math.max(1, min)} min`;
}
const nowIso = () => new Date().toISOString();
const daysOpen = (from?: string | null) => (from ? Math.floor((Date.now() - new Date(from).getTime()) / 86_400_000) : 0);

/* Bill · Property · Bill date · Amount · the four steps · Time taken · Comment (the one column that takes the spare width). */
const COLS = "grid-cols-[200px_136px_74px_88px_repeat(4,92px)_78px_minmax(190px,1fr)]";
/* Hold-back bills are never listed here, so "(Contractor)" on a name says nothing: the row shows the name without it. */
const shortName = (t?: string) => (t || "").replace(/\s*\(contractor\)\s*$/i, "").trim();

/* The four figures on top of the list, each with its own colour so the page reads at a glance. */
const TONES = {
  accent: { bar: "bg-accent", chip: "bg-accent-soft text-accent" },
  blue: { bar: "bg-money-in", chip: "bg-[color-mix(in_srgb,var(--money-in)_13%,transparent)] text-money-in" },
  good: { bar: "bg-good", chip: "bg-good-soft text-good" },
  high: { bar: "bg-high", chip: "bg-high-soft text-high" },
  critical: { bar: "bg-critical", chip: "bg-critical-soft text-critical" },
  slate: { bar: "bg-info", chip: "bg-info-soft text-info" },
} as const;
function Tile({ tone, icon, label, value, sub }: { tone: keyof typeof TONES; icon: React.ReactNode; label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="relative overflow-hidden rounded-xl border border-line bg-elev pl-4 pr-3.5 py-3 flex items-center gap-3 shadow-[var(--shadow-sm)]">
      <span className={cn("absolute inset-y-0 left-0 w-[3px]", TONES[tone].bar)} />
      <span className={cn("grid place-items-center size-9 rounded-lg shrink-0", TONES[tone].chip)}>{icon}</span>
      <div className="min-w-0">
        <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-faint">{label}</div>
        <div className="text-[18px] font-semibold tracking-tight tnum leading-tight mt-0.5">{value}</div>
        {sub && <div className="text-[11px] text-muted truncate mt-0.5">{sub}</div>}
      </div>
    </div>
  );
}
/* A bill is overdue once its due day has passed and it is not ticked paid (worked out now, not from the day Buildertrend was last read). */
const overdue = (due?: string | null) => !!due && Date.now() > new Date(due).getTime() + 12 * 3_600_000;

/* RKB's own note on a bill. Written in place; saved when the box is left (or on Enter). Empty removes it. */
function Comment({ row, onSaved }: { row: Row; onSaved: (c: Row["comment"]) => void }) {
  const saved = row.comment?.text || "";
  const [text, setText] = React.useState(saved);
  const [busy, setBusy] = React.useState(false);
  const ref = React.useRef<HTMLTextAreaElement>(null);
  React.useEffect(() => { if (document.activeElement !== ref.current) setText(saved); }, [saved]);
  React.useLayoutEffect(() => { const el = ref.current; if (el) { el.style.height = "0px"; el.style.height = `${Math.max(30, el.scrollHeight + 2)}px`; } }, [text]);
  const save = async () => {
    const next = text.replace(/\s+/g, " ").trim();
    if (next === saved) { setText(saved); return; }
    setBusy(true);
    try {
      const res = await api.post<{ comment: Row["comment"] }>(`/brain/bills/${row.bill_id}/comment`, { text: next });
      onSaved(res.comment || null);
      setText(res.comment?.text || "");
    } catch (e) { toast.error(errText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="min-w-0">
      <textarea ref={ref} value={text} rows={1} maxLength={500} disabled={busy} placeholder="Add a comment" aria-label={`Comment — ${row.title || row.number}`}
        onChange={(e) => setText(e.target.value)} onBlur={save}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.blur(); } else if (e.key === "Escape") { setText(saved); requestAnimationFrame(() => ref.current?.blur()); } }}
        style={{ scrollbarWidth: "none" }}
        className={cn("block w-full resize-none overflow-hidden rounded-md border px-2 py-[5px] text-[12.5px] leading-snug outline-none transition placeholder:text-faint",
          text ? "border-transparent bg-sunken/60" : "border-dashed border-line bg-transparent", "hover:border-line focus:border-accent focus:bg-elev")} />
      {row.comment?.at && text === saved && saved && (
        <div className="mt-0.5 px-2 text-[10.5px] text-faint truncate">{(row.comment.by_name || "").replace(/ Sir$/, "")}{row.comment.by_name ? " · " : ""}{et(row.comment.at)}</div>
      )}
    </div>
  );
}

export default function BillsPage() {
  const qc = useQueryClient();
  const [view, setView] = React.useState<"open" | "paid">("open");
  const [open, setOpen] = React.useState<number | null>(null);
  const [ask, setAsk] = React.useState<{ row: Row; step: string } | null>(null);     // "send the email?" before the tick
  const [sending, setSending] = React.useState(false);
  const key = ["brain", "bills", view];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<Resp>(`/brain/bills?view=${view}`), placeholderData: (p) => p, refetchInterval: 60_000 });
  const rows = q.data?.rows || [];
  const steps = q.data?.steps || [];

  const tick = async (r: Row, step: string, done: boolean) => {
    // Show the tick at once; the server's own time replaces this one when it answers.
    qc.setQueryData<Resp>(key, (cur) => cur ? { ...cur, rows: cur.rows.map((x) => x.bill_id === r.bill_id ? { ...x, steps: { ...x.steps, [step]: done ? { at: nowIso(), by_name: "you" } : null } } : x) } : cur);
    try {
      const res = await api.post<{ mail_result?: MailResult | null }>(`/brain/bills/${r.bill_id}/steps`, { step, done });
      if (done && SENDS[step]) {
        const who = names(q.data?.mail_to?.[step]?.to) || "the recipients";
        const m = res.mail_result?.now;
        const time = step === "paid" ? ` Paid ${took(r.bill_moment || r.bill_date, nowIso()) || "the same day"} from the bill date.` : "";
        if (m === "sent") toast.success(`Email sent to ${who}.${time}`);
        else if (m === "already_sent") toast.message(`Ticked. This email had already been sent to ${who}.`);
        else if (m === "still_sending") toast.message(`Ticked. The email to ${who} is being sent; this line will show when it has gone.`);
        else if (m === "not_accepted") toast.warning(`Ticked, but the email has NOT gone yet: Microsoft's mail service did not accept it just now. It is kept and retried every few minutes until it goes.`, { duration: 12000 });
        else toast.error(`Ticked, but the email was not sent${res.mail_result?.why ? `: ${res.mail_result.why}` : "."}`);
      }
    } catch (e) { toast.error(errText(e)); }
    qc.invalidateQueries({ queryKey: ["brain", "bills"] });
    qc.invalidateQueries({ queryKey: ["brain", "counts"] });
  };

  const total = rows.reduce((a, r) => a + (r.amount || 0), 0);
  const oldest = rows.reduce((m, r) => Math.max(m, daysOpen(r.bill_moment || r.bill_date)), 0);
  const paidTimes = rows.map((r) => (r.steps.paid && (r.bill_moment || r.bill_date) ? new Date(r.steps.paid.at).getTime() - new Date((r.bill_moment || r.bill_date)!).getTime() : null)).filter((x): x is number => x != null && x >= 0);
  const avgDays = paidTimes.length ? paidTimes.reduce((a, b) => a + b, 0) / paidTimes.length / 86_400_000 : null;

  return (
    <div className="px-6 py-6 max-w-[1400px] mx-auto">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">Bills</h1>
          <p className="text-[12.5px] text-muted mt-0.5">
            {!q.data ? "Loading…" : view === "open"
              ? (rows.length ? "Bills waiting for RKB, from approval to payment" : "No bill is waiting.")
              : (rows.length ? "Bills ticked Paid, with the time each one took" : "No bill has been marked paid yet.")}
            {q.data?.pulled_at && <span className="text-faint"> · Buildertrend read {ago(q.data.pulled_at)}</span>}
          </p>
        </div>
        <div className="inline-flex rounded-lg bg-sunken p-0.5 border border-line">
          {([["open", "To process"], ["paid", "Paid"]] as const).map(([k, l]) => (
            <button key={k} type="button" onClick={() => { setView(k); setOpen(null); }} className={cn("h-7 px-3 rounded-md text-[12.5px] font-medium transition", view === k ? "bg-accent text-white shadow-[var(--shadow-sm)]" : "text-muted hover:text-fg")}>{l}</button>
          ))}
        </div>
      </div>

      {q.data && rows.length > 0 && (
        <div className="mt-4 grid grid-cols-2 xl:grid-cols-4 gap-3">
          <Tile tone="accent" icon={<Receipt size={17} />} label={view === "open" ? "Bills to process" : "Bills paid"} value={rows.length}
            sub={view === "open" ? `${rows.filter((r) => r.check?.check.count).length} with lines to check` : "ticked Paid here"} />
          <Tile tone="blue" icon={<Banknote size={17} />} label={view === "open" ? "Amount waiting" : "Amount paid"} value={usd(total)}
            sub={view === "open" ? `${new Set(rows.map((r) => r.property_id)).size} properties` : `${new Set(rows.map((r) => r.property_id)).size} properties`} />
          {view === "open"
            ? <Tile tone={oldest > 14 ? "critical" : oldest > 7 || rows.some((r) => !r.steps.paid && overdue(r.due_day)) ? "high" : "good"} icon={<Clock size={17} />} label="Oldest bill" value={`${oldest} day${oldest === 1 ? "" : "s"}`}
                sub={`${rows.filter((r) => !r.steps.paid && overdue(r.due_day)).length} past their due date`} />
            : <Tile tone="good" icon={<Clock size={17} />} label="Average time to pay" value={avgDays != null ? `${avgDays.toFixed(1)} days` : "—"} sub="from bill date to Paid" />}
          <Tile tone="slate" icon={<Lock size={16} />} label="Held back (10%)" value={usd(q.data.held_back?.held_amount ?? 0)} sub={`${q.data.held_back?.held ?? 0} hold-back bills, not listed`} />
        </div>
      )}

      <Card className="mt-3 overflow-x-auto">
        <div className="min-w-[1256px]">
          <div className={cn("grid gap-2.5 px-4 py-2.5 border-b border-accent/20 bg-accent-soft/70 text-[9.5px] font-semibold uppercase tracking-[0.09em] text-accent items-end", COLS)}>
            <div className="pl-[19px]">Bill</div><div>Property</div><div>Bill date</div><div className="text-right">Amount</div>
            {steps.map((s) => <div key={s.key} className="text-center leading-tight">{s.label}</div>)}
            <div className="text-right">Time taken</div>
            <div className="pl-2">Comment</div>
          </div>
          {!q.data ? <div className="p-4 space-y-2.5"><Skeleton className="h-10" /><Skeleton className="h-10" /><Skeleton className="h-10" /></div>
            : rows.length === 0 ? <div className="px-4 py-8 text-[13px] text-muted text-center">{view === "open" ? "No bill is in review in Buildertrend, and none is part-way through." : "Tick “Paid” on a bill and it moves here with its times."}</div>
              : rows.map((r) => {
                const paid = r.steps.paid;
                const days = daysOpen(r.bill_moment || r.bill_date);
                const late = !paid && overdue(r.due_day);
                const next = steps.find((s) => !r.steps[s.key])?.key;                  // the step this bill is waiting on
                return (
                  <div key={r.bill_id} className="relative border-b border-line/70 last:border-0">
                    <span className={cn("absolute left-0 top-0 bottom-0 w-[3px]", paid ? "bg-good" : !r.check ? "bg-line-strong" : r.check.check.count ? "bg-high" : "bg-good")} />
                    <div className={cn("grid gap-2.5 px-4 py-2.5 items-start hover:bg-accent-soft/25 transition-colors", COLS)}>
                      <button type="button" onClick={() => setOpen(open === r.bill_id ? null : r.bill_id)} title={[r.title || r.number, r.number, r.pay_to].filter(Boolean).join(" · ")} className="min-w-0 text-left flex gap-1.5">
                        <ChevronRight size={13} className={cn("mt-[3px] shrink-0 text-faint transition", open === r.bill_id && "rotate-90")} />
                        <span className="min-w-0">
                          <span className="block text-[13px] font-medium leading-snug line-clamp-2">{shortName(r.title) || r.number}</span>
                          <span className="block text-[11px] text-faint truncate">{r.pay_to || r.number}</span>
                        </span>
                      </button>
                      <div className="min-w-0 text-[12.5px]">
                        <div className="truncate">{r.property_id ? <Link href={`/property/${r.property_id}?tab=bills`} className="font-medium text-accent hover:underline">{r.property}</Link> : r.property}</div>
                        {r.in_buildertrend !== false && (
                          <Link href={`/bills/${r.bill_id}`} title="Open this bill line by line: the proposal line, what was billed before, and what the logs and photos show"
                            className={cn("mt-1 inline-flex items-center gap-1 h-[20px] pl-1.5 pr-1 rounded text-[11px] font-semibold whitespace-nowrap hover:brightness-95",
                              !r.check ? "bg-sunken text-muted" : r.check.check.count ? "bg-high-soft text-high" : "bg-good-soft text-good")}>
                            {!r.check ? "Not checked yet" : r.check.check.count ? `${r.check.check.count} to check · ${usd(r.check.check.amount)}` : r.check.lines === 1 ? "Line supported" : `All ${r.check.lines} lines supported`}
                            <ChevronRight size={11} />
                          </Link>
                        )}
                      </div>
                      <div className="text-[12.5px] tnum">
                        {fmtDate(r.bill_day, "d MMM yy")}
                        {r.due_day && <div className={cn("text-[10.5px]", late ? "text-critical font-semibold" : "text-faint")}>due {fmtDate(r.due_day, "d MMM")}</div>}
                      </div>
                      <div className="text-[13px] font-semibold tnum text-right">{usd(r.amount)}</div>
                      {steps.map((s) => {
                        const m = r.steps[s.key];
                        return (
                          <div key={s.key} className="flex flex-col items-center text-center">
                            <Checkbox checked={!!m} onCheckedChange={(v) => (v && SENDS[s.key] ? setAsk({ row: r, step: s.key }) : tick(r, s.key, v))} size={18} label={`${s.label} — ${r.title || r.number}`}
                              className={cn("rounded-md data-[state=checked]:bg-good data-[state=checked]:border-good", !m && next === s.key && "border-accent shadow-[0_0_0_3px_color-mix(in_srgb,var(--accent)_14%,transparent)]")} />
                            {m && (
                              <div className="mt-1 w-full rounded-md bg-good-soft/70 px-1 py-[3px] text-[10.5px] leading-tight text-muted">
                                <div className="tnum">{et(m.at)}</div>
                                <div className="text-faint">{took(r.bill_moment || r.bill_date, m.at) ? `+${took(r.bill_moment || r.bill_date, m.at)}` : ""}{m.by_name ? ` · ${m.by_name.replace(/ Sir$/, "")}` : ""}</div>
                                {r.mail?.[s.key]?.status === "queued" && !r.mail[s.key].error && <div className="text-high">email to {(r.mail[s.key].to || []).join(", ")} is being sent</div>}
                                {(r.mail?.[s.key]?.status === "sent" || r.mail?.[s.key]?.status === "replied") && <div className="text-good">emailed {(r.mail[s.key].to || []).join(", ")} {etTime(r.mail[s.key].sent_at)}</div>}
                                {r.mail?.[s.key]?.status === "needs_consent" && <div className="text-critical">email not sent: the mailbox needs to allow sending</div>}
                                {r.mail?.[s.key]?.status === "queued" && r.mail[s.key].error && <div className="text-critical">email NOT gone yet: Microsoft&apos;s mail service did not accept it; retrying every few minutes</div>}
                                {r.mail?.[s.key]?.status === "failed" && <div className="text-critical">email could not be sent</div>}
                              </div>
                            )}
                          </div>
                        );
                      })}
                      <div className="text-right text-[12.5px] tnum">
                        {paid ? <><span className="inline-flex h-[22px] items-center rounded-full bg-good-soft px-2 text-[12px] font-semibold text-good">{took(r.bill_moment || r.bill_date, paid.at) || "—"}</span><div className="mt-0.5 text-[10.5px] text-faint">bill date to paid</div></>
                          : <><span className={cn("inline-flex h-[22px] items-center rounded-full px-2 text-[12px] font-semibold", days > 14 ? "bg-critical-soft text-critical" : days > 7 ? "bg-high-soft text-high" : "bg-good-soft text-good")}>{days} day{days === 1 ? "" : "s"}</span><div className="mt-0.5 text-[10.5px] text-faint">open so far</div></>}
                      </div>
                      <Comment row={r} onSaved={(c) => { qc.setQueryData<Resp>(key, (cur) => cur ? { ...cur, rows: cur.rows.map((x) => x.bill_id === r.bill_id ? { ...x, comment: c } : x) } : cur); }} />
                    </div>
                    {open === r.bill_id && (
                      <div className="px-4 pb-3 pl-[34px] max-w-3xl">
                        <div className="mb-1.5 text-[11.5px] text-muted">
                          <span className="font-medium text-fg">{r.title || r.number}</span>{r.number ? ` · ${r.number}` : ""}{r.pay_to ? ` · ${r.pay_to}` : ""}
                          <span className="text-faint"> · Buildertrend: {r.buildertrend_status}{r.approvers?.length ? ` · approver there: ${r.approvers.join(", ")}` : ""}</span>
                        </div>
                        <BillLines b={r as unknown as Bill} />
                      </div>
                    )}
                  </div>
                );
              })}
        </div>
      </Card>
      {q.data?.held_back && q.data.held_back.held > 0 && (
        <p className="mt-2.5 text-[12px] text-muted">
          Hold-back bills are not listed: RKB keeps 10% of every draw until the work is complete.
          {" "}<b className="text-fg font-semibold tnum">{usd(q.data.held_back.held_amount)}</b> is held across {q.data.held_back.held} hold-back bills
          {q.data.held_back.in_review > 0 ? <> ({q.data.held_back.in_review} of them, {usd(q.data.held_back.in_review_amount)}, entered with the bills now in review)</> : null}.
        </p>
      )}
      <p className="mt-2.5 text-[11px] text-faint leading-relaxed">
        Ticking “Ready to pay” emails JP Sir, and ticking “Paid” emails Wes and Kelly (copy Manjunath Sir and Shivansh), from rakesh@mtreh.com. The page asks first, and on yes the email is sent at once. All times are US Eastern.
        A bill appears here when it is on Buildertrend’s “Bills need your approval” list, and stays until it is ticked paid. Each tick records the time and who made it; “+2d 3h” is the time since the bill date. Unticking a box removes its time.
        A comment is RKB’s own note on the bill: it is saved when you leave the box, with who wrote it, and is not sent to anyone.
      </p>

      <Dialog open={!!ask} onOpenChange={(o) => { if (!o && !sending) setAsk(null); }}>
        <DialogContent title={ask ? `Tick “${SENDS[ask.step]}” and send the email?` : ""}>
          {ask && (
            <div>
              <div className="rounded-xl border border-line bg-sunken/50 px-3.5 py-2.5 text-[13px]">
                <div className="font-medium">{ask.row.title || ask.row.number}</div>
                <div className="text-muted mt-0.5">{ask.row.property} · {usd(ask.row.amount)} · bill date {fmtDate(ask.row.bill_day, "d MMM yyyy")}</div>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed">
                An email will be sent now from <b className="font-semibold">rakesh@mtreh.com</b> to <b className="font-semibold">{names(q.data?.mail_to?.[ask.step]?.to) || "the recipients"}</b>
                {(q.data?.mail_to?.[ask.step]?.cc || []).length > 0 && <>, with a copy to {names(q.data!.mail_to![ask.step].cc)}</>}
                {ask.step === "paid" ? ", saying this bill has been paid, with the bill date, the time of each step and how long it took." : ", with this bill and everything on file about the property."}
              </p>
              {(ask.row.mail?.[ask.step]?.status === "sent" || ask.row.mail?.[ask.step]?.status === "replied") && <p className="mt-2 text-[12.5px] text-high">This email was already sent once; it will not be sent again.</p>}
              <div className="mt-4 flex justify-end gap-2">
                <Button variant="ghost" onClick={() => setAsk(null)} disabled={sending}>Cancel — do not tick</Button>
                <Button variant="primary" disabled={sending} onClick={async () => { setSending(true); try { await tick(ask.row, ask.step, true); } finally { setSending(false); setAsk(null); } }}>
                  {sending ? "Sending…" : "Yes, tick and send"}
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
