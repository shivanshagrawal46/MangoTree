"use client";

/**
 * Sold deals — the Deal Intelligence dashboard.
 *
 * Money is RKB's summary workbook, row for row, with the sheet's own Total rows
 * shown as written; the derived lines (cash profit, annualised) are labelled as
 * derived. Verdicts and one-line reasons come from the cross-deal ranking; the
 * long text (post-mortem sections, synthesis sections) is collapsed by default.
 */
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Archive, ArrowLeft, Info } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, CardHeader, Empty, Skeleton } from "@/components/ui";
import { cn, fmtDate } from "@/lib/utils";
import type { SoldDeal, SoldPortfolio, SoldRow, SoldSection } from "@/lib/types";

const usd = (n?: number | null, cents = false) => (n === null || n === undefined || isNaN(n)) ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: cents ? 2 : 0, minimumFractionDigits: cents ? 2 : 0 });
const pct = (n?: number | null, d = 1) => (n === null || n === undefined || isNaN(n)) ? "—" : `${(n * 100).toFixed(d)}%`;
const VERDICT_TONE: Record<string, "good" | "high" | "critical" | "neutral"> = { good: "good", mixed: "high", bad: "critical" };
const VERDICT_LABEL: Record<string, string> = { good: "Good", mixed: "Mixed", bad: "Bad" };

function Verdict({ v }: { v?: string | null }) {
  if (!v) return <Badge tone="neutral">not ranked</Badge>;
  return <Badge tone={VERDICT_TONE[v] || "neutral"}>{VERDICT_LABEL[v] || v}</Badge>;
}

function Num({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "critical" | "good" | "muted" }) {
  return (
    <div className="rounded-2xl border border-line bg-elev px-4 py-3 min-w-0">
      <div className="text-[10.5px] uppercase tracking-wide text-faint">{label}</div>
      <div className={cn("text-[22px] font-semibold tracking-tight tnum mt-0.5 leading-tight", tone === "critical" && "text-critical", tone === "good" && "text-good", tone === "muted" && "text-muted")}>{value}</div>
      {sub && <div className="text-[11.5px] text-muted mt-1">{sub}</div>}
    </div>
  );
}

function Section({ s, open: openDefault = false }: { s: SoldSection; open?: boolean }) {
  const [open, setOpen] = React.useState(openDefault);
  return (
    <div className="rounded-xl border border-line bg-elev">
      <button onClick={() => setOpen((v) => !v)} className="w-full text-left px-4 py-3 flex items-center gap-2 hover:bg-sunken/50 rounded-xl">
        {open ? <ChevronDown size={14} className="text-faint shrink-0" /> : <ChevronRight size={14} className="text-faint shrink-0" />}
        <span className="text-[11px] font-bold text-accent w-5">{s.n}</span>
        <span className="text-[13.5px] font-semibold">{s.title}</span>
      </button>
      {open && <div className="px-5 pb-4 prose-mt text-[13px] sold-md" dangerouslySetInnerHTML={{ __html: s.html }} />}
    </div>
  );
}

// ------------------------------------------------------------------ portfolio
type SortKey = keyof SoldRow;

export function SoldDashboard() {
  const q = useQuery({ queryKey: ["sold-portfolio"], queryFn: () => api.get<SoldPortfolio>("/sold/portfolio"), staleTime: 60_000 });
  const [sort, setSort] = React.useState<{ k: SortKey; asc: boolean }>({ k: "investment_date", asc: true });
  const [showCols, setShowCols] = React.useState(false);
  if (q.isLoading) return <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-96" /></div>;
  if (!q.data) return <Empty title="Sold deals not loaded" sub="Run the Deal Intelligence ingest to load the summary workbook." />;
  const d = q.data; const dv = d.derived;
  const grand = d.totals_as_written.find((t) => t.which === "grand");
  const deals = d.rows.filter((r) => r.kind === "deal");
  const advances = d.rows.filter((r) => r.kind === "advance");
  const sorted = [...deals].sort((a, b) => {
    const x = a[sort.k] as unknown, y = b[sort.k] as unknown;
    const c = typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""));
    return sort.asc ? c : -c;
  });
  const owed = d.rows.filter((r) => (r.excess_shortage ?? 0) < -1).sort((a, b) => (a.excess_shortage ?? 0) - (b.excess_shortage ?? 0));
  const th = (k: SortKey, label: string, right = false) => (
    <th onClick={() => setSort((s) => ({ k, asc: s.k === k ? !s.asc : true }))} className={cn("px-3 py-2 text-[10.5px] uppercase tracking-wide text-faint font-semibold cursor-pointer select-none whitespace-nowrap hover:text-fg", right && "text-right")}>
      {label}{sort.k === k && <span className="ml-1 text-accent">{sort.asc ? "↑" : "↓"}</span>}
    </th>
  );
  const syn = d.synthesis;
  const keySections = new Set([2, 5, 7, 10]);
  return (
    <div className="space-y-6">
      <div>
        <div className="flex items-center gap-2 text-[11px] font-bold tracking-[0.18em] text-accent uppercase"><Archive size={12} /> Deal intelligence</div>
        <h1 className="text-2xl font-semibold tracking-tight mt-1">Sold deals</h1>
        <p className="text-[13px] text-muted mt-1">{dv.deals} completed deals and {dv.advances} advance rows, from <span className="font-medium text-fg">{d.workbook.file}</span> (sheet “{d.workbook.sheet}”, file dated {fmtDate(d.workbook.file_mtime, "d MMM yyyy")}). Every figure below is from that sheet; the three derived lines are marked.</p>
      </div>

      {/* 1. the sheet's own grand total, as written */}
      <section>
        <div className="text-[12px] font-semibold text-muted mb-2">The sheet’s Grand Total, as written (29 deals + 5 DC advances)</div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Num label="Cash invested" value={usd(grand?.cash_invested)} />
          <Num label="Yield billed" value={usd(grand?.rkb_yield)} sub={`points ${usd(grand?.points_billed)} · interest ${usd(grand?.interest_billed)} · ext. fees ${usd(grand?.extension_fee_billed)}`} />
          <Num label="Total to be collected" value={usd(grand?.total_to_collect)} sub="= invested + yield" />
          <Num label="Cash collected" value={usd(grand?.cash_collected)} />
          <Num label="Still to collect (net)" value={usd(grand?.excess_shortage)} tone="critical" sub={`DC advances ${usd(-dv.advances_outstanding)} + the 29 deals net ${usd((grand?.excess_shortage ?? 0) + dv.advances_outstanding)} · gross shortages ${usd(-dv.still_to_collect_all)}`} />
        </div>
      </section>

      {/* 2. the 29 deals: what was actually made */}
      <section>
        <div className="text-[12px] font-semibold text-muted mb-2">The 29 deals only — derived from the rows (labelled; not on the sheet)</div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Num label="Cash profit realised" value={usd(dv.deals_cash_profit)} tone={dv.deals_cash_profit >= 0 ? "good" : "critical"} sub={`collected − invested · ${pct(dv.deals_cash_profit_pct)} on cash`} />
          <Num label="Cash profit, per year" value={pct(dv.cash_profit_annualised)} sub={`capital out ${Math.round(dv.capital_weighted_days)} days on average (capital-weighted)`} />
          <Num label="Yield billed, per year" value={pct(dv.billed_yield_annualised)} sub={`${pct(dv.deals_yield_pct)} billed on cash — if it had all been collected`} />
          <Num label="Shortages" value={usd(dv.deals_shortage_sum)} tone="critical" sub={`${owed.filter((r) => r.kind === "deal").length} deals not fully collected`} />
          <Num label="Over-collections" value={usd(dv.deals_excess_sum)} sub="rows where collected exceeds the amount due — JP to attribute" />
        </div>
        <div className="mt-2 text-[12px] text-muted flex items-start gap-1.5"><Info size={13} className="mt-0.5 shrink-0" /><span>A negative in “Excess / (shortage)” is <b>collected minus the amount due</b>: that deal still owes RKB. “Yield” is what was <b>billed</b> (points + interest + fees), not what came in — which is why billed return is {pct(dv.billed_yield_annualised)} a year and cash return is {pct(dv.cash_profit_annualised)}.</span></div>
      </section>

      {/* 3. verdicts */}
      <section className="grid grid-cols-3 gap-3 max-w-xl">
        <Num label="Good" value={d.verdict_counts.good} tone="good" sub="full collection, ≤125 days, a control that held" />
        <Num label="Mixed" value={d.verdict_counts.mixed} sub="money back, but the file shows the control would not have held" />
        <Num label="Bad" value={d.verdict_counts.bad} tone="critical" sub="material shortage or LP kept the proceeds" />
      </section>

      {/* 4. every deal */}
      <Card>
        <CardHeader title="Every deal" sub="The workbook's columns, as on the sheet. Click a column to sort; click an address for its post-mortem." right={<button onClick={() => setShowCols((v) => !v)} className="text-[12px] text-muted hover:text-fg">{showCols ? "Fewer columns" : "All columns"}</button>} />
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-[12.5px]">
            <thead><tr className="border-b border-line">
              {th("address", "Property")}{th("state", "St")}{th("investment_date", "Invested")}{th("received_date", "Received")}{th("days_invested", "Days", true)}
              {th("cash_invested", "Cash invested", true)}{showCols && th("points_billed", "Points", true)}{showCols && th("interest_billed", "Interest", true)}{showCols && th("extension_fee_billed", "Ext. fee", true)}
              {th("rkb_yield", "Yield", true)}{showCols && th("roi", "ROI", true)}{th("annualized_roi", "Ann. ROI", true)}{showCols && th("total_to_collect", "To collect", true)}
              {th("cash_collected", "Collected", true)}{th("excess_shortage", "Excess / (short)", true)}{th("section", "Category")}{th("verdict", "Verdict")}
            </tr></thead>
            <tbody>
              {sorted.map((r) => {
                const es = r.excess_shortage ?? 0;
                return (
                  <tr key={r.label} className={cn("border-b border-line/60", r.verdict === "bad" && "bg-critical-soft/40", r.verdict === "good" && "bg-good-soft/30")}>
                    <td className="px-3 py-2 font-medium whitespace-nowrap">{r.property_id ? <Link href={`/sold/${r.property_id}`} className="hover:underline">{r.address}</Link> : r.address}</td>
                    <td className="px-3 py-2 text-muted">{r.state}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDate(r.investment_date, "d MMM yy")}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{fmtDate(r.received_date, "d MMM yy")}</td>
                    <td className="px-3 py-2 text-right tnum">{r.days_invested ?? ""}</td>
                    <td className="px-3 py-2 text-right tnum">{usd(r.cash_invested, true)}</td>
                    {showCols && <td className="px-3 py-2 text-right tnum">{usd(r.points_billed, true)}</td>}
                    {showCols && <td className="px-3 py-2 text-right tnum">{usd(r.interest_billed, true)}</td>}
                    {showCols && <td className="px-3 py-2 text-right tnum">{usd(r.extension_fee_billed, true)}</td>}
                    <td className="px-3 py-2 text-right tnum">{usd(r.rkb_yield, true)}</td>
                    {showCols && <td className="px-3 py-2 text-right tnum">{pct(r.roi)}</td>}
                    <td className="px-3 py-2 text-right tnum">{pct(r.annualized_roi)}</td>
                    {showCols && <td className="px-3 py-2 text-right tnum">{usd(r.total_to_collect, true)}</td>}
                    <td className="px-3 py-2 text-right tnum">{usd(r.cash_collected, true)}</td>
                    <td className={cn("px-3 py-2 text-right tnum font-medium", es < -1 && "text-critical", es > 1 && "text-good")}>{usd(r.excess_shortage, true)}</td>
                    <td className="px-3 py-2 text-muted whitespace-nowrap">{r.section === "lp_sold_kept_cash" ? "LP sold, kept cash" : "Paid from sale"}</td>
                    <td className="px-3 py-2"><Verdict v={r.verdict} /></td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              {d.totals_as_written.map((t) => (
                <tr key={t.which} className="border-t border-line bg-sunken/60 font-semibold">
                  <td className="px-3 py-2" colSpan={5}>{t.which === "grand" ? "Grand Total (sheet)" : t.which === "paid_from_sale" ? "Total — paid from sale (sheet)" : "Total — LP sold and kept cash + DC advances (sheet)"}</td>
                  <td className="px-3 py-2 text-right tnum">{usd(t.cash_invested, true)}</td>
                  {showCols && <td className="px-3 py-2 text-right tnum">{usd(t.points_billed, true)}</td>}
                  {showCols && <td className="px-3 py-2 text-right tnum">{usd(t.interest_billed, true)}</td>}
                  {showCols && <td className="px-3 py-2 text-right tnum">{usd(t.extension_fee_billed, true)}</td>}
                  <td className="px-3 py-2 text-right tnum">{usd(t.rkb_yield, true)}</td>
                  {showCols && <td />}<td />
                  {showCols && <td className="px-3 py-2 text-right tnum">{usd(t.total_to_collect, true)}</td>}
                  <td className="px-3 py-2 text-right tnum">{usd(t.cash_collected, true)}</td>
                  <td className={cn("px-3 py-2 text-right tnum", (t.excess_shortage ?? 0) < 0 && "text-critical")}>{usd(t.excess_shortage, true)}</td>
                  <td colSpan={2} />
                </tr>
              ))}
            </tfoot>
          </table>
        </div>
        {advances.length > 0 && (
          <div className="mt-3 text-[12px] text-muted">Also on the sheet, not properties: {advances.length} “Advance against DC properties” rows totalling {usd(dv.advances_outstanding)} — nothing billed, nothing collected.</div>
        )}
      </Card>

      {/* 5. still to collect */}
      <Card>
        <CardHeader title="Still to collect" sub="Rows where collected is below the amount due, largest first." />
        <ul className="divide-y divide-line">
          {owed.map((r) => (
            <li key={r.label} className="py-2 flex items-center justify-between gap-3 text-[13px]">
              <div className="min-w-0"><span className="font-medium">{r.property_id ? <Link href={`/sold/${r.property_id}`} className="hover:underline">{r.address}</Link> : r.label}</span><span className="text-muted"> · due {usd(r.total_to_collect, true)} · collected {usd(r.cash_collected, true)}</span>{r.reason && <div className="text-[12px] text-muted truncate">{r.reason}</div>}</div>
              <div className="text-critical font-semibold tnum whitespace-nowrap">{usd(r.excess_shortage, true)}</div>
            </li>
          ))}
        </ul>
      </Card>

      {/* 6. what the deals say together */}
      {syn && (
        <Card>
          <CardHeader title="What the deals say together" sub={`Cross-deal analysis over ${syn.deals} deals · ${syn.model} · ${fmtDate(syn.built_at, "d MMM yyyy")}. The four sections that matter are open; the rest are a click away.`} />
          <div className="space-y-2">
            {syn.sections.map((s) => <Section key={s.n} s={s} open={keySections.has(s.n)} />)}
          </div>
        </Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- deal page
export function SoldDealView({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["sold-deal", pid], queryFn: () => api.get<SoldDeal>(`/sold/deals/${pid}`), staleTime: 60_000 });
  if (q.isLoading) return <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-96" /></div>;
  if (!q.data) return <Empty title="Deal not found" />;
  const d = q.data; const r = d.row; const pm = d.postmortem;
  const key = new Set([6, 7, 8, 9]);
  const ordered = pm ? [...pm.sections.filter((s) => key.has(s.n)), ...pm.sections.filter((s) => !key.has(s.n))] : [];
  return (
    <div className="space-y-5">
      <div>
        <Link href="/sold" className="text-[12px] text-muted hover:text-fg inline-flex items-center gap-1"><ArrowLeft size={12} /> Sold deals</Link>
        <div className="flex flex-wrap items-center gap-3 mt-1">
          <h1 className="text-2xl font-semibold tracking-tight">{d.address}</h1>
          <Verdict v={d.verdict} />
          <span className="text-[13px] text-muted">{d.city}{d.city && d.state ? ", " : ""}{d.state}{r?.section === "lp_sold_kept_cash" ? " · LP sold and kept the cash" : ""}</span>
        </div>
        {d.reason && <p className="text-[13.5px] mt-1.5">{d.reason}</p>}
      </div>
      {r && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <Num label="Cash invested" value={usd(r.cash_invested, true)} sub={fmtDate(r.investment_date, "d MMM yyyy")} />
          <Num label="Yield billed" value={usd(r.rkb_yield, true)} sub={`pts ${usd(r.points_billed)} · int ${usd(r.interest_billed)} · ext ${usd(r.extension_fee_billed)}`} />
          <Num label="Total to collect" value={usd(r.total_to_collect, true)} />
          <Num label="Collected" value={usd(r.cash_collected, true)} sub={fmtDate(r.received_date, "d MMM yyyy")} />
          <Num label="Excess / (shortage)" value={usd(r.excess_shortage, true)} tone={(r.excess_shortage ?? 0) < -1 ? "critical" : "good"} />
          <Num label="Days · ann. ROI" value={`${r.days_invested ?? "—"} · ${pct(r.annualized_roi)}`} sub={`ROI ${pct(r.roi)} (billed)`} />
        </div>
      )}
      <div className="text-[12px] text-muted">File read for the post-mortem: {d.counts.emails} emails, {d.counts.attachments} attachments, {d.counts.files} folder documents{pm ? ` · ${pm.model} · ${pm.tool_calls} retrievals · $${pm.cost_usd}` : ""}.</div>
      {pm ? (
        <div className="space-y-2">
          <div className="text-[12px] font-semibold text-muted">Best 3, worst 3, verdict and lessons first; the full account below.</div>
          {ordered.map((s) => <Section key={s.n} s={s} open={key.has(s.n)} />)}
          <details className="rounded-xl border border-line bg-elev px-4 py-2 text-[12.5px]"><summary className="cursor-pointer font-semibold">Sources read ({pm.sources.length})</summary>
            <ol className="mt-2 space-y-0.5 text-muted">{pm.sources.map((s, i) => <li key={s.sha256}><span className="cite">[#{i + 1}]</span> {s.name || s.sha256.slice(0, 12)} <span className="text-faint">{fmtDate(s.date, "d MMM yyyy")}</span></li>)}</ol>
          </details>
        </div>
      ) : <Empty title="No post-mortem yet for this deal" />}
    </div>
  );
}
