"use client";

/* The money sheet of one property: the proposal line by line, and against each
   line what has been billed, is in review, is paid and is held back. On top, the
   deal's own figures. What the system reads is never typed over; JP Sir and
   Rakesh Sir may correct a figure (the system's reading stays beside it), and
   the team may record work done and a note on a line. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, Pencil } from "lucide-react";
import { api } from "@/lib/api";
import { Card, Skeleton } from "@/components/ui";
import { TabBody } from "@/components/brain/tabs";
import { usd } from "@/components/brain/side";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText } from "@/lib/brain";

type Row = {
  key: string; kind: "proposal" | "change_order" | "other"; group: string; title: string; cost_code: string; description?: string;
  proposal: number; price: number; change_orders: number; revised: number; billed: number; in_review: number; to_pay: number; paid: number;
  held_back: number; left: number; billed_percent: number | null; bills: number; work_done: number | null; note: string; input_by?: string | null; input_at?: string | null;
};
type Figure = { key: string; label: string; kind: "money" | "percent" | "date"; value: number | string | null; system: number | string | null; source: string; set_by?: string | null; set_at?: string | null; assumed?: boolean };
type Step = { at?: string; by_name?: string } | null;
type SheetBill = { bill_id: number; title: string; pay_to?: string; amount: number; date?: string; status: string; holdback: boolean; has_lines: boolean; steps: Record<string, Step> };
type Markup = { builder_cost: number; price: number; markup: number; points: number; six_months_interest: number; remainder: number; rkb_half: number; roi_at_start: number; roi_after_sale: number; basis: string };
type Interest = { starts: string | null; daily: number | null; days: number | null; accrued: number | null; why_not: string | null; free_days_left?: number };
type Sheet = {
  property_id: string; rows: Row[]; totals: Record<string, number | null>; notes: string[]; figures: Record<string, Figure>;
  deal: { markup: Markup | null; interest: Interest; rate_assumed: boolean }; bills: SheetBill[];
  payments: { paid: number; paid_count: number; to_pay: number; to_pay_count: number; in_review: number; in_review_count: number; held_back: number; last_paid: { title: string; amount: number; at?: string | null } | null };
  estimate: { locked: boolean; locked_by?: string; locked_at?: string; pulled_at?: string } | null; bills_pulled_at?: string | null;
  history: { what: string; label?: string; key: string; value?: number | string | null; was?: number | string | null; work_done?: number | null; note?: string; by_name?: string; at?: string }[];
  can: { figures: boolean; lines: boolean };
};

const num = (n?: number | null) => (n == null || n === 0 ? "" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const figText = (f: Figure) => f.value == null ? "—" : f.kind === "money" ? usd(Number(f.value)) : f.kind === "percent" ? `${f.value}%` : fmtDate(String(f.value), "d MMM yyyy");
const LABEL = "text-[9.5px] font-semibold uppercase tracking-[0.09em] text-faint";

function FigureTile({ f, can, onSave }: { f: Figure; can: boolean; onSave: (key: string, value: string) => Promise<void> }) {
  const [edit, setEdit] = React.useState(false);
  const [v, setV] = React.useState("");
  const start = () => { setV(f.value == null ? "" : f.kind === "date" ? String(f.value).slice(0, 10) : String(f.value)); setEdit(true); };
  const save = async (value: string) => { await onSave(f.key, value); setEdit(false); };
  return (
    <div className="rounded-lg border border-line bg-elev px-3 py-2 min-w-0">
      <div className={cn(LABEL, "flex items-center gap-1")}>
        <span className="truncate flex-1">{f.label}</span>
        {can && !edit && <button type="button" onClick={start} title="Correct this figure" className="text-faint hover:text-accent"><Pencil size={10} /></button>}
      </div>
      {edit ? (
        <form className="mt-1" onSubmit={(e) => { e.preventDefault(); save(v); }}>
          <input autoFocus value={v} onChange={(e) => setV(e.target.value)} type={f.kind === "date" ? "date" : "text"} inputMode={f.kind === "date" ? undefined : "decimal"}
            className="w-full h-7 rounded-md border border-line-strong bg-bg px-2 text-[12.5px] tnum outline-none focus:border-accent" />
          <div className="mt-1 flex items-center gap-2 text-[11px]">
            <button type="submit" className="font-semibold text-accent">Save</button>
            <button type="button" onClick={() => setEdit(false)} className="text-muted">Cancel</button>
            {f.set_by && <button type="button" onClick={() => save("")} className="text-muted ml-auto" title="Go back to what the system reads">Use system reading</button>}
          </div>
        </form>
      ) : (
        <>
          <div className={cn("mt-0.5 text-[15px] font-semibold tracking-tight tnum truncate", f.value == null && "text-faint")}>{figText(f)}</div>
          <div className="text-[11px] text-muted truncate" title={f.set_by ? `System reading: ${f.system == null ? "none" : figText({ ...f, value: f.system })}` : f.source}>
            {f.set_by ? <>set by {f.set_by} {ago(f.set_at)}</> : f.value == null ? (can ? "not on record — add it" : "not on record") : f.source || "from the records"}
          </div>
        </>
      )}
    </div>
  );
}

/* A cell a person fills in. Saved when they leave it, only if it changed. */
type CellProps = { value: string; can: boolean; onSave: (v: string) => void; width: string; align?: "right"; placeholder?: string; suffix?: string };

function Cell(p: CellProps) {
  if (!p.can) return <span className={cn("text-muted", p.align === "right" && "tnum")}>{p.value ? `${p.value}${p.suffix || ""}` : ""}</span>;
  return <CellInput key={p.value} {...p} />;      // a fresh input whenever the saved value changes
}

function CellInput({ value, onSave, width, align, placeholder }: CellProps) {
  const [v, setV] = React.useState(value);
  return (
    <input value={v} onChange={(e) => setV(e.target.value)} onBlur={() => { if (v.trim() !== value) onSave(v.trim()); }}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setV(value); }} placeholder={placeholder}
      className={cn("h-6 rounded border border-transparent bg-transparent px-1.5 text-[12px] outline-none hover:border-line focus:border-accent focus:bg-bg placeholder:text-faint/70", width, align === "right" && "text-right tnum")} />
  );
}

export function MoneySheetTab({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const key = ["brain", "sheet", pid];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<Sheet>(`/brain/properties/${pid}/sheet`) });
  const [all, setAll] = React.useState(false);
  if (!q.data) return <TabBody wide><Card className="p-4"><Skeleton className="h-40" /></Card></TabBody>;
  const s = q.data;
  const reload = () => qc.invalidateQueries({ queryKey: key });
  const saveFigure = async (k: string, value: string) => {
    try { await api.post(`/brain/properties/${pid}/sheet/figure`, { key: k, value }); toast.success("Saved."); } catch (e) { toast.error(errText(e)); }
    reload();
  };
  const saveLine = async (k: string, body: { work_done?: string; note?: string }) => {
    try { await api.post(`/brain/properties/${pid}/sheet/line`, { key: k, ...body }); } catch (e) { toast.error(errText(e)); }
    reload();
  };
  const rows = all ? s.rows : s.rows.filter((r) => r.billed || r.kind !== "proposal" || r.work_done != null || r.note);
  const groups: { name: string; rows: Row[] }[] = [];
  rows.forEach((r) => { const g = groups[groups.length - 1]; if (g && g.name === r.group) g.rows.push(r); else groups.push({ name: r.group, rows: [r] }); });
  const t = s.totals, mk = s.deal.markup, it = s.deal.interest;
  const TH = "px-2 py-1.5 font-semibold text-right whitespace-nowrap";
  const TD = "px-2 py-1 text-right tnum whitespace-nowrap";

  return (
    <TabBody wide>
      {s.notes.map((n) => <div key={n} className="rounded-lg border border-high/30 bg-high-soft px-3 py-1.5 text-[12px] text-high">{n}</div>)}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2.5">
        {Object.values(s.figures).map((f) => <FigureTile key={f.key} f={f} can={s.can.figures} onSave={saveFigure} />)}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
        {([["Paid on bills", s.payments.paid, `${s.payments.paid_count} bills${s.payments.last_paid?.at ? ` · last ${ago(s.payments.last_paid.at)}` : ""}`, "text-good"],
          ["Approved, to pay", s.payments.to_pay, `${s.payments.to_pay_count} bills waiting for payment`, s.payments.to_pay > 0 ? "text-high" : ""],
          ["In review", s.payments.in_review, `${s.payments.in_review_count} waiting for approval`, ""],
          ["Held back until the sale", s.payments.held_back, "the contractor's 10%", "text-muted"]] as [string, number, string, string][]).map(([l, v, sub, tone]) => (
          <div key={l} className="rounded-lg border border-line bg-sunken/40 px-3 py-2 min-w-0">
            <div className={LABEL}>{l}</div>
            <div className={cn("mt-0.5 text-[15px] font-semibold tracking-tight tnum", tone)}>{usd(v)}</div>
            <div className="text-[11px] text-muted truncate">{sub}</div>
          </div>
        ))}
      </div>
      <div className="-mt-1 text-[11px] text-muted">These four move by themselves: when a bill is ticked Paid on the Bills page, and each time Buildertrend is read.</div>

      <div className="grid lg:grid-cols-2 gap-2.5">
        <Card className="px-3.5 py-2.5">
          <div className={LABEL}>Interest to the homeowner</div>
          <ul className="mt-1.5 space-y-1 text-[12.5px]">
            <li className="flex justify-between gap-3"><span className="text-muted">First six months</span><span>no interest to the homeowner</span></li>
            <li className="flex justify-between gap-3"><span className="text-muted">Interest starts</span><span className="tnum">{it.starts ? fmtDate(it.starts, "d MMM yyyy") : "—"}{it.free_days_left ? ` · in ${it.free_days_left} days` : ""}</span></li>
            <li className="flex justify-between gap-3"><span className="text-muted">Daily, on the full note at {String(s.figures.rate.value)}%{s.deal.rate_assumed ? " (assumed)" : ""}</span><span className="tnum">{it.daily == null ? "—" : usd(it.daily)}</span></li>
            <li className="flex justify-between gap-3"><span className="text-muted">Accrued to today</span><span className="tnum font-semibold">{it.accrued == null ? "—" : `${usd(it.accrued)} · ${it.days} days`}</span></li>
          </ul>
          {it.why_not && <div className="mt-1.5 text-[11.5px] text-high">Not worked out: {it.why_not}.</div>}
        </Card>
        <Card className="px-3.5 py-2.5">
          <div className={LABEL}>How the markup is shared</div>
          {!mk ? <div className="mt-1.5 text-[12.5px] text-muted">No proposal read from Buildertrend yet.</div> : (
            <>
              <ul className="mt-1.5 space-y-1 text-[12.5px]">
                <li className="flex justify-between gap-3"><span className="text-muted">Price to homeowner − builder cost</span><span className="tnum">{usd(mk.price)} − {usd(mk.builder_cost)} = <b className="font-semibold">{usd(mk.markup)}</b></span></li>
                <li className="flex justify-between gap-3"><span className="text-muted">Less 2 points and six months&apos; interest</span><span className="tnum">−{usd(mk.points)} −{usd(mk.six_months_interest)}</span></li>
                <li className="flex justify-between gap-3"><span className="text-muted">Left to share</span><span className="tnum font-semibold">{usd(mk.remainder)}</span></li>
                <li className="flex justify-between gap-3"><span className="text-muted">RKB half · ROI at start · ROI after sale</span><span className="tnum">{usd(mk.rkb_half)} · {usd(mk.roi_at_start)} · {usd(mk.roi_after_sale)}</span></li>
              </ul>
              <div className="mt-1.5 text-[11.5px] text-high">{mk.basis}</div>
            </>
          )}
        </Card>
      </div>

      <Card className="overflow-hidden">
        <div className="px-3.5 py-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line">
          <div className="text-[13px] font-semibold">Proposal, line by line</div>
          <div className="text-[11.5px] text-muted">
            {s.estimate ? <>Proposal {s.estimate.locked ? `locked by ${s.estimate.locked_by || "the contractor"} ${fmtDate(s.estimate.locked_at, "d MMM yyyy")}` : "not locked"} · read {ago(s.estimate.pulled_at)}</> : "No proposal read"}
            {s.bills_pulled_at && <> · bills read {ago(s.bills_pulled_at)}</>}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <div className="inline-flex rounded-lg bg-sunken p-0.5 border border-line">
              {[["With activity", false], ["All lines", true]].map(([l, v]) => (
                <button key={String(l)} type="button" onClick={() => setAll(v as boolean)} className={cn("h-6 px-2 rounded-md text-[11.5px] font-medium", all === v ? "bg-elev text-fg shadow-[var(--shadow-sm)]" : "text-muted")}>{l}</button>
              ))}
            </div>
            <a href={`/api/brain/properties/${pid}/sheet.xlsx`} className="h-7 px-2.5 rounded-lg border border-line bg-bg text-[12px] font-medium inline-flex items-center gap-1.5 hover:border-line-strong"><Download size={12} /> Excel</a>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead><tr className="text-[10px] uppercase tracking-[0.07em] text-faint border-b border-line bg-sunken/40">
              <th className="px-3 py-1.5 font-semibold text-left">Line item</th><th className={TH}>Proposal</th><th className={TH}>Change orders</th><th className={TH}>Revised</th>
              <th className={TH}>Billed</th><th className={TH}>In review</th><th className={TH}>To pay</th><th className={TH}>Paid</th><th className={TH}>Held back</th><th className={TH}>Left to bill</th>
              <th className={TH}>%</th><th className={TH}>Work done</th><th className="px-2 py-1.5 font-semibold text-left">Note</th>
            </tr></thead>
            <tbody>
              {groups.map((g) => (
                <React.Fragment key={g.name + g.rows[0].key}>
                  <tr className="bg-sunken/30 border-b border-line/60"><td colSpan={13} className="px-3 py-1 text-[11px] font-semibold text-muted">{g.name}</td></tr>
                  {g.rows.map((r) => (
                    <tr key={r.key} className="border-b border-line/50 hover:bg-sunken/30">
                      <td className="px-3 py-1 min-w-[230px]" title={r.description || undefined}>
                        <div className="truncate max-w-[300px]">{r.title}{r.kind === "change_order" && <span className="ml-1.5 text-[10px] font-semibold text-high">CHANGE ORDER</span>}</div>
                        <div className="text-[10.5px] text-faint truncate max-w-[300px]">{r.cost_code}</div>
                      </td>
                      <td className={TD}>{num(r.proposal)}</td><td className={cn(TD, "text-high")}>{num(r.change_orders)}</td><td className={TD}>{num(r.revised)}</td>
                      <td className={cn(TD, "font-medium")}>{num(r.billed)}</td><td className={cn(TD, "text-high")}>{num(r.in_review)}</td><td className={TD}>{num(r.to_pay)}</td>
                      <td className={cn(TD, "text-good")}>{num(r.paid)}</td><td className={cn(TD, "text-muted")}>{num(r.held_back)}</td><td className={TD}>{num(r.left)}</td>
                      <td className={cn(TD, "text-muted")}>{r.billed_percent == null ? "" : `${r.billed_percent}%`}</td>
                      <td className="px-1 py-0.5 text-right"><Cell value={r.work_done == null ? "" : String(r.work_done)} can={s.can.lines} width="w-14" align="right" placeholder="%" suffix="%" onSave={(v) => saveLine(r.key, { work_done: v })} /></td>
                      <td className="px-1 py-0.5" title={r.input_by ? `${r.input_by} ${ago(r.input_at)}` : undefined}><Cell value={r.note} can={s.can.lines} width="w-56" placeholder="note" onSave={(v) => saveLine(r.key, { note: v })} /></td>
                    </tr>
                  ))}
                </React.Fragment>
              ))}
              <tr className="font-semibold bg-sunken/40">
                <td className="px-3 py-1.5">Total · {s.rows.length} lines</td><td className={TD}>{num(t.proposal)}</td><td className={TD}>{num(t.change_orders)}</td><td className={TD}>{num(t.revised)}</td>
                <td className={TD}>{num(t.billed)}</td><td className={TD}>{num(t.in_review)}</td><td className={TD}>{num(t.to_pay)}</td><td className={TD}>{num(t.paid)}</td>
                <td className={TD}>{num(t.held_back)}</td><td className={TD}>{num(t.left)}</td><td className={TD}>{t.billed_percent == null ? "" : `${t.billed_percent}%`}</td><td /><td />
              </tr>
            </tbody>
          </table>
        </div>
        <div className="px-3.5 py-1.5 text-[11px] text-muted border-t border-line">
          Proposal = builder cost. To pay = approved in Buildertrend, not yet paid. Held back = the contractor&apos;s 10%, paid at the sale. Amounts are the contractor&apos;s entries in Buildertrend.
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="px-3.5 py-2 text-[13px] font-semibold border-b border-line">Bills on this property <span className="font-normal text-muted">· {s.bills.length}</span></div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead><tr className="text-[10px] uppercase tracking-[0.07em] text-faint border-b border-line text-left">
              <th className="px-3 py-1.5 font-semibold">Bill date</th><th className="px-2 py-1.5 font-semibold">Bill</th><th className={TH}>Amount</th><th className="px-2 py-1.5 font-semibold">Buildertrend</th>
              <th className="px-2 py-1.5 font-semibold">Manju Sir</th><th className="px-2 py-1.5 font-semibold">Ready to pay</th><th className="px-2 py-1.5 font-semibold">Paid</th>
            </tr></thead>
            <tbody>
              {[...s.bills].reverse().map((b) => {
                const tick = (k: string) => b.steps?.[k]?.at ? <span className="text-good" title={b.steps[k]?.by_name || undefined}>{fmtDate(b.steps[k]!.at, "d MMM")}</span> : <span className="text-faint">—</span>;
                return (
                  <tr key={b.bill_id} className="border-b border-line/50">
                    <td className="px-3 py-1 whitespace-nowrap tnum">{fmtDate(b.date, "d MMM yyyy")}</td>
                    <td className="px-2 py-1"><span className="truncate">{b.title}</span>{b.holdback && <span className="ml-1.5 text-[10px] font-semibold text-muted">HOLD-BACK</span>}{!b.has_lines && <span className="ml-1.5 text-[10px] text-high">lines not read</span>}</td>
                    <td className={TD}>{usd(b.amount)}</td>
                    <td className={cn("px-2 py-1 whitespace-nowrap", b.status === "In review" ? "text-high" : b.status === "Paid" ? "text-good" : "text-muted")}>{b.status}</td>
                    <td className="px-2 py-1">{tick("manju_approved")}</td><td className="px-2 py-1">{tick("ready_to_pay")}</td><td className="px-2 py-1">{tick("paid")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {s.history.length > 0 && (
        <Card className="px-3.5 py-2.5">
          <div className={LABEL}>Changes made by people</div>
          <ul className="mt-1.5 space-y-0.5 text-[12px] text-muted">
            {s.history.slice(0, 12).map((h, i) => (
              <li key={i}>
                <span className="text-fg">{h.by_name}</span> {h.what === "figure"
                  ? <>set {h.label} to {h.value == null ? "the system reading" : String(h.value).slice(0, 10)}{h.was != null && <> (was {String(h.was).slice(0, 10)})</>}</>
                  : <>wrote on a line{h.work_done != null && <> · work done {h.work_done}%</>}{h.note ? <> · “{h.note}”</> : null}</>} · {ago(h.at)}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </TabBody>
  );
}
