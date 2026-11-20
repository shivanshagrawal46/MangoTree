"use client";

/* Underwriting · every file, one line each: the property, the kind of deal, the
   answer of the quick check, and which of the six steps it stands on. A new file
   starts from an ARV file (nothing is typed twice) or from an address. */

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import { Button, Dialog, DialogContent, Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";
import { day, errText, pct, usd, uw, type UwRow } from "@/lib/uw";
import { Panel, Status, input, td, th, type Tone } from "./bits";

function verdict(r: UwRow): { tone: Tone; word: string } {
  const a = r.assessment || {};
  if (r.gates && r.gates.details_missing.length) return { tone: "needed", word: "Details to finish" };
  if (a.verdict === "fits") return { tone: "pass", word: `Fits · ${pct(a.rule_65?.ratio)}` };
  if (a.verdict === "exception") return { tone: "look", word: "For Rakesh Sir" };
  if (a.rejected_second_mortgage) return { tone: "fail", word: "Rejected: second mortgage" };
  if (a.verdict === "does_not_fit") return { tone: "fail", word: "Does not fit" };
  return { tone: "needed", word: "Not checked yet" };
}

export function UnderwritingList() {
  const router = useRouter();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["uw", "list"], queryFn: uw.list, staleTime: 15_000 });
  const [open, setOpen] = React.useState(false);
  const arv = useQuery({ queryKey: ["uw", "arv-files"], queryFn: uw.arvFiles, enabled: open, staleTime: 60_000 });
  const [address, setAddress] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const rows = q.data?.items || [];
  const canDelete = !!q.data?.can_delete;      // the server says who may; it checks again on the delete itself
  const [asking, setAsking] = React.useState<string | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const remove = async (r: UwRow) => {
    setDeleting(true);
    try { await uw.remove(r.file_id); toast.success(`Deleted: ${r.facts.address || "the file"}`); await qc.invalidateQueries({ queryKey: ["uw", "list"] }); } catch (e) { toast.error(errText(e)); }
    setDeleting(false); setAsking(null);
  };
  const start = async (facts: { address?: string }, arvId?: string) => {
    setBusy(true);
    try { const f = await uw.create(facts, arvId); router.push(`/underwriting/${f.file_id}`); } catch (e) { toast.error(errText(e)); setBusy(false); }
  };
  return (
    <div className="max-w-[1240px] mx-auto px-4 pt-4 pb-14 text-[13px]">
      <div className="flex flex-wrap items-end justify-between gap-3 mb-3">
        <div><h1 className="text-[19px] font-bold tracking-tight">Underwriting</h1>
          <p className="text-muted mt-0.5">From a new address to a recorded deed of trust, on RKB&apos;s rules. One file per deal.</p></div>
        <Button variant="primary" onClick={() => setOpen(true)}>New file</Button>
      </div>
      <Panel className="overflow-x-auto">
        <table className="w-full border-collapse min-w-[820px]">
          <thead><tr><th className={th}>Property</th><th className={th}>Deal</th><th className={th}>Quick check</th><th className={th}>Note</th><th className={th}>Stands on</th><th className={th}>Started by</th><th className={th}>Last change</th>{canDelete && <th className={th}><span className="sr-only">Delete</span></th>}</tr></thead>
          <tbody>
            {q.isLoading && <tr><td colSpan={8} className="p-3"><Skeleton className="h-24" /></td></tr>}
            {!q.isLoading && !rows.length && <tr><td colSpan={8} className="px-3 py-8 text-muted">No file yet. Start one from an ARV file with “New file”.</td></tr>}
            {rows.map((r) => {
              const v = verdict(r), g = r.gates, s = g?.steps.find((x) => x.n === g.step);
              return (
                <tr key={r.file_id} className="hover:bg-sunken cursor-pointer" onClick={() => router.push(`/underwriting/${r.file_id}`)}>
                  <td className={td}><Link href={`/underwriting/${r.file_id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>{r.facts.address || "No address yet"}</Link></td>
                  <td className={cn(td, "text-muted")}>{[r.assessment?.kind_label, r.facts.state].filter(Boolean).join(" · ")}</td>
                  <td className={td}><Status tone={v.tone}>{v.word}</Status></td>
                  <td className={cn(td, "tnum")}>{r.assessment?.money?.principal ? usd(r.assessment.money.principal) : ""}</td>
                  <td className={td}>{g ? (g.steps[5].done ? <Status tone="done">Closed and complete</Status> : <span>{g.step} · {s?.name}</span>) : ""}</td>
                  <td className={cn(td, "text-muted")}>{r.created_by_name}{r.created_side === "contractor" ? " · ROI Blocks" : ""}</td>
                  <td className={cn(td, "text-muted whitespace-nowrap")}>{day(r.updated_at)}</td>
                  {canDelete && (
                    <td className={cn(td, "text-right whitespace-nowrap w-px")} onClick={(e) => e.stopPropagation()}>
                      {asking === r.file_id ? (
                        <span className="inline-flex items-center gap-1.5">
                          <span className="text-critical font-medium">{r.signed?.at ? "Signed file. Delete it?" : "Delete this file?"}</span>
                          <Button size="sm" variant="danger" className="h-7" disabled={deleting} onClick={() => remove(r)}>{deleting ? "Deleting…" : "Delete"}</Button>
                          <Button size="sm" className="h-7" disabled={deleting} onClick={() => setAsking(null)}>Cancel</Button>
                        </span>
                      ) : (
                        <button type="button" aria-label={`Delete ${r.facts.address || "this file"}`} title="Delete this file" onClick={() => setAsking(r.file_id)}
                          className="h-7 w-7 inline-grid place-items-center rounded-md text-faint hover:text-critical hover:bg-critical-soft"><Trash2 size={14} /></button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>
      <Dialog open={open} onOpenChange={(o) => { if (!busy) setOpen(o); }}>
        <DialogContent title="New underwriting file" description="Start from an ARV file and its figures come with it. Or start from an address and enter them on page 1.">
          <div className="max-h-[46vh] overflow-y-auto border border-line rounded-xl">
            {arv.isLoading && <div className="p-3"><Skeleton className="h-20" /></div>}
            {!arv.isLoading && !(arv.data?.items || []).length && <div className="px-3 py-4 text-muted text-[13px]">No finished ARV file found.</div>}
            {(arv.data?.items || []).map((x) => (
              <button key={x.analysis_id} type="button" disabled={busy} onClick={() => start({}, x.analysis_id)} className="w-full text-left px-3 py-2 border-b border-line last:border-b-0 hover:bg-sunken flex items-center justify-between gap-3 text-[13px]">
                <span className="min-w-0"><span className="font-medium block truncate">{x.address}</span><span className="text-muted text-[12px]">{x.by} · {day(x.at)}</span></span>
                <span className="text-right whitespace-nowrap tnum"><b>{usd(x.arv)}</b><span className="block text-muted text-[12px]">after repair</span></span>
              </button>
            ))}
          </div>
          <div className="mt-3 flex gap-2"><input className={input} placeholder="Or type the property address" value={address} onChange={(e) => setAddress(e.target.value)} />
            <Button className="h-8 shrink-0" disabled={busy || address.trim().length < 6} onClick={() => start({ address: address.trim() })}>Start</Button></div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
