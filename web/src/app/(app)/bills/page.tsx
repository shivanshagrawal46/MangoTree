"use client";

/* Bills — every contractor bill RKB is handling, on one line each: the bill, the
   property, the bill date, and RKB's four steps as tick boxes. Each tick records
   when it was made and by whom, and the page shows how long the bill took.
   A row opens the bill's own page (the check, line by line). */

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Calendar, CircleCheck, CircleDollarSign, Home, MessageSquare, Receipt } from "lucide-react";
import { api } from "@/lib/api";
import { Dialog, DialogContent, Skeleton } from "@/components/ui";
import { Btn, Chip, Col, PageBody, PageHead, Tag, TD, TH, Tick, View, Views, type Tone } from "@/components/ds";
import { PropertyMark, usePropertyColours } from "@/components/property-mark";
import { BillLines, usd } from "@/components/brain/side";
import { EntryChangeOrders } from "@/components/brain/entry";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText, type Bill } from "@/lib/brain";

type Mark = { at: string; by?: string; by_name?: string } | null | undefined;
type MailState = { status?: string; send_after?: string | null; sent_at?: string | null; to?: string[]; cc?: string[]; error?: string | null };

/* Every time on this page is US Eastern, the same as in the emails the page sends. */
const et = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }) + " ET" : "");
const etTime = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }) + " ET" : "");
const etDay = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-GB", { timeZone: "America/New_York", day: "numeric", month: "short" }) : "");     // "24 Nov", as the bill date is written
type Row = {
  bill_id: number; property_id?: string; property: string; number?: string; title?: string; pay_to?: string; amount?: number | null;
  bill_date?: string | null; bill_moment?: string | null; mail?: Record<string, MailState>; bill_day?: string | null; due_day?: string | null; past_due?: boolean; buildertrend_status?: string; in_buildertrend?: boolean;
  approvers?: string[]; line_items?: Bill["line_items"]; created_by?: string; documents?: number; steps: Record<string, Mark>;
  comment?: { text: string; at?: string; by_name?: string } | null;
  check?: { lines: number; ok: { count: number; amount: number }; check: { count: number; amount: number }; unread: { count: number; amount: number }; read_at?: string | null } | null;
};
type MailResult = { now?: "sent" | "already_sent" | "still_sending" | "not_accepted" | "failed"; status?: string; why?: string };
/* The ticks that send an email. The page asks first; on yes the box is ticked and the email goes at once.
   "Manju Sir approved" tells JP Sir the bill is approved, unless the bill is already marked ready to pay (he has that email). */
const SENDS: Record<string, string> = { manju_approved: "Manju Sir approved", ready_to_pay: "Ready to pay", paid: "Paid" };
const sends = (r: { steps: Record<string, unknown> }, step: string) => !!SENDS[step] && !(step === "manju_approved" && r.steps.ready_to_pay);
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
/* How long a bill has been open is highlighted: green up to a week, amber after it, red after two. */
const dayTone = (days: number): Tone => (days > 14 ? "red" : days > 7 ? "amber" : "green");

/* Hold-back bills are never listed here, so "(Contractor)" on a name says nothing: the row shows the name without it. */
const shortName = (t?: string) => (t || "").replace(/\s*\(contractor\)\s*$/i, "").trim();
/* The step headings, short enough for a column; the full name is on the heading's tooltip and on each box. */
const SHORT: Record<string, string> = { manju_approved: "Manju Sir", team_reviewed: "Team" };
/* Bill · Property · Bill date · Amount · Check · the four steps · Open · Comment (the one column that takes the spare width). */
const WIDTHS = [222, 176, 104, 96, 108, 88, 88, 108, 72, 84];
/* A bill is overdue once its due day has passed and it is not ticked paid (worked out now, not from the day Buildertrend was last read). */
const overdue = (due?: string | null) => !!due && Date.now() > new Date(due).getTime() + 12 * 3_600_000;

/* RKB's own note on a bill. Written in place and read in full: the box grows with the words.
   Saved when the box is left (or on Enter). Empty removes it. */
function Comment({ row, onSaved }: { row: Row; onSaved: (c: Row["comment"]) => void }) {
  const saved = row.comment?.text || "";
  const [text, setText] = React.useState(saved);
  const [busy, setBusy] = React.useState(false);
  const ref = React.useRef<HTMLTextAreaElement>(null);
  React.useEffect(() => { if (document.activeElement !== ref.current) setText(saved); }, [saved]);
  /* The same words, unseen, sit under the box in the same cell of a grid: they give the box its height,
     so the whole comment shows whatever the width of the column. */
  const SHAPE = "[grid-area:1/1] min-w-0 px-1.5 py-[3px] border text-[13px] leading-[1.4] whitespace-pre-wrap break-words";
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
      <div className="grid -mx-1.5">
        <div aria-hidden className={cn(SHAPE, "invisible border-transparent")}>{text || "Add a comment"}{" "}</div>
        <textarea ref={ref} value={text} rows={1} maxLength={500} disabled={busy} placeholder="Add a comment" aria-label={`Comment — ${row.title || row.number}`}
          onChange={(e) => setText(e.target.value)} onBlur={save}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.blur(); } else if (e.key === "Escape") { setText(saved); requestAnimationFrame(() => ref.current?.blur()); } }}
          style={{ scrollbarWidth: "none" }}
          className={cn(SHAPE, "block h-full w-full resize-none overflow-hidden rounded border-transparent bg-transparent text-fg outline-none transition-colors placeholder:text-faint/70 hover:border-line focus:border-accent focus:bg-elev")} />
      </div>
      {row.comment?.at && text === saved && saved && (
        <div className="text-[11px] text-faint truncate">{(row.comment.by_name || "").replace(/ Sir$/, "")}{row.comment.by_name ? " · " : ""}{et(row.comment.at)}</div>
      )}
    </div>
  );
}

export default function BillsPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const colourOf = usePropertyColours();
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
      if (done && sends(r, step)) {
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
  const properties = new Set(rows.map((r) => r.property_id)).size;
  const toCheck = rows.filter((r) => r.check?.check.count).length;
  const late = rows.filter((r) => !r.steps.paid && overdue(r.due_day)).length;
  const cols = 6 + steps.length + 1;

  return (
    <div>
      <PageHead icon={<Receipt size={16} strokeWidth={1.6} />} crumbs={[{ label: "Bills" }]}>
        {q.data?.pulled_at && <span className="text-[12px] text-faint">Buildertrend read {ago(q.data.pulled_at)}</span>}
      </PageHead>
      <PageBody>
      <Views>
        <View active={view === "open"} count={view === "open" && q.data ? rows.length : undefined} onClick={() => { setView("open"); setOpen(null); }}>To process</View>
        <View active={view === "paid"} count={view === "paid" && q.data ? rows.length : undefined} onClick={() => { setView("paid"); setOpen(null); }}>Paid</View>
        <span className="flex-1" />
        {/* The figures of the list, in one quiet line. */}
        {q.data && rows.length > 0 && (
          <span className="pr-2 text-[12px] text-faint tnum truncate">
            {usd(total)} {view === "open" ? "waiting" : "paid"} on {properties} propert{properties === 1 ? "y" : "ies"}
            {view === "open"
              ? <> · oldest <span className={cn(oldest > 14 ? "text-critical" : oldest > 7 ? "text-high" : undefined)}>{oldest} day{oldest === 1 ? "" : "s"}</span>{late > 0 && <> · <span className="text-critical">{late} past due</span></>}</>
              : avgDays != null && <> · {avgDays.toFixed(1)} days on average to pay</>}
            {" "}· {usd(q.data.held_back?.held_amount ?? 0)} held back
          </span>
        )}
      </Views>

      <div className="px-4 pb-4 border-b border-line-soft empty:hidden"><EntryChangeOrders /></div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[1290px] table-fixed border-collapse">
          <colgroup>{WIDTHS.slice(0, 5).map((w, i) => <col key={i} style={{ width: w }} />)}{steps.map((s, i) => <col key={s.key} style={{ width: WIDTHS[5 + i] ?? 88 }} />)}<col style={{ width: WIDTHS[9] }} /><col /></colgroup>
          <thead>
            <tr>
              <th className={TH}><Col icon={<Receipt size={14} strokeWidth={1.6} />}>Bill</Col></th>
              <th className={TH}><Col icon={<Home size={14} strokeWidth={1.6} />}>Property</Col></th>
              <th className={TH}><Col icon={<Calendar size={14} strokeWidth={1.6} />}>Bill date</Col></th>
              <th className={cn(TH, "text-right")}><Col icon={<CircleDollarSign size={14} strokeWidth={1.6} />}>Amount</Col></th>
              <th className={TH}><Col icon={<CircleCheck size={14} strokeWidth={1.6} />}>Check</Col></th>
              {steps.map((s) => <th key={s.key} title={s.label} className={cn(TH, "text-center")}>{SHORT[s.key] || s.label}</th>)}
              <th className={cn(TH, "text-right")}>{view === "paid" ? "Took" : "Open"}</th>
              <th className={TH}><Col icon={<MessageSquare size={14} strokeWidth={1.6} />}>Comment</Col></th>
            </tr>
          </thead>
          <tbody>
            {!q.data ? <tr><td colSpan={cols} className="p-4"><div className="space-y-2"><Skeleton className="h-6" /><Skeleton className="h-6" /><Skeleton className="h-6" /></div></td></tr>
              : rows.length === 0 ? <tr><td colSpan={cols} className="h-8 px-3 text-[13px] text-faint border-b border-line">{view === "open" ? "No bill is in review in Buildertrend, and none is part-way through." : "Tick “Paid” on a bill and it moves here with its times."}</td></tr>
                : rows.map((r) => {
                  const paid = r.steps.paid;
                  const days = daysOpen(r.bill_moment || r.bill_date);
                  const isLate = !paid && overdue(r.due_day);
                  const next = steps.find((s) => !r.steps[s.key])?.key;                  // the step this bill is waiting on
                  const page = r.in_buildertrend !== false;                              // it has its own page: the check, line by line
                  const name = shortName(r.title) || r.number || "";
                  const go = () => (page ? router.push(`/bills/${r.bill_id}`) : setOpen(open === r.bill_id ? null : r.bill_id));
                  return (
                    <React.Fragment key={r.bill_id}>
                      <tr onClick={go} className="cursor-pointer hover:bg-hover transition-colors">
                        <td className={TD} title={[r.title || r.number, r.number, r.pay_to && `pay to ${r.pay_to}`].filter(Boolean).join(" · ")}>
                          {page ? <Link href={`/bills/${r.bill_id}`} onClick={(e) => e.stopPropagation()} className="hover:underline">{name}</Link> : name}
                        </td>
                        <td className={TD}>{r.property_id
                          ? <Link href={`/property/${r.property_id}?tab=bills`} onClick={(e) => e.stopPropagation()} className="inline-flex max-w-full align-middle hover:brightness-95"><Chip icon={<PropertyMark label={r.property} colour={colourOf(r.property_id)} />}>{r.property}</Chip></Link>
                          : r.property}</td>
                        <td className={cn(TD, "tnum text-muted", isLate && "text-critical")} title={r.due_day ? `Due ${fmtDate(r.due_day, "d MMM yyyy")}${isLate ? ", past its due date" : ""}` : undefined}>{fmtDate(r.bill_day, "d MMM")}</td>
                        <td className={cn(TD, "text-right tnum")}>{usd(r.amount)}</td>
                        <td className={TD}>{page && (!r.check ? <Tag>Not checked</Tag>
                          : r.check.check.count ? <Tag tone="amber" title={`${usd(r.check.check.amount)} to check`}>{r.check.check.count} to check</Tag>
                            : <Tag tone="green">{r.check.lines === 1 ? "Line supported" : "Lines supported"}</Tag>)}</td>
                        {steps.map((s) => {
                          const m = r.steps[s.key];
                          const mail = r.mail?.[s.key];
                          const gone = mail?.status === "sent" || mail?.status === "replied";
                          const tip = m ? [s.label, et(m.at), took(r.bill_moment || r.bill_date, m.at) && `+${took(r.bill_moment || r.bill_date, m.at)} from the bill date`, m.by_name && `by ${m.by_name}`,
                            gone && `emailed ${(mail!.to || []).join(", ")} ${etTime(mail!.sent_at)}`].filter(Boolean).join(" · ") : s.label;
                          return (
                            <td key={s.key} className={cn(TD, "text-center whitespace-normal py-1.5")} title={tip} onClick={(e) => e.stopPropagation()}>
                              {/* The box, and under it the day it was ticked. */}
                              <span className="inline-flex flex-col items-center gap-1 align-middle">
                                <Tick checked={!!m} next={!m && next === s.key} label={`${s.label} — ${r.title || r.number}`} onChange={(v) => (v && sends(r, s.key) ? setAsk({ row: r, step: s.key }) : tick(r, s.key, v))} />
                                {m && <span className="text-[12px] leading-none text-muted tnum">{etDay(m.at)}</span>}
                              </span>
                              {m && mail?.status === "queued" && !mail.error && <div className="text-[11px] leading-tight text-high">email being sent</div>}
                              {m && mail?.status === "needs_consent" && <div className="text-[11px] leading-tight text-critical">email not sent: the mailbox must allow sending</div>}
                              {m && mail?.status === "queued" && mail.error && <div className="text-[11px] leading-tight text-critical">email NOT gone yet; retrying</div>}
                              {m && mail?.status === "failed" && <div className="text-[11px] leading-tight text-critical">email could not be sent</div>}
                            </td>
                          );
                        })}
                        <td className={cn(TD, "text-right")}>
                          {paid ? <Tag tone="green" title="From the bill date to Paid">{took(r.bill_moment || r.bill_date, paid.at) || "—"}</Tag>
                            : <Tag tone={dayTone(days)} title="Open so far, from the bill date">{days} day{days === 1 ? "" : "s"}</Tag>}
                        </td>
                        <td className={cn(TD, "whitespace-normal py-1 cursor-text")} onClick={(e) => e.stopPropagation()}>
                          <Comment row={r} onSaved={(c) => { qc.setQueryData<Resp>(key, (cur) => cur ? { ...cur, rows: cur.rows.map((x) => x.bill_id === r.bill_id ? { ...x, comment: c } : x) } : cur); }} />
                        </td>
                      </tr>
                      {open === r.bill_id && (
                        <tr>
                          <td colSpan={cols} className="px-4 py-3 border-b border-line bg-subtle">
                            <div className="max-w-3xl">
                              <div className="mb-1.5 text-[12px] text-muted">
                                <span className="font-medium text-fg">{r.title || r.number}</span>{r.number ? ` · ${r.number}` : ""}{r.pay_to ? ` · ${r.pay_to}` : ""}
                                <span className="text-faint"> · Buildertrend: {r.buildertrend_status}{r.approvers?.length ? ` · approver there: ${r.approvers.join(", ")}` : ""}</span>
                              </div>
                              <BillLines b={r as unknown as Bill} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
          </tbody>
          {q.data && rows.length > 0 && (
            <tfoot>
              <tr className="text-[12px] text-faint [&>td]:border-b [&>td]:border-b-line-soft">
                <td className="h-8 px-3">{rows.length} bill{rows.length === 1 ? "" : "s"}</td>
                <td className="h-8 px-3">{properties} propert{properties === 1 ? "y" : "ies"}</td>
                <td />
                <td className="h-8 px-3 text-right tnum">{usd(total)}</td>
                <td className="h-8 px-3">{view === "open" ? `${toCheck} to check` : ""}</td>
                {steps.map((s) => <td key={s.key} className="h-8 px-3 text-center tnum">{rows.filter((r) => r.steps[s.key]).length} of {rows.length}</td>)}
                <td /><td />
              </tr>
            </tfoot>
          )}
        </table>
      </div>

      {/* How the page works is kept, but closed: it is read once, not every day. */}
      <details className="px-3 pt-3 pb-6 max-w-5xl text-[12px] leading-relaxed text-faint">
        <summary className="cursor-pointer select-none hover:text-muted">How this page works</summary>
        <div className="mt-2 space-y-2">
        {q.data?.held_back && q.data.held_back.held > 0 && (
          <p className="text-muted">
            Hold-back bills are not listed: RKB keeps 10% of every draw until the work is complete.
            {" "}<b className="text-fg font-semibold tnum">{usd(q.data.held_back.held_amount)}</b> is held across {q.data.held_back.held} hold-back bills
            {q.data.held_back.in_review > 0 ? <> ({q.data.held_back.in_review} of them, {usd(q.data.held_back.in_review_amount)}, entered with the bills now in review)</> : null}.
          </p>
        )}
        <p>
          Ticking “Manju Sir approved” emails JP Sir, ticking “Ready to pay” emails JP Sir, and ticking “Paid” emails Wes and Kelly (copy Manjunath Sir), from rakesh@mtreh.com. The page asks first, and on yes the email is sent at once.
          A bill appears here when it is on Buildertrend’s “Bills need your approval” list, and stays until it is ticked paid. Each tick records the time and who made it: hold the pointer over a tick to see them. Unticking a box removes its time.
          A comment is RKB’s own note on the bill: it is saved when you leave the box, with who wrote it, and is not sent to anyone.
        </p>
        </div>
      </details>
      </PageBody>

      <Dialog open={!!ask} onOpenChange={(o) => { if (!o && !sending) setAsk(null); }}>
        <DialogContent className="rounded-lg p-5" title={ask ? `Tick “${SENDS[ask.step]}” and send the email?` : ""}>
          {ask && (
            <div>
              <div className="rounded-lg border border-line bg-subtle px-3 py-2 text-[13px]">
                <div className="font-medium">{ask.row.title || ask.row.number}</div>
                <div className="text-muted mt-0.5">{ask.row.property} · {usd(ask.row.amount)} · bill date {fmtDate(ask.row.bill_day, "d MMM yyyy")}</div>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed">
                An email will be sent now from <b className="font-semibold">rakesh@mtreh.com</b> to <b className="font-semibold">{names(q.data?.mail_to?.[ask.step]?.to) || "the recipients"}</b>
                {(q.data?.mail_to?.[ask.step]?.cc || []).length > 0 && <>, with a copy to {names(q.data!.mail_to![ask.step].cc)}</>}
                {ask.step === "paid" ? ", saying this bill has been paid, with the bill date, the time of each step and how long it took."
                  : ask.step === "manju_approved" ? ", saying this bill has been approved, with the bill and everything on file about the property."
                  : ", with this bill and everything on file about the property."}
              </p>
              {(ask.row.mail?.[ask.step]?.status === "sent" || ask.row.mail?.[ask.step]?.status === "replied") && <p className="mt-2 text-[13px] text-high">This email was already sent once; it will not be sent again.</p>}
              <div className="mt-4 flex justify-end gap-2">
                <Btn variant="ghost" onClick={() => setAsk(null)} disabled={sending}>Cancel — do not tick</Btn>
                <Btn variant="accent" disabled={sending} onClick={async () => { setSending(true); try { await tick(ask.row, ask.step, true); } finally { setSending(false); setAsk(null); } }}>
                  {sending ? "Sending…" : "Yes, tick and send"}
                </Btn>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
