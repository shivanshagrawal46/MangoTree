"use client";

/* Follow-ups and the alert that sits on Today.

   The list is worked out from the records every time (bills, daily logs, the
   money sheet, RKB's ticks) — nobody keeps it, so it cannot go stale. An item
   leaves by itself when the thing is done; a person can also put one aside. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, BellRing, Check, Clock, Copy } from "lucide-react";
import { api } from "@/lib/api";
import { Card, Skeleton } from "@/components/ui";
import { usd } from "@/components/brain/side";
import { ago, cn } from "@/lib/utils";
import { errText } from "@/lib/brain";

export type FollowItem = {
  key: string; kind: string; property_id: string; property: string; owner: string; owner_name: string; title: string; detail: string;
  since?: string | null; days: number | null; tab: string; amount?: number | null; level: "high" | "normal";
  mark?: { state: string; note?: string; by_name?: string; at?: string; until?: string | null } | null;
};
type Counts = Record<string, { open: number; high: number }>;
type FollowData = { items: FollowItem[]; counts: Counts; owners: Record<string, string>; me: string | null; message_for_wes: string; properties: number };
export type Alerts = {
  read_at?: string | null; analysed: boolean;
  new_logs: { property_id: string; property: string; count: number; site_now: string }[];
  in_review: { count: number; amount: number; new: number; bills: { bill_id: number; property_id: string; property: string; title: string; amount: number; date?: string; new: boolean }[] };
  mine: { owner: string; owner_name: string; open: number; high: number; top: { key: string; property_id: string; property: string; title: string; days: number | null; level: string; tab: string }[] } | null;
  counts: Counts;
};

const href = (i: { property_id: string; tab: string }) => (i.tab === "overview" ? `/property/${i.property_id}` : `/property/${i.property_id}?tab=${i.tab}`);
const OWNER_TONE: Record<string, string> = { manju: "bg-accent-soft text-accent", jp: "bg-good-soft text-good", rakesh: "bg-high-soft text-high", wes: "bg-sunken text-muted" };

/* Top of Today, for everyone at RKB: what is new on the construction jobs since
   Buildertrend was last read, and what is waiting for the person looking. */
export function AlertsCard() {
  const q = useQuery({ queryKey: ["brain", "alerts"], queryFn: () => api.get<Alerts>("/brain/alerts"), staleTime: 60_000, refetchInterval: 180_000 });
  const a = q.data;
  if (!a) return null;
  const nothing = a.new_logs.length === 0 && a.in_review.count === 0 && !(a.mine && a.mine.open);
  if (nothing) return null;
  return (
    <Card className="mt-4 overflow-hidden border-accent/25">
      <div className="px-4 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 bg-accent-soft/50 border-b border-line">
        <BellRing size={14} className="text-accent" />
        <div className="text-[13px] font-semibold">Construction — what is new</div>
        <div className="text-[11.5px] text-muted">Buildertrend read {ago(a.read_at)}</div>
        <Link href="/chase" className="ml-auto text-[12px] font-medium text-accent inline-flex items-center gap-1 hover:underline">All follow-ups <ArrowRight size={12} /></Link>
      </div>
      <div className="grid md:grid-cols-3 divide-y md:divide-y-0 md:divide-x divide-line">
        <div className="px-4 py-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-faint">New daily logs</div>
          {a.new_logs.length === 0 ? <div className="mt-1 text-[12.5px] text-muted">None since the last read.</div> : (
            <ul className="mt-1 space-y-1.5">
              {a.new_logs.map((l) => (
                <li key={l.property_id} className="text-[12.5px]">
                  <Link href={`/property/${l.property_id}?tab=site`} className="font-medium hover:text-accent">{l.property}</Link>
                  <span className="text-muted"> · {l.count} new</span>
                  {l.site_now && <div className="text-[11.5px] text-muted leading-snug line-clamp-2">{l.site_now}</div>}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="px-4 py-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-faint">Bills in review</div>
          <div className="mt-1 text-[12.5px]"><b className="font-semibold tnum">{a.in_review.count}</b> waiting · <span className="tnum">{usd(a.in_review.amount)}</span>{a.in_review.new > 0 && <span className="ml-1.5 text-[10.5px] font-semibold text-high">{a.in_review.new} NEW</span>}</div>
          <ul className="mt-1 space-y-0.5">
            {a.in_review.bills.slice(0, 5).map((b) => (
              <li key={b.bill_id} className="text-[11.5px] text-muted flex gap-2">
                <Link href={`/property/${b.property_id}?tab=sheet`} className="truncate flex-1 hover:text-accent">{b.property.split(" ").slice(1, 3).join(" ")} · {b.title}</Link>
                <span className="tnum shrink-0">{usd(b.amount)}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="px-4 py-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.09em] text-faint">{a.mine ? `Waiting for you, ${a.mine.owner_name.replace(/ Sir$/, "")}` : "Follow-ups"}</div>
          {a.mine ? (
            <>
              <div className="mt-1 text-[12.5px]"><b className="font-semibold tnum">{a.mine.open}</b> follow-up{a.mine.open === 1 ? "" : "s"}{a.mine.high > 0 && <span className="text-high"> · {a.mine.high} late or urgent</span>}</div>
              <ul className="mt-1 space-y-0.5">
                {a.mine.top.map((i) => (
                  <li key={i.key} className="text-[11.5px] flex gap-1.5">
                    <span className={cn("mt-[5px] h-1.5 w-1.5 rounded-full shrink-0", i.level === "high" ? "bg-critical" : "bg-line-strong")} />
                    <Link href={href(i)} className="truncate text-muted hover:text-accent">{i.property.split(" ").slice(1, 3).join(" ")} · {i.title}</Link>
                  </li>
                ))}
              </ul>
            </>
          ) : <div className="mt-1 text-[12.5px] text-muted">{Object.values(a.counts).reduce((n, c) => n + c.open, 0)} open across the team.</div>}
        </div>
      </div>
    </Card>
  );
}

export function FollowUpsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brain", "followups"], queryFn: () => api.get<FollowData>("/brain/followups"), refetchInterval: 180_000 });
  const [who, setWho] = React.useState<string | null>(null);
  const [aside, setAside] = React.useState(false);
  if (!q.data) return <div className="p-8 max-w-5xl mx-auto space-y-3"><Skeleton className="h-9 w-64" /><Skeleton className="h-64" /></div>;
  const d = q.data;
  const owner = who ?? d.me ?? "all";
  const open = d.items.filter((i) => !i.mark);
  const shown = (aside ? d.items.filter((i) => i.mark) : open).filter((i) => owner === "all" || i.owner === owner);
  const groups: { name: string; pid: string; items: FollowItem[] }[] = [];
  [...shown].sort((a, b) => a.property.localeCompare(b.property) || (a.level === b.level ? (b.days || 0) - (a.days || 0) : a.level === "high" ? -1 : 1))
    .forEach((i) => { const g = groups[groups.length - 1]; if (g && g.pid === i.property_id) g.items.push(i); else groups.push({ name: i.property, pid: i.property_id, items: [i] }); });
  const mark = async (i: FollowItem, state: "done" | "later" | "open") => {
    qc.setQueryData<FollowData>(["brain", "followups"], (cur) => cur ? { ...cur, items: cur.items.map((x) => x.key === i.key ? { ...x, mark: state === "open" ? null : { state, by_name: "you" } } : x) } : cur);
    try {
      await api.post("/brain/followups/mark", { key: i.key, state });
      if (state !== "open") toast.success(state === "done" ? "Put aside as done." : "Put aside for 3 days.", { action: { label: "Undo", onClick: () => mark(i, "open") } });
    } catch (e) { toast.error(errText(e)); }
    qc.invalidateQueries({ queryKey: ["brain", "followups"] });
    qc.invalidateQueries({ queryKey: ["brain", "alerts"] });
    qc.invalidateQueries({ queryKey: ["brain", "counts"] });
  };
  const copyWes = async () => { try { await navigator.clipboard.writeText(d.message_for_wes); toast.success("Message for Wes copied. Read it, change it, and send it yourself."); } catch { toast.error("Could not copy."); } };
  const tabs: [string, string][] = [...Object.entries(d.owners), ["all", "Everyone"]];

  return (
    <div className="px-6 lg:px-10 py-7 max-w-5xl mx-auto">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">Follow-ups</h1>
          <p className="text-[13px] text-muted mt-0.5">Who has to do what on the {d.properties} properties under construction. Worked out from Buildertrend and the money sheet each time — an item leaves by itself when it is done.</p>
        </div>
        {d.message_for_wes && <button type="button" onClick={copyWes} className="h-8 px-3 rounded-lg border border-line bg-elev text-[12.5px] font-medium inline-flex items-center gap-1.5 hover:border-line-strong"><Copy size={13} /> Copy message for Wes</button>}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="inline-flex flex-wrap rounded-lg bg-sunken p-0.5 border border-line">
          {tabs.map(([k, label]) => {
            const c = k === "all" ? { open: open.length, high: open.filter((i) => i.level === "high").length } : d.counts[k] || { open: 0, high: 0 };
            return (
              <button key={k} type="button" onClick={() => setWho(k)} className={cn("h-7 px-2.5 rounded-md text-[12px] font-medium inline-flex items-center gap-1.5", owner === k ? "bg-elev text-fg shadow-[var(--shadow-sm)]" : "text-muted")}>
                {label}{k === d.me && " (you)"} <span className={cn("tnum text-[11px]", c.high ? "text-critical" : "text-faint")}>{c.open}</span>
              </button>
            );
          })}
        </div>
        <button type="button" onClick={() => setAside((v) => !v)} className={cn("h-7 px-2.5 rounded-lg border text-[12px]", aside ? "border-accent/40 text-accent bg-accent-soft" : "border-line text-muted")}>
          {aside ? "Showing: put aside" : "Show put aside"}
        </button>
      </div>

      {groups.length === 0 ? (
        <Card className="mt-4 px-5 py-8 text-center text-[13px] text-muted">{aside ? "Nothing has been put aside." : "Nothing to follow up here."}</Card>
      ) : groups.map((g) => (
        <Card key={g.pid} className="mt-3 overflow-hidden">
          <div className="px-4 py-1.5 flex items-center gap-2 bg-sunken/40 border-b border-line">
            <Link href={`/property/${g.pid}`} className="text-[13px] font-semibold hover:text-accent">{g.name}</Link>
            <span className="text-[11.5px] text-muted">{g.items.length}</span>
          </div>
          <ul className="divide-y divide-line/70">
            {g.items.map((i) => (
              <li key={i.key} className="px-4 py-2 flex items-start gap-3">
                <span className={cn("mt-1.5 h-2 w-2 rounded-full shrink-0", i.level === "high" ? "bg-critical" : "bg-line-strong")} title={i.level === "high" ? "Late or urgent" : undefined} />
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium leading-snug">{i.title}</div>
                  {i.detail && <div className="text-[12px] text-muted leading-snug mt-0.5">{i.detail}</div>}
                  {i.mark && <div className="text-[11.5px] text-faint mt-0.5">Put aside ({i.mark.state === "done" ? "done" : "for 3 days"}) by {i.mark.by_name} {ago(i.mark.at)}</div>}
                </div>
                <div className="shrink-0 flex items-center gap-2">
                  {i.days != null && i.days > 0 && <span className={cn("text-[11.5px] tnum", i.level === "high" ? "text-critical font-semibold" : "text-muted")}>{i.days}d</span>}
                  <span className={cn("h-5 px-1.5 rounded text-[10.5px] font-semibold inline-flex items-center", OWNER_TONE[i.owner])}>{i.owner_name.replace(" (contractor)", "")}</span>
                  <Link href={href(i)} className="text-[12px] text-accent hover:underline">Open</Link>
                  {i.mark
                    ? <button type="button" onClick={() => mark(i, "open")} className="text-[12px] text-muted hover:text-fg">Bring back</button>
                    : <>
                        <button type="button" onClick={() => mark(i, "later")} title="Put aside for 3 days" className="h-6 w-6 grid place-items-center rounded border border-line text-muted hover:text-fg"><Clock size={12} /></button>
                        <button type="button" onClick={() => mark(i, "done")} title="Done — put aside" className="h-6 w-6 grid place-items-center rounded border border-line text-muted hover:text-good hover:border-good/40"><Check size={12} /></button>
                      </>}
                </div>
              </li>
            ))}
          </ul>
        </Card>
      ))}
      <p className="mt-4 text-[11.5px] text-muted">Nothing on this page is sent to anyone. The message for Wes is copied for Rakesh Sir to read and send himself.</p>
    </div>
  );
}
