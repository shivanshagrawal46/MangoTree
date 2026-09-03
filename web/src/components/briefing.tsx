"use client";

/* Morning briefing hero, the "what's new" card feed, and the deadlines board. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { Sunrise, RefreshCw, FileDown, X, Check, ChevronRight, Sparkles, CalendarClock, ArrowUpRight } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, CardHeader, Checkbox, Skeleton, Textarea, Dialog, DialogContent } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { cn, fmtDate, ago, money, propertyLabel, URGENCY } from "@/lib/utils";

/* ---------------------------------------------------------------- Briefing */
export function BriefingHero({ userName }: { userName: string }) {
  const qc = useQueryClient();
  const { open } = useEvidence();
  const q = useQuery({ queryKey: ["briefing"], queryFn: () => api.get<{ briefing: any; is_today: boolean }>("/briefing"), refetchInterval: 60_000 });
  const [busy, setBusy] = React.useState(false);
  const [expanded, setExpanded] = React.useState(false);
  const regenerate = async () => {
    setBusy(true);
    const { job_id } = await api.post<{ job_id: string }>("/briefing/generate");
    subscribeJob(job_id, () => {}, () => { setBusy(false); qc.invalidateQueries({ queryKey: ["briefing"] }); toast.success("Briefing refreshed"); });
  };
  const b = q.data?.briefing;
  if (q.isLoading) return <Skeleton className="h-36 rounded-3xl" />;
  const sections: any[] = (b?.sections || []).filter((s: any) => s.items?.length);
  const needs = sections.find((s: any) => /needs/i.test(s.title));
  const changed = sections.find((s: any) => /changed|new/i.test(s.title));
  const rest = sections.filter((s: any) => s !== needs && s !== changed);

  /* One line of a newspaper column: a quiet small-caps urgency word for anything
     pressing, the sentence itself, the property as a byline. No boxes. */
  const Item = ({ it, n, size = "lg" }: { it: any; n?: number; size?: "lg" | "md" }) => {
    const pressing = it.urgency === "critical" ? "Urgent" : it.urgency === "high" ? "This week" : it.urgency === "good" ? "In order" : null;
    const tone = it.urgency === "critical" ? "text-critical" : it.urgency === "high" ? "text-high" : it.urgency === "good" ? "text-good" : "text-faint";
    return (
      <li className={cn("brief-item py-2.5 flex gap-3", size === "lg" ? "text-[15px] leading-[1.5]" : "text-[14px] leading-[1.5]")}>
        {n !== undefined && <span className="serif text-faint tnum w-4 shrink-0 text-right pt-[1px]">{n}.</span>}
        <div className="min-w-0 flex-1 serif">
          {pressing && <span className={cn("smallcaps font-semibold mr-2 text-[0.85em]", tone)}>{pressing}</span>}
          <button className={cn("text-left text-fg", it.source_sha && "hover:text-accent transition-colors")} onClick={() => it.source_sha && open({ sha: it.source_sha })}>{it.text}</button>
          {it.property_id && <Link href={`/property/${it.property_id}`} className="ml-2 smallcaps text-[0.8em] text-muted hover:text-accent whitespace-nowrap">{propertyLabel(it.property_id)} ›</Link>}
        </div>
      </li>
    );
  };

  const dateLine = b ? new Date(b.day + "T00:00:00").toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" }) : "";

  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="paper rounded-3xl border border-line shadow-[var(--shadow-sm)] overflow-hidden">
      <div className="px-6 pt-4 pb-5">
        {/* masthead — the date is the first thing the eye lands on */}
        <div className="flex items-end justify-between gap-4">
          <div className="flex items-end gap-4">
            {b && (
              <div className="flex items-baseline gap-2 serif">
                <span className="text-[34px] leading-none font-medium tnum text-fg">{new Date(b.day + "T00:00:00").getDate()}</span>
                <div className="leading-tight">
                  <div className="text-[15px] font-medium text-fg">{new Date(b.day + "T00:00:00").toLocaleDateString("en-US", { weekday: "long" })}</div>
                  <div className="text-[12.5px] text-muted">{new Date(b.day + "T00:00:00").toLocaleDateString("en-US", { month: "long", year: "numeric" })}</div>
                </div>
              </div>
            )}
            <div className="smallcaps tracking-wider font-semibold text-[11px] text-muted flex items-center gap-2 pb-1"><Sunrise size={13} className="text-accent" /> Morning Brief for {userName}</div>
            {b && (q.data?.is_today ? <span className="pb-1 text-[10px] font-semibold uppercase tracking-wider text-good bg-good-soft rounded-full px-2 h-5 grid place-items-center">Today</span>
              : <span className="pb-1 text-[10px] font-semibold uppercase tracking-wider text-high bg-high-soft rounded-full px-2 h-5 grid place-items-center">Not today's — refresh</span>)}
          </div>
          <div className="flex items-center gap-3 text-[11px] text-muted pb-1">
            {b && <a href="/api/export/briefing.pdf" target="_blank" rel="noreferrer" className="hover:text-fg flex items-center gap-1"><FileDown size={12} /> PDF</a>}
            <button onClick={regenerate} disabled={busy} className="hover:text-fg flex items-center gap-1 disabled:opacity-50"><RefreshCw size={12} className={cn(busy && "animate-spin")} /> {busy ? "Writing…" : b ? "Refresh" : "Write my brief"}</button>
          </div>
        </div>
        <div className="hairline mt-3" />

        {!b && <div className="serif mt-4 text-[15px] text-muted">No briefing yet for {userName}. It is written automatically before 6 a.m.; press "Write my brief" for one now (about a minute).</div>}

        {b && (
          <>
            {/* headline + lede */}
            <h2 className="serif mt-4 text-[24px] leading-[1.22] font-medium tracking-[-0.01em] text-fg max-w-4xl">{b.headline}</h2>
            {b.closing && <p className="serif mt-1.5 text-[14.5px] italic text-muted max-w-3xl leading-relaxed">{b.closing}</p>}
            <div className="hairline mt-4" />

            {/* two newspaper columns — three items each, the rest behind the fold */}
            <div className="grid gap-x-10 lg:grid-cols-[1.2fr_1fr] pt-1">
              <section>
                <h3 className="smallcaps text-[11.5px] font-semibold text-muted pt-3 pb-0.5">Needs you today</h3>
                {needs ? <ol>{needs.items.slice(0, expanded ? 8 : 3).map((it: any, i: number) => <Item key={i} it={it} n={i + 1} />)}</ol>
                  : <p className="serif text-[15px] text-muted py-3">Nothing needs you today.</p>}
              </section>
              <section className="lg:border-l lg:border-line lg:pl-10">
                <h3 className="smallcaps text-[11.5px] font-semibold text-muted pt-3 pb-0.5">What's new</h3>
                {changed ? <ul>{changed.items.slice(0, expanded ? 8 : 3).map((it: any, i: number) => <Item key={i} it={it} size="md" />)}</ul>
                  : <p className="serif text-[14px] text-muted py-3">Nothing changed overnight.</p>}
              </section>
            </div>

            {/* the rest of the paper, folded */}
            <AnimatePresence initial={false}>
              {expanded && rest.length > 0 && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                  <div className="hairline mt-3" />
                  <div className="grid gap-x-10 md:grid-cols-2 xl:grid-cols-3">
                    {rest.map((s: any) => (
                      <section key={s.title}><h3 className="smallcaps text-[11.5px] font-semibold text-muted pt-3 pb-0.5">{s.title}</h3><ul>{s.items.slice(0, 5).map((it: any, i: number) => <Item key={i} it={it} size="md" />)}</ul></section>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
            <div className="hairline mt-3 pt-3 flex items-center justify-center gap-3">
              <button onClick={() => setExpanded((v) => !v)}
                className="inline-flex items-center gap-1.5 h-9 px-4 rounded-full bg-accent text-white text-[13px] font-medium shadow-sm hover:brightness-110 active:scale-[0.98] transition">
                {expanded ? "Show less" : "Read the full brief"} <ChevronRight size={14} className={cn("transition", expanded && "rotate-90")} />
              </button>
              {!expanded && <span className="text-[12px] text-faint">{Math.max(0, (needs?.items?.length || 0) - 3) + Math.max(0, (changed?.items?.length || 0) - 3) + rest.reduce((a: number, s: any) => a + (s.items?.length || 0), 0)} more items</span>}
            </div>
          </>
        )}
      </div>
    </motion.div>
  );
}

/* ------------------------------------------------------------- Cards feed */
const SIG: Record<number, { label: string; cls: string; bar: string }> = {
  5: { label: "Act today", cls: "text-critical", bar: "bg-critical" }, 4: { label: "This week", cls: "text-high", bar: "bg-high" },
  3: { label: "Worth knowing", cls: "text-fg", bar: "bg-accent" }, 2: { label: "Progress", cls: "text-muted", bar: "bg-info" }, 1: { label: "Background", cls: "text-faint", bar: "bg-faint" },
};

export function CardsFeed({ propertyId, limit = 8, variant = "grid" }: { propertyId?: string; limit?: number; variant?: "grid" | "row" | "list" }) {
  const qc = useQueryClient();
  const { open } = useEvidence();
  // Property sidebar reads newest-first by document date; the dashboard grid by significance.
  const order = variant === "list" ? "date" : "significance";
  const q = useQuery({ queryKey: ["cards", propertyId || "", order], queryFn: () => api.get<any[]>(`/cards?status=new&order=${order}${propertyId ? `&property_id=${propertyId}` : ""}`), refetchInterval: 60_000 });
  const [dismissing, setDismissing] = React.useState<any | null>(null);
  const [remark, setRemark] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const act = async (card: any, kind: "seen" | "dismiss") => {
    await api.post(`/cards/${card.card_id}/${kind}`, kind === "dismiss" ? { remark } : undefined);
    setDismissing(null); setRemark("");
    qc.invalidateQueries({ queryKey: ["cards"] });
    if (kind === "dismiss") toast.success("Dismissed — your remark teaches the next pass");
  };
  const detect = async () => {
    setBusy(true);
    const { job_id } = await api.post<{ job_id: string }>(`/cards/detect${propertyId ? `?property_id=${propertyId}` : ""}`);
    subscribeJob(job_id, () => {}, () => { setBusy(false); qc.invalidateQueries({ queryKey: ["cards"] }); toast.success("Checked for changes"); });
  };
  const cards = (q.data || []).slice(0, limit);
  const dismissDialog = (
    <Dialog open={!!dismissing} onOpenChange={(o) => !o && setDismissing(null)}>
      <DialogContent title="Dismiss this card" description="Say why in a few words — it becomes correction memory so the system stops raising cards like it.">
        <Textarea rows={3} value={remark} onChange={(e) => setRemark(e.target.value)} placeholder="e.g. Routine invoice, already handled by JP." autoFocus />
        <div className="mt-3 flex justify-end gap-2"><Button variant="ghost" onClick={() => setDismissing(null)}>Cancel</Button><Button variant="danger" onClick={() => dismissing && act(dismissing, "dismiss")}>Dismiss</Button></div>
      </DialogContent>
    </Dialog>
  );

  if (variant === "list") {
    // Sidebar: one card per row, compact, nothing side by side.
    return (
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <div className="text-[11px] uppercase tracking-wide text-faint">What's new {cards.length ? `· ${cards.length}` : ""}</div>
          <button onClick={detect} disabled={busy} className="text-[11px] text-muted hover:text-accent flex items-center gap-1 disabled:opacity-50"><Sparkles size={11} className={cn(busy && "animate-pulse")} /> {busy ? "Checking…" : "Check now"}</button>
        </div>
        {q.isLoading && <div className="space-y-2"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
        {!q.isLoading && cards.length === 0 && <div className="text-xs text-muted">Nothing new since the last look.</div>}
        <ul className="space-y-2">
          <AnimatePresence initial={false}>
            {cards.map((c) => { const s = SIG[c.significance] || SIG[3]; return (
              <motion.li key={c.card_id} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, height: 0 }} className="rounded-xl border border-line bg-elev overflow-hidden">
                <div className={cn("h-1", s.bar)} />
                <div className="px-3 py-2.5">
                  <div className="flex items-center justify-between"><span className={cn("text-[10px] font-bold uppercase tracking-wider", s.cls)}>{s.label}</span><span className="text-[10px] text-faint tnum">{fmtDate(c.source_date || c.created_at, "MMM d")}</span></div>
                  <button className="text-[13px] font-semibold leading-snug mt-1 text-left hover:text-accent" onClick={() => open({ sha: c.source_sha, highlight: c.quote.slice(0, 60) })}>{c.title}</button>
                  <div className="text-[12px] text-muted mt-1 leading-snug line-clamp-2">{c.why_it_matters || c.what_changed}</div>
                  {c.suggested_action && <div className="mt-1.5 text-[12px] flex items-start gap-1.5"><ArrowUpRight size={12} className="text-accent shrink-0 mt-0.5" /><span className="line-clamp-2"><span className="font-semibold">{c.owner ? `${c.owner}: ` : ""}</span>{c.suggested_action}</span></div>}
                  <div className="mt-2 flex items-center gap-1">
                    <Button size="sm" variant="soft" onClick={() => act(c, "seen")}><Check size={12} /> Got it</Button>
                    <Button size="sm" variant="ghost" onClick={() => setDismissing(c)}><X size={12} /> Dismiss</Button>
                  </div>
                </div>
              </motion.li>); })}
          </AnimatePresence>
        </ul>
        {dismissDialog}
      </div>
    );
  }

  if (variant === "row") {
    return (
      <div>
        <div className="flex items-center justify-between px-1 mb-2">
          <div className="text-[13px] font-semibold tracking-tight flex items-center gap-2">What's new <span className="text-[11px] font-normal text-faint">{cards.length ? `${cards.length} since the last look · scroll →` : ""}</span></div>
          <Button size="sm" variant="ghost" onClick={detect} disabled={busy}><Sparkles size={13} className={cn(busy && "animate-pulse")} /> {busy ? "Checking…" : "Check now"}</Button>
        </div>
        {q.isLoading && <div className="flex gap-3"><Skeleton className="h-28 w-80" /><Skeleton className="h-28 w-80" /></div>}
        {!q.isLoading && cards.length === 0 && <div className="rounded-2xl border border-dashed border-line px-4 py-4 text-sm text-muted">Nothing new since the last look.</div>}
        {cards.length > 0 && (
          <ul className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1 snap-x snap-mandatory">
            {cards.map((c) => { const s = SIG[c.significance] || SIG[3]; return (
              <li key={c.card_id} className="snap-start shrink-0 w-[340px] rounded-2xl border border-line bg-elev overflow-hidden flex flex-col">
                <div className={cn("h-1", s.bar)} />
                <div className="p-3.5 flex-1">
                  <div className="flex items-center justify-between"><span className={cn("text-[10px] font-bold uppercase tracking-wider", s.cls)}>{s.label}</span><span className="text-[11px] text-faint">{ago(c.created_at)}</span></div>
                  <div className="text-[14px] font-semibold leading-snug mt-1 line-clamp-2">{c.title}</div>
                  <div className="text-[12.5px] text-muted mt-1 leading-snug line-clamp-2">{c.why_it_matters || c.what_changed}</div>
                  {c.suggested_action && <div className="mt-2 text-[12px] flex items-start gap-1.5"><ArrowUpRight size={13} className="text-accent shrink-0 mt-0.5" /><span className="line-clamp-1"><span className="font-semibold">{c.owner ? `${c.owner}: ` : ""}</span>{c.suggested_action}</span></div>}
                </div>
                <div className="border-t border-line px-2 py-1.5 flex items-center gap-1 bg-sunken/40">
                  <Button size="sm" variant="ghost" onClick={() => open({ sha: c.source_sha, highlight: c.quote.slice(0, 60) })}>Source</Button>
                  <span className="flex-1" />
                  <Button size="sm" variant="soft" onClick={() => act(c, "seen")}><Check size={12} /> Got it</Button>
                  <Button size="icon" variant="ghost" title="Dismiss" onClick={() => setDismissing(c)}><X size={13} /></Button>
                </div>
              </li>); })}
          </ul>
        )}
        {dismissDialog}
      </div>
    );
  }

  return (
    <Card>
      <CardHeader title="What's new" sub="Changes since the last look, by significance. Every card has a quote behind it." right={<Button size="sm" variant="ghost" onClick={detect} disabled={busy}><Sparkles size={13} className={cn(busy && "animate-pulse")} /> {busy ? "Checking…" : "Check now"}</Button>} />
      {q.isLoading && <div className="px-5 pb-4 space-y-2"><Skeleton className="h-16" /><Skeleton className="h-16" /></div>}
      {!q.isLoading && cards.length === 0 && <div className="px-5 pb-6 text-sm text-muted">Nothing new since the last look.</div>}
      <ul className="px-4 pb-4 grid gap-3 md:grid-cols-2">
        <AnimatePresence initial={false}>
          {cards.map((c) => { const s = SIG[c.significance] || SIG[3]; const soft = { 5: "border-critical/40", 4: "border-high/40", 3: "border-line", 2: "border-line", 1: "border-line" }[c.significance as 1 | 2 | 3 | 4 | 5] || "border-line"; return (
            <motion.li key={c.card_id} layout initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98 }} className={cn("rounded-2xl border bg-bg overflow-hidden flex flex-col", soft)}>
              <div className={cn("h-1.5", s.bar)} />
              <div className="p-4 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className={cn("text-[10px] font-bold uppercase tracking-wider", s.cls)}>{s.label}</span>
                  <span className="text-[11px] text-faint">{!propertyId && c.property_id ? <Link href={`/property/${c.property_id}`} className="hover:text-accent">{propertyLabel(c.property_id)}</Link> : null}{!propertyId && c.property_id ? " · " : ""}{ago(c.created_at)}</span>
                </div>
                <div className="text-[15px] font-semibold leading-snug mt-1.5">{c.title}</div>
                <div className="text-[13px] text-muted mt-1.5 leading-relaxed">{c.why_it_matters || c.what_changed}</div>
                <button onClick={() => open({ sha: c.source_sha, highlight: c.quote.slice(0, 60) })} className="mt-3 w-full text-left rounded-xl bg-sunken/70 border border-line px-3 py-2 text-[12px] italic text-muted hover:text-fg hover:border-accent transition line-clamp-2">“{c.quote}”</button>
                {c.suggested_action && <div className="mt-3 flex items-start gap-2 text-[13px]"><ArrowUpRight size={14} className="text-accent shrink-0 mt-0.5" /><span><span className="font-semibold">{c.owner ? `${c.owner}: ` : ""}</span>{c.suggested_action}</span></div>}
              </div>
              <div className="border-t border-line px-3 py-2 flex items-center gap-1 bg-elev/60">
                <Button size="sm" variant="ghost" onClick={() => open({ sha: c.source_sha, highlight: c.quote.slice(0, 60) })}>Open source</Button>
                <span className="flex-1" />
                <Button size="sm" variant="soft" onClick={() => act(c, "seen")}><Check size={13} /> Got it</Button>
                <Button size="sm" variant="ghost" onClick={() => setDismissing(c)}><X size={13} /> Dismiss</Button>
              </div>
            </motion.li>); })}
        </AnimatePresence>
      </ul>
      {dismissDialog}
    </Card>
  );
}

/* ---------------------------------------------------------- Deadlines board */
export function DeadlinesBoard({ compact }: { compact?: boolean }) {
  const qc = useQueryClient();
  const { open } = useEvidence();
  const q = useQuery({ queryKey: ["deadlines"], queryFn: () => api.get<{ items: any[]; now: string }>("/deadlines?days=45") });
  const tick = async (task_id: string, done: boolean) => {
    await api.post(`/tasks/${task_id}/status`, { status: done ? "done" : "open" });
    qc.invalidateQueries({ queryKey: ["deadlines"] }); qc.invalidateQueries({ queryKey: ["tasks"] });
    if (done) toast.success("Done — recorded with your name");
  };
  const items = q.data?.items || [];
  const now = q.data?.now ? new Date(q.data.now) : new Date();
  const groups = items.reduce((acc, it) => { const d = new Date(it.date); const k = d < now ? "Overdue" : (d.getTime() - now.getTime()) / 864e5 < 7 ? "This week" : (d.getTime() - now.getTime()) / 864e5 < 14 ? "Next week" : "Later"; (acc[k] ||= []).push(it); return acc; }, {} as Record<string, any[]>);
  return (
    <Card>
      <CardHeader title="Deadlines board" sub="Every dated item across the portfolio — timeline events ahead and tasks with due dates. Tick tasks here; it ticks everywhere." right={<CalendarClock size={16} className="text-muted" />} />
      {q.isLoading && <div className="px-5 pb-4"><Skeleton className="h-24" /></div>}
      {!q.isLoading && items.length === 0 && <div className="px-5 pb-6 text-sm text-muted">Nothing dated in the next 45 days.</div>}
      <div className="px-5 pb-4 space-y-4">
        {["This week", "Overdue", "Next week", "Later"].filter((k) => groups[k]?.length).map((k) => (
          <div key={k}>
            <div className={cn("text-[11px] uppercase tracking-wide mb-1.5", k === "Overdue" ? "text-critical" : k === "This week" ? "text-high" : "text-faint")}>{k} · {groups[k].length}</div>
            <ul className="divide-y divide-line rounded-xl border border-line bg-bg overflow-hidden">
              {groups[k].slice(0, compact ? 6 : 40).map((it: any, i: number) => (
                <li key={i} className="flex items-center gap-3 px-3 py-2 hover:bg-sunken">
                  {it.kind === "task" ? <Checkbox checked={it.status === "done"} onCheckedChange={(v) => tick(it.task_id, v)} size={22} /> : <span className={cn("h-[22px] w-[22px] rounded-lg grid place-items-center", it.type === "default" || it.type === "legal" ? "bg-critical-soft" : "bg-info-soft")} title="Timeline event"><span className={cn("h-2 w-2 rounded-full", it.type === "default" || it.type === "legal" ? "bg-critical" : "bg-info")} /></span>}
                  <span className="text-[11px] tnum text-faint w-14 shrink-0">{fmtDate(it.date, "MMM d")}</span>
                  <button className="min-w-0 flex-1 text-left text-[13px] truncate hover:text-accent" onClick={() => it.source_sha && open({ sha: it.source_sha })}>{it.title}</button>
                  {it.owner && <Badge tone="accent">{it.owner}</Badge>}
                  {typeof it.amount === "number" && <span className="text-xs tnum font-medium">{money(it.amount, true)}</span>}
                  {it.property_id && <Link href={`/property/${it.property_id}`} className="text-[11px] text-faint hover:text-accent truncate max-w-[120px]">{propertyLabel(it.property_id)}</Link>}
                  <Badge>{it.kind === "task" ? it.type : it.type.replace("_", " ")}</Badge>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Card>
  );
}
