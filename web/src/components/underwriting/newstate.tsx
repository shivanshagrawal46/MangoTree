"use client";

/* A state RKB has not lent in. The quick check has already answered on the worst
   case; the file and the documents stay shut until this state has its own rules.
   Claude reads that state's statutes and writes the sheet once, with the extra
   papers the state needs and the source of each rule. A person at RKB reads it,
   unticks any extra paper they do not want, and approves it with RKB's password;
   from then on the state is like the others. The short answer is shown; the full
   wording of each rule is behind one click. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { subscribeJob } from "@/lib/api";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { PARTY, errText, usd, uwStates, type UwFile } from "@/lib/uw";
import { KV, Lab, Panel, SectionHead, Status, input, linkBtn } from "./bits";

const WHEN: Record<string, string> = { file: "before closing", closing: "at closing", after: "after closing" };

export function NewState({ f }: { f: UwFile }) {
  const qc = useQueryClient();
  const code = f.assessment.state || "";
  const q = useQuery({ queryKey: ["uw", "state", code], queryFn: () => uwStates.get(code), retry: false, staleTime: 30_000 });
  const [busy, setBusy] = React.useState<string | null>(null);
  const [pw, setPw] = React.useState("");
  const [full, setFull] = React.useState(false);
  const [drop, setDrop] = React.useState<string[]>([]);
  const row = q.data, s = row?.sheet;
  const proposed = !!row && row.status !== "approved";
  const write = async () => {
    setBusy("Starting…");
    try {
      const { job_id } = await uwStates.propose(code);
      subscribeJob(job_id, (e) => { if (e.kind === "status" && e.data?.text) setBusy(String(e.data.text)); if (e.kind === "error") toast.error(e.data?.error || "The rules could not be written"); },
        () => { setBusy(null); setDrop([]); qc.invalidateQueries({ queryKey: ["uw", "state", code] }); });
    } catch (e) { toast.error(errText(e)); setBusy(null); }
  };
  const approve = async () => {
    if (!pw) { toast.error("Enter RKB's confirmation password."); return; }
    setBusy("Approving…");
    try { await uwStates.approve(code, pw, drop); toast.success(`${s?.name ?? code} is set up.`); setPw(""); await qc.invalidateQueries({ queryKey: ["uw"] }); } catch (e) { toast.error(errText(e)); }
    setBusy(null);
  };
  const long: [string, string][] = s ? [["Contractor's licence", s.licence_register], ["Lender's licence", s.lender_licence], ["Rate and points", s.rate_limits.join(" ")], ["Contractors' liens", s.mechanics_lien],
    ["The instrument must have", s.instrument_must_have.join(" · ")], ["The contractor's contract must have", s.contract_must_have.join(" · ")]] : [];
  return (
    <Panel className="mt-3 overflow-hidden">
      <SectionHead title={<>{s?.name ?? code} is a state RKB has not lent in</>}
        right={row ? <Status tone={proposed ? "check" : "done"}>{proposed ? "Rules written, to be approved" : "Approved"}</Status> : <Status tone="needed">Rules not written</Status>} />
      {!s ? (
        <div className="px-3.5 py-3 flex flex-col items-start gap-2.5">
          <ul className="text-muted list-disc pl-4 flex flex-col gap-0.5">
            <li>The tests on this page already ran on the slowest and costliest foreclosure we know.</li>
            <li>The file and the documents open once this state has its own rules.</li>
            <li>Claude reads the state&apos;s statutes and writes them, with the extra papers this state needs. It takes a few minutes; RKB approves the result.</li>
          </ul>
          {f.me.can_confirm ? <Button variant="primary" disabled={!!busy} onClick={write}>{busy ? "Writing…" : `Write the rules for ${code}`}</Button> : <span className="text-muted">RKB starts this.</span>}
          {busy && <span className="text-[12.5px] text-info">{busy}</span>}
        </div>
      ) : (
        <>
          <KV k="Security instrument">{s.instrument}</KV>
          <KV k="If RKB must foreclose">{s.foreclosure.route}<span className="font-normal text-muted"> · about {s.foreclosure.months} months, {usd(s.foreclosure.cost)} (estimates)</span></KV>
          <KV k="Recording tax"><span className="font-normal">{s.recording_tax.basis}</span></KV>
          {full && long.filter(([, v]) => v).map(([k, v]) => <KV key={k} k={k} className="items-start"><span className="font-normal text-[12.5px]">{v}</span></KV>)}
          <div className="px-3.5 py-2 border-b border-line"><button type="button" className={cn(linkBtn, "font-normal text-[12.5px]")} onClick={() => setFull(!full)}>{full ? "Hide the full rules" : "Show the full rules: licences, rate limits, liens, what the papers must contain"}</button></div>
          <div className="px-3.5 py-2.5"><Lab>Extra papers this state adds to the file · {s.extra_lines.length - drop.length}</Lab>
            {s.extra_lines.length ? <ul className="mt-1.5 flex flex-col">{s.extra_lines.map((l) => {
              const off = drop.includes(l.key);
              return (
                <li key={l.key} className="flex gap-2.5 py-1.5 border-b border-line last:border-b-0 items-start">
                  {proposed && f.me.can_confirm && <input type="checkbox" className="mt-1 accent-[var(--accent)]" checked={!off} aria-label={`Keep ${l.name}`} onChange={() => setDrop(off ? drop.filter((k) => k !== l.key) : [...drop, l.key])} />}
                  <div className={cn("min-w-0", off && "opacity-50 line-through")}>
                    <b className="font-semibold">{l.name}</b> <span className="text-faint">{PARTY[l.party]} · {WHEN[l.when] ?? l.when} · {l.required ? "compulsory" : "optional"}</span>
                    <div className="text-muted">{l.label}</div>
                    {full && <div className="text-faint text-[12.5px]">{l.why}</div>}
                  </div>
                </li>);
            })}</ul> : <p className="text-muted mt-1">None: this state asks for nothing beyond the standard list.</p>}
          </div>
          {s.unsure.length > 0 && <div className="px-3.5 py-2.5 border-t border-line bg-high-soft text-high"><b>Claude could not confirm {s.unsure.length} {s.unsure.length === 1 ? "point" : "points"} on the state&apos;s own pages.</b>
            {full ? <ul className="mt-1 list-disc pl-4 flex flex-col gap-0.5 text-[12.5px]">{s.unsure.map((x) => <li key={x}>{x}</li>)}</ul> : <> Open the full rules to read them before approving.</>}</div>}
          {full && <div className="px-3.5 py-2.5 border-t border-line text-[12px] text-muted"><Lab>Sources</Lab>
            <ul className="mt-1 flex flex-col gap-0.5">{s.sources.slice(0, 20).map((x, i) => <li key={i}>{x.rule}: {x.url ? <a className="text-accent hover:underline" href={x.url} target="_blank" rel="noreferrer">{x.citation}</a> : x.citation}</li>)}</ul></div>}
          {proposed && f.me.can_confirm && (
            <div className="px-3.5 py-3 border-t border-line flex flex-wrap items-center gap-2">
              <input className={input + " max-w-[280px]"} type="password" autoComplete="new-password" placeholder="RKB's confirmation password" value={pw} onChange={(e) => setPw(e.target.value)} />
              <Button variant="primary" size="sm" className="h-8" disabled={!!busy} onClick={approve}>Approve these rules for {s.name}</Button>
              <Button size="sm" className="h-8" disabled={!!busy} onClick={write}>{busy ? "Writing…" : "Write them again"}</Button>
              {busy && <span className="text-[12.5px] text-info">{busy}</span>}
            </div>
          )}
          {proposed && !f.me.can_confirm && <div className="px-3.5 py-2.5 border-t border-line text-muted">RKB approves these rules; the file opens then.</div>}
        </>
      )}
    </Panel>
  );
}
