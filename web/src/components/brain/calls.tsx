"use client";

/* The calls that were read — what each said about each property, with the
   speaker's own words and the time (US Eastern). Used by the Calls page (every
   property) and by the Calls tab of a property (that property only). */

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Phone, Quote } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, Empty, Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { CallNote, CallSection } from "@/lib/brain";

const POINT: Record<string, { label: string; tone: "neutral" | "accent" | "critical" | "high" | "good" | "info" }> = {
  decision: { label: "Decided", tone: "good" }, promise: { label: "Promised", tone: "accent" }, number: { label: "Figure", tone: "info" },
  instruction: { label: "Instruction", tone: "accent" }, open_question: { label: "Still open", tone: "high" }, concern: { label: "Concern", tone: "critical" },
  update: { label: "Update", tone: "neutral" },
};

function changes(a?: CallNote["applied"][string]) {
  if (!a) return "";
  const bits = [
    a.items_closed ? `${a.items_closed} to-do${a.items_closed === 1 ? "" : "s"} closed` : "",
    a.items_added ? `${a.items_added} added` : "",
    a.items_updated ? `${a.items_updated} changed` : "",
    a.drafts_outdated ? `${a.drafts_outdated} waiting repl${a.drafts_outdated === 1 ? "y" : "ies"} marked for a second look` : "",
    (a.changed || []).includes("facts") ? "facts updated" : "",
  ].filter(Boolean);
  return bits.join(" · ");
}

function Section({ s, applied }: { s: CallSection; applied?: CallNote["applied"][string] }) {
  const done = changes(applied);
  return (
    <div className="px-5 py-4 border-t border-line">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link href={`/property/${s.property_id}`} className="text-[13.5px] font-semibold tracking-tight hover:text-accent">{s.name}</Link>
        <span className="text-[12px] text-muted">{s.topic}</span>
      </div>
      <p className="mt-1.5 text-[13px] leading-relaxed">{s.summary}</p>
      {done && <div className="mt-1.5 text-[11.5px] text-good">In the file: {done}</div>}
      <ul className="mt-3 space-y-3">
        {s.points.map((p, i) => {
          const k = POINT[p.kind] || POINT.update;
          return (
            <li key={i}>
              <div className="flex items-start gap-2">
                <Badge tone={k.tone} className="mt-0.5 shrink-0">{k.label}</Badge>
                <div className="text-[13px] leading-snug">{p.text}</div>
              </div>
              {p.quote && (
                <blockquote className="mt-1.5 ml-1 pl-3 border-l-2 border-accent/40 text-[12.5px] text-muted leading-relaxed">
                  <Quote size={11} className="inline -mt-1 mr-1 text-accent/70" />{p.quote}
                  <div className="mt-0.5 text-[11px] text-faint not-italic">{p.by}{p.at ? ` · ${p.at} ET` : ""}</div>
                </blockquote>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function CallCard({ c, start }: { c: CallNote; start: boolean }) {
  const [open, setOpen] = React.useState(start);
  const ref = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => { if (start) ref.current?.scrollIntoView({ block: "start" }); }, [start]);
  const named = c.speakers.filter((s) => s !== "Voices not separated");
  return (
    <Card className="overflow-hidden scroll-mt-6">
      <div ref={ref} />
      <button type="button" onClick={() => setOpen((v) => !v)} className="w-full text-left px-5 py-4 flex items-start gap-3 hover:bg-sunken/50">
        <div className="mt-0.5 h-8 w-8 rounded-lg bg-accent-soft text-accent grid place-items-center shrink-0"><Phone size={14} /></div>
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-semibold tracking-tight leading-snug">{c.title}</div>
          <div className="mt-0.5 text-[12px] text-muted">{c.when}{named.length ? ` · ${named.join(", ")}` : ""}{c.separated === false ? " · the recorder did not separate the voices" : ""}</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">{c.sections.map((s, i) => <Badge key={i}>{s.name}</Badge>)}</div>
          {open && c.overall && <p className="mt-2.5 text-[13px] text-muted leading-relaxed">{c.overall}</p>}
        </div>
        <ChevronDown size={15} className={cn("mt-1 text-faint transition shrink-0", !open && "-rotate-90")} />
      </button>
      {open && c.sections.map((s, i) => <Section key={i} s={s} applied={c.applied?.[s.property_id]} />)}
    </Card>
  );
}

export function CallsList({ pid, open }: { pid?: string; open?: string | null }) {
  const q = useQuery({ queryKey: ["brain", "calls", pid || ""], queryFn: () => api.get<{ calls: CallNote[] }>(`/brain/calls${pid ? `?property_id=${pid}` : ""}`), placeholderData: (prev) => prev });
  const calls = q.data?.calls || [];
  if (q.isLoading) return <div className="space-y-4"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>;
  if (!calls.length) return <Empty title={pid ? "No calls about this property yet" : "No calls yet"} sub="A call put in the RKB-ROI folder in Granola appears here a few minutes after it ends." />;
  // On a property's own tab the newest call is open, so its words are there without a click.
  return <div className="space-y-4">{calls.map((c, i) => <CallCard key={c.note_id} c={c} start={open ? open === c.note_id : !!pid && i === 0} />)}</div>;
}
