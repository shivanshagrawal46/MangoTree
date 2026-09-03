"use client";

/* Review — the human decisions the system asked for. Every item shows why the
   AI couldn't place it; one click files it, confirms it common, or discards it
   (a status, never a delete). Decisions ripple to chunks, attachments, timeline. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { Mail, FileText, Check, Layers, Trash2, ExternalLink, MessagesSquare } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, CardHeader, Skeleton, Empty, Tabs, TabsList, TabsTrigger } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { ThreadDialog } from "../property/[pid]/page";
import { cn, fmtDate, propertyLabel } from "@/lib/utils";
import type { ArtifactRow, PropertySummary } from "@/lib/types";

export default function ReviewPage() {
  const qc = useQueryClient();
  const { open } = useEvidence();
  const [tab, setTab] = React.useState("unplaced");
  const q = useQuery({ queryKey: ["review", "unplaced"], queryFn: () => api.get<{ items: ArtifactRow[]; total: number; low_confidence: number }>("/review/unplaced?limit=150") });
  const low = useQuery({ queryKey: ["review", "low"], queryFn: () => api.get<ArtifactRow[]>("/review/low-confidence"), enabled: tab === "low" });
  const cands = useQuery({ queryKey: ["review", "registry"], queryFn: () => api.get<any[]>("/review/registry-candidates"), enabled: tab === "registry" });
  const props = useQuery({ queryKey: ["properties"], queryFn: () => api.get<PropertySummary[]>("/properties"), staleTime: 60_000 });
  const [picked, setPicked] = React.useState<Record<string, string[]>>({});
  const [thread, setThread] = React.useState<string | null>(null);

  const decide = async (sha: string, action: string, property_ids: string[] = []) => {
    try {
      const out = await api.post<any>("/review/decide", { action, artifact_shas: [sha], property_ids });
      toast.success(action === "assign" ? `Filed under ${property_ids.map(propertyLabel).join(", ")} — ${out.chunks_updated} passages, ${out.attachments_updated} attachments updated` : action === "common" ? "Confirmed as portfolio-level" : "Discarded (kept, hidden from search)");
      qc.invalidateQueries({ queryKey: ["review"] }); qc.invalidateQueries({ queryKey: ["dashboard"] });
    } catch (e: any) { toast.error(e.message); }
  };
  const toggle = (sha: string, pid: string) => setPicked((c) => { const cur = c[sha] || []; return { ...c, [sha]: cur.includes(pid) ? cur.filter((x) => x !== pid) : [...cur, pid] }; });

  return (
    <div className="p-6 max-w-[1200px] mx-auto space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">Review</h1><p className="text-sm text-muted mt-1">Where the system wasn't sure, it asks. Each decision is recorded with your name and ripples everywhere the document lives.</p></div>
      <Tabs value={tab} onValueChange={setTab}><TabsList><TabsTrigger value="unplaced">Unplaced · {q.data?.total ?? "…"}</TabsTrigger><TabsTrigger value="low">Low confidence · {q.data?.low_confidence ?? "…"}</TabsTrigger><TabsTrigger value="registry">Deals outside the registry</TabsTrigger></TabsList></Tabs>

      {tab === "unplaced" && (
        <div className="space-y-3">
          {q.isLoading && <Skeleton className="h-40" />}
          {q.data?.items.length === 0 && <Empty title="Everything is placed." />}
          <AnimatePresence initial={false}>
            {(q.data?.items || []).map((a) => (
              <motion.div key={a.sha256} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, height: 0 }}>
                <Card className="p-4">
                  <div className="flex items-start gap-3">
                    <div className="h-9 w-9 rounded-xl bg-sunken grid place-items-center shrink-0 text-muted">{a.source_type === "email" ? <Mail size={15} /> : <FileText size={15} />}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2"><div className="text-[13.5px] font-semibold truncate">{a.name}</div><span className="text-[11px] text-faint tnum">{fmtDate(a.date)}</span>{a.from && <span className="text-[11px] text-faint truncate">from {a.from}</span>}
                        {a.thread_size ? <button className="text-[11px] text-accent flex items-center gap-1" onClick={() => setThread(a.thread_key!)}><MessagesSquare size={11} /> thread of {a.thread_size}</button> : null}
                        <button className="text-[11px] text-faint hover:text-fg flex items-center gap-1" onClick={() => open({ sha: a.sha256 })}><ExternalLink size={11} /> open</button></div>
                      {a.reasoning && <div className="text-xs text-muted mt-1"><span className="font-medium text-fg">Opus 5:</span> {a.reasoning}</div>}
                      {a.body_excerpt && <div className="text-xs text-faint mt-1 line-clamp-2">{a.body_excerpt}</div>}
                      <div className="mt-3 flex flex-wrap gap-1">
                        {(props.data || []).map((p) => { const on = (picked[a.sha256] || []).includes(p.property_id); const hint = (a.candidates || []).includes(p.property_id); return (
                          <button key={p.property_id} onClick={() => toggle(a.sha256, p.property_id)} className={cn("h-6 px-2 rounded-full text-[11px] border transition", on ? "bg-accent text-white border-accent" : hint ? "border-accent/50 text-accent bg-accent-soft/40" : "border-line text-muted hover:border-line-strong")}>{propertyLabel(p.property_id)}{hint && !on ? " ?" : ""}</button>); })}
                      </div>
                    </div>
                    <div className="flex flex-col gap-1.5 shrink-0">
                      <Button size="sm" variant="primary" disabled={!(picked[a.sha256] || []).length} onClick={() => decide(a.sha256, "assign", picked[a.sha256])}><Check size={13} /> File ({(picked[a.sha256] || []).length || 0})</Button>
                      <Button size="sm" variant="secondary" onClick={() => decide(a.sha256, "common")}><Layers size={13} /> Portfolio-level</Button>
                      <Button size="sm" variant="ghost" onClick={() => decide(a.sha256, "discard")}><Trash2 size={13} /> No use</Button>
                    </div>
                  </div>
                </Card>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}

      {tab === "low" && (
        <Card><CardHeader title="Filed with doubt" sub="Opus 5 placed these below its confidence bar. Confirm by leaving them, or re-file above." />
          <ul className="divide-y divide-line">{(low.data || []).map((a) => <li key={a.sha256} className="px-5 py-2.5 flex items-center gap-3 hover:bg-sunken cursor-pointer" onClick={() => open({ sha: a.sha256 })}><div className="min-w-0 flex-1"><div className="text-[13px] truncate">{a.name}</div><div className="text-[11px] text-faint">{a.reasoning}</div></div>{a.property_ids.map((p) => <Badge key={p} tone="accent">{propertyLabel(p)}</Badge>)}<Badge tone="high">{Math.round((a.confidence || 0) * 100)}%</Badge></li>)}</ul></Card>
      )}

      {tab === "registry" && (
        <Card><CardHeader title="Addresses outside the 15" sub="Catalogued from the records. Per your decision these stay as other deals (global chat only); listed here for reference." right={<Button size="sm" variant="ghost" onClick={async () => { const r = await api.post<any>("/review/registry-candidates/close-all"); toast.success(`${r.closed} closed as 'leave'`); qc.invalidateQueries({ queryKey: ["review"] }); }}>Close all as "leave"</Button>} />
          <ul className="divide-y divide-line">{(cands.data || []).map((c) => { const p = c.payload || {}; return (
            <li key={c.artifact_sha} className="px-5 py-2.5 flex items-center gap-3"><div className="min-w-0 flex-1"><div className="text-[13px] font-medium">{p.spellings?.[0]}</div><div className="text-[11px] text-faint">{(p.strong_signals || []).join(", ") || "no strong signals"} · {p.first_seen ? fmtDate(p.first_seen, "MMM yy") : ""} → {p.last_seen ? fmtDate(p.last_seen, "MMM yy") : ""}</div></div><span className="text-xs tnum text-muted">{p.documents} docs</span><Badge tone={c.status === "closed" ? "neutral" : "high"}>{c.status}</Badge></li>); })}</ul></Card>
      )}
      <ThreadDialog threadKey={thread} onClose={() => setThread(null)} />
    </div>
  );
}
