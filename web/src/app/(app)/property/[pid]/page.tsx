"use client";

/* The property workspace — one property, one place, one chat. This page is the
   AI's context rendered for humans. */

import * as React from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Mail, FileText, Paperclip, Search, ExternalLink, HardHat, Users } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, CardHeader, Input, Skeleton, Stat, Tabs, TabsContent, TabsList, TabsTrigger, Empty, Dialog, DialogContent, Select } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { ChatPanel } from "@/components/chat";
import { Timeline } from "@/components/timeline";
import { TaskBoard } from "@/components/tasks";
import { MoneyFlow, ByType, Donut } from "@/components/charts";
import { CardsFeed } from "@/components/briefing";
import { LedgerView, WesAgendaCard, WesAgendaStrip, Figure } from "@/components/ledger";
import { UploadBox } from "@/components/upload";
import { FileDown } from "lucide-react";
import { cn, fmtDate, ago, money, HEALTH, PLACEMENT_LABEL } from "@/lib/utils";
import type { PropertySummary, ArtifactRow, WesItem } from "@/lib/types";

export default function PropertyPage() {
  const { pid } = useParams<{ pid: string }>();
  const sp = useSearchParams();
  const router = useRouter();
  const tab = sp.get("tab") || "chat";
  const p = useQuery({ queryKey: ["property", pid], queryFn: () => api.get<PropertySummary>(`/properties/${pid}`) });
  if (p.isLoading || !p.data) return <div className="p-6 space-y-3"><Skeleton className="h-24" /><Skeleton className="h-[60vh]" /></div>;
  const d = p.data;
  const h = HEALTH[d.health.level] || HEALTH.good;
  const setTab = (t: string) => router.replace(`/property/${pid}?tab=${t}`, { scroll: false });

  return (
    <div className="h-screen flex flex-col">
      <div className="px-6 pt-5 pb-3 border-b border-line bg-elev/60 backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2"><h1 className="text-xl font-semibold tracking-tight">{d.address}</h1><span className={cn("text-[10px] font-semibold uppercase tracking-wide px-2 h-5 rounded-full grid place-items-center ring-1", h.ring, h.cls)}>{h.label}</span></div>
            <div className="text-xs text-muted mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span>{d.deal_type || "loan"}</span><span>day <b className="tnum text-fg">{d.day_count ?? "—"}</b> since {fmtDate(d.started)}</span><span>last activity {ago(d.last_activity)}</span>
              {d.health.reasons.slice(0, 2).map((r, i) => <span key={i} className={h.cls}>· {r}</span>)}
            </div>
          </div>
          <div className="grid grid-cols-5 gap-6">
            <Stat label="Documents" value={d.documents.total} sub={`${d.documents.email || 0} emails · ${(d.documents.attachment || 0) + (d.documents.disk_file || 0) + (d.documents.upload || 0)} files`} />
            <div><div className="text-[11px] uppercase tracking-wide text-faint">Invested</div><Figure value={d.money.invested} tone="text-money-in" sub={d.money.established ? "ledger, confirmed rows" : "no money record on file"} /></div>
            <div><div className="text-[11px] uppercase tracking-wide text-faint">Owed to RKB</div><Figure value={d.money.owed} sub={d.money.owed_as_of ? `as of ${fmtDate(d.money.owed_as_of)}` : "no balance statement"} /></div>
            <Stat label="Open tasks" value={d.tasks.open} sub={d.tasks.suggested ? `+${d.tasks.suggested} suggested` : ""} />
            <div className="flex items-center gap-2"><Donut done={d.wes.done} total={d.wes.total} size={48} /><div><div className="text-[11px] uppercase tracking-wide text-faint">Wes</div><div className="text-xs text-muted tnum">{d.wes.done}/{d.wes.total} done</div></div></div>
          </div>
        </div>
        <WesAgendaStrip pid={pid} />
        <Tabs value={tab} onValueChange={setTab} className="mt-3">
          <TabsList>
            {[["chat", "Chat"], ["timeline", `Timeline · ${d.events}`], ["tasks", "Tasks"], ["money", "Money"], ["docs", `Docs · ${d.documents.total}`], ["files", "Files"], ["comms", "Comms"], ["wes", "Wes's work"], ["people", "People"]].map(([k, l]) => <TabsTrigger key={k} value={k}>{l}</TabsTrigger>)}
          </TabsList>
        </Tabs>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden">
        <motion.div key={tab} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="h-full">
          {tab === "chat" && <div className="h-full grid lg:grid-cols-[1fr_360px]"><div className="h-full min-h-0 p-5"><ChatPanel propertyId={pid} className="h-full" /></div><Sidebar d={d} pid={pid} /></div>}
          {tab === "timeline" && <div className="h-full overflow-y-auto p-6 max-w-4xl"><Timeline propertyId={pid} /></div>}
          {tab === "tasks" && <div className="h-full overflow-y-auto p-6 max-w-4xl"><TaskBoard propertyId={pid} /></div>}
          {tab === "money" && <div className="h-full overflow-y-auto p-6 max-w-6xl"><LedgerView pid={pid} /><details className="mt-6"><summary className="text-xs text-muted cursor-pointer hover:text-fg">Amounts mentioned in documents (old view — mentions, not movements)</summary><div className="mt-3"><Money pid={pid} /></div></details></div>}
          {tab === "docs" && <div className="h-full overflow-y-auto p-6"><Docs pid={pid} /></div>}
          {tab === "files" && <div className="h-full overflow-y-auto p-6"><Files pid={pid} /></div>}
          {tab === "comms" && <div className="h-full overflow-y-auto p-6"><Comms pid={pid} /></div>}
          {tab === "wes" && <div className="h-full overflow-y-auto p-6 max-w-4xl space-y-4"><WesAgendaCard pid={pid} /><Wes pid={pid} /></div>}
          {tab === "people" && <div className="h-full overflow-y-auto p-6"><People pid={pid} /></div>}
        </motion.div>
      </div>
    </div>
  );
}

function Sidebar({ d, pid }: { d: PropertySummary; pid: string }) {
  const { open } = useEvidence();
  return (
    <aside className="border-l border-line bg-elev/40 p-4 space-y-4 overflow-y-auto hidden lg:block">
      <div><div className="text-[11px] uppercase tracking-wide text-faint mb-1.5">Coming up</div>
        {d.upcoming.length === 0 ? <div className="text-xs text-muted">No dated items in the next 60 days.</div> :
          <ul className="space-y-1.5">{d.upcoming.map((u, i) => <li key={i} className="text-xs flex gap-2 cursor-pointer hover:text-accent" onClick={() => u.source_sha && open({ sha: u.source_sha })}><span className="tnum text-faint w-14 shrink-0">{fmtDate(u.date, "MMM d")}</span><span className="truncate">{u.title}</span></li>)}</ul>}
      </div>
      {d.risk_events.length > 0 && <div><div className="text-[11px] uppercase tracking-wide text-critical mb-1.5">Risk events (90 days)</div>
        <ul className="space-y-1.5">{d.risk_events.map((u, i) => <li key={i} className="text-xs flex gap-2 cursor-pointer hover:text-accent" onClick={() => u.source_sha && open({ sha: u.source_sha })}><span className="tnum text-faint w-14 shrink-0">{fmtDate(u.date, "MMM d")}</span><Badge tone="critical">{u.type}</Badge><span className="truncate">{u.title}</span></li>)}</ul></div>}
      <CardsFeed propertyId={pid} limit={6} variant="list" />
      <div><div className="text-[11px] uppercase tracking-wide text-faint mb-1.5">Open tasks</div><TaskBoard propertyId={pid} statusFilter="open" groupBy="none" compact showAdd={false} /></div>
      <div className="text-[10px] text-faint">Health is derived from quote-verified timeline events; money from the ledger of documented movements. Click anything to see its source.</div>
    </aside>
  );
}

function Money({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const q = useQuery({ queryKey: ["money", pid], queryFn: () => api.get<any>(`/properties/${pid}/money`) });
  if (!q.data) return <Skeleton className="h-64" />;
  const m = q.data;
  return (
    <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
      <Card className="xl:col-span-2 border-high/40">
        <div className="px-5 py-4 flex items-start gap-3">
          <div className="h-8 w-8 rounded-xl bg-high-soft text-high grid place-items-center shrink-0 text-sm font-bold">!</div>
          <div className="text-sm">
            <div className="font-semibold">Invested and returned are not shown yet</div>
            <div className="text-muted text-xs mt-0.5">What is below are <b>amounts mentioned</b> in dated documents — loan commitments, appraisals, insurance limits and the same wire quoted in several emails all appear. Summing them gives a number that is wrong by an order of magnitude, so no total is displayed. A ledger of confirmed transactions (settlement statements, wires, bank records), each with its verbatim quote, is being built to replace this.</div>
          </div>
        </div>
      </Card>
      <Card><CardHeader title="Amounts mentioned, by month" sub="Mentions, not movements" right={<a href={`/api/export/money.xlsx?property_id=${pid}`}><Button size="sm" variant="ghost"><FileDown size={13} /> Excel</Button></a>} /><div className="px-3 pb-2"><MoneyFlow series={m.series} height={260} /></div></Card>
      <Card><CardHeader title="Mentions by document type" /><div className="px-2 pb-2"><ByType byType={m.by_type} /></div></Card>
      <Card className="xl:col-span-2"><CardHeader title={`Every dated amount mentioned · ${m.events.length}`} sub="Click a row to open the document at the quote" />
        <div className="overflow-x-auto"><table className="w-full text-xs"><thead className="text-faint text-left"><tr className="border-b border-line"><th className="px-5 py-2 font-medium">Date</th><th className="py-2 font-medium">Type</th><th className="py-2 font-medium">What</th><th className="py-2 font-medium text-right pr-5">Amount</th></tr></thead>
          <tbody>{[...m.events].reverse().map((e: any) => <tr key={e.event_id} className="border-b border-line/60 hover:bg-sunken cursor-pointer" onClick={() => open({ sha: e.source_sha, highlight: e.quote?.slice(0, 60) })}><td className="px-5 py-2 tnum text-muted whitespace-nowrap">{fmtDate(e.occurred_at)}</td><td className="py-2"><Badge>{e.event_type}</Badge></td><td className="py-2 max-w-[520px] truncate">{e.title}</td><td className={cn("py-2 pr-5 text-right tnum font-medium", ["payment", "payoff"].includes(e.event_type) ? "text-money-back" : "text-money-in")}>{money(e.amount)}</td></tr>)}</tbody></table></div></Card>
    </div>
  );
}

function Docs({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const [placement, setPlacement] = React.useState("");
  const [q, setQ] = React.useState("");
  const d = useQuery({ queryKey: ["docs", pid, placement, q], queryFn: () => api.get<{ items: ArtifactRow[]; inventory: Record<string, number>; total: number }>(`/properties/${pid}/documents?placement=${placement}&q=${encodeURIComponent(q)}`) });
  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_260px]">
      <Card>
        <CardHeader title={placement === "portfolio" ? "Portfolio-level documents (common store)" : placement === "unplaced" ? "Unplaced documents (pending review)" : "This property's file"} sub={`${d.data?.total ?? "…"} documents — the same inventory the AI reads`}
          right={<div className="flex items-center gap-2"><div className="relative"><Search size={13} className="absolute left-2.5 top-2.5 text-faint" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter…" className="pl-8 w-44 h-8 text-xs" /></div>
            <Select value={placement} onChange={(e) => setPlacement(e.target.value)} className="h-8 text-xs"><option value="">Property file</option><option value="portfolio">Portfolio (common)</option><option value="unplaced">Unplaced</option></Select></div>} />
        <ul className="divide-y divide-line">
          {(d.data?.items || []).map((a) => (
            <li key={a.sha256} className="flex items-center gap-3 px-5 py-2 hover:bg-sunken cursor-pointer" onClick={() => open({ sha: a.sha256 })}>
              <div className="h-8 w-8 rounded-lg bg-sunken grid place-items-center shrink-0 text-muted">{a.source_type === "email" ? <Mail size={14} /> : <FileText size={14} />}</div>
              <div className="min-w-0 flex-1"><div className="text-[13px] truncate">{a.name}</div><div className="text-[11px] text-faint flex gap-2">{a.from && <span className="truncate">{a.from}</span>}{a.doc_class && <span>· {a.doc_class}</span>}{a.topics?.length > 0 && <span>· {a.topics.join(", ")}</span>}{a.attachments > 0 && <span className="flex items-center gap-0.5">· <Paperclip size={10} /> {a.attachments}</span>}</div></div>
              {a.placement && a.placement !== "property" && <Badge tone={a.placement === "unplaced" ? "high" : "info"}>{PLACEMENT_LABEL[a.placement]}</Badge>}
              <span className="text-[11px] tnum text-faint w-20 text-right">{fmtDate(a.date, "MMM d, yy")}</span>
              <a href={`/api/evidence/original/${a.sha256}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-faint hover:text-fg"><ExternalLink size={13} /></a>
            </li>
          ))}
          {d.data && d.data.items.length === 0 && <li><Empty title="Nothing matches." /></li>}
        </ul>
      </Card>
      <Card className="h-fit"><CardHeader title="Inventory" sub="By type" /><ul className="px-5 pb-4 space-y-1.5">{Object.entries(d.data?.inventory || {}).map(([k, v]) => <li key={k} className="flex justify-between text-xs"><span className="text-muted">{k}</span><span className="tnum font-medium">{v}</span></li>)}</ul></Card>
    </div>
  );
}

function Files({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const q = useQuery({ queryKey: ["files", pid], queryFn: () => api.get<any>(`/properties/${pid}/files`) });
  const [collapsed, setCollapsed] = React.useState<Record<string, boolean>>({});
  const [query, setQuery] = React.useState("");
  if (!q.data) return <Skeleton className="h-64" />;
  const icon = (name: string) => { const e = (name.split(".").pop() || "").toLowerCase(); return e === "pdf" ? "PDF" : ["xlsx", "xls", "csv"].includes(e) ? "XLS" : ["doc", "docx"].includes(e) ? "DOC" : ["jpg", "jpeg", "png", "heic"].includes(e) ? "IMG" : e.toUpperCase().slice(0, 4) || "FILE"; };
  const needle = query.trim().toLowerCase();
  // Search flattens the tree: every matching file with its folder path, most recent first.
  const flat: { f: any; folder: string }[] = [];
  const walk = (node: any, path: string[]) => { node.files.forEach((f: any) => flat.push({ f, folder: path.join(" / ") })); node.children.forEach((c: any) => walk(c, [...path, c.name])); };
  walk(q.data.tree, []);
  const matches = needle ? flat.filter(({ f }) => (f.filename || f.name || "").toLowerCase().includes(needle)).sort((a, b) => String(b.f.date || "").localeCompare(String(a.f.date || ""))) : [];
  const Row = ({ f, depth, folder }: { f: any; depth: number; folder?: string }) => (
    <div className="flex items-center gap-3 px-2 py-1.5 rounded-lg hover:bg-sunken cursor-pointer group" style={{ paddingLeft: depth * 16 + 8 }} onClick={() => open({ sha: f.sha256 })}>
      <span className="h-6 w-9 rounded-md bg-sunken border border-line text-[9px] font-bold text-muted grid place-items-center shrink-0">{icon(f.filename || f.name)}</span>
      <span className="min-w-0 flex-1">
        <span className="text-[13px] truncate block">{highlight(f.filename || f.name, needle)}</span>
        {(folder || f.uploaded_by) && <span className="text-[11px] text-faint truncate block">{folder}{folder && f.uploaded_by ? " · " : ""}{f.uploaded_by ? <>uploaded by <b className="text-muted">{f.uploaded_by}</b> on {fmtDate(f.uploaded_at, "MMM d, yyyy")}</> : null}</span>}
      </span>
      {f.doc_class && <Badge>{f.doc_class}</Badge>}
      <span className="text-[11px] tnum text-faint w-16 text-right">{f.size ? `${(f.size / 1024).toFixed(0)} KB` : ""}</span>
      <span className="text-[11px] tnum text-faint w-20 text-right">{fmtDate(f.date, "MMM d, yy")}</span>
      <a href={`/api/evidence/original/${f.sha256}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-faint hover:text-fg opacity-0 group-hover:opacity-100"><ExternalLink size={13} /></a>
    </div>
  );
  const Node = ({ node, depth, path }: { node: any; depth: number; path: string }) => {
    const isOpen = !collapsed[path];
    return (
      <div>
        {depth > 0 && (
          <button onClick={() => setCollapsed((c) => ({ ...c, [path]: !c[path] }))} className="flex items-center gap-2 w-full text-left px-2 py-1.5 rounded-lg hover:bg-sunken text-[13px]" style={{ paddingLeft: depth * 16 }}>
            <span className={cn("text-faint transition", isOpen && "rotate-90")}>▸</span><span className="font-medium">{node.name}</span><span className="text-[11px] text-faint">{node.count}</span>
          </button>
        )}
        {isOpen && (
          <div>
            {node.children.map((c: any) => <Node key={c.name} node={c} depth={depth + 1} path={`${path}/${c.name}`} />)}
            {node.files.map((f: any) => <Row key={f.sha256} f={f} depth={depth + 1} />)}
          </div>
        )}
      </div>
    );
  };
  return (
    <div className="space-y-4">
      <UploadBox pid={pid} onDone={() => q.refetch()} />
      <Card>
        <CardHeader title="Files — as they are on the drive" sub={`${q.data.total} files in their original folders, everything that arrived as an email attachment, and anything uploaded here. Click any file to view the exact original.`}
          right={<div className="flex items-center gap-2">
            <div className="relative"><Search size={13} className="absolute left-2.5 top-2.5 text-faint" /><Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by file name…" className="pl-8 w-64 h-8 text-xs" /></div>
            <Badge tone={q.data.store === "SpacesObjectStore" ? "accent" : "neutral"}>{q.data.store === "SpacesObjectStore" ? "stored on DigitalOcean" : "stored locally"}</Badge>
          </div>} />
        <div className="px-3 pb-4">
          {needle ? (
            matches.length ? <>
              <div className="px-2 pb-1 text-[11px] text-faint">{matches.length} file{matches.length > 1 ? "s" : ""} matching “{query}”</div>
              {matches.map(({ f, folder }) => <Row key={f.sha256} f={f} depth={0} folder={folder || "(root)"} />)}
            </> : <div className="px-2 py-6 text-sm text-muted">No file name contains “{query}”.</div>
          ) : <Node node={q.data.tree} depth={0} path="" />}
        </div>
      </Card>
    </div>
  );
}

function highlight(text: string, needle: string) {
  if (!needle) return text;
  const i = text.toLowerCase().indexOf(needle);
  if (i < 0) return text;
  return <>{text.slice(0, i)}<mark className="bg-high-soft text-fg rounded px-0.5">{text.slice(i, i + needle.length)}</mark>{text.slice(i + needle.length)}</>;
}

function Comms({ pid }: { pid: string }) {
  const [q, setQ] = React.useState("");
  const [thread, setThread] = React.useState<string | null>(null);
  const d = useQuery({ queryKey: ["comms", pid, q], queryFn: () => api.get<ArtifactRow[]>(`/properties/${pid}/comms?q=${encodeURIComponent(q)}`) });
  return (
    <Card>
      <CardHeader title="Emails" sub="Open any message to read its whole conversation, attachments inline" right={<div className="relative"><Search size={13} className="absolute left-2.5 top-2.5 text-faint" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Subject or sender…" className="pl-8 w-56 h-8 text-xs" /></div>} />
      <ul className="divide-y divide-line">
        {(d.data || []).map((a) => (
          <li key={a.sha256} className="flex items-center gap-3 px-5 py-2.5 hover:bg-sunken cursor-pointer" onClick={() => setThread(a.thread_key || a.sha256)}>
            <div className="h-8 w-8 rounded-full bg-sunken grid place-items-center text-[10px] font-semibold text-muted shrink-0">{(a.from || "?").slice(0, 2).toUpperCase()}</div>
            <div className="min-w-0 flex-1"><div className="text-[13px] font-medium truncate">{a.subject || "(no subject)"}</div><div className="text-[11px] text-faint truncate">{a.from}{a.attachments > 0 && <span className="ml-2 inline-flex items-center gap-0.5"><Paperclip size={10} /> {a.attachment_names.slice(0, 2).join(", ")}{a.attachments > 2 ? ` +${a.attachments - 2}` : ""}</span>}</div></div>
            {a.placement === "unplaced" && <Badge tone="high">unplaced</Badge>}
            <span className="text-[11px] tnum text-faint w-24 text-right">{fmtDate(a.date, "MMM d, yy HH:mm")}</span>
          </li>
        ))}
      </ul>
      <ThreadDialog threadKey={thread} onClose={() => setThread(null)} />
    </Card>
  );
}

export function ThreadDialog({ threadKey, onClose }: { threadKey: string | null; onClose: () => void }) {
  const { open } = useEvidence();
  const t = useQuery({ queryKey: ["thread", threadKey], queryFn: () => api.get<any>(`/threads/${encodeURIComponent(threadKey!)}`), enabled: !!threadKey });
  return (
    <Dialog open={!!threadKey} onOpenChange={(o) => !o && onClose()}>
      <DialogContent wide title={t.data?.subject || "Conversation"} description={t.data ? `${t.data.messages.length} messages · ${(t.data.property_ids || []).join(", ")}` : ""} className="max-h-[85vh] overflow-y-auto">
        {!t.data && <Skeleton className="h-40" />}
        <div className="space-y-3">
          {(t.data?.messages || []).map((m: any) => (
            <div key={m.sha256} className="rounded-2xl border border-line bg-bg p-4">
              <div className="flex items-center justify-between text-xs mb-2"><div><span className="font-semibold">{m.from}</span> <span className="text-faint">→ {(m.to || []).join(", ")}</span></div><div className="flex items-center gap-2 text-faint">{m.timeline_event && <Badge tone="accent">on timeline: {m.timeline_event.event_type}</Badge>}<span className="tnum">{fmtDate(m.date, "MMM d, yyyy HH:mm")}</span><button onClick={() => open({ sha: m.sha256 })} className="hover:text-fg"><ExternalLink size={12} /></button></div></div>
              <div className="text-[13px] whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto">{m.body || "(empty)"}</div>
              {m.attachments_list?.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{m.attachments_list.map((a: any) => <button key={a.sha256} onClick={() => open({ sha: a.sha256 })} className="inline-flex items-center gap-1 h-6 px-2 rounded-lg border border-line bg-elev text-[11px] hover:border-accent"><Paperclip size={10} /> {a.name}</button>)}</div>}
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Wes({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const w = useQuery({ queryKey: ["wes", pid], queryFn: () => api.get<{ items: WesItem[]; done: number; total: number }>(`/properties/${pid}/wes`) });
  if (!w.data) return <Skeleton className="h-64" />;
  const tone: Record<string, "good" | "high" | "critical" | "neutral"> = { done: "good", in_progress: "high", blocked: "critical", remaining: "neutral" };
  return (
    <Card>
      <CardHeader title="Wes's construction work" sub="Extracted by Opus 5 from the records, each item with its quote. What's finished and what remains." right={<div className="flex items-center gap-3"><Donut done={w.data.done} total={w.data.total} size={56} label="done" /><HardHat size={16} className="text-muted" /></div>} />
      {w.data.items.length === 0 && <Empty title="No construction items found in the records yet." sub="Run task extraction from the Tasks page after new emails arrive." />}
      <ul className="divide-y divide-line">
        {w.data.items.map((it, i) => (
          <li key={i} className="px-5 py-3 flex items-start gap-3 hover:bg-sunken cursor-pointer" onClick={() => it.source_sha && open({ sha: it.source_sha, highlight: it.quote.slice(0, 60) })}>
            <Badge tone={tone[it.status]}>{it.status.replace("_", " ")}</Badge>
            <div className="min-w-0 flex-1"><div className={cn("text-[13px] font-medium", it.status === "done" && "line-through text-muted")}>{it.title}</div><div className="text-xs text-muted italic mt-0.5 line-clamp-2">“{it.quote}”</div></div>
            {it.due && <span className="text-[11px] tnum text-faint">{fmtDate(it.due)}</span>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function People({ pid }: { pid: string }) {
  const router = useRouter();
  const p = useQuery({ queryKey: ["people", pid], queryFn: () => api.get<any[]>(`/people?property_id=${pid}`) });
  return (
    <Card>
      <CardHeader title="People on this deal" sub="From the knowledge graph — click for their history across every property" right={<Users size={16} className="text-muted" />} />
      {p.data?.length === 0 && <Empty title="No linked people yet." />}
      <ul className="divide-y divide-line">{(p.data || []).map((x) => (
        <li key={x.person_id} className="px-5 py-2.5 flex items-center gap-3 hover:bg-sunken cursor-pointer" onClick={() => router.push(`/people/${x.person_id}`)}>
          <div className="h-8 w-8 rounded-full bg-accent-soft text-accent grid place-items-center text-xs font-semibold">{(x.display_name || "?").split(" ").map((w: string) => w[0]).slice(0, 2).join("")}</div>
          <div className="min-w-0 flex-1"><div className="text-[13px] font-medium truncate">{x.display_name}</div><div className="text-[11px] text-faint truncate">{x.role} · {x.org}</div></div>
          <Badge tone={x.side === "rkb" ? "accent" : "neutral"}>{x.side}</Badge><span className="text-[11px] tnum text-faint w-20 text-right">{x.mentions} mentions</span>
        </li>))}</ul>
    </Card>
  );
}
