"use client";

/* Add documents to a property. The property is known from where the user is,
   so the file skips the "which property?" step and goes straight through
   reading (OCR if scanned), chunking, context, questions, embedding and the
   timeline — the same process as every other document. Duplicates are
   recognised by content and reported, never stored twice. Who uploaded and when
   is recorded on the document and shown in Files.

   Two faces of the same logic: `UploadBox` (a card, in the Files tab) and
   `UploadButton` (a small button that opens the same box in a dialog — beside
   "Remember" in the property chat). */

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, Dialog, DialogContent } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { cn } from "@/lib/utils";

type Phase = "idle" | "uploading" | "processing" | "done";

function useUploader(pid: string, onDone?: () => void) {
  const qc = useQueryClient();
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [status, setStatus] = React.useState<string>("");
  const [results, setResults] = React.useState<any[]>([]);
  const [failed, setFailed] = React.useState<any[]>([]);
  const busy = phase === "uploading" || phase === "processing";

  const send = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length || busy) return;
    setPhase("uploading"); setResults([]); setFailed([]); setStatus(`Uploading ${list.length} file${list.length > 1 ? "s" : ""}…`);
    const form = new FormData();
    list.forEach((f) => form.append("files", f, f.name));
    try {
      const out = await api.upload<{ results: any[]; failed: any[]; job_id: string | null }>(`/properties/${pid}/upload`, form);
      setResults(out.results); setFailed(out.failed);
      qc.invalidateQueries({ queryKey: ["files", pid] }); onDone?.();
      if (out.job_id) {
        setPhase("processing"); setStatus("Stored. Reading the document…");
        await new Promise<void>((resolve) => subscribeJob(out.job_id!, (ev) => {
          if (ev.kind === "status") setStatus(ev.data?.text || "Working…");
          if (ev.kind === "error") setStatus(`Failed: ${ev.data?.error || "unknown"}`);
        }, resolve));
        for (const k of [["property", pid], ["docs", pid], ["timeline", pid], ["tasks"], ["wes-agenda", pid], ["files", pid]]) qc.invalidateQueries({ queryKey: k });
        setStatus("Done — searchable in chat, on the timeline, and in Files.");
      } else {
        setStatus(out.results.length ? "Nothing new to read — see below." : "");
      }
      setPhase("done"); onDone?.();
    } catch (e: any) {
      setPhase("done"); setStatus(`Upload failed: ${e?.message || e}`);
    }
  };
  return { phase, busy, status, results, failed, send };
}

const tone = (s: string) => (s === "new" ? "good" : s === "duplicate" ? "neutral" : "accent");
const label = (s: string) => (s === "new" ? "New" : s === "duplicate" ? "Already here" : "Also filed here now");

function Results({ results, failed }: { results: any[]; failed: any[] }) {
  const { open } = useEvidence();
  if (!results.length && !failed.length) return null;
  return (
    <ul className="border-t border-line px-5 py-3 space-y-1.5">
      {results.map((r) => (
        <li key={r.sha256} className="flex items-start gap-2 text-xs">
          <Badge tone={tone(r.status) as any}>{label(r.status)}</Badge>
          <span className="font-medium truncate">{r.filename}</span>
          <span className="text-muted flex-1 min-w-0">{r.message}{r.warnings?.length ? ` ${r.warnings.join(" ")}` : ""}</span>
          <button onClick={() => open({ sha: r.sha256 })} className="text-accent hover:underline shrink-0">open</button>
        </li>
      ))}
      {failed.map((f, i) => <li key={i} className="flex items-start gap-2 text-xs"><Badge tone="critical">Refused</Badge><span className="font-medium">{f.filename}</span><span className="text-critical">{f.error}</span></li>)}
    </ul>
  );
}

export function UploadBox({ pid, onDone, className }: { pid: string; onDone?: () => void; className?: string }) {
  const u = useUploader(pid, onDone);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [drag, setDrag] = React.useState(false);
  return (
    <Card className={cn("transition", drag && "ring-2 ring-accent", className)}>
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); u.send(e.dataTransfer.files); }}
        className="p-5 flex flex-col sm:flex-row sm:items-center gap-4"
      >
        <div className="h-11 w-11 rounded-2xl bg-accent-soft text-accent grid place-items-center shrink-0"><Upload size={18} /></div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold">Add documents to this property</div>
          <div className="text-xs text-muted mt-0.5">Drop files here or choose them. Filed under this property, read (OCR if scanned), made searchable and placed on the timeline. Duplicates are recognised and never stored twice. Your name and the date are recorded on the document.</div>
          {u.status && <div className={cn("text-xs mt-2 flex items-center gap-1.5", u.busy ? "text-accent" : "text-muted")}>{u.busy && <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />}{u.status}</div>}
        </div>
        <input ref={inputRef} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files) u.send(e.target.files); e.target.value = ""; }} />
        <Button onClick={() => inputRef.current?.click()} disabled={u.busy}>{u.busy ? "Working…" : "Choose files"}</Button>
      </div>
      <Results results={u.results} failed={u.failed} />
    </Card>
  );
}

/* The button beside "Remember" in the property chat. */
export function UploadButton({ pid }: { pid: string }) {
  const [open, setOpen] = React.useState(false);
  const u = useUploader(pid);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [drag, setDrag] = React.useState(false);
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}><Upload size={13} /> Add document</Button>
      <Dialog open={open} onOpenChange={(o) => { if (!u.busy) setOpen(o); }}>
        <DialogContent title="Add documents to this property" description="Read, made searchable, placed on the timeline and the ledger — same as every other document. Your name and today's date are recorded on it.">
          <div
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); u.send(e.dataTransfer.files); }}
            onClick={() => !u.busy && inputRef.current?.click()}
            className={cn("mt-4 rounded-2xl border-2 border-dashed px-6 py-8 text-center cursor-pointer transition", drag ? "border-accent bg-accent-soft/40" : "border-line hover:border-accent/60 hover:bg-sunken/60")}
          >
            <Upload size={22} className="mx-auto text-accent" />
            <div className="text-sm font-medium mt-2">{u.busy ? "Working…" : "Drop files here, or click to choose"}</div>
            <div className="text-xs text-muted mt-1">PDF, Word, Excel, images, text. Duplicates are recognised and never stored twice.</div>
            <input ref={inputRef} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files) u.send(e.target.files); e.target.value = ""; }} />
          </div>
          {u.status && <div className={cn("text-xs mt-3 flex items-center gap-1.5", u.busy ? "text-accent" : "text-muted")}>{u.busy && <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />}{u.status}</div>}
          <div className="-mx-6 mt-3"><Results results={u.results} failed={u.failed} /></div>
          <div className="mt-4 flex justify-end"><Button variant="ghost" onClick={() => setOpen(false)} disabled={u.busy}>{u.phase === "done" ? "Close" : "Cancel"}</Button></div>
        </DialogContent>
      </Dialog>
    </>
  );
}
