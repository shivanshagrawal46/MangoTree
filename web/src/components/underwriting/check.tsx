"use client";

/* Step 2 · Quick check. Only the answer of RKB's rules on what page 1 holds.

   The answer first. When the 65% rule and the foreclosure test pass and nothing
   stops the deal, the page says "RKB can fund the deal" in green and celebrates,
   whatever the budget test says: the budget is Manju Sir's to look at and never
   holds the answer (asked for on 2026-11-21). Then the three rules as three rows,
   the one needing attention first, each with its working behind a click; then
   RKB's waterfall: what stands against the house, and what is left for RKB if
   the house had to be sold in a foreclosure. Nothing is entered here. */

import * as React from "react";
import { PartyPopper } from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { num, pct, usd, type Assessment } from "@/lib/uw";
import { MoneyTable, PageHead, Panel, Status, linkBtn, type Tone } from "./bits";
import { Celebrate } from "./celebrate";
import { NewState } from "./newstate";
import type { PageProps } from "./workspace";

function Rule({ tone, word, name, line, figure, under, working }: { tone: Tone; word: string; name: string; line: React.ReactNode; figure: string; under: string; working: React.ReactNode }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] md:grid-cols-[170px_minmax(0,1fr)_auto] gap-x-3.5 gap-y-1 px-3.5 py-3 border-b border-line last:border-b-0 items-center">
      <div className="col-span-2 md:col-span-1"><Status tone={tone}>{word}</Status></div>
      <div className="min-w-0"><b className="font-semibold">{name}</b><div className="text-muted">{line}</div>
        <button type="button" className={cn(linkBtn, "font-normal text-[12px]")} onClick={() => setOpen(!open)}>{open ? "Hide the working" : "Show the working"}</button></div>
      <div className="text-right"><div className="text-[20px] font-bold tnum leading-tight">{figure}</div><div className="text-[11.5px] text-muted">{under}</div></div>
      {open && <div className="col-span-2 md:col-start-2 text-[12.5px] text-muted">{working}</div>}
    </div>
  );
}

/* One line of a waterfall: a name, a thin bar placed where the amount sits on the scale, the amount.
   Names and amounts stay in ink; the bar alone carries the colour. */
function Step({ label, note, from, size, scale, amount, kind }: { label: string; note?: string; from: number; size: number; scale: number; amount: string; kind: "start" | "less" | "left" | "rkb" | "short" }) {
  const left = Math.max(0, Math.min(100, (100 * from) / scale)), width = Math.max(0.6, Math.min(100 - left, (100 * Math.max(0, size)) / scale));
  const bar = { start: "bg-fg/55", less: "bg-fg/20", left: "bg-good", rkb: "bg-accent", short: "bg-critical" }[kind];
  const total = kind === "left" || kind === "start";
  return (
    <div className={cn("grid grid-cols-[minmax(0,1fr)_auto] sm:grid-cols-[minmax(0,230px)_minmax(0,1fr)_112px] gap-x-3 gap-y-1 items-center py-1.5 border-b border-line last:border-b-0", total && "font-semibold")} title={`${label}: ${amount}`}>
      <span className="min-w-0">{label}{note && <span className="text-muted font-normal"> · {note}</span>}</span>
      <span className="order-3 sm:order-none col-span-2 sm:col-span-1 relative h-2.5 rounded-[3px] bg-sunken"><span className={cn("absolute top-0 h-2.5 rounded-[3px]", bar)} style={{ left: `${left}%`, width: `${width}%` }} /></span>
      <span className="text-right tnum whitespace-nowrap">{amount}</span>
    </div>
  );
}

function Waterfall({ a }: { a: Assessment }) {
  const r = a.rule_65, fin = a.foreclosure.finished, m = a.money;
  const arv = fin.value || 1;
  const pc = (x: number) => `${Math.max(0, Math.min(100, (100 * x) / arv))}%`;
  const cushion = fin.left_for_rkb - fin.rkb_out;
  // What comes off the value, in the order it is paid, each bar hanging where the one before it ended.
  const off: { label: string; note: string; amount: number }[] = [
    { label: "Forced sale", note: "15% off", amount: fin.value - fin.marked_down },
    { label: "Selling cost", note: "8%", amount: fin.marked_down - fin.after_selling },
    { label: "Mortgage ahead of RKB", note: "paid first", amount: fin.mortgage_ahead },
    { label: "Its growth meanwhile", note: `${fin.months} months`, amount: fin.mortgage_growth },
    { label: "Holding the house", note: `${fin.months} months`, amount: fin.holding },
    { label: "Foreclosure cost", note: `about ${fin.months} months`, amount: fin.foreclosure_cost },
  ].filter((x) => x.amount > 0);
  const ends = off.map((_, i) => fin.value - off.slice(0, i + 1).reduce((s, x) => s + x.amount, 0));
  return (
    <Panel className="mt-3 overflow-hidden">
      <div className="px-3.5 py-2.5 border-b border-line"><h3 className="text-[13.5px] font-semibold">RKB&apos;s waterfall</h3>
        <p className="text-[12.5px] text-muted">From the value of the finished house down to RKB. The note is the owner&apos;s number and is not used here.</p></div>
      <div className="grid grid-cols-1 lg:grid-cols-2">
        <div className="px-3.5 py-3 lg:border-r border-b lg:border-b-0 border-line">
          <h4 className="font-semibold mb-2">What stands against the house</h4>
          {/* One bar the width of the after-repair value: the mortgage ahead, then RKB's money, and the 65% line. */}
          <div className="relative pt-5 pb-5">
            <div className="flex h-3.5 rounded-[4px] bg-sunken overflow-hidden gap-[2px]" role="img" aria-label={`${usd(r.exposure)} stands against a house worth ${usd(arv)}; the limit is ${usd(r.limit_amount)}`}>
              {m.staying > 0 && <span className="bg-fg/45 h-full" style={{ width: pc(m.staying) }} title={`Mortgage that stays ahead of RKB: ${usd(m.staying)}`} />}
              <span className={cn("h-full", r.passed ? "bg-accent" : "bg-critical")} style={{ width: pc(r.rkb_money) }} title={`RKB's money in the deal: ${usd(r.rkb_money)}`} />
            </div>
            <span className="absolute top-3.5 bottom-3.5 w-[2px] bg-fg" style={{ left: `${r.limit * 100}%` }} />
            <span className="absolute top-0 text-[11px] font-semibold -translate-x-1/2 whitespace-nowrap" style={{ left: `${r.limit * 100}%` }}>65% limit · {usd(r.limit_amount)}</span>
            <span className="absolute bottom-0 left-0 text-[11px] text-muted">$0</span>
            <span className="absolute bottom-0 right-0 text-[11px] text-muted">ARV {usd(arv)}</span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted mb-2">
            {m.staying > 0 && <span className="inline-flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-[2px] bg-fg/45" />Mortgage ahead of RKB</span>}
            <span className="inline-flex items-center gap-1.5"><i className={cn("h-2.5 w-2.5 rounded-[2px]", r.passed ? "bg-accent" : "bg-critical")} />RKB&apos;s money in the deal</span>
          </div>
          <MoneyTable rows={[...r.parts.filter((p) => p.amount > 0).map((p) => ({ label: p.label, amount: usd(p.amount, true) })),
            ...(m.commission.known ? [] : [{ label: "Paid to ROI Blocks at closing", amount: "after the price to the owner" }])]}
            total={{ label: `Stands against the house · ${pct(r.ratio)} of ARV`, amount: usd(r.exposure, true) }} />
          <MoneyTable rows={[{ label: "65% of the after-repair value", amount: usd(r.limit_amount, true) }, { label: r.room >= 0 ? "Room under the limit" : "Over the limit by", amount: usd(Math.abs(r.room), true) }]} />
        </div>
        <div className="px-3.5 py-3">
          <h4 className="font-semibold mb-2">If RKB had to foreclose</h4>
          <Step label="Finished house (ARV)" from={0} size={fin.value} scale={arv} amount={usd(fin.value)} kind="start" />
          {off.map((x, i) => <Step key={x.label} label={x.label} note={x.note} from={ends[i]} size={x.amount} scale={arv} amount={`− ${usd(x.amount)}`} kind="less" />)}
          <Step label="Left for RKB" from={0} size={fin.left_for_rkb} scale={arv} amount={usd(fin.left_for_rkb)} kind={fin.passed ? "left" : "short"} />
          <Step label="RKB's money in the deal" from={0} size={fin.rkb_out} scale={arv} amount={`− ${usd(fin.rkb_out)}`} kind="rkb" />
          <Step label={cushion >= 0 ? "Cushion after RKB is repaid" : "RKB is short by"} from={cushion >= 0 ? fin.rkb_out : Math.max(0, fin.left_for_rkb)} size={Math.abs(cushion)} scale={arv} amount={usd(Math.abs(cushion))} kind={cushion >= 0 ? "left" : "short"} />
          <p className="mt-2 text-[12px] text-muted">{a.foreclosure.state_route ? `${a.state_name ?? "This state"}: ${a.foreclosure.state_route}.` : ""}{a.foreclosure.estimate ? " The months and the cost are RKB's estimate." : ""}{a.foreclosure.worst_case ? " This state is not set up, so the slowest and costliest foreclosure we know is assumed." : ""}</p>
        </div>
      </div>
    </Panel>
  );
}

/* Celebrate once for each answer: the same file with the same figure does not throw paper again in this browser session. */
function useFirstTime(fileId: string, good: boolean, mark: string): boolean {
  const key = `uw-can-fund-${fileId}`;
  const [first] = React.useState(() => { if (!good) return false; try { return window.sessionStorage.getItem(key) !== mark; } catch { return true; } });
  React.useEffect(() => { try { if (good) window.sessionStorage.setItem(key, mark); else window.sessionStorage.removeItem(key); } catch { /* storage switched off: it celebrates each time */ } }, [key, good, mark]);
  return first;
}

export function CheckPage({ f, go }: PageProps) {
  const a = f.assessment, r = a.rule_65, fc = a.foreclosure, b = a.budget, m = a.money, g = f.gates;
  const good = a.verdict === "fits" && !a.new_state, exc = a.verdict === "exception";
  const fire = useFirstTime(f.file_id, good, pct(r.ratio));
  const waiting = (b.pending || []).length > 0 && !b.looks.length;      // the budget test has not got its answers yet
  const more = (g.more_missing || []).length;
  const rules = [
    { order: r.passed ? 2 : 0, el: <Rule key="r" tone={r.passed ? "pass" : "fail"} word={r.passed ? "Pass" : "Fail"} name="65% rule"
      line={<>{usd(r.exposure)} stands against the house. The limit is {usd(r.limit_amount)}.{!m.commission.known && <span className="text-high"> ROI Blocks&apos; commission is not counted yet: it is worked out from the price to the owner.</span>}</>} figure={pct(r.ratio)} under="of ARV, limit 65%"
      working={<>{r.parts.filter((p) => p.amount > 0).map((p) => `${p.label}: ${usd(p.amount, true)}`).join(" · ")}. {m.commission.known ? <>The note ({usd(m.principal, true)}) is the owner&apos;s number and is not counted here.</> : <>The note is the owner&apos;s number and is not counted here.</>}</>} /> },
    { order: fc.passed ? 2 : 0, el: <Rule key="f" tone={fc.passed ? "pass" : "fail"} word={fc.passed ? "Pass" : "Fail"} name="Foreclosure test"
      line={`If the house never sells and is sold in a foreclosure at its after-repair value, ${usd(fc.finished.left_for_rkb)} is left for RKB against ${usd(fc.finished.rkb_out)} of RKB's money in the deal.`}
      figure={fc.finished.cover !== null ? `${fc.finished.cover.toFixed(1)}×` : ""} under="covered"
      working={<>Finished house {usd(fc.finished.value)}, less 15% = {usd(fc.finished.marked_down)}, less 8% selling cost = {usd(fc.finished.after_selling)}
        {fc.finished.mortgage_ahead > 0 && <>, less the mortgage ahead {usd(fc.finished.mortgage_ahead)} and its growth {usd(fc.finished.mortgage_growth)}</>}, less a foreclosure of about {fc.finished.months} months ({usd(fc.finished.foreclosure_cost)}).
        {fc.estimate && " The months and the cost are RKB's estimate for this state."}{fc.worst_case && " This state is not set up, so the slowest and costliest foreclosure we know is assumed."}</>} /> },
    { order: b.passed ? 2 : 1, el: <Rule key="b" tone={b.passed ? "pass" : waiting ? "needed" : "look"} word={b.passed ? "Pass" : waiting ? "After the remaining details" : "For Manju Sir"} name="Budget test · Manju Sir"
      line={b.passed ? `Within the usual range for this scope${b.range ? ` ($${b.range[0]} to $${b.range[1]} a square foot)` : ""}, with contingency of ${pct(b.contingency_share)}.`
        : waiting ? `Run once these are in: ${(b.pending || []).join(", ")}. It does not hold the answer above.` : `${b.looks.join(" ")} This does not hold the answer above: Manju Sir looks and answers on the file page.`}
      figure={b.per_sqft !== null ? `$${Math.round(b.per_sqft)}` : "—"} under="a square foot"
      working={<>Builder&apos;s cost {usd(m.work, true)} over {num(b.area)} sq ft.{b.contingency_share !== null && !(b.pending || []).includes("the contingency") ? ` Contingency is ${pct(b.contingency_share)} of the work; at least 10% is asked.` : " At least 10% contingency is asked."} A budget outside the range is not refused: one person looks and answers on the file page.</>} /> },
  ].sort((x, y) => x.order - y.order);
  const st = a.state_name;
  const title = good ? "RKB can fund the deal" : exc ? "For Rakesh Sir to decide" : a.verdict === "incomplete" ? "Not enough to check yet" : a.rejected_second_mortgage ? "Rejected: second mortgage" : a.verdict === "fits" ? "The numbers pass; this state is not set up" : "RKB cannot fund this deal as entered";
  return (
    <>
      <Celebrate fire={fire} />
      <PageHead n={2} title="Quick check" line="The answer of RKB's rules on what page 1 holds. Nothing is entered here."
        holds={<>Holding the next step: <b className="text-fg">{g.verdict_ok ? "nothing" : a.new_state ? "this state is not set up" : "the rules below"}</b></>} />
      <Panel className={cn("px-4 py-3.5 border-l-4 flex flex-wrap items-center gap-x-6 gap-y-2", good ? "border-l-good border-good/50 bg-good-soft" : exc ? "border-l-high" : "border-l-critical")}>
        {good && <span className="h-11 w-11 rounded-full bg-good text-white grid place-items-center shrink-0"><PartyPopper size={22} /></span>}
        <div className="min-w-0">
          <div className={cn("font-bold", good ? "text-[24px] text-good leading-tight" : "text-[20px]")}>{title}</div>
          <div className={good ? "text-fg" : "text-muted"}>{good ? <>{pct(r.ratio)} of ARV against a limit of 65%. The 65% rule and the foreclosure test both pass.{!m.commission.known && " ROI Blocks' commission is added once the price to the owner is entered."}</> : a.headline}</div>
        </div>
        <div className="ml-auto text-right"><div className="text-[11px] uppercase tracking-[.05em] text-faint font-semibold">{r.room >= 0 ? "Room under the 65% limit" : "Over the 65% limit by"}</div><b className="text-[17px] tnum">{usd(Math.abs(r.room))}</b></div>
        <div className="w-full flex flex-wrap gap-2 justify-end">
          <Button onClick={() => go(1)}>Change the numbers</Button>
          <Button variant="primary" disabled={!g.steps[2]?.open} onClick={() => go(g.steps[3]?.open && !more ? 4 : 3)}>{more ? `Go on: ${more} more detail${more === 1 ? "" : "s"}` : g.steps[3]?.open ? "Go to the file" : "Go to the remaining details"}</Button>
        </div>
      </Panel>
      {a.reasons.length > 0 && (
        <Panel className="mt-3 px-3.5 py-3 border-critical/40"><h3 className="font-semibold mb-1">Why</h3>
          <ul className="flex flex-col gap-1.5">{a.reasons.map((x) => <li key={x} className="flex gap-2"><Status tone="fail">{""}</Status><span>{x}</span></li>)}</ul></Panel>
      )}
      {a.new_state && <NewState f={f} />}
      <Panel className="mt-3">{rules.map((x) => x.el)}</Panel>
      {a.verdict !== "incomplete" && <Waterfall a={a} />}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3 items-start">
        <Panel className="px-3.5 py-3"><h3 className="font-semibold mb-1.5">Where RKB&apos;s money goes</h3>
          <MoneyTable rows={m.lines.map((l) => ({ label: l.label, amount: usd(l.amount, true) }))} total={{ label: "RKB pays out", amount: usd(m.cash_out, true) }} /></Panel>
        <Panel className="px-3.5 py-3"><h3 className="font-semibold mb-1.5">{a.new_state ? `${st} is not set up yet` : `What ${st ?? "the state"} changes`}</h3>
          <MoneyTable rows={[
            { label: "Lien", amount: a.lien_position === "second" ? "Second, behind one mortgage" : "First" },
            { label: "If RKB must foreclose", amount: `About ${fc.finished.months} months, ${usd(fc.finished.foreclosure_cost)}` },
            ...(m.commission.known ? [{ label: "Note to RKB (the owner's number)", amount: usd(m.principal, true) }] : []),
            { label: "The note's rate, points, term", amount: `${r.rate}% · ${r.points} points · ${r.term_months} months${r.interest_free_months ? ` · ${r.interest_free_months} interest-free` : ""}` },
          ]} />
          {a.notices.length > 0 && <ul className="mt-2 flex flex-col gap-1.5 text-[12.5px] text-muted list-disc pl-4">{a.notices.map((x) => <li key={x}>{x}</li>)}</ul>}
        </Panel>
      </div>
    </>
  );
}
