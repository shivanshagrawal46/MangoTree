"use client";

/* My day — one person's own work, and nothing else (the layout agreed on 2026-11-18).

   Four numbers at the top. Then two lists side by side, never mixed:
     · Replies to send  — grouped Late / Today / Waiting longer; one button, "Read and send".
     · My to-dos        — grouped Decide / Do; one button, "Done".
   (JP Sir and Manjunath Sir have "Bills and site checks" where Rakesh Sir has replies.)
   About seven rows of each show; "more" opens the rest in place. A click on a row's words
   opens that row in place: the whole line, its detail, and the email or call it came from.
   One thin strip under them says what came in from the sites.

   The team is NOT here. It has its own page (TeamPage, Rakesh Sir only): he sees each
   person's list, gives a task, sends a reminder — and cannot mark another person's work
   done. Only the owner can; the server refuses anyone else. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, Check, ChevronDown, Plus, Send } from "lucide-react";
import { api } from "@/lib/api";
import { useEvidence } from "@/components/evidence";
import { Button, Card, Dialog, DialogContent, Input, Select, Skeleton, Textarea } from "@/components/ui";
import { DraftCard } from "@/components/brain/draft";
import type { Alerts } from "@/components/brain/followups";
import { cn } from "@/lib/utils";
import { errText, type DayBoard, type DayCard, type DayCheck, type Draft, type TeamRow } from "@/lib/brain";

const SHOW = 7;                                   // rows of a list before "more"
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
type Actions = ReturnType<typeof useActions>;

/* ───────────────────────── small pieces ───────────────────────── */

function Tile({ label, value, hint, late }: { label: string; value: number; hint: string; late?: boolean }) {
  return (
    <div className="rounded-xl border border-line bg-elev px-4 py-3">
      <div className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-faint">{label}</div>
      <div className={cn("mt-0.5 text-[26px] font-semibold tracking-tight leading-tight tnum", late && value > 0 && "text-critical")}>{value}</div>
      <div className="text-[12px] text-muted truncate">{hint}</div>
    </div>
  );
}

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

function ReplyRow({ c, a }: { c: DayCard; a: Actions }) {
  const waited = daysSince(c.received);
  const said = c.why_now && !/^(new|open for|the reply is written)/i.test(c.why_now) ? c.why_now : "";
  const note = [waited ? `waiting ${dayWord(waited)}` : "came in today", said].filter(Boolean).join(" · ");
  return (
    <Row c={c} title={`${c.from_name || "Reply"} — ${c.subject || c.title}`} {...where(c)} note={note}
      chips={<>{lateChip(c)}{c.stale && <Chip tone="warn">changed — read again</Chip>}</>}
      right={<Button size="sm" variant={c.stale ? "secondary" : "primary"} onClick={() => c.draft_id && a.openDraft(c.draft_id)}>{c.stale ? "Read again" : <><Send size={12} /> Read and send</>}</Button>} />
  );
}

function TodoRow({ c, a }: { c: DayCard; a: Actions }) {
  const due = c.due && !c.overdue && !dueToday(c) ? dueText(c) : "";
  const why = c.why_now && !/^(new|open for|waiting for your decision|due |was due)/i.test(c.why_now) ? c.why_now : "";
  return (
    <Row c={c} title={c.title} {...where(c)} note={[due, why].filter(Boolean).join(" · ") || undefined}
      chips={<>{lateChip(c)}{c.assigned_by && <Chip tone="plain">from {c.assigned_by_name || "Rakesh Sir"}</Chip>}</>}
      right={<Button size="sm" variant="secondary" onClick={() => a.done(c)}><Check size={12} /> Done</Button>} />
  );
}

function WorkRow({ c }: { c: DayCard }) {
  const note = [c.amount != null ? usd(c.amount) : "", c.days ? `${dayWord(c.days)} old` : ""].filter(Boolean).join(" · ");
  return <Row c={c} title={c.title} {...where(c)} note={note || undefined} chips={c.overdue ? <Chip tone="late">late</Chip> : null}
    right={<Link href={c.href}><Button size="sm" variant="secondary">Open <ArrowRight size={12} /></Button></Link>} />;
}

type Group = { label: string; cards: DayCard[] };

/* One list: its heading, its groups, about seven rows, then "more" in place. */
function ListCard({ title, groups, empty, row, all }: { title: string; groups: Group[]; empty: string; row: (c: DayCard) => React.ReactNode; all?: { href: string; label: string } }) {
  const [open, setOpen] = React.useState(false);
  const total = groups.reduce((n, g) => n + g.cards.length, 0);
  // How many rows of each group show: the first SHOW rows of the whole list, group by group.
  const before = groups.map((_, i) => groups.slice(0, i).reduce((n, g) => n + g.cards.length, 0));
  const shown = groups.map((g, i) => (open ? g.cards : g.cards.slice(0, Math.max(0, SHOW - before[i]))));
  return (
    <Card className="overflow-hidden self-start">
      <div className="flex items-baseline justify-between px-4 pt-3 pb-2.5">
        <h2 className="text-[15px] font-semibold tracking-tight">{title} <span className="font-medium text-faint tnum">· {total}</span></h2>
        {all && total > 0 && <Link href={all.href} className="text-[12.5px] font-semibold text-accent hover:underline">{all.label} →</Link>}
      </div>
      {total === 0 && <div className="border-t border-line px-4 py-6 text-[13px] text-muted">{empty}</div>}
      {groups.map((g, i) => {
        const rows = shown[i];
        if (!rows.length) return null;
        return (
          <React.Fragment key={g.label}>
            <div className="border-t border-line bg-sunken/70 px-4 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-faint">{g.label} · {g.cards.length}</div>
            <ul>{rows.map((c) => <React.Fragment key={c.key}>{row(c)}</React.Fragment>)}</ul>
          </React.Fragment>
        );
      })}
      {total > SHOW && (
        <button type="button" onClick={() => setOpen((v) => !v)} className="w-full border-t border-line bg-sunken/30 px-4 py-2.5 text-center text-[12.5px] font-semibold text-accent hover:bg-sunken/70">
          {open ? "Show fewer" : `${total - SHOW} more →`}
        </button>
      )}
    </Card>
  );
}

/* Late first, then by due date, then in the order the day was planned. */
function byUrgency(cards: DayCard[]) {
  const t = (c: DayCard) => (c.due ? Date.parse(dueDay(c)) : Number.MAX_SAFE_INTEGER);
  return cards.map((c, i) => ({ c, i })).sort((x, y) => Number(!!y.c.overdue) - Number(!!x.c.overdue) || t(x.c) - t(y.c) || x.i - y.i).map((x) => x.c);
}

function split(cards: DayCard[]) {
  const replies = cards.filter((c) => c.type === "reply");
  const todos = cards.filter((c) => c.type === "todo");
  const work = cards.filter((c) => c.type === "bill" || c.type === "check");
  const fresh = (c: DayCard) => dueToday(c) || (daysSince(c.received) ?? 99) <= 1;
  return {
    replies, todos, work,
    replyGroups: [
      { label: "Late", cards: replies.filter((c) => c.overdue) },
      { label: "Today", cards: replies.filter((c) => !c.overdue && fresh(c)) },
      { label: "Waiting longer", cards: replies.filter((c) => !c.overdue && !fresh(c)) },
    ],
    todoGroups: [
      { label: "Decide", cards: byUrgency(todos.filter((c) => c.kind === "decision")) },
      { label: "Do", cards: byUrgency(todos.filter((c) => c.kind !== "decision")) },
    ],
    workGroups: [
      { label: "Bills", cards: work.filter((c) => c.type === "bill") },
      { label: "Site checks", cards: work.filter((c) => c.type === "check") },
    ],
  };
}

/* What came in from the sites: one thin strip, not a card of its own. */
function SiteStrip() {
  const q = useQuery({ queryKey: ["brain", "alerts"], queryFn: () => api.get<Alerts>("/brain/alerts"), staleTime: 60_000, refetchInterval: 180_000 });
  const a = q.data;
  if (!a || (a.new_logs.length === 0 && a.in_review.count === 0)) return null;
  const logs = a.new_logs.reduce((n, l) => n + l.count, 0);
  const fresh = a.in_review.bills.filter((b) => b.new);
  return (
    <Card className="mt-3.5 grid sm:grid-cols-[1fr_1fr_auto] overflow-hidden">
      <div className="px-4 py-2.5 text-[13px] min-w-0">
        <span className="block text-[10.5px] font-semibold uppercase tracking-[0.09em] text-faint">New from the sites</span>
        {logs === 0 ? <span className="text-muted">No new daily log since Buildertrend was last read.</span> : (
          <span className="block truncate">{a.new_logs.map((l, i) => <React.Fragment key={l.property_id}>{i > 0 && " · "}<Link href={`/property/${l.property_id}?tab=site`} className="font-semibold hover:text-accent">{l.property}</Link> {l.count} new log{l.count === 1 ? "" : "s"}</React.Fragment>)}</span>
        )}
      </div>
      <div className="px-4 py-2.5 text-[13px] min-w-0 sm:border-l border-line">
        <span className="block text-[10.5px] font-semibold uppercase tracking-[0.09em] text-faint">Bills waiting</span>
        {a.in_review.count === 0 ? <span className="text-muted">None.</span> : (
          <span className="block truncate"><b className="font-semibold tnum">{a.in_review.count} bill{a.in_review.count === 1 ? "" : "s"} · {usd(a.in_review.amount)}</b>
            {fresh.length > 0 && <> · {fresh.length} new: {fresh.map((b) => `${b.property.replace(/^\d+\s+/, "")} ${b.title.toLowerCase()}`).join(", ")}</>}</span>
        )}
      </div>
      <div className="px-4 py-2.5 self-center sm:border-l border-line"><Link href="/bills" className="text-[12.5px] font-semibold text-accent hover:underline whitespace-nowrap">Open bills →</Link></div>
    </Card>
  );
}

/* What the last daily run settled: one line, and the to-dos it took off the list on request. */
function CheckLine({ k }: { k: DayCheck }) {
  const [open, setOpen] = React.useState(false);
  const when = new Date(k.at).toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "2-digit" });
  const e = k.emails;
  const n = (x: number, one: string, many: string) => `${x} ${x === 1 ? one : many}`;
  return (
    <div className="mt-4 rounded-lg border border-line bg-elev px-4 py-2 text-[12.5px] text-muted">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <b className="font-semibold text-fg">Last check: {when} ET</b>
        {e && <span>{n(e.read, "new email", "new emails")} read: {n(e.reply_needed, "needs", "need")} a reply, {n(e.no_reply_needed, "needs", "need")} none{e.found_answered > 0 ? `; ${n(e.found_answered, "waiting reply was", "waiting replies were")} found already sent` : ""}.</span>}
        <span>Your to-dos: {k.n_closed > 0 ? `${k.n_closed} found done and taken off` : "none found done"}, <b className="font-semibold text-fg tnum">{k.left}</b> left.</span>
        {k.closed.length > 0 && <button type="button" onClick={() => setOpen((v) => !v)} className="font-semibold text-accent hover:underline">{open ? "Hide" : "See which"}</button>}
      </div>
      {open && (
        <ul className="mt-1.5 space-y-1 border-t border-line pt-1.5">
          {k.closed.map((c, i) => (
            <li key={i}><span className="font-semibold text-fg">{c.title}</span> · {c.property} · {c.how}{c.note ? <span> — {c.note}</span> : null}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ───────────────────────── Today: my own work ───────────────────────── */

export function MyDay() {
  const q = useQuery({ queryKey: ["brain", "myday"], queryFn: () => api.get<DayBoard>("/brain/myday"), placeholderData: (prev) => prev, refetchInterval: 60_000 });
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const openDraft = async (id: string) => { try { setDraft(await api.get<Draft>(`/brain/drafts/${id}`)); } catch (e) { toast.error(errText(e)); } };
  const a = useActions(openDraft);
  if (q.isLoading || !q.data) return <div className="mt-5 space-y-3.5"><Skeleton className="h-20" /><Skeleton className="h-96" /></div>;
  const d = q.data;
  const s = split([...d.now, ...d.later]);
  const decide = s.todoGroups[0].cards, doing = s.todoGroups[1].cards;
  const late = [...s.replies, ...s.todos, ...s.work].filter((c) => c.overdue);
  const soon = decide.filter((c) => c.due && daysLate(c) >= -7).length;
  const changed = s.replies.filter((c) => c.stale).length;
  const lateBy = [[late.filter((c) => c.type === "reply").length, "reply", "replies"], [late.filter((c) => c.type === "todo").length, "to-do", "to-dos"],
    [late.filter((c) => c.type === "bill" || c.type === "check").length, "bill or check", "bills and checks"]] as const;
  const lists = [s.replies.length > 0, s.work.length > 0, true].filter(Boolean).length;
  return (
    <>
      {d.check && <CheckLine k={d.check} />}
      <div className={cn("mt-3 grid gap-3 grid-cols-2", s.replies.length > 0 && s.work.length > 0 ? "lg:grid-cols-5" : s.replies.length > 0 || s.work.length > 0 ? "lg:grid-cols-4" : "lg:grid-cols-3")}>
        {s.replies.length > 0 && <Tile label="Replies to send" value={s.replies.length} hint={changed ? `${changed} changed since written — read again` : "all written and ready to read"} />}
        {s.work.length > 0 && <Tile label="Bills and site checks" value={s.work.length} hint={`${s.workGroups[0].cards.length} bill${s.workGroups[0].cards.length === 1 ? "" : "s"} · ${s.workGroups[1].cards.length} site check${s.workGroups[1].cards.length === 1 ? "" : "s"}`} />}
        <Tile label="Decisions waiting for you" value={decide.length} hint={soon ? `${soon} due this week` : "none due this week"} />
        <Tile label="Things to do" value={doing.length} hint={`${d.done_today.length} done today`} />
        <Tile label="Late" value={late.length} late hint={late.length ? lateBy.filter(([n]) => n > 0).map(([n, one, many]) => `${n} ${n === 1 ? one : many}`).join(" · ") : "nothing is late"} />
      </div>

      <div className={cn("mt-3.5 grid gap-3.5", lists > 1 && "lg:grid-cols-2")}>
        {s.replies.length > 0 && <ListCard title="Replies to send" groups={s.replyGroups} empty="No reply is waiting to be sent." all={{ href: "/replies", label: "See all replies" }} row={(c) => <ReplyRow c={c} a={a} />} />}
        {s.work.length > 0 && <ListCard title="Bills and site checks" groups={s.workGroups} empty="No bill or site check is waiting." all={{ href: "/chase", label: "See all follow-ups" }} row={(c) => <WorkRow c={c} />} />}
        <ListCard title="My to-dos" groups={s.todoGroups} empty="Nothing is waiting for you. New things appear here as emails, calls and bills come in." row={(c) => <TodoRow c={c} a={a} />} />
      </div>

      <SiteStrip />

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
