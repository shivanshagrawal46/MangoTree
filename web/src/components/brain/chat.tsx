"use client";

/* Chat with someone who already knows the file. One conversation that holds its
   thread; answers stream as they are written; a follow-up takes seconds.
   The same component serves a property and the whole portfolio. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowUp, ChevronDown, FileText, History, Paperclip, Plus, Square, X } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Button, Skeleton } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { Md } from "@/components/brain/md";
import { Checked, DraftCard } from "@/components/brain/draft";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText, type ChatFile, type ChatMessage, type ChatThread } from "@/lib/brain";

type Live = { jobId: string; question: string; files: ChatFile[]; text: string; steps: string[]; startedAt: number };

const STARTERS_PROPERTY = [
  "Where does this stand, and what do I need to do this week?",
  "What came in over the last 7 days?",
  "What is RKB owed here, and what protects it?",
  "Who is waiting on whom right now?",
];
const STARTERS_ALL = [
  "Which properties need me today, and why?",
  "What changed across the book in the last 3 days?",
  "Where is RKB's money most at risk right now?",
  "Which replies am I behind on?",
];

export function BrainChat({ propertyId, className, initialQuestion }: { propertyId?: string; className?: string; initialQuestion?: string }) {
  const qc = useQueryClient();
  const scope = propertyId || "portfolio";
  const [threadId, setThreadId] = React.useState<string | null>(null);
  const key = ["brain", "thread", scope, threadId || "current"];
  const q = useQuery({
    queryKey: key,
    queryFn: () => threadId ? api.get<ChatThread>(`/brain/chat/threads/${threadId}`)
      : api.get<ChatThread>(`/brain/chat/current${propertyId ? `?property_id=${propertyId}` : ""}`),
    refetchOnMount: "always", staleTime: 0,
  });
  const threads = useQuery({ queryKey: ["brain", "threads", scope], queryFn: () => api.get<ChatThread[]>(`/brain/chat/threads${propertyId ? `?property_id=${propertyId}` : ""}`) });
  const thread = q.data;
  const [text, setText] = React.useState(initialQuestion || "");
  const [files, setFiles] = React.useState<ChatFile[]>([]);
  const [uploading, setUploading] = React.useState(false);
  const [live, setLive] = React.useState<Live | null>(null);
  const [showHistory, setShowHistory] = React.useState(false);
  const unsub = React.useRef<(() => void) | null>(null);
  const bottom = React.useRef<HTMLDivElement>(null);
  const fileInput = React.useRef<HTMLInputElement>(null);
  const area = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => () => { unsub.current?.(); }, []);
  React.useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [thread?.messages?.length, live?.text, live?.steps.length]);
  React.useLayoutEffect(() => { const el = area.current; if (el) { el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 180)}px`; } }, [text]);

  const attach = React.useCallback((jobId: string, question: string, sentFiles: ChatFile[] = []) => {
    unsub.current?.();
    setLive({ jobId, question, files: sentFiles, text: "", steps: [], startedAt: Date.now() });
    unsub.current = subscribeJob(jobId, (e) => {
      setLive((cur) => {
        if (!cur || cur.jobId !== jobId) return cur;
        if (e.kind === "delta") return { ...cur, text: cur.text + (e.data?.text || "") };
        // A new round begins: what was written before a lookup was a working note, not the answer.
        if (e.kind === "round") return { ...cur, text: "" };
        if (e.kind === "tool") return { ...cur, steps: [...cur.steps, e.data?.label || e.data?.name || "Looked something up"] };
        return cur;
      });
      if (e.kind === "error") toast.error(e.data?.error || "That did not go through. Please ask again.");
    }, () => {
      unsub.current = null;
      qc.invalidateQueries({ queryKey: ["brain"] }).finally(() => setLive((cur) => (cur && cur.jobId === jobId ? null : cur)));
    });
  }, [qc]);

  // Re-attach to an answer still being written when the chat is reopened.
  React.useEffect(() => {
    const a = thread?.active?.[0];
    // Subscribing to the running job's stream is exactly what an effect is for.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (a && !live) attach(a.job_id, a.text || "");
  }, [thread?.active?.[0]?.job_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = async (override?: string) => {
    const question = (override ?? text).trim();
    if (!question || live || !thread) return;
    const sending = files;
    setText(""); setFiles([]);
    try {
      const { job_id } = await api.post<{ job_id: string }>(`/brain/chat/threads/${thread.thread_id}/ask`, { text: question, file_ids: sending.map((f) => f.file_id) });
      attach(job_id, question, sending);
    } catch (e) { toast.error(errText(e, "Could not send")); setText(question); setFiles(sending); }
  };
  const stop = async () => { if (live) { await api.post(`/brain/chat/jobs/${live.jobId}/stop`).catch(() => {}); } };

  const upload = async (list: FileList | null) => {
    if (!list?.length || !thread) return;
    setUploading(true);
    try {
      const form = new FormData();
      Array.from(list).forEach((f) => form.append("files", f));
      const out = await api.upload<ChatFile[]>(`/brain/chat/threads/${thread.thread_id}/files`, form);
      setFiles((cur) => [...cur, ...out]);
      const unreadable = out.filter((f) => f.readable === false);
      if (unreadable.length) toast.message(`${unreadable.map((f) => f.name).join(", ")} could not be read here`, { description: "It is stored, but I can only see its name." });
    } catch (e) { toast.error(errText(e, "Upload failed")); } finally { setUploading(false); if (fileInput.current) fileInput.current.value = ""; }
  };

  const newChat = async () => {
    const t = await api.post<ChatThread>("/brain/chat/threads", { property_id: propertyId || null });
    qc.setQueryData(["brain", "thread", scope, t.thread_id], t);
    setThreadId(t.thread_id); setShowHistory(false);
  };

  const msgs = (thread?.messages || []).filter((m) => !(live && m.role === "user" && m.text === live.question && m === thread!.messages![thread!.messages!.length - 1]));
  const empty = !q.isLoading && msgs.length === 0 && !live;
  const starters = propertyId ? STARTERS_PROPERTY : STARTERS_ALL;
  const past = (threads.data || []).filter((t) => t.thread_id !== thread?.thread_id);

  return (
    <div className={cn("flex flex-col min-h-0 h-full", className)}>
      <div className="flex items-center justify-between gap-2 px-4 h-9 shrink-0 border-b border-line">
        <div className="text-[12.5px] font-medium text-muted truncate">{thread?.title || (propertyId ? "Chat about this property" : "Chat across all properties")}</div>
        <div className="flex items-center gap-1 relative">
          {past.length > 0 && <Button size="sm" variant="ghost" onClick={() => setShowHistory((v) => !v)}><History size={13} /> Earlier <ChevronDown size={12} /></Button>}
          <Button size="sm" variant="ghost" onClick={newChat} disabled={!!live || empty}><Plus size={13} /> New chat</Button>
          {showHistory && (
            <div className="absolute right-0 top-9 z-20 w-80 max-h-80 overflow-y-auto rounded-xl border border-line bg-elev shadow-[var(--shadow)] p-1">
              {past.map((t) => (
                <button key={t.thread_id} type="button" onClick={() => { setThreadId(t.thread_id); setShowHistory(false); }} className="w-full text-left px-3 py-2 rounded-lg hover:bg-sunken">
                  <div className="text-[12.5px] truncate">{t.title || "Untitled"}</div>
                  <div className="text-[11px] text-faint">{ago(t.updated_at)} · {t.n_messages} messages</div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4">
        <div className="mx-auto max-w-3xl space-y-6">
          {q.isLoading && <Skeleton className="h-24" />}
          {empty && (
            <div className="pt-2">
              <div className="text-[14px] font-semibold tracking-tight">{propertyId ? "Ask about this property" : "Ask about any property"}</div>
              <div className="text-[13px] text-muted mt-1 leading-relaxed">I already have the file open — where it stands, what is open, the last week of mail. Ask, or drop in a document and ask about it.</div>
              <div className="mt-4 grid gap-2">
                {starters.map((s) => (
                  <button key={s} type="button" onClick={() => send(s)} className="text-left text-[12.5px] px-3 py-2 rounded-lg border border-line bg-elev hover:border-accent/40 hover:bg-accent-soft/40 transition">{s}</button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m) => m.role === "user" ? <UserBubble key={m.message_id} text={m.text} files={m.files} meta={`${m.by_name || m.by || ""} · ${fmtDate(m.at, "MMM d, HH:mm")}`} /> : <Answer key={m.message_id} m={m} />)}
          {live && (
            <>
              <UserBubble text={live.question} files={live.files} />
              <div>
                <Steps steps={live.steps} running />
                {live.text ? <Md text={live.text} streaming /> : <Thinking since={live.startedAt} hasSteps={live.steps.length > 0} />}
                <div className="mt-1.5 text-[11.5px] text-faint">The records are searched, the answer is written, then every statement is checked before you see it. This takes one to four minutes.</div>
              </div>
            </>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div className="shrink-0 px-4 pb-2.5 pt-1">
        <div className="mx-auto max-w-3xl rounded-2xl border border-line bg-elev shadow-[var(--shadow-sm)] focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/20 transition">
          {files.length > 0 && (
            <div className="px-3 pt-2.5 flex flex-wrap gap-1.5">
              {files.map((f) => (
                <span key={f.file_id} className="inline-flex items-center gap-1.5 h-7 pl-2 pr-1 rounded-lg bg-sunken border border-line text-xs">
                  <FileText size={12} className="text-muted" /> <span className="max-w-[180px] truncate">{f.name}</span>
                  <button type="button" onClick={() => setFiles((cur) => cur.filter((x) => x.file_id !== f.file_id))} className="h-5 w-5 grid place-items-center rounded text-faint hover:text-fg"><X size={12} /></button>
                </span>
              ))}
            </div>
          )}
          <textarea ref={area} rows={1} value={text} onChange={(e) => setText(e.target.value)}
            placeholder={propertyId ? "Ask, or tell me what happened…" : "Ask about any property…"}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            className="w-full bg-transparent px-3.5 pt-2.5 pb-0.5 text-[13.5px] leading-relaxed placeholder:text-faint focus:outline-none resize-none" />
          <div className="flex items-center justify-between px-2 pb-2">
            <div className="flex items-center gap-1">
              <input ref={fileInput} type="file" multiple hidden onChange={(e) => upload(e.target.files)} />
              <Button size="sm" variant="ghost" onClick={() => fileInput.current?.click()} disabled={uploading || !thread}><Paperclip size={13} /> {uploading ? "Reading…" : "Attach"}</Button>
            </div>
            {live
              ? <Button size="icon" variant="secondary" onClick={stop} title="Stop"><Square size={13} /></Button>
              : <Button size="icon" variant="primary" onClick={() => send()} disabled={!text.trim() || !thread} title="Send (Enter)"><ArrowUp size={15} /></Button>}
          </div>
        </div>
        <div className="mx-auto max-w-3xl mt-1 text-[10.5px] text-faint text-center">Answers come from RKB&apos;s own records. Open a source number to see the exact passage.</div>
      </div>
    </div>
  );
}

function UserBubble({ text, files, meta }: { text: string; files?: ChatFile[]; meta?: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%]">
        {!!files?.length && (
          <div className="flex flex-wrap justify-end gap-1.5 mb-1.5">
            {files.map((f) => <span key={f.file_id} className="inline-flex items-center gap-1.5 h-7 px-2 rounded-lg bg-sunken border border-line text-xs"><FileText size={12} className="text-muted" /> <span className="max-w-[200px] truncate">{f.name}</span></span>)}
          </div>
        )}
        <div className="rounded-2xl rounded-br-md bg-sunken border border-line px-4 py-2.5 text-[14px] leading-relaxed whitespace-pre-wrap">{text}</div>
        {meta && <div className="text-[10.5px] text-faint mt-1 text-right">{meta}</div>}
      </div>
    </div>
  );
}

function Thinking({ since, hasSteps }: { since: number; hasSteps: boolean }) {
  const [s, setS] = React.useState(0);
  React.useEffect(() => { const id = setInterval(() => setS(Math.max(0, Math.round((Date.now() - since) / 1000))), 1000); return () => clearInterval(id); }, [since]);
  return (
    <div className="flex items-center gap-2 text-[13px] text-muted">
      <span className="relative flex h-2 w-2"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-60" /><span className="relative inline-flex rounded-full h-2 w-2 bg-accent" /></span>
      {hasSteps ? "Working…" : "Starting…"} <span className="text-faint tnum">{Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}</span>
    </div>
  );
}

function Steps({ steps, running }: { steps: string[]; running?: boolean }) {
  if (!steps.length) return null;
  return (
    <ul className="mb-2.5 space-y-0.5">
      {steps.map((s, i) => (
        <li key={i} className={cn("text-[12px] flex items-center gap-1.5", running && i === steps.length - 1 ? "text-muted" : "text-faint")}>
          <span className="h-1 w-1 rounded-full bg-line-strong shrink-0" /> <span className="truncate">{s}</span>
        </li>
      ))}
    </ul>
  );
}

function Answer({ m }: { m: ChatMessage }) {
  const [showWork, setShowWork] = React.useState(false);
  const evidence = useEvidence();
  const sources = m.sources || [];
  const steps = (m.tool_log || []).map((t) => t.label);
  const notFound = m.checks?.not_found || [];
  const secs = m.elapsed_ms ? Math.round(m.elapsed_ms / 1000) : null;
  return (
    <div>
      <Md text={m.text} sources={sources} />
      {(m.drafts || []).map((d) => <div key={d.draft_id} className="mt-3"><DraftCard draft={d} showProperty={false} /></div>)}
      {(m.actions || []).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {(m.actions || []).map((a, i) => (
            <span key={i} className="text-[11.5px] px-2 h-6 inline-flex items-center rounded-full bg-good-soft text-good border border-good/25">
              {a.type === "todo_added" ? `Added to-do: ${a.title}` : a.type === "todo_closed" ? "Closed a to-do" : a.type === "note" ? "Recorded on the file" : a.type === "remember" ? "Stored as a standing instruction" : a.type}
            </span>
          ))}
        </div>
      )}
      {m.verification && <div className="mt-3"><Checked v={m.verification} figures={notFound} /></div>}
      {!m.verification && notFound.length > 0 && (
        <div className="mt-2.5 text-[12px] text-high bg-high-soft/60 border border-high/20 rounded-lg px-2.5 py-1.5">
          {notFound.length === 1 ? "One figure" : `${notFound.length} figures`} above ({notFound.slice(0, 4).join(", ")}) {notFound.length === 1 ? "does" : "do"} not appear word for word in the records I read — {notFound.length === 1 ? "it is" : "they are"} either my own arithmetic or should be checked.
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint">
        {sources.length > 0 && (
          <span className="flex flex-wrap items-center gap-1">
            Sources
            {sources.slice(0, 8).map((s) => (
              <button key={s.index} type="button" onClick={() => evidence.open({ chunk_id: s.chunk_id })} title={`${s.citation}${s.date ? " · " + s.date : ""}`}
                className="inline-flex items-center gap-1 h-5 pl-1 pr-2 rounded-full border border-line bg-elev hover:border-accent/40 text-[11px] text-muted max-w-[220px]">
                <span className="cite !m-0 !h-4 !min-w-4 !text-[10px] pointer-events-none">{s.index}</span><span className="truncate">{s.display_name || s.citation}</span>
              </button>
            ))}
            {sources.length > 8 && <span>+{sources.length - 8}</span>}
          </span>
        )}
        {steps.length > 0 && <button type="button" onClick={() => setShowWork((v) => !v)} className="hover:text-fg underline-offset-2 hover:underline">{showWork ? "Hide" : "What I looked at"} ({steps.length})</button>}
        {secs != null && <span className="tnum">{secs}s</span>}
      </div>
      {showWork && <div className="mt-1.5"><Steps steps={steps} /></div>}
    </div>
  );
}
