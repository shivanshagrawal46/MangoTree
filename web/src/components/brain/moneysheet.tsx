"use client";

/* The money sheet of one property, as a funding ledger.

   Top: the builder cost and where every dollar of it stands — paid, approved and
   waiting, in review, held back until the sale, not billed yet.
   Middle: the proposal line by line. Approved and Paid show what Buildertrend
   shows until a person writes the amount themselves; their figure then stands,
   marked as theirs, and Buildertrend's is kept beside it.
   Below: money that is not a line of the proposal — a contingency, a payment to
   the homeowner, a fee — each under a name a person gives it.

   JP Sir, Manjunath Sir and Rakesh Sir write on the lines; JP Sir and Rakesh Sir
   also correct the loan's own figures. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, Download, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Card, Skeleton } from "@/components/ui";
import { TabBody } from "@/components/brain/tabs";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText } from "@/lib/brain";

type Row = {
  key: string; kind: "proposal" | "change_order" | "other"; group: string; title: string; cost_code: string; description?: string;
  proposal: number; change_orders: number; revised: number; billed: number; in_review: number; to_pay: number; paid: number; held_back: number;
  to_fund: number; approved: number; sys_approved: number; sys_paid: number; approved_by?: string | null; paid_by?: string | null;
  work_done: number | null; note: string; input_by?: string | null; input_at?: string | null;
  includes?: { key: string; title: string; amount: number }[];      // bills a person put on this line
};
type Outside = {
  key: string; source: "buildertrend" | "entry"; title: string; detail?: string; category: string | null; amount: number; approved: number; paid: number;
  in_review: number; date?: string | null; note?: string; by_name?: string | null; is_approved?: boolean; is_paid?: boolean;
  suggest?: { key: string; title: string } | null;                  // the proposal line it most likely belongs to
};
type Funding = {
  budget: number; proposal: number; change_orders: number; paid: number; held_back: number; to_pay: number; in_review: number; not_billed: number; over_budget: number;
  left: number; paid_percent: number | null; outside: { key: string | null; label: string; amount: number; paid: number; count: number }[];
  outside_amount: number; outside_paid: number; homeowner_paid: number; homeowner_count: number; total_paid_out: number; suggested: number;
};
type Figure = { key: string; label: string; kind: "money" | "percent" | "date"; value: number | string | null; system: number | string | null; source: string; set_by?: string | null; set_at?: string | null; assumed?: boolean };
type Step = { at?: string; by_name?: string } | null;
type SheetBill = { bill_id: number; title: string; pay_to?: string; amount: number; date?: string; status: string; holdback: boolean; has_lines: boolean; steps: Record<string, Step> };
type Markup = { builder_cost: number; price: number; markup: number; points: number; six_months_interest: number; remainder: number; rkb_half: number; roi_at_start: number; roi_after_sale: number; basis: string };
type Interest = { starts: string | null; daily: number | null; days: number | null; accrued: number | null; why_not: string | null; free_days_left?: number };
type Change = { what: string; label?: string; key: string; value?: number | string | null; was?: number | string | null; work_done?: number | null; note?: string; approved?: number | boolean | null; paid?: number | boolean | null; category?: string | null; by_name?: string; at?: string };
type Sheet = {
  property_id: string; rows: Row[]; notes: string[]; figures: Record<string, Figure>; funding: Funding; outside: Outside[]; categories: Record<string, string>;
  deal: { markup: Markup | null; interest: Interest; rate_assumed: boolean }; bills: SheetBill[];
  estimate: { locked: boolean; locked_by?: string; locked_at?: string; pulled_at?: string } | null; bills_pulled_at?: string | null;
  history: Change[]; can: { figures: boolean; lines: boolean };
};
type PartKey = "paid" | "to_pay" | "in_review" | "held_back" | "not_billed";

const usd = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const usd0 = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }));
const amt = (n?: number | null) => (n == null || Math.abs(n) < 0.005 ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const figText = (f: Figure) => f.value == null ? "—" : f.kind === "money" ? usd(Number(f.value)) : f.kind === "percent" ? `${f.value}%` : fmtDate(String(f.value), "d MMM yyyy");
const EYEBROW = "text-[10px] font-semibold uppercase tracking-[0.1em] text-faint";

/* ───────────────────────────── the funding picture ───────────────────────────── */

const PARTS: { key: PartKey; label: string; bar: string; hint: string }[] = [
  { key: "paid", label: "Paid", bar: "bg-good", hint: "Paid to the contractor against lines of the builder cost" },
  { key: "to_pay", label: "Approved, to pay", bar: "bg-high", hint: "Approved and waiting for payment" },
  { key: "in_review", label: "In review", bar: "bg-sky-500", hint: "Billed by the contractor, waiting for approval" },
  { key: "held_back", label: "Held back until the sale", bar: "bg-slate-400", hint: "The contractor's 10%, paid at the sale" },
  { key: "not_billed", label: "Not billed yet", bar: "bg-line-strong", hint: "Work the contractor has not billed" },
];

function FundingHero({ f }: { f: Funding }) {
  const pct = (n: number) => (f.budget > 0 ? Math.max(0, (n / f.budget) * 100) : 0);
  return (
    <Card className="px-5 pt-4 pb-4">
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-x-8 gap-y-3">
        <div>
          <div className={EYEBROW}>Builder cost</div>
          <div className="mt-1 text-[26px] leading-none font-semibold tracking-tight tnum">{usd(f.budget)}</div>
          <div className="mt-1.5 text-[12px] text-muted tnum">Proposal {usd0(f.proposal)}{f.change_orders > 0 && <> + change orders {usd0(f.change_orders)}</>}</div>
        </div>
        <div>
          <div className={EYEBROW}>Funded so far</div>
          <div className="mt-1 text-[26px] leading-none font-semibold tracking-tight tnum text-good">{usd(f.paid)}</div>
          <div className="mt-1.5 text-[12px] text-muted">{f.paid_percent == null ? "—" : `${f.paid_percent}%`} of the builder cost has been paid out</div>
        </div>
        <div>
          <div className={EYEBROW}>Left to fund</div>
          <div className="mt-1 text-[26px] leading-none font-semibold tracking-tight tnum">{usd(f.left)}</div>
          <div className="mt-1.5 text-[12px] text-muted tnum">{f.held_back > 0 ? <>of which {usd0(f.held_back)} is held back until the sale</> : "nothing held back yet"}</div>
        </div>
      </div>

      <div className="mt-4 h-3 w-full rounded-full bg-sunken overflow-hidden flex" role="img" aria-label="Where the builder cost stands">
        {PARTS.map((p) => { const w = pct(f[p.key]); return w > 0 ? <div key={p.key} className={cn("h-full", p.bar)} style={{ width: `${w}%` }} title={`${p.label}: ${usd(f[p.key])}`} /> : null; })}
      </div>
      <div className="mt-3 grid grid-cols-2 md:grid-cols-5 gap-x-5 gap-y-2">
        {PARTS.map((p) => (
          <div key={p.key} title={p.hint} className="min-w-0">
            <div className="flex items-center gap-1.5 text-[11.5px] text-muted"><span className={cn("h-2 w-2 rounded-sm shrink-0", p.bar)} /><span className="truncate">{p.label}</span></div>
            <div className="mt-0.5 pl-3.5 text-[13.5px] font-semibold tnum">{usd(f[p.key])}</div>
          </div>
        ))}
      </div>
      {f.over_budget > 0 && <div className="mt-3 rounded-lg bg-critical-soft text-critical text-[12px] px-3 py-1.5">Billed {usd(f.over_budget)} more than the builder cost. A change order is missing, or a bill is too high.</div>}
    </Card>
  );
}

/* ───────────────────────────── small cards ───────────────────────────── */

function FigureRow({ f, can, onSave }: { f: Figure; can: boolean; onSave: (key: string, value: string) => Promise<void> }) {
  const [edit, setEdit] = React.useState(false);
  const [v, setV] = React.useState("");
  const start = () => { setV(f.value == null ? "" : f.kind === "date" ? String(f.value).slice(0, 10) : String(f.value)); setEdit(true); };
  const save = async (value: string) => { await onSave(f.key, value); setEdit(false); };
  if (edit) return (
    <form className="py-1.5 flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); save(v); }}>
      <span className="text-[12.5px] text-muted flex-1 truncate">{f.label}</span>
      <input autoFocus value={v} onChange={(e) => setV(e.target.value)} type={f.kind === "date" ? "date" : "text"} inputMode={f.kind === "date" ? undefined : "decimal"}
        className="w-32 h-7 rounded-md border border-accent bg-bg px-2 text-[12.5px] tnum text-right outline-none" />
      <button type="submit" className="text-[12px] font-semibold text-accent">Save</button>
      <button type="button" onClick={() => setEdit(false)} className="text-[12px] text-muted">Cancel</button>
      {f.set_by && <button type="button" onClick={() => save("")} className="text-[12px] text-muted" title="Go back to what the system reads">Reset</button>}
    </form>
  );
  return (
    <div className="group py-1.5 flex items-baseline gap-2">
      <span className="text-[12.5px] text-muted flex-1 truncate">{f.label}</span>
      <span className={cn("text-[13px] font-medium tnum", f.value == null && "text-faint font-normal")} title={f.set_by ? `Set by ${f.set_by}. System reading: ${f.system == null ? "none" : figText({ ...f, value: f.system })}` : f.source}>
        {f.value == null ? "not on record" : figText(f)}{f.assumed && <span className="ml-1 text-[10.5px] font-normal text-high">assumed</span>}{f.set_by && <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-accent align-middle" />}
      </span>
      {can && <button type="button" onClick={start} title="Correct this figure" className="text-faint hover:text-accent opacity-0 group-hover:opacity-100 focus:opacity-100 transition"><Pencil size={11} /></button>}
    </div>
  );
}

function Line({ label, value, strong, tone }: { label: React.ReactNode; value: React.ReactNode; strong?: boolean; tone?: string }) {
  return <div className="py-1.5 flex items-baseline gap-3"><span className="text-[12.5px] text-muted flex-1 min-w-0 truncate">{label}</span><span className={cn("text-[13px] tnum", strong ? "font-semibold" : "font-medium", tone)}>{value}</span></div>;
}

/* ───────────────────────────── cells people write in ───────────────────────────── */

/* A money cell: shows the amount; click to write your own. Empty = back to Buildertrend's. */
function MoneyCell({ value, system, by, can, onSave, tone }: { value: number; system?: number; by?: string | null; can: boolean; onSave: (v: string) => void; tone?: string }) {
  const [edit, setEdit] = React.useState(false);
  const [v, setV] = React.useState("");
  const shown = <span className={cn("tnum", Math.abs(value) < 0.005 && "text-faint", tone)}>{amt(value)}</span>;
  if (!can) return shown;
  if (edit) return (
    <input autoFocus value={v} onChange={(e) => setV(e.target.value)} inputMode="decimal" placeholder={system != null ? amt(system) : ""}
      onBlur={() => { setEdit(false); const next = v.trim(); if (next !== (by ? String(value) : "")) onSave(next); }}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setV(by ? String(value) : ""); setEdit(false); } }}
      className="w-24 h-6 rounded border border-accent bg-bg px-1.5 text-[12px] text-right tnum outline-none" />
  );
  return (
    <button type="button" onClick={() => { setV(by ? String(value) : ""); setEdit(true); }}
      title={by ? `Written by ${by}. Buildertrend shows ${amt(system)}. Click to change; clear it to go back.` : "From Buildertrend. Click to write the amount yourself."}
      className="inline-flex items-center gap-1 rounded px-1 -mx-1 h-6 cursor-text border-b border-dashed border-transparent hover:border-accent/60 hover:bg-accent-soft transition">
      {by && <span className="h-1.5 w-1.5 rounded-full bg-accent shrink-0" />}{shown}
    </button>
  );
}

type TextCellProps = { value: string; can: boolean; onSave: (v: string) => void; className?: string; placeholder?: string; suffix?: string; align?: "right" };
function TextCell(p: TextCellProps) {
  if (!p.can) return <span className={cn("text-muted", p.align === "right" && "tnum")}>{p.value ? `${p.value}${p.suffix || ""}` : ""}</span>;
  return <TextCellInput key={p.value} {...p} />;
}
function TextCellInput({ value, onSave, className, placeholder, align }: TextCellProps) {
  const [v, setV] = React.useState(value);
  return (
    <input value={v} onChange={(e) => setV(e.target.value)} onBlur={() => { if (v.trim() !== value) onSave(v.trim()); }} placeholder={placeholder}
      onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setV(value); }}
      className={cn("h-6 rounded border border-transparent bg-transparent px-1.5 text-[12px] outline-none hover:border-line focus:border-accent focus:bg-bg placeholder:text-faint/60", align === "right" && "text-right tnum", className)} />
  );
}

function Bar({ row }: { row: Pick<Row, "revised" | "paid" | "held_back" | "to_pay" | "in_review"> }) {
  const of = (n: number) => (row.revised > 0 ? Math.min(100, Math.max(0, (n / row.revised) * 100)) : 0);
  const done = row.revised > 0 ? Math.round(((row.paid + row.held_back + row.to_pay + row.in_review) / row.revised) * 100) : null;
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-[72px] rounded-full bg-sunken overflow-hidden flex">
        <div className="h-full bg-good" style={{ width: `${of(row.paid)}%` }} /><div className="h-full bg-high" style={{ width: `${of(row.to_pay)}%` }} />
        <div className="h-full bg-sky-500" style={{ width: `${of(row.in_review)}%` }} /><div className="h-full bg-slate-400" style={{ width: `${of(row.held_back)}%` }} />
      </div>
      <span className="w-8 text-right text-[11px] text-muted tnum">{done == null ? "" : `${Math.min(999, done)}%`}</span>
    </div>
  );
}

/* ───────────────────────────── the page ───────────────────────────── */

const TH = "px-2.5 py-2 font-semibold text-right whitespace-nowrap";
const TD = "px-2.5 py-1.5 text-right whitespace-nowrap";
const FRESH = { category: "contingency", title: "", amount: "", date: "", approved: true, paid: false };

export function MoneySheetTab({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const key = ["brain", "sheet", pid];
  const q = useQuery({ queryKey: key, queryFn: () => api.get<Sheet>(`/brain/properties/${pid}/sheet`) });
  const [view, setView] = React.useState<"active" | "all">("active");
  const [find, setFind] = React.useState("");
  const [shut, setShut] = React.useState<Record<string, boolean>>({});
  const [adding, setAdding] = React.useState(false);
  const [draft, setDraft] = React.useState(FRESH);
  const [outView, setOutView] = React.useState<"todo" | "all">("todo");
  const [outAll, setOutAll] = React.useState(false);
  if (!q.data) return <TabBody wide><Card className="p-5"><Skeleton className="h-28" /></Card><Card className="p-5"><Skeleton className="h-64" /></Card></TabBody>;
  const s = q.data, f = s.funding, mk = s.deal.markup, it = s.deal.interest;
  const reload = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["brain", "followups"] }); };
  const post = async (path: string, body: unknown, ok?: string) => { try { await api.post(`/brain/properties/${pid}/sheet/${path}`, body); if (ok) toast.success(ok); } catch (e) { toast.error(errText(e)); } reload(); };
  const saveFigure = (k: string, value: string) => post("figure", { key: k, value }, "Saved.");
  const saveLine = (k: string, body: Record<string, string>) => post("line", { key: k, ...body });
  const saveEntry = (o: Outside, patch: Partial<{ category: string; approved: boolean; paid: boolean }>) =>
    post("entry", { id: o.key, category: patch.category ?? o.category ?? "other", title: o.title, amount: String(o.amount), date: o.date ? String(o.date).slice(0, 10) : null,
      approved: patch.approved ?? !!o.is_approved, paid: patch.paid ?? !!o.is_paid, note: o.note || "" });
  const addEntry = async () => {
    if (!draft.amount.trim()) { toast.error("Enter the amount."); return; }
    await post("entry", { ...draft, date: draft.date || null }, "Payment added.");
    setAdding(false); setDraft(FRESH);
  };
  const removeEntry = async (o: Outside) => { try { await api.del(`/brain/properties/${pid}/sheet/entry/${o.key}`); toast.success("Removed."); } catch (e) { toast.error(errText(e)); } reload(); };
  const acceptAll = async () => {
    try { const r = await api.post<{ assigned: number }>(`/brain/properties/${pid}/sheet/accept-suggestions`); toast.success(`${r.assigned} bill${r.assigned === 1 ? "" : "s"} put on their lines.`); } catch (e) { toast.error(errText(e)); }
    reload();
  };
  /* "What it is": a category, or — for a bill from Buildertrend — the proposal line it belongs to. */
  const nameIt = (o: Outside, value: string) => {
    if (value.startsWith("line:")) return saveLine(o.key, { assign: value.slice(5) });
    return o.source === "entry" ? saveEntry(o, { category: value }) : saveLine(o.key, { category: value });
  };
  const openAdd = () => { setAdding(true); document.getElementById("outside")?.scrollIntoView({ behavior: "smooth", block: "center" }); };

  const lines = s.rows.filter((r) => r.kind !== "other");
  const needle = find.trim().toLowerCase();
  const visible = lines.filter((r) => (view === "all" || r.billed !== 0 || r.kind === "change_order" || r.work_done != null || !!r.note || !!r.approved_by || !!r.paid_by)
    && (!needle || `${r.title} ${r.cost_code} ${r.group}`.toLowerCase().includes(needle)));
  const groups: { name: string; rows: Row[] }[] = [];
  visible.forEach((r) => { const g = groups[groups.length - 1]; if (g && g.name === r.group) g.rows.push(r); else groups.push({ name: r.group, rows: [r] }); });
  const sum = (rows: Row[], k: "revised" | "approved" | "paid" | "held_back" | "to_fund" | "to_pay" | "in_review") => rows.reduce((n, r) => n + r[k], 0);
  const cats = Object.entries(s.categories);
  const shownCats = f.outside.filter((c) => c.count > 0 || c.key === "contingency" || c.key === "homeowner");
  const unnamed = s.outside.filter((o) => !o.category);
  const outList = outView === "todo" && unnamed.length > 0 ? unnamed : s.outside;
  const outShown = outAll ? outList : outList.slice(0, 12);
  const lineGroups: { name: string; rows: Row[] }[] = [];
  lines.forEach((r) => { const g = lineGroups[lineGroups.length - 1]; if (g && g.name === r.group) g.rows.push(r); else lineGroups.push({ name: r.group, rows: [r] }); });

  return (
    <TabBody wide>
      {s.notes.map((n) => <div key={n} className="rounded-lg border border-high/30 bg-high-soft px-3 py-1.5 text-[12px] text-high">{n}</div>)}

      <FundingHero f={f} />

      <div className="grid lg:grid-cols-3 gap-3">
        <Card className="px-4 py-3">
          <div className="flex items-center justify-between"><div className={EYEBROW}>Outside the builder cost</div>
            {s.can.lines && <button type="button" onClick={openAdd} className="text-[12px] font-medium text-accent inline-flex items-center gap-1 hover:underline"><Plus size={12} /> Add payment</button>}
          </div>
          <div className="mt-1.5 divide-y divide-line/60">
            {shownCats.map((c) => (
              <Line key={String(c.key)} label={<>{c.label}{c.key === "homeowner" && c.count === 0 && <span className="ml-1.5 text-faint">· none recorded</span>}{c.key === null && <span className="ml-1.5 text-high">· name these below</span>}</>}
                value={c.count === 0 ? <span className="text-faint font-normal">—</span> : <>{usd(c.paid)}{c.amount !== c.paid && <span className="text-muted font-normal"> of {usd(c.amount)}</span>}</>} />
            ))}
            <Line label="Paid out in total, builder cost and outside" value={usd(f.total_paid_out)} strong />
          </div>
        </Card>

        <Card className="px-4 py-3">
          <div className={EYEBROW}>The loan</div>
          <div className="mt-1.5 divide-y divide-line/60">
            {Object.values(s.figures).map((fg) => <FigureRow key={fg.key} f={fg} can={s.can.figures} onSave={saveFigure} />)}
          </div>
        </Card>

        <Card className="px-4 py-3">
          <div className={EYEBROW}>Interest and markup</div>
          <div className="mt-1.5 divide-y divide-line/60">
            <Line label="Interest to the homeowner starts" value={it.starts ? <>{fmtDate(it.starts, "d MMM yyyy")}{it.free_days_left ? <span className="text-muted font-normal"> · in {it.free_days_left} days</span> : null}</> : "—"} />
            <Line label={`Accrued to today${it.daily != null ? ` · ${usd(it.daily)} a day` : ""}`} value={it.accrued == null ? <span className="text-faint font-normal" title={it.why_not || undefined}>needs the note amount</span> : usd(it.accrued)} />
            {mk && <>
              <Line label="Markup (price − builder cost)" value={usd(mk.markup)} />
              <Line label="Less 2 points and six months' interest" value={`− ${usd(mk.points + mk.six_months_interest)}`} />
              <Line label="RKB's half" value={usd(mk.rkb_half)} strong />
              <Line label="ROI Blocks · at start / after the sale" value={`${usd0(mk.roi_at_start)} / ${usd0(mk.roi_after_sale)}`} />
            </>}
          </div>
          {mk && <div className="mt-1.5 text-[11px] text-high leading-snug">{mk.basis}</div>}
        </Card>
      </div>

      {/* ───────── the builder cost, line by line ───────── */}
      <Card className="overflow-hidden">
        <div className="px-4 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line">
          <div>
            <div className="text-[14px] font-semibold tracking-tight">Builder cost, line by line</div>
            <div className="text-[11.5px] text-muted">
              {s.estimate ? <>Proposal {s.estimate.locked ? `locked by ${s.estimate.locked_by || "the contractor"}` : "not locked"} · Buildertrend read {ago(s.bills_pulled_at || s.estimate.pulled_at)}</> : "No proposal read from Buildertrend"}
              {s.can.lines && <> · click an Approved or Paid amount to write your own</>}
            </div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <label className="h-8 w-48 rounded-lg border border-line bg-bg px-2.5 flex items-center gap-1.5 text-muted focus-within:border-accent"><Search size={13} />
              <input value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find a line" className="w-full bg-transparent text-[12.5px] text-fg outline-none placeholder:text-faint" /></label>
            <div className="inline-flex rounded-lg bg-sunken p-0.5 border border-line">
              {([["active", "With activity"], ["all", `All ${lines.length} lines`]] as const).map(([k, l]) => (
                <button key={k} type="button" onClick={() => setView(k)} className={cn("h-7 px-2.5 rounded-md text-[12px] font-medium", view === k ? "bg-elev text-fg shadow-[var(--shadow-sm)]" : "text-muted")}>{l}</button>
              ))}
            </div>
            <a href={`/api/brain/properties/${pid}/sheet.xlsx`} className="h-8 px-3 rounded-lg border border-line bg-bg text-[12.5px] font-medium inline-flex items-center gap-1.5 hover:border-line-strong"><Download size={13} /> Excel</a>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead><tr className="text-[10px] uppercase tracking-[0.08em] text-faint border-b border-line">
              <th className="pl-4 pr-2 py-2 font-semibold text-left">Line item</th><th className={TH}>Budget</th><th className={TH}>Approved</th><th className={TH}>Paid</th>
              <th className={TH}>Held back</th><th className={TH}>Left to fund</th><th className="px-2.5 py-2 font-semibold text-left" title="How much of the line has been billed: paid, to pay, in review and held back">Billed</th><th className={TH}>Work done</th><th className="px-2.5 py-2 font-semibold text-left">Note</th>
            </tr></thead>
            <tbody>
              {groups.length === 0 && <tr><td colSpan={9} className="px-4 py-8 text-center text-[12.5px] text-muted">{needle ? "No line matches." : "Nothing billed yet. Show all lines to see the proposal."}</td></tr>}
              {groups.map((g) => {
                const closed = !!shut[g.name] && !needle;
                return (
                  <React.Fragment key={g.name + g.rows[0].key}>
                    <tr className="bg-sunken/50 border-b border-line/70 cursor-pointer select-none" onClick={() => setShut((x) => ({ ...x, [g.name]: !x[g.name] }))}>
                      <td className="pl-3 pr-2 py-1.5 text-[12px] font-semibold"><span className="inline-flex items-center gap-1.5"><ChevronDown size={13} className={cn("text-muted transition", closed && "-rotate-90")} />{g.name}<span className="font-normal text-faint">{g.rows.length}</span></span></td>
                      <td className={cn(TD, "tnum font-semibold")}>{amt(sum(g.rows, "revised"))}</td><td className={cn(TD, "tnum text-muted")}>{amt(sum(g.rows, "approved"))}</td>
                      <td className={cn(TD, "tnum text-muted")}>{amt(sum(g.rows, "paid"))}</td><td className={cn(TD, "tnum text-muted")}>{amt(sum(g.rows, "held_back"))}</td>
                      <td className={cn(TD, "tnum font-semibold")}>{amt(sum(g.rows, "to_fund"))}</td>
                      <td className="px-2.5"><Bar row={{ revised: sum(g.rows, "revised"), paid: sum(g.rows, "paid"), held_back: sum(g.rows, "held_back"), to_pay: sum(g.rows, "to_pay"), in_review: sum(g.rows, "in_review") }} /></td><td /><td />
                    </tr>
                    {!closed && g.rows.map((r) => (
                      <tr key={r.key} className="border-b border-line/50 hover:bg-accent-soft/30 transition-colors">
                        <td className="pl-9 pr-2 py-1.5 min-w-[200px] max-w-[300px]" title={r.description || undefined}>
                          <div className="truncate font-medium">{r.title}{r.kind === "change_order" && <span className="ml-2 align-middle rounded bg-high-soft text-high text-[9.5px] font-semibold px-1 py-px">CHANGE ORDER</span>}</div>
                          <div className="text-[11px] text-faint truncate">{r.cost_code}{r.in_review > 0 && <span className="text-sky-600"> · {amt(r.in_review)} in review</span>}{r.to_pay > 0 && <span className="text-high"> · {amt(r.to_pay)} to pay</span>}</div>
                          {(r.includes || []).length > 0 && (
                            <div className="mt-0.5 flex flex-wrap gap-1">
                              {r.includes!.map((x) => (
                                <span key={x.key} title="A bill under another name that a person put on this line" className="inline-flex items-center gap-1 rounded bg-sunken text-[10.5px] text-muted pl-1.5 pr-1 py-px max-w-full">
                                  <span className="truncate">+ {x.title} {amt(x.amount)}</span>
                                  {s.can.lines && <button type="button" onClick={() => saveLine(x.key, { assign: "" })} title="Take it off this line" className="text-faint hover:text-critical">×</button>}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td className={cn(TD, "tnum")}>{amt(r.revised)}</td>
                        <td className={TD}><MoneyCell value={r.approved} system={r.sys_approved} by={r.approved_by} can={s.can.lines} onSave={(v) => saveLine(r.key, { approved: v })} /></td>
                        <td className={TD}><MoneyCell value={r.paid} system={r.sys_paid} by={r.paid_by} can={s.can.lines} onSave={(v) => saveLine(r.key, { paid: v })} tone={r.paid > 0 ? "text-good" : undefined} /></td>
                        <td className={cn(TD, "tnum text-muted")}>{amt(r.held_back)}</td>
                        <td className={cn(TD, "tnum font-medium", r.to_fund < -0.005 && "text-critical")}>{amt(r.to_fund)}</td>
                        <td className="px-2.5"><Bar row={r} /></td>
                        <td className="px-1.5 py-0.5 text-right"><TextCell value={r.work_done == null ? "" : String(r.work_done)} can={s.can.lines} className="w-12" align="right" placeholder="%" suffix="%" onSave={(v) => saveLine(r.key, { work_done: v })} /></td>
                        <td className="px-1.5 py-0.5" title={r.input_by ? `${r.input_by} ${ago(r.input_at)}` : undefined}><TextCell value={r.note} can={s.can.lines} className="w-full min-w-[110px]" placeholder="Add a note" onSave={(v) => saveLine(r.key, { note: v })} /></td>
                      </tr>
                    ))}
                  </React.Fragment>
                );
              })}
            </tbody>
            <tfoot><tr className="border-t border-line bg-sunken/50 text-[12.5px] font-semibold">
              <td className="pl-4 pr-2 py-2">Builder cost · {lines.length} lines</td><td className={cn(TD, "tnum")}>{amt(f.budget)}</td><td className={cn(TD, "tnum")}>{amt(sum(lines, "approved"))}</td>
              <td className={cn(TD, "tnum text-good")}>{amt(f.paid)}</td><td className={cn(TD, "tnum")}>{amt(f.held_back)}</td><td className={cn(TD, "tnum")}>{amt(f.left)}</td>
              <td className="px-2.5"><Bar row={{ revised: f.budget, paid: f.paid, held_back: f.held_back, to_pay: f.to_pay, in_review: f.in_review }} /></td><td /><td />
            </tr></tfoot>
          </table>
        </div>
      </Card>

      {/* ───────── money that is not a line of the proposal ───────── */}
      <Card className="overflow-hidden" id="outside">
        <div className="px-4 py-2.5 flex flex-wrap items-center gap-3 border-b border-line">
          <div>
            <div className="text-[14px] font-semibold tracking-tight">Payments outside the builder cost</div>
            <div className="text-[11.5px] text-muted">Bills that are not on a proposal line, and payments RKB records itself. Put each on its line, or name it: contingency, paid to the homeowner, a fee.</div>
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {unnamed.length > 0 && (
              <div className="inline-flex rounded-lg bg-sunken p-0.5 border border-line">
                {([["todo", `To name ${unnamed.length}`], ["all", `All ${s.outside.length}`]] as const).map(([k, l]) => (
                  <button key={k} type="button" onClick={() => { setOutView(k); setOutAll(false); }} className={cn("h-7 px-2.5 rounded-md text-[12px] font-medium", outView === k ? "bg-elev text-fg shadow-[var(--shadow-sm)]" : "text-muted")}>{l}</button>
                ))}
              </div>
            )}
            {s.can.lines && f.suggested > 0 && <button type="button" onClick={acceptAll} title="Bills whose cost code matches exactly one proposal line" className="h-8 px-3 rounded-lg border border-accent/40 bg-accent-soft text-accent text-[12.5px] font-medium hover:border-accent">Put {f.suggested} suggested on their lines</button>}
            {s.can.lines && !adding && <button type="button" onClick={() => setAdding(true)} className="h-8 px-3 rounded-lg bg-accent text-white text-[12.5px] font-medium inline-flex items-center gap-1.5 hover:opacity-90"><Plus size={13} /> Add payment</button>}
          </div>
        </div>
        {adding && (
          <form className="px-4 py-3 flex flex-wrap items-end gap-2.5 border-b border-line bg-accent-soft/40" onSubmit={(e) => { e.preventDefault(); addEntry(); }}>
            <label className="text-[11px] text-muted">What it is
              <select value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} className="mt-0.5 block h-8 w-48 rounded-lg border border-line bg-bg px-2 text-[12.5px] text-fg outline-none focus:border-accent">
                {cats.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="text-[11px] text-muted flex-1 min-w-[200px]">Description
              <input autoFocus value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="e.g. paid to the homeowner at closing" className="mt-0.5 block h-8 w-full rounded-lg border border-line bg-bg px-2.5 text-[12.5px] text-fg outline-none focus:border-accent" /></label>
            <label className="text-[11px] text-muted">Amount
              <input value={draft.amount} onChange={(e) => setDraft({ ...draft, amount: e.target.value })} inputMode="decimal" placeholder="0.00" className="mt-0.5 block h-8 w-32 rounded-lg border border-line bg-bg px-2.5 text-[12.5px] text-fg text-right tnum outline-none focus:border-accent" /></label>
            <label className="text-[11px] text-muted">Date
              <input type="date" value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} className="mt-0.5 block h-8 rounded-lg border border-line bg-bg px-2 text-[12.5px] text-fg outline-none focus:border-accent" /></label>
            <label className="h-8 inline-flex items-center gap-1.5 text-[12.5px]"><input type="checkbox" checked={draft.approved} onChange={(e) => setDraft({ ...draft, approved: e.target.checked })} className="accent-[var(--accent)]" /> Approved</label>
            <label className="h-8 inline-flex items-center gap-1.5 text-[12.5px]"><input type="checkbox" checked={draft.paid} onChange={(e) => setDraft({ ...draft, paid: e.target.checked })} className="accent-[var(--accent)]" /> Paid</label>
            <button type="submit" className="h-8 px-3.5 rounded-lg bg-accent text-white text-[12.5px] font-medium">Save</button>
            <button type="button" onClick={() => setAdding(false)} className="h-8 px-2 text-[12.5px] text-muted">Cancel</button>
          </form>
        )}
        {s.outside.length === 0 ? (
          <div className="px-4 py-7 text-center text-[12.5px] text-muted">Nothing has been paid outside the builder cost. Nothing is recorded as paid to the homeowner.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead><tr className="text-[10px] uppercase tracking-[0.08em] text-faint border-b border-line text-left">
                <th className="pl-4 pr-2 py-2 font-semibold">Payment</th><th className="px-2.5 py-2 font-semibold">What it is</th><th className={TH}>Amount</th><th className={TH}>Approved</th><th className={TH}>Paid</th>
                <th className="px-2.5 py-2 font-semibold">Date</th><th className="px-2.5 py-2 font-semibold">From</th><th className="w-8" />
              </tr></thead>
              <tbody>
                {outShown.map((o) => (
                  <tr key={o.key} className="border-b border-line/50 hover:bg-accent-soft/30 transition-colors">
                    <td className="pl-4 pr-2 py-1.5 max-w-[320px]"><div className="truncate font-medium">{o.title}</div>{(o.detail || o.note) && <div className="text-[11px] text-faint truncate">{[o.detail, o.note].filter(Boolean).join(" · ")}</div>}</td>
                    <td className="px-2.5 py-1">
                      {s.can.lines ? (
                        <div className="flex items-center gap-2">
                          <select value={o.category || ""} onChange={(e) => nameIt(o, e.target.value)}
                            className={cn("h-7 w-44 rounded-md border bg-bg px-1.5 text-[12px] outline-none focus:border-accent", o.category ? "border-line text-fg" : "border-high/60 text-high font-medium")}>
                            {!o.category && <option value="">Choose what this is…</option>}
                            <optgroup label="Not a line of the proposal">{cats.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</optgroup>
                            {o.source === "buildertrend" && lineGroups.map((g) => (
                              <optgroup key={g.name + g.rows[0].key} label={`Proposal · ${g.name}`}>{g.rows.map((r) => <option key={r.key} value={`line:${r.key}`}>{r.title}</option>)}</optgroup>
                            ))}
                          </select>
                          {o.suggest && <button type="button" onClick={() => saveLine(o.key, { assign: o.suggest!.key })} title="Same cost code as this one proposal line" className="h-6 px-1.5 rounded border border-accent/40 text-accent text-[11px] font-medium whitespace-nowrap max-w-[190px] truncate hover:bg-accent-soft">→ {o.suggest.title}</button>}
                        </div>
                      ) : <span className={cn(!o.category && "text-high")}>{o.category ? s.categories[o.category] : "Not yet named"}</span>}
                    </td>
                    <td className={cn(TD, "tnum font-medium")}>{amt(o.amount)}</td>
                    <td className={TD}>{o.source === "entry"
                      ? <input type="checkbox" disabled={!s.can.lines} checked={!!o.is_approved} onChange={(e) => saveEntry(o, { approved: e.target.checked })} className="accent-[var(--accent)]" title="Approved" />
                      : <MoneyCell value={o.approved} can={s.can.lines} onSave={(v) => saveLine(o.key, { approved: v })} />}</td>
                    <td className={TD}>{o.source === "entry"
                      ? <input type="checkbox" disabled={!s.can.lines} checked={!!o.is_paid} onChange={(e) => saveEntry(o, { paid: e.target.checked })} className="accent-[var(--accent)]" title="Paid" />
                      : <MoneyCell value={o.paid} can={s.can.lines} onSave={(v) => saveLine(o.key, { paid: v })} tone={o.paid > 0 ? "text-good" : undefined} />}</td>
                    <td className="px-2.5 py-1.5 text-muted whitespace-nowrap tnum">{o.date ? fmtDate(o.date, "d MMM yyyy") : "—"}</td>
                    <td className="px-2.5 py-1.5 text-muted whitespace-nowrap">{o.source === "buildertrend" ? "Buildertrend" : o.by_name || "RKB"}</td>
                    <td className="pr-3 text-right">{o.source === "entry" && s.can.lines && <button type="button" onClick={() => removeEntry(o)} title="Remove this payment" className="text-faint hover:text-critical"><Trash2 size={13} /></button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {outList.length > outShown.length && <button type="button" onClick={() => setOutAll(true)} className="w-full py-2 text-[12.5px] font-medium text-accent hover:bg-accent-soft/40 border-t border-line">Show all {outList.length}</button>}
          </div>
        )}
      </Card>

      {/* ───────── the record underneath ───────── */}
      <details className="group rounded-xl border border-line bg-elev">
        <summary className="px-4 py-2.5 cursor-pointer select-none list-none flex items-center gap-2 text-[13px] font-semibold"><ChevronDown size={14} className="text-muted transition -rotate-90 group-open:rotate-0" />Bills in Buildertrend <span className="font-normal text-muted">{s.bills.length}</span></summary>
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full text-[12.5px]">
            <thead><tr className="text-[10px] uppercase tracking-[0.08em] text-faint border-b border-line text-left">
              <th className="pl-4 pr-2 py-2 font-semibold">Bill date</th><th className="px-2.5 py-2 font-semibold">Bill</th><th className={TH}>Amount</th><th className="px-2.5 py-2 font-semibold">Buildertrend</th>
              <th className="px-2.5 py-2 font-semibold">Manju Sir</th><th className="px-2.5 py-2 font-semibold">Ready to pay</th><th className="px-2.5 py-2 font-semibold">Paid</th>
            </tr></thead>
            <tbody>
              {[...s.bills].reverse().map((b) => {
                const tick = (k: string) => b.steps?.[k]?.at ? <span className="text-good" title={b.steps[k]?.by_name || undefined}>{fmtDate(b.steps[k]!.at, "d MMM")}</span> : <span className="text-faint">—</span>;
                return (
                  <tr key={b.bill_id} className="border-b border-line/50">
                    <td className="pl-4 pr-2 py-1.5 whitespace-nowrap tnum text-muted">{fmtDate(b.date, "d MMM yyyy")}</td>
                    <td className="px-2.5 py-1.5">{b.title}{b.holdback && <span className="ml-2 rounded bg-sunken text-muted text-[9.5px] font-semibold px-1 py-px">HOLD-BACK</span>}</td>
                    <td className={cn(TD, "tnum")}>{usd(b.amount)}</td>
                    <td className={cn("px-2.5 py-1.5 whitespace-nowrap", b.status === "In review" ? "text-sky-600" : b.status === "Paid" ? "text-good" : "text-high")}>{b.status}</td>
                    <td className="px-2.5 py-1.5">{tick("manju_approved")}</td><td className="px-2.5 py-1.5">{tick("ready_to_pay")}</td><td className="px-2.5 py-1.5">{tick("paid")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </details>

      {s.history.length > 0 && (
        <details className="group rounded-xl border border-line bg-elev">
          <summary className="px-4 py-2.5 cursor-pointer select-none list-none flex items-center gap-2 text-[13px] font-semibold"><ChevronDown size={14} className="text-muted transition -rotate-90 group-open:rotate-0" />Written by people <span className="font-normal text-muted">{s.history.length}</span></summary>
          <ul className="px-4 py-2.5 border-t border-line space-y-1 text-[12.5px] text-muted">
            {s.history.map((h, i) => (
              <li key={i}><span className="text-fg font-medium">{h.by_name}</span>{" "}
                {h.what === "figure" ? <>set {h.label} to {h.value == null ? "the system reading" : String(h.value).slice(0, 10)}</>
                  : h.what === "payment" ? <>recorded {h.category}: {h.label} — {usd(Number(h.value))}{h.paid ? " (paid)" : ""}</>
                  : h.what === "payment_removed" ? <>removed the payment “{h.label}”</>
                  : <>wrote on a line{typeof h.approved === "number" && <> · approved {usd(h.approved)}</>}{typeof h.paid === "number" && <> · paid {usd(h.paid)}</>}{h.work_done != null && <> · work done {h.work_done}%</>}{h.category ? <> · named it {h.category}</> : null}{h.note ? <> · “{h.note}”</> : null}</>}
                <span className="text-faint"> · {ago(h.at)}</span></li>
            ))}
          </ul>
        </details>
      )}
    </TabBody>
  );
}
