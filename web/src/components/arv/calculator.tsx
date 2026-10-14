"use client";

/* The MAO Rehab Calculator, live. Left: every input the team's spreadsheet has
   (buying, holding, selling, financing, repair lines with adders). Right: the
   numbers — MAO two ways, profit, ROI, the capital stack, lender checks, and a
   sensitivity grid of profit against ARV and rehab misses. Each change commits
   to the server and the whole file recomputes in one round trip. */
import * as React from "react";
import { Segmented, Statistic, Switch } from "antd";
import { FileDown, Hammer, Info, Scale } from "lucide-react";
import { Badge, Button, Card } from "@/components/arv/ui";
import { cn, money } from "@/lib/utils";
import { arvApi, num, pct, type ArvStatus, type RehabLine } from "@/lib/arv";
import { H, NumField, Row, SelectField } from "./bits";
import type { TabProps } from "./comps";

export function CalculatorTab({ doc, patch, saving, status }: TabProps & { status?: ArvStatus }) {
  const uw = doc.underwriting;
  const rb = doc.rehab_budget;
  const a = { ...(status?.defaults.assumptions || {}), ...(uw?.assumptions || {}), ...(doc.assumptions || {}) };
  const A = (k: string) => (v: number | null) => patch({ assumptions: { [k]: v } });
  const adders = { ...(status?.defaults.adders || {}), ...(rb?.adders || {}) };
  const AD = (k: string) => (v: number | null) => patch({ rehab: { adders: { ...adders, [k]: v } } });
  const mode = doc.rehab?.mode || rb?.mode || "tier";
  const lines: RehabLine[] = rb?.lines || [];
  const setLine = (key: string, amount: number | null) => {
    const next = lines.map((l) => ({ key: l.key, label: l.label, amount: l.key === key ? (amount ?? 0) : l.amount }));
    patch({ rehab: { mode: "lines", lines: next } });
  };
  const d = uw?.deal;
  const mao = uw?.mao;
  return (
    <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
      {/* ------------------------------------------------------------ inputs */}
      <div className="space-y-4">
        <Card className="p-4">
          <H right={<Badge tone={saving ? "high" : "good"}>{saving ? "recomputing…" : "live"}</Badge>}>Step 1 · After repair value</H>
          <div className="flex items-end justify-between gap-4">
            <div><div className="text-3xl font-semibold tracking-tight tnum">{money(doc.valuation?.arv)}</div><div className="text-[11px] text-muted mt-0.5">from the comps · {doc.valuation?.confidence?.label}{doc.analysis ? ` · analyst ${money(doc.analysis.arv_opinion, true)}` : ""}</div></div>
            <div className="text-right text-[11px] text-muted">$/sqft <b className="tnum text-fg">{num(uw?.value_per_sqft, 0)}</b><br />rehab $/sqft <b className="tnum text-fg">{num(uw?.rehab_per_sqft, 0)}</b></div>
          </div>
        </Card>

        <Card className="p-4 space-y-4">
          <H>Step 2 · Fixed costs</H>
          <div>
            <div className="text-xs font-medium mb-2">Buying costs <span className="text-faint font-normal">· total {money(uw?.buying.total)}</span></div>
            <div className="grid grid-cols-3 gap-2">
              <NumField label="Brokerage" value={a.buy_brokerage} onCommit={A("buy_brokerage")} prefix="$" compact />
              <NumField label="Inspection" value={a.buy_inspection} onCommit={A("buy_inspection")} prefix="$" compact />
              <NumField label="Appraisal" value={a.buy_appraisal} onCommit={A("buy_appraisal")} prefix="$" compact />
              <NumField label="Title search" value={a.buy_title_search} onCommit={A("buy_title_search")} prefix="$" compact />
              <NumField label="Title insurance" value={a.buy_title_insurance} onCommit={A("buy_title_insurance")} prefix="$" compact />
              <NumField label="Other" value={a.buy_other} onCommit={A("buy_other")} prefix="$" compact />
            </div>
          </div>
          <div>
            <div className="text-xs font-medium mb-2">Holding costs <span className="text-faint font-normal">· {money(uw?.holding.total)} over {a.months_held} months ({money(uw?.holding.monthly)}/mo)</span></div>
            <div className="grid grid-cols-3 gap-2">
              <NumField label="Months held" value={a.months_held} onCommit={A("months_held")} compact />
              <NumField label="Property taxes / yr" value={a.property_taxes_annual ?? uw?.holding.taxes_annual_used ?? null} onCommit={A("property_taxes_annual")} prefix="$" compact hint={uw?.holding.taxes_basis} />
              <NumField label="Insurance / yr" value={a.insurance_annual} onCommit={A("insurance_annual")} prefix="$" compact />
              <NumField label="Utilities / mo" value={a.utilities_monthly} onCommit={A("utilities_monthly")} prefix="$" compact />
              <NumField label="HOA / mo" value={a.hoa_monthly} onCommit={A("hoa_monthly")} prefix="$" compact />
              <NumField label="Maintenance / mo" value={a.maintenance_monthly} onCommit={A("maintenance_monthly")} prefix="$" compact />
            </div>
          </div>
          <div>
            <div className="text-xs font-medium mb-2">Selling costs <span className="text-faint font-normal">· total {money(uw?.selling.total)}</span></div>
            <div className="grid grid-cols-3 gap-2">
              <NumField label="Commission" value={a.commission_pct} onCommit={A("commission_pct")} suffix="%" step={0.5} compact />
              <NumField label="Seller-assisted closing" value={a.seller_assist_pct} onCommit={A("seller_assist_pct")} suffix="%" step={0.5} compact />
              <NumField label="Transfer tax" value={a.transfer_tax_pct ?? uw?.selling.transfer_tax_pct ?? null} onCommit={A("transfer_tax_pct")} suffix="%" step={0.05} compact hint={a.transfer_tax_pct == null ? `${doc.geocode?.state || "state"} default` : undefined} />
              <NumField label="Home warranty" value={a.home_warranty} onCommit={A("home_warranty")} prefix="$" compact />
              <NumField label="Title insurance" value={a.sell_title_insurance} onCommit={A("sell_title_insurance")} prefix="$" compact />
              <NumField label="2nd appraisal" value={a.second_appraisal} onCommit={A("second_appraisal")} prefix="$" compact />
            </div>
          </div>
          <div>
            <div className="text-xs font-medium mb-2 flex items-center gap-2">Financing <span className="text-faint font-normal">· {money(d?.financing.total)} · loan {money(d?.financing.loan)} ({d?.financing.binding})</span>
              <span className="ml-auto flex items-center gap-1.5 text-[11px] font-normal text-muted"><Switch size="small" checked={a.finance !== false} onChange={(v) => patch({ assumptions: { finance: v } })} /> hard-money loan</span></div>
            <div className={cn("grid grid-cols-3 gap-2", a.finance === false && "opacity-40 pointer-events-none")}>
              <NumField label="Max loan-to-cost" value={a.ltc_pct} onCommit={A("ltc_pct")} suffix="%" compact />
              <NumField label="Max loan-to-ARV" value={a.ltarv_pct} onCommit={A("ltarv_pct")} suffix="%" compact />
              <NumField label="Rehab financed" value={a.rehab_financed_pct} onCommit={A("rehab_financed_pct")} suffix="%" compact />
              <NumField label="Interest rate" value={a.rate_pct} onCommit={A("rate_pct")} suffix="%" step={0.25} compact hint="interest-only" />
              <NumField label="Points" value={a.points_pct} onCommit={A("points_pct")} suffix="%" step={0.25} compact />
              <NumField label="Loan fees" value={a.loan_fees} onCommit={A("loan_fees")} prefix="$" compact />
            </div>
          </div>
        </Card>

        <Card className="p-4 space-y-3">
          <H right={<Segmented size="small" value={mode} onChange={(m) => patch({ rehab: { mode: m as "tier" | "lines" | "lump" } })} options={[{ value: "tier", label: "By scope" }, { value: "lines", label: "Line items" }, { value: "lump", label: "Lump sum" }]} />}>Step 3 · Repair costs</H>
          <div className="grid grid-cols-3 gap-2">
            <SelectField label="Scope" value={doc.rehab?.scope || rb?.scope || "moderate"} onChange={(v) => patch({ rehab: { scope: v, mode: mode === "lump" ? "lump" : "tier" } })} options={Object.entries(status?.scopes || {}).map(([k, s]) => [k, s.label] as [string, string])} compact />
            <SelectField label="Finish level" value={doc.rehab?.level || rb?.level || "mid"} onChange={(v) => patch({ rehab: { level: v as "low" | "mid" | "high", mode: "tier" } })} options={[["low", "Low"], ["mid", "Mid"], ["high", "High"]]} compact />
            <NumField label="Lump sum" value={doc.rehab?.lump_sum ?? null} onCommit={(v) => patch({ rehab: { lump_sum: v, mode: v ? "lump" : "tier" } })} prefix="$" compact />
          </div>
          {rb && <div className="text-[11px] text-muted flex items-start gap-1.5"><Info size={12} className="mt-0.5 shrink-0" /> {rb.basis} Tier range {money(rb.tier.range.low, true)} – {money(rb.tier.range.high, true)}.</div>}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {lines.map((l) => (
              <div key={l.key} className="flex items-center justify-between gap-2 border-b border-line/50 py-0.5">
                <span className={cn("text-[12px] truncate", !l.amount && "text-faint")}>{l.label}</span>
                <NumField value={l.amount} onCommit={(v) => setLine(l.key, v)} prefix="$" compact className="w-[112px] shrink-0" />
              </div>
            ))}
          </div>
          <div className="grid grid-cols-4 gap-2 pt-1">
            <NumField label="Location mult." value={adders.location_multiplier} onCommit={AD("location_multiplier")} step={0.05} compact />
            <NumField label="Permits" value={adders.permit_pct} onCommit={AD("permit_pct")} suffix="%" step={0.5} compact />
            <NumField label="Contractor OH&P" value={adders.oh_and_p_pct} onCommit={AD("oh_and_p_pct")} suffix="%" compact />
            <NumField label="Contingency" value={adders.contingency_pct} onCommit={AD("contingency_pct")} suffix="%" compact />
          </div>
          <div className="rounded-lg bg-sunken/60 p-3 text-[12px]">
            <Row label="Subtotal estimate" value={money(rb?.subtotal)} />
            <Row label="Adders" sub={`location ${money(rb?.location_adjustment)} · permits ${money(rb?.permit_cost)} · OH&P ${money(rb?.oh_and_p)} · contingency ${money(rb?.contingency)}`} value={money(rb?.total_adders)} />
            <Row label="Total repair estimate" value={money(rb?.total)} strong />
          </div>
        </Card>
      </div>

      {/* ----------------------------------------------------------- results */}
      <div className="space-y-4">
        <Card className="p-4">
          <H right={<Badge tone="accent"><Scale size={11} /> {uw?.price_basis}</Badge>}>Purchase price</H>
          <div className="flex items-end gap-4">
            <NumField label="Purchase price" value={a.purchase_price ?? null} onCommit={A("purchase_price")} prefix="$" placeholder={mao ? String(mao.recommended) : ""} className="w-48" hint="blank = use the recommended MAO" />
            <NumField label="Rule" value={a.rule_pct} onCommit={A("rule_pct")} suffix="%" className="w-28" />
            <NumField label="Profit target" value={a.profit_target_pct} onCommit={A("profit_target_pct")} suffix="% ARV" className="w-32" />
            <NumField label="Min profit" value={a.profit_target_min} onCommit={A("profit_target_min")} prefix="$" className="w-32" />
          </div>
        </Card>

        <div className="grid grid-cols-2 gap-4">
          <Card className="p-4">
            <Statistic title={`MAO · ${num(mao?.rule_pct, 0)}% rule`} value={money(mao?.rule)} />
            <div className="text-[11px] text-muted mt-1">ARV × {num(mao?.rule_pct, 0)}% − repairs {money(uw?.rehab_total, true)}</div>
          </Card>
          <Card className="p-4">
            <Statistic title="MAO · profit target" value={money(mao?.profit_target)} />
            <div className="text-[11px] text-muted mt-1">leaves {money(mao?.target_profit, true)} after every cost</div>
          </Card>
        </div>
        <Card className="p-4 border-accent/40 bg-accent-soft/30">
          <div className="flex items-start justify-between gap-3">
            <div><div className="text-[11px] uppercase tracking-wide text-accent">Recommended maximum offer</div><div className="text-3xl font-semibold tracking-tight tnum mt-1">{money(mao?.recommended)}</div><div className="text-[11px] text-muted mt-1">{mao?.basis}</div></div>
            {doc.analysis && <div className="text-right"><div className="text-[11px] uppercase tracking-wide text-muted font-medium">Analyst&apos;s offer</div><div className="text-xl font-semibold tnum">{money(doc.analysis.mao_opinion)}</div></div>}
          </div>
        </Card>

        {d && (
          <Card className="p-4">
            <H>Deal at {money(d.purchase_price)}</H>
            <Row label="Purchase price" value={money(d.purchase_price)} />
            <Row label="Repairs (with adders)" value={money(uw?.rehab_total)} />
            <Row label="Buying costs" value={money(uw?.buying.total)} />
            <Row label="Holding costs" sub={`${a.months_held} months`} value={money(d.holding_total)} />
            <Row label="Selling costs" value={money(d.selling_total)} />
            <Row label="Financing costs" sub={`loan ${money(d.financing.loan, true)} · ${pct(d.financing.ltarv_pct, 0)} LTARV · ${pct(d.financing.ltc_pct, 0)} LTC · ${d.financing.binding}`} value={money(d.financing.total)} />
            <Row label="Total project cost" value={money(d.total_project_cost)} strong />
            <Row label="Profit" value={money(d.profit)} strong tone={d.profit >= 0 ? "text-good" : "text-critical"} />
            <div className="grid grid-cols-4 gap-3 mt-3">
              <Stat l="Profit % ARV" v={pct(d.profit_pct_of_arv)} />
              <Stat l="Profit / month" v={money(d.profit_per_month, true)} />
              <Stat l="Cash ROI" v={pct(d.cash_roi_pct)} />
              <Stat l="Annualized" v={pct(d.annualized_roi_pct)} />
            </div>
            <div className="mt-3 text-[11px] text-muted">Capital needed {money(d.capital.total)}: debt {money(d.capital.debt)} · cash {money(d.capital.equity)}</div>
            <div className="mt-2 h-2 w-full rounded-full bg-sunken overflow-hidden flex"><div className="bg-money-in" style={{ width: `${d.capital.total ? (d.capital.debt / d.capital.total) * 100 : 0}%` }} /><div className="bg-accent" style={{ width: `${d.capital.total ? (d.capital.equity / d.capital.total) * 100 : 0}%` }} /></div>
          </Card>
        )}

        {uw?.checks && (
          <Card className="p-4">
            <H>Lender and investor checks</H>
            <ul className="space-y-2">
              {uw.checks.map((c) => (
                <li key={c.key} className="flex items-start gap-2 text-[12px]">
                  <span className={cn("mt-0.5 h-4 w-4 rounded-full grid place-items-center text-[10px] font-bold shrink-0", c.ok ? "bg-good-soft text-good" : "bg-critical-soft text-critical")}>{c.ok ? "✓" : "!"}</span>
                  <div><b>{c.label}</b> {c.value === null || c.value === undefined ? "—" : `${c.value}%`}{c.limit !== null ? <span className="text-faint"> · line {c.limit}%</span> : null}<div className="text-muted">{c.note}</div></div>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {uw?.sensitivity && (
          <Card className="p-4">
            <H right={<a href={arvApi.xlsxUrl(doc.analysis_id)}><Button size="sm" variant="ghost"><FileDown size={13} /> Calculator .xlsx</Button></a>}>Profit if ARV or rehab miss</H>
            <table className="w-full text-[11px] tnum">
              <thead><tr><th className="text-left text-faint font-medium py-1">ARV \ rehab</th>{uw.sensitivity[0].cells.map((c) => <th key={c.rehab_delta_pct} className="text-right text-faint font-medium py-1">{c.rehab_delta_pct > 0 ? "+" : ""}{c.rehab_delta_pct}%</th>)}</tr></thead>
              <tbody>
                {uw.sensitivity.map((r) => (
                  <tr key={r.arv_delta_pct}>
                    <td className="py-1 text-muted">{r.arv_delta_pct > 0 ? "+" : ""}{r.arv_delta_pct}%</td>
                    {r.cells.map((c) => {
                      const p = c.profit;
                      const tone = p < 0 ? "bg-critical-soft text-critical" : (c.profit_pct ?? 0) < 10 ? "bg-high-soft text-high" : "bg-good-soft text-good";
                      return <td key={c.rehab_delta_pct} className="py-1 pl-1"><div className={cn("rounded-md px-2 py-1 text-right font-medium", tone, r.arv_delta_pct === 0 && c.rehab_delta_pct === 0 && "ring-1 ring-accent")}>{money(p, true)}</div></td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[10.5px] text-faint mt-2 flex items-center gap-1"><Hammer size={11} /> Red: a loss. Amber: under 10% of ARV. The ringed cell is the base case at the purchase price above.</div>
          </Card>
        )}
      </div>
    </div>
  );
}

function Stat({ l, v }: { l: string; v: React.ReactNode }) {
  return <div><div className="text-[10px] uppercase tracking-wide text-muted font-medium">{l}</div><div className="text-base font-semibold tnum">{v}</div></div>;
}
