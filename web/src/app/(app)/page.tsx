"use client";

/* The home screen. One glance: what needs a person, what's handled, what's new,
   how the money looks, what's due. Then your tasks. Nothing decorative. */

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { ArrowRight, Inbox, AlertTriangle, CalendarClock, CheckSquare, Sparkles, Bot, MessageSquare, TrendingUp, FileDown, Building2 } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, CardHeader, Skeleton, Stat } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { MoneyFlow, PortfolioBars } from "@/components/charts";
import { TaskBoard } from "@/components/tasks";
import { BriefingHero, CardsFeed, DeadlinesBoard } from "@/components/briefing";
import { cn, fmtDate, ago, money, HEALTH, propertyLabel } from "@/lib/utils";
import type { Dashboard, PropertySummary, LedgerPortfolio } from "@/lib/types";
import { Figure } from "@/components/ledger";

export default function DashboardPage() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/dashboard"), refetchInterval: 90_000 });
  const lq = useQuery({ queryKey: ["ledger-portfolio"], queryFn: () => api.get<LedgerPortfolio>("/ledger"), refetchInterval: 300_000 });
  const ledger = lq.data;
  const router = useRouter();
  const { open } = useEvidence();
  const [ask, setAsk] = React.useState("");
  if (q.isLoading || !q.data) return <div className="p-6 space-y-4"><Skeleton className="h-40 rounded-3xl" /><div className="grid gap-4 md:grid-cols-4">{[...Array(4)].map((_, i) => <Skeleton key={i} className="h-24" />)}</div><Skeleton className="h-64" /></div>;
  const d = q.data;
  const na = d.needs_attention;
  const me = d.user;
  const owner = { rakesh: "Rakesh", jp: "JP", manjunath: "Manjunath" }[me.user_id] || "Rakesh";
  const portfolio = d.portfolio;
  const critical = portfolio.filter((p) => p.health.level === "critical");
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  const attention = [
    { n: na.overdue_tasks.length, label: "tasks past due", sub: na.overdue_tasks[0]?.title, href: "/tasks?status=open", icon: CheckSquare, tone: "critical" },
    { n: na.risk_events.length, label: "default / legal events in 60 days", sub: na.risk_events[0] ? `${propertyLabel(na.risk_events[0].property_id)} — ${na.risk_events[0].title}` : "", href: na.risk_events[0] ? `/property/${na.risk_events[0].property_id}?tab=timeline` : "/", icon: AlertTriangle, tone: "critical" },
    { n: na.deadlines.length, label: "dated items in the next 3 weeks", sub: na.deadlines[0]?.title, href: "#deadlines", icon: CalendarClock, tone: "high" },
    { n: na.unplaced.count, label: "documents waiting to be placed", sub: na.unplaced.oldest ? `oldest ${ago(na.unplaced.oldest)}` : "", href: "/review", icon: Inbox, tone: "high" },
    { n: na.suggested_tasks, label: "AI-suggested tasks to accept or drop", sub: "each with its evidence", href: "/tasks?status=suggested", icon: Sparkles, tone: "accent" },
    { n: na.low_confidence, label: "low-confidence placements", sub: "filed, but worth a glance", href: "/review", icon: Inbox, tone: "neutral" },
  ].filter((a) => a.n > 0);
  const total = attention.reduce((a, x) => a + x.n, 0);

  return (
    <div className="p-6 max-w-[1440px] mx-auto space-y-5">
      {/* header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight">{greeting}, {me.name}.</h1>
          <p className="text-sm text-muted mt-1">{total ? <><b className="text-fg tnum">{total}</b> things need a person.</> : "Nothing needs you right now."}{critical.length ? <> <b className="text-critical tnum">{critical.length}</b> propert{critical.length > 1 ? "ies" : "y"} need{critical.length > 1 ? "" : "s"} attention.</> : " All properties steady."} Everything else is handled.</p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); if (ask.trim()) router.push(`/ask?q=${encodeURIComponent(ask.trim())}`); }} className="flex items-center gap-2 w-full md:w-[480px]">
          <div className="relative flex-1"><MessageSquare size={14} className="absolute left-3 top-3 text-faint" /><input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Ask across every property… (⌘K anywhere)" className="h-10 w-full rounded-xl border border-line bg-elev pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent/30 shadow-[var(--shadow-sm)]" /></div>
          <Button type="submit" variant="primary">Ask <ArrowRight size={14} /></Button>
        </form>
      </div>

      {/* briefing */}
      <BriefingHero userName={me.name} />

      {/* KPI strip */}
      {/* Money from the ledger only (documented movements, quote-verified). The
          earlier "out / back / net" summed every dollar mentioned in any event and
          was wrong by an order of magnitude. A figure covers only the properties
          whose ledger is established; the count says how many. */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Kpi label="Needs a person" value={total} tone={total ? "text-high" : "text-good"} sub={total ? "items below" : "clear"} />
        <Kpi label="Properties at risk" value={critical.length} tone={critical.length ? "text-critical" : "text-good"} sub={critical.map((p) => propertyLabel(p.property_id)).slice(0, 2).join(", ") || "none"} />
        <Kpi label="Invested" value={ledger?.invested != null ? money(ledger.invested, true) : <span className="text-muted text-base font-medium">not established</span>} tone="text-money-in" sub={ledger ? `${ledger.established} of ${ledger.properties} properties documented` : "ledger not built"} />
        <Kpi label="Owed to RKB" value={ledger?.owed != null ? money(ledger.owed, true) : <span className="text-muted text-base font-medium">not established</span>} tone="text-fg" sub={ledger ? `latest balance statements · ${ledger.owed_properties} properties` : ""} />
        <Kpi label="Money risks" value={ledger ? ledger.risks.filter((r) => r.severity === "critical").length : "—"} tone={ledger && ledger.risks.some((r) => r.severity === "critical") ? "text-critical" : "text-good"} sub={ledger ? `${ledger.risks.length} flagged in the ledgers` : ""} />
        <Kpi label="My open tasks" value={na.my_open_tasks} tone="text-fg" sub={`${d.tasks.by_status?.suggested || 0} suggested by AI`} />
      </div>

      {/* attention + handled + what's new */}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-1">
          <CardHeader title="Needs a person" sub="Most urgent first" right={<Badge tone={attention.length ? "high" : "good"}>{attention.length ? `${attention.length} kinds` : "clear"}</Badge>} />
          <ul className="px-3 pb-3 space-y-1">
            {attention.length === 0 && <li className="text-sm text-muted px-2 py-6 text-center">Nothing waiting.</li>}
            {attention.map((a, i) => (
              <motion.li key={a.label} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}>
                <Link href={a.href} className="flex items-center gap-3 rounded-xl px-3 py-2.5 hover:bg-sunken transition group">
                  <div className={cn("h-9 w-9 rounded-xl grid place-items-center shrink-0", { critical: "bg-critical-soft text-critical", high: "bg-high-soft text-high", accent: "bg-accent-soft text-accent", neutral: "bg-sunken text-muted" }[a.tone])}><a.icon size={16} /></div>
                  <div className="min-w-0 flex-1"><div className="text-[13.5px]"><span className="font-semibold tnum">{a.n}</span> {a.label}</div>{a.sub && <div className="text-xs text-muted truncate">{a.sub}</div>}</div>
                  <ArrowRight size={14} className="text-faint group-hover:text-fg transition" />
                </Link>
              </motion.li>
            ))}
          </ul>
          <div className="border-t border-line px-5 py-3">
            <div className="text-[11px] uppercase tracking-wide text-faint mb-1.5 flex items-center gap-1.5"><Bot size={12} className="text-accent" /> Handled by the system · 36h</div>
            <ul className="space-y-1">{d.handled.slice(0, 6).map((h) => <li key={h.kind} className="flex items-center justify-between text-xs"><span className="text-muted truncate">{h.label}</span><span className="font-semibold tnum ml-2">{h.count.toLocaleString()}</span></li>)}{d.handled.length === 0 && <li className="text-xs text-muted">Quiet.</li>}</ul>
          </div>
          <IntakeRow intake={d.intake} onDone={() => q.refetch()} />
        </Card>
        <div className="xl:col-span-2"><CardsFeed limit={6} /></div>
      </div>

      {/* portfolio */}
      <Card>
        <CardHeader title="Portfolio" sub={`${portfolio.length} properties · health derived from timeline events`} right={<div className="flex items-center gap-1"><a href="/api/export/portfolio.xlsx"><Button size="sm" variant="ghost"><FileDown size={13} /> Excel</Button></a><Building2 size={16} className="text-muted ml-1" /></div>} />
        <div className="px-4 pb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {[...portfolio].sort((a, b) => ({ critical: 0, watch: 1, good: 2 }[a.health.level] - { critical: 0, watch: 1, good: 2 }[b.health.level])).map((p, i) => <PropertyCard key={p.property_id} p={p} i={i} />)}
        </div>
      </Card>

      {/* deadlines */}
      <div id="deadlines"><DeadlinesBoard compact /></div>

      {/* money — ledger only */}
      <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
        <Card>
          <CardHeader title="Invested and owed, by property" sub="From each property's ledger of documented movements. Blank = the documents do not establish it." right={<a href="/api/export/portfolio.xlsx"><Button size="sm" variant="ghost"><FileDown size={13} /> Excel</Button></a>} />
          <div className="px-2 pb-2"><PortfolioBars rows={portfolio.filter((p) => p.money.established).map((p) => ({ name: propertyLabel(p.property_id).replace(" St", "").replace(" Nw", ""), in: p.money.invested || 0, back: p.money.owed || 0 }))} /></div>
          <div className="px-5 pb-3 text-[11px] text-faint">Bars: invested (documented) and latest owed balance. {portfolio.filter((p) => !p.money.established).length > 0 && <>Not established for {portfolio.filter((p) => !p.money.established).map((p) => propertyLabel(p.property_id)).join(", ")}.</>}</div>
        </Card>
        <Card>
          <CardHeader title="Money risks" sub="Flagged in the ledgers, most severe first" right={<TrendingUp size={16} className="text-muted" />} />
          <ul className="px-5 pb-4 space-y-2">
            {(ledger?.risks || []).slice(0, 7).map((r, i) => (
              <li key={i} className="text-xs flex items-start gap-2 cursor-pointer hover:text-accent" onClick={() => open({ sha: r.source_sha, highlight: r.quote.slice(0, 60) })}>
                <span className={cn("mt-1 h-1.5 w-1.5 rounded-full shrink-0", r.severity === "critical" ? "bg-critical" : r.severity === "high" ? "bg-high" : "bg-line-strong")} />
                <span className="min-w-0"><span className="font-medium">{propertyLabel(r.property_id)}</span> <span className="text-muted">— {r.title}</span></span>
              </li>
            ))}
            {!ledger?.risks?.length && <li className="text-xs text-muted">None flagged.</li>}
          </ul>
        </Card>
      </div>

      {/* my tasks */}
      <Card>
        <CardHeader title={`Tasks for ${owner}`} sub="Yours first. Tick to mark done — saved with your name." right={<div className="flex items-center gap-1"><a href={`/api/export/tasks.xlsx?owner=${owner}`}><Button size="sm" variant="ghost"><FileDown size={13} /> Excel</Button></a><Link href="/tasks"><Button size="sm" variant="ghost">All tasks <ArrowRight size={13} /></Button></Link></div>} />
        <div className="px-5 pb-5"><TaskBoard ownerFilter={owner} statusFilter="open" groupBy="property" compact showAdd /></div>
      </Card>
    </div>
  );
}

/* Mail intake: when both mailboxes were last read, what came in today, and a
   one-click "check now". Green dot = both mailboxes read within two polls. */
function IntakeRow({ intake, onDone }: { intake?: Dashboard["intake"]; onDone: () => void }) {
  const [busy, setBusy] = React.useState<string | null>(null);
  if (!intake) return null;
  const poll = intake.poll_minutes || 10;
  const fresh = (iso?: string) => !!iso && Date.now() - new Date(iso).getTime() < poll * 2 * 60_000;
  const gOk = fresh(intake.gmail_last_ok), oOk = fresh(intake.outlook_last_ok);
  const lr = intake.last_run;
  const err = intake.error || (lr?.source_errors && Object.keys(lr.source_errors).length ? Object.entries(lr.source_errors).map(([k, v]) => `${k}: ${v}`).join(" · ") : null);
  const checkNow = async () => {
    setBusy("Checking…");
    try {
      const { job_id } = await api.post<{ job_id: string }>("/intake/check-now", {});
      await new Promise<void>((resolve) => subscribeJob(job_id, (ev) => { if (ev.kind === "status") setBusy(ev.data?.text || "Working…"); if (ev.kind === "error") setBusy(ev.data?.error || "Failed"); }, resolve));
    } catch (e: any) { setBusy(e?.message || "Failed"); await new Promise((r) => setTimeout(r, 2500)); }
    setBusy(null); onDone();
  };
  return (
    <div className="border-t border-line px-5 py-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-wide text-faint flex items-center gap-1.5"><Inbox size={12} className="text-accent" /> Mail intake · every {poll} min</div>
        <button onClick={checkNow} disabled={!!busy} className="text-[11px] font-medium text-accent hover:underline disabled:opacity-60">{busy ? "Working…" : "Check now"}</button>
      </div>
      <ul className="mt-1.5 space-y-1 text-xs">
        <li className="flex items-center justify-between"><span className="text-muted flex items-center gap-1.5"><span className={cn("h-1.5 w-1.5 rounded-full", gOk ? "bg-good" : "bg-high")} />Gmail</span><span className="text-faint tnum">{intake.gmail_last_ok ? ago(intake.gmail_last_ok) : "never"}</span></li>
        <li className="flex items-center justify-between"><span className="text-muted flex items-center gap-1.5"><span className={cn("h-1.5 w-1.5 rounded-full", oOk ? "bg-good" : "bg-high")} />Outlook</span><span className="text-faint tnum">{intake.outlook_last_ok ? ago(intake.outlook_last_ok) : "never"}</span></li>
        <li className="flex items-center justify-between"><span className="text-muted">Today</span><span className="tnum"><b className="text-fg">{intake.today?.ingested ?? 0}</b> new of {intake.today?.seen ?? 0} seen{intake.today?.errors ? <span className="text-critical"> · {intake.today.errors} errors</span> : null}</span></li>
        {intake.last_arrival && <li className="flex items-center justify-between"><span className="text-muted">Last processed</span><span className="text-faint tnum">{intake.last_arrival.emails} email{intake.last_arrival.emails === 1 ? "" : "s"} · {intake.last_arrival.elapsed_s}s{intake.last_arrival.errors?.length ? <span className="text-critical"> · {intake.last_arrival.errors.length} stage error</span> : null}</span></li>}
        {busy && <li className="text-accent">{busy}</li>}
        {err && <li className="text-critical break-words">{err}</li>}
      </ul>
    </div>
  );
}

function Kpi({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: string; tone?: string }) {
  return (
    <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} className="rounded-2xl border border-line bg-elev px-4 py-3 shadow-[var(--shadow-sm)]">
      <Stat label={label} value={value} sub={sub} tone={tone} />
    </motion.div>
  );
}

export function PropertyCard({ p, i = 0 }: { p: PropertySummary; i?: number }) {
  const h = HEALTH[p.health.level] || HEALTH.good;
  const stripe = { critical: "bg-critical", watch: "bg-high", good: "bg-good" }[p.health.level] || "bg-good";
  const wesPct = p.wes.total ? Math.round((p.wes.done / p.wes.total) * 100) : null;
  const reason = p.health.reasons[0]?.replace(/^(legal|default|extension|maturity passed):\s*/i, "");
  return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.02 }}>
      <Link href={`/property/${p.property_id}`} className="block rounded-2xl border border-line bg-elev overflow-hidden hover:border-line-strong hover:shadow-[var(--shadow)] hover:-translate-y-0.5 transition">
        <div className={cn("h-1.5", stripe)} />
        <div className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[15px] font-semibold leading-tight">{p.address}</div>
              <div className="text-[11.5px] text-faint mt-0.5">{(p.deal_type || "loan").replace("_", " ")} · day {p.day_count ?? "—"} · last activity {ago(p.last_activity)}</div>
            </div>
            <span className={cn("shrink-0 text-[10px] font-bold uppercase tracking-wider px-2.5 h-6 rounded-full grid place-items-center ring-1", h.ring, h.cls)}>{h.label}</span>
          </div>

          <div className={cn("mt-3 text-[12.5px] leading-snug line-clamp-2 min-h-[2.4em]", reason ? h.cls : "text-muted")}>
            {reason ? reason : p.upcoming[0] ? <>Next: <b>{fmtDate(p.upcoming[0].date, "MMM d")}</b> — {p.upcoming[0].title}</> : "Nothing flagged. Steady."}
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-sunken/70 px-3 py-2"><div className="text-[10px] uppercase tracking-wide text-faint">Invested</div><Figure value={p.money.invested} tone="text-money-in" /></div>
            <div className="rounded-xl bg-sunken/70 px-3 py-2"><div className="text-[10px] uppercase tracking-wide text-faint">Owed{p.money.owed_as_of ? ` · ${fmtDate(p.money.owed_as_of, "MMM yy")}` : ""}</div><Figure value={p.money.owed} /></div>
          </div>
          {p.money.critical_risks?.length ? <div className="mt-2 text-[11px] text-critical flex items-start gap-1"><AlertTriangle size={11} className="mt-0.5 shrink-0" /><span className="truncate">{p.money.critical_risks[0]}</span></div> : null}

          <div className="mt-3 flex items-center justify-between text-[12px] text-muted">
            <span><b className="text-fg tnum">{p.documents.total}</b> documents</span>
            <span><b className="text-fg tnum">{p.tasks.open}</b> open{p.tasks.suggested ? <span className="text-accent"> · {p.tasks.suggested} suggested</span> : ""}</span>
            <span className="flex items-center gap-1.5">Wes {wesPct === null ? <span className="text-faint">—</span> : <><span className="inline-block h-1.5 w-12 rounded-full bg-line overflow-hidden"><span className="block h-full bg-good" style={{ width: `${wesPct}%` }} /></span><b className="text-fg tnum">{wesPct}%</b></>}</span>
          </div>
        </div>
      </Link>
    </motion.div>
  );
}
