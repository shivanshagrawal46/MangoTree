"use client";

/* Calls — what was said on each call, property by property, with the speaker's
   own words and the time (US Eastern). The calls come from Rakesh Sir's Granola
   by themselves; nobody uploads anything. */

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Phone } from "lucide-react";
import { api } from "@/lib/api";
import { Select, Skeleton } from "@/components/ui";
import { PageBody, PageHead } from "@/components/ds";
import { CallsList } from "@/components/brain/calls";

function Calls() {
  const sp = useSearchParams();
  const [pid, setPid] = React.useState(sp.get("property") || "");
  const props = useQuery({ queryKey: ["properties"], queryFn: () => api.get<{ property_id: string; address: string }[]>("/properties"), staleTime: 5 * 60_000 });
  return (
    <div>
      <PageHead icon={<Phone size={16} strokeWidth={1.6} />} crumbs={[{ label: "Calls" }]}>
        <span className="hidden lg:inline text-[12px] text-faint">In the speakers&rsquo; own words · times are US Eastern</span>
        <Select value={pid} onChange={(e) => setPid(e.target.value)} className="w-64">
          <option value="">Every property</option>
          <option value="_general">General &amp; portfolio</option>
          {(props.data || []).map((p) => <option key={p.property_id} value={p.property_id}>{p.address}</option>)}
        </Select>
      </PageHead>
      <PageBody><div className="px-6 pt-4 pb-8 max-w-4xl"><CallsList pid={pid || undefined} open={sp.get("open")} /></div></PageBody>
    </div>
  );
}

export default function CallsPage() {
  return <React.Suspense fallback={<div className="p-8"><Skeleton className="h-40" /></div>}><Calls /></React.Suspense>;
}
