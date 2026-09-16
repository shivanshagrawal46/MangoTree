"use client";

import * as React from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, MailCheck, MailWarning, RefreshCw } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { useUser } from "@/components/providers";
import { FollowupList, FollowupRules } from "@/components/followups";
import { Badge, Button, Card, CardHeader, Empty, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui";
import { ago, fmtDate } from "@/lib/utils";
import type { OutboxItem, SendStatus } from "@/lib/types";

export default function FollowupsPage() {
  const { user } = useUser();
  const sp = useSearchParams();
  const ceo = user?.role === "ceo";
  const [tab, setTab] = React.useState(sp.get("tab") === "outbox" && ceo ? "outbox" : "followups");
  const qc = useQueryClient();
  const [running, setRunning] = React.useState(false);
  const tick = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/followups/tick"),
    onSuccess: ({ job_id }) => { setRunning(true); subscribeJob(job_id, () => {}, () => { setRunning(false); qc.invalidateQueries({ queryKey: ["followups"] }); qc.invalidateQueries({ queryKey: ["outbox"] }); }); },
  });
  return (
    <div className="p-6 max-w-[1100px] mx-auto space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight">Follow-ups</h1>
          <p className="text-sm text-muted mt-1">Who owes whom a reply — and what the system has done about it.</p>
        </div>
        {ceo && <Button size="sm" variant="secondary" disabled={running} onClick={() => tick.mutate()}>{running ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Check now</Button>}
      </div>
      {ceo ? (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList><TabsTrigger value="followups">Follow-ups</TabsTrigger><TabsTrigger value="outbox">Sent by the system</TabsTrigger><TabsTrigger value="rules">The procedure</TabsTrigger></TabsList>
          <TabsContent value="followups" className="mt-4"><FollowupList /></TabsContent>
          <TabsContent value="outbox" className="mt-4"><OutboxView /></TabsContent>
          <TabsContent value="rules" className="mt-4"><FollowupRules /></TabsContent>
        </Tabs>
      ) : <FollowupList owner={user?.user_id} />}
    </div>
  );
}

function OutboxView() {
  const q = useQuery({ queryKey: ["outbox"], queryFn: () => api.get<{ items: OutboxItem[]; send_status: SendStatus }>("/outbox"), refetchInterval: 60_000 });
  const qc = useQueryClient();
  const flush = useMutation({ mutationFn: () => api.post("/outbox/flush"), onSuccess: () => qc.invalidateQueries({ queryKey: ["outbox"] }) });
  const s = q.data?.send_status;
  const tone: Record<string, "good" | "accent" | "high" | "critical" | "neutral"> = { replied: "good", sent: "accent", queued: "neutral", needs_consent: "high", failed: "critical", superseded: "neutral" };
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          {s?.can_send ? <MailCheck size={16} className="text-good" /> : <MailWarning size={16} className="text-high" />}
          <div className="text-sm flex-1 min-w-[280px]">
            {s?.can_send ? <>Sending from <b>{s.mailbox}</b> is enabled.</> : <>Sending from <b>{s?.mailbox || "rakesh@mtreh.com"}</b> needs a one-time sign-in that adds “Send mail as you”. Until then, emails wait here and go out automatically the moment it is granted.</>}
            {!s?.can_send && s?.how_to_enable && <div className="text-xs text-muted mt-1 font-mono break-all">{s.how_to_enable}</div>}
          </div>
          <Button size="sm" variant="secondary" onClick={() => flush.mutate()} disabled={flush.isPending}>Try sending now</Button>
        </div>
      </Card>
      {!q.data?.items?.length ? <Empty title="Nothing sent yet" sub="Next-steps sheets and reminders appear here with their reply status." /> : (
        <div className="space-y-2">
          {q.data.items.map((o) => (
            <div key={o.outbox_id} className="rounded-xl border border-line bg-elev p-3.5">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={tone[o.status] || "neutral"}>{o.status.replace("_", " ")}</Badge>
                <span className="text-[13px] font-medium">{o.subject}</span>
                <span className="text-xs text-muted">to {o.to.map((t) => t.name || t.address).join(", ")}</span>
                <span className="text-xs text-faint ml-auto">{o.sent_at ? `sent ${ago(o.sent_at)}` : `queued ${ago(o.queued_at)}`}</span>
              </div>
              <div className="text-[11.5px] text-muted mt-1 flex flex-wrap gap-x-3">
                <span>{o.kind.replace(/_/g, " ")}</span>
                {o.attachments?.length ? <span>{o.attachments.map((a) => a.filename).join(" · ")}</span> : null}
                {o.replied_at && <span className="text-good">replied {ago(o.replied_at)} by {o.replied_by}</span>}
                {o.error && o.status !== "replied" && <span className="text-critical">{o.error}</span>}
              </div>
              {o.reply_preview && <div className="text-xs text-muted mt-1 italic line-clamp-2">“{o.reply_preview}”</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
