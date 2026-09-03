"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Sparkles, FileDown } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Button, Card, CardHeader, Stat } from "@/components/ui";
import { TaskBoard } from "@/components/tasks";

export default function TasksPage() {
  const sp = useSearchParams();
  const qc = useQueryClient();
  const counts = useQuery({ queryKey: ["tasks", "counts"], queryFn: () => api.get<any>("/tasks?status=open,suggested,done") });
  const [busy, setBusy] = React.useState(false);
  const by = counts.data?.counts?.by_owner || {};
  const extract = async () => {
    setBusy(true);
    const { job_id } = await api.post<{ job_id: string }>("/tasks/extract");
    toast.message("Opus 5 is reading every property's recent records for tasks…");
    subscribeJob(job_id, () => {}, () => { setBusy(false); qc.invalidateQueries({ queryKey: ["tasks"] }); toast.success("Task extraction finished"); });
  };
  return (
    <div className="p-6 max-w-[1200px] mx-auto space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">Tasks</h1><p className="text-sm text-muted mt-1">Everyone's to-do list. Ticking saves it as done with your name. AI suggestions carry their evidence.</p></div>
        <div className="flex items-center gap-2">
          <a href="/api/export/tasks.xlsx"><Button variant="ghost"><FileDown size={14} /> Excel</Button></a>
          <Button variant="soft" onClick={extract} disabled={busy}><Sparkles size={14} /> {busy ? "Extracting…" : "Refresh AI tasks from records"}</Button>
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {["Rakesh", "JP", "Manjunath", "Wes"].map((o) => (
          <Card key={o} className="p-4"><Stat label={o} value={by[o]?.open || 0} sub={`${by[o]?.suggested || 0} suggested · ${by[o]?.done || 0} done`} /></Card>
        ))}
      </div>
      <TaskBoard propertyId={sp.get("property") || undefined} statusFilter={sp.get("status") || undefined} ownerFilter={sp.get("owner") || undefined} groupBy="owner" />
    </div>
  );
}
