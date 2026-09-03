"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, Input, Skeleton } from "@/components/ui";
import { fmtDate, propertyLabel, initials } from "@/lib/utils";

export default function PeoplePage() {
  const [q, setQ] = React.useState("");
  const p = useQuery({ queryKey: ["people", "all", q], queryFn: () => api.get<any[]>(`/people?q=${encodeURIComponent(q)}`) });
  const groups: Record<string, any[]> = (p.data || []).reduce((acc: Record<string, any[]>, x: any) => { (acc[x.org] ||= []).push(x); return acc; }, {});
  return (
    <div className="p-6 max-w-[1200px] mx-auto space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">People</h1><p className="text-sm text-muted mt-1">Everyone in the records — borrowers, counsel, title, contractors — with their history across every property.</p></div>
        <div className="relative"><Search size={14} className="absolute left-3 top-2.5 text-faint" /><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, role, company, email…" className="pl-9 w-72" /></div>
      </div>
      {p.isLoading && <Skeleton className="h-64" />}
      {Object.entries(groups).map(([org, list]) => (
        <div key={org}>
          <div className="text-[11px] uppercase tracking-wide text-faint mb-2">{org} · {list.length}</div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {list.map((x) => (
              <Link key={x.person_id} href={`/people/${x.person_id}`}>
                <Card className="p-3.5 hover:border-line-strong transition h-full">
                  <div className="flex items-center gap-3"><div className="h-9 w-9 rounded-full bg-accent-soft text-accent grid place-items-center text-xs font-semibold shrink-0">{initials(x.display_name)}</div>
                    <div className="min-w-0"><div className="text-[13px] font-semibold truncate">{x.display_name}</div><div className="text-[11px] text-faint truncate">{x.role}</div></div></div>
                  <div className="mt-2.5 flex flex-wrap gap-1">{(x.properties || []).slice(0, 4).map((pid: string) => <Badge key={pid}>{propertyLabel(pid)}</Badge>)}{(x.properties || []).length > 4 && <Badge>+{x.properties.length - 4}</Badge>}</div>
                  <div className="mt-2 text-[11px] text-faint flex justify-between"><span>{x.mentions} mentions</span>{x.last_seen && <span>last {fmtDate(x.last_seen, "MMM yy")}</span>}</div>
                </Card>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
