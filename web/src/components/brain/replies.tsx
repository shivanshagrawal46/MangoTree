"use client";

/* The emails that are waiting for a reply — one line each, nothing else.
   Who wrote, about what, which property, how long it has waited, and whether
   the written reply can go as it is. A line opens the reply on its own page. */

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Reply } from "lucide-react";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui";
import { Chip, PageBody, PageHead, Tag, TD, TH, type Tone } from "@/components/ds";
import { PropertyMark, usePropertyColours } from "@/components/property-mark";
import { DraftCard } from "@/components/brain/draft";
import { cn, fmtDate } from "@/lib/utils";
import { OWNER_LABEL, errText, type Draft } from "@/lib/brain";

type State = { label: string; cls: string; tone: Tone; rank: number };

export function replyState(d: Draft): State {
  // A call after the draft was written changed what it says (see brain/calls.py).
  if (d.stale?.kind === "call") return { label: "Changed by a call — rewrite", cls: "bg-critical-soft text-critical", tone: "red", rank: 0 };
  // The reading-over of the lists found it overtaken by a later email (see brain/sweep.py).
  if (d.stale?.kind === "record") return { label: "Out of date — rewrite", cls: "bg-critical-soft text-critical", tone: "red", rank: 0 };
  if (d.stale?.kind === "not_needed") return { label: "Looks already answered", cls: "bg-high-soft text-high", tone: "amber", rank: 0 };
  if (d.stale?.why) return { label: "Partly answered — rewrite", cls: "bg-critical-soft text-critical", tone: "red", rank: 0 };
  if (!d.verification?.ran) return { label: "Not yet checked", cls: "bg-high-soft text-high", tone: "amber", rank: 1 };
  const n = d.verification.open?.length || 0;
  if (n > 0) return { label: `${n} point${n === 1 ? "" : "s"} to check`, cls: "bg-high-soft text-high", tone: "amber", rank: 1 };
  return { label: "Ready to send", cls: "bg-good-soft text-good", tone: "green", rank: 2 };
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

/* An email's line is given room above and below: these are read one by one, not scanned like figures. */
const ROOM = "h-12";

/** Where one waiting reply opens: its own page. */
export const replyHref = (id: string) => `/replies/${id}`;

export function ReplyList({ drafts, limit }: { drafts: Draft[]; limit?: number }) {
  const router = useRouter();
  const colourOf = usePropertyColours();
  const rows = sortReplies(drafts).slice(0, limit || drafts.length);
  if (!rows.length) return <div className="h-8 px-4 flex items-center text-[13px] text-faint">No email is waiting for a reply.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1040px] table-fixed border-collapse">
        <colgroup><col style={{ width: 176 }} /><col /><col style={{ width: 200 }} /><col style={{ width: 92 }} /><col style={{ width: 224 }} /><col style={{ width: 168 }} /></colgroup>
        <thead><tr><th className={TH}>From</th><th className={TH}>Email</th><th className={TH}>Property</th><th className={TH}>Waiting</th><th className={TH}>Reply</th><th className={TH}>With</th></tr></thead>
        <tbody>
          {rows.map((d) => {
            const s = replyState(d);
            const from = d.in_reply_to?.from_name || d.to?.[0]?.name || "—";
            const late = daysSince(d.in_reply_to?.date) >= 3;
            const props = d.properties || [];
            return (
              <tr key={d.draft_id} onClick={() => router.push(replyHref(d.draft_id))} className="cursor-pointer hover:bg-hover transition-colors">
                <td className={cn(TD, ROOM)}>{from}</td>
                <td className={cn(TD, ROOM)} title={d.why || undefined}>
                  <Link href={replyHref(d.draft_id)} onClick={(e) => e.stopPropagation()} className="text-fg hover:underline">{d.in_reply_to?.subject || d.subject}</Link>
                  {d.why && <span className="text-faint"> · {d.why}</span>}
                </td>
                <td className={cn(TD, ROOM)}>{props.length ? <Chip icon={<PropertyMark label={props[0].name} colour={colourOf(props[0].property_id)} />}>{props.map((p) => p.name).join(", ")}</Chip> : <span className="text-faint">General</span>}</td>
                <td className={cn(TD, ROOM, "tnum text-muted", late && "text-critical")} title={d.in_reply_to?.date ? `Received ${fmtDate(d.in_reply_to.date, "EEE d MMM, HH:mm")}` : undefined}>{waited(d.in_reply_to?.date)}</td>
                <td className={cn(TD, ROOM)}><Tag tone={s.tone}>{s.label}</Tag></td>
                {/* Whose list it is on: the person it is written for, or the people it was handed to with "Send to team". */}
                <td className={cn(TD, ROOM, !d.team?.names?.length && "text-muted")} title={d.team?.names?.length ? `Sent to the team by ${d.team.by_name || "Rakesh Sir"}` : undefined}>
                  {d.team?.names?.length ? d.team.names.join(", ") : OWNER_LABEL[d.sender || "rakesh"] || d.sender}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* One waiting reply on its own page: the email that came, the reply as written, and what to do with it. */
export function ReplyPage({ draftId }: { draftId: string }) {
  const router = useRouter();
  const q = useQuery({ queryKey: ["brain", "draft", draftId], queryFn: () => api.get<Draft>(`/brain/drafts/${draftId}`) });
  const icon = <Reply size={16} strokeWidth={1.6} />;
  const d = q.data;
  return (
    <div>
      <PageHead icon={icon} crumbs={[{ label: "To reply", href: "/replies" }, { label: d ? (d.in_reply_to?.subject || d.subject || "Reply") : "…" }]}>
        {d && <Tag tone={replyState(d).tone}>{replyState(d).label}</Tag>}
      </PageHead>
      <PageBody>
        <div className="px-6 pt-4 pb-10 max-w-[1040px]">
          {q.isError ? <div className="text-[13px] text-muted">That reply could not be opened: {errText(q.error)} <Link href="/replies" className="text-accent hover:underline">Back to To reply</Link></div>
            : !d ? <div className="space-y-4"><Skeleton className="h-8 w-96" /><Skeleton className="h-40" /><Skeleton className="h-72" /></div>
              : <DraftCard draft={d} onChange={(next) => { if (next.status !== "ready") router.push("/replies"); }} />}
        </div>
      </PageBody>
    </div>
  );
}
