"use client";

/* An email the AI wrote on the team's behalf, ready to send. Shown under an
   answer's next steps and on any task that carries one. Two actions only:
   copy the whole thing, or open it prefilled in the reader's mail app. The
   text is editable in place before either, because the AI drafts and a
   person sends. */

import * as React from "react";
import { Copy, Mail, ChevronDown, Check } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { EmailDraft } from "@/lib/types";

/* The clipboard API exists only on HTTPS or localhost. The app is reached over
   plain HTTP on the droplet's address, so it silently failed there (2026-09-11).
   Fall back to the old selection-based copy, which works on any origin. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard && (window as any).isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.left = "-9999px";
    document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

export function EmailDraftCard({ draft, defaultOpen = false, compact }: { draft: EmailDraft; defaultOpen?: boolean; compact?: boolean }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const [subject, setSubject] = React.useState(draft.subject || "");
  const [body, setBody] = React.useState(draft.body || "");
  const [copied, setCopied] = React.useState(false);
  const to = draft.to_email ? `${draft.to} <${draft.to_email}>` : draft.to;

  const copy = async () => {
    const ok = await copyText(`To: ${to}\nSubject: ${subject}\n\n${body}`);
    if (ok) { setCopied(true); setTimeout(() => setCopied(false), 1500); toast.success("Email copied — paste it into your mail app"); }
    else toast.error("Could not copy automatically — select the text and press Ctrl+C");
  };
  /* mailto links break past ~2,000 characters in most mail apps. For a long body,
     copy it and open the mail app with the address and subject only. */
  const openMail = async () => {
    const full = `mailto:${encodeURIComponent(draft.to_email || "")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    if (full.length <= 1900) { window.location.href = full; return; }
    const ok = await copyText(body);
    window.location.href = `mailto:${encodeURIComponent(draft.to_email || "")}?subject=${encodeURIComponent(subject)}`;
    toast.message(ok ? "Body copied — paste it into the email that opens" : "Long email — copy the text and paste it into the email that opens");
  };

  return (
    <div className={cn("rounded-xl border border-line bg-elev overflow-hidden", compact ? "text-xs" : "text-[13px]")}>
      <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-sunken/60 transition">
        <Mail size={13} className="text-accent shrink-0" />
        <span className="font-medium truncate flex-1">
          <span className="text-muted">To {draft.to}{draft.to_email ? "" : " (no address on file — add it)"} · </span>{subject || "(no subject)"}
        </span>
        <span className="text-[10px] text-faint uppercase tracking-wide shrink-0">from {draft.from}</span>
        <ChevronDown size={13} className={cn("shrink-0 text-faint transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-line px-3 py-2.5 space-y-2 bg-bg">
          <div className="flex items-center gap-2 text-xs">
            <span className="text-faint w-14 shrink-0">To</span>
            <span className="tnum truncate">{to}</span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-faint w-14 shrink-0">Subject</span>
            <input value={subject} onChange={(e) => setSubject(e.target.value)} className="flex-1 bg-transparent border-b border-line focus:border-accent outline-none py-0.5" />
          </div>
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={Math.min(18, Math.max(6, body.split("\n").length + 1))}
            className="w-full rounded-lg border border-line bg-elev px-3 py-2 text-[13px] leading-relaxed focus:border-accent outline-none resize-y font-[inherit]" />
          <div className="flex items-center gap-2">
            <Button size="sm" variant="primary" onClick={copy}>{copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "Copied" : "Copy email"}</Button>
            <Button size="sm" variant="soft" onClick={openMail}><Mail size={13} /> Open in mail app</Button>
            <span className="text-[11px] text-faint ml-auto">Written by the AI from the records — read once before sending.</span>
          </div>
        </div>
      )}
    </div>
  );
}

export function EmailDrafts({ drafts, title = "Emails ready to send", compact, print }: { drafts?: EmailDraft[] | null; title?: string; compact?: boolean; print?: boolean }) {
  if (!drafts || drafts.length === 0) return null;
  if (print) {
    return (
      <div className="mx-5 mb-3">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-muted mb-1.5">{title} · {drafts.length}</div>
        <div className="space-y-3">{drafts.map((d, i) => (
          <div key={i} className="rounded-xl border border-line bg-bg px-4 py-3 text-[13px] avoid-break">
            <div className="text-xs text-muted">To <span className="text-fg">{d.to}{d.to_email ? ` <${d.to_email}>` : ""}</span> · from {d.from}</div>
            <div className="font-semibold mt-0.5">{d.subject}</div>
            <div className="mt-2 whitespace-pre-line leading-relaxed">{d.body}</div>
          </div>
        ))}</div>
      </div>
    );
  }
  return (
    <div className="mx-5 mb-3 rounded-xl border border-accent/25 bg-accent-soft/40 px-3 py-2">
      <div className="text-xs font-semibold text-accent flex items-center gap-1 mb-1.5"><Mail size={12} /> {title} <span className="text-faint font-normal">· {drafts.length}</span></div>
      <div className="space-y-1.5">{drafts.map((d, i) => <EmailDraftCard key={i} draft={d} defaultOpen={drafts.length === 1} compact={compact} />)}</div>
    </div>
  );
}
