"use client";

/* The intake. Six short steps that take everything a lender's underwriter
   would ask for, in a fixed order, so no file arrives half-described:

     1 Address      checked live against the US Census geocoder
     2 Documents    inspection, appraisal, bid, listing, photos — read by the
                    model; every fact is offered, none applied silently
     3 The house    rooms, structure, systems, site, condition, known issues
     4 The plan     planned work, scope tier or a contractor's bid, the house
                    as it will be (rooms, area, condition)
     5 The deal     price, exit, timing, financing, holding and selling costs
     6 Review       what will run, what it costs, run

   State is one object; each step reads and writes its slice. The right rail
   shows the file as it builds and how complete it is. */
import * as React from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Checkbox, Input as AntInput, Progress, Steps } from "antd";
import { ArrowLeft, ArrowRight, Building2, Check, CheckCircle2, ClipboardList, DollarSign, FileText, Hammer, Home, Loader2, MapPin, Search, Sparkles, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button, Card, Input, Textarea } from "@/components/arv/ui";
import { cn, money } from "@/lib/utils";
import { arvApi, ACQUISITION, COOLING, EXIT, EXTERIOR, FACT_LABEL, FOUNDATION, HEATING, KNOWN_ISSUES, OCCUPANCY, PARKING, PLANNED_WORK, PROPERTY_TYPE_LABEL, fmtFact,
  type ArvDocument, type ArvStatus, type Geocode, type PropertyType } from "@/lib/arv";
import { CONDITION_OPTIONS, Chip, Field, NumField, Seg, SelectField } from "./bits";
import { DocCard, DropZone, NoDocs, rehabLinesFrom, useDocUploader } from "./documents-drop";

const CompMap = dynamic(() => import("./comp-map"), { ssr: false });

type Form = {
  address: string; property_type: PropertyType; property_id: string; label: string; acquisition_type: string;
  docs: ArvDocument[]; fact_sources: Record<string, string>;
  beds: number | null; baths: number | null; sqft: number | null; lot_sqft: number | null; stories: number | null; year_built: number | null; garage_spaces: number | null; parking: string;
  basement: string; pool: string; waterfront: string; foundation: string; exterior: string; roof_age_years: number | null; heating: string; cooling: string;
  condition_current: string; occupancy: string; hoa_monthly: number | null; zoning: string; view: string; known_issues: string[]; notes: string;
  planned_work: string[]; scope: string; level: string; budget_mode: "tier" | "doc" | "lump"; budget_doc_id: string; budget_touched: boolean; lump_sum: number | null;
  beds_after: number | null; baths_after: number | null; sqft_after: number | null; condition_after: string;
  list_price: number | null; purchase_price: number | null; exit_strategy: string; months_held: number | null; rule_pct: number | null; profit_target_pct: number | null;
  finance: boolean; ltc_pct: number | null; ltarv_pct: number | null; rate_pct: number | null; points_pct: number | null;
  property_taxes_annual: number | null; insurance_annual: number | null; commission_pct: number | null;
  use_web_search: boolean; use_rentcast: boolean; radius_mi: number | null; months: number | null; web_searches: number | null;
};

const INITIAL: Form = {
  address: "", property_type: "single_family", property_id: "", label: "", acquisition_type: "mls", docs: [], fact_sources: {},
  beds: null, baths: null, sqft: null, lot_sqft: null, stories: null, year_built: null, garage_spaces: null, parking: "", basement: "unknown", pool: "unknown", waterfront: "unknown",
  foundation: "", exterior: "", roof_age_years: null, heating: "", cooling: "", condition_current: "", occupancy: "", hoa_monthly: null, zoning: "", view: "", known_issues: [], notes: "",
  planned_work: [], scope: "moderate", level: "mid", budget_mode: "tier", budget_doc_id: "", budget_touched: false, lump_sum: null, beds_after: null, baths_after: null, sqft_after: null, condition_after: "C2",
  list_price: null, purchase_price: null, exit_strategy: "flip", months_held: 8, rule_pct: 70, profit_target_pct: 15, finance: true, ltc_pct: 85, ltarv_pct: 70, rate_pct: 10.5, points_pct: 2,
  property_taxes_annual: null, insurance_annual: null, commission_pct: 5,
  use_web_search: true, use_rentcast: true, radius_mi: 1.0, months: 12, web_searches: 10,
};

const STEPS = [
  { key: "address", label: "Address", icon: MapPin, blurb: "Where the house is, and what kind of house." },
  { key: "documents", label: "Documents", icon: FileText, blurb: "Anything on paper: inspection, appraisal, bids, listing, photos." },
  { key: "house", label: "The house", icon: Home, blurb: "Rooms, structure, systems, site and condition today." },
  { key: "plan", label: "The plan", icon: Hammer, blurb: "What will be done, what it costs, what the house becomes." },
  { key: "deal", label: "The deal", icon: DollarSign, blurb: "Price, exit, timing, money." },
  { key: "review", label: "Review & run", icon: ClipboardList, blurb: "Check the file and start the analysis." },
] as const;

type StepKey = (typeof STEPS)[number]["key"];

/* Map a document fact onto the form. */
const FACT_TO_FORM: Record<string, keyof Form> = { property_type: "property_type", beds: "beds", baths: "baths", sqft: "sqft", lot_sqft: "lot_sqft", year_built: "year_built", stories: "stories", garage_spaces: "garage_spaces",
  basement: "basement", pool: "pool", condition_current: "condition_current", roof_age_years: "roof_age_years", heating: "heating", cooling: "cooling", foundation: "foundation", exterior: "exterior",
  occupancy: "occupancy", hoa_monthly: "hoa_monthly", property_taxes_annual: "property_taxes_annual", zoning: "zoning", waterfront: "waterfront" };

export function Wizard({ status, initialAddress, initialPropertyId }: { status?: ArvStatus; initialAddress?: string; initialPropertyId?: string }) {
  const router = useRouter();
  const [f, setF] = React.useState<Form>({ ...INITIAL, address: initialAddress || "", property_id: initialPropertyId || "" });
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((s) => ({ ...s, [k]: v }));
  const [step, setStep] = React.useState<StepKey>("address");
  const idx = STEPS.findIndex((s) => s.key === step);

  // ----------------------------------------------------------- geocode
  const [geo, setGeo] = React.useState<Geocode | null>(null);
  const [geoErr, setGeoErr] = React.useState<string | null>(null);
  const [checking, setChecking] = React.useState(false);
  const addr = f.address.trim();
  const setAddress = (v: string) => { set("address", v); setGeo(null); setGeoErr(null); };
  React.useEffect(() => {
    if (addr.length < 8) return;
    const t = setTimeout(async () => {
      setChecking(true);
      try { setGeo(await arvApi.geocode(addr)); } catch (e: any) { setGeoErr(e?.message || "not found"); } finally { setChecking(false); }
    }, 600);
    return () => clearTimeout(t);
  }, [addr]);

  // --------------------------------------------------------- documents
  const upsertDoc = (d: ArvDocument) => setF((s) => {
    const i = s.docs.findIndex((x) => x.doc_id === d.doc_id); const docs = s.docs.slice(); if (i === -1) docs.push(d); else docs[i] = { ...docs[i], ...d };
    // A priced scope on file is the budget unless the person already chose one; the tier estimate is only a placeholder.
    const priced = d.status === "done" && (d.extracted?.rehab_items.length || 0) > 0;
    if (priced && !s.budget_touched && s.budget_mode === "tier") return { ...s, docs, budget_mode: "doc", budget_doc_id: d.doc_id };
    return { ...s, docs };
  });
  const uploader = useDocUploader({ address: geo?.formatted || addr, onDocument: upsertDoc });
  const applyFacts = (d: ArvDocument, keys: string[]) => {
    const facts = d.extracted?.subject_facts || {};
    setF((s) => {
      const next: any = { ...s, fact_sources: { ...s.fact_sources } };
      for (const k of keys) {
        if (!(k in facts) || !(k in FACT_TO_FORM)) continue;
        let v: any = facts[k];
        if (k === "pool" || k === "waterfront") v = v ? "yes" : "no";
        next[FACT_TO_FORM[k]] = v;
        next.fact_sources[k] = d.filename;
      }
      return next;
    });
  };
  const bidDocs = f.docs.filter((d) => (d.extracted?.rehab_items.length || 0) > 0);

  // ----------------------------------------------------------- derived
  const scopes = status?.scopes || {};
  const stateMult = geo?.state && status?.state_cost_index ? status.state_cost_index[geo.state] : undefined;
  const effSqft = f.sqft_after || f.sqft;
  const sqftRate = scopes[f.scope] ? scopes[f.scope][f.level as "low" | "mid" | "high"] : null;
  const tierEstimate = sqftRate && effSqft ? Math.round(sqftRate * (stateMult || 1) * effSqft) : null;
  const bidDoc = f.docs.find((d) => d.doc_id === f.budget_doc_id);
  const budgetFigure = f.budget_mode === "lump" ? f.lump_sum : f.budget_mode === "doc" ? bidDoc?.extracted?.rehab_total || null : tierEstimate;
  const complete = completeness(f, !!geo);

  // ------------------------------------------------------------ create
  const create = useMutation({
    mutationFn: () => {
      const yn = (v: string) => (v === "unknown" || v === "" ? null : v === "yes");
      const rehab = f.budget_mode === "lump" && f.lump_sum ? { mode: "lump" as const, lump_sum: f.lump_sum, scope: f.scope }
        : f.budget_mode === "doc" && bidDoc ? { mode: "lines" as const, lines: rehabLinesFrom(bidDoc), scope: f.scope, source: bidDoc.filename, source_doc_id: bidDoc.doc_id }
        : { mode: "tier" as const, scope: f.scope, level: f.level as any };
      return arvApi.create({
        subject: {
          address: f.address, property_type: f.property_type, beds: f.beds, baths: f.baths, sqft: f.sqft, lot_sqft: f.lot_sqft, stories: f.stories, year_built: f.year_built, garage_spaces: f.garage_spaces,
          parking: f.parking || null, basement: f.basement as any, pool: yn(f.pool), waterfront: yn(f.waterfront), foundation: f.foundation || null, exterior: f.exterior || null, roof_age_years: f.roof_age_years,
          heating: f.heating || null, cooling: f.cooling || null, condition_current: (f.condition_current || null) as any, condition_after: f.condition_after as any, occupancy: (f.occupancy || null) as any,
          hoa_monthly: f.hoa_monthly, zoning: f.zoning || null, view: f.view || null, known_issues: f.known_issues, planned_work: f.planned_work, notes: f.notes || null,
          beds_after: f.beds_after, baths_after: f.baths_after, sqft_after: f.sqft_after, fact_sources: f.fact_sources,
        },
        rehab: rehab as any,
        assumptions: {
          purchase_price: f.purchase_price, list_price: f.list_price, months_held: f.months_held, rule_pct: f.rule_pct, profit_target_pct: f.profit_target_pct, exit_strategy: f.exit_strategy, acquisition_type: f.acquisition_type,
          finance: f.finance, ltc_pct: f.ltc_pct, ltarv_pct: f.ltarv_pct, rate_pct: f.rate_pct, points_pct: f.points_pct, property_taxes_annual: f.property_taxes_annual, insurance_annual: f.insurance_annual,
          hoa_monthly: f.hoa_monthly, commission_pct: f.commission_pct,
        },
        settings: { use_web_search: f.use_web_search, use_rentcast: f.use_rentcast, radius_mi: f.radius_mi, months: f.months, web_searches: f.web_searches },
        property_id: f.property_id || null, label: f.label || null, document_ids: f.docs.filter((d) => d.status !== "reading").map((d) => d.doc_id),
      });
    },
    onSuccess: ({ analysis_id }) => router.push(`/arv/${analysis_id}`),
    onError: (e: any) => toast.error(e?.message || "Could not start"),
  });

  const canLeaveAddress = !!geo;
  const next = () => { if (idx < STEPS.length - 1) setStep(STEPS[idx + 1].key); };
  const back = () => { if (idx > 0) setStep(STEPS[idx - 1].key); };
  const readingCount = f.docs.filter((d) => d.status === "reading").length;
  const readingDocs = readingCount > 0 || uploader.busy;

  return (
    <div className="min-h-screen">
      {/* ------------------------------------------------------ top bar */}
      <div className="px-6 pt-5 pb-4 border-b border-line bg-elev/70 backdrop-blur">
        <Link href="/arv" className="text-[11px] text-muted hover:text-fg flex items-center gap-1 mb-1"><ArrowLeft size={11} /> ARV &amp; underwriting</Link>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">New ARV file</h1>
            <div className="text-xs text-muted mt-0.5">{STEPS[idx].blurb}</div>
          </div>
          <Steps size="small" current={idx} onChange={(i) => { const k = STEPS[i].key; if (i === 0 || canLeaveAddress) setStep(k); }} className="arv-steps max-w-[760px]"
            items={STEPS.map((st, i) => { const Icon = st.icon; return { title: st.label, icon: i < idx ? undefined : <span className="grid place-items-center h-full"><Icon size={13} /></span>, disabled: i > 0 && !canLeaveAddress }; })} />
        </div>
      </div>

      <div className="p-6 grid gap-6 xl:grid-cols-[1fr_340px]">
        {/* --------------------------------------------------- main */}
        <div className="min-w-0 space-y-4">
          {step === "address" && (
            <Card className="p-6 space-y-6">
              <div>
                <label className="block text-xs text-muted mb-1">Property address · United States</label>
                <AntInput size="large" value={f.address} onChange={(e) => setAddress(e.target.value)} placeholder="513 Allison St NW, Washington, DC 20011" autoFocus
                  prefix={<MapPin size={16} className="text-faint" />}
                  suffix={checking ? <Loader2 size={18} className="animate-spin text-faint" /> : geo ? <CheckCircle2 size={18} className="text-good" /> : geoErr ? <XCircle size={18} className="text-critical" /> : <span />} />
                <div className="mt-1.5 min-h-[18px] text-[12px]">
                  {geo && <span className="text-good">Found · {geo.formatted}{geo.county ? ` · ${geo.county}` : ""}{geo.tract ? ` · tract ${geo.tract}` : ""} <span className="text-faint">({geo.provider})</span></span>}
                  {geoErr && <span className="text-critical">{geoErr}</span>}
                  {!geo && !geoErr && addr.length < 8 && <span className="text-faint">House number, street, city and state. Non-US addresses are refused.</span>}
                </div>
                {status?.properties?.length ? (
                  <div className="mt-3 flex flex-wrap gap-1.5 items-center">
                    <span className="text-[10.5px] text-faint">Portfolio:</span>
                    {status.properties.slice(0, 12).map((p) => (
                      <button key={p.property_id} type="button" onClick={() => { setAddress(p.address); set("property_id", p.property_id); }}
                        className={cn("text-[11px] px-2 h-6 rounded-full border transition", f.property_id === p.property_id ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:border-line-strong hover:text-fg")}>{p.address.split(",")[0]}</button>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="grid gap-5 md:grid-cols-2">
                <Field label="Property type">
                  <div className="grid grid-cols-2 gap-2">
                    {(Object.entries(PROPERTY_TYPE_LABEL) as [PropertyType, string][]).map(([k, l]) => (
                      <button key={k} type="button" onClick={() => set("property_type", k)} className={cn("h-10 rounded-xl border text-[12.5px] font-medium text-left px-3 transition flex items-center gap-2", f.property_type === k ? "border-accent bg-accent-soft/60 text-accent" : "border-line hover:border-line-strong")}>
                        <Building2 size={13} className={f.property_type === k ? "text-accent" : "text-faint"} /> {l}
                      </button>
                    ))}
                  </div>
                </Field>
                <div className="space-y-4">
                  <Field label="How the deal comes to you">
                    <Seg value={f.acquisition_type} onChange={(v) => set("acquisition_type", v)} options={ACQUISITION as [string, string][]} className="flex-wrap" size="sm" />
                  </Field>
                  <Field label="Label for this file" hint="Optional. Offer round, borrower name, anything that tells files apart.">
                    <Input value={f.label} onChange={(e) => set("label", e.target.value)} placeholder="e.g. Offer round 1 · Smith" />
                  </Field>
                </div>
              </div>
            </Card>
          )}

          {step === "documents" && (
            <Card className="p-6 space-y-5">
              <DropZone onFiles={(files, hint) => uploader.send(files, hint)} busy={uploader.busy} status={uploader.status} />
              {f.docs.length === 0 ? <NoDocs /> : (
                <div className="space-y-2">
                  {f.docs.map((d) => <DocCard key={d.doc_id} d={d} appliedFacts={f.fact_sources} onApplyFacts={(keys) => applyFacts(d, keys)} onRemove={() => set("docs", f.docs.filter((x) => x.doc_id !== d.doc_id))} defaultOpen={d.status === "done"} />)}
                </div>
              )}
              <FactsFound docs={f.docs} applied={f.fact_sources} onApply={applyFacts} />
            </Card>
          )}

          {step === "house" && (
            <div className="space-y-4">
              <Card className="p-6">
                <Section title="Size and rooms" sub="As the house is today. The plan step records what changes." />
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
                  <Field label="Bedrooms" source={f.fact_sources.beds}><NumField value={f.beds} onCommit={(v) => set("beds", v)} placeholder="3" /></Field>
                  <Field label="Bathrooms" source={f.fact_sources.baths}><NumField value={f.baths} onCommit={(v) => set("baths", v)} step={0.5} placeholder="1.5" /></Field>
                  <Field label="Living area" source={f.fact_sources.sqft} hint="Finished, above grade"><NumField value={f.sqft} onCommit={(v) => set("sqft", v)} suffix="sqft" placeholder="1,800" /></Field>
                  <Field label="Lot" source={f.fact_sources.lot_sqft}><NumField value={f.lot_sqft} onCommit={(v) => set("lot_sqft", v)} suffix="sqft" placeholder="3,000" /></Field>
                  <Field label="Stories" source={f.fact_sources.stories}><NumField value={f.stories} onCommit={(v) => set("stories", v)} placeholder="2" /></Field>
                  <Field label="Year built" source={f.fact_sources.year_built}><NumField value={f.year_built} onCommit={(v) => set("year_built", v)} placeholder="1925" /></Field>
                  <Field label="Garage" source={f.fact_sources.garage_spaces}><NumField value={f.garage_spaces} onCommit={(v) => set("garage_spaces", v)} suffix="cars" placeholder="0" /></Field>
                  <SelectField label="Parking" value={f.parking} onChange={(v) => set("parking", v)} options={PARKING} />
                </div>
              </Card>
              <Card className="p-6">
                <Section title="Structure and systems" sub="What an appraiser adjusts for, and what an inspector flags." />
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
                  <Field label="Basement" source={f.fact_sources.basement}><Seg value={f.basement} onChange={(v) => set("basement", v)} options={[["unknown", "?"], ["none", "None"], ["unfinished", "Unfin."], ["finished", "Finished"]]} size="sm" className="w-full" /></Field>
                  <SelectField label="Foundation" value={f.foundation} onChange={(v) => set("foundation", v)} options={FOUNDATION} />
                  <SelectField label="Exterior" value={f.exterior} onChange={(v) => set("exterior", v)} options={EXTERIOR} />
                  <Field label="Roof age" source={f.fact_sources.roof_age_years}><NumField value={f.roof_age_years} onCommit={(v) => set("roof_age_years", v)} suffix="yrs" placeholder="?" /></Field>
                  <SelectField label="Heating" value={f.heating} onChange={(v) => set("heating", v)} options={HEATING} />
                  <SelectField label="Cooling" value={f.cooling} onChange={(v) => set("cooling", v)} options={COOLING} />
                  <Field label="Pool" source={f.fact_sources.pool}><Seg value={f.pool} onChange={(v) => set("pool", v)} options={[["unknown", "?"], ["no", "No"], ["yes", "Yes"]]} size="sm" className="w-full" /></Field>
                  <Field label="Waterfront / view" source={f.fact_sources.waterfront}><Seg value={f.waterfront} onChange={(v) => set("waterfront", v)} options={[["unknown", "?"], ["no", "No"], ["yes", "Yes"]]} size="sm" className="w-full" /></Field>
                </div>
              </Card>
              <Card className="p-6">
                <Section title="Condition today" sub="UAD scale. C4 is the typical tired house a flipper buys; C5 and C6 need everything." />
                <div className="grid gap-2 md:grid-cols-3 mt-3">
                  {CONDITION_OPTIONS.map(([k, l]) => (
                    <button key={k} type="button" onClick={() => set("condition_current", k)} className={cn("rounded-xl border px-3 py-2.5 text-left transition", f.condition_current === k ? "border-accent bg-accent-soft/60" : "border-line hover:border-line-strong")}>
                      <div className="text-[13px] font-semibold">{k}</div><div className="text-[11px] text-muted leading-snug">{l.split(" · ")[1]}</div>
                    </button>
                  ))}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4">
                  <SelectField label="Occupancy" value={f.occupancy} onChange={(v) => set("occupancy", v)} options={OCCUPANCY} />
                  <Field label="HOA" source={f.fact_sources.hoa_monthly}><NumField value={f.hoa_monthly} onCommit={(v) => set("hoa_monthly", v)} prefix="$" suffix="/mo" placeholder="0" /></Field>
                  <Field label="Zoning" source={f.fact_sources.zoning}><Input value={f.zoning} onChange={(e) => set("zoning", e.target.value)} placeholder="R-2" /></Field>
                  <Field label="View / setting"><Input value={f.view} onChange={(e) => set("view", e.target.value)} placeholder="corner lot, backs to park…" /></Field>
                </div>
              </Card>
              <Card className="p-6">
                <Section title="Known issues" sub="Tick what you know or suspect. Each one changes the scope the analyst expects to see in the budget." />
                <div className="flex flex-wrap gap-2 mt-3">
                  {KNOWN_ISSUES.map(([k, l]) => <Chip key={k} tone="critical" on={f.known_issues.includes(k)} onClick={() => set("known_issues", toggle(f.known_issues, k))}>{l}</Chip>)}
                </div>
                <Field label="Anything else about the house" className="mt-4">
                  <Textarea rows={3} value={f.notes} onChange={(e) => set("notes", e.target.value)} placeholder="Roof replaced 2023; seller says the basement took water in 2021; detached garage is unpermitted; tenant leaves in June…" />
                </Field>
              </Card>
            </div>
          )}

          {step === "plan" && (
            <div className="space-y-4">
              <Card className="p-6">
                <Section title="Planned work" sub="Tick everything in scope. Adding a bed, bath or area changes the house the comps are compared with." />
                <div className="flex flex-wrap gap-2 mt-3">
                  {PLANNED_WORK.map(([k, l]) => <Chip key={k} on={f.planned_work.includes(k)} onClick={() => set("planned_work", toggle(f.planned_work, k))}>{l}</Chip>)}
                </div>
              </Card>
              <Card className="p-6">
                <Section title="Budget" sub="Three ways to set it. A contractor's bid is the strongest; a tier estimate is a placeholder the analyst will question." />
                <Seg value={f.budget_mode} onChange={(v) => setF((s) => ({ ...s, budget_mode: v, budget_touched: true, budget_doc_id: v === "doc" && !s.budget_doc_id ? (bidDocs[0]?.doc_id || "") : s.budget_doc_id }))} className="mt-3"
                  options={[["tier", "Tier estimate"], ["doc", bidDocs.length ? `Contractor bid (${bidDocs.length})` : "Contractor bid"], ["lump", "My own number"]]} />
                {f.budget_mode === "tier" && (
                  <div className="mt-4 space-y-3">
                    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                      {Object.entries(scopes).map(([k, s]) => (
                        <button key={k} type="button" onClick={() => set("scope", k)} className={cn("text-left rounded-xl border p-3 transition", f.scope === k ? "border-accent bg-accent-soft/60" : "border-line hover:border-line-strong")}>
                          <div className="text-[13px] font-semibold">{s.label}</div>
                          <div className="text-[11px] text-faint tnum">${s.low}–${s.high} / sqft</div>
                          <div className="text-[11px] text-muted mt-1 leading-snug line-clamp-3">{s.covers}</div>
                        </button>
                      ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-4">
                      <Field label="Finish level"><Seg value={f.level} onChange={(v) => set("level", v)} options={[["low", "Low"], ["mid", "Mid"], ["high", "High end"]]} size="sm" /></Field>
                      <div className="text-[12.5px]">{tierEstimate ? <>Estimate <b className="tnum">{money(tierEstimate)}</b>{stateMult ? <span className="text-faint"> · {geo?.state} cost index {stateMult.toFixed(2)}</span> : null} <span className="text-faint">before permits, overhead and contingency</span></> : <span className="text-faint">Enter the living area on the house step to see an estimate.</span>}</div>
                    </div>
                  </div>
                )}
                {f.budget_mode === "doc" && (
                  <div className="mt-4 space-y-2">
                    {bidDocs.length > 0 && !f.budget_touched && <div className="text-[12px] text-muted">The bid on file is the budget. Pick a tier estimate or your own number to use something else.</div>}
                    {bidDocs.length === 0 ? <div className="text-[12.5px] text-muted">No uploaded document has priced items yet. <button className="text-accent underline" onClick={() => setStep("documents")}>Add a bid</button> or pick another budget source.</div>
                      : bidDocs.map((d) => (
                        <button key={d.doc_id} type="button" onClick={() => set("budget_doc_id", d.doc_id)} className={cn("w-full text-left rounded-xl border p-3 transition flex items-center justify-between gap-3", f.budget_doc_id === d.doc_id ? "border-accent bg-accent-soft/60" : "border-line hover:border-line-strong")}>
                          <div className="min-w-0"><div className="text-[13px] font-semibold truncate">{d.extracted?.title || d.filename}</div><div className="text-[11px] text-muted">{d.extracted?.rehab_items.length} priced items{d.extracted?.prepared_by ? ` · ${d.extracted.prepared_by}` : ""}</div></div>
                          <div className="text-[15px] font-semibold tnum shrink-0">{money(d.extracted?.rehab_total)}</div>
                        </button>
                      ))}
                  </div>
                )}
                {f.budget_mode === "lump" && (
                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <Field label="Total repair budget" hint="Before permits, overhead and contingency; those are added on top."><NumField value={f.lump_sum} onCommit={(v) => set("lump_sum", v)} prefix="$" placeholder="120,000" /></Field>
                    <SelectField label="Closest scope (for the line split)" value={f.scope} onChange={(v) => set("scope", v)} options={Object.entries(scopes).map(([k, s]) => [k, s.label] as [string, string])} />
                  </div>
                )}
              </Card>
              <Card className="p-6">
                <Section title="The house after the work" sub="Leave a field blank if the plan does not change it." />
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
                  <Field label="Bedrooms after" hint={f.beds != null ? `today ${f.beds}` : undefined}><NumField value={f.beds_after} onCommit={(v) => set("beds_after", v)} placeholder={f.beds != null ? String(f.beds) : "same"} /></Field>
                  <Field label="Bathrooms after" hint={f.baths != null ? `today ${f.baths}` : undefined}><NumField value={f.baths_after} onCommit={(v) => set("baths_after", v)} step={0.5} placeholder={f.baths != null ? String(f.baths) : "same"} /></Field>
                  <Field label="Living area after" hint={f.sqft ? `today ${f.sqft.toLocaleString()}` : undefined}><NumField value={f.sqft_after} onCommit={(v) => set("sqft_after", v)} suffix="sqft" placeholder={f.sqft ? f.sqft.toLocaleString() : "same"} /></Field>
                  <SelectField label="Condition after" value={f.condition_after} onChange={(v) => set("condition_after", v)} options={CONDITION_OPTIONS.slice(0, 4)} />
                </div>
              </Card>
            </div>
          )}

          {step === "deal" && (
            <div className="space-y-4">
              <Card className="p-6">
                <Section title="Price and exit" sub="Leave the purchase price blank to underwrite at the maximum allowable offer." />
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
                  <Field label="Asking / list price"><NumField value={f.list_price} onCommit={(v) => set("list_price", v)} prefix="$" placeholder="optional" /></Field>
                  <Field label="Purchase price" hint="blank = use the MAO"><NumField value={f.purchase_price} onCommit={(v) => set("purchase_price", v)} prefix="$" placeholder="MAO" /></Field>
                  <Field label="Months held" hint="buy to sold"><NumField value={f.months_held} onCommit={(v) => set("months_held", v)} /></Field>
                  <Field label="Exit"><Seg value={f.exit_strategy} onChange={(v) => set("exit_strategy", v)} options={EXIT.map(([k, l]) => [k, l.split(" (")[0]] as [string, string])} size="sm" className="flex-wrap" /></Field>
                </div>
              </Card>
              <Card className="p-6">
                <Section title="Offer rules" sub="The 70% rule and a profit target; the lower of the two is the recommended maximum offer." />
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
                  <Field label="Rule" hint="70 classic · 65 slow market · 75 cheap money"><NumField value={f.rule_pct} onCommit={(v) => set("rule_pct", v)} suffix="% of ARV" /></Field>
                  <Field label="Profit target" hint="of ARV, minimum $25k"><NumField value={f.profit_target_pct} onCommit={(v) => set("profit_target_pct", v)} suffix="%" /></Field>
                  <Field label="Sale commission"><NumField value={f.commission_pct} onCommit={(v) => set("commission_pct", v)} suffix="%" /></Field>
                  <Field label="Insurance" hint="builder's risk, per year"><NumField value={f.insurance_annual} onCommit={(v) => set("insurance_annual", v)} prefix="$" placeholder="3,600" /></Field>
                  <Field label="Property taxes" source={f.fact_sources.property_taxes_annual} hint="per year; estimated from value if blank"><NumField value={f.property_taxes_annual} onCommit={(v) => set("property_taxes_annual", v)} prefix="$" placeholder="auto" /></Field>
                </div>
              </Card>
              <Card className="p-6">
                <Section title="Financing" sub="Hard money, interest only. The loan is the smaller of the loan-to-cost and loan-to-ARV caps." right={<Seg value={f.finance ? "yes" : "no"} onChange={(v) => set("finance", v === "yes")} options={[["yes", "Financed"], ["no", "All cash"]]} size="sm" />} />
                {f.finance && (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
                    <Field label="Loan to cost"><NumField value={f.ltc_pct} onCommit={(v) => set("ltc_pct", v)} suffix="%" /></Field>
                    <Field label="Loan to ARV"><NumField value={f.ltarv_pct} onCommit={(v) => set("ltarv_pct", v)} suffix="%" /></Field>
                    <Field label="Rate"><NumField value={f.rate_pct} onCommit={(v) => set("rate_pct", v)} suffix="%" step={0.25} /></Field>
                    <Field label="Points"><NumField value={f.points_pct} onCommit={(v) => set("points_pct", v)} suffix="pts" step={0.5} /></Field>
                  </div>
                )}
              </Card>
            </div>
          )}

          {step === "review" && (
            <div className="space-y-4">
              <Card className="p-6">
                <Section title="The file" sub="Everything the engine and the analyst will see. Click a block to change it." />
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 mt-3">
                  <ReviewBlock title="Address" onEdit={() => setStep("address")} rows={[["Address", geo?.formatted || f.address], ["Type", PROPERTY_TYPE_LABEL[f.property_type]], ["Source", ACQUISITION.find(([k]) => k === f.acquisition_type)?.[1] || "—"], ["County · tract", `${geo?.county || "—"} · ${geo?.tract || "—"}`]]} />
                  <ReviewBlock title={`Documents · ${f.docs.filter((d) => d.status === "done").length}`} onEdit={() => setStep("documents")} rows={f.docs.length ? f.docs.slice(0, 4).map((d) => [d.extracted?.doc_type_label || "reading…", d.extracted?.title || d.filename] as [string, string]) : [["None", "An inspection or bid would strengthen the file"]]} />
                  <ReviewBlock title="The house" onEdit={() => setStep("house")} rows={[["Rooms", `${f.beds ?? "?"} bd · ${f.baths ?? "?"} ba · ${f.sqft ? f.sqft.toLocaleString() + " sqft" : "? sqft"}`], ["Built · lot", `${f.year_built ?? "?"} · ${f.lot_sqft ? f.lot_sqft.toLocaleString() + " sqft" : "?"}`], ["Condition", f.condition_current || "not set"], ["Issues", f.known_issues.length ? f.known_issues.map((k) => KNOWN_ISSUES.find(([kk]) => kk === k)?.[1].split(" /")[0]).join(", ") : "none ticked"]]} />
                  <ReviewBlock title="The plan" onEdit={() => setStep("plan")} rows={[["Work", f.planned_work.length ? f.planned_work.map((k) => PLANNED_WORK.find(([kk]) => kk === k)?.[1]).join(", ") : "not ticked"], ["Budget", `${f.budget_mode === "doc" ? "bid" : f.budget_mode === "lump" ? "own number" : `${scopes[f.scope]?.label || f.scope} · ${f.level}`} · ${money(budgetFigure, true)}`], ["After", `${f.beds_after ?? f.beds ?? "?"} bd · ${f.baths_after ?? f.baths ?? "?"} ba · ${(f.sqft_after || f.sqft)?.toLocaleString() || "?"} sqft · ${f.condition_after}`]]} />
                  <ReviewBlock title="The deal" onEdit={() => setStep("deal")} rows={[["Price", f.purchase_price ? money(f.purchase_price) : `at the MAO${f.list_price ? ` · asking ${money(f.list_price, true)}` : ""}`], ["Exit · months", `${EXIT.find(([k]) => k === f.exit_strategy)?.[1].split(" (")[0]} · ${f.months_held}`], ["Rules", `${f.rule_pct}% · ${f.profit_target_pct}% profit`], ["Money", f.finance ? `${f.ltc_pct}% LTC / ${f.ltarv_pct}% LTARV · ${f.rate_pct}% + ${f.points_pct} pts` : "all cash"]]} />
                  <div className="rounded-xl border border-line p-3.5 bg-sunken/40">
                    <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium mb-2 flex items-center gap-1"><Search size={11} /> Comparable search</div>
                    <div className="grid grid-cols-3 gap-2">
                      <NumField label="Radius" value={f.radius_mi} onCommit={(v) => set("radius_mi", v)} suffix="mi" step={0.25} compact />
                      <NumField label="Sold within" value={f.months} onCommit={(v) => set("months", v)} suffix="mo" compact />
                      <NumField label="Searches" value={f.web_searches} onCommit={(v) => set("web_searches", v)} compact />
                    </div>
                    <div className="mt-2 flex flex-col gap-1 text-[11.5px]">
                      <Checkbox checked={f.use_web_search} onChange={(e) => set("use_web_search", e.target.checked)}>AI searches public listing pages for sales</Checkbox>
                      <Checkbox disabled={!status?.providers.rentcast} checked={f.use_rentcast && !!status?.providers.rentcast} onChange={(e) => set("use_rentcast", e.target.checked)}>RentCast records, comps and AVM {!status?.providers.rentcast && <Badge>no key</Badge>}</Checkbox>
                    </div>
                  </div>
                </div>
              </Card>
              <Card className="p-6 flex flex-wrap items-center justify-between gap-4 border-accent/30 bg-gradient-to-br from-accent-soft/50 to-transparent">
                <div>
                  <div className="text-[15px] font-semibold tracking-tight">Run the analysis</div>
                  <div className="text-xs text-muted mt-0.5 max-w-xl">Locate · public records · market context · comparable sales · score, adjust, reconcile · MAO calculator · AI review. About {f.use_web_search ? "4–7" : "1–2"} minutes.</div>
                  {readingDocs && <div className="text-xs text-high mt-1 flex items-center gap-1.5"><Loader2 size={12} className="animate-spin" /> {uploader.busy && readingCount === 0 ? "Uploading documents…" : `Reading ${readingCount} of ${f.docs.length} document${f.docs.length === 1 ? "" : "s"}…`} The run starts once every document has been read, so the AI review sees all of them.</div>}
                </div>
                <Button variant="primary" size="lg" disabled={!geo || create.isPending || readingDocs} onClick={() => create.mutate()} className="min-w-[200px]">
                  {create.isPending || readingDocs ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />} {readingDocs ? (readingCount ? `Reading ${readingCount} of ${f.docs.length}…` : "Uploading…") : "Run the analysis"}
                </Button>
              </Card>
            </div>
          )}

          {/* ---------------------------------------------- nav */}
          <div className="flex items-center justify-between">
            <Button variant="ghost" onClick={back} disabled={idx === 0}><ArrowLeft size={14} /> Back</Button>
            {step !== "review" && <Button variant="primary" onClick={next} disabled={step === "address" && !canLeaveAddress}>{step === "documents" && f.docs.length === 0 ? "Skip for now" : "Continue"} <ArrowRight size={14} /></Button>}
          </div>
        </div>

        {/* ---------------------------------------------------- rail */}
        <aside className="space-y-4 xl:sticky xl:top-6 self-start">
          <Card className="overflow-hidden">
            {geo ? <CompMap subject={{ lat: geo.lat, lon: geo.lon, address: geo.formatted }} comps={[]} height={180} className="border-b border-line" />
              : <div className="h-[180px] grid place-items-center text-[12px] text-faint bg-sunken/50 border-b border-line">The map appears when the address is found</div>}
            <div className="p-4">
              <div className="text-[13px] font-semibold truncate">{geo?.formatted || addr || "No address yet"}</div>
              <div className="text-[11px] text-muted mt-0.5">{[PROPERTY_TYPE_LABEL[f.property_type], f.beds != null ? `${f.beds} bd` : null, f.baths != null ? `${f.baths} ba` : null, f.sqft ? `${f.sqft.toLocaleString()} sqft` : null, f.year_built ? `built ${f.year_built}` : null].filter(Boolean).join(" · ")}</div>
              <div className="mt-3">
                <div className="flex items-center justify-between text-[11px] mb-1"><span className="text-muted">File completeness</span><span className={cn("font-semibold tnum", complete.score >= 0.75 ? "text-good" : complete.score >= 0.45 ? "text-high" : "text-muted")}>{Math.round(complete.score * 100)}%</span></div>
                <Progress percent={Math.round(complete.score * 100)} showInfo={false} size={["100%", 6]} strokeColor={complete.score >= 0.75 ? "var(--good)" : complete.score >= 0.45 ? "var(--high)" : "var(--accent)"} />
                {complete.missing.length > 0 && <div className="text-[11px] text-muted mt-2 leading-snug">Still useful: {complete.missing.slice(0, 4).join(", ")}{complete.missing.length > 4 ? "…" : ""}</div>}
              </div>
            </div>
          </Card>
          <Card className="p-4">
            <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium mb-2">On file so far</div>
            <ul className="space-y-1.5 text-[12px]">
              <RailRow ok={!!geo} label="Address located" sub={geo ? `${geo.provider} · ${geo.precision || "rooftop"}` : "type it above"} />
              <RailRow ok={f.docs.some((d) => d.status === "done")} label={`${f.docs.filter((d) => d.status === "done").length} document${f.docs.filter((d) => d.status === "done").length === 1 ? "" : "s"} read`} sub={Object.keys(f.fact_sources).length ? `${Object.keys(f.fact_sources).length} facts taken from them` : f.docs.length ? "reading…" : "none"} />
              <RailRow ok={f.beds != null && f.baths != null && !!f.sqft} label="Rooms and area" sub={f.sqft ? `${f.beds ?? "?"} bd · ${f.baths ?? "?"} ba · ${f.sqft.toLocaleString()} sqft` : "needed for every adjustment"} />
              <RailRow ok={!!f.condition_current} label="Condition today" sub={f.condition_current || "the comps are adjusted from here"} />
              <RailRow ok={f.planned_work.length > 0 || f.budget_mode !== "tier"} label="Plan and budget" sub={budgetFigure ? money(budgetFigure) : "tier estimate until a bid lands"} />
              <RailRow ok={!!(f.purchase_price || f.list_price)} label="Price" sub={f.purchase_price ? money(f.purchase_price) : f.list_price ? `asking ${money(f.list_price)}` : "underwriting at the MAO"} />
            </ul>
          </Card>
          <div className="text-[10.5px] text-faint leading-snug px-1">What you type always wins over records and documents. Every number stays editable in the workspace, with an instant recompute.</div>
        </aside>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- pieces */
function toggle(list: string[], k: string) { return list.includes(k) ? list.filter((x) => x !== k) : [...list, k]; }

function Section({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div><div className="text-[14px] font-semibold tracking-tight">{title}</div>{sub && <div className="text-[12px] text-muted mt-0.5">{sub}</div>}</div>
      {right}
    </div>
  );
}

function ReviewBlock({ title, rows, onEdit }: { title: string; rows: [string, string][]; onEdit: () => void }) {
  return (
    <button type="button" onClick={onEdit} className="rounded-xl border border-line p-3.5 text-left hover:border-accent/60 hover:bg-accent-soft/20 transition min-w-0">
      <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium mb-2">{title}</div>
      <dl className="space-y-1">{rows.map(([k, v], i) => <div key={i} className="grid grid-cols-[72px_1fr] gap-2 text-[12px]"><dt className="text-muted truncate">{k}</dt><dd className="truncate" title={v}>{v}</dd></div>)}</dl>
    </button>
  );
}

function RailRow({ ok, label, sub }: { ok: boolean; label: string; sub?: string }) {
  return (
    <li className="flex items-start gap-2">
      <span className={cn("mt-[3px] h-4 w-4 rounded-full grid place-items-center shrink-0", ok ? "bg-good-soft text-good" : "bg-sunken text-faint")}>{ok ? <Check size={10} /> : <span className="h-1.5 w-1.5 rounded-full bg-faint" />}</span>
      <span className="min-w-0"><div className={cn("font-medium", !ok && "text-muted")}>{label}</div>{sub && <div className="text-[11px] text-faint truncate">{sub}</div>}</span>
    </li>
  );
}

/* Facts across all documents, merged, each with where it came from and whether it is in use. */
function FactsFound({ docs, applied, onApply }: { docs: ArvDocument[]; applied: Record<string, string>; onApply: (d: ArvDocument, keys: string[]) => void }) {
  const rows: { k: string; v: any; d: ArvDocument }[] = [];
  for (const d of docs) if (d.status === "done") for (const [k, v] of Object.entries(d.extracted?.subject_facts || {})) rows.push({ k, v, d });
  if (!rows.length) return null;
  const byKey = new Map<string, { k: string; v: any; d: ArvDocument }[]>();
  rows.forEach((r) => byKey.set(r.k, [...(byKey.get(r.k) || []), r]));
  const unapplied = rows.filter((r) => applied[r.k] !== r.d.filename);
  return (
    <div className="rounded-xl border border-accent/30 bg-accent-soft/20 p-4">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="text-[12.5px] font-semibold flex items-center gap-1.5"><Sparkles size={13} className="text-accent" /> {byKey.size} fact{byKey.size > 1 ? "s" : ""} about the house found in the documents</div>
        {unapplied.length > 0 && <Button size="sm" variant="primary" onClick={() => { const seen = new Set<string>(); for (const r of [...rows].reverse()) { if (seen.has(r.k)) continue; seen.add(r.k); onApply(r.d, [r.k]); } }}>Use all</Button>}
      </div>
      <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {[...byKey.entries()].map(([k, list]) => {
          const inUse = list.find((r) => applied[k] === r.d.filename);
          const conflict = new Set(list.map((r) => JSON.stringify(r.v))).size > 1;
          return (
            <div key={k} className={cn("rounded-lg border px-2.5 py-1.5 text-[12px] bg-elev", inUse ? "border-good/40" : "border-line")}>
              <div className="flex items-center justify-between gap-2"><span className="text-muted">{FACT_LABEL[k] || k}</span>{conflict && <Badge tone="high">differs</Badge>}</div>
              {list.map((r, i) => (
                <div key={i} className="flex items-center justify-between gap-2 mt-0.5">
                  <span className="truncate"><b className="tnum">{fmtFact(k, r.v)}</b> <span className="text-faint text-[10.5px]">{r.d.filename}</span></span>
                  {applied[k] === r.d.filename ? <span className="text-[10.5px] text-good shrink-0">in use</span> : <button onClick={() => onApply(r.d, [k])} className="text-[10.5px] text-accent hover:underline shrink-0">use</button>}
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function completeness(f: Form, located: boolean): { score: number; missing: string[] } {
  const checks: [boolean, string, number][] = [
    [located, "address", 2], [f.beds != null, "bedrooms", 1], [f.baths != null, "bathrooms", 1], [!!f.sqft, "living area", 2], [!!f.year_built, "year built", 1], [!!f.lot_sqft, "lot size", 0.5],
    [!!f.condition_current, "condition today", 1.5], [f.basement !== "unknown", "basement", 0.5], [!!f.foundation || !!f.exterior, "structure", 0.5], [!!f.heating || !!f.cooling, "systems", 0.5],
    [f.planned_work.length > 0, "planned work", 1], [f.budget_mode !== "tier" || !!f.lump_sum, "a real budget (bid or your number)", 1], [!!(f.purchase_price || f.list_price), "a price", 1],
    [f.docs.some((d) => d.status === "done"), "a document", 1.5], [f.known_issues.length > 0 || f.notes.trim().length > 20, "known issues or notes", 0.5],
  ];
  const total = checks.reduce((a, c) => a + c[2], 0);
  const got = checks.filter((c) => c[0]).reduce((a, c) => a + c[2], 0);
  return { score: got / total, missing: checks.filter((c) => !c[0]).map((c) => c[1]) };
}
