"use client";

/* To reply — the emails waiting for an answer, and nothing else on the page. */

import { useQuery } from "@tanstack/react-query";
import { Reply } from "lucide-react";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui";
import { PageBody, PageHead, View, Views } from "@/components/ds";
import { ReplyList, replyState } from "@/components/brain/replies";
import type { Draft } from "@/lib/brain";

export default function RepliesPage() {
  const q = useQuery({ queryKey: ["brain", "drafts", "ready"], queryFn: () => api.get<Draft[]>("/brain/drafts?status=ready"), refetchInterval: 120_000 });
  const rows = q.data || [];
  const ready = rows.filter((d) => replyState(d).rank === 2).length;
  const rewrite = rows.filter((d) => replyState(d).rank === 0).length;
  return (
    <div>
      <PageHead icon={<Reply size={16} strokeWidth={1.6} />} crumbs={[{ label: "To reply" }]}>
        <span className="text-[12px] text-faint">A reply sent from Outlook closes its line by itself</span>
      </PageHead>
      <PageBody>
      {!q.isLoading && rows.length > 0 && (
        <Views>
          <View active count={rows.length}>Waiting</View>
          <span className="flex-1" />
          <span className="pr-2 text-[12px] text-faint tnum">{ready} ready to send · {rows.length - ready - rewrite} with points to check{rewrite > 0 && <> · <span className="text-critical">{rewrite} to rewrite</span></>}</span>
        </Views>
      )}
      {q.isLoading ? <div className="p-4 space-y-2"><Skeleton className="h-6" /><Skeleton className="h-6" /><Skeleton className="h-6" /></div> : <ReplyList drafts={rows} />}
      </PageBody>
    </div>
  );
}
