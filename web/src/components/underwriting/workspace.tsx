"use client";

/* One underwriting file: the address, the seven-step tracker, and one page per step.

   The tracker is the only navigation. Each step says in words what it is waiting
   for; a step that cannot start yet is grey and does not open. The order is fixed
   on the server: Property and cost → Quick check → Remaining details → The file →
   Documents → Review and sign → Closing. */

import * as React from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronLeft } from "lucide-react";
import { Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";
import { errText, pct, uw, type UwFile } from "@/lib/uw";
import { Panel } from "./bits";
import { DetailsPage, MorePage } from "./details";
import { CheckPage } from "./check";
import { FilePage } from "./file";
import { DocumentsPage } from "./docs";
import { ReviewPage } from "./review";
import { ClosingPage } from "./closing";

/* `go(step, from)`: `from` is the file as it stands after a save, when the caller has it; without it the
   step is checked against the file on screen, which is one save behind inside a save handler. */
export type PageProps = { f: UwFile; go: (step: number, from?: UwFile) => void; save: (next: UwFile) => void; run: <T extends UwFile>(p: Promise<T>, ok?: string) => Promise<T | null> };

function sub(f: UwFile, n: number): string {
  const g = f.gates, a = f.assessment, more = (g.more_missing || []).length;
  if (n === 1) return g.details_missing.length ? `${g.details_missing.length} answer${g.details_missing.length === 1 ? "" : "s"} needed` : "Complete";
  if (n === 2) return !g.steps[1].open ? "After page 1" : a.new_state ? "New state: not set up" : a.verdict === "fits" ? `RKB can fund · ${pct(a.rule_65.ratio)} of ARV` : a.verdict === "exception" ? "For Rakesh Sir" : a.rejected_second_mortgage ? "Rejected: second mortgage" : "Cannot fund as entered";
  if (n === 3) return !g.steps[2].open ? "After the quick check" : more ? `${more} answer${more === 1 ? "" : "s"} needed` : "Complete";
  if (n === 4) return !g.steps[3].open ? "After the remaining details" : g.file_complete ? "Complete" : `${g.waiting_file.length} needed line${g.waiting_file.length === 1 ? "" : "s"} open`;
  if (n === 5) return !g.steps[4].open ? "After the remaining details" : g.documents.total ? `${g.documents.approved} of ${g.documents.total} approved` : "Not written yet";
  if (n === 6) return g.signed ? "Signed" : g.steps[5].open ? "Ready for Rakesh Sir" : "Opens after steps 1 to 5";
  return g.steps[6].done ? "Complete" : g.steps[6].open ? (g.can_fund ? "Money can move" : "Money locked") : "Opens after the signature";
}

export function UnderwritingFile({ fileId }: { fileId: string }) {
  const qc = useQueryClient();
  const key = React.useMemo(() => ["uw", "file", fileId], [fileId]);
  const q = useQuery({ queryKey: key, queryFn: () => uw.get(fileId), staleTime: 15_000 });
  const f = q.data;
  const [step, setStep] = React.useState<number | null>(null);
  // The step the person chose, while it is still open; otherwise the step the file stands on.
  const shown = step && f?.gates.steps[step - 1]?.open ? step : f?.gates.step ?? 1;

  const save = React.useCallback((next: UwFile) => { qc.setQueryData(key, next); qc.invalidateQueries({ queryKey: ["uw", "list"] }); }, [qc, key]);
  const run = React.useCallback(async <T extends UwFile>(p: Promise<T>, ok?: string): Promise<T | null> => {
    try { const next = await p; save(next); if (ok) toast.success(ok); return next; } catch (e) { toast.error(errText(e)); return null; }
  }, [save]);

  if (q.isLoading) return <div className="p-4 max-w-[1240px] mx-auto"><Skeleton className="h-24 rounded-xl" /><Skeleton className="h-96 rounded-xl mt-3" /></div>;
  if (!f) return <div className="p-6 text-muted">This file could not be opened. <Link className="text-accent" href="/underwriting">Back to the list</Link></div>;

  const a = f.assessment;
  const go = (n: number, from: UwFile = f) => { if (from.gates.steps[n - 1]?.open) setStep(n); };
  const props: PageProps = { f, go, save, run };
  return (
    <div className="max-w-[1240px] mx-auto px-4 pt-3.5 pb-14 text-[13px]">
      <Panel className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 px-3.5 py-2.5 border-b border-line">
          <Link href="/underwriting" className="text-muted hover:text-fg -ml-1" aria-label="Back to all files"><ChevronLeft size={16} /></Link>
          <span className="text-[15px] font-semibold">{f.facts.address || "New file"}</span>
          <span className="text-muted">{[a.kind_label, a.state_name, f.facts.owner_name].filter(Boolean).join(" · ")}</span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7">
          {f.gates.steps.map((s) => {
            const state = s.done ? "done" : s.open ? "now" : "locked";
            return (
              <button key={s.n} type="button" disabled={!s.open} onClick={() => go(s.n)} aria-current={shown === s.n ? "step" : undefined}
                className={cn("relative text-left px-3 pt-2.5 pb-3 border-r border-b lg:border-b-0 border-line last:border-r-0 flex flex-col gap-0.5 min-w-0 transition-colors",
                  shown === s.n ? "bg-accent-soft" : s.open ? "hover:bg-sunken" : "cursor-not-allowed")}>
                <span className="flex items-center gap-1.5">
                  <span className={cn("h-5 w-5 rounded-full grid place-items-center text-[11px] font-bold shrink-0 border-[1.5px]",
                    state === "done" ? "bg-good border-good text-white" : state === "now" ? "border-accent text-accent" : "border-faint text-faint")}>{s.done ? "✓" : s.n}</span>
                  <span className={cn("font-semibold truncate", state === "locked" && "text-faint")}>{s.name}</span>
                </span>
                <span className={cn("text-[11.5px] truncate pl-[26px]", state === "locked" ? "text-faint" : "text-muted")}>{sub(f, s.n)}</span>
                {shown === s.n && <span className="absolute inset-x-0 bottom-0 h-[3px] bg-accent" />}
              </button>
            );
          })}
        </div>
      </Panel>
      {shown === 1 && <DetailsPage {...props} />}
      {shown === 2 && <CheckPage {...props} />}
      {shown === 3 && <MorePage {...props} />}
      {shown === 4 && <FilePage {...props} />}
      {shown === 5 && <DocumentsPage {...props} />}
      {shown === 6 && <ReviewPage {...props} />}
      {shown === 7 && <ClosingPage {...props} />}
    </div>
  );
}
