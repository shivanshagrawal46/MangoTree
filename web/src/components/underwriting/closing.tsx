"use client";

/* Step 6 · Closing. Three tables in the order things happen:
   A · at the closing table (the money stays locked until all are done),
   B · the papers that must come back afterwards (from whom, to whom),
   C · the foreclosure file: everything a foreclosure will ask for, held or not, and where.
   The same line detail as on the file page opens on the right. */

import * as React from "react";
import { cn } from "@/lib/utils";
import { PARTY, isDone, lineState, type Item } from "@/lib/uw";
import { PageHead, Panel, SectionHead, Status, td, th } from "./bits";
import { LinePanel } from "./file";
import type { PageProps } from "./workspace";

const GOES: Record<string, string> = { original_note: "RKB's custodian", recorded_copy: "RKB's custodian", request_for_notice: "The land records" };
const HELD: Record<string, string> = { identity: "With RKB's attorney; the numbers are never in this system", original_note: "RKB's custodian", recorded_copy: "RKB's custodian", recording: "The land records" };

function record(i: Item): string {
  const v = i.values || {};
  const got = i.fields.map((x) => (v[x.key] ? `${x.label}: ${v[x.key]}` : "")).filter(Boolean);
  return got.length ? got.join(" · ") : i.fields.length ? i.fields.map((x) => x.label).join(" · ") : i.needs_file ? "The paper itself" : "Confirmed on the day of recording";
}

function Rows({ rows, sel, pick, goes }: { rows: Item[]; sel: string | null; pick: (k: string) => void; goes?: boolean }) {
  return (
    <tbody>{rows.map((i) => (
      <tr key={i.key} onClick={() => pick(i.key)} className={cn("cursor-pointer", sel === i.key ? "bg-accent-soft" : "hover:bg-sunken")}>
        <td className={td}><span className="font-medium">{i.name}</span></td><td className={cn(td, "text-muted")}>{PARTY[i.party]}</td>
        {goes && <td className={cn(td, "text-muted")}>{GOES[i.key] || "RKB"}</td>}
        <td className={td}><Status tone={lineState(i)} /></td><td className={cn(td, "text-muted text-[12px]")}>{record(i)}</td>
      </tr>))}</tbody>
  );
}

export function ClosingPage({ f, run, save }: PageProps) {
  const [sel, setSel] = React.useState<string | null>(null);
  const at = f.items.filter((i) => i.when === "closing"), after = f.items.filter((i) => i.when === "after"), kit = f.items.filter((i) => i.foreclosure);
  const g = f.gates, held = g.foreclosure_file.held;
  const item = sel ? f.items.find((i) => i.key === sel) : null;
  const flow: [string, boolean][] = [["Signed", g.signed], ["At the table", !g.waiting_closing.length && g.signed], ["Money can move", g.can_fund], ["Papers back", g.signed && !g.waiting_after.length], ["Foreclosure file complete", g.foreclosure_file.complete]];
  const pick = (k: string) => setSel(sel === k ? null : k);
  return (
    <>
      <PageHead n={6} title="Closing" line="What must happen at the table, what must come back, and what RKB must hold if it ever has to foreclose."
        holds={<>Holding this step: <b className="text-fg">{g.steps[5].done ? "nothing" : g.waiting_closing.length ? `${g.waiting_closing.length} at the table` : g.waiting_after.length ? `${g.waiting_after.length} paper${g.waiting_after.length === 1 ? "" : "s"} to come back` : "nothing"}</b></>} />
      <Panel className="px-3.5 py-2.5 flex flex-wrap items-center gap-1.5">
        {flow.map(([l, ok], n) => <React.Fragment key={l}>{n > 0 && <span className="text-faint">→</span>}<span className={cn("px-2.5 py-0.5 rounded-full text-[12px]", ok ? "bg-good-soft text-good font-semibold" : "bg-sunken text-muted")}>{ok ? "✓ " : ""}{l}</span></React.Fragment>)}
      </Panel>
      <div className={cn("grid gap-3 mt-3 items-start", item ? "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_370px]" : "grid-cols-1")}>
        <div className="flex flex-col gap-3 min-w-0">
          <Panel className="overflow-x-auto"><SectionHead title="A · At the closing table" right={<Status tone={g.can_fund ? "done" : "locked"}>{g.can_fund ? "Money can move" : "Money locked until all of these are done"}</Status>} />
            <table className="w-full border-collapse min-w-[640px]"><thead><tr><th className={th}>Line</th><th className={th}>From</th><th className={th}>Status</th><th className={th}>What is recorded</th></tr></thead>
              <Rows rows={at} sel={sel} pick={pick} /></table></Panel>
          <Panel className="overflow-x-auto"><SectionHead title="B · Papers that must come back" right={<span className="text-[12px] text-muted">{after.filter((i) => !isDone(i)).length} still to come</span>} />
            <table className="w-full border-collapse min-w-[640px]"><thead><tr><th className={th}>Paper</th><th className={th}>From</th><th className={th}>Goes to</th><th className={th}>Status</th><th className={th}>What is recorded</th></tr></thead>
              <Rows rows={after} sel={sel} pick={pick} goes /></table></Panel>
          <Panel className="overflow-x-auto"><SectionHead title="C · Foreclosure file" right={<span className="tnum"><b>{held} of {kit.length}</b> held</span>} />
            <table className="w-full border-collapse min-w-[560px]"><thead><tr><th className={th}>What a foreclosure will ask for</th><th className={th}>Status</th><th className={th}>Where it is held</th></tr></thead>
              <tbody>{kit.map((i) => (
                <tr key={i.key} onClick={() => pick(i.key)} className={cn("cursor-pointer", sel === i.key ? "bg-accent-soft" : "hover:bg-sunken")}>
                  <td className={td}><span className="font-medium">{i.name}</span></td>
                  <td className={td}>{isDone(i) ? <Status tone={lineState(i)}>Held</Status> : <Status tone="needed">Not held yet</Status>}</td>
                  <td className={cn(td, "text-muted text-[12px]")}>{HELD[i.key] || "In this file"}</td>
                </tr>))}</tbody></table>
            <div className="px-3.5 py-2 text-[12px] text-muted border-t border-line">None of these can be skipped. On Euclid the Social Security number and date of birth were never collected, and the foreclosure filing waited four months for them.</div>
          </Panel>
        </div>
        {item && <LinePanel key={item.key} f={f} item={item} onClose={() => setSel(null)} run={run} save={save} />}
      </div>
    </>
  );
}
