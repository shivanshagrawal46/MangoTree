"use client";

/* Today — the one page to open in the morning. First "my day": the few things
   to do now (see components/brain/myday.tsx), and for Rakesh Sir the team. Then
   what is new on the construction jobs, what changed, and every property in a
   line. Nothing here has to be asked for. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, Skeleton } from "@/components/ui";
import { useUser } from "@/components/providers";
import { MyDay } from "@/components/brain/myday";
import { AlertsCard } from "@/components/brain/followups";
import { ActivityList } from "@/components/brain/file";
import { ago, cn, fmtDate } from "@/lib/utils";
import { type Home } from "@/lib/brain";

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

  const today = new Date().toLocaleDateString("en-US", { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="px-6 lg:px-10 py-8 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight">{greeting()}, {(user?.name || "").replace(/ Sir$/, "")}</h1>
          <p className="text-[14px] text-muted mt-1">{today}. Here is your day.</p>
        </div>
        <div className="flex items-center gap-2">
          {d.pending_records > 0 && <Badge tone="high">{d.pending_records} new record{d.pending_records === 1 ? "" : "s"} not read yet</Badge>}
          <Button size="sm" onClick={readNow} disabled={!!reading}><RefreshCw size={13} className={cn(reading && "animate-spin")} /> {reading ? reading.slice(0, 48) : "Check for new mail"}</Button>
        </div>
      </div>

      <MyDay />

      <AlertsCard />

      {d.missing.length > 0 && ceo && (
        <div className="mt-4 rounded-xl border border-high/25 bg-high-soft/60 px-4 py-2.5 text-[13px]">
          {d.missing.length} file{d.missing.length === 1 ? " has" : "s have"} not been opened yet: {d.missing.map((m) => <Link key={m.property_id} href={`/property/${m.property_id}`} className="text-accent hover:underline mr-2">{m.name}</Link>)}
        </div>
      )}

      <Card className="mt-6">
        <div className="flex items-center justify-between px-5 pt-4 pb-3">
          <div><div className="text-[14px] font-semibold tracking-tight">What changed</div><div className="text-xs text-muted mt-0.5">Emails, calls and files as they came in, newest first.</div></div>
          <div className="flex rounded-lg border border-line overflow-hidden text-[11px]">
            {[1, 3, 7].map((n) => <button key={n} type="button" onClick={() => setDays(n)} className={cn("px-2.5 h-6 transition", days === n ? "bg-fg text-bg font-semibold" : "text-muted hover:bg-sunken")}>{n === 1 ? "24h" : `${n}d`}</button>)}
          </div>
        </div>
        <div className="px-5 pb-5 max-h-[520px] overflow-y-auto"><ActivityList rows={d.activity.slice(0, ceo ? 120 : 40)} showProperty /></div>
      </Card>

      {(
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
                  {(p.open_mine?.[me] || 0) > 0 && <Badge tone="high">{p.open_mine[me]} need you</Badge>}
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
