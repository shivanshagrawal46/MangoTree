"use client";

/* The evidence drawer — every number, claim and citation opens the exact source.
   One context, one drawer, mounted once in Providers; anything can call
   `openEvidence({chunk_id})` or `openEvidence({sha})`. */

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, FileText, Mail, X, Calendar, Layers } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Button, Skeleton } from "@/components/ui";
import { fmtDate, PLACEMENT_LABEL, propertyLabel, cn } from "@/lib/utils";

type Target = { chunk_id?: string; sha?: string; highlight?: string } | null;
const Ctx = React.createContext<{ open: (t: Target) => void }>({ open: () => {} });
export const useEvidence = () => React.useContext(Ctx);

export function EvidenceProvider({ children }: { children: React.ReactNode }) {
  const [target, setTarget] = React.useState<Target>(null);
  return (
    <Ctx.Provider value={{ open: setTarget }}>
      {children}
      <DialogPrimitive.Root open={!!target} onOpenChange={(o) => !o && setTarget(null)}>
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/20" />
          <DialogPrimitive.Content className="fixed z-50 right-0 top-0 h-full w-full max-w-2xl bg-elev border-l border-line shadow-[var(--shadow)] focus:outline-none flex flex-col data-[state=open]:animate-in data-[state=open]:slide-in-from-right duration-200">
            <DialogPrimitive.Title className="sr-only">Evidence</DialogPrimitive.Title>
            {target && <EvidenceBody target={target} onClose={() => setTarget(null)} />}
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </Ctx.Provider>
  );
}

function Highlight({ text, needle }: { text: string; needle?: string }) {
  if (!needle || needle.length < 4) return <>{text}</>;
  const i = text.toLowerCase().indexOf(needle.toLowerCase());
  if (i < 0) return <>{text}</>;
  return <>{text.slice(0, i)}<mark className="bg-high-soft text-fg rounded px-0.5">{text.slice(i, i + needle.length)}</mark>{text.slice(i + needle.length)}</>;
}

function EvidenceBody({ target, onClose }: { target: NonNullable<Target>; onClose: () => void }) {
  const chunkQ = useQuery({ queryKey: ["evidence", "chunk", target.chunk_id], queryFn: () => api.get<any>(`/evidence/chunk/${target.chunk_id}`), enabled: !!target.chunk_id });
  const sha = target.sha || chunkQ.data?.chunk?.artifact_sha;
  const artQ = useQuery({ queryKey: ["evidence", "artifact", sha], queryFn: () => api.get<any>(`/evidence/artifact/${sha}`), enabled: !!sha });
  const a = artQ.data?.artifact;
  const chunk = chunkQ.data?.chunk;
  const isEmail = a?.source_type === "email";

  return (
    <>
      <div className="flex items-start gap-3 px-5 py-4 border-b border-line">
        <div className="h-9 w-9 rounded-xl bg-sunken grid place-items-center shrink-0">{isEmail ? <Mail size={16} /> : <FileText size={16} />}</div>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold truncate">{a?.name || <Skeleton className="h-4 w-48" />}</div>
          <div className="text-xs text-muted mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
            {a?.date && <span>{fmtDate(a.date)}</span>}
            {a?.from && <span>from {a.from}</span>}
            {a?.placement && <Badge tone={a.placement === "property" ? "accent" : a.placement === "unplaced" ? "high" : "info"}>{PLACEMENT_LABEL[a.placement] || a.placement}</Badge>}
            {(a?.property_ids || []).map((p: string) => <Badge key={p}>{propertyLabel(p)}</Badge>)}
          </div>
        </div>
        {sha && <a href={`/api/evidence/original/${sha}`} target="_blank" rel="noreferrer"><Button size="sm" variant="soft"><ExternalLink size={13} /> Original</Button></a>}
        <button onClick={onClose} className="h-8 w-8 rounded-lg grid place-items-center text-muted hover:bg-sunken"><X size={16} /></button>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
        {a && sha && <OriginalViewer sha={sha} artifact={a} />}
        {chunk && (
          <section>
            <div className="text-[11px] uppercase tracking-wide text-faint mb-1.5 flex items-center gap-2"><Layers size={12} /> Cited passage {chunk.source_ref ? `· ${chunk.source_ref}` : ""}</div>
            {chunk.tier1 && <div className="text-xs text-muted italic mb-2 border-l-2 border-accent/40 pl-2">{chunk.tier1}</div>}
            <div className="text-[13.5px] leading-relaxed whitespace-pre-wrap bg-sunken/60 rounded-xl p-3 border border-line"><Highlight text={chunk.text} needle={target.highlight} /></div>
          </section>
        )}
        {artQ.data?.events?.length > 0 && (
          <section>
            <div className="text-[11px] uppercase tracking-wide text-faint mb-1.5 flex items-center gap-2"><Calendar size={12} /> Timeline events from this document</div>
            <ul className="space-y-1">{artQ.data.events.map((e: any) => (
              <li key={e.event_id} className="text-xs flex gap-2"><span className="text-faint tnum w-20 shrink-0">{fmtDate(e.occurred_at, "yyyy-MM-dd")}</span><Badge>{e.event_type}</Badge><span className="truncate">{e.title}</span></li>
            ))}</ul>
          </section>
        )}
        {a && (
          <section>
            <div className="text-[11px] uppercase tracking-wide text-faint mb-1.5">{isEmail ? "Full email" : "Full document text"}</div>
            <div className={cn("text-[13px] leading-relaxed whitespace-pre-wrap", !chunk && "bg-sunken/60 rounded-xl p-3 border border-line")}>
              {isEmail ? (a.body || "(no body)") : (a.text ? a.text.slice(0, 60000) : "(no extractable text — open the original)")}
            </div>
          </section>
        )}
        {artQ.data?.carried_by?.length > 0 && (
          <section>
            <div className="text-[11px] uppercase tracking-wide text-faint mb-1.5">Carried by</div>
            {artQ.data.carried_by.map((p: any) => <div key={p.sha256} className="text-xs text-muted">{fmtDate(p.date)} — {p.subject} <span className="text-faint">from {p.from}</span></div>)}
          </section>
        )}
        {artQ.isLoading && <Skeleton className="h-40" />}
      </div>
    </>
  );
}

/** The exact original, in place: PDFs and images render inline; everything else
 *  gets the real file one click away. Collapsed by default for emails, whose
 *  body is already shown as text. */
export function OriginalViewer({ sha, artifact, defaultOpen }: { sha: string; artifact: any; defaultOpen?: boolean }) {
  const name: string = artifact?.filename || artifact?.name || "";
  const ext = (artifact?.extension || ("." + (name.split(".").pop() || ""))).toLowerCase();
  const isPdf = ext === ".pdf";
  const isImage = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"].includes(ext);
  const isEmail = artifact?.source_type === "email";
  const [open, setOpen] = React.useState(defaultOpen ?? (isPdf || isImage));
  const src = `/api/evidence/original/${sha}`;
  const label = isPdf ? "PDF" : isImage ? "Image" : ext ? ext.replace(".", "").toUpperCase() : isEmail ? "Email (.eml)" : "File";
  return (
    <section className="rounded-2xl border border-line overflow-hidden">
      <button onClick={() => setOpen((v) => !v)} className="w-full flex items-center justify-between px-4 py-2.5 bg-sunken/60 text-xs">
        <span className="flex items-center gap-2 font-medium"><FileText size={13} /> Original document <Badge>{label}</Badge>{artifact?.size ? <span className="text-faint">{(artifact.size / 1024).toFixed(0)} KB</span> : null}</span>
        <span className="flex items-center gap-2">
          <a href={src} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-accent hover:underline flex items-center gap-1"><ExternalLink size={11} /> open in tab</a>
          <a href={src} download={name || sha} onClick={(e) => e.stopPropagation()} className="text-accent hover:underline">download</a>
          <span className="text-faint">{open ? "hide" : "show"}</span>
        </span>
      </button>
      {open && (
        <div className="bg-bg">
          {isPdf && <iframe title={name} src={`${src}#toolbar=1&view=FitH`} className="w-full h-[70vh] border-0" />}
          {isImage && <img src={src} alt={name} className="max-h-[70vh] w-auto mx-auto block" />}
          {!isPdf && !isImage && (
            <div className="px-4 py-6 text-center text-xs text-muted">
              {isEmail ? "The email body is shown below in full. Download the .eml to open it in Outlook or Gmail exactly as received." :
                `This ${label} opens in its own application — download it to view the exact original. The extracted text is shown below.`}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/** Renders [#N] citations inside text as clickable chips. */
export function Cited({ text, sources, className }: { text: string; sources: { index: number; chunk_id: string }[]; className?: string }) {
  const { open } = useEvidence();
  const parts = text.split(/(\[#\d+\])/g);
  return (
    <span className={className}>
      {parts.map((p, i) => {
        const m = p.match(/^\[#(\d+)\]$/);
        if (!m) return <React.Fragment key={i}>{p}</React.Fragment>;
        const idx = Number(m[1]);
        const src = sources.find((s) => s.index === idx);
        return <button key={i} className="cite" title={src ? "Open source" : "Source not on pad"} onClick={() => src && open({ chunk_id: src.chunk_id })}>{idx}</button>;
      })}
    </span>
  );
}
