"use client";

/* Step 1 · Property and deal. Every basic fact of the house and the loan, entered
   once, in six sections. A section shows what was entered, with "Change"; a section
   that still lacks an answer opens ready to fill. Anything can be changed, the
   value after repair and the square footage included: the page keeps what the ARV
   file said beside the edited figure, with "put back". Nothing here is a paper. */

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { num, usd, uw, type Assessment, type Facts, type Lien, type UwConfig } from "@/lib/uw";
import { KV, PageHead, Panel, SectionHead, Status, input, linkBtn } from "./bits";
import type { PageProps } from "./workspace";

type Kind = "text" | "money" | "number" | "percent" | "select" | "yesno" | "date" | "long";
type F = { key: keyof Facts; label: string; kind: Kind; required?: boolean; unit?: string; options?: { key: string; label: string }[]; list?: string[]; show?: (f: Facts) => boolean; hint?: string };

function sections(cfg: UwConfig | undefined, f: Facts): { key: string; title: string; fields: F[] }[] {
  const person = f.owner_kind === "individual" || !f.owner_kind;
  return [
    { key: "property", title: "The property", fields: [
      { key: "address", label: "Address", kind: "text", required: true },
      { key: "state", label: "State (two letters)", kind: "text", required: true, list: (cfg?.states || []).map((s) => s.code) },
      { key: "county", label: "County or city", kind: "text", required: f.state === "MD", list: f.state === "MD" ? cfg?.md_counties : undefined },
      { key: "arv", label: "Value after repair (ARV)", kind: "money", required: true },
      /* Needed only when the value today is RKB's risk: a mortgage on the house, or cash to the owner (the server decides the same way). */
      { key: "as_is_value", label: "Value as it stands today", kind: "money", required: (f.liens || []).some((l) => Number(l.balance) > 0) || Number(f.cash_to_owner) > 0,
        hint: "only needed with a mortgage or cash to the owner" },
      { key: "sqft_above", label: "Square feet above grade", kind: "number", required: true, unit: "sq ft" },
      { key: "sqft_below", label: "Square feet below grade", kind: "number", required: true, unit: "sq ft", hint: "0 if none" },
      { key: "measured", label: "Has the area been measured, with a sketch?", kind: "yesno", required: true },
      { key: "beds", label: "Bedrooms after the work", kind: "number" },
      { key: "baths", label: "Bathrooms after the work", kind: "number" },
    ] },
    { key: "owner", title: "The owner", fields: [
      { key: "owner_kind", label: "Who owns the house", kind: "select", required: true, options: cfg?.owner_kinds },
      { key: "owner_name", label: "Owner's name as on the deed", kind: "text", required: true },
      { key: "owner_lives_there", label: "Does the owner live in the house?", kind: "yesno", required: true },
      { key: "borrower_address", label: "Owner's mailing address (not the property)", kind: "text" },
      { key: "signer_name", label: "Who signs for the owner", kind: "text", show: () => !person },
      { key: "guarantor_name", label: "Personal guarantor", kind: "text", show: () => f.owner_kind === "llc" },
    ] },
    { key: "mortgage", title: "Mortgage on the house", fields: [
      { key: "has_mortgage", label: "Is there a mortgage on the house?", kind: "yesno", required: true },
    ] },
    { key: "work", title: "The work", fields: [
      { key: "builder_cost", label: "Builder's cost of the work", kind: "money", required: true },
      { key: "contingency", label: "Contingency", kind: "money", required: true, hint: "0 if none" },
      { key: "scope", label: "Scope", kind: "select", required: true, options: cfg?.scopes },
      { key: "contractor_name", label: "Contractor's exact legal name", kind: "text" },
      { key: "contractor_licence", label: "Contractor's licence number", kind: "text" },
      { key: "completion_date", label: "Work complete by", kind: "date" },
    ] },
    { key: "loan", title: "The loan", fields: [
      { key: "contract_price", label: "Contract price to the owner (the note)", kind: "money", required: true },
      { key: "closing_costs", label: "Closing costs, estimated", kind: "money", required: true, hint: "0 if not known yet" },
      { key: "cash_to_owner", label: "Cash to the owner", kind: "money" },
      { key: "rate", label: "Rate", kind: "percent", unit: "% a year" },
      { key: "points", label: "Points", kind: "percent" },
      { key: "term_months", label: "Term", kind: "number", unit: "months" },
      { key: "interest_free_months", label: "Interest-free months", kind: "number", unit: "months" },
    ] },
    { key: "closing", title: "The closing", fields: [
      { key: "title_company", label: "Title company", kind: "text" },
      { key: "closing_date", label: "Closing date", kind: "date" },
      { key: "tax_id", label: "Tax ID of the property", kind: "text" },
      { key: "legal_description", label: "Legal description (from the title commitment)", kind: "long" },
    ] },
  ];
}

const DEFAULTS: Partial<Record<keyof Facts, string>> = { rate: "18", points: "2", term_months: "12", interest_free_months: "0", cash_to_owner: "0" };
const blank = (v: unknown) => v === undefined || v === null || v === "";

function show(field: F, v: unknown, cfg?: UwConfig): React.ReactNode {
  if (blank(v)) return <span className="text-faint font-normal">{DEFAULTS[field.key] !== undefined ? `${DEFAULTS[field.key]}${field.unit ? (field.kind === "percent" ? "" : " ") + field.unit : ""}` : "Not entered"}</span>;
  if (field.kind === "money") return usd(v as number, true);
  if (field.kind === "yesno") return v ? "Yes" : "No";
  if (field.kind === "select") return field.options?.find((o) => o.key === v)?.label ?? String(v);
  if (field.kind === "number") return `${num(v as number)}${field.unit ? " " + field.unit : ""}`;
  if (field.kind === "percent") return `${v}${field.unit ?? ""}`;
  if (field.key === "state") return cfg?.states.find((s) => s.code === v)?.name ?? String(v);
  return String(v);
}

const LIEN_BLANK: Lien = { holder: "", balance: "", status: "current", payoff_at_closing: false, monthly_payment: "", arrears: "" };

/* ROI Blocks' share: worked out on the server from the note and the builder's cost, never typed in.
   A quarter of the margin at closing, the same again when the house sells. */
function Commission({ c }: { c: Assessment["money"]["commission"] }) {
  const tag = <span className="text-[11px] text-info font-normal">worked out, not entered</span>;
  const wait = <span className="text-faint font-normal">Worked out once the contract price and the builder&apos;s cost are in</span>;
  return (
    <>
      <KV k="Paid to ROI Blocks at closing">{c.known ? <><span>{usd(c.at_closing, true)}</span>{tag}</> : wait}</KV>
      <KV k="Paid to ROI Blocks at the sale">{c.known ? <><span>{usd(c.at_sale, true)}</span>{tag}</> : wait}</KV>
      {c.known && (
        <div className="px-3.5 py-2 text-[12px] text-muted">
          A quarter of the margin each time. Margin {usd(c.margin, true)} = the note {usd(c.note, true)}
          {c.beside > 0 && <>, less {usd(c.beside, true)} in it that is not renovation (mortgage paid off, missed payments, cash to the owner)</>}
          , less the builder&apos;s cost {usd(c.builder_cost, true)}, less RKB&apos;s 2 points {usd(c.points, true)} and six months at 14% {usd(c.interest, true)}, both on 90% of the builder&apos;s cost.
          {c.margin <= 0 && <b className="text-high"> No margin is left, so nothing is paid to ROI Blocks.</b>}
        </div>
      )}
    </>
  );
}

export function DetailsPage({ f, go, run }: PageProps) {
  const cfg = useQuery({ queryKey: ["uw", "config"], queryFn: uw.config, staleTime: Infinity }).data;
  const facts = f.facts;
  const secs = sections(cfg, facts).map((s) => ({ ...s, fields: s.fields.filter((x) => !x.show || x.show(facts)) }));
  const lacking = (s: { key: string; fields: F[] }) => s.fields.some((x) => x.required && blank(facts[x.key])) || (s.key === "mortgage" && facts.has_mortgage === true && !(facts.liens || []).length);
  const [open, setOpen] = React.useState<Record<string, boolean>>({});
  const [draft, setDraft] = React.useState<Record<string, Record<string, string | boolean>>>({});
  const [liens, setLiens] = React.useState<Lien[] | null>(null);
  const [busy, setBusy] = React.useState(false);
  const edited = Object.fromEntries((f.edited || []).map((e) => [e.field, e]));
  const locked = false;   // never locked: a change after the signature is allowed, and the server takes the signature away and says so
  const signed = !!f.gates.signed;

  const isOpen = (s: { key: string; fields: F[] }) => open[s.key] ?? lacking(s);
  const val = (s: string, k: string) => {
    const d = draft[s]?.[k];
    if (d !== undefined) return d;
    const v = facts[k as keyof Facts];
    return blank(v) ? (DEFAULTS[k as keyof Facts] ?? "") : (v as string | boolean);
  };
  const set = (s: string, k: string, v: string | boolean) => setDraft((d) => ({ ...d, [s]: { ...(d[s] || {}), [k]: v } }));

  async function saveSection(s: { key: string; fields: F[] }) {
    const out: Record<string, unknown> = {};
    for (const x of s.fields) {
      const v = val(s.key, x.key);
      if (x.kind === "yesno") { if (typeof v === "boolean") out[x.key] = v; continue; }
      const str = String(v ?? "").trim();
      if (["money", "number", "percent"].includes(x.kind)) out[x.key] = str === "" ? null : Number(str.replace(/[$,\s]/g, ""));
      else out[x.key] = x.key === "state" ? str.toUpperCase().slice(0, 2) : str;
    }
    if (s.key === "mortgage") out.liens = out.has_mortgage ? (liens ?? facts.liens ?? []).filter((l) => String(l.holder).trim() || Number(l.balance) > 0)
      .map((l) => ({ ...l, balance: Number(String(l.balance).replace(/[$,\s]/g, "")) || 0, monthly_payment: Number(String(l.monthly_payment ?? "").replace(/[$,\s]/g, "")) || 0, arrears: Number(String(l.arrears ?? "").replace(/[$,\s]/g, "")) || 0 })) : [];
    setBusy(true);
    const before = f.assessment.verdict;
    const next = await run(uw.setFacts(f.file_id, out as Facts));
    setBusy(false);
    if (!next) return;
    setOpen((o) => ({ ...o, [s.key]: false })); setDraft((d) => ({ ...d, [s.key]: {} })); setLiens(null);
    if (next.assessment.verdict !== before && !next.gates.details_missing.length) go(2);
  }

  const lienRows = liens ?? (facts.liens?.length ? facts.liens : [LIEN_BLANK]);
  const setLien = (i: number, patch: Partial<Lien>) => setLiens(lienRows.map((l, n) => (n === i ? { ...l, ...patch } : l)));
  const missing = f.gates.details_missing;

  return (
    <>
      <PageHead n={1} title="Property and deal" line="Every basic fact of the house and the loan, entered once. Nothing on this page is a paper."
        holds={<>Holding the next step: <b className="text-fg">{missing.length ? `${missing.length} answer${missing.length === 1 ? "" : "s"}` : "nothing"}</b></>} />
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,780px)_280px] gap-3 items-start">
        <div className="flex flex-col gap-3">
          {secs.map((s) => {
            const on = isOpen(s) && !locked;
            return (
              <Panel key={s.key} className="overflow-hidden">
                <SectionHead title={<><Status tone={lacking(s) ? "needed" : "done"}>{""}</Status>{s.title}</>}
                  right={on ? <span className="flex gap-1.5"><Button size="sm" disabled={busy} onClick={() => { setOpen((o) => ({ ...o, [s.key]: false })); setDraft((d) => ({ ...d, [s.key]: {} })); setLiens(null); }}>Cancel</Button><Button size="sm" variant="primary" disabled={busy} onClick={() => saveSection(s)}>Save</Button></span>
                    : !locked && <button type="button" className={linkBtn} onClick={() => setOpen((o) => ({ ...o, [s.key]: true }))}>Change</button>} />
                {s.fields.map((x) => {
                  const e = edited[x.key as string];
                  const fromArv = f.from_arv && !blank(f.from_arv[x.key]) && !e;
                  return (
                    <KV key={x.key} k={<>{x.label}{x.required && on && <span className="text-faint"> · needed</span>}</>}>
                      {on ? (
                        x.kind === "yesno" ? (
                          <span className="inline-flex border border-line rounded-lg overflow-hidden">
                            {[["Yes", true], ["No", false]].map(([l, b]) => <button key={String(l)} type="button" onClick={() => set(s.key, x.key, b as boolean)} className={cn("px-3 h-8 text-[13px] border-r border-line last:border-r-0", val(s.key, x.key) === b ? "bg-accent-soft text-accent font-semibold" : "text-muted")}>{l}</button>)}
                          </span>
                        ) : x.kind === "select" ? (
                          <select className={cn(input, "max-w-[320px]")} value={String(val(s.key, x.key) ?? "")} onChange={(ev) => set(s.key, x.key, ev.target.value)}>
                            <option value="">Choose</option>{(x.options || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                          </select>
                        ) : x.kind === "long" ? (
                          <textarea className={cn(input, "h-20 py-1.5")} value={String(val(s.key, x.key) ?? "")} onChange={(ev) => set(s.key, x.key, ev.target.value)} />
                        ) : (
                          <span className="flex items-center gap-2 w-full max-w-[380px]">
                            <input className={input} type={x.kind === "date" ? "date" : "text"} inputMode={["money", "number", "percent"].includes(x.kind) ? "decimal" : undefined} list={x.list ? `dl-${x.key}` : undefined}
                              value={String(val(s.key, x.key) ?? "")} placeholder={x.hint} onChange={(ev) => set(s.key, x.key, ev.target.value)} />
                            {x.list && <datalist id={`dl-${x.key}`}>{x.list.map((o) => <option key={o} value={o} />)}</datalist>}
                            {x.unit && <span className="text-muted text-[12px] whitespace-nowrap font-normal">{x.unit}</span>}
                          </span>
                        )
                      ) : (
                        <>
                          <span>{show(x, facts[x.key], cfg)}</span>
                          {e && <span className="text-[11.5px] text-high font-normal">Edited · the ARV file said {x.kind === "money" ? usd(e.arv_file as number) : num(e.arv_file as number) || String(e.arv_file)}
                            {!locked && <> · <button type="button" className="underline" onClick={() => run(uw.setFacts(f.file_id, { [x.key]: e.arv_file } as Facts))}>put back</button></>}</span>}
                          {fromArv && <span className="text-[11px] text-info font-normal">from the ARV file</span>}
                        </>
                      )}
                    </KV>
                  );
                })}
                {s.key === "loan" && !on && <Commission c={f.assessment.money.commission} />}
                {s.key === "mortgage" && (on ? val("mortgage", "has_mortgage") === true : facts.has_mortgage) && (
                  <div className="px-3.5 py-2.5 border-t border-line">
                    {on ? (
                      <div className="flex flex-col gap-2">
                        {lienRows.map((l, i) => (
                          <div key={i} className="grid grid-cols-2 md:grid-cols-[1.4fr_1fr_1fr_1.1fr_1.2fr_auto] gap-1.5 items-center">
                            <input className={input} placeholder="Lender" value={l.holder} onChange={(ev) => setLien(i, { holder: ev.target.value })} />
                            <input className={input} placeholder="Balance" inputMode="decimal" value={String(l.balance ?? "")} onChange={(ev) => setLien(i, { balance: ev.target.value })} />
                            <input className={input} placeholder="Monthly payment" inputMode="decimal" value={String(l.monthly_payment ?? "")} onChange={(ev) => setLien(i, { monthly_payment: ev.target.value })} />
                            <select className={input} value={l.status} onChange={(ev) => setLien(i, { status: ev.target.value })}>{(cfg?.lien_status || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select>
                            <select className={input} value={l.payoff_at_closing ? "paid" : "stays"} onChange={(ev) => setLien(i, { payoff_at_closing: ev.target.value === "paid" })}><option value="stays">Stays on the house</option><option value="paid">Paid off at closing</option></select>
                            <button type="button" aria-label="Remove this mortgage" className="h-8 w-8 grid place-items-center text-muted hover:text-fg" onClick={() => setLiens(lienRows.filter((_, n) => n !== i))}><X size={14} /></button>
                          </div>
                        ))}
                        <button type="button" className={cn(linkBtn, "self-start inline-flex items-center gap-1 text-[12.5px]")} onClick={() => setLiens([...lienRows, LIEN_BLANK])}><Plus size={13} /> Another mortgage</button>
                        <span className="text-[12px] text-muted">A house that would still carry two mortgages ahead of RKB is rejected.</span>
                      </div>
                    ) : (
                      <table className="w-full text-[13px]"><tbody>
                        {(facts.liens || []).map((l, i) => (
                          <tr key={i} className="border-b border-line last:border-b-0"><td className="py-1.5 font-medium">{l.holder}</td><td className="py-1.5 tnum">{usd(l.balance, true)}</td>
                            <td className="py-1.5 text-muted">{cfg?.lien_status.find((o) => o.key === l.status)?.label}</td><td className="py-1.5 text-muted text-right">{l.payoff_at_closing ? "Paid off at closing" : "Stays on the house"}</td></tr>
                        ))}
                      </tbody></table>
                    )}
                  </div>
                )}
              </Panel>
            );
          })}
        </div>
        <div className="flex flex-col gap-2.5 lg:sticky lg:top-3">
          <Panel className="px-3.5 py-3">
            <h3 className="font-semibold mb-1.5">{missing.length ? "Still needed on this page" : "Page 1 is complete"}</h3>
            {missing.length ? <ul className="flex flex-col gap-1">{missing.map((m) => <li key={m}><Status tone="needed">{m}</Status></li>)}</ul>
              : secs.map((s) => <div key={s.key} className="py-0.5"><Status tone="done">{s.title}</Status></div>)}
            <p className="text-[12px] text-muted mt-2">The quick check opens when every answer is in.</p>
          </Panel>
          {(f.edited || []).length > 0 && (
            <Panel className="px-3.5 py-3"><h3 className="font-semibold mb-1">{f.edited.length} figure{f.edited.length === 1 ? "" : "s"} changed from the ARV file</h3>
              <p className="text-[12px] text-muted">The file keeps what the ARV file said, who changed it and when. Rakesh Sir sees the list at review.</p></Panel>
          )}
          {signed && <Panel className="px-3.5 py-3 text-[12px] text-high">Rakesh Sir has signed this file. A change on this page takes his signature and the document approvals away.</Panel>}
          <Button variant="primary" disabled={!f.gates.steps[1].open} onClick={() => go(2)}>Go to the quick check</Button>
        </div>
      </div>
    </>
  );
}
