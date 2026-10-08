"use client";

/**
 * Wes's sheet, item by item. Each step: the title and detail written for him,
 * the due date, how long it has been open; a reply box; "Done on my side".
 * Everything typed here is recorded and read by RKB the same day, and by the
 * morning writer the next day. Nothing on this page comes from RKB's internal
 * reasoning — only the projection the server built for this organisation.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCircle2, Clock, History, Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button, Textarea } from "@/components/ui";
import { cn, fmtDate } from "@/lib/utils";
import type { PortalSheet, PortalStep, PortalTask } from "@/lib/types";

export default function PortalPage() {
  const q = useQuery({ queryKey: ["portal-sheet"], queryFn: () => api.get<{ sheet: PortalSheet | null; message?: string }>("/portal/sheet"), staleTime: 30_000 });
  const tasks = useQuery({ queryKey: ["portal-tasks"], queryFn: () => api.get<{ items: PortalTask[] }>("/portal/tasks"), staleTime: 30_000 });
  const sheet = q.data?.sheet ?? null;
  // Read receipt: once per sheet version.
  React.useEffect(() => { if (sheet?.sheet_id) api.post("/portal/viewed", { sheet_id: sheet.sheet_id }).catch(() => {}); }, [sheet?.sheet_id]);
  const [tab, setTab] = React.useState<"sheet" | "tasks" | "history">("sheet");
  const openTasks = (tasks.data?.items || []).filter((t) => !t.reported_done).length;

  if (q.isLoading) return <div className="text-sm text-muted">Loading your sheet…</div>;

  return (
    <div className="space-y-5">
      <div className="border-b border-[#D9D3C7] dark:border-line pb-3 flex items-end justify-between gap-4">
        <div>
          <div className="text-[10px] font-bold tracking-[0.22em] text-[#B0893B]">RKB CONSULTING GROUP, INC.</div>
          <h1 className="serif text-[26px] font-bold text-[#1F3550] dark:text-[#dbe3ee] leading-tight mt-1">{tab === "tasks" ? "My tasks" : sheet ? `Next steps — ${fmtDate(sheet.day, "d MMMM yyyy")}` : "Next steps"}</h1>
          {tab === "tasks"
            ? <div className="serif italic text-[13px] text-[#6B6B76] mt-0.5">{openTasks} open task{openTasks === 1 ? "" : "s"} assigned to you</div>
            : sheet && <div className="serif italic text-[13px] text-[#6B6B76] mt-0.5">{sheet.step_count} item{sheet.step_count === 1 ? "" : "s"} across {sheet.properties.length} propert{sheet.properties.length === 1 ? "y" : "ies"} · {sheet.open_count} still to answer</div>}
        </div>
        <div className="flex gap-1 text-xs">
          <button onClick={() => setTab("sheet")} className={cn("h-8 px-3 rounded-lg", tab === "sheet" ? "bg-[#1F3550] text-white" : "text-muted hover:bg-sunken")}>Today's sheet</button>
          <button onClick={() => setTab("tasks")} className={cn("h-8 px-3 rounded-lg flex items-center gap-1.5", tab === "tasks" ? "bg-[#1F3550] text-white" : "text-muted hover:bg-sunken")}>My tasks{openTasks > 0 && <span className={cn("text-[10px] font-semibold rounded-full px-1.5 h-4 grid place-items-center", tab === "tasks" ? "bg-white/20" : "bg-[#B0893B]/20 text-[#1F3550] dark:text-fg")}>{openTasks}</span>}</button>
          <button onClick={() => setTab("history")} className={cn("h-8 px-3 rounded-lg flex items-center gap-1.5", tab === "history" ? "bg-[#1F3550] text-white" : "text-muted hover:bg-sunken")}><History size={13} /> What I've said</button>
        </div>
      </div>
      {tab === "tasks" && <TaskList items={tasks.data?.items || []} loading={tasks.isLoading} />}
      {tab === "history" && <HistoryList />}
      {tab === "sheet" && !sheet && <div className="rounded-2xl border border-line bg-elev p-6 text-sm text-muted">{q.data?.message || "No sheet has been published for you yet."}</div>}
      {tab === "sheet" && sheet && (
        <>
          <p className="text-[13px] text-muted leading-relaxed">A line per item is plenty — done, in hand, or blocked and why. Rakesh sees your replies the same day; anything you mark done is checked and comes off the next sheet.</p>
          {sheet.properties.map((p, i) => (
            <section key={p.property_id} className="rounded-2xl border border-[#D9D3C7] dark:border-line bg-elev overflow-hidden">
              <div className="px-5 pt-4 pb-2">
                <div className="text-[10px] font-bold tracking-[0.22em] text-[#B0893B]">PROPERTY {String(i + 1).padStart(2, "0")}</div>
                <h2 className="serif text-[19px] font-bold text-[#1F3550] dark:text-[#dbe3ee] leading-tight">{p.address}</h2>
              </div>
              <div className="px-5 pb-5 space-y-3">
                {p.steps.length === 0 && <div className="text-[12.5px] text-faint">Nothing for you on this property today.</div>}
                {p.steps.map((s, n) => <StepItem key={s.step_id} step={s} n={n + 1} />)}
              </div>
            </section>
          ))}
        </>
      )}
    </div>
  );
}

const PRIO_TONE: Record<string, string> = { critical: "text-[#B4432B] font-semibold", high: "text-[#B0893B] font-semibold", normal: "", low: "text-faint" };

/** Wes's open tasks, the way Rakesh Sir's board shows them under "Wes" —
 *  grouped by property, overdue first — minus RKB's notes. */
function TaskList({ items, loading }: { items: PortalTask[]; loading: boolean }) {
  if (loading) return <div className="text-sm text-muted">Loading your tasks…</div>;
  if (!items.length) return <div className="rounded-2xl border border-line bg-elev p-6 text-sm text-muted">No open tasks assigned to you right now.</div>;
  const groups = Object.entries(items.reduce((acc, t) => { const k = t.address || "General"; (acc[k] ||= []).push(t); return acc; }, {} as Record<string, PortalTask[]>))
    .sort((a, b) => a[0].localeCompare(b[0]));
  return (
    <>
      <p className="text-[13px] text-muted leading-relaxed">These are the tasks Rakesh has assigned to you. Reply on any of them, or mark it done on your side — Rakesh confirms and ticks it off.</p>
      {groups.map(([address, list], i) => (
        <section key={address} className="rounded-2xl border border-[#D9D3C7] dark:border-line bg-elev overflow-hidden">
          <div className="px-5 pt-4 pb-2">
            <div className="text-[10px] font-bold tracking-[0.22em] text-[#B0893B]">PROPERTY {String(i + 1).padStart(2, "0")}</div>
            <h2 className="serif text-[19px] font-bold text-[#1F3550] dark:text-[#dbe3ee] leading-tight">{address}</h2>
          </div>
          <div className="px-5 pb-5 space-y-3">
            {list.map((t, n) => <TaskItem key={t.task_id} task={t} n={n + 1} />)}
          </div>
        </section>
      ))}
    </>
  );
}

function TaskItem({ task, n }: { task: PortalTask; n: number }) {
  const qc = useQueryClient();
  const [text, setText] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["portal-tasks"] });
  const reply = useMutation({
    mutationFn: () => api.post(`/portal/tasks/${task.task_id}/reply`, { text }),
    onSuccess: () => { setText(""); setOpen(false); toast.success("Reply sent to Rakesh"); refresh(); },
    onError: (e: any) => toast.error(e.message || "Could not send"),
  });
  const done = useMutation({
    mutationFn: () => api.post(`/portal/tasks/${task.task_id}/done`, { text }),
    onSuccess: () => { setText(""); setOpen(false); toast.success("Marked done on your side — Rakesh will confirm"); refresh(); },
    onError: (e: any) => toast.error(e.message || "Could not mark done"),
  });
  const busy = reply.isPending || done.isPending;
  const replies = (task.replies || []).filter((r) => r.action === "replied");
  const overdue = !!task.due && !task.reported_done && new Date(task.due) < new Date();
  return (
    <div className={cn("rounded-r-xl bg-[#F7F2EA] dark:bg-[#2a2620] border-l-[3px] border-[#B0893B]", task.reported_done && "opacity-80")}>
      <div className="flex gap-4 px-4 py-3.5">
        <div className="serif text-[#B0893B] font-bold text-[17px] leading-6 w-4 shrink-0">{n}</div>
        <div className="min-w-0 flex-1">
          <div className={cn("font-semibold text-[#1F3550] dark:text-[#dbe3ee] leading-snug text-[14px]", task.reported_done && "line-through")}>{task.title}</div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-[11.5px] text-[#6B6B76]">
            {task.due && <span className={cn("font-semibold flex items-center gap-1", overdue ? "text-[#B4432B]" : "text-[#1F3550] dark:text-fg")}><Clock size={11} /> {overdue ? "Was due" : "By"} {fmtDate(task.due, "d MMM yyyy")}</span>}
            {task.priority !== "normal" && <span className={cn("capitalize", PRIO_TONE[task.priority])}>{task.priority}</span>}
          </div>
          {task.reported_done && (
            <div className="mt-2 text-[12px] text-good flex items-center gap-1.5"><CheckCircle2 size={14} /> You marked this done {fmtDate(task.reported_done.at, "d MMM HH:mm")} — Rakesh will confirm.</div>
          )}
          {replies.length > 0 && (
            <div className="mt-2 space-y-1">
              {replies.map((r) => (
                <div key={r.event_id} className="rounded-lg bg-white/70 dark:bg-black/20 border border-[#D9D3C7] dark:border-line px-3 py-2 text-[12px]">
                  <span className="font-semibold text-[#1F3550] dark:text-fg">{r.by || r.name}</span> <span className="text-faint">{fmtDate(r.at, "d MMM HH:mm")}</span>
                  <div className="text-[#2A2A2E] dark:text-fg/85 whitespace-pre-wrap mt-0.5">{r.text}</div>
                </div>
              ))}
            </div>
          )}
          {!task.reported_done && (
            open ? (
              <div className="mt-3 space-y-2">
                <Textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder="Where does this stand? Done, in hand, or blocked — and why." className="bg-white dark:bg-black/20" />
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="primary" disabled={busy || !text.trim()} onClick={() => reply.mutate()}>{reply.isPending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />} Send reply</Button>
                  <Button size="sm" disabled={busy} onClick={() => done.mutate()}>{done.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Done on my side</Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setOpen(false); setText(""); }}>Cancel</Button>
                </div>
              </div>
            ) : (
              <div className="mt-3"><Button size="sm" onClick={() => setOpen(true)}>Reply</Button></div>
            )
          )}
        </div>
      </div>
    </div>
  );
}

function StepItem({ step, n }: { step: PortalStep; n: number }) {
  const qc = useQueryClient();
  const [text, setText] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["portal-sheet"] });
  const reply = useMutation({
    mutationFn: () => api.post(`/portal/steps/${step.step_id}/reply`, { text }),
    onSuccess: () => { setText(""); setOpen(false); toast.success("Reply sent to Rakesh"); refresh(); },
    onError: (e: any) => toast.error(e.message || "Could not send"),
  });
  const done = useMutation({
    mutationFn: () => api.post(`/portal/steps/${step.step_id}/done`, { text }),
    onSuccess: () => { setText(""); setOpen(false); toast.success("Marked done on your side — Rakesh will confirm"); refresh(); },
    onError: (e: any) => toast.error(e.message || "Could not mark done"),
  });
  const busy = reply.isPending || done.isPending;
  const replies = step.replies.filter((r) => r.action === "replied");
  return (
    <div className={cn("rounded-r-xl bg-[#F7F2EA] dark:bg-[#2a2620] border-l-[3px] border-[#B0893B]", step.reported_done && "opacity-80")}>
      <div className="flex gap-4 px-4 py-3.5">
        <div className="serif text-[#B0893B] font-bold text-[17px] leading-6 w-4 shrink-0">{n}</div>
        <div className="min-w-0 flex-1">
          <div className={cn("font-semibold text-[#1F3550] dark:text-[#dbe3ee] leading-snug text-[14px]", step.reported_done && "line-through")}>{step.title}</div>
          {(step.carried_days ?? 0) > 0 && !step.reported_done && (
            <div className="text-[11.5px] font-semibold text-[#B4432B] mt-1">Still open — on your sheet since {fmtDate(step.first_seen || null, "d MMM")} (day {(step.carried_days ?? 0) + 1})</div>
          )}
          <p className="text-[13px] leading-relaxed text-[#2A2A2E] dark:text-fg/85 mt-1.5">{step.detail}</p>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-[11.5px] text-[#6B6B76]">
            {step.due && <span className="font-semibold text-[#1F3550] dark:text-fg flex items-center gap-1"><Clock size={11} /> By {fmtDate(step.due, "d MMM yyyy")}</span>}
            {step.urgency === "critical" && <span className="text-[#B4432B] font-semibold">Critical</span>}
          </div>
          {step.reported_done && (
            <div className="mt-2 text-[12px] text-good flex items-center gap-1.5"><CheckCircle2 size={14} /> You marked this done {fmtDate(step.replies.find((r) => r.action === "reported_done")?.at || null, "d MMM HH:mm")} — Rakesh will confirm.</div>
          )}
          {replies.length > 0 && (
            <div className="mt-2 space-y-1">
              {replies.map((r) => (
                <div key={r.event_id} className="rounded-lg bg-white/70 dark:bg-black/20 border border-[#D9D3C7] dark:border-line px-3 py-2 text-[12px]">
                  <span className="font-semibold text-[#1F3550] dark:text-fg">{r.name}</span> <span className="text-faint">{fmtDate(r.at, "d MMM HH:mm")}</span>
                  <div className="text-[#2A2A2E] dark:text-fg/85 whitespace-pre-wrap mt-0.5">{r.text}</div>
                </div>
              ))}
            </div>
          )}
          {!step.reported_done && (
            open ? (
              <div className="mt-3 space-y-2">
                <Textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder="Where does this stand? Done, in hand, or blocked — and why." className="bg-white dark:bg-black/20" />
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="primary" disabled={busy || !text.trim()} onClick={() => reply.mutate()}>{reply.isPending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />} Send reply</Button>
                  <Button size="sm" disabled={busy} onClick={() => done.mutate()} title="Tell Rakesh this item is done on your side; add a line about what was done if you can">{done.isPending ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Done on my side</Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setOpen(false); setText(""); }}>Cancel</Button>
                </div>
              </div>
            ) : (
              <div className="mt-3"><Button size="sm" onClick={() => setOpen(true)}>Reply</Button></div>
            )
          )}
        </div>
      </div>
    </div>
  );
}

function HistoryList() {
  const q = useQuery({ queryKey: ["portal-history"], queryFn: () => api.get<{ items: { event_id: string; at: string; name?: string; action: string; text?: string; step_title?: string; address?: string }[] }>("/portal/history") });
  const items = q.data?.items || [];
  if (q.isLoading) return <div className="text-sm text-muted">Loading…</div>;
  if (!items.length) return <div className="rounded-2xl border border-line bg-elev p-6 text-sm text-muted">Nothing yet. Replies you send and items you mark done appear here, with the time.</div>;
  return (
    <div className="space-y-2">
      {items.map((e) => (
        <div key={e.event_id} className="rounded-xl border border-line bg-elev px-4 py-3 text-[12.5px]">
          <div className="flex flex-wrap items-center gap-x-2 text-[11.5px] text-muted">
            <span className="font-semibold text-fg">{e.action === "reported_done" ? "Marked done" : "Reply"}</span>
            <span>·</span><span>{fmtDate(e.at, "d MMM yyyy, HH:mm")}</span>
            {e.address && <><span>·</span><span>{e.address}</span></>}
          </div>
          {e.step_title && <div className="text-[#1F3550] dark:text-[#dbe3ee] font-medium mt-1">{e.step_title}</div>}
          {e.text && <div className="whitespace-pre-wrap mt-0.5">{e.text}</div>}
        </div>
      ))}
    </div>
  );
}
