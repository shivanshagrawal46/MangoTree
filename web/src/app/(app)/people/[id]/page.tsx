"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Mail, Paperclip } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, CardHeader, Skeleton, Stat } from "@/components/ui";
import { ThreadDialog } from "../../property/[pid]/page";
import { fmtDate, propertyLabel, initials } from "@/lib/utils";

export default function PersonPage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({ queryKey: ["person", id], queryFn: () => api.get<any>(`/people/${id}`) });
  const [thread, setThread] = React.useState<string | null>(null);
  if (!q.data) return <div className="p-6"><Skeleton className="h-64" /></div>;
  const { person: p, entity: e, emails, by_property, edges, total_emails } = q.data;
  return (
    <div className="p-6 max-w-[1100px] mx-auto space-y-5">
      <div className="flex items-start gap-4">
        <div className="h-14 w-14 rounded-2xl bg-accent-soft text-accent grid place-items-center text-lg font-semibold">{initials(p.display_name)}</div>
        <div className="flex-1"><h1 className="text-2xl font-semibold tracking-tight">{p.display_name}</h1><div className="text-sm text-muted">{p.role} · {p.org}</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">{(p.addresses || []).map((a: string) => <Badge key={a}>{a}</Badge>)}<Badge tone={p.side === "rkb" ? "accent" : "neutral"}>{p.side}</Badge></div>
          {p.notes && <div className="text-xs text-muted mt-2 max-w-2xl">{p.notes}</div>}</div>
        <div className="grid grid-cols-3 gap-5"><Stat label="Emails" value={total_emails} /><Stat label="Properties" value={Object.keys(by_property).length} /><Stat label="First seen" value={e?.first_seen ? fmtDate(e.first_seen, "MMM yyyy") : "—"} /></div>
      </div>
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Card><CardHeader title="Recent emails" sub="Click to open the whole conversation" />
          <ul className="divide-y divide-line">{emails.map((m: any) => <li key={m.sha256} className="px-5 py-2.5 flex items-center gap-3 hover:bg-sunken cursor-pointer" onClick={() => setThread(m.thread_key || m.sha256)}><Mail size={14} className="text-muted shrink-0" /><div className="min-w-0 flex-1"><div className="text-[13px] truncate">{m.subject || "(no subject)"}</div><div className="text-[11px] text-faint">{m.from}{m.attachments > 0 && <span className="ml-2 inline-flex items-center gap-0.5"><Paperclip size={10} />{m.attachments}</span>}</div></div>{(m.property_ids || []).slice(0, 2).map((pid: string) => <Badge key={pid}>{propertyLabel(pid)}</Badge>)}<span className="text-[11px] tnum text-faint w-20 text-right">{fmtDate(m.date, "MMM d, yy")}</span></li>)}</ul></Card>
        <div className="space-y-4">
          <Card><CardHeader title="By property" /><ul className="px-5 pb-4 space-y-1.5">{Object.entries(by_property).map(([pid, n]: any) => <li key={pid} className="flex justify-between text-xs"><a href={`/property/${pid}`} className="text-accent hover:underline">{propertyLabel(pid)}</a><span className="tnum">{n}</span></li>)}</ul></Card>
          {edges.length > 0 && <Card><CardHeader title="Connections" sub="Knowledge graph" /><ul className="px-5 pb-4 space-y-1 text-xs">{edges.slice(0, 20).map((x: any) => <li key={x.edge_id} className="text-muted"><span className="text-fg">{x.edge_type}</span> · {x.src === e?.entity_id ? x.dst : x.src}</li>)}</ul></Card>}
        </div>
      </div>
      <ThreadDialog threadKey={thread} onClose={() => setThread(null)} />
    </div>
  );
}
