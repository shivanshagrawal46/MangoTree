"use client";

/* One persistent conversation per property (or global). Ask → job → live
   trace streamed → answer card appended. History retained forever. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Send, StickyNote, Pin } from "lucide-react";
import { api, subscribeJob, type SSEEvent } from "@/lib/api";
import { AnswerCard, LiveTrace } from "@/components/answer";
import { Button, Textarea, Badge, Empty, Dialog, DialogContent } from "@/components/ui";
import { useUser } from "@/components/providers";
import { UploadButton } from "@/components/upload";
import { fmtDate, initials, cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/types";

export function ChatPanel({ propertyId, initialQuestion, className }: { propertyId?: string; initialQuestion?: string; className?: string }) {
  const { user } = useUser();
  const qc = useQueryClient();
  const router = useRouter();
  const key = ["chat", propertyId || "global"];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<{ chat_id: string; messages: ChatMessage[]; remember_notes: any[]; pending_notes?: any[]; summary?: string; summary_at?: string; active?: { job_id: string; question: string }[] }>(propertyId ? `/chat/${propertyId}` : "/chat"),
    // Always ask the server on (re)open: a cached copy from before a question was
    // asked would not know an answer is running and the trace would not re-attach.
    refetchOnMount: "always" });
  const [showSummary, setShowSummary] = React.useState(false);
  const [text, setText] = React.useState(initialQuestion || "");
  const [live, setLive] = React.useState<{ jobId: string; question: string; events: SSEEvent[] } | null>(null);
  const [noteOpen, setNoteOpen] = React.useState(false);
  const [note, setNote] = React.useState("");
  const bottom = React.useRef<HTMLDivElement>(null);
  const unsubRef = React.useRef<(() => void) | null>(null);

  React.useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [q.data?.messages?.length, live?.events.length]);

  /* Attach to a running answer. Used both for a question asked here and for one
     found still running when the chat is reopened — the job replays its events,
     so the live trace resumes where it was. Ask in one property, walk to another
     and ask there; each chat keeps its own answer going on the server. */
  const attach = React.useCallback((job_id: string, question: string) => {
    unsubRef.current?.();
    setLive({ jobId: job_id, question, events: [] });
    unsubRef.current = subscribeJob(job_id, (e) => {
      setLive((cur) => (cur && cur.jobId === job_id ? { ...cur, events: [...cur.events, e] } : cur));
      if (e.kind === "error") toast.error(`Answer failed: ${e.data?.error}`);
    }, () => {
      setLive(null); unsubRef.current = null;
      qc.invalidateQueries({ queryKey: key });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["shell"] });
    });
  }, [key, qc]); // eslint-disable-line react-hooks/exhaustive-deps

  React.useEffect(() => {
    const a = q.data?.active?.[0];
    if (a && !live) attach(a.job_id, a.question);
  }, [q.data?.active?.[0]?.job_id]); // eslint-disable-line react-hooks/exhaustive-deps
  React.useEffect(() => () => { unsubRef.current?.(); }, []);

  const ask = async () => {
    const question = text.trim();
    if (!question || live) return;
    setText("");
    const { job_id } = await api.post<{ job_id: string }>(propertyId ? `/chat/${propertyId}/ask` : "/chat/ask", { question });
    qc.invalidateQueries({ queryKey: ["shell"] });
    attach(job_id, question);
  };

  const save = async (job_id: string) => {
    await api.post("/saved", { chat_id: q.data!.chat_id, job_id });
    toast.success("Saved to the answer library");
  };
  const addNote = async () => {
    if (!note.trim()) return;
    await api.post("/notes", { text: note.trim(), scope: propertyId ? "property" : "global", property_id: propertyId });
    setNote(""); setNoteOpen(false); qc.invalidateQueries({ queryKey: key });
    toast.success(user?.role === "ceo" ? "Remembered — it rides with every answer in this scope from now on" : "Saved — pending Rakesh Sir's approval before it is used");
  };
  const approve = async (note_id: string) => { await api.post(`/notes/${note_id}/approve`); qc.invalidateQueries({ queryKey: key }); toast.success("Approved — now active"); };

  React.useEffect(() => { if (initialQuestion && !q.isLoading && !live) { setText(initialQuestion); } /* user presses send */ }, [initialQuestion, q.isLoading]); // eslint-disable-line

  const msgs = q.data?.messages || [];
  return (
    <div className={cn("flex flex-col h-full min-h-0", className)}>
      <div className="flex items-center justify-between px-1 pb-2">
        <div className="text-xs text-muted">{propertyId ? "Hard-filtered to this property. Portfolio-level documents appear labelled." : "Every property, the portfolio store, and everything else."}</div>
        <div className="flex items-center gap-2">
          {q.data?.summary && <button onClick={() => setShowSummary((v) => !v)}><Badge tone="info">Running summary {showSummary ? "▴" : "▾"}</Badge></button>}
          {q.data?.remember_notes?.length ? <Badge tone="accent"><Pin size={10} /> {q.data.remember_notes.length} note{q.data.remember_notes.length > 1 ? "s" : ""} in context</Badge> : null}
          <Button size="sm" variant="ghost" onClick={() => setNoteOpen(true)}><StickyNote size={13} /> Remember</Button>
          {propertyId && <UploadButton pid={propertyId} />}
        </div>
      </div>
      {showSummary && q.data?.summary && (
        <div className="mb-3 rounded-2xl border border-line bg-sunken/60 px-4 py-3 text-xs whitespace-pre-wrap leading-relaxed">
          <div className="text-[10px] uppercase tracking-wide text-faint mb-1">What this chat has established so far · updated {fmtDate(q.data.summary_at, "MMM d, HH:mm")} · rides with every question</div>
          {q.data.summary}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto space-y-4 pr-1">
        {msgs.length === 0 && !live && <Empty title="Nothing asked yet." sub="Ask about a payoff, a guarantor, a draw, a deadline — anything in the records. The answer takes a few minutes and shows its work." />}
        {/* The question being answered is already in history (it is written when asked); the live block renders it. */}
        {msgs.filter((m) => !(live && m.role === "user" && (m as any).job_id === live.jobId)).map((m, i) => m.role === "user" ? (
          <div key={i} className="flex justify-end"><div className="max-w-[80%] rounded-2xl rounded-br-md bg-accent text-white px-4 py-2.5 text-[13.5px] shadow-sm">
            <div>{m.content}</div><div className="text-[10px] opacity-70 mt-1 text-right">{m.by} · {fmtDate(m.at, "MMM d, HH:mm")}</div></div></div>
        ) : (
          <div key={i} className="flex gap-3"><div className="h-7 w-7 rounded-full bg-sunken grid place-items-center text-[10px] font-semibold text-muted shrink-0 mt-1">AI</div>
            <div className="flex-1 min-w-0"><AnswerCard answer={m.answer} onSave={() => save(m.job_id)} pdfHref={`/api/export/answer/${encodeURIComponent(q.data!.chat_id)}/${m.job_id}.pdf`} onAcceptTasks={() => router.push(propertyId ? `/tasks?property=${propertyId}&status=suggested` : "/tasks?status=suggested")} /></div></div>
        ))}
        {live && (
          <>
            <div className="flex justify-end"><div className="max-w-[80%] rounded-2xl rounded-br-md bg-accent text-white px-4 py-2.5 text-[13.5px] shadow-sm">{live.question}</div></div>
            <div className="flex gap-3"><div className="h-7 w-7 rounded-full bg-sunken grid place-items-center text-[10px] font-semibold text-muted shrink-0 mt-1">AI</div><div className="flex-1"><LiveTrace events={live.events} /></div></div>
          </>
        )}
        <div ref={bottom} />
      </div>
      <div className="pt-3">
        <div className="rounded-2xl border border-line bg-elev shadow-[var(--shadow-sm)] focus-within:ring-2 focus-within:ring-accent/30 transition">
          <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={propertyId ? "Ask about this property…" : "Ask across every property…"} className="border-0 focus:ring-0 bg-transparent"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }} />
          <div className="flex items-center justify-between px-3 pb-2"><span className="text-[11px] text-faint">{live ? "Answering — you can open another property and ask there; this one keeps going and will be here when you return." : "Opus 5 investigates · GPT-5.6 second-reads · Opus 5 writes the final. 5–10 min. Enter to send."}</span>
            <Button size="sm" variant="primary" onClick={ask} disabled={!text.trim() || !!live}><Send size={13} /> {live ? "Working…" : "Ask"}</Button></div>
        </div>
      </div>
      <Dialog open={noteOpen} onOpenChange={setNoteOpen}>
        <DialogContent title="Remember this" description={`Stored verbatim, attributed to ${user?.name}, injected into every answer ${propertyId ? "for this property" : "everywhere"}.`}>
          <Textarea rows={4} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Wes's draws on Varnum need JP's sign-off before release." autoFocus />
          <div className="mt-3 flex justify-end gap-2"><Button variant="ghost" onClick={() => setNoteOpen(false)}>Cancel</Button><Button variant="primary" onClick={addNote}>Remember</Button></div>
          {(q.data?.remember_notes?.length || q.data?.pending_notes?.length) ? (
            <div className="mt-4 border-t border-line pt-3 space-y-1.5 max-h-48 overflow-y-auto">
              {(q.data?.pending_notes || []).map((n: any) => (
                <div key={n.note_id} className="text-xs flex items-start gap-2 rounded-lg bg-high-soft/60 px-2 py-1.5"><span className="flex-1"><span className="text-high font-semibold">Pending · </span><span className="text-faint">{n.author} · {fmtDate(n.created_at)}:</span> {n.text}</span>{user?.role === "ceo" && <Button size="sm" variant="soft" onClick={() => approve(n.note_id)}>Approve</Button>}</div>
              ))}
              {(q.data?.remember_notes || []).map((n: any) => <div key={n.note_id} className="text-xs"><span className="text-faint">{n.author} · {fmtDate(n.created_at)}:</span> {n.text}</div>)}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </div>
  );
}
