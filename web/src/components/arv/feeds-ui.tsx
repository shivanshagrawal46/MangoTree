"use client";

/* What the paid feeds added on 2026-10-20, on screen:
   - ShortlistPanel: the analyst's five picks with reasons, what was set aside
     and why, the HouseCanary verification line and the data cost.
   - CompEvidence: inside an expanded comp row — the deed and MLS history
     RealEstateAPI returned, the HouseCanary grade against the county's word,
     and the analyst's one-line reason.
   - PropertyFile: on the overview — the subject's mortgages, deed history,
     MLS history, owner and flags from the public record. */
import * as React from "react";
import { Table, Tooltip } from "antd";
import { Bot, ShieldCheck, Sparkles } from "lucide-react";
import { Button, Card } from "@/components/arv/ui";
import { cn, money, fmtDate } from "@/lib/utils";
import { num, type AnalysisDoc, type Comp } from "@/lib/arv";
import { Pill } from "./bits";

const short = (s?: string | null) => (s ? fmtDate(s, "d MMM yy") : "—");

/* ------------------------------------------------------------ shortlist */
export function ShortlistPanel({ doc, onRerun, running }: { doc: AnalysisDoc; onRerun?: (stages: string[]) => void; running: boolean }) {
  const sl = doc.shortlist;
  const hc = doc.housecanary;
  const [showRejected, setShowRejected] = React.useState(false);
  if (!sl && !hc) return null;
  const graded = Object.values(hc?.comps || {}).filter((x) => x?.condition).length;
  return (
    <Card className="p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[13px] font-semibold tracking-tight flex items-center gap-2"><Bot size={14} className="text-accent" /> How the comps were chosen</div>
        <div className="flex items-center gap-2">
          {doc.data_cost_usd ? <span className="text-[11px] text-muted">data ${doc.data_cost_usd.toFixed(2)}</span> : null}
          {onRerun && <Button size="sm" variant="ghost" disabled={running} onClick={() => onRerun(["shortlist"])}><Sparkles size={12} /> Re-pick</Button>}
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-3 text-[12px]">
        <Step n={1} title="Rules" body={`${doc.valuation?.selection?.qualified ?? "—"} of ${doc.valuation?.selection?.candidates ?? doc.comps.length} nearby sales passed distance, recency, size, type and the arm's-length test.`} />
        <Step n={2} title="Analyst (Opus 5.5)" body={sl ? `Read deed and MLS history for ${sl.candidates} candidates and kept ${sl.picks.length}; set ${sl.rejected.length} aside with a reason each.` : "Not run."} />
        <Step n={3} title="HouseCanary" body={hc ? `Graded ${graded} of ${Object.keys(hc.comps || {}).length} picks C1–C6 and checked the deed price${hc.subject?.condition ? `; subject graded ${hc.subject.condition}` : ""}.` : "Not run."} />
      </div>
      {sl?.notes && <div className="text-[12.5px] leading-relaxed border-l-2 border-accent/50 pl-3">{sl.notes}</div>}
      {sl?.picks?.length ? (
        <ol className="space-y-1 text-[12px]">
          {sl.picks.map((p) => <li key={p.comp_id} className="flex gap-2"><span className="tnum font-semibold text-accent shrink-0">#{p.rank}</span><span><b>{p.address}</b> — {p.reason}</span></li>)}
        </ol>
      ) : null}
      {sl?.rejected?.length ? (
        <div>
          <button onClick={() => setShowRejected((x) => !x)} className="text-[11.5px] text-muted hover:text-fg">{showRejected ? "▾" : "▸"} {sl.rejected.length} set aside by the analyst</button>
          {showRejected && <ul className="mt-1 space-y-0.5 text-[11.5px] text-muted">{sl.rejected.map((r) => <li key={r.comp_id}>• <span className="text-fg">{r.address}</span> — {r.reason}</li>)}</ul>}
        </div>
      ) : null}
      {doc.reapi_comps?.set_aside?.length ? (
        <div className="text-[11px] text-faint">{doc.reapi_comps.set_aside.length} deed transfer{doc.reapi_comps.set_aside.length === 1 ? "" : "s"} nearby had no market price and were never candidates: {doc.reapi_comps.set_aside.slice(0, 4).map((s) => `${s.address} (${s.deed_amount ? money(s.deed_amount) : "$0"})`).join(", ")}{doc.reapi_comps.set_aside.length > 4 ? "…" : ""}.</div>
      ) : null}
      {hc?.errors?.length ? <div className="text-[11px] text-high">HouseCanary could not match: {hc.errors.join("; ")}</div> : null}
    </Card>
  );
}

function Step({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <div className="rounded-lg border border-line bg-sunken/40 p-2.5">
      <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">{n}. {title}</div>
      <div className="mt-0.5 leading-snug">{body}</div>
    </div>
  );
}

/* --------------------------------------------------------- comp pills */
export function CompFeedPills({ c }: { c: Comp }) {
  return (
    <>
      {c.ai_pick && <Pill tone="good" title={c.ai_reason || undefined}><Bot size={9} /> analyst pick{c.ai_rank ? ` #${c.ai_rank}` : ""}</Pill>}
      {c.ai_pick === false && c.ai_reason && <Pill tone="neutral" title={c.ai_reason}>set aside</Pill>}
      {c.price_verified && <Pill tone="good" title="HouseCanary's deed record shows the same price"><ShieldCheck size={9} /> deed verified</Pill>}
      {c.arms_length === false && <Pill tone="critical">not arm's length</Pill>}
      {c.evidence?.resale_within_24_months && <Pill tone="info" title={`Bought ${money(c.evidence.resale_within_24_months.bought)} on ${short(c.evidence.resale_within_24_months.bought_on)}`}>flip resale +{c.evidence.resale_within_24_months.gain_pct}%</Pill>}
    </>
  );
}

export function ConditionSource({ c }: { c: Comp }) {
  if (!c.condition) return null;
  const src = c.condition_source === "housecanary" ? "HouseCanary grade" : c.condition_source === "analyst" ? "analyst, from the deed and MLS evidence" : c.condition_source === "manual" ? "set by hand" : c.source === "web" ? "from the listing text" : null;
  const diff = c.condition_hc && c.condition_hc !== c.condition ? ` · HouseCanary says ${c.condition_hc}` : "";
  if (!src && !diff) return null;
  return <Tooltip title={`${src || ""}${diff}`}><span className={cn("ml-1 text-[9.5px] uppercase tracking-wide", diff ? "text-high" : "text-faint")}>{c.condition_source === "housecanary" ? "HC" : c.condition_source === "analyst" ? "AI" : diff ? "≠HC" : ""}</span></Tooltip>;
}

/* ----------------------------------------------------- comp evidence */
export function CompEvidence({ c }: { c: Comp }) {
  const ev = c.evidence;
  const hc = c.hc;
  if (!ev && !hc && !c.ai_reason) return null;
  return (
    <div className="space-y-2">
      {c.ai_reason && (
        <div className={cn("rounded-lg border p-2.5", c.ai_pick ? "border-good/40 bg-good/5" : "border-line bg-elev")}>
          <div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1 flex items-center gap-1"><Bot size={11} /> {c.ai_pick ? `Analyst pick${c.ai_rank ? ` #${c.ai_rank}` : ""}` : "Set aside by the analyst"}</div>
          <div>{c.ai_reason}</div>
        </div>
      )}
      {hc && (
        <div className="rounded-lg border border-line bg-elev p-2.5">
          <div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1 flex items-center gap-1"><ShieldCheck size={11} /> HouseCanary</div>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-muted">
            <span>grade <b className="text-fg">{hc.condition || "—"}</b></span>
            {hc.county_condition && <span>county says <b className="text-fg">{hc.county_condition}</b></span>}
            {hc.sqft ? <span>living area <b className="text-fg tnum">{num(hc.sqft)}</b> sqft</span> : <span>no living area on file</span>}
            {hc.beds != null && <span>{hc.beds} bd / {hc.baths ?? "—"} ba</span>}
            {hc.year_built && <span>built {hc.year_built}</span>}
            {c.hc_sale?.price ? <span>deed {money(c.hc_sale.price)} on {short(c.hc_sale.date)}</span> : null}
          </div>
        </div>
      )}
      {ev && (
        <div className="text-muted space-y-0.5">
          {ev.latest_sale && <div>Deed: {money(ev.latest_sale.price)} on {short(ev.latest_sale.date)}{ev.latest_sale.transaction_type ? ` · ${ev.latest_sale.transaction_type}` : ""}{ev.latest_sale.purchase_method ? ` · ${ev.latest_sale.purchase_method}` : ""}{ev.latest_sale.buyer ? ` · to ${ev.latest_sale.buyer}` : ""}</div>}
          {ev.prior_sale && <div>Before that: {money(ev.prior_sale.price)} on {short(ev.prior_sale.date)}{ev.prior_sale.buyer ? ` · to ${ev.prior_sale.buyer}` : ""}</div>}
          {ev.mls_closed && <div>MLS: closed {money(ev.mls_closed.price)} on {short(ev.mls_closed.date)}{ev.mls_closed.dom != null ? ` after ${ev.mls_closed.dom} days` : ""}{ev.mls_closed.agent ? ` · ${ev.mls_closed.agent}${ev.mls_closed.office ? `, ${ev.mls_closed.office}` : ""}` : ""}</div>}
          {ev.mls_listing && <div>MLS: {ev.mls_listing.status} at {money(ev.mls_listing.price)}{ev.mls_listing.dom != null ? ` · ${ev.mls_listing.dom} days on market` : ""}</div>}
          {(ev.county_condition || ev.vacant || Object.keys(ev.flags || {}).length) ? (
            <div className="flex flex-wrap gap-1 pt-0.5">
              {ev.county_condition && <Pill tone="neutral">county: {ev.county_condition}</Pill>}
              {ev.vacant && <Pill tone="high">vacant</Pill>}
              {Object.keys(ev.flags || {}).map((f) => <Pill key={f} tone={["auction", "bankOwned", "preForeclosure", "taxLien"].includes(f) ? "critical" : "neutral"}>{f.replace(/([A-Z])/g, " $1").toLowerCase()}</Pill>)}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------ property file */
export function PropertyFile({ doc }: { doc: AnalysisDoc }) {
  const p = doc.profile;
  const hc = doc.housecanary?.subject;
  if (!p && !hc) return null;
  const mortgages = p?.mortgages || [];
  const flags = Object.keys(p?.flags || {});
  return (
    <div className="space-y-3 text-[12.5px]">
      <div className="grid gap-x-6 gap-y-1 md:grid-cols-2">
        {hc && (
          <Row k="Condition" v={<span>HouseCanary <b>{hc.condition || "—"}</b>{hc.county_condition ? <span className="text-muted"> · county says {hc.county_condition}</span> : null}{p?.facts?.county_condition && !hc.county_condition ? <span className="text-muted"> · county says {p.facts.county_condition}</span> : null}</span>} />
        )}
        {p?.owner?.name && <Row k="Owner" v={<span>{p.owner.name}{p.owner.absentee ? <span className="text-muted"> · absentee</span> : null}{p.owner.mail_address && p.owner.mail_address !== p.address ? <span className="text-muted"> · mail {p.owner.mail_address}</span> : null}</span>} />}
        {p?.last_sale?.price != null && <Row k="Last market sale" v={`${money(p.last_sale.price)} · ${short(p.last_sale.date)}${p.last_sale.arms_length === false ? " · not arm's length" : ""}`} />}
        {p?.mls?.status && <Row k="MLS" v={`${p.mls.status}${p.mls.sold_price ? ` · sold ${money(p.mls.sold_price)} ${short(p.mls.sold_date)}` : p.mls.listing_price ? ` · listed ${money(p.mls.listing_price)}` : ""}${p.mls.dom != null ? ` · ${p.mls.dom} DOM` : ""}`} />}
        {p?.tax?.annual != null && <Row k="Tax · assessed" v={`${money(p.tax.annual)}/yr · ${money(p.tax.assessed_value)}${p.tax.market_value ? ` (market ${money(p.tax.market_value)})` : ""}`} />}
        {p?.estimated_value != null && <Row k="Record AVM · equity" v={`${money(p.estimated_value)} · ${p.equity_pct != null ? `${num(p.equity_pct)}%` : "—"}`} />}
        {p?.apn && <Row k="Parcel" v={`${p.apn}${p.legal_description ? ` · ${p.legal_description}` : ""}`} />}
      </div>
      {flags.length > 0 && <div className="flex flex-wrap gap-1">{flags.map((f) => <Pill key={f} tone={["preForeclosure", "auction", "bankOwned", "taxLien", "lien", "judgment"].includes(f) ? "critical" : f === "vacant" ? "high" : "neutral"}>{f.replace(/([A-Z])/g, " $1").toLowerCase()}</Pill>)}</div>}
      {mortgages.length > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1">Open mortgages · {money(p?.open_mortgage_balance)}</div>
          <Table size="small" pagination={false} rowKey={(r: any, i) => String(i)} dataSource={mortgages}
            columns={[{ title: "Lender", dataIndex: "lender", render: (t: string) => <span className="font-medium">{t || "—"}</span> },
              { title: "Amount", dataIndex: "amount", align: "right", width: 110, render: (v: number) => <span className="tnum">{money(v)}</span> },
              { title: "Type", dataIndex: "loan_type", render: (t: string, r: any) => <span className="text-muted">{[t, r.position, r.term].filter(Boolean).join(" · ")}</span> },
              { title: "Recorded", dataIndex: "recorded", width: 100, render: (t: string) => <span className="tnum">{short(t)}</span> },
              { title: "Matures", dataIndex: "maturity", width: 100, render: (t: string) => <span className="tnum">{short(t)}</span> }]} />
        </div>
      )}
      {(p?.sale_history?.length || 0) > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1">Deed history</div>
          <ul className="space-y-0.5 text-[12px]">
            {p!.sale_history!.slice(0, 6).map((s, i) => (
              <li key={i} className="flex flex-wrap gap-x-2 text-muted"><span className="tnum text-fg w-[76px] shrink-0">{short(s.date)}</span><span className="tnum text-fg w-[84px] shrink-0 text-right">{s.price ? money(s.price) : "$0"}</span><span>{[s.transaction_type, s.document_type, s.purchase_method].filter(Boolean).join(" · ")}{s.buyer ? ` · to ${s.buyer}` : ""}</span></li>
            ))}
          </ul>
        </div>
      )}
      {(p?.mls?.history?.length || 0) > 0 && (
        <div>
          <div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1">MLS history</div>
          <ul className="space-y-0.5 text-[12px]">
            {p!.mls!.history!.map((m, i) => <li key={i} className="flex flex-wrap gap-x-2 text-muted"><span className="tnum text-fg w-[76px] shrink-0">{short(m.date)}</span><span className="text-fg w-[84px] shrink-0">{m.status}</span><span>{money(m.price)}{m.dom != null ? ` · ${m.dom} DOM` : ""}{m.agent ? ` · ${m.agent}${m.office ? `, ${m.office}` : ""}` : ""}</span></li>)}
          </ul>
        </div>
      )}
      <div className="text-[10.5px] text-muted">RealEstateAPI public record{p?.fetched_at ? `, ${fmtDate(p.fetched_at)}` : ""}{hc ? " · HouseCanary condition grade" : ""}. Deed amounts are what the county recorded; MLS figures are from the listing feed.</div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="flex gap-3 min-w-0"><span className="text-muted whitespace-nowrap w-[120px] shrink-0">{k}</span><span className="min-w-0">{v}</span></div>;
}
