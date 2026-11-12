"use client";

/* Today — the one page to open in the morning. Replies that are written and
   waiting, what needs you, what changed, and every property in a line.
   Nothing here has to be asked for. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, RefreshCw } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { useUser } from "@/components/providers";
import { ReplyList } from "@/components/brain/replies";
import { ActivityList, ItemRow } from "@/components/brain/file";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText, type Home, type OpenItem } from "@/lib/brain";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export default function TodayPage() {
  const { user } = useUser();
  const qc = useQueryClient();
  const [days, setDays] = React.useState(3);
  const q = useQuery({ queryKey: ["brain", "home", days], queryFn: () => api.get<Home>(`/brain/home?days=${days}`), placeholderData: (prev) => prev, refetchInterval: 120_000 });
  const [reading, setReading] = React.useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["brain"] });

  if (q.isLoading || !q.data) return <div className="p-8 max-w-6xl mx-auto space-y-4"><Skeleton className="h-10 w-80" /><Skeleton className="h-40" /><Skeleton className="h-72" /></div>;
  const d = q.data;
  const ceo = user?.role === "ceo";
  const me = user?.user_id || d.me;
  const mine = d.todos.filter((t) => t.owner === me);
  const drafts = ceo ? d.drafts : d.drafts.filter((x) => x.sender === me);
  const overdue = mine.filter((t) => t.overdue).length;

  const close = async (item: OpenItem, status: "done" | "dropped") => {
    qc.setQueryData<Home>(["brain", "home", days], (cur) => cur ? { ...cur, todos: cur.todos.filter((t) => t.id !== item.id) } : cur);
    try {
      await api.patch(`/brain/properties/${item.property_id}/items/${item.id}`, { status });
      toast.success(status === "done" ? "Done" : "Removed", { action: { label: "Undo", onClick: async () => { await api.patch(`/brain/properties/${item.property_id}/items/${item.id}`, { status: "open" }); refresh(); } } });
    } catch (e) { toast.error(errText(e)); }
    refresh();
  };
  const readNow = () => {
    setReading("Reading new mail…");
    api.post<{ job_id: string }>("/brain/run").then(({ job_id }) => {
      subscribeJob(job_id, (e) => {
        if (e.kind === "status" && e.data?.text) setReading(e.data.text);
        if (e.kind === "result") toast.success(e.data?.read ? `Read ${e.data.read} new record${e.data.read === 1 ? "" : "s"}${e.data.drafts ? `, wrote ${e.data.drafts} draft${e.data.drafts === 1 ? "" : "s"}` : ""}` : "Nothing new to read");
        if (e.kind === "error") toast.error(e.data?.error || "Could not read new mail");
      }, () => { setReading(null); refresh(); });
    }).catch((e) => { toast.error(e.message); setReading(null); });
  };

  const summary = [
    drafts.length ? `${drafts.length} repl${drafts.length === 1 ? "y is" : "ies are"} written and waiting` : "no replies waiting",
    mine.length ? `${mine.length} item${mine.length === 1 ? "" : "s"} need${mine.length === 1 ? "s" : ""} you${overdue ? ` (${overdue} overdue)` : ""}` : "nothing is waiting on you",
  ].join(" · ");
  const byProperty = mine.reduce<Record<string, OpenItem[]>>((acc, t) => { (acc[t.property] ||= []).push(t); return acc; }, {});

  return (
    <div className="px-6 lg:px-10 py-8 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight">{greeting()}, {(user?.name || "").replace(/ Sir$/, "")}</h1>
          <p className="text-[14px] text-muted mt-1 first-letter:uppercase">{summary}.</p>
        </div>
        <div className="flex items-center gap-2">
          {d.pending_records > 0 && <Badge tone="high">{d.pending_records} new record{d.pending_records === 1 ? "" : "s"} not read yet</Badge>}
          <Button size="sm" onClick={readNow} disabled={!!reading}><RefreshCw size={13} className={cn(reading && "animate-spin")} /> {reading ? reading.slice(0, 48) : "Check for new mail"}</Button>
        </div>
      </div>

      {d.missing.length > 0 && ceo && (
        <div className="mt-4 rounded-xl border border-high/25 bg-high-soft/60 px-4 py-2.5 text-[13px]">
          {d.missing.length} file{d.missing.length === 1 ? " has" : "s have"} not been opened yet: {d.missing.map((m) => <Link key={m.property_id} href={`/property/${m.property_id}`} className="text-accent hover:underline mr-2">{m.name}</Link>)}
        </div>
      )}

      <Card className="mt-7 overflow-hidden">
        <div className="flex items-center justify-between px-5 pt-4 pb-3">
          <div className="text-[14px] font-semibold tracking-tight">Emails to reply{drafts.length ? ` · ${drafts.length}` : ""}</div>
          {drafts.length > 8 && <Link href="/replies" className="text-xs text-accent hover:underline flex items-center gap-1">See all {drafts.length} <ArrowRight size={12} /></Link>}
        </div>
        <ReplyList drafts={drafts} limit={8} />
      </Card>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="space-y-6 min-w-0">
          <Card>
            <div className="px-5 pt-4 pb-1"><div className="text-[14px] font-semibold tracking-tight">Needs you</div><div className="text-xs text-muted mt-0.5">Tick it and it is closed. Send the email and it closes itself.</div></div>
            {mine.length === 0 ? <div className="px-5 pb-5 pt-2 text-[13px] text-muted">Nothing is waiting on you.</div> : (
              <div className="px-5 pb-3">
                {Object.entries(byProperty).map(([name, rows]) => (
                  <div key={name} className="pt-3">
                    <Link href={`/property/${rows[0].property_id}`} className="text-[11px] font-semibold uppercase tracking-[0.08em] text-faint hover:text-accent">{name}</Link>
                    <ul className="divide-y divide-line">{rows.map((t) => <ItemRow key={t.id} item={t} onDone={() => close(t, "done")} onDrop={() => close(t, "dropped")} />)}</ul>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-6 min-w-0">
          <Card>
            <div className="flex items-center justify-between px-5 pt-4 pb-3">
              <div className="text-[14px] font-semibold tracking-tight">What changed</div>
              <div className="flex rounded-lg border border-line overflow-hidden text-[11px]">
                {[1, 3, 7].map((n) => <button key={n} type="button" onClick={() => setDays(n)} className={cn("px-2.5 h-6 transition", days === n ? "bg-fg text-bg font-semibold" : "text-muted hover:bg-sunken")}>{n === 1 ? "24h" : `${n}d`}</button>)}
              </div>
            </div>
            <div className="px-5 pb-5 max-h-[640px] overflow-y-auto"><ActivityList rows={ceo ? d.activity : d.activity.slice(0, 30)} showProperty /></div>
          </Card>
        </div>
      </div>

      {ceo && (
        <section className="mt-9">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-[14px] font-semibold tracking-tight">Every property, in a line</h2>
            {d.stats.drafts_sent > 0 && <span className="text-xs text-faint">{d.stats.sent_as_written} of {d.stats.drafts_sent} drafts were sent as written</span>}
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {d.properties.map((p) => (
              <Link key={p.property_id} href={`/property/${p.property_id}`} className="group rounded-2xl border border-line bg-elev p-4 hover:border-accent/40 hover:shadow-[var(--shadow-sm)] transition">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-[13.5px] font-semibold tracking-tight group-hover:text-accent">{p.name}</div>
                  <span className="text-[10.5px] text-faint whitespace-nowrap mt-0.5">{ago(p.updated_at)}</span>
                </div>
                <div className="text-[12.5px] text-muted mt-1.5 leading-relaxed line-clamp-3">{p.headline || "—"}</div>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {p.drafts > 0 && <Badge tone="accent">{p.drafts} repl{p.drafts === 1 ? "y" : "ies"} ready</Badge>}
                  {(p.open_mine?.rakesh || 0) > 0 && <Badge tone="high">{p.open_mine.rakesh} need you</Badge>}
                  {p.waiting > 0 && <Badge>{p.waiting} waiting on others</Badge>}
                  {p.last_event_at && <span className="text-[11px] text-faint self-center">last activity {fmtDate(p.last_event_at, "MMM d")}</span>}
                </div>
              </Link>
            ))}
          </div>
        </section>
      )}

    </div>
  );
}
