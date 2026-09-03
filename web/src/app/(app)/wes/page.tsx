"use client";

/* The Wes meeting page: today's top three per property, all on one screen, in
   the order the money risk demands. Print it, or tick as you go. */

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { HardHat, Printer } from "lucide-react";
import { api } from "@/lib/api";
import { Button, Skeleton } from "@/components/ui";
import { WesAgendaCard } from "@/components/ledger";
import { fmtDate, propertyLabel } from "@/lib/utils";
import type { WesAgendaDoc } from "@/lib/types";

export default function WesPage() {
  const q = useQuery({ queryKey: ["wes-agenda-all"], queryFn: () => api.get<{ day: string; properties: WesAgendaDoc[] }>("/wes-agenda") });
  if (!q.data) return <div className="p-6 space-y-4"><Skeleton className="h-16" /><Skeleton className="h-48" /><Skeleton className="h-48" /></div>;
  const order = { critical: 0, high: 1, normal: 2 } as Record<string, number>;
  const rows = [...q.data.properties]
    .filter((p) => (p.issues || []).length > 0)
    .sort((a, b) => Math.min(...a.issues.map((i) => order[i.urgency] ?? 3)) - Math.min(...b.issues.map((i) => order[i.urgency] ?? 3)));
  const quiet = q.data.properties.filter((p) => !(p.issues || []).length);
  const total = rows.reduce((n, p) => n + p.issues.length, 0);
  return (
    <div className="p-6 max-w-5xl space-y-5">
      <div className="flex items-start justify-between gap-4 print:hidden">
        <div>
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2"><HardHat size={18} className="text-accent" /> Wes — today's agenda</h1>
          <p className="text-sm text-muted mt-1">{fmtDate(q.data.day, "EEEE d MMMM yyyy")} · <b className="text-fg tnum">{total}</b> issues across <b className="text-fg tnum">{rows.length}</b> properties, freshest money risk first. Written each morning by Fable 5.1 from the records; every point opens its source.</p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => window.print()}><Printer size={13} /> Print</Button>
      </div>
      {rows.length === 0 && <div className="text-sm text-muted">No agenda generated yet today. It is written each morning; open a property's Wes tab and press Refresh to generate one now.</div>}
      {rows.map((p) => (
        <section key={p.property_id} className="break-inside-avoid">
          <div className="flex items-baseline justify-between mb-1.5">
            <Link href={`/property/${p.property_id}?tab=wes`} className="text-sm font-semibold hover:text-accent">{propertyLabel(p.property_id)}</Link>
            <span className="text-[11px] text-faint">{p.issues.filter((i) => i.discussed).length}/{p.issues.length} discussed</span>
          </div>
          <WesAgendaCard pid={p.property_id} showHeader={false} />
        </section>
      ))}
      {quiet.length > 0 && <div className="text-xs text-faint">Quiet today: {quiet.map((p) => propertyLabel(p.property_id)).join(", ")}.</div>}
    </div>
  );
}
