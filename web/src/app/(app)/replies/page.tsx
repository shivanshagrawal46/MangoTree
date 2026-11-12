"use client";

/* To reply — the emails waiting for an answer, and nothing else on the page. */

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Card, Skeleton } from "@/components/ui";
import { ReplyList, replyState } from "@/components/brain/replies";
import type { Draft } from "@/lib/brain";

export default function RepliesPage() {
  const q = useQuery({ queryKey: ["brain", "drafts", "ready"], queryFn: () => api.get<Draft[]>("/brain/drafts?status=ready"), refetchInterval: 120_000 });
  const rows = q.data || [];
  const ready = rows.filter((d) => replyState(d).rank === 2).length;
  const rewrite = rows.filter((d) => replyState(d).rank === 0).length;
  return (
    <div className="px-6 lg:px-10 py-8 max-w-6xl mx-auto">
      <h1 className="text-[24px] font-semibold tracking-tight">To reply</h1>
      <p className="text-[13.5px] text-muted mt-1">
        {q.isLoading ? "Checking the mail…" : rows.length === 0 ? "No email is waiting for a reply." :
          `${rows.length} email${rows.length === 1 ? "" : "s"} waiting · ${ready} ready to send · ${rows.length - ready - rewrite} with points to check${rewrite ? ` · ${rewrite} partly answered already` : ""}. A reply sent from Outlook closes its line by itself.`}
      </p>
      <Card className="mt-5 overflow-hidden">
        {q.isLoading ? <div className="p-5 space-y-3"><Skeleton className="h-9" /><Skeleton className="h-9" /><Skeleton className="h-9" /></div> : <ReplyList drafts={rows} />}
      </Card>
    </div>
  );
}
