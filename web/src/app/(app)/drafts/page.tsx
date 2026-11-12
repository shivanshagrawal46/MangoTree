"use client";

/* Every reply that is written and waiting, in one list — and what the system
   has learned from the changes made to earlier drafts. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Empty, Skeleton } from "@/components/ui";
import { DraftCard } from "@/components/brain/draft";
import { sortReplies } from "@/components/brain/replies";
import { cn, fmtDate } from "@/lib/utils";
import { errText, type Draft, type Lesson } from "@/lib/brain";

const TABS: [string, string][] = [["ready", "Waiting to be sent"], ["sent", "Sent"], ["dismissed,superseded", "Set aside"], ["learned", "What it has learned"]];

export default function DraftsPage() {
  const [tab, setTab] = React.useState("ready");
  const learned = tab === "learned";
  const q = useQuery({ queryKey: ["brain", "drafts", tab], queryFn: () => api.get<Draft[]>(`/brain/drafts?status=${tab}`), placeholderData: (prev) => prev, enabled: !learned });
  const rows = learned ? [] : tab === "ready" ? sortReplies(q.data || []) : q.data || [];
  const sent = rows.filter((d) => d.status === "sent" && d.kept_ratio != null);
  return (
    <div className="px-6 lg:px-10 py-8 max-w-3xl mx-auto">
      <h1 className="text-[24px] font-semibold tracking-tight">Drafts</h1>
      <p className="text-[13.5px] text-muted mt-1 leading-relaxed">When an email needs an answer, the reply is written with every email and file of the last two weeks, the property file and a search of the whole record in view, then checked statement by statement before it appears here. Nothing is sent from this system — put a draft in Outlook, send it there, and it closes itself here.</p>
      <div className="mt-5 inline-flex flex-wrap rounded-xl bg-sunken p-1 border border-line">
        {TABS.map(([k, label]) => <button key={k} type="button" onClick={() => setTab(k)} className={cn("h-8 px-3 rounded-lg text-[13px] font-medium transition", tab === k ? "bg-elev text-fg shadow-[var(--shadow-sm)]" : "text-muted hover:text-fg")}>{label}</button>)}
      </div>
      {tab === "sent" && sent.length > 0 && (
        <div className="mt-3 text-xs text-muted">{sent.filter((d) => d.sent_as_written).length} of {sent.length} were sent as written; on average {Math.round((sent.reduce((a, d) => a + (d.kept_ratio || 0), 0) / sent.length) * 100)}% of a draft&apos;s wording was kept.</div>
      )}
      {learned ? <Learned /> : (
        <div className="mt-5 space-y-4">
          {q.isLoading && <Skeleton className="h-48" />}
          {!q.isLoading && rows.length === 0 && <Empty title={tab === "ready" ? "No reply is owed right now." : "Nothing here yet."} sub={tab === "ready" ? "When an email comes in that needs an answer, its draft appears here within a few minutes." : undefined} />}
          {rows.map((d) => <DraftCard key={d.draft_id} draft={d} compact={tab !== "ready"} />)}
        </div>
      )}
    </div>
  );
}

/** The rules drawn from his corrections. Each shows the edit it came from; one click removes it. */
function Learned() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brain", "lessons"], queryFn: () => api.get<Lesson[]>("/brain/lessons") });
  const [open, setOpen] = React.useState<string | null>(null);
  const remove = async (id: string) => {
    try { await api.del(`/brain/lessons/${id}`); toast.success("Removed — it will not be applied again"); qc.invalidateQueries({ queryKey: ["brain", "lessons"] }); }
    catch (e) { toast.error(errText(e)); }
  };
  const rows = q.data || [];
  return (
    <div className="mt-5">
      <p className="text-[13px] text-muted leading-relaxed mb-4">Each time a draft is changed — edited here, changed through the chat, or sent with different wording — the difference is read and anything he would want every time is kept as a rule. A rule for all his email applies only to the person it was learned on until a second correction shows it again. Remove any rule that is wrong.</p>
      {q.isLoading && <Skeleton className="h-32" />}
      {!q.isLoading && rows.length === 0 && <Empty title="Nothing learned yet." sub="The first rules appear after a draft has been changed and sent." />}
      <ul className="space-y-3">
        {rows.map((l) => (
          <li key={l.lesson_id} className="rounded-2xl border border-line bg-elev px-4 py-3">
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="text-[14px] leading-relaxed">{l.rule}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
                  <Badge tone={l.applies_now === "everyone" ? "accent" : "neutral"}>Applies to {l.applies_now === "everyone" ? "all his email" : l.applies_now === "this recipient" ? (l.recipient_names || "this person") : l.applies_now === "this property" ? (l.properties || []).join(", ") || "this property" : (l.learned_on || "this person") + ", until seen again"}</Badge>
                  <Badge>{l.kind}</Badge>
                  {(l.times_seen || 1) > 1 && <Badge tone="good">Seen {l.times_seen} times</Badge>}
                  <span>learned {fmtDate(l.created_at, "MMM d")}</span>
                  <button type="button" className="text-accent hover:underline" onClick={() => setOpen(open === l.lesson_id ? null : l.lesson_id)}>{open === l.lesson_id ? "Hide the edit" : "See the edit it came from"}</button>
                </div>
              </div>
              <button type="button" onClick={() => remove(l.lesson_id)} title="Remove this rule" className="h-8 w-8 grid place-items-center rounded-lg text-faint hover:text-critical hover:bg-sunken shrink-0"><Trash2 size={14} /></button>
            </div>
            {open === l.lesson_id && l.from && (
              <div className="mt-3 border-t border-line pt-3 text-[12.5px]">
                {l.shown_by && <div className="text-muted mb-2">{l.shown_by}</div>}
                {l.from.instruction && <div className="mb-2"><span className="font-medium">He said: </span>{l.from.instruction}</div>}
                <div className="grid gap-3 sm:grid-cols-2">
                  <div><div className="text-[10.5px] uppercase tracking-wide text-faint mb-1">Drafted</div><div className="rounded-xl bg-sunken/70 border border-line p-2.5 whitespace-pre-wrap leading-relaxed">{l.from.before}</div></div>
                  <div><div className="text-[10.5px] uppercase tracking-wide text-faint mb-1">What he used</div><div className="rounded-xl bg-accent-soft/50 border border-accent/20 p-2.5 whitespace-pre-wrap leading-relaxed">{l.from.after}</div></div>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
