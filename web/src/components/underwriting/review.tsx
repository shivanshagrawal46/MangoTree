"use client";

/* Step 6 · Review and sign. The whole deal on one page, in a single column:
   the steps before this one, the deal, the three rules, what rests on RKB's word,
   what was changed from the ARV file, then the declaration and the signature.
   Opens only when steps 1 to 5 are complete; only Rakesh Sir can sign. */

import * as React from "react";
import { Button } from "@/components/ui";
import { day, num, pct, usd, uw } from "@/lib/uw";
import { KV, PageHead, Panel, SectionHead, Status, linkBtn } from "./bits";
import type { PageProps } from "./workspace";

const LABEL: Record<string, string> = { arv: "Value after repair", as_is_value: "Value as it stands today", sqft_above: "Square feet above grade", beds: "Bedrooms", baths: "Bathrooms", state: "State", county: "County", address: "Address" };

export function ReviewPage({ f, go, run }: PageProps) {
  const a = f.assessment, g = f.gates, r = a.rule_65, fc = a.foreclosure, b = a.budget;
  const [busy, setBusy] = React.useState(false);
  const comp = f.items.filter((i) => i.when === "file" && i.required);
  const word = f.items.filter((i) => i.confirmed);
  const signed = !!f.signed?.at;
  const fmt = (field: string, v: number | string) => (field === "arv" || field === "as_is_value" ? usd(v) : typeof v === "number" ? num(v) : String(v));
  return (
    <>
      <PageHead n={6} title="Review and sign" line="The whole deal on one page. Rakesh Sir reviews it and signs once."
        holds={<>Holding the signature: <b className="text-fg">{signed ? "signed" : g.ready_to_sign ? "nothing" : "the steps before it"}</b></>} />
      <div className="max-w-[840px] flex flex-col gap-3">
        <Panel className="overflow-hidden"><SectionHead title="Steps before this one" />
          <KV k="1 · Property and cost"><Status tone={g.steps[0].done ? "done" : "needed"}>{g.steps[0].done ? "Complete" : `${g.details_missing.length} answers needed`}</Status></KV>
          <KV k="2 · Quick check"><Status tone={g.steps[1].done ? "done" : "needed"}>{a.verdict === "fits" ? `RKB can fund the deal · ${pct(r.ratio)} of ARV` : a.verdict === "exception" ? "Passes; an exception Rakesh Sir approved" : a.headline}</Status></KV>
          <KV k="3 · Remaining details"><Status tone={g.steps[2].done ? "done" : "needed"}>{g.steps[2].done ? "Complete" : `${(g.more_missing || []).length} answers needed`}</Status></KV>
          <KV k="4 · The file"><Status tone={g.file_complete ? "done" : "needed"}>{g.file_complete ? `All ${comp.length} needed lines done` : `${comp.length - g.waiting_file.length} of ${comp.length} needed lines done`}</Status>
            {g.optional_open.length > 0 && <span className="text-muted font-normal">{g.optional_open.length} optional open</span>}</KV>
          <KV k="5 · Documents"><Status tone={g.steps[4].done ? "done" : "needed"}>{g.documents.total ? `${g.documents.approved} of ${g.documents.total} approved` : "Not written yet"}</Status></KV>
        </Panel>
        <Panel className="overflow-hidden"><SectionHead title="The deal" right={<button type="button" className={linkBtn} onClick={() => go(3)}>Change</button>} />
          <KV k="Property">{f.facts.address}</KV>
          <KV k="Borrower">{f.facts.owner_name}{f.facts.owner_kind === "llc" && f.facts.guarantor_name ? `, guaranteed by ${f.facts.guarantor_name}` : ""}</KV>
          <KV k="Note to RKB (the contract price)">{usd(a.money.principal, true)}</KV>
          <KV k="RKB's own money in the deal">{usd(a.money.at_risk, true)}<span className="font-normal text-muted">90% of the builder&apos;s cost, RKB&apos;s points and six months&apos; interest on it, ROI Blocks at closing</span></KV>
          <KV k="ROI Blocks is paid">{usd(a.money.commission.at_closing, true)} at closing<span className="font-normal text-muted">and {usd(a.money.commission.at_sale, true)} at the sale</span></KV>
          <KV k="The note's rate, points, term">{r.rate}% · {r.points} points · {r.term_months} months{r.interest_free_months ? ` · ${r.interest_free_months} interest-free` : ""}</KV>
          <KV k="Security">{a.lien_position === "second" ? "Second lien, behind one mortgage" : "First lien"}, {a.state_name}</KV>
        </Panel>
        <Panel className="overflow-hidden"><SectionHead title="The three rules" right={<button type="button" className={linkBtn} onClick={() => go(2)}>Open</button>} />
          <KV k="65% rule"><Status tone={r.passed ? "pass" : "fail"} /><span className="font-normal">{pct(r.ratio)} of ARV; {usd(r.exposure)} against a limit of {usd(r.limit_amount)}</span></KV>
          <KV k="Foreclosure test"><Status tone={fc.passed ? "pass" : "fail"} /><span className="font-normal">{usd(fc.finished.left_for_rkb)} left for RKB against {usd(fc.finished.rkb_out)} of RKB&apos;s money</span></KV>
          <KV k="Budget test"><Status tone={b.passed ? "pass" : "look"}>{b.passed ? "Pass" : "Looked at by Manju Sir"}</Status><span className="font-normal">{b.per_sqft !== null ? `$${Math.round(b.per_sqft)} a square foot` : ""}{b.range ? `; usual $${b.range[0]} to $${b.range[1]}` : ""}</span></KV>
        </Panel>
        <Panel className="overflow-hidden"><SectionHead title={`Resting on RKB's word${word.length ? ` · ${word.length}` : ""}`} right={<button type="button" className={linkBtn} onClick={() => go(4)}>Open the file</button>} />
          {word.length ? word.map((i) => <KV key={i.key} k={i.name}><Status tone="confirmed">{i.confirmed!.by_name}, {day(i.confirmed!.at)}</Status>
            <span className="font-normal text-muted">{i.confirmed!.without_paper ? "without a paper" : i.confirmed!.over_a_failed_reading ? "over the system's reading" : "with its record"}{i.confirmed!.note ? ` · ${i.confirmed!.note}` : ""}</span></KV>)
            : <KV k="Lines confirmed by RKB">None: every line passed on its own paper or its own record.</KV>}
          {f.items.filter((i) => i.deferred).map((i) => <KV key={i.key} k={i.name}><Status tone="confirmed">Allowed after closing · {i.deferred!.by_name}, {day(i.deferred!.at)}</Status>
            <span className="font-normal text-muted">an exception for this loan{i.deferred!.note ? ` · ${i.deferred!.note}` : ""}</span></KV>)}
        </Panel>
        {(f.edited || []).length > 0 && (
          <Panel className="overflow-hidden"><SectionHead title="Changed from the ARV file" />
            {f.edited.map((e) => <KV key={e.field} k={LABEL[e.field] || e.field}><span className="font-normal">{fmt(e.field, e.arv_file)} in the ARV file</span><span>→ {fmt(e.field, e.now)} on page 1</span></KV>)}
          </Panel>
        )}
        <Panel className="px-3.5 py-3.5 flex flex-col gap-2.5">
          {signed ? <Status tone="done">Signed by {f.signed!.by_name}, {day(f.signed!.at)}. Clear to close.</Status> : <>
            {f.signed?.voided_because && <span className="text-high text-[12.5px]">An earlier signature no longer stands. {f.signed.voided_because}.</span>}
            <span>I have reviewed this file and its {g.documents.total || ""} documents. The loan is clear to close on these terms.</span>
            <div className="flex flex-wrap items-center gap-2.5">
              <Button variant="primary" disabled={busy || !g.ready_to_sign || !f.me.can_sign} onClick={async () => { setBusy(true); const n = await run(uw.sign(f.file_id), "Signed: clear to close."); setBusy(false); if (n) go(7, n); }}>Sign: clear to close</Button>
              <span className="text-[12px] text-muted">{!f.me.can_sign ? "Rakesh Sir signs." : "Signed as Rakesh Sir, with the date and time. A change on page 1 or page 3 after this takes the signature away."}</span>
            </div></>}
        </Panel>
      </div>
    </>
  );
}
