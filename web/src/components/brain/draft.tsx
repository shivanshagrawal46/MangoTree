"use client";

/* A reply that is written and waiting. Read it, change a word if needed, send
   it from Outlook. Nothing is sent from here. */

import * as React from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Copy, ExternalLink, Mail, MailOpen, RefreshCw, X } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Input } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText, type Draft, type Person, type Verification } from "@/lib/brain";

function people(list: Person[] | undefined) {
  return (list || []).map((p) => (p.name && p.name !== p.email ? `${p.name}${p.email ? ` <${p.email}>` : ""}` : p.email || p.name || "")).filter(Boolean).join(", ");
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); return true; } catch { return false; }
}

export function useDraftRefresh() {
  const qc = useQueryClient();
  return React.useCallback(() => {
    qc.invalidateQueries({ queryKey: ["brain", "home"] });
    qc.invalidateQueries({ queryKey: ["brain", "drafts"] });
    qc.invalidateQueries({ queryKey: ["brain", "counts"] });
    qc.invalidateQueries({ queryKey: ["brain", "property"] });
  }, [qc]);
}

export function DraftCard({ draft, compact, onChange, showProperty = true }: { draft: Draft; compact?: boolean; onChange?: (d: Draft) => void; showProperty?: boolean }) {
  const [d, setD] = React.useState<Draft>(draft);
  const [body, setBody] = React.useState(draft.body || "");
  const [subject, setSubject] = React.useState(draft.subject || "");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [redo, setRedo] = React.useState(false);
  const [instruction, setInstruction] = React.useState("");
  const [open, setOpen] = React.useState(!compact);
  const refresh = useDraftRefresh();
  const evidence = useEvidence();
  const area = React.useRef<HTMLTextAreaElement>(null);

  // When the draft changes underneath (the chat revised it, or it was sent), take the new version.
  const [seen, setSeen] = React.useState(draft);
  if (draft !== seen && (draft.draft_id !== seen.draft_id || draft.body !== seen.body || draft.subject !== seen.subject || draft.status !== seen.status)) {
    setSeen(draft); setD(draft); setBody(draft.body || ""); setSubject(draft.subject || "");
  }
  React.useLayoutEffect(() => { const el = area.current; if (el) { el.style.height = "auto"; el.style.height = `${el.scrollHeight + 2}px`; } }, [body, open]);

  const patch = async (fields: Partial<Draft>, label?: string) => {
    setBusy(label || "save");
    try {
      const next = await api.patch<Draft>(`/brain/drafts/${d.draft_id}`, fields);
      setD(next); onChange?.(next); refresh();
      return next;
    } catch (e) { toast.error(errText(e, "Could not save")); } finally { setBusy(null); }
  };
  const saveIfChanged = () => {
    if (body.trim() !== (d.body || "").trim() || subject.trim() !== (d.subject || "").trim()) patch({ body, subject });
  };
  const toOutlook = async () => {
    setBusy("outlook");
    try {
      if (body.trim() !== (d.body || "").trim() || subject.trim() !== (d.subject || "").trim()) await api.patch<Draft>(`/brain/drafts/${d.draft_id}`, { body, subject });
      const info = await api.post<Draft["outlook"]>(`/brain/drafts/${d.draft_id}/outlook`);
      setD((cur) => ({ ...cur, outlook: info })); refresh();
      toast.success(info?.as_reply ? "It is in your Outlook Drafts, as a reply in the same conversation" : "It is in your Outlook Drafts");
    } catch (e) { toast.error(errText(e, "Could not reach Outlook")); } finally { setBusy(null); }
  };
  const rewrite = () => {
    setBusy("redo");
    api.post<{ job_id: string }>(`/brain/drafts/${d.draft_id}/redo`, { instruction }).then(({ job_id }) => {
      subscribeJob(job_id, (e) => {
        if (e.kind === "result" && e.data?.draft_id) { setD(e.data); setBody(e.data.body || ""); setSubject(e.data.subject || ""); onChange?.(e.data); }
        if (e.kind === "result" && e.data?.needed === false) toast.message("No reply seems needed", { description: e.data?.note });
        if (e.kind === "error") toast.error(e.data?.error || "Rewrite failed");
      }, () => { setBusy(null); setRedo(false); setInstruction(""); refresh(); });
    }).catch((e) => { toast.error(errText(e)); setBusy(null); });
  };

  const done = d.status !== "ready";
  const names = (d.properties || []).map((p) => p.name).join(" · ");
  return (
    <div className={cn("rounded-2xl border bg-elev overflow-hidden", done ? "border-line opacity-75" : "border-line shadow-[var(--shadow-sm)]")}>
      <button type="button" onClick={() => setOpen((v) => !v)} className="w-full text-left px-4 pt-3 pb-2.5 flex items-start gap-3">
        <div className={cn("h-8 w-8 rounded-xl grid place-items-center shrink-0", done ? "bg-sunken text-muted" : "bg-accent-soft text-accent")}><Mail size={15} /></div>
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-semibold leading-snug truncate">{subject || "(no subject)"}</div>
          {d.in_reply_to?.subject && (
            <div className="text-xs mt-0.5 truncate"><span className="font-medium text-fg">Answers the email from {d.in_reply_to.from_name || (d.in_reply_to.from || [])[0] || "—"}</span>
              <span className="text-muted"> of {fmtDate(d.in_reply_to.date, "EEE d MMM, HH:mm")} — “{d.in_reply_to.subject}”</span></div>
          )}
          <div className="text-xs text-muted mt-0.5 truncate">To {people(d.to) || "—"}{d.cc?.length ? ` · cc ${people(d.cc)}` : ""}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-faint">
            {showProperty && names && <Badge>{names}</Badge>}
            {d.status === "sent" && <Badge tone="good"><Check size={10} /> {d.sent_by ? `Sent by ${d.sent_by}` : "Sent"}{d.sent_at ? ` · ${fmtDate(d.sent_at, "d MMM")}` : ""}{d.kept_ratio != null ? ` · ${Math.round(d.kept_ratio * 100)}% as written` : ""}</Badge>}
            {d.status === "sent" && d.auto_closed && <Badge>Closed automatically</Badge>}
            {d.status === "ready" && d.answer_check?.status === "not_answered" && <Badge tone="info">No reply sent yet{d.answer_check.checked_at ? ` · checked ${ago(d.answer_check.checked_at)}` : ""}</Badge>}
            {d.status === "dismissed" && <Badge>Dismissed</Badge>}
            {d.status === "superseded" && <Badge>Replaced by a newer draft</Badge>}
            {d.status === "ready" && d.urgency === "today" && <Badge tone="high">Reply today</Badge>}
            {d.outlook?.saved_at && d.outlook.as_reply && <Badge tone="good">In Outlook — reply all, in the conversation</Badge>}
            {d.outlook?.saved_at && !d.outlook.as_reply && <Badge tone="high">In Outlook — as a new email, not in the conversation</Badge>}
            <span>written {ago(d.created_at)}</span>
          </div>
        </div>
      </button>

      {open && (
        <div className="px-4 pb-3">
          {done && d.closed_note && (
            <div className="mb-2.5 rounded-xl bg-good-soft/70 border border-good/25 px-3 py-2 text-[12.5px] leading-relaxed">
              <span className="font-semibold text-good">{d.answered_before_draft ? "This had already been answered. " : "Sent and done. "}</span>{d.closed_note}
              {d.sent_sha && <button type="button" onClick={() => evidence.open({ sha: d.sent_sha! })} className="ml-2 underline text-good">See what was sent</button>}
            </div>
          )}
          {d.stale?.why && !done && (
            <div className="mb-2.5 rounded-xl bg-critical-soft border border-critical/30 px-3 py-2 text-[12.5px] leading-relaxed">
              <span className="font-semibold text-critical">Do not send this as it is. </span>{d.stale.why} Rewrite it before sending.
              {d.reply_to_sha && d.origin !== "chat" && <button type="button" onClick={rewrite} disabled={busy === "redo"} className="ml-2 underline text-critical">{busy === "redo" ? "Rewriting…" : "Rewrite now"}</button>}
            </div>
          )}
          {!done && d.verification && <Checked v={d.verification} figures={d.checks?.not_found} />}
          {!done && !d.verification && d.origin !== "chat" && (
            <div className="mb-2.5 rounded-xl bg-high-soft/70 border border-high/20 px-3 py-2 text-[12.5px] leading-relaxed">
              <span className="font-semibold text-high">Not yet checked. </span>This draft was written before the statement-by-statement check was added. Rewrite it before relying on it.
              {d.reply_to_sha && <button type="button" onClick={rewrite} disabled={busy === "redo"} className="ml-2 underline text-high">{busy === "redo" ? "Rewriting…" : "Rewrite and check now"}</button>}
            </div>
          )}
          {ownNote(d.note) && !done && (
            <div className="mb-2.5 rounded-xl bg-high-soft/70 border border-high/20 px-3 py-2 text-[12.5px] leading-relaxed text-fg whitespace-pre-line">
              <span className="font-semibold text-high">Before you send: </span>{ownNote(d.note)}
            </div>
          )}
          <Input value={subject} onChange={(e) => setSubject(e.target.value)} onBlur={saveIfChanged} disabled={done} className="mb-2 text-[13px] font-medium" aria-label="Subject" />
          <textarea ref={area} value={body} onChange={(e) => setBody(e.target.value)} onBlur={saveIfChanged} disabled={done} spellCheck
            className="w-full rounded-xl border border-line bg-bg px-3.5 py-3 text-[13.5px] leading-[1.65] whitespace-pre-wrap focus:outline-none focus:ring-2 focus:ring-accent/30 resize-none min-h-[120px]" aria-label="Email text" />
          {redo && (
            <div className="mt-2 flex gap-2">
              <Input autoFocus value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="What should change? e.g. firmer on the insurance, drop the second paragraph"
                onKeyDown={(e) => { if (e.key === "Enter") rewrite(); }} />
              <Button variant="primary" onClick={rewrite} disabled={busy === "redo"}>{busy === "redo" ? "Rewriting…" : "Rewrite"}</Button>
            </div>
          )}
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            {!done && (
              <>
                <Button size="sm" variant="primary" onClick={toOutlook} disabled={!!busy}><MailOpen size={13} /> {busy === "outlook" ? "Placing…" : d.outlook?.saved_at ? "Update in Outlook Drafts" : "Put in Outlook Drafts"}</Button>
                <Button size="sm" onClick={async () => { if (await copy(`Subject: ${subject}\n\n${body}`)) toast.success("Copied"); }}><Copy size={13} /> Copy</Button>
                {d.reply_to_sha && d.origin !== "chat" && <Button size="sm" variant="ghost" onClick={() => setRedo((v) => !v)}><RefreshCw size={13} /> Rewrite</Button>}
                <Button size="sm" variant="ghost" onClick={() => patch({ status: "sent" }, "sent").then(() => toast.success("Marked as sent"))}><Check size={13} /> I sent it</Button>
                <Button size="sm" variant="ghost" onClick={() => patch({ status: "dismissed" }, "dismiss")}><X size={13} /> Not needed</Button>
              </>
            )}
            {done && <Button size="sm" variant="ghost" onClick={() => patch({ status: "ready" })}>Reopen</Button>}
            <span className="flex-1" />
            {d.outlook?.web_link && <a href={d.outlook.web_link} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline flex items-center gap-1"><ExternalLink size={11} /> Open in Outlook</a>}
            {d.outlook?.saved_at && !d.outlook.as_reply && d.outlook.why_not && <span className="basis-full text-[11.5px] text-high">Saved as a new email because {d.outlook.why_not}.</span>}
            {d.reply_to_sha && <button type="button" onClick={() => evidence.open({ sha: d.reply_to_sha! })} className="text-xs text-accent hover:underline">See the email it answers</button>}
          </div>
          {d.lookups?.length ? <div className="mt-2 text-[11px] text-faint">Looked at: {d.lookups.slice(0, 3).join("; ")}</div> : null}
        </div>
      )}
    </div>
  );
}

/** The writer's own note, without the "CHECK FIRST" lines — those are shown as a list by <Checked>. */
function ownNote(note?: string) {
  const n = (note || "").trim();
  if (!n.startsWith("CHECK FIRST")) return n;
  const i = n.indexOf("\n\n");
  return i >= 0 ? n.slice(i + 2).trim() : "";
}

/** What the second reading found. Open points are shown in full; a clean check is one quiet line. */
export function Checked({ v, figures }: { v?: Verification; figures?: string[] }) {
  if (!v) return null;
  const open = v.open || [];
  const fig = figures || [];
  if (!v.ran) {
    return <div className="mb-2.5 rounded-xl bg-critical-soft border border-critical/30 px-3 py-2 text-[12.5px]"><span className="font-semibold text-critical">Not checked. </span>The second check could not be run on this, so nothing in it has been independently confirmed.</div>;
  }
  if (!open.length && !fig.length) {
    return <div className="mb-2.5 text-[11.5px] text-good flex items-center gap-1.5"><Check size={12} /> Checked against the records: {v.statements_checked} statement{v.statements_checked === 1 ? "" : "s"}{v.corrected ? `, ${v.corrected} corrected before you saw it` : ""}, nothing left open.</div>;
  }
  return (
    <div className="mb-2.5 rounded-xl bg-critical-soft/70 border border-critical/30 px-3 py-2.5 text-[12.5px] leading-relaxed">
      <div className="font-semibold text-critical">Check before you rely on this{v.corrected ? ` (${v.corrected} other point${v.corrected === 1 ? " was" : "s were"} already corrected)` : ""}</div>
      <ul className="mt-1.5 space-y-2">
        {open.map((p, i) => (
          <li key={i}>
            <div className="font-medium">{p.label}: <span className="font-normal italic">“{p.statement}”</span></div>
            <div className="text-muted">{p.record_shows}</div>
          </li>
        ))}
        {fig.length > 0 && <li><div className="font-medium">Not found word for word in the records</div><div className="text-muted">{fig.join(", ")} — a calculation, or to be confirmed.</div></li>}
      </ul>
    </div>
  );
}

/** One line per waiting reply — for the home page and the property panel. */
export function DraftRow({ draft, onOpen }: { draft: Draft; onOpen: () => void }) {
  const names = (draft.properties || []).map((p) => p.name).join(" · ");
  return (
    <button type="button" onClick={onOpen} className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-sunken/60 transition">
      <div className="h-8 w-8 rounded-xl bg-accent-soft text-accent grid place-items-center shrink-0"><Mail size={15} /></div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[13.5px] font-semibold truncate">{draft.subject}</span>
          {draft.urgency === "today" && <Badge tone="high">Today</Badge>}
          {draft.stale?.why && <Badge tone="critical">Out of date</Badge>}
          {(draft.verification?.open?.length || 0) > 0 && <Badge tone="high">{draft.verification!.open.length} to check</Badge>}
        </div>
        <div className="text-xs text-muted mt-0.5 truncate">{draft.in_reply_to?.from_name ? `Answers ${draft.in_reply_to.from_name}'s email of ${fmtDate(draft.in_reply_to.date, "d MMM")}` : `To ${people(draft.to)}`}{names ? ` · ${names}` : ""}</div>
        <div className="text-[12.5px] text-muted mt-1 line-clamp-2 leading-relaxed">{(draft.body || "").replace(/\s+/g, " ").slice(0, 220)}</div>
      </div>
      <span className="text-[11px] text-faint shrink-0 mt-0.5">{ago(draft.created_at)}</span>
    </button>
  );
}

export function DraftsLink({ count }: { count: number }) {
  if (!count) return null;
  return <Link href="/replies" className="text-xs text-accent hover:underline">All {count} drafts</Link>;
}
