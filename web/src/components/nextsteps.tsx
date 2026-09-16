"use client";

/**
 * Next-steps sheets — the on-screen twin of the printed sheet (14 Sept style):
 * navy serif titles, gold "PROPERTY 01" eyebrow, cream cards with a gold rule,
 * a real checkbox per step. `Sheet` renders one person's sheet; `NextStepsPanel`
 * is Rakesh's control on the dashboard (generate → confirm → progress → four
 * sheets, download, send to JP Sir / Manjunath Sir with confirmation).
 */
import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { AlertTriangle, Check, Download, FileText, Loader2, Mail, MailCheck, MailWarning, RefreshCw, Send, Sparkles } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, Checkbox, Dialog, DialogContent } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { cn, fmtDate, ago } from "@/lib/utils";
import type { NextPerson, NextStep, NextStepsLatest, OutboxItem, Sheet as SheetT, SheetSection } from "@/lib/types";

export const PERSON_LABEL: Record<NextPerson, string> = { wes: "Wes", manjunath: "Manjunath Sir", jp: "JP Sir", rakesh: "Rakesh Sir" };
export const PERSONS: NextPerson[] = ["wes", "manjunath", "jp", "rakesh"];

/* ------------------------------------------------------------- sheet */
export function StepCard({ step, n, onDone, compact }: { step: NextStep; n: number; onDone?: (done: boolean) => void; compact?: boolean }) {
  const { open } = useEvidence();
  const ev = step.evidence?.[0];
  return (
    <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.18 }}
      className={cn("relative rounded-r-xl bg-[#F7F2EA] dark:bg-[#2a2620] border-l-[3px] border-[#B0893B]", step.done && "opacity-60")}>
      <div className={cn("flex gap-4", compact ? "px-4 py-3" : "px-5 py-4")}>
        <div className="serif text-[#B0893B] font-bold text-[17px] leading-6 w-4 shrink-0">{n}</div>
        <div className="min-w-0 flex-1">
          <div className={cn("font-semibold text-[#1F3550] dark:text-[#dbe3ee] leading-snug", compact ? "text-[13px]" : "text-[14px]", step.done && "line-through")}>{step.title}</div>
          {(step.carried_days ?? 0) > 0 && !step.done && (
            <div className="text-[11.5px] font-semibold text-[#B4432B] mt-1">Still outstanding — on the sheet since {fmtDate(step.first_seen || null, "d MMM")} (day {(step.carried_days ?? 0) + 1})</div>
          )}
          {!compact && <p className="text-[13px] leading-relaxed text-[#2A2A2E] dark:text-fg/85 mt-1.5">{step.detail}</p>}
          {compact && <p className="text-[12px] leading-relaxed text-[#2A2A2E] dark:text-fg/85 mt-1 line-clamp-2">{step.detail}</p>}
          {step.why_critical && !compact && <p className="text-[12px] italic text-[#6B6B76] mt-1.5">Why now: {step.why_critical}</p>}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-[11.5px] text-[#6B6B76]">
            {step.due && <span className="font-semibold text-[#1F3550] dark:text-fg">Due {fmtDate(step.due, "d MMM yyyy")}</span>}
            {step.urgency === "critical" && <span className="text-[#B4432B] font-semibold">Critical</span>}
            {ev && <button onClick={() => open({ sha: ev.source_sha, highlight: ev.quote })} className="hover:text-accent text-left truncate max-w-[420px]" title="Open the record">“{ev.quote.slice(0, 120)}{ev.quote.length > 120 ? "…" : ""}”</button>}
            {!step.verified && <span title="No verbatim quote could be matched to a record for this step" className="text-faint">unquoted</span>}
          </div>
          {onDone && <div className="mt-3"><Checkbox checked={!!step.done} onCheckedChange={onDone} size={20} /></div>}
        </div>
      </div>
    </motion.div>
  );
}

export function SheetHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: React.ReactNode }) {
  return (
    <div className="border-b border-[#D9D3C7] dark:border-line pb-3 mb-2 flex items-end justify-between gap-4">
      <div>
        <div className="text-[10px] font-bold tracking-[0.22em] text-[#B0893B]">RKB CONSULTING GROUP, INC.</div>
        <h1 className="serif text-[28px] font-bold text-[#1F3550] dark:text-[#dbe3ee] leading-tight mt-1">{title}</h1>
        {subtitle && <div className="serif italic text-[14px] text-[#6B6B76] mt-0.5">{subtitle}</div>}
      </div>
      {right}
    </div>
  );
}

export function SheetBody({ sheet, person, onDone, compact }: { sheet: { sections: SheetSection[]; run_id: string }; person: NextPerson; onDone?: (pid: string, index: number, done: boolean) => void; compact?: boolean }) {
  if (!sheet.sections.length) return <div className="serif italic text-[#6B6B76] py-8">Nothing critical today.</div>;
  return (
    <div className="space-y-7">
      {sheet.sections.map((sec, i) => (
        <section key={sec.property_id}>
          <div className="text-[9.5px] font-bold tracking-[0.22em] text-[#B0893B]">PROPERTY {String(i + 1).padStart(2, "0")}</div>
          <Link href={`/property/${sec.property_id}`} className="serif text-[19px] font-bold text-[#1F3550] dark:text-[#dbe3ee] hover:underline leading-tight block mt-0.5">{sec.address}</Link>
          {sec.headline && <div className="serif italic text-[13px] text-[#6B6B76] mt-0.5">{sec.headline}</div>}
          <div className="border-b border-[#D9D3C7] dark:border-line my-2.5" />
          <div className="space-y-3">
            {sec.steps.map((s, k) => <StepCard key={k} step={s} n={k + 1} compact={compact} onDone={onDone ? (d) => onDone(sec.property_id, k, d) : undefined} />)}
            {!sec.steps.length && person === "rakesh" && <div className="text-[12.5px] italic text-[#6B6B76]">Nothing needs you here today; the others’ steps are below.</div>}
          </div>
          {person === "rakesh" && (["wes", "manjunath", "jp"] as NextPerson[]).map((o) => {
            const items = sec.others?.[o] || [];
            if (!items.length) return null;
            return (
              <div key={o} className="mt-3">
                <div className="text-[9.5px] font-bold tracking-[0.16em] text-[#6B6B76]">ALSO ON THIS PROPERTY — {PERSON_LABEL[o].toUpperCase()}</div>
                <ul className="mt-1 space-y-0.5">
                  {items.map((s, k) => <li key={k} className="text-[12.5px] text-[#2A2A2E] dark:text-fg/85 pl-3 relative before:content-['•'] before:absolute before:left-0 before:text-[#B0893B]">{s.title}{s.due && <span className="text-[#6B6B76]"> (due {fmtDate(s.due, "d MMM")})</span>}</li>)}
                </ul>
              </div>
            );
          })}
        </section>
      ))}
    </div>
  );
}

/** Full sheet for one person, from the API. */
export function Sheet({ runId = "latest", person, allowDone = true }: { runId?: string; person: NextPerson; allowDone?: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sheet", runId, person], queryFn: () => api.get<SheetT>(`/next-steps/${runId}/sheet/${person}`) });
  const done = useMutation({
    mutationFn: (v: { property_id: string; index: number; done: boolean }) => api.post(`/next-steps/${q.data!.run_id}/done`, { ...v, person }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["sheet"] }); qc.invalidateQueries({ queryKey: ["desk"] }); },
  });
  if (q.isLoading) return <div className="text-sm text-muted py-6">Loading the sheet…</div>;
  if (q.error) return <div className="text-sm text-muted py-6">No sheet yet — generate one from the dashboard.</div>;
  const s = q.data!;
  return (
    <div>
      <SheetHeader title={`Next steps — ${PERSON_LABEL[person]}`} subtitle={s.subtitle} right={<DownloadButtons runId={s.run_id} person={person} />} />
      <SheetBody sheet={s} person={person} onDone={allowDone ? (pid, index, d) => done.mutate({ property_id: pid, index, done: d }) : undefined} />
    </div>
  );
}

export function DownloadButtons({ runId, person, size = "sm" }: { runId: string; person: NextPerson; size?: "sm" | "md" }) {
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <a href={`/api/next-steps/${runId}/download/${person}.docx`}><Button size={size} variant="secondary"><FileText size={13} /> Word</Button></a>
      <a href={`/api/next-steps/${runId}/download/${person}.pdf`}><Button size={size} variant="secondary"><Download size={13} /> PDF</Button></a>
    </div>
  );
}

/* --------------------------------------------------------- CEO panel */
export function NextStepsPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["next-steps-latest"], queryFn: () => api.get<NextStepsLatest>("/next-steps/latest"), refetchInterval: (x) => (x.state.data?.running ? 10_000 : 120_000) });
  const [confirm, setConfirm] = React.useState<null | "generate" | "send">(null);
  const [progress, setProgress] = React.useState<{ text: string; done: number; total: number; props: Record<string, string> } | null>(null);
  const [err, setErr] = React.useState<string | null>(null);

  const generate = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/next-steps/generate"),
    onSuccess: ({ job_id }) => {
      setProgress({ text: "Starting…", done: 0, total: 14, props: {} });
      subscribeJob(job_id, (e) => {
        if (e.kind === "status") setProgress((p) => ({ ...(p || { done: 0, total: 14, props: {} }), text: e.data.text, done: e.data.done ?? p?.done ?? 0, total: e.data.total ?? p?.total ?? 14 }));
        if (e.kind === "property") setProgress((p) => ({ ...(p || { text: "", done: 0, total: 14, props: {} }), done: e.data.done, total: e.data.total, props: { ...(p?.props || {}), [e.data.property_id]: e.data.error ? "error" : `${e.data.steps} steps` } }));
        if (e.kind === "error") setErr(e.data.error);
      }, () => { setProgress(null); qc.invalidateQueries({ queryKey: ["next-steps-latest"] }); qc.invalidateQueries({ queryKey: ["desk"] }); });
    },
    onError: (e: any) => setErr(e.message),
  });
  const send = useMutation({
    mutationFn: () => api.post(`/next-steps/${q.data!.run!.run_id}/send`, { persons: ["jp", "manjunath"], confirm: true }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["next-steps-latest"] }); qc.invalidateQueries({ queryKey: ["outbox"] }); },
    onError: (e: any) => setErr(e.message),
  });
  const autoSend = useMutation({
    mutationFn: (enabled: boolean) => api.post("/next-steps/auto-send", { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["next-steps-latest"] }),
    onError: (e: any) => setErr(e.message),
  });

  const d = q.data;
  const run = d?.run || null;
  const running = !!progress || !!d?.running;
  const sentFor = (p: NextPerson): OutboxItem | undefined => (d?.sent || []).find((o) => o.meta?.person === p);
  const counts = run?.counts;

  return (
    <Card className="overflow-hidden">
      <div className="px-5 pt-4 pb-3 flex flex-wrap items-start justify-between gap-3 border-b border-line">
        <div>
          <div className="flex items-center gap-2 text-[13px] font-semibold tracking-tight"><Sparkles size={14} className="text-accent" /> Next steps — four sheets</div>
          <div className="text-xs text-muted mt-0.5">
            {run ? <>From the {fmtDate(run.day, "d MMMM")} reviews · {run.status === "complete" ? <>generated {ago(run.finished_at || run.started_at)} by {run.by === "morning" ? "the morning cycle" : run.by}</> : run.status}{run.errors?.length ? <> · <span className="text-high">{run.errors.length} propert{run.errors.length > 1 ? "ies" : "y"} failed</span></> : null}</> : "No sheets yet. Generate the first set."}
            {d?.in_progress && !progress && <> · <span className="text-accent inline-flex items-center gap-1"><Loader2 size={11} className="animate-spin" /> a new set is being prepared ({d.in_progress.progress?.done ?? 0}/{d.in_progress.progress?.total ?? 14}, started {ago(d.in_progress.started_at)})</span></>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {run && <Link href="/next-steps"><Button size="sm" variant="secondary">Open sheets</Button></Link>}
          <Button size="sm" variant="primary" disabled={running} onClick={() => setConfirm("generate")}>{running ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Generate next steps</Button>
        </div>
      </div>

      {progress && (
        <div className="px-5 py-3 border-b border-line bg-sunken/60">
          <div className="flex items-center justify-between text-xs"><span className="text-muted truncate">{progress.text}</span><span className="tnum font-medium">{progress.done}/{progress.total}</span></div>
          <div className="h-1.5 rounded-full bg-line mt-2 overflow-hidden"><motion.div className="h-full bg-accent" animate={{ width: `${(100 * progress.done) / Math.max(1, progress.total)}%` }} transition={{ duration: 0.4 }} /></div>
          {Object.keys(progress.props).length > 0 && <div className="flex flex-wrap gap-1.5 mt-2">{Object.entries(progress.props).map(([pid, v]) => <span key={pid} className={cn("text-[10.5px] px-1.5 h-5 rounded-full border inline-flex items-center gap-1", v === "error" ? "border-critical/30 text-critical bg-critical-soft" : "border-line text-muted bg-elev")}><Check size={10} /> {pid.replace(/_/g, " ")} · {v}</span>)}</div>}
        </div>
      )}
      {err && <div className="px-5 py-2 text-xs text-critical bg-critical-soft border-b border-critical/20 flex items-center gap-2"><AlertTriangle size={13} /> {err} <button className="ml-auto underline" onClick={() => setErr(null)}>dismiss</button></div>}

      {run && (
        <div className="grid sm:grid-cols-2 xl:grid-cols-4 divide-y sm:divide-y-0 sm:divide-x divide-line">
          {PERSONS.map((p) => {
            const ob = sentFor(p);
            const mailable = p === "jp" || p === "manjunath";
            return (
              <div key={p} className="p-4 space-y-2.5">
                <div className="flex items-center justify-between">
                  <Link href={`/next-steps/${p}`} className="serif font-bold text-[15px] text-[#1F3550] dark:text-[#dbe3ee] hover:underline">{PERSON_LABEL[p]}</Link>
                  <span className="tnum text-xs text-muted">{counts?.[p] ?? 0} step{(counts?.[p] ?? 0) === 1 ? "" : "s"}</span>
                </div>
                <DownloadButtons runId={run.run_id} person={p} />
                {p === "wes" && <><SendToWes runId={run.run_id} disabled={running || run.status !== "complete"} /><PreviewNote runId={run.run_id} person="wes" /></>}
                {p === "rakesh" && <div className="text-[11.5px] text-muted">Your own steps, with the team’s under each property.</div>}
                {mailable && <MailState ob={ob} />}
                {mailable && <PreviewNote runId={run.run_id} person={p} />}
              </div>
            );
          })}
        </div>
      )}

      {run && (
        <div className="px-5 py-3 border-t border-line flex flex-wrap items-center justify-between gap-3 bg-sunken/40">
          <div className="text-xs text-muted flex items-center gap-2">
            {d?.send_status?.can_send ? <><MailCheck size={13} className="text-good" /> Sending from {d.send_status.mailbox} is enabled.</> :
              <><MailWarning size={13} className="text-high" /> Sending from {d?.send_status?.mailbox || "rakesh@mtreh.com"} needs a one-time sign-in — emails will wait in the outbox until then. <Link href="/followups?tab=outbox" className="underline">How to enable</Link></>}
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-xs text-muted cursor-pointer select-none" title="When the morning cycle completes (early afternoon in India), JP Sir's and Manjunath Sir's sheets are emailed automatically. Off = only when you press Send.">
              <input type="checkbox" className="accent-[var(--accent)] h-3.5 w-3.5" checked={!!d?.auto_send} disabled={autoSend.isPending} onChange={(e) => autoSend.mutate(e.target.checked)} />
              Send automatically each morning
            </label>
            <Button size="sm" variant="primary" disabled={send.isPending || running || run.status !== "complete"} onClick={() => setConfirm("send")}><Mail size={13} /> Send now</Button>
          </div>
        </div>
      )}

      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent title={confirm === "generate" ? "Generate the next-steps sheets?" : "Send the sheets to JP Sir and Manjunath Sir?"}
          description={confirm === "generate"
            ? "Fourteen properties are reviewed one by one in fast mode (GPT-6 Astra, about 3–5 minutes per property, three at a time). Four sheets come out: Wes, Manjunath Sir, JP Sir and yours. Nothing is emailed by this step."
            : "Each receives their own sheet as Word and PDF from rakesh@mtreh.com with a cover note: the steps that matter most, anything carried over, the replies they still owe. The system watches for their reply; tomorrow's sheet mentions it gently if none came."}>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirm(null)}>Not now</Button>
            <Button variant="primary" onClick={() => { const c = confirm; setConfirm(null); if (c === "generate") generate.mutate(); else send.mutate(); }}>{confirm === "generate" ? "Yes, generate" : "Yes, send both"}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

type WesPreview = { to: { name: string; address: string }; subject: string; body: string; steps: number; properties: number; attachments: string[]; already_sent?: OutboxItem | null };

function SendToWes({ runId, disabled }: { runId: string; disabled?: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState<{ subject: string; body: string } | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const q = useQuery({ queryKey: ["wes-preview", runId], queryFn: () => api.get<WesPreview>(`/next-steps/${runId}/wes-preview`), enabled: open });
  React.useEffect(() => { if (q.data && !draft) setDraft({ subject: q.data.subject, body: q.data.body }); }, [q.data, draft]);
  const send = useMutation({
    mutationFn: () => api.post<{ result: { status: string } }>(`/next-steps/${runId}/send-wes`, { ...draft, confirm: true }),
    onSuccess: () => { setOpen(false); setDraft(null); qc.invalidateQueries({ queryKey: ["next-steps-latest"] }); qc.invalidateQueries({ queryKey: ["outbox", "wes-preview", "followups"] }); },
    onError: (e: any) => setErr(e.message),
  });
  const sent = q.data?.already_sent;
  return (
    <>
      <div className="space-y-1">
        <Button size="sm" variant="primary" disabled={disabled} onClick={() => { setErr(null); setOpen(true); }} title="Opens the note for review first; nothing is sent until you confirm inside"><Send size={12} /> Send to Wes…</Button>
        {sent && <div className="text-[11px] text-muted">Sent {ago(sent.sent_at || sent.queued_at)}{sent.status === "replied" ? " · Wes replied" : sent.status === "needs_consent" ? " · waiting for sign-in" : ""}</div>}
      </div>
      <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setDraft(null); }}>
        <DialogContent title="Review, then send Wes his sheet" description={q.data ? `From rakesh@mtreh.com to ${q.data.to.address} · ${q.data.steps} steps across ${q.data.properties} properties. Nothing has been sent yet — edit the note if you like, then press the button below.` : "Preparing the note…"} wide>
          {q.isLoading || !draft ? <div className="text-sm text-muted py-6">Preparing the note…</div> : (
            <div className="space-y-3">
              <input value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} className="h-9 w-full rounded-xl border border-line bg-elev px-3 text-sm font-medium" />
              <textarea value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} className="w-full min-h-[300px] rounded-xl border border-line bg-elev px-3 py-2 text-[13px] leading-relaxed focus:outline-none focus:ring-2 focus:ring-accent/30" />
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <span className="font-medium text-fg">Attached:</span>
                {q.data!.attachments.map((a) => <span key={a} className="inline-flex items-center gap-1 rounded-lg border border-line bg-sunken px-2 h-6"><FileText size={11} /> {a}</span>)}
              </div>
              {sent && <div className="text-xs text-high">This run's sheet was already sent to Wes {ago(sent.sent_at || sent.queued_at)}. Sending again sends a second copy.</div>}
              {err && <div className="text-xs text-critical">{err}</div>}
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
                <Button variant="primary" disabled={send.isPending || !draft.body.trim()} onClick={() => send.mutate()}>{send.isPending ? <Loader2 size={13} className="animate-spin" /> : <Mail size={13} />} Send to Wes now</Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

type TeamPreview = { to: { name: string; address: string }; subject: string; body: string; attachments: string[]; already_sent?: OutboxItem | null; as_sent: boolean };

/** Read the cover note that goes (or went) to JP Sir / Manjunath Sir. */
function PreviewNote({ runId, person }: { runId: string; person: NextPerson }) {
  const [open, setOpen] = React.useState(false);
  const path = person === "wes" ? `/next-steps/${runId}/wes-preview` : `/next-steps/${runId}/preview/${person}`;
  const q = useQuery({ queryKey: ["team-preview", runId, person], queryFn: () => api.get<TeamPreview>(path), enabled: open });
  const asSent = !!q.data?.as_sent || (person === "wes" && !!q.data?.already_sent);
  return (
    <>
      <button onClick={() => setOpen(true)} className="text-[11.5px] text-accent hover:underline inline-flex items-center gap-1"><Mail size={11} /> Read the email note</button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={`Email to ${PERSON_LABEL[person]}`}
          description={q.data ? (asSent ? `Sent to ${q.data.to.address}${q.data.already_sent?.sent_at ? ` ${ago(q.data.already_sent.sent_at)}` : ""}. ${person === "wes" ? "Nothing is sent from here — use Send to Wes." : ""}` : `As it will go to ${q.data.to.address} from rakesh@mtreh.com. ${person === "wes" ? "Nothing is sent from here — use Send to Wes." : ""}`) : "Loading…"} wide>
          {q.data && <EmailPreview subject={q.data.subject} body={q.data.body} attachments={q.data.attachments} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

export function EmailPreview({ subject, body, attachments }: { subject: string; body?: string | null; attachments?: string[] }) {
  return (
    <div className="space-y-3">
      <div className="text-sm font-semibold">{subject}</div>
      <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed bg-sunken rounded-xl p-4 max-h-[420px] overflow-y-auto">{body || "(no text)"}</pre>
      {attachments && attachments.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span className="font-medium text-fg">Attached:</span>
          {attachments.map((a) => <span key={a} className="inline-flex items-center gap-1 rounded-lg border border-line bg-sunken px-2 h-6"><FileText size={11} /> {a}</span>)}
        </div>
      )}
    </div>
  );
}

function MailState({ ob }: { ob?: OutboxItem }) {
  if (!ob) return <div className="text-[11.5px] text-muted flex items-center gap-1.5"><Mail size={12} /> Not sent yet.</div>;
  const map: Record<string, { label: string; tone: "good" | "high" | "critical" | "neutral" | "accent" }> = {
    replied: { label: `Replied ${ago(ob.replied_at)}`, tone: "good" }, sent: { label: `Sent ${ago(ob.sent_at)} · awaiting reply`, tone: "accent" },
    queued: { label: "Queued to send", tone: "neutral" }, needs_consent: { label: "Waiting for sign-in to send", tone: "high" },
    failed: { label: "Send failed", tone: "critical" }, superseded: { label: "Superseded by a newer sheet", tone: "neutral" },
  };
  const m = map[ob.status] || { label: ob.status, tone: "neutral" as const };
  return <div className="space-y-1"><Badge tone={m.tone}>{m.label}</Badge>{ob.reply_preview && <div className="text-[11px] text-muted line-clamp-2">“{ob.reply_preview}”</div>}{ob.error && ob.status !== "replied" && <div className="text-[11px] text-critical line-clamp-2">{ob.error}</div>}</div>;
}
