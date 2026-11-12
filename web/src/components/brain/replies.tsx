"use client";

/* The emails that are waiting for a reply — one line each, nothing else.
   Who wrote, about what, which property, how long it has waited, and whether
   the written reply can go as it is. Click a line and the reply opens. */

import * as React from "react";
import { Dialog, DialogContent } from "@/components/ui";
import { DraftCard } from "@/components/brain/draft";
import { cn, fmtDate } from "@/lib/utils";
import type { Draft } from "@/lib/brain";

type State = { label: string; cls: string; rank: number };

export function replyState(d: Draft): State {
  if (d.stale?.why) return { label: "Partly answered — rewrite", cls: "bg-critical-soft text-critical", rank: 0 };
  if (!d.verification?.ran) return { label: "Not yet checked", cls: "bg-high-soft text-high", rank: 1 };
  const n = d.verification.open?.length || 0;
  if (n > 0) return { label: `${n} point${n === 1 ? "" : "s"} to check`, cls: "bg-high-soft text-high", rank: 1 };
  return { label: "Ready to send", cls: "bg-good-soft text-good", rank: 2 };
}

function daysSince(date?: string) {
  return date ? Math.floor((Date.now() - new Date(date).getTime()) / 86_400_000) : 0;
}

function waited(date?: string) {
  if (!date) return "";
  const days = daysSince(date);
  return days <= 0 ? "today" : days === 1 ? "1 day" : `${days} days`;
}

/** Waiting replies, always newest email first, down to the oldest. One order on every screen. */
export function sortReplies(drafts: Draft[]) {
  const t = (d: Draft) => new Date(d.in_reply_to?.date || d.created_at).getTime();
  return [...drafts].sort((a, b) => t(b) - t(a));
}

export function ReplyList({ drafts, limit }: { drafts: Draft[]; limit?: number }) {
  const [open, setOpen] = React.useState<Draft | null>(null);
  const rows = sortReplies(drafts).slice(0, limit || drafts.length);
  if (!rows.length) return <div className="px-5 py-6 text-[13.5px] text-muted">No email is waiting for a reply.</div>;
  return (
    <>
      <div className="hidden md:grid grid-cols-[170px_minmax(0,1fr)_150px_84px_176px] gap-4 px-5 py-2 text-[10.5px] font-semibold uppercase tracking-[0.08em] text-faint border-b border-line">
        <div>From</div><div>Email</div><div>Property</div><div>Waiting</div><div>Reply</div>
      </div>
      <ul className="divide-y divide-line">
        {rows.map((d) => {
          const s = replyState(d);
          const from = d.in_reply_to?.from_name || d.to?.[0]?.name || "—";
          const late = daysSince(d.in_reply_to?.date) >= 3;
          return (
            <li key={d.draft_id}>
              <button type="button" onClick={() => setOpen(d)} className="w-full text-left grid md:grid-cols-[170px_minmax(0,1fr)_150px_84px_176px] gap-x-4 gap-y-1 items-center px-5 py-3 hover:bg-sunken/60 transition">
                <div className="text-[13.5px] font-semibold truncate">{from}</div>
                <div className="min-w-0">
                  <div className="text-[13.5px] truncate">{d.in_reply_to?.subject || d.subject}</div>
                  {d.why && <div className="text-[12px] text-muted truncate">{d.why}</div>}
                </div>
                <div className="text-[12.5px] text-muted truncate">{(d.properties || []).map((p) => p.name).join(", ") || "General"}</div>
                <div className={cn("text-[12.5px] tnum", late ? "text-critical font-medium" : "text-muted")} title={d.in_reply_to?.date ? `Received ${fmtDate(d.in_reply_to.date, "EEE d MMM, HH:mm")}` : undefined}>{waited(d.in_reply_to?.date)}</div>
                <div><span className={cn("inline-flex items-center h-6 px-2.5 rounded-full text-[11.5px] font-medium whitespace-nowrap", s.cls)}>{s.label}</span></div>
              </button>
            </li>
          );
        })}
      </ul>
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent wide className="max-h-[90vh] overflow-y-auto" title="Reply">
          {open && <DraftCard draft={open} onChange={(next) => { if (next.status !== "ready") setOpen(null); }} />}
        </DialogContent>
      </Dialog>
    </>
  );
}
