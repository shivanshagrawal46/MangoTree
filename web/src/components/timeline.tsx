"use client";

/* The unified timeline — every dated, quote-verified event; filter by type;
   the as-of slider replays the property as it was known on any past date;
   every row opens its source. */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Search, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Input, Slider, Skeleton, Empty } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { cn, EVENT_COLORS, fmtDate, money } from "@/lib/utils";
import type { TimelineEvent } from "@/lib/types";

const TYPES = ["origination", "assignment", "funding", "payment", "payoff", "extension", "default", "legal", "construction", "listing_sale", "title", "tax_insurance", "communication", "other"];

export function Timeline({ propertyId, compact }: { propertyId: string; compact?: boolean }) {
  const { open } = useEvidence();
  const [types, setTypes] = React.useState<string[]>([]);
  const [q, setQ] = React.useState("");
  const [asOfPct, setAsOfPct] = React.useState(100);
  const all = useQuery({ queryKey: ["timeline", propertyId, types.join(","), q], queryFn: () => api.get<TimelineEvent[]>(`/properties/${propertyId}/timeline?types=${types.join(",")}&q=${encodeURIComponent(q)}`) });

  const dated = (all.data || []).filter((e) => e.occurred_at);
  const times = dated.map((e) => new Date(e.occurred_at!).getTime());
  const minT = times.length ? Math.min(...times) : 0;
  const maxT = times.length ? Math.max(...times) : 0;
  const asOf = minT + ((maxT - minT) * asOfPct) / 100;
  const visible = asOfPct >= 100 ? all.data || [] : (all.data || []).filter((e) => !e.occurred_at || new Date(e.occurred_at).getTime() <= asOf);
  const byMonth = visible.reduce((acc, e) => { const k = e.occurred_at ? fmtDate(e.occurred_at, "MMMM yyyy") : "Undated"; (acc[k] ||= []).push(e); return acc; }, {} as Record<string, TimelineEvent[]>);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative"><Search size={13} className="absolute left-2.5 top-2.5 text-faint" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search events…" className="pl-8 w-56 h-8 text-xs" /></div>
        <div className="flex flex-wrap gap-1">
          {TYPES.map((t) => (
            <button key={t} onClick={() => setTypes((cur) => cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t])}
              className={cn("h-6 px-2 rounded-full text-[11px] border transition flex items-center gap-1", types.includes(t) ? "bg-fg text-bg border-fg" : "border-line text-muted hover:border-line-strong")}>
              <span className={cn("h-1.5 w-1.5 rounded-full", EVENT_COLORS[t])} />{t.replace("_", " ")}
            </button>
          ))}
          {types.length > 0 && <button onClick={() => setTypes([])} className="h-6 px-2 text-[11px] text-faint hover:text-fg flex items-center gap-1"><RotateCcw size={11} /> clear</button>}
        </div>
      </div>
      {times.length > 1 && (
        <div className="rounded-xl border border-line bg-elev px-4 py-2.5 flex items-center gap-4">
          <div className="text-[11px] uppercase tracking-wide text-faint shrink-0">As of</div>
          <Slider value={[asOfPct]} onValueChange={([v]) => setAsOfPct(v)} min={0} max={100} step={0.5} className="flex-1" />
          <div className={cn("text-xs tnum w-28 text-right font-medium", asOfPct < 100 ? "text-accent" : "text-muted")}>{asOfPct >= 100 ? "Today" : fmtDate(new Date(asOf).toISOString())}</div>
          <div className="text-[11px] text-faint w-24 text-right">{visible.length} of {(all.data || []).length}</div>
        </div>
      )}
      {all.isLoading && <div className="space-y-2">{[...Array(6)].map((_, i) => <Skeleton key={i} className="h-12" />)}</div>}
      {!all.isLoading && visible.length === 0 && <Empty title="No events match." />}
      <div className="relative">
        {Object.entries(byMonth).map(([month, list]) => (
          <div key={month} className="mb-4">
            <div className="sticky top-0 z-10 bg-bg/90 backdrop-blur py-1 text-[11px] font-semibold uppercase tracking-wide text-faint">{month} <span className="font-normal">· {list.length}</span></div>
            <ul className="mt-1 border-l border-line ml-1.5">
              {list.map((e, i) => (
                <motion.li key={e.event_id} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i, 12) * 0.02 }}
                  className="relative pl-5 py-1.5 group cursor-pointer" onClick={() => e.source_sha && open({ sha: e.source_sha, highlight: e.quote?.slice(0, 80) })}>
                  <span className={cn("absolute -left-[5px] top-3 h-2.5 w-2.5 rounded-full ring-4 ring-bg", EVENT_COLORS[e.event_type] || "bg-faint")} />
                  <div className="rounded-xl border border-transparent group-hover:border-line group-hover:bg-elev px-3 py-2 transition">
                    <div className="flex items-start gap-2">
                      <span className="text-[11px] tnum text-faint w-[4.6rem] shrink-0 pt-0.5">{fmtDate(e.occurred_at, "MMM d, yyyy")}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-medium leading-snug">{e.title}</div>
                        {!compact && e.quote && <div className="text-xs text-muted italic mt-0.5 line-clamp-2 border-l-2 border-line pl-2">“{e.quote}”</div>}
                        <div className="text-[11px] text-faint mt-0.5 flex flex-wrap items-center gap-x-2">
                          <Badge>{e.event_type.replace("_", " ")}</Badge>
                          {typeof e.amount === "number" && <span className="tnum font-medium text-fg">{money(e.amount)}</span>}
                          {e.source_name && <span className="truncate max-w-[280px]">{e.source_name}</span>}
                          {e.extracted_by && e.extracted_by !== "deterministic" && <span>· AI, quote-verified</span>}
                          {e.date_basis && e.date_basis !== "stated_in_text" && <span>· {e.date_basis.replace("_", " ")}</span>}
                        </div>
                      </div>
                    </div>
                  </div>
                </motion.li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
