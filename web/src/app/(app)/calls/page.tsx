"use client";

/* Calls — what was said on each call, property by property, with the speaker's
   own words and the time (US Eastern). The calls come from Rakesh Sir's Granola
   by themselves; nobody uploads anything. */

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Select, Skeleton } from "@/components/ui";
import { CallsList } from "@/components/brain/calls";

function Calls() {
  const sp = useSearchParams();
  const [pid, setPid] = React.useState(sp.get("property") || "");
  const props = useQuery({ queryKey: ["properties"], queryFn: () => api.get<{ property_id: string; address: string }[]>("/properties"), staleTime: 5 * 60_000 });
  return (
    <div className="px-6 lg:px-10 py-8 max-w-4xl mx-auto">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight">Calls</h1>
          <p className="text-[14px] text-muted mt-1">What was said on each call, property by property, in the speakers&rsquo; own words. Times are US Eastern.</p>
        </div>
        <Select value={pid} onChange={(e) => setPid(e.target.value)} className="w-64">
          <option value="">Every property</option>
          <option value="_general">General &amp; portfolio</option>
          {(props.data || []).map((p) => <option key={p.property_id} value={p.property_id}>{p.address}</option>)}
        </Select>
      </div>
      <div className="mt-6"><CallsList pid={pid || undefined} open={sp.get("open")} /></div>
    </div>
  );
}

export default function CallsPage() {
  return <React.Suspense fallback={<div className="p-8"><Skeleton className="h-40" /></div>}><Calls /></React.Suspense>;
}
