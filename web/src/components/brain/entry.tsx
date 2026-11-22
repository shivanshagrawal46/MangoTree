"use client";

/* RKB's part in what the contractor enters in the portal (2026-11-22): a change order the contractor has
   sent in waits here until Rakesh Sir or Manjunath Sir agrees to pay for it, or says why not. Until then
   it cannot be put on a bill. Shown at the top of the Bills page, and only when something is waiting. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { cn, fmtDate } from "@/lib/utils";

type FileRec = { file_id: number; role: string; name: string; is_photo: boolean };
type Waiting = {
  co_id: number; number: string; property: string; property_id: string; title: string; group?: string; days?: number; why: string; cost: number; price: number;
  lines: { title: string; kind: string; cost: number; price: number }[]; approved_by: string; approved_on: string; sent_at?: string | null; by_name?: string; files: FileRec[];
};
const usd = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const BTN = "inline-flex h-[28px] items-center whitespace-nowrap rounded-[5px] border px-2.5 text-[12.5px] font-medium";

export function EntryChangeOrders() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brain", "entry-cos"], queryFn: () => api.get<{ waiting: Waiting[]; can_decide: boolean }>("/brain/entry/change-orders"), refetchInterval: 120_000 });
  const [open, setOpen] = React.useState<number | null>(null);
  const [no, setNo] = React.useState<number | null>(null);
  const [why, setWhy] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const rows = q.data?.waiting || [];
  if (!rows.length) return null;
  const decide = async (c: Waiting, agree: boolean) => {
    setBusy(true);
    try {
      await api.post(`/brain/entry/change-orders/${c.co_id}/decide`, { agree, why });
      toast.success(agree ? `Agreed. The contractor can now bill ${c.number}.` : "Recorded. The contractor sees the reason.");
      setNo(null); setWhy(""); qc.invalidateQueries({ queryKey: ["brain"] });
    } catch (e) { toast.error(e instanceof Error ? e.message : "Could not record it"); }
    setBusy(false);
  };
  return (
    <section className="mt-4 overflow-hidden rounded-md border border-l-[3px] border-high/25 border-l-high bg-elev">
      <div className="border-b border-line px-4 py-2.5 text-[14px] font-semibold">Change orders to agree <span className="ml-1.5 rounded-[3px] bg-sunken px-1.5 py-px text-[12px] font-medium text-muted">{rows.length}</span>
        <span className="ml-2 text-[12.5px] font-normal text-muted">Sent in by the contractor. Each can be billed only after RKB agrees.</span></div>
      {rows.map((c, i) => (
        <div key={c.co_id} className={cn("px-4 py-3", i > 0 && "border-t border-line")}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <button type="button" onClick={() => setOpen(open === c.co_id ? null : c.co_id)} className="min-w-0 text-left">
              <div className="truncate text-[13.5px] font-semibold">{c.property} · {c.title}</div>
              <div className="text-[12.5px] text-muted">{c.number} · <span className="tnum">{usd(c.cost)}</span> cost to RKB · <span className="tnum">{usd(c.price)}</span> to the homeowner · approved in Buildertrend by {c.approved_by} on {fmtDate(c.approved_on, "d MMM")} · sent {fmtDate(c.sent_at, "d MMM, HH:mm")}{c.by_name ? ` by ${c.by_name}` : ""}</div>
            </button>
            {q.data?.can_decide ? (
              <div className="flex items-center gap-2">
                <button type="button" disabled={busy} onClick={() => { setNo(no === c.co_id ? null : c.co_id); setWhy(""); }} className={cn(BTN, "border-line-strong bg-elev text-fg hover:bg-sunken")}>Do not agree</button>
                <button type="button" disabled={busy} onClick={() => decide(c, true)} className={cn(BTN, "border-accent bg-accent text-white hover:opacity-90")}>Agree</button>
              </div>
            ) : <span className="text-[12px] text-muted">Rakesh Sir or Manjunath Sir decides</span>}
          </div>
          {no === c.co_id && (
            <div className="mt-2 flex gap-2">
              <input autoFocus value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why RKB does not agree (the contractor sees this)" className="h-8 flex-1 rounded-[5px] border border-line-strong bg-elev px-2.5 text-[13px] outline-none focus:border-accent" />
              <button type="button" disabled={busy || why.trim().length < 5} onClick={() => decide(c, false)} className={cn(BTN, "h-8 border-line-strong bg-elev disabled:opacity-50")}>Record</button>
            </div>
          )}
          {open === c.co_id && (
            <div className="mt-2.5 rounded-md bg-sunken/60 px-3.5 py-2.5 text-[13px] leading-relaxed">
              <div className="whitespace-pre-line">{c.why}</div>
              <ul className="mt-1.5">{c.lines.map((l, n) => <li key={n} className="flex justify-between gap-4 border-t border-line/70 py-1"><span>{l.title} <span className="text-muted">· {l.kind}</span></span><span className="tnum">{usd(l.cost)}</span></li>)}</ul>
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12.5px]">{c.files.map((f) => (
                <a key={f.file_id} href={`/api/brain/entry/files/${f.file_id}`} target="_blank" rel="noreferrer" className="font-medium text-accent hover:underline">{f.role === "approval" ? "Approved copy: " : f.is_photo ? "Photo: " : ""}{f.name}</a>))}</div>
              {c.days ? <div className="mt-1 text-[12.5px] text-muted">Adds {c.days} day{c.days === 1 ? "" : "s"} to the job{c.group ? ` · ${c.group}` : ""}</div> : null}
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
