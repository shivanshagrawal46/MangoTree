"use client";

/* Step 2 · Quick check. Only the answer of RKB's rules on what page 1 holds:
   the verdict first, then the three rules as three rows, the one needing
   attention first, each with its working behind a click. Nothing is entered here. */

import * as React from "react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { num, pct, usd } from "@/lib/uw";
import { MoneyTable, PageHead, Panel, Status, linkBtn, type Tone } from "./bits";
import { NewState } from "./newstate";
import type { PageProps } from "./workspace";

function Rule({ tone, word, name, line, figure, under, working }: { tone: Tone; word: string; name: string; line: string; figure: string; under: string; working: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] md:grid-cols-[150px_minmax(0,1fr)_auto] gap-x-3.5 gap-y-1 px-3.5 py-3 border-b border-line last:border-b-0 items-center">
      <div className="col-span-2 md:col-span-1"><Status tone={tone}>{word}</Status></div>
      <div className="min-w-0"><b className="font-semibold">{name}</b><div className="text-muted">{line}</div>
        <button type="button" className={cn(linkBtn, "font-normal text-[12px]")} onClick={() => setOpen(!open)}>{open ? "Hide the working" : "Show the working"}</button></div>
      <div className="text-right"><div className="text-[20px] font-bold tnum leading-tight">{figure}</div><div className="text-[11.5px] text-muted">{under}</div></div>
      {open && <div className="col-span-2 md:col-start-2 text-[12.5px] text-muted">{working}</div>}
    </div>
  );
}

export function CheckPage({ f, go }: PageProps) {
  const a = f.assessment, r = a.rule_65, fc = a.foreclosure, b = a.budget, m = a.money;
  const good = a.verdict === "fits", exc = a.verdict === "exception";
  const rules = [
    { order: r.passed ? 2 : 0, el: <Rule key="r" tone={r.passed ? "pass" : "fail"} word={r.passed ? "Pass" : "Fail"} name="65% rule"
      line={`${usd(r.exposure)} stands against the house. The limit is ${usd(r.limit_amount)}.`} figure={pct(r.ratio)} under="of ARV, limit 65%"
      working={<>{r.parts.filter((p) => p.amount > 0).map((p) => `${p.label}: ${usd(p.amount, true)}`).join(" · ")}. The note ({usd(m.principal, true)}) is the owner&apos;s number and is not counted here.</>} /> },
    { order: fc.passed ? 2 : 0, el: <Rule key="f" tone={fc.passed ? "pass" : "fail"} word={fc.passed ? "Pass" : "Fail"} name="Foreclosure test"
      line={`If the house never sells and is sold in a foreclosure at its after-repair value, ${usd(fc.finished.left_for_rkb)} is left for RKB against ${usd(fc.finished.rkb_out)} of RKB's money in the deal.`}
      figure={fc.finished.cover !== null ? `${fc.finished.cover.toFixed(1)}×` : ""} under="covered"
      working={<>Finished house {usd(fc.finished.value)}, less 15% = {usd(fc.finished.marked_down)}, less 8% selling cost = {usd(fc.finished.after_selling)}
        {fc.finished.mortgage_ahead > 0 && <>, less the mortgage ahead {usd(fc.finished.mortgage_ahead)} and its growth {usd(fc.finished.mortgage_growth)}</>}, less a foreclosure of about {fc.finished.months} months ({usd(fc.finished.foreclosure_cost)}).
        {fc.estimate && " The months and the cost are RKB's estimate for this state."}{fc.worst_case && " This state is not set up, so the slowest and costliest foreclosure we know is assumed."}</>} /> },
    { order: b.passed ? 2 : 1, el: <Rule key="b" tone={b.passed ? "pass" : "look"} word={b.passed ? "Pass" : "For Manju Sir"} name="Budget test"
      line={b.passed ? `Within the usual range for this scope${b.range ? ` ($${b.range[0]} to $${b.range[1]} a square foot)` : ""}, with contingency of ${pct(b.contingency_share)}.` : b.looks.join(" ")}
      figure={b.per_sqft !== null ? `$${Math.round(b.per_sqft)}` : "—"} under="a square foot"
      working={<>Builder&apos;s cost {usd(m.work, true)} over {num(b.area)} sq ft. Contingency is {pct(b.contingency_share)} of the work; at least 10% is asked. A budget outside the range is not refused: one person looks and answers on the file page.</>} /> },
  ].sort((x, y) => x.order - y.order);
  const st = a.state_name;
  return (
    <>
      <PageHead n={2} title="Quick check" line="The answer of RKB's rules on what page 1 holds. Nothing is entered here."
        holds={<>Holding the next step: <b className="text-fg">{f.gates.verdict_ok ? "nothing" : a.new_state ? "this state is not set up" : "the rules below"}</b></>} />
      <Panel className={cn("px-4 py-3.5 border-l-4 flex flex-wrap items-center gap-x-6 gap-y-2", good ? "border-l-good" : exc ? "border-l-high" : "border-l-critical")}>
        <div className="min-w-0">
          <div className="text-[20px] font-bold">{good ? "Fits RKB's rules" : exc ? "For Rakesh Sir to decide" : a.verdict === "incomplete" ? "Not enough to check yet" : a.rejected_second_mortgage ? "Rejected: second mortgage" : "Does not fit RKB's rules"}</div>
          <div className="text-muted">{good ? `${pct(r.ratio)} of ARV: the mortgage that stays and RKB's own money, against a limit of 65%` : a.headline}</div>
        </div>
        <div className="ml-auto text-right"><div className="text-[11px] uppercase tracking-[.05em] text-faint font-semibold">{r.room >= 0 ? "Room under the 65% limit" : "Over the 65% limit by"}</div><b className="text-[17px] tnum">{usd(Math.abs(r.room))}</b></div>
      </Panel>
      {a.reasons.length > 0 && (
        <Panel className="mt-3 px-3.5 py-3 border-critical/40"><h3 className="font-semibold mb-1">Why</h3>
          <ul className="flex flex-col gap-1.5">{a.reasons.map((x) => <li key={x} className="flex gap-2"><Status tone="fail">{""}</Status><span>{x}</span></li>)}</ul></Panel>
      )}
      {a.new_state && <NewState f={f} />}
      <Panel className="mt-3">{rules.map((x) => x.el)}</Panel>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 items-start">
        <Panel className="px-3.5 py-3"><h3 className="font-semibold mb-1.5">Where RKB&apos;s money goes</h3>
          <MoneyTable rows={m.lines.map((l) => ({ label: l.label, amount: usd(l.amount, true) }))} total={{ label: "RKB pays out", amount: usd(m.cash_out, true) }} /></Panel>
        <Panel className="px-3.5 py-3"><h3 className="font-semibold mb-1.5">{a.new_state ? `${st} is not set up yet` : `What ${st ?? "the state"} changes`}</h3>
          <MoneyTable rows={[
            { label: "Lien", amount: a.lien_position === "second" ? "Second, behind one mortgage" : "First" },
            { label: "If RKB must foreclose", amount: `About ${fc.finished.months} months, ${usd(fc.finished.foreclosure_cost)}` },
            { label: "Note to RKB (the owner's number)", amount: usd(m.principal, true) },
            { label: "The note's rate, points, term", amount: `${r.rate}% · ${r.points} points · ${r.term_months} months${r.interest_free_months ? ` · ${r.interest_free_months} interest-free` : ""}` },
          ]} />
          <p className="mt-2 text-[12.5px] text-muted">Foreclosure here: {fc.state_route}.{fc.estimate ? " The months and the cost are RKB's estimate." : ""}</p>
          {a.notices.length > 0 && <ul className="mt-2 flex flex-col gap-1.5 text-[12.5px] text-muted list-disc pl-4">{a.notices.map((x) => <li key={x}>{x}</li>)}</ul>}
          <div className="flex gap-2 justify-end mt-3"><Button onClick={() => go(1)}>Change the details</Button><Button variant="primary" disabled={!f.gates.steps[2].open} onClick={() => go(3)}>Go to the file</Button></div>
        </Panel>
      </div>
    </>
  );
}
