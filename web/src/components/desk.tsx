"use client";

/**
 * "My desk" — the whole home page for JP Sir and Manjunath Sir. Three things,
 * nothing else: today's next steps for them (their sheet), what they still owe
 * a reply on, and their open tasks. No portfolio, no ledger, no cards.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckSquare, ClipboardList, Mail, MailCheck, MessageSquare, ArrowRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { Badge, Button, Card, CardHeader, Skeleton } from "@/components/ui";
import { DownloadButtons, PERSON_LABEL, SheetBody, SheetHeader } from "@/components/nextsteps";
import { FollowupList } from "@/components/followups";
import { TaskBoard } from "@/components/tasks";
import { ago, fmtDate } from "@/lib/utils";
import type { Desk as DeskT, SheetSection } from "@/lib/types";

const OWNER: Record<string, string> = { rakesh: "Rakesh", jp: "JP", manjunath: "Manjunath" };

export function Desk() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["desk"], queryFn: () => api.get<DeskT>("/desk"), refetchInterval: 120_000 });
  const router = useRouter();
  const [ask, setAsk] = React.useState("");
  const done = useMutation({
    mutationFn: (v: { run_id: string; property_id: string; index: number; done: boolean; person: string }) => api.post(`/next-steps/${v.run_id}/done`, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["desk"] }),
  });
  if (q.isLoading || !q.data) return <div className="p-6 space-y-4"><Skeleton className="h-24 rounded-2xl" /><Skeleton className="h-64" /></div>;
  const d = q.data;
  const person = d.person!;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  // Sheet sections from the flat step list.
  const sections: SheetSection[] = [];
  for (const s of d.steps) {
    let sec = sections.find((x) => x.property_id === s.property_id);
    if (!sec) { sec = { property_id: s.property_id!, address: s.address || s.property_id!, headline: "", steps: [], others: {} }; sections.push(sec); }
    sec.steps.push(s);
  }
  const open = d.steps.filter((s) => !s.done).length;
  const late = d.followups.filter((f) => new Date(f.due).getTime() < Date.now()).length;
  return (
    <div className="p-6 max-w-[1000px] mx-auto space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[26px] font-semibold tracking-tight">{greeting}, {d.user.name}.</h1>
          <p className="text-sm text-muted mt-1">
            {open ? <><b className="text-fg tnum">{open}</b> next step{open > 1 ? "s" : ""} for you today.</> : "No next steps for you today."}
            {d.followups.length ? <> <b className={late ? "text-critical tnum" : "text-fg tnum"}>{d.followups.length}</b> repl{d.followups.length > 1 ? "ies" : "y"} awaited from you{late ? `, ${late} overdue` : ""}.</> : " Nothing awaiting your reply."}
          </p>
        </div>
        <form onSubmit={(e) => { e.preventDefault(); if (ask.trim()) router.push(`/ask?q=${encodeURIComponent(ask.trim())}`); }} className="flex items-center gap-2 w-full md:w-[440px]">
          <div className="relative flex-1"><MessageSquare size={14} className="absolute left-3 top-3 text-faint" /><input value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="Ask about any property…" className="h-10 w-full rounded-xl border border-line bg-elev pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent/30 shadow-[var(--shadow-sm)]" /></div>
          <Button type="submit" variant="primary">Ask <ArrowRight size={14} /></Button>
        </form>
      </div>

      {/* 1. Next steps sheet */}
      <div className="bg-elev border border-line rounded-2xl px-8 py-7 shadow-[var(--shadow-sm)]">
        {d.run ? (
          <>
            <SheetHeader title={`Next steps — ${PERSON_LABEL[person]}`} subtitle={d.subtitle || undefined}
              right={<div className="flex flex-col items-end gap-1.5"><DownloadButtons runId={d.run.run_id} person={person} />
                {d.sheet_mail && <span className="text-[11px] text-muted inline-flex items-center gap-1">{d.sheet_mail.status === "replied" ? <><MailCheck size={11} className="text-good" /> acknowledged</> : d.sheet_mail.sent_at ? <><Mail size={11} /> emailed to you {ago(d.sheet_mail.sent_at)} — please reply to it</> : null}</span>}</div>} />
            <SheetBody sheet={{ sections, run_id: d.run.run_id }} person={person} onDone={(pid, index, dn) => done.mutate({ run_id: d.run!.run_id, property_id: pid, index, done: dn, person })} />
          </>
        ) : <div className="serif italic text-[#6B6B76] py-4">Today’s sheet has not been prepared yet.</div>}
      </div>

      {/* 2. Follow-ups */}
      <Card>
        <CardHeader title={<span className="inline-flex items-center gap-2"><ClipboardList size={14} className="text-accent" /> Awaiting your reply</span>} sub="Each of these stays here until you answer in the email thread or tick it done." />
        <div className="px-5 pb-5"><FollowupList owner={person} /></div>
      </Card>

      {/* 3. Tasks */}
      <Card>
        <CardHeader title={<span className="inline-flex items-center gap-2"><CheckSquare size={14} className="text-accent" /> Your tasks</span>} sub="Open tasks assigned to you. Tick to close." />
        <div className="px-5 pb-5"><TaskBoard ownerFilter={OWNER[d.user.user_id]} statusFilter="open" groupBy="property" compact showAdd /></div>
      </Card>
    </div>
  );
}
