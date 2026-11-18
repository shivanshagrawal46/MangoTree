"use client";

/* My day — a few things to do now, not everything at once.

   Each person sees the first five things waiting for them, the most pressing
   first, each with the one action that finishes it. The rest wait under
   "Later" and move up by themselves. Rakesh Sir also sees the team — what is
   left and what is late for each person — and can give anyone a task: it lands
   on their list and an email goes to them. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { type LucideIcon, ArrowRight, CalendarClock, Check, CheckCircle2, ChevronDown, Clock, Plus, Receipt, Reply, Scale, Search, Send, UserRound, X } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, Dialog, DialogContent, Input, Select, Skeleton, Textarea } from "@/components/ui";
import { DraftCard } from "@/components/brain/draft";
import { cn, fmtDate } from "@/lib/utils";
import { errText, type DayBoard, type DayCard, type Draft, type TeamRow } from "@/lib/brain";

const TYPE: Record<string, { label: string; icon: LucideIcon; cls: string }> = {
  reply: { label: "Reply", icon: Reply, cls: "bg-accent-soft text-accent" },
  decision: { label: "Decide", icon: Scale, cls: "bg-info-soft text-info" },
  bill: { label: "Bill", icon: Receipt, cls: "bg-high-soft text-high" },
  check: { label: "Check", icon: Search, cls: "bg-high-soft text-high" },
  todo: { label: "To do", icon: Check, cls: "bg-sunken text-muted" },
};

function typeOf(c: DayCard) {
  if (c.type === "todo" && c.kind === "decision") return TYPE.decision;
  return TYPE[c.type] || TYPE.todo;
}

function useActions(onOpenDraft: (id: string) => void) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["brain"] });
  const close = async (c: DayCard, status: "done" | "dropped") => {
    // The card leaves at once; the next one moves up when the list comes back.
    qc.setQueryData<DayBoard>(["brain", "myday"], (cur) => cur ? { ...cur, now: cur.now.filter((x) => x.key !== c.key), later: cur.later.filter((x) => x.key !== c.key) } : cur);
    try {
      await api.patch(`/brain/properties/${c.property_id}/items/${c.item_id}`, { status });
      toast.success(status === "done" ? "Done" : "Removed", { action: { label: "Undo", onClick: async () => { await api.patch(`/brain/properties/${c.property_id}/items/${c.item_id}`, { status: "open" }); refresh(); } } });
    } catch (e) { toast.error(errText(e)); }
    refresh();
  };
  const later = async (c: DayCard) => {
    try { await api.post("/brain/followups/mark", { key: c.follow_key, state: "later" }); toast.success("Put aside for three days"); } catch (e) { toast.error(errText(e)); }
    refresh();
  };
  return { close, later, openDraft: onOpenDraft, refresh };
}

function Action({ c, a, mine }: { c: DayCard; a: ReturnType<typeof useActions>; mine: boolean }) {
  if (!mine) return <Link href={c.href} className="text-xs text-accent hover:underline whitespace-nowrap flex items-center gap-1">Open <ArrowRight size={12} /></Link>;
  if (c.type === "reply" && c.draft_id) return <Button size="sm" variant="primary" onClick={() => a.openDraft(c.draft_id!)}><Send size={12} /> Read and send</Button>;
  if (c.type === "bill") return <Link href={c.href}><Button size="sm" variant="primary"><Receipt size={12} /> Open the bill</Button></Link>;
  if (c.type === "check") return (
    <div className="flex items-center gap-1.5">
      <Link href={c.href}><Button size="sm" variant="primary">Open</Button></Link>
      <Button size="sm" variant="ghost" onClick={() => a.later(c)}>Later</Button>
    </div>
  );
  return (
    <div className="flex items-center gap-1.5">
      <Button size="sm" variant="primary" onClick={() => a.close(c, "done")}><Check size={12} /> Done</Button>
      <Button size="sm" variant="ghost" onClick={() => a.close(c, "dropped")} title="Not needed any more"><X size={12} /></Button>
    </div>
  );
}

function Meta({ c }: { c: DayCard }) {
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-faint">
      <Link href={`/property/${c.property_id}`} className="hover:text-accent">{c.property}</Link>
      {c.assigned_by && <Badge tone="accent"><UserRound size={10} /> From {c.assigned_by_name || "Rakesh Sir"}</Badge>}
      {c.due && !/due/i.test(c.why_now || "") && <Badge tone={c.overdue ? "critical" : "high"}><CalendarClock size={10} /> {c.overdue ? "Was due" : "Due"} {fmtDate(c.due, "MMM d")}</Badge>}
      {c.due && /due/i.test(c.why_now || "") && <span className="tnum">{fmtDate(c.due, "EEE d MMM")}</span>}
      {!c.due && c.overdue && <Badge tone="critical">Late</Badge>}
      {c.amount != null && <span className="tnum text-muted">${c.amount.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>}
      {c.stale && <Badge tone="high">Reply needs a second look</Badge>}
    </div>
  );
}

/* One of the first five: number, what it is, why today, the one button. */
function NowCard({ c, n, a, mine }: { c: DayCard; n: number; a: ReturnType<typeof useActions>; mine: boolean }) {
  const t = typeOf(c);
  const [more, setMore] = React.useState(false);
  const long = (c.detail || "").length > 170;
  return (
    <li className={cn("relative flex gap-4 px-5 py-4", n === 1 && mine && "bg-accent-soft/40")}>
      <div className={cn("h-8 w-8 shrink-0 rounded-full grid place-items-center text-[13px] font-semibold tnum", n === 1 && mine ? "bg-accent text-white" : "bg-sunken text-muted")}>{n}</div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("inline-flex items-center gap-1 h-5 px-2 rounded-full text-[11px] font-medium", t.cls)}><t.icon size={11} /> {t.label}</span>
          {c.why_now && <span className={cn("inline-flex items-center gap-1 text-[12px]", c.overdue ? "text-critical font-medium" : "text-muted")}><Clock size={11} /> {c.why_now}</span>}
        </div>
        <div className="mt-1.5 text-[14.5px] font-medium leading-snug tracking-tight">{c.title}</div>
        {c.detail && (
          <div className="mt-1 text-[12.5px] text-muted leading-relaxed">
            <span className={cn(!more && long && "line-clamp-2")}>{c.detail}</span>
            {long && <button type="button" onClick={() => setMore((v) => !v)} className="ml-1 text-accent hover:underline">{more ? "less" : "more"}</button>}
          </div>
        )}
        <Meta c={c} />
      </div>
      <div className="shrink-0 self-center"><Action c={c} a={a} mine={mine} /></div>
    </li>
  );
}

function LaterRow({ c, a, mine }: { c: DayCard; a: ReturnType<typeof useActions>; mine: boolean }) {
  const t = typeOf(c);
  return (
    <li className="flex items-center gap-3 px-5 py-2.5">
      <span className={cn("h-6 w-6 shrink-0 rounded-md grid place-items-center", t.cls)} title={t.label}><t.icon size={12} /></span>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] leading-snug truncate" title={c.title}>{c.title}</div>
        <div className="text-[11.5px] text-faint truncate">{c.property}{c.why_now ? ` · ${c.why_now}` : ""}</div>
      </div>
      <div className="shrink-0"><Action c={c} a={a} mine={mine} /></div>
    </li>
  );
}

/* What a list is made of, in words: "35 to-dos · 19 replies to send". */
function madeOf(cards: DayCard[]) {
  const n = (t: DayCard["type"]) => cards.filter((c) => c.type === t).length;
  const part = (k: number, one: string, many: string) => (k ? `${k} ${k === 1 ? one : many}` : "");
  return [part(n("todo"), "to-do", "to-dos"), part(n("reply"), "reply to send", "replies to send"), part(n("bill"), "bill", "bills"), part(n("check"), "site check", "site checks")].filter(Boolean).join(" · ");
}

function Fold({ label, count, children, hint, open: start = false }: { label: string; count: number; children: React.ReactNode; hint?: string; open?: boolean }) {
  const [open, setOpen] = React.useState(start);
  if (!count) return null;
  return (
    <div className="border-t border-line">
      <button type="button" onClick={() => setOpen((v) => !v)} className="w-full flex items-center gap-2 px-5 h-10 text-[12.5px] text-muted hover:bg-sunken/60">
        <ChevronDown size={13} className={cn("transition", !open && "-rotate-90")} /> {label} <span className="tnum text-faint">· {count}</span>{hint && <span className="text-faint">— {hint}</span>}
      </button>
      {open && children}
    </div>
  );
}

function DoneList({ rows }: { rows: DayBoard["done_today"] }) {
  return (
    <ul className="px-5 pb-3 space-y-1.5">
      {rows.map((d, i) => (
        <li key={i} className="flex items-start gap-2 text-[12.5px] text-muted">
          <CheckCircle2 size={14} className="mt-0.5 text-good shrink-0" />
          <div className="min-w-0"><span className="line-through decoration-faint/60">{d.title}</span> <span className="text-faint">· {d.property}{d.closed_by === "call" ? " · settled on a call" : d.closed_by === "brain" ? " · settled by an email" : d.closed_by === "sweep" ? " · found done in the record" : ""}</span></div>
        </li>
      ))}
    </ul>
  );
}

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

function TeamCard({ rows, a, onGive }: { rows: TeamRow[]; a: ReturnType<typeof useActions>; onGive: (person: string | null) => void }) {
  const [open, setOpen] = React.useState<string | null>(null);
  const row = rows.find((r) => r.person === open);
  return (
    <Card className="mt-6 overflow-hidden">
      <div className="flex items-center justify-between px-5 pt-4 pb-3">
        <div><div className="text-[14px] font-semibold tracking-tight">The team</div><div className="text-xs text-muted mt-0.5">What is left for each person today. Click a name to see their list.</div></div>
        <Button size="sm" variant="soft" onClick={() => onGive(open)}><Plus size={13} /> Give a task</Button>
      </div>
      <div className="grid sm:grid-cols-3 gap-3 px-5 pb-4">
        {rows.map((r) => {
          const total = r.left + r.done_today;
          return (
            <button key={r.person} type="button" onClick={() => setOpen(open === r.person ? null : r.person)}
              className={cn("text-left rounded-xl border p-3.5 transition", open === r.person ? "border-accent bg-accent-soft/40" : "border-line hover:border-line-strong")}>
              <div className="flex items-center justify-between gap-2">
                <div className="text-[13.5px] font-semibold tracking-tight">{r.name}</div>
                {r.late > 0 ? <Badge tone="critical">{r.late} late</Badge> : r.left === 0 ? <Badge tone="good">All clear</Badge> : null}
              </div>
              <div className="mt-2 flex items-baseline gap-1.5"><span className="text-[22px] font-semibold tnum leading-none">{r.left}</span><span className="text-xs text-muted">left{r.done_today ? ` · ${r.done_today} done today` : ""}</span></div>
              <div className="mt-2.5 h-1.5 rounded-full bg-sunken overflow-hidden"><div className="h-full bg-good rounded-full" style={{ width: `${total ? Math.round((100 * r.done_today) / total) : 0}%` }} /></div>
              <div className="mt-2 text-[11.5px] text-faint truncate">{r.now[0] ? `First: ${r.now[0].title}` : "Nothing waiting"}</div>
            </button>
          );
        })}
      </div>
      {row && (
        <div className="border-t border-line">
          <div className="px-5 pt-3 pb-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">{row.name} — first {row.now.length}</div>
          {row.now.length === 0 ? <div className="px-5 pb-4 text-[13px] text-muted">Nothing is waiting for {row.name}.</div> : <ul className="divide-y divide-line">{row.now.map((c) => <LaterRow key={c.key} c={c} a={a} mine={false} />)}</ul>}
          <Fold label="After that" count={row.later.length} hint={madeOf(row.later)}><ul className="divide-y divide-line">{row.later.map((c) => <LaterRow key={c.key} c={c} a={a} mine={false} />)}</ul></Fold>
          <Fold label="Done today" count={row.done.length}><DoneList rows={row.done} /></Fold>
        </div>
      )}
    </Card>
  );
}

export function MyDay() {
  const q = useQuery({ queryKey: ["brain", "myday"], queryFn: () => api.get<DayBoard>("/brain/myday"), placeholderData: (prev) => prev, refetchInterval: 60_000 });
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [give, setGive] = React.useState<{ to: string | null } | null>(null);
  const openDraft = async (id: string) => { try { setDraft(await api.get<Draft>(`/brain/drafts/${id}`)); } catch (e) { toast.error(errText(e)); } };
  const a = useActions(openDraft);
  if (q.isLoading || !q.data) return <Skeleton className="h-72 mt-6" />;
  const d = q.data;
  const done = d.done_today.length;
  const total = d.left + done;
  return (
    <>
      <Card className="mt-6 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4 pb-3">
          <div>
            <div className="text-[15px] font-semibold tracking-tight">{d.now.length ? `Do these ${d.now.length === 1 ? "one" : d.now.length} first` : "Nothing is waiting for you"}</div>
            <div className="text-xs text-muted mt-0.5">{d.now.length ? "Start at number 1. When one is finished, the next moves up by itself." : "New things appear here as emails, calls and bills come in."}</div>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right"><div className="text-[12px] text-muted"><span className="font-semibold text-fg tnum">{done}</span> done today · <span className="tnum">{d.left}</span> left</div>
              <div className="mt-1.5 h-1.5 w-40 rounded-full bg-sunken overflow-hidden"><div className="h-full bg-good rounded-full transition-all" style={{ width: `${total ? Math.round((100 * done) / total) : 0}%` }} /></div></div>
          </div>
        </div>
        {d.now.length > 0 && <ol className="divide-y divide-line border-t border-line">{d.now.map((c, i) => <NowCard key={c.key} c={c} n={i + 1} a={a} mine />)}</ol>}
        <Fold label="Later" count={d.later.length} hint={madeOf(d.later)}><ul className="divide-y divide-line">{d.later.map((c) => <LaterRow key={c.key} c={c} a={a} mine />)}</ul></Fold>
        <Fold label="Done today" count={done}><DoneList rows={d.done_today} /></Fold>
      </Card>

      {d.team && <TeamCard rows={d.team} a={a} onGive={(to) => setGive({ to })} />}

      <Dialog open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent wide className="max-h-[90vh] overflow-y-auto" title="Reply">
          {draft && <DraftCard draft={draft} onChange={(next) => { if (next.status !== "ready") { setDraft(null); a.refresh(); } }} />}
        </DialogContent>
      </Dialog>
      <Dialog open={!!give} onOpenChange={(o) => !o && setGive(null)}>
        <DialogContent title="Give a task" description="It lands on their list, and they get an email from you.">
          {give && <GiveTask people={d.people} to={give.to} onClose={() => setGive(null)} />}
        </DialogContent>
      </Dialog>
    </>
  );
}
