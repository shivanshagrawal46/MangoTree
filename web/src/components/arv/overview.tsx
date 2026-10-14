"use client";

/* The overview of one file, read in a minute: a verdict strip, the analyst's
   points, what moves the number, how the value was reached, the deal and the
   open questions. Everything secondary (the sales, the subject's full fact
   list, documents, market, search notes) sits closed under one-line headers
   with a preview, so the page shows the decision first and the detail on
   demand. No prose paragraphs. */
import * as React from "react";
import { AlertTriangle, Bot, ChevronRight, HelpCircle, Loader2 } from "lucide-react";
import { Badge, Card } from "@/components/arv/ui";
import { cn, money, fmtDate } from "@/lib/utils";
import { PROPERTY_TYPE_LABEL, VERDICT_LABEL, VERDICT_TONE, fmtFact, num, type AnalysisDoc, type PropertyType } from "@/lib/arv";
import { CheckLine, Compare, Disclosure, MorePill, RangeBar, Tile } from "./bits";
import { TYPE_TONE } from "./documents-drop";

const CONF_TONE: Record<string, "good" | "accent" | "high" | "critical"> = { high: "good", good: "accent", moderate: "high", low: "critical", very_low: "critical" };
const VERDICT_EDGE: Record<string, string> = { fund: "border-l-good", fund_with_conditions: "border-l-high", renegotiate: "border-l-high", pass: "border-l-critical" };

export function Overview({ doc, setTab }: { doc: AnalysisDoc; setTab: (t: string) => void }) {
  const v = doc.valuation;
  const an = doc.analysis;
  const rec = v?.reconciliation;
  const uw = doc.underwriting;
  const running = doc.status === "running" || doc.status === "queued";
  const used = (doc.comps || []).filter((c) => c.selected).sort((a, b) => (b.weight || 0) - (a.weight || 0));
  const docs = doc.documents || [];
  const points = an?.summary_points?.length ? an.summary_points : an?.summary ? sentences(an.summary).slice(0, 6) : [];
  const drivers = (an?.value_drivers || []).slice().sort((a, b) => Math.abs(b.impact_usd) - Math.abs(a.impact_usd));
  const engineArv = v?.arv;
  const diff = an && engineArv ? ((an.arv_opinion - engineArv) / engineArv) * 100 : null;
  const failedChecks = (uw?.checks || []).filter((c) => !c.ok);
  const s = doc.subject;

  return (
    <div className="space-y-5">
      {/* ------------------------------------------------------- verdict */}
      {an ? (
        <Card className="overflow-hidden">
          <div className="flex flex-col lg:flex-row">
            <div className={cn("flex-1 min-w-0 p-5 border-l-4", VERDICT_EDGE[an.verdict || ""] || "border-l-accent")}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="h-7 w-7 rounded-lg bg-accent-soft text-accent grid place-items-center"><Bot size={14} /></span>
                {an.verdict && <Badge tone={VERDICT_TONE[an.verdict] || "neutral"} className="h-6 px-2.5 text-[12px]">{VERDICT_LABEL[an.verdict]}</Badge>}
                <Badge tone={CONF_TONE[an.confidence] || "neutral"}>{an.confidence.replace("_", " ")} confidence</Badge>
                {diff !== null && <Badge tone={Math.abs(diff) > 5 ? "high" : "good"}>analyst {diff > 0 ? "+" : ""}{diff.toFixed(1)}% vs engine</Badge>}
              </div>
              <div className="text-[17px] font-semibold tracking-tight leading-snug mt-3">{an.headline}</div>
              {an.verdict_reason && <div className="text-[12.5px] text-muted mt-2 leading-relaxed">{an.verdict_reason}</div>}
            </div>
            <div className="lg:w-[400px] shrink-0 border-t lg:border-t-0 lg:border-l border-line bg-sunken/30 p-5">
              <div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-3">Analyst against the engine</div>
              <Compare rows={[
                { label: "Analyst", value: an.arv_opinion, low: an.arv_low, high: an.arv_high, tone: "bg-high" },
                { label: "Engine", value: engineArv, low: v?.arv_low, high: v?.arv_high, tone: "bg-accent" },
                ...(doc.avm?.value ? [{ label: doc.avm.source?.split(" ")[0] || "AVM", value: doc.avm.value, low: doc.avm.low, high: doc.avm.high, tone: "bg-info" }] : []),
              ]} />
              <div className="mt-3 pt-3 border-t border-line grid grid-cols-2 gap-3">
                <div><div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">Analyst max offer</div><div className="text-[17px] font-semibold tnum tracking-tight">{money(an.mao_opinion, true)}</div></div>
                <div><div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">Engine max offer</div><div className="text-[17px] font-semibold tnum tracking-tight">{money(uw?.mao.recommended, true)}</div></div>
              </div>
            </div>
          </div>
        </Card>
      ) : running ? (
        <Card className="p-5 text-sm text-muted flex items-center gap-2"><Loader2 size={14} className="animate-spin text-accent" /> The analyst&apos;s verdict lands here when the AI finishes reading the file.</Card>
      ) : doc.analysis_error ? (
        <Card className="p-5 text-sm text-critical flex items-center gap-2"><AlertTriangle size={14} /> The review failed: {doc.analysis_error}</Card>
      ) : null}

      {/* ------------------------------------------------------- points + drivers */}
      {(points.length > 0 || drivers.length > 0) && (
        <div className={cn("grid gap-5", points.length > 0 && drivers.length > 0 && "xl:grid-cols-[1.25fr_1fr]")}>
          {points.length > 0 && (
            <Card className="p-5">
              <Head title="What the analyst found" right={<button onClick={() => setTab("analyst")} className="text-[11.5px] text-accent hover:underline flex items-center gap-1">full review <ChevronRight size={11} /></button>} />
              <MoreList items={points} limit={5} render={(p, i) => (
                <li key={i} className="flex items-start gap-3 text-[13px] leading-snug">
                  <span className="mt-[2px] h-5 w-5 rounded-full bg-sunken text-muted grid place-items-center text-[10.5px] font-semibold shrink-0">{i + 1}</span>
                  <span>{emphasise(p)}</span>
                </li>
              )} />
            </Card>
          )}
          {drivers.length > 0 && (
            <Card className="p-5">
              <Head title="What moves the number" right={<span className="text-[11px] text-muted">effect on the ARV</span>} />
              <Drivers drivers={drivers} limit={4} />
            </Card>
          )}
        </div>
      )}

      {/* ------------------------------------------------------- how the value */}
      <Card className="p-5">
        <Head title="How the value was reached" right={<button onClick={() => setTab("comps")} className="text-[11.5px] text-accent hover:underline flex items-center gap-1">comps grid and map <ChevronRight size={11} /></button>} />
        {v?.arv && rec ? (
          <>
            <div className="flex items-stretch gap-1.5 overflow-x-auto pb-1">
              <Step label="Candidates" value={num(v.selection.candidates)} sub={`${v.selection.radius_used} mi · ${v.selection.months_used} mo`} />
              <Arrow /><Step label="Qualified" value={num(v.selection.qualified)} sub="passed the filters" />
              <Arrow /><Step label="Used" value={num(rec.comps_used)} sub="weighted" />
              <Arrow /><Step label="Weighted mean" value={money(rec.weighted_mean, true)} sub={`median ${money(rec.weighted_median, true)}`} />
              <Arrow /><Step label="Sales comparison" value={money(rec.sales_comparison, true)} sub={`${money(rec.ppsf_used, true)}/sqft × ${num(s.sqft_after || s.sqft)}`} />
              {rec.avm && rec.avm.value ? <><Arrow /><Step label={rec.avm.source.split(" ")[0] + " AVM"} value={money(rec.avm.value, true)} sub="blended 20%" /></> : null}
              <Arrow /><Step label="ARV" value={money(v.arv, true)} sub={`${money(v.arv_low, true)} – ${money(v.arv_high, true)}`} strong />
            </div>
            <RangeBar low={v.arv_low} high={v.arv_high} value={v.arv} compLow={rec.comp_low} compHigh={rec.comp_high}
              marks={[{ label: "analyst", value: an?.arv_opinion, tone: "text-high" }, { label: doc.avm?.source?.split(" ")[0] || "AVM", value: doc.avm?.value, tone: "text-info" }]} className="mt-2" />
            <ul className="grid gap-1.5 sm:grid-cols-2 mt-3">
              <CheckLine ok={!!rec.bracketing?.size}>Bracketed on size</CheckLine>
              <CheckLine ok={!!rec.bracketing?.value}>Bracketed on adjusted value</CheckLine>
              <CheckLine ok={rec.cv_pct != null ? rec.cv_pct <= 10 : null} detail={rec.cv_pct != null ? `${rec.cv_pct}% spread` : undefined}>Comps agree with each other</CheckLine>
              <CheckLine ok={(rec.comps_used || 0) >= 3} detail={`${rec.comps_used} used`}>At least three closed sales</CheckLine>
            </ul>
            {v.selection.notes.length > 0 && <div className="mt-2 text-[12px] text-high flex items-start gap-1.5"><AlertTriangle size={13} className="mt-0.5 shrink-0" /> {v.selection.notes.join(" ")}</div>}
            <div className="grid gap-2 md:grid-cols-2 mt-4">
              <Disclosure title={`Why grade ${v.confidence.grade} · ${v.confidence.score}/100`} preview={v.confidence.reasons[0]}>
                <ul className="space-y-1">{v.confidence.reasons.map((r, i) => <CheckLine key={i} ok={/not|only|wide|few|outside|missing|listing|estimated|web/i.test(r) ? false : true}>{r}</CheckLine>)}</ul>
                <div className="text-[11px] text-muted mt-2">Time adjustment {v.rates.market_monthly_pct > 0 ? "+" : ""}{v.rates.market_monthly_pct}% a month ({v.rates.market_basis}). Living area ${num(v.rates.gla_per_sqft)}/sqft ({v.rates.gla_basis}).</div>
              </Disclosure>
              <Disclosure title={`The ${used.length} sales used`} preview={used[0] ? `top weight ${used[0].address?.split(",")[0]} · ${used[0].weight}%` : undefined}>
                <ul className="space-y-2">
                  {used.map((c) => (
                    <li key={c.comp_id} className="text-[12.5px]">
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate flex items-center gap-1.5">{c.address?.split(",")[0]}{c.price_basis !== "closed" && <Badge tone="high">{c.price_basis}</Badge>}<span className="text-muted text-[11px]">{c.distance_mi != null ? `${c.distance_mi} mi` : ""}{c.sqft ? ` · ${num(c.sqft)} sqft` : ""}{c.condition ? ` · ${c.condition}` : ""}</span></span>
                        <span className="tnum text-muted shrink-0">{money(c.sale_price, true)} → <b className="text-fg">{money(c.adjustment?.adjusted_price, true)}</b> · {c.weight}%</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-sunken mt-1 overflow-hidden"><div className={cn("h-full rounded-full", c.source === "web" ? "bg-high" : c.source === "document" ? "bg-accent" : "bg-money-in")} style={{ width: `${c.weight || 0}%` }} /></div>
                    </li>
                  ))}
                </ul>
              </Disclosure>
            </div>
          </>
        ) : <div className="text-sm text-muted">{running ? "Waiting for comparable sales…" : rec?.error || "No value yet."}</div>}
      </Card>

      {/* ------------------------------------------------------- deal + questions */}
      <div className="grid gap-5 xl:grid-cols-2">
        {uw && (
          <Card className="p-5">
            <Head title={`Deal at ${money(uw.deal.purchase_price, true)}`} right={<button onClick={() => setTab("calculator")} className="text-[11.5px] text-accent hover:underline flex items-center gap-1">calculator <ChevronRight size={11} /></button>} />
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <Tile label="Profit" value={money(uw.deal.profit, true)} sub={`${uw.deal.profit_pct_of_arv ?? "—"}% of ARV`} tone={(uw.deal.profit ?? 0) < 0 ? "text-critical" : "text-good"} />
              <Tile label="Annualized ROI" value={uw.deal.annualized_roi_pct != null ? `${uw.deal.annualized_roi_pct}%` : "—"} sub={`cash in ${money(uw.deal.cash_needed, true)}`} />
              <Tile label="Rehab" value={money(uw.rehab_total, true)} sub={doc.rehab_budget?.tier.label} />
              <Tile label="Max offer" value={money(uw.mao.recommended, true)} sub={`${num(uw.mao.rule_pct, 0)}% rule ${money(uw.mao.rule, true)}`} />
            </div>
            {failedChecks.length > 0 ? (
              <ul className="mt-3 space-y-1">{failedChecks.map((c) => <CheckLine key={c.key} ok={false} detail={c.note}>{c.label}</CheckLine>)}</ul>
            ) : <div className="mt-3 text-[12px] text-good flex items-center gap-1.5">✓ All {uw.checks.length} lender checks pass at this price.</div>}
          </Card>
        )}
        {an?.questions?.length ? (
          <Card className="p-5 border-high/40">
            <Head title={<span className="flex items-center gap-1.5"><HelpCircle size={13} className="text-high" /> The analyst wants to know</span>} right={<button onClick={() => setTab("analyst")} className="text-[11.5px] text-accent hover:underline flex items-center gap-1">answer and re-run <ChevronRight size={11} /></button>} />
            <ul className="space-y-2 text-[12.5px]">{an.questions.slice(0, 3).map((q, i) => <li key={i} className="flex gap-2"><Badge tone={q.impact === "high" ? "critical" : q.impact === "medium" ? "high" : "neutral"} className="shrink-0 mt-0.5">{q.impact}</Badge><span>{q.question}</span></li>)}</ul>
            {an.questions.length > 3 && <div className="text-[11px] text-muted mt-2">{an.questions.length - 3} more on the review tab.</div>}
          </Card>
        ) : null}
      </div>

      {/* ------------------------------------------------------- the file, folded */}
      <div className="space-y-2">
        <Disclosure title={`Documents · ${docs.length}`} badge={docs.some((d) => d.status === "reading") ? <Loader2 size={12} className="animate-spin text-accent" /> : undefined}
          preview={docs.length ? docs.map((d) => d.extracted?.doc_type_label?.split(" ")[0] || d.filename).join(", ") : "none on file"}>
          {docs.length === 0 ? <div className="text-[12.5px] text-muted">None on file. An inspection report or a contractor&apos;s bid changes the number more than anything typed. <button onClick={() => setTab("documents")} className="text-accent hover:underline">Add one</button>.</div> : (
            <ul className="space-y-1.5">
              {docs.map((d) => {
                const ex = d.extracted;
                const crit = (ex?.findings || []).filter((f) => f.severity === "critical").length;
                return (
                  <li key={d.doc_id} className="flex items-center justify-between gap-2 text-[12.5px]">
                    <span className="truncate">{ex?.title || d.filename}</span>
                    <span className="flex items-center gap-1 shrink-0">
                      {ex?.doc_type && <Badge tone={TYPE_TONE[ex.doc_type] || "neutral"}>{ex.doc_type_label?.split(" ")[0] || ex.doc_type}</Badge>}
                      {crit > 0 && <Badge tone="critical">{crit} critical</Badge>}
                      {ex?.rehab_total ? <Badge tone="good">{money(ex.rehab_total, true)}</Badge> : null}
                      {d.status === "failed" && <Badge tone="critical">failed</Badge>}
                    </span>
                  </li>
                );
              })}
              <li><button onClick={() => setTab("documents")} className="text-[11.5px] text-accent hover:underline flex items-center gap-1">open the documents tab <ChevronRight size={11} /></button></li>
            </ul>
          )}
        </Disclosure>

        <Disclosure title="Subject" preview={[PROPERTY_TYPE_LABEL[s.property_type as PropertyType], s.beds != null ? `${s.beds} bd` : null, s.baths != null ? `${s.baths} ba` : null, s.sqft ? `${num(s.sqft)} sqft` : null, s.year_built ? `built ${s.year_built}` : null, `${s.condition_current || "?"} → ${s.condition_after}`].filter(Boolean).join(" · ")}>
          <dl className="grid grid-cols-[auto_1fr] md:grid-cols-[auto_1fr_auto_1fr] gap-x-4 gap-y-1 text-[12.5px]">
            <KV k="Address" v={doc.geocode?.formatted || s.address} />
            <KV k="County · tract" v={`${doc.geocode?.county || "—"} · ${doc.geocode?.tract || "—"}`} />
            <KV k="Type" v={PROPERTY_TYPE_LABEL[s.property_type as PropertyType]} src={s.fact_sources?.property_type} />
            <KV k="Rooms" v={`${s.beds ?? "—"} bd · ${s.baths ?? "—"} ba${s.beds_after || s.baths_after ? ` → ${s.beds_after ?? s.beds} bd · ${s.baths_after ?? s.baths} ba after` : ""}`} src={s.fact_sources?.beds || s.fact_sources?.baths} />
            <KV k="Area · lot" v={`${num(s.sqft)}${s.sqft_after ? ` → ${num(s.sqft_after)}` : ""} · ${num(s.lot_sqft)} sqft`} src={s.fact_sources?.sqft} />
            <KV k="Built · stories" v={`${s.year_built ?? "—"} · ${s.stories ?? "—"}`} src={s.fact_sources?.year_built} />
            <KV k="Garage · parking" v={`${s.garage_spaces ?? "—"} · ${(s.parking || "—").replace(/_/g, " ")}`} />
            <KV k="Basement · foundation" v={`${s.basement} · ${s.foundation || "—"}`} src={s.fact_sources?.basement} />
            <KV k="Exterior · roof" v={`${s.exterior || "—"} · ${s.roof_age_years != null ? `${s.roof_age_years} yrs` : "—"}`} src={s.fact_sources?.roof_age_years} />
            <KV k="Heat · cool" v={`${(s.heating || "—").replace(/_/g, " ")} · ${(s.cooling || "—").replace(/_/g, " ")}`} />
            <KV k="Pool · waterfront" v={`${fmtFact("pool", s.pool)} · ${fmtFact("waterfront", s.waterfront)}`} />
            <KV k="Condition" v={`${s.condition_current || "?"} → ${s.condition_after}`} src={s.fact_sources?.condition_current} />
            <KV k="Occupancy · HOA" v={`${s.occupancy || "—"} · ${s.hoa_monthly ? `$${num(s.hoa_monthly)}/mo` : "—"}`} />
            {doc.records?.last_sale_price && <KV k="Last sale" v={`${money(doc.records.last_sale_price)} · ${fmtDate(doc.records.last_sale_date)}`} />}
          </dl>
          {(s.known_issues?.length || 0) > 0 && <div className="mt-3 flex flex-wrap gap-1">{s.known_issues!.map((k) => <Badge key={k} tone="critical">{k.replace(/_/g, " ")}</Badge>)}</div>}
          {(s.planned_work?.length || 0) > 0 && <div className="mt-1.5 flex flex-wrap gap-1">{s.planned_work!.map((k) => <Badge key={k} tone="accent">{k.replace(/_/g, " ")}</Badge>)}</div>}
          {s.notes && <div className="mt-3 text-[12px] text-muted border-t border-line pt-2 whitespace-pre-wrap">{s.notes}</div>}
          <div className="mt-3 text-[10.5px] text-muted">Sources: {(doc.data_sources || []).join(", ") || "—"}{Object.keys(s.fact_sources || {}).length ? ` · ${Object.keys(s.fact_sources!).length} facts from documents (green dot)` : ""}</div>
        </Disclosure>

        {doc.market && (
          <Disclosure title="Market" preview={[doc.market.zhvi ? `ZIP ${money(doc.market.zhvi.value, true)} (${(doc.market.zhvi.change_12m_pct ?? 0) > 0 ? "+" : ""}${doc.market.zhvi.change_12m_pct}%/yr)` : null, doc.market.mortgage ? `${doc.market.mortgage.rate_pct}% rate` : null, doc.market.flood ? `flood ${doc.market.flood.zone}` : null].filter(Boolean).join(" · ")}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <Tile label="ZIP typical value" value={doc.market.zhvi ? money(doc.market.zhvi.value, true) : "—"} sub={doc.market.zhvi ? `${(doc.market.zhvi.change_12m_pct ?? 0) > 0 ? "+" : ""}${doc.market.zhvi.change_12m_pct}% / yr` : undefined} />
              <Tile label="30-yr rate" value={doc.market.mortgage ? `${doc.market.mortgage.rate_pct}%` : "—"} sub={doc.market.mortgage?.year_ago_pct != null ? `${doc.market.mortgage.year_ago_pct}% a year ago` : undefined} />
              <Tile label="Flood zone" value={doc.market.flood?.zone || "—"} sub={doc.market.flood ? (doc.market.flood.sfha ? "special flood hazard area" : "outside the SFHA") : undefined} tone={doc.market.flood?.sfha ? "text-critical" : undefined} />
              <Tile label="Tract median value" value={money(doc.market.acs?.median_home_value, true)} sub={doc.market.acs?.median_year_built ? `median built ${doc.market.acs.median_year_built}` : undefined} />
            </div>
            <button onClick={() => setTab("market")} className="mt-2 text-[11.5px] text-accent hover:underline flex items-center gap-1">charts <ChevronRight size={11} /></button>
          </Disclosure>
        )}

        {doc.web_search?.notes && (
          <Disclosure title="What the web search found" badge={<span className="text-[11px] text-muted">{doc.web_search.searches} searches · {doc.web_search.found} sales</span>} preview={sentences(doc.web_search.notes)[0]}>
            <ul className="space-y-1 text-[12.5px]">{sentences(doc.web_search.notes).map((l, i) => <li key={i} className="flex gap-2"><span className="text-muted">•</span><span>{l}</span></li>)}</ul>
            <div className="text-[10.5px] text-muted mt-2">Public listing pages, unverified against MLS or county records.</div>
          </Disclosure>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- bits */
function Head({ title, right }: { title: React.ReactNode; right?: React.ReactNode }) {
  return <div className="flex items-center justify-between gap-3 mb-3"><div className="text-[13px] font-semibold tracking-tight">{title}</div>{right}</div>;
}

export function MoreList<T>({ items, limit, render, className }: { items: T[]; limit: number; render: (item: T, i: number) => React.ReactNode; className?: string }) {
  const [all, setAll] = React.useState(false);
  const shown = all ? items : items.slice(0, limit);
  return (
    <>
      <ol className={cn("space-y-2", className)}>{shown.map(render)}</ol>
      {items.length > limit && <MorePill n={items.length - limit} open={all} onClick={() => setAll((a) => !a)} />}
    </>
  );
}

export function Drivers({ drivers, limit }: { drivers: { factor: string; impact_usd: number; why: string }[]; limit: number }) {
  const maxDrv = Math.max(1, ...drivers.map((d) => Math.abs(d.impact_usd)));
  return (
    <MoreList items={drivers} limit={limit} render={(d, i) => (
      <li key={i} className="text-[12.5px]" title={d.why}>
        <div className="flex items-center justify-between gap-3"><span className="truncate">{d.factor}</span><span className={cn("tnum font-semibold shrink-0", d.impact_usd < 0 ? "text-critical" : "text-good")}>{d.impact_usd < 0 ? "−" : "+"}{money(Math.abs(d.impact_usd), true)}</span></div>
        <div className="relative h-2 mt-1 rounded-full bg-sunken overflow-hidden">
          <div className="absolute inset-y-0 left-1/2 w-px bg-line-strong" />
          <div className={cn("absolute inset-y-0 rounded-full", d.impact_usd < 0 ? "bg-critical/70" : "bg-good/70")}
            style={d.impact_usd < 0 ? { right: "50%", width: `${(Math.abs(d.impact_usd) / maxDrv) * 50}%` } : { left: "50%", width: `${(Math.abs(d.impact_usd) / maxDrv) * 50}%` }} />
        </div>
      </li>
    )} />
  );
}

function Step({ label, value, sub, strong }: { label: string; value: React.ReactNode; sub?: string; strong?: boolean }) {
  return (
    <div className={cn("rounded-lg border px-2.5 py-2 min-w-[108px] shrink-0", strong ? "border-accent bg-accent-soft/50" : "border-line bg-sunken/40")}>
      <div className="text-[9.5px] uppercase tracking-wide text-muted font-medium whitespace-nowrap">{label}</div>
      <div className={cn("text-[14px] font-semibold tnum tracking-tight whitespace-nowrap", strong && "text-accent")}>{value}</div>
      {sub && <div className="text-[10px] text-muted whitespace-nowrap">{sub}</div>}
    </div>
  );
}
function Arrow() { return <div className="self-center text-faint shrink-0"><ChevronRight size={12} /></div>; }

function KV({ k, v, src }: { k: string; v: React.ReactNode; src?: string | null }) {
  return <><dt className="text-muted whitespace-nowrap">{k}</dt><dd className="min-w-0 truncate flex items-center gap-1.5" title={typeof v === "string" ? v : undefined}>{v}{src && <span className="h-1.5 w-1.5 rounded-full bg-good shrink-0" title={`From ${src}`} />}</dd></>;
}

/* Split prose into sentence bullets. */
export function sentences(text: string): string[] {
  const plain = (text || "").replace(/```[\s\S]*?```/g, " ").replace(/^#{1,6}\s+/gm, "").replace(/\*\*|__|`/g, "").replace(/^\s*[-*•]\s+/gm, "").replace(/\s+/g, " ").trim();
  const parts = plain.split(/(?<=[.!?])\s+(?=[A-Z0-9$(])/).map((s) => s.trim()).filter((s) => s.length > 2);
  const out: string[] = [];
  for (const p of parts) {
    // a fragment such as "The engine says $852K." joins the sentence before it
    if (out.length && (p.length < 40 || /^(It|Its|This|That|These|Those|They|Both|So|But|And)\b/.test(p)) && out[out.length - 1].length < 220) out[out.length - 1] += " " + p;
    else out.push(p);
  }
  return out;
}

/* Bold the dollar figures, percentages and C-grades in a sentence. */
export function emphasise(text: string): React.ReactNode {
  const parts = text.split(/(\$[\d,.]+[kKmM]?|\b\d+(?:\.\d+)?%|\bC[1-6]\b)/g);
  return parts.map((p, i) => (/^(\$[\d,.]+[kKmM]?|\d+(?:\.\d+)?%|C[1-6])$/.test(p) ? <b key={i} className="tnum">{p}</b> : <React.Fragment key={i}>{p}</React.Fragment>));
}
