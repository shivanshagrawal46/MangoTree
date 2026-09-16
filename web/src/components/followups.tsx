"use client";

/**
 * Follow-ups — what is owed, by whom, since when, and what the system has done
 * about it. Strong by design: overdue is loud, escalated is louder, and every
 * row says exactly who has not replied to what.
 */
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, ArrowUpRight, BellRing, Check, Clock, Copy, Mail, Send, X } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Card, CardHeader, Dialog, DialogContent, Empty, Textarea } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { useUser } from "@/components/providers";
import { cn, fmtDate, ago, propertyLabel } from "@/lib/utils";
import type { Followup, FollowupsResponse } from "@/lib/types";

const OWNER: Record<string, string> = { rakesh: "Rakesh Sir", jp: "JP Sir", manjunath: "Manjunath Sir" };
const TOPIC: Record<string, string> = { payment: "Payment", invoice: "Invoice", permit: "Permit", inspection: "Inspection", insurance: "Insurance", documents: "Documents", decision: "Decision", other: "Follow-up" };

function overdue(f: Followup) { return new Date(f.due).getTime() < Date.now(); }
function daysSince(v: string) { return Math.max(0, Math.floor((Date.now() - new Date(v).getTime()) / 86_400_000)); }

export function FollowupRow({ f, onChange, ceo }: { f: Followup; onChange: () => void; ceo: boolean }) {
  const { open } = useEvidence();
  const qc = useQueryClient();
  const [draft, setDraft] = React.useState<null | { subject: string; body: string }>(null);
  const status = useMutation({ mutationFn: (s: string) => api.post(`/followups/${f.followup_id}/status`, { status: s }), onSuccess: () => { onChange(); qc.invalidateQueries({ queryKey: ["desk"] }); } });
  const sendExt = useMutation({ mutationFn: (v: { subject: string; body: string }) => api.post(`/followups/${f.followup_id}/send-reminder`, v), onSuccess: () => { setDraft(null); onChange(); qc.invalidateQueries({ queryKey: ["outbox"] }); } });
  const late = overdue(f);
  const esc = f.status === "escalated";
  const who = f.kind === "ask_internal" ? OWNER[f.owner] : f.kind === "report_ack" ? OWNER[f.owner] : f.counterparty?.name;
  const owes = f.kind === "ask_internal" ? `a reply to ${f.counterparty?.name || "the counterparty"}` : f.kind === "report_ack" ? "an acknowledgement of the sheet" : "a reply to RKB";
  const reminders = f.reminders?.length || 0;
  return (
    <div className={cn("rounded-xl border p-3.5 bg-elev transition", esc ? "border-critical/40 bg-critical-soft/40" : late ? "border-high/40" : "border-line")}>
      <div className="flex items-start gap-3">
        <div className={cn("mt-0.5 h-8 w-8 rounded-lg grid place-items-center shrink-0", esc ? "bg-critical text-white" : late ? "bg-high-soft text-high" : "bg-sunken text-muted")}>
          {esc ? <AlertOctagon size={15} /> : late ? <BellRing size={15} /> : <Clock size={15} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[13.5px] font-semibold">{who}</span>
            <span className="text-xs text-muted">owes {owes}</span>
            <Badge tone={esc ? "critical" : late ? "high" : "neutral"}>{esc ? `Escalated · ${daysSince(f.asked_at)} days` : late ? `Overdue · was due ${fmtDate(f.due, "d MMM")}` : `Due ${fmtDate(f.due, "d MMM")}`}</Badge>
            <Badge tone="info">{TOPIC[f.topic] || f.topic}</Badge>
            {reminders > 0 && <Badge tone="accent"><BellRing size={10} /> {reminders} reminder{reminders > 1 ? "s" : ""}</Badge>}
          </div>
          <div className="text-[13px] mt-1.5 leading-relaxed">{f.what}</div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted mt-1.5">
            {f.property_ids?.length ? <span>{f.property_ids.map((p, i) => <React.Fragment key={p}>{i > 0 && ", "}<Link href={`/property/${p}`} className="hover:text-accent">{propertyLabel(p)}</Link></React.Fragment>)}</span> : <span>Portfolio</span>}
            <span>asked {ago(f.asked_at)}</span>
            {f.subject && <span className="truncate max-w-[360px]" title={f.subject}>“{f.subject}”</span>}
            {f.source_sha && <button onClick={() => open({ sha: f.source_sha! })} className="inline-flex items-center gap-0.5 hover:text-accent">open email <ArrowUpRight size={11} /></button>}
            {f.last_reminder_at && <span>last reminder {ago(f.last_reminder_at)}</span>}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {f.kind === "ask_external" && ceo && (
            <Button size="sm" variant={late ? "primary" : "secondary"} onClick={() => setDraft(f.draft ? { subject: f.draft.subject, body: f.draft.body } : { subject: `Re: ${f.subject || ""}`, body: "" })} title="Send a kind reminder from rakesh@mtreh.com"><Send size={12} /> Remind {f.counterparty?.name?.split(" ")[0]}</Button>
          )}
          <Button size="sm" variant="soft" onClick={() => status.mutate("done")} title="They replied / it is handled"><Check size={13} /> Done</Button>
          <Button size="icon" variant="ghost" onClick={() => status.mutate("dismissed")} title="Not a real follow-up"><X size={14} /></Button>
        </div>
      </div>
      <Dialog open={!!draft} onOpenChange={(o) => !o && setDraft(null)}>
        <DialogContent title={`Reminder to ${f.counterparty?.name}`} description={`Sent from rakesh@mtreh.com to ${f.counterparty?.email || "—"}. Edit freely; the tone stays kind.`} wide>
          {draft && (
            <div className="space-y-3">
              <input value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} className="h-9 w-full rounded-xl border border-line bg-elev px-3 text-sm" />
              <Textarea value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} className="min-h-[260px] text-[13px] leading-relaxed" />
              <div className="flex items-center justify-between">
                <Button size="sm" variant="ghost" onClick={() => navigator.clipboard?.writeText(`${draft.subject}\n\n${draft.body}`)}><Copy size={12} /> Copy instead</Button>
                <div className="flex gap-2"><Button variant="secondary" onClick={() => setDraft(null)}>Cancel</Button><Button variant="primary" disabled={sendExt.isPending || !f.counterparty?.email} onClick={() => sendExt.mutate(draft)}><Mail size={13} /> Send now</Button></div>
              </div>
              {sendExt.error && <div className="text-xs text-critical">{(sendExt.error as any).message}</div>}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function FollowupList({ owner, compact, limit }: { owner?: string; compact?: boolean; limit?: number }) {
  const { user } = useUser();
  const ceo = user?.role === "ceo";
  const q = useQuery({ queryKey: ["followups", owner || "all"], queryFn: () => api.get<FollowupsResponse>(`/followups${owner ? `?owner=${owner}` : ""}`), refetchInterval: 120_000 });
  const items = q.data?.items || [];
  const sorted = [...items].sort((a, b) => (a.status === "escalated" ? -1 : 0) - (b.status === "escalated" ? -1 : 0) || new Date(a.due).getTime() - new Date(b.due).getTime());
  const shown = limit ? sorted.slice(0, limit) : sorted;
  if (q.isLoading) return <div className="text-sm text-muted py-4">Loading follow-ups…</div>;
  if (!items.length) return <Empty title="Nothing awaiting a reply" sub={`Tracked from ${fmtDate(q.data?.since || null, "d MMMM yyyy")}: every ask in every email, until the person replies.`} />;
  if (compact || owner) {
    return <div className="space-y-2">{shown.map((f) => <FollowupRow key={f.followup_id} f={f} onChange={() => q.refetch()} ceo={ceo} />)}{limit && items.length > limit && <Link href="/followups" className="text-xs text-accent hover:underline">All {items.length} follow-ups →</Link>}</div>;
  }
  // CEO view: grouped by who owes the move.
  const groups: { key: string; label: string; items: Followup[] }[] = [];
  const push = (key: string, label: string, f: Followup) => { let g = groups.find((x) => x.key === key); if (!g) { g = { key, label, items: [] }; groups.push(g); } g.items.push(f); };
  for (const f of sorted) {
    if (f.kind === "ask_external") push(`ext:${f.counterparty?.email || f.counterparty?.name}`, `${f.counterparty?.name || "Counterparty"} owes RKB`, f);
    else push(`int:${f.owner}`, `${OWNER[f.owner] || f.owner} owes`, f);
  }
  groups.sort((a, b) => b.items.filter((x) => x.status === "escalated" || overdue(x)).length - a.items.filter((x) => x.status === "escalated" || overdue(x)).length);
  return (
    <div className="space-y-6">
      {groups.map((g) => (
        <section key={g.key}>
          <div className="flex items-center gap-2 mb-2"><h3 className="text-[13px] font-semibold">{g.label}</h3><span className="text-xs text-muted tnum">{g.items.length}</span>{g.items.some((x) => x.status === "escalated") && <Badge tone="critical">escalated</Badge>}</div>
          <div className="space-y-2">{g.items.map((f) => <FollowupRow key={f.followup_id} f={f} onChange={() => q.refetch()} ceo={ceo} />)}</div>
        </section>
      ))}
    </div>
  );
}

export function FollowupRules() {
  const q = useQuery({ queryKey: ["followups", "all"], queryFn: () => api.get<FollowupsResponse>("/followups") });
  const r = q.data?.rules;
  if (!r) return null;
  return (
    <Card className="p-4 text-[12.5px] leading-relaxed text-muted">
      <div className="text-[13px] font-semibold text-fg mb-1.5">The standard procedure</div>
      <ul className="space-y-1 list-disc pl-4">
        <li>Every email from {fmtDate(q.data?.since || null, "d MMMM yyyy")} on is read once, in the morning cycle, property by property. Each ask becomes a follow-up that stays open until the person replies in that thread — or someone here marks it done.</li>
        <li>Money asks go to JP Sir; invoices, permits, inspections, insurance and contractor papers to Manjunath Sir; decisions to Rakesh Sir. Our people get <b className="text-fg">{r.internal_due_business_days} business day</b>; then one kind email a day from rakesh@mtreh.com, as the morning cycle completes (early afternoon in India), with the replies most needed and any next step still open — inside their next-steps email when a sheet goes that day.</li>
        <li>Wes, Kelly and other counterparties get <b className="text-fg">{r.external_due_business_days} business day{r.external_due_business_days > 1 ? "s" : ""}</b>; then a reminder is drafted for one click. After <b className="text-fg">{r.escalate_after_business_days}</b> it is escalated here in red.</li>
        <li>The next-steps sheets emailed to JP Sir and Manjunath Sir are chased the same way until acknowledged. Only internal replies to system emails are read; all other internal mail is left alone.</li>
      </ul>
      {q.data?.last_tick && <div className="mt-2 text-[11px] text-faint">Last check {ago(q.data.last_tick)}.</div>}
    </Card>
  );
}
