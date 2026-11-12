"use client";

/* The property file, as a person reads it: where it stands, what is open, what
   happened this week, the money, the facts. Kept current by every email, sent
   reply and call note — nobody fills this in. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { type LucideIcon, ArrowDownLeft, ArrowUpRight, CalendarClock, FileText, MessageSquareText, Phone, Plus, RefreshCw, X } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Checkbox, Input, Select, Skeleton } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { useUser } from "@/components/providers";
import { DraftCard } from "@/components/brain/draft";
import { ago, cn, fmtDate } from "@/lib/utils";
import { KIND_LABEL, OWNER_LABEL, errText, type Activity, type OpenItem, type PropertyFile } from "@/lib/brain";

const KIND_ICON: Record<string, LucideIcon> = { email_in: ArrowDownLeft, email_out: ArrowUpRight, call: Phone, document: FileText, said_in_chat: MessageSquareText };

function Section({ title, right, children, className }: { title: string; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("pt-6", className)}>
      <div className="flex items-center justify-between mb-2.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-faint">{title}</h2>
        {right}
      </div>
      {children}
    </section>
  );
}

export function ItemRow({ item, onDone, onDrop, showProperty }: { item: OpenItem; onDone: () => void; onDrop?: () => void; showProperty?: boolean }) {
  const evidence = useEvidence();
  const waiting = item.kind === "waiting";
  return (
    <li className="group flex items-start gap-3 py-2.5">
      <div className="pt-0.5"><Checkbox checked={false} onCheckedChange={onDone} size={20} label={waiting ? "Mark received" : "Mark done"} /></div>
      <div className="min-w-0 flex-1">
        <div className="text-[13.5px] leading-snug">{item.title}</div>
        {item.detail && <div className="text-[12.5px] text-muted mt-0.5 leading-relaxed whitespace-pre-line">{item.detail}</div>}
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
          {showProperty && <Badge>{item.property}</Badge>}
          <Badge tone={item.owner === "rakesh" ? "accent" : "neutral"}>{waiting ? `Waiting on ${OWNER_LABEL[item.owner] || item.owner}` : OWNER_LABEL[item.owner] || item.owner}</Badge>
          {item.kind === "decision" && <Badge tone="info">Decision</Badge>}
          {item.due && <Badge tone={item.overdue ? "critical" : "high"}><CalendarClock size={10} /> {item.overdue ? "Was due" : "Due"} {fmtDate(item.due, "MMM d")}</Badge>}
          {item.opened_at && <span>open {ago(item.opened_at).replace(" ago", "")}</span>}
          {item.source_sha && <button type="button" className="text-accent hover:underline" onClick={() => evidence.open({ sha: item.source_sha! })}>source</button>}
        </div>
      </div>
      {onDrop && <button type="button" onClick={onDrop} title="Not needed" className="mt-0.5 h-7 w-7 grid place-items-center rounded-lg text-faint hover:text-critical hover:bg-sunken opacity-0 group-hover:opacity-100 transition"><X size={14} /></button>}
    </li>
  );
}

export function ActivityList({ rows, showProperty }: { rows: Activity[]; showProperty?: boolean }) {
  const evidence = useEvidence();
  if (!rows.length) return <div className="text-[13px] text-muted">Nothing came in or went out in this period.</div>;
  return (
    <ol className="space-y-3">
      {rows.map((a, i) => {
        const Icon = KIND_ICON[a.kind] || FileText;
        return (
          <li key={i} className="flex gap-3">
            <div className={cn("mt-0.5 h-7 w-7 rounded-lg grid place-items-center shrink-0", a.kind === "email_out" ? "bg-accent-soft text-accent" : "bg-sunken text-muted")}><Icon size={14} /></div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <button type="button" disabled={!a.sha} onClick={() => a.sha && evidence.open({ sha: a.sha })} className={cn("text-[13.5px] font-medium text-left leading-snug", a.sha && "hover:text-accent")}>{a.title}</button>
                <span className="text-[11px] text-faint whitespace-nowrap">{fmtDate(a.at, "EEE MMM d")} · {KIND_LABEL[a.kind] || a.kind}{a.who ? ` · ${a.who}` : ""}</span>
              </div>
              {showProperty && a.property && <div className="mt-0.5"><Badge>{a.property}</Badge></div>}
              {a.summary && <div className="text-[12.5px] text-muted mt-0.5 leading-relaxed whitespace-pre-line">{a.summary}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function PropertyFilePanel({ pid }: { pid: string }) {
  const { user } = useUser();
  const qc = useQueryClient();
  const [days, setDays] = React.useState(7);
  const key = ["brain", "property", pid, days];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<PropertyFile>(`/brain/properties/${pid}?days=${days}`), placeholderData: (prev) => prev });
  const [adding, setAdding] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [owner, setOwner] = React.useState("rakesh");
  const [busy, setBusy] = React.useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["brain"] });

  if (q.isLoading || !q.data) return <div className="p-6 space-y-3"><Skeleton className="h-8 w-2/3" /><Skeleton className="h-28" /><Skeleton className="h-64" /></div>;
  const f = q.data;

  const close = async (item: OpenItem, status: "done" | "dropped") => {
    // Optimistic: the row leaves at once; the server follows.
    qc.setQueryData<PropertyFile>(key, (cur) => cur ? { ...cur, open_items: (cur.open_items || []).filter((i) => i.id !== item.id) } : cur);
    try { await api.patch(`/brain/properties/${pid}/items/${item.id}`, { status }); toast.success(status === "done" ? "Done" : "Removed", { action: { label: "Undo", onClick: async () => { await api.patch(`/brain/properties/${pid}/items/${item.id}`, { status: "open" }); refresh(); } } }); }
    catch (e) { toast.error(errText(e)); }
    refresh();
  };
  const add = async () => {
    if (!title.trim()) return;
    await api.post(`/brain/properties/${pid}/items`, { title: title.trim(), owner });
    setTitle(""); setAdding(false); refresh();
  };
  const build = () => {
    setBusy(true);
    api.post<{ job_id: string }>(`/brain/properties/${pid}/rebuild`).then(({ job_id }) => {
      subscribeJob(job_id, (e) => { if (e.kind === "error") toast.error(e.data?.error || "Could not build the file"); }, () => { setBusy(false); refresh(); });
    }).catch((e) => { toast.error(e.message); setBusy(false); });
  };

  if (!f.exists) {
    return (
      <div className="p-8 max-w-xl">
        <h1 className="text-xl font-semibold tracking-tight">{f.address}</h1>
        <p className="text-sm text-muted mt-2 leading-relaxed">This property&apos;s file has not been opened yet. Opening it reads the recent mail, call notes and the earlier investigation once, then keeps itself current from every new email.</p>
        {user?.role === "ceo" && <Button variant="primary" className="mt-4" onClick={build} disabled={busy}>{busy ? "Reading the record… (2–3 minutes)" : "Open the file"}</Button>}
      </div>
    );
  }

  const items = f.open_items || [];
  const me = user?.user_id || "rakesh";
  const mine = items.filter((i) => i.kind !== "waiting" && i.owner === me);
  const team = items.filter((i) => i.kind !== "waiting" && i.owner !== me);
  const waiting = items.filter((i) => i.kind === "waiting");
  const drafts = f.drafts || [];

  return (
    <div className="px-6 lg:px-8 py-6 max-w-3xl">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-[22px] font-semibold tracking-tight leading-tight">{f.address}</h1>
          <div className="text-xs text-faint mt-1">File updated {ago(f.updated_at)}{f.last_event_at ? ` · last activity ${fmtDate(f.last_event_at, "MMM d")}` : ""}</div>
        </div>
        {!f.is_general && <a href={`/property/${pid}/records`} className="text-xs text-accent hover:underline whitespace-nowrap mt-1.5">Documents, timeline &amp; records</a>}
      </div>

      <p className="mt-4 text-[16.5px] leading-[1.5] font-medium tracking-[-0.005em]">{f.headline}</p>
      {f.status && <p className="mt-3 text-[14px] leading-[1.7] text-fg/90 whitespace-pre-line">{f.status}</p>}

      {drafts.length > 0 && (
        <Section title={drafts.length === 1 ? "A reply is ready for you" : `${drafts.length} replies are ready for you`}>
          <div className="space-y-3">{drafts.map((d) => <DraftCard key={d.draft_id} draft={d} compact showProperty={false} />)}</div>
        </Section>
      )}

      <Section title={`Needs you${mine.length ? ` · ${mine.length}` : ""}`} right={<Button size="sm" variant="ghost" onClick={() => setAdding((v) => !v)}><Plus size={13} /> Add</Button>}>
        {adding && (
          <div className="flex gap-2 mb-2">
            <Input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What needs doing?" onKeyDown={(e) => { if (e.key === "Enter") add(); }} />
            <Select value={owner} onChange={(e) => setOwner(e.target.value)}>{Object.entries(OWNER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
            <Button variant="primary" onClick={add}>Add</Button>
          </div>
        )}
        {mine.length === 0 ? <div className="text-[13px] text-muted">Nothing is waiting on you here.</div>
          : <ul className="divide-y divide-line">{mine.map((i) => <ItemRow key={i.id} item={i} onDone={() => close(i, "done")} onDrop={() => close(i, "dropped")} />)}</ul>}
      </Section>

      {team.length > 0 && (
        <Section title={`With the team · ${team.length}`}>
          <ul className="divide-y divide-line">{team.map((i) => <ItemRow key={i.id} item={i} onDone={() => close(i, "done")} onDrop={() => close(i, "dropped")} />)}</ul>
        </Section>
      )}
      {waiting.length > 0 && (
        <Section title={`Waiting on others · ${waiting.length}`}>
          <ul className="divide-y divide-line">{waiting.map((i) => <ItemRow key={i.id} item={i} onDone={() => close(i, "done")} onDrop={() => close(i, "dropped")} />)}</ul>
        </Section>
      )}

      <Section title={`Last ${days} days`} right={
        <div className="flex rounded-lg border border-line overflow-hidden text-[11px]">
          {[7, 14, 30].map((n) => <button key={n} type="button" onClick={() => setDays(n)} className={cn("px-2.5 h-6 transition", days === n ? "bg-fg text-bg font-semibold" : "text-muted hover:bg-sunken")}>{n}d</button>)}
        </div>}>
        <ActivityList rows={f.activity || []} />
      </Section>

      {(f.watch || []).length > 0 && (
        <Section title="Keep in view">
          <ul className="space-y-1.5">{f.watch!.map((w, i) => <li key={i} className="flex gap-2.5 text-[13px] leading-relaxed"><span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-high shrink-0" /><span className="whitespace-pre-line">{w}</span></li>)}</ul>
        </Section>
      )}

      {(f.money || []).length > 0 && (
        <Section title="Money — from RKB's workbooks">
          <div className="space-y-4">
            {f.money!.map((src, i) => (
              <div key={i} className="rounded-xl border border-line overflow-hidden">
                <div className="px-3.5 py-2 bg-sunken/70 text-[11.5px] text-muted flex items-center justify-between gap-2"><span className="truncate font-medium">{src.file}</span><span className="text-faint whitespace-nowrap">{src.as_of}</span></div>
                <table className="w-full text-[13px]"><tbody>
                  {src.rows.map((r, j) => (
                    <tr key={j} className="border-t border-line/70 align-top">
                      <td className="px-3.5 py-1.5 text-muted w-[42%]">{r.item}</td>
                      <td className="px-3.5 py-1.5 tnum">{r.value}{r.note ? <div className="text-[11px] text-faint mt-0.5">{r.note}</div> : null}</td>
                    </tr>
                  ))}
                </tbody></table>
              </div>
            ))}
          </div>
        </Section>
      )}

      {(f.facts || []).length > 0 && (
        <Section title="Key facts">
          <dl className="grid grid-cols-[minmax(110px,30%)_1fr] gap-x-4 gap-y-1.5 text-[13px]">
            {f.facts!.map((x, i) => <React.Fragment key={i}><dt className="text-muted">{x.key}</dt><dd className="leading-relaxed">{x.value}</dd></React.Fragment>)}
          </dl>
        </Section>
      )}

      {(f.closed_items || []).length > 0 && (
        <Section title="Recently closed">
          <ul className="space-y-1">{f.closed_items!.slice(0, 8).map((i) => (
            <li key={i.id} className="text-[12.5px] text-muted flex gap-2"><span className="text-good">✓</span><span className="line-through decoration-line-strong">{i.title}</span>{i.closed_note ? <span className="text-faint no-underline">— {i.closed_note}</span> : null}</li>
          ))}</ul>
        </Section>
      )}

      {user?.role === "ceo" && (
        <div className="pt-8 pb-4 text-[11px] text-faint flex items-center gap-2">
          Built from the record {fmtDate(f.built_at, "MMM d")} and updated by every email since.
          <button type="button" onClick={() => { if (window.confirm("Rebuild this file from the records? Open items and waiting drafts are re-derived.")) build(); }} disabled={busy} className="inline-flex items-center gap-1 text-accent hover:underline"><RefreshCw size={10} /> {busy ? "Rebuilding…" : "Rebuild"}</button>
        </div>
      )}
    </div>
  );
}
