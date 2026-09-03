"use client";

/* Tasks — checkbox discipline. Ticking saves as done and is audited. AI
   suggestions arrive as a separate lane and become real when accepted. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { toast } from "sonner";
import { Plus, Sparkles, Check, X, Clock, History } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Checkbox, Dialog, DialogContent, Input, Select, Textarea, Empty, Tip } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { cn, fmtDate, propertyLabel } from "@/lib/utils";
import type { Task } from "@/lib/types";

const OWNER_TONE: Record<string, string> = { Rakesh: "bg-accent-soft text-accent", JP: "bg-info-soft text-info", Manjunath: "bg-normal-soft text-normal", Wes: "bg-high-soft text-high" };
const PRIO: Record<string, string> = { critical: "text-critical", high: "text-high", normal: "text-muted", low: "text-faint" };

export function TaskBoard({ propertyId, ownerFilter, statusFilter, showAdd = true, groupBy = "owner", compact }: {
  propertyId?: string; ownerFilter?: string; statusFilter?: string; showAdd?: boolean; groupBy?: "owner" | "property" | "none"; compact?: boolean;
}) {
  const qc = useQueryClient();
  const { open } = useEvidence();
  const [status, setStatus] = React.useState(statusFilter || "suggested,open");
  const [owner, setOwner] = React.useState(ownerFilter || "");
  const params = new URLSearchParams();
  if (propertyId) params.set("property_id", propertyId);
  if (owner) params.set("owner", owner);
  params.set("status", status);
  const q = useQuery({ queryKey: ["tasks", propertyId || "", owner, status], queryFn: () => api.get<{ items: Task[]; counts: any; owners: string[] }>(`/tasks?${params}`) });
  const [add, setAdd] = React.useState(false);

  const mutate = async (task_id: string, s: string, remark = "") => {
    await api.post(`/tasks/${task_id}/status`, { status: s, remark });
    qc.invalidateQueries({ queryKey: ["tasks"] }); qc.invalidateQueries({ queryKey: ["dashboard"] }); qc.invalidateQueries({ queryKey: ["property", propertyId] });
    if (s === "done") toast.success("Marked done — recorded with your name and the time");
  };

  const items = q.data?.items || [];
  const suggested = items.filter((t) => t.status === "suggested");
  const active = items.filter((t) => t.status !== "suggested");
  const groups = groupBy === "none" ? [["", active]] as [string, Task[]][] :
    Object.entries(active.reduce((acc, t) => { const k = groupBy === "owner" ? t.owner : (t.property_id || "Portfolio"); (acc[k] ||= []).push(t); return acc; }, {} as Record<string, Task[]>))
      .sort((a, b) => (groupBy === "owner" ? ["Rakesh", "JP", "Manjunath", "Wes"].indexOf(a[0]) - ["Rakesh", "JP", "Manjunath", "Wes"].indexOf(b[0]) : a[0].localeCompare(b[0])));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={owner} onChange={(e) => setOwner(e.target.value)}><option value="">Everyone</option>{["Rakesh", "JP", "Manjunath", "Wes"].map((o) => <option key={o}>{o}</option>)}</Select>
        <Select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="suggested,open">Open + suggested</option><option value="open">Open</option><option value="suggested">Suggested by AI</option><option value="done">Done</option><option value="dismissed">Dismissed</option>
        </Select>
        <span className="flex-1" />
        {showAdd && <Button variant="primary" size="sm" onClick={() => setAdd(true)}><Plus size={14} /> Add task</Button>}
      </div>

      {suggested.length > 0 && (
        <div className="rounded-2xl border border-accent/25 bg-accent-soft/40 p-3">
          <div className="text-xs font-semibold text-accent flex items-center gap-1.5 mb-2"><Sparkles size={13} /> Suggested by Opus 5 — accept to make real, dismiss to drop</div>
          <ul className="space-y-1.5">
            <AnimatePresence initial={false}>
              {suggested.map((t) => (
                <motion.li key={t.task_id} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, height: 0 }} className="flex items-start gap-2 rounded-xl bg-elev border border-line px-3 py-2">
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium">{t.title}</div>
                    <div className="text-xs text-muted mt-0.5 flex flex-wrap items-center gap-x-2">
                      <span className={cn("px-1.5 rounded-md text-[10px] font-semibold", OWNER_TONE[t.owner] || "bg-sunken")}>{t.owner}</span>
                      {t.property_id && !propertyId && <span>{propertyLabel(t.property_id)}</span>}
                      {t.due && <span className="flex items-center gap-1"><Clock size={11} /> {fmtDate(t.due)}</span>}
                      <span className={cn("capitalize", PRIO[t.priority])}>{t.priority}</span>
                      {t.why && <span className="text-faint">— {t.why}</span>}
                    </div>
                    {t.evidence?.[0]?.quote && <button onClick={() => t.evidence?.[0]?.source_sha && open({ sha: t.evidence[0].source_sha, highlight: t.evidence[0].quote.slice(0, 60) })} className="mt-1 text-[11.5px] text-left italic text-muted border-l-2 border-accent/40 pl-2 hover:text-fg line-clamp-2">“{t.evidence[0].quote}”</button>}
                  </div>
                  <Button size="sm" variant="soft" onClick={() => mutate(t.task_id, "open")}><Check size={13} /> Accept</Button>
                  <Button size="icon" variant="ghost" onClick={() => mutate(t.task_id, "dismissed")}><X size={14} /></Button>
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        </div>
      )}

      {active.length === 0 && suggested.length === 0 && !q.isLoading && <Empty title="No tasks here." sub="Add one, or ask the AI a question — it suggests next steps with evidence." />}

      {groups.map(([g, list]) => (
        <div key={g}>
          {g && <div className="flex items-center gap-2 mb-1.5"><span className={cn("px-2 h-5 rounded-md text-[11px] font-semibold grid place-items-center", OWNER_TONE[g] || "bg-sunken text-muted")}>{groupBy === "owner" ? g : propertyLabel(g)}</span><span className="text-[11px] text-faint">{list.length}</span></div>}
          <ul className={cn("rounded-2xl border border-line bg-elev divide-y divide-line overflow-hidden")}>
            <AnimatePresence initial={false}>
              {list.map((t) => (
                <motion.li key={t.task_id} layout initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className={cn("flex items-start gap-3 px-3 py-3 group hover:bg-sunken/60 transition", t.status === "done" && "opacity-60")}>
                  <label className="flex items-center gap-2 cursor-pointer select-none shrink-0 mt-0.5" title={t.status === "done" ? "Mark not done" : "Mark done"}>
                    <Checkbox checked={t.status === "done"} onCheckedChange={(v) => mutate(t.task_id, v ? "done" : "open")} />
                    <span className={cn("text-[10px] font-semibold uppercase tracking-wide w-9", t.status === "done" ? "text-good" : "text-faint group-hover:text-accent")}>{t.status === "done" ? "Done" : "Tick"}</span>
                  </label>
                  <div className="flex-1 min-w-0">
                    <div className={cn("text-[13px] font-medium", t.status === "done" && "line-through")}>{t.title}</div>
                    <div className="text-xs text-muted mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      {groupBy !== "owner" && <span className={cn("px-1.5 rounded-md text-[10px] font-semibold", OWNER_TONE[t.owner] || "bg-sunken")}>{t.owner}</span>}
                      {t.property_id && !propertyId && groupBy !== "property" && <span>{propertyLabel(t.property_id)}</span>}
                      {t.due && <span className={cn("flex items-center gap-1", t.status !== "done" && new Date(t.due) < new Date() && "text-critical font-medium")}><Clock size={11} /> {fmtDate(t.due)}</span>}
                      <span className={cn("capitalize", PRIO[t.priority])}>{t.priority}</span>
                      {t.source !== "manual" && <Badge tone="accent"><Sparkles size={9} /> AI</Badge>}
                      {t.status === "done" && t.done_by && <span className="text-faint">done by {t.done_by} · {fmtDate(t.done_at)}</span>}
                      {!compact && t.why && <span className="text-faint">— {t.why}</span>}
                    </div>
                    {!compact && t.evidence?.[0]?.quote && <button onClick={() => t.evidence?.[0]?.source_sha && open({ sha: t.evidence[0].source_sha, highlight: t.evidence[0].quote.slice(0, 60) })} className="mt-1 text-[11.5px] text-left italic text-muted border-l-2 border-line pl-2 hover:text-fg hover:border-accent line-clamp-1">“{t.evidence[0].quote}”</button>}
                  </div>
                  <Tip content="History"><button className="opacity-0 group-hover:opacity-100 h-7 w-7 grid place-items-center rounded-lg text-faint hover:bg-sunken" onClick={async () => { const h = await api.get<any[]>(`/tasks/${t.task_id}/history`); toast.message("History", { description: h.map((x) => `${fmtDate(x.at, "MMM d HH:mm")} · ${x.action} · ${x.by}`).join("\n") }); }}><History size={13} /></button></Tip>
                  {t.status === "open" && <button className="opacity-0 group-hover:opacity-100 h-7 w-7 grid place-items-center rounded-lg text-faint hover:bg-sunken" onClick={() => mutate(t.task_id, "dismissed")} title="Dismiss"><X size={13} /></button>}
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        </div>
      ))}

      <AddTask open={add} onOpenChange={setAdd} propertyId={propertyId} />
    </div>
  );
}

export function AddTask({ open, onOpenChange, propertyId }: { open: boolean; onOpenChange: (v: boolean) => void; propertyId?: string }) {
  const qc = useQueryClient();
  const props = useQuery({ queryKey: ["properties"], queryFn: () => api.get<any[]>("/properties"), staleTime: 60_000 });
  const [f, setF] = React.useState({ title: "", owner: "Rakesh", property_id: propertyId || "", priority: "normal", due: "", why: "" });
  const submit = async () => {
    if (!f.title.trim()) return;
    await api.post("/tasks", { ...f, property_id: f.property_id || null, due: f.due || null });
    qc.invalidateQueries({ queryKey: ["tasks"] }); qc.invalidateQueries({ queryKey: ["dashboard"] });
    onOpenChange(false); setF({ title: "", owner: "Rakesh", property_id: propertyId || "", priority: "normal", due: "", why: "" });
    toast.success("Task added");
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New task">
        <div className="space-y-3">
          <Input autoFocus placeholder="What needs doing?" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} onKeyDown={(e) => e.key === "Enter" && submit()} />
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-muted">Owner<Select className="w-full mt-1" value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })}>{["Rakesh", "JP", "Manjunath", "Wes"].map((o) => <option key={o}>{o}</option>)}</Select></label>
            <label className="text-xs text-muted">Priority<Select className="w-full mt-1" value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}>{["critical", "high", "normal", "low"].map((o) => <option key={o}>{o}</option>)}</Select></label>
            <label className="text-xs text-muted">Property<Select className="w-full mt-1" value={f.property_id} onChange={(e) => setF({ ...f, property_id: e.target.value })}><option value="">Portfolio</option>{(props.data || []).map((p) => <option key={p.property_id} value={p.property_id}>{p.address}</option>)}</Select></label>
            <label className="text-xs text-muted">Due<Input type="date" className="mt-1" value={f.due} onChange={(e) => setF({ ...f, due: e.target.value })} /></label>
          </div>
          <Textarea rows={2} placeholder="Why (optional)" value={f.why} onChange={(e) => setF({ ...f, why: e.target.value })} />
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" onClick={submit}>Add</Button></div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
