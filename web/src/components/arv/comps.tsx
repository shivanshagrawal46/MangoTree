"use client";

/* The comparable sales: map and grid side by side. Toggle a comp and the ARV
   re-renders; expand a row to see every adjustment line with its rate and
   reason; set a comp's condition when you know it from the photos; add one by
   hand; widen or tighten the search. The grid is an Ant Design table with
   expandable rows. */
import * as React from "react";
import dynamic from "next/dynamic";
import { Select, Switch, Table, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { AlertTriangle, ExternalLink, Plus, Trash2, Lock } from "lucide-react";
import { toast } from "sonner";
import { Button, Card, Dialog, DialogContent, Input } from "@/components/arv/ui";
import { cn, money, fmtDate } from "@/lib/utils";
import { arvApi, SOURCE_LABEL, num, pct, signed, type AnalysisDoc, type Comp } from "@/lib/arv";
import { CONDITION_OPTIONS, H, NumField, Pill, SelectField } from "./bits";

const CompMap = dynamic(() => import("./comp-map"), { ssr: false, loading: () => <div className="h-[420px] rounded-xl bg-sunken animate-pulse" /> });

export type TabProps = { doc: AnalysisDoc; patch: (body: Parameters<typeof arvApi.patch>[1]) => Promise<void>; setDoc: (d: AnalysisDoc) => void; saving: boolean; running: boolean };
type Note = NonNullable<AnalysisDoc["analysis"]>["comp_notes"][number];

export function CompsTab({ doc, patch, setDoc, saving, running }: TabProps) {
  const [hover, setHover] = React.useState<string | null>(null);
  const [open, setOpen] = React.useState<string[]>([]);
  const [showAll, setShowAll] = React.useState(false);
  const [adding, setAdding] = React.useState(false);
  const comps = doc.comps || [];
  const used = comps.filter((c) => c.selected);
  const rest = comps.filter((c) => !c.selected);
  const v = doc.valuation;
  const busy = saving || running;
  const toggle = (c: Comp) => patch({ comps: [{ comp_id: c.comp_id, included: c.selected ? false : true }] });
  const setCond = (c: Comp, cond: string) => patch({ comps: [{ comp_id: c.comp_id, condition: (cond || null) as any }] });
  const remove = async (c: Comp) => { try { setDoc(await arvApi.deleteComp(doc.analysis_id, c.comp_id)); } catch (e: any) { toast.error(e.message); } };
  const s = doc.settings || {};
  const notes = Object.fromEntries((doc.analysis?.comp_notes || []).map((n) => [n.comp_id, n])) as Record<string, Note>;

  const columns: ColumnsType<Comp> = [
    { key: "use", width: 56, render: (_, c) => (
      <Tooltip title={c.selected ? "Exclude this sale" : "Include this sale (forces it in even if it fails a filter)"}>
        <span onClick={(e) => e.stopPropagation()}><Switch size="small" checked={!!c.selected} disabled={busy} onChange={() => toggle(c)} /></span>
      </Tooltip>
    ) },
    { title: "Address", key: "address", render: (_, c) => {
      const a = c.adjustment; const n = notes[c.comp_id];
      return (
        <div className="min-w-[220px]">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="truncate font-medium">{c.address || "—"}</span>
            {c.url && <a href={c.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-faint hover:text-accent shrink-0"><ExternalLink size={11} /></a>}
          </div>
          <div className="flex flex-wrap items-center gap-1 mt-0.5">
            <Pill tone={c.source === "web" ? "high" : c.source === "manual" || c.source === "document" ? "info" : "neutral"}>{SOURCE_LABEL[c.source] || c.source}</Pill>
            {c.price_basis !== "closed" && <Pill tone="high">{c.price_basis} price</Pill>}
            {c.forced && <Pill tone="critical"><Lock size={9} /> forced in</Pill>}
            {n && <Pill tone={n.verdict === "drop" ? "critical" : n.verdict === "caution" ? "high" : "good"}>analyst: {n.verdict}</Pill>}
            {a?.flags?.map((f, i) => <Pill key={i} tone="high">{f}</Pill>)}
          </div>
        </div>
      );
    } },
    { title: "Dist", key: "dist", align: "right", width: 64, sorter: (a, b) => (a.distance_mi ?? 99) - (b.distance_mi ?? 99), render: (_, c) => <span className="tnum">{c.distance_mi ?? "—"}</span> },
    { title: "Sold", key: "sold", width: 76, sorter: (a, b) => String(a.sale_date || "").localeCompare(String(b.sale_date || "")), render: (_, c) => <span className="tnum whitespace-nowrap">{c.sale_date ? fmtDate(c.sale_date, "MMM yy") : "—"}</span> },
    { title: "Price", key: "price", align: "right", width: 96, sorter: (a, b) => (a.sale_price || 0) - (b.sale_price || 0), render: (_, c) => <span className="tnum font-medium">{money(c.sale_price)}</span> },
    { title: "Sqft", key: "sqft", align: "right", width: 70, sorter: (a, b) => (a.sqft || 0) - (b.sqft || 0), render: (_, c) => <span className="tnum">{num(c.sqft)}</span> },
    { title: "$/sqft", key: "ppsf", align: "right", width: 70, render: (_, c) => <span className="tnum">{c.sqft && c.sale_price ? num(c.sale_price / c.sqft) : "—"}</span> },
    { title: "Bd/Ba", key: "bdba", align: "right", width: 64, render: (_, c) => <span className="tnum">{c.beds ?? "—"}/{c.baths ?? "—"}</span> },
    { title: "Built", key: "built", align: "right", width: 60, render: (_, c) => <span className="tnum">{c.year_built ?? "—"}</span> },
    { title: "Cond.", key: "cond", width: 88, render: (_, c) => (
      <span onClick={(e) => e.stopPropagation()}>
        <Select size="small" value={c.condition || ""} disabled={busy} onChange={(val) => setCond(c, val)} popupMatchSelectWidth={false} className="!w-[72px]"
          options={[{ value: "", label: "C3?" }, ...CONDITION_OPTIONS.map(([val]) => ({ value: val, label: val }))]} />
      </span>
    ) },
    { title: "Fit", key: "fit", align: "right", width: 54, sorter: (a, b) => (a.similarity || 0) - (b.similarity || 0), render: (_, c) => <span className="tnum">{c.similarity ?? "—"}</span> },
    { title: "Net adj", key: "net", align: "right", width: 120, render: (_, c) => { const a = c.adjustment; return <span className={cn("tnum", a && Math.abs(a.net_pct) > 15 && "text-high")}>{a ? `${signed(a.net)} (${a.net_pct}%)` : "—"}</span>; } },
    { title: "Adjusted", key: "adj", align: "right", width: 100, sorter: (a, b) => (a.adjustment?.adjusted_price || 0) - (b.adjustment?.adjusted_price || 0), render: (_, c) => <span className="tnum font-semibold">{c.adjustment ? money(c.adjustment.adjusted_price) : "—"}</span> },
    { title: "Weight", key: "w", align: "right", width: 70, sorter: (a, b) => (a.weight || 0) - (b.weight || 0), render: (_, c) => <span className="tnum">{c.weight !== undefined && c.selected ? pct(c.weight, 0) : "—"}</span> },
  ];

  const table = (rows: Comp[], dim?: boolean) => (
    <Table<Comp> rowKey="comp_id" columns={columns} dataSource={rows} pagination={false} size="small" scroll={{ x: 1180 }}
      expandable={{ expandedRowKeys: open, onExpandedRowsChange: (k) => setOpen(k as string[]), expandRowByClick: true, showExpandColumn: false,
        expandedRowRender: (c) => <CompDetail c={c} note={notes[c.comp_id]} onRemove={() => remove(c)} busy={busy} /> }}
      onRow={(c) => ({ onMouseEnter: () => setHover(c.comp_id), onMouseLeave: () => setHover(null), className: cn("cursor-pointer", hover === c.comp_id && "arv-row-hover", dim && "opacity-70") })} />
  );

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[1fr_1.35fr]">
        <Card className="p-3">
          <CompMap subject={{ lat: doc.geocode?.lat ?? doc.subject.lat, lon: doc.geocode?.lon ?? doc.subject.lon, address: doc.geocode?.formatted || doc.subject.address }} comps={comps} hovered={hover} onHover={setHover} onSelect={(id) => setOpen((o) => (o.includes(id) ? o : [...o, id]))} height={440} />
          <div className="flex flex-wrap items-center gap-3 mt-2.5 text-[11px] text-muted">
            <span className="flex items-center gap-1"><span className="h-3 w-3 rounded-full" style={{ background: "#1d4ed8" }} /> used (data feed / manual)</span>
            <span className="flex items-center gap-1"><span className="h-3 w-3 rounded-full" style={{ background: "#b45309" }} /> used (web search)</span>
            <span className="flex items-center gap-1"><span className="h-3 w-3 rounded-full bg-faint/70" /> not used</span>
            <span className="flex items-center gap-1"><span className="h-3 w-3 rounded-full border border-accent border-dashed" /> ½ and 1 mile</span>
          </div>
        </Card>
        <Card className="p-4 space-y-3">
          <H right={<Button size="sm" onClick={() => setAdding(true)}><Plus size={13} /> Add a comp</Button>}>
            Search · {v?.selection ? `${v.selection.radius_used} mi · ${v.selection.months_used} months · ${v.selection.qualified} of ${v.selection.candidates} qualified` : "—"}
          </H>
          <div className="grid grid-cols-3 gap-2">
            <NumField label="Radius" value={s.radius_mi ?? 1} onCommit={(x) => patch({ settings: { radius_mi: x } })} suffix="mi" step={0.25} compact />
            <NumField label="Sold within" value={s.months ?? 12} onCommit={(x) => patch({ settings: { months: x } })} suffix="mo" compact />
            <NumField label="Comps to use" value={s.target ?? 6} onCommit={(x) => patch({ settings: { target: x } })} compact />
          </div>
          {v?.selection?.notes?.length ? <div className="text-[11.5px] text-high flex items-start gap-1.5"><AlertTriangle size={13} className="mt-0.5 shrink-0" /> {v.selection.notes.join(" ")}</div> : null}
          {v?.rates && (
            <div className="text-[11px] text-muted leading-relaxed">
              <b className="text-fg">Rates in use.</b> Time {v.rates.market_monthly_pct}%/mo ({v.rates.market_basis}). Living area ${v.rates.gla_per_sqft}/sqft ({v.rates.gla_basis}). Bedroom ${num(v.rates.bedroom)}, full bath ${num(v.rates.bath_full)}, half ${num(v.rates.bath_half)}, garage ${num(v.rates.garage_space)}/space, finished basement ${num(v.rates.basement_finished)}, pool ${num(v.rates.pool)}, lot ${v.rates.lot_per_sqft}/sqft, age {v.rates.age_per_year_pct}%/yr, condition {v.rates.condition_step_pct}% per C step. Median sale ${num(v.rates.median_sale_price)} at ${v.rates.median_ppsf}/sqft.
            </div>
          )}
          {doc.avm && <div className="text-[11px] text-muted"><b className="text-fg">{doc.avm.source}</b> {money(doc.avm.value)} {doc.avm.low && doc.avm.high ? `(${money(doc.avm.low, true)} – ${money(doc.avm.high, true)})` : ""} · {v?.reconciliation?.avm_note}</div>}
        </Card>
      </div>

      <Card className="overflow-hidden">
        <div className="px-4 pt-3 pb-2 flex items-center justify-between">
          <div className="text-[13px] font-semibold tracking-tight">Comparable sales used · {used.length}</div>
          <div className="text-[11px] text-muted">{saving ? "recomputing…" : "switch to include or exclude · click a row for the adjustment grid"}</div>
        </div>
        {table(used)}
        {rest.length > 0 && (
          <div className="border-t border-line">
            <button onClick={() => setShowAll((x) => !x)} className="w-full text-left px-4 py-2.5 text-xs text-muted hover:bg-sunken flex items-center gap-2">
              {showAll ? "▾" : "▸"} {rest.length} more candidate{rest.length === 1 ? "" : "s"} not used — distance, age of sale, size or type ruled them out; include one to force it in
            </button>
            {showAll && table(rest, true)}
          </div>
        )}
        {comps.length === 0 && <div className="px-4 pb-6 pt-2 text-sm text-muted">No comparable sales yet. {running ? "The search is still running." : "Add comps by hand or re-run the comps stage."}</div>}
      </Card>
      <AddComp open={adding} onOpenChange={setAdding} doc={doc} setDoc={setDoc} />
    </div>
  );
}

function CompDetail({ c, note, onRemove, busy }: { c: Comp; note?: Note; onRemove: () => void; busy: boolean }) {
  const a = c.adjustment;
  return (
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr] text-[12px] px-2 py-1">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1.5">Adjustments to the subject as repaired</div>
        {a ? (
          <Table size="small" pagination={false} rowKey={(r: any) => r.key}
            dataSource={[{ key: "_sale", label: "Sale price", amount: null, why: c.price_basis !== "closed" ? `${c.price_basis} price — verify the closed figure` : c.months_since_sale != null ? `${Math.round(c.months_since_sale)} months ago` : "", value: money(c.sale_price) },
              ...a.lines.map((l) => ({ key: l.key, label: l.label, amount: l.amount, why: l.why, value: signed(l.amount) })),
              { key: "_net", label: "Net / gross", amount: null, why: `${a.net_pct}% net · ${a.gross_pct}% gross${a.flags.length ? " · " + a.flags.join("; ") : ""}`, value: `${signed(a.net)} · ${money(a.gross)}`, strong: true },
              { key: "_adj", label: "Adjusted price", amount: null, why: a.adjusted_ppsf ? `$${num(a.adjusted_ppsf)}/sqft` : "", value: money(a.adjusted_price), strong: true }]}
            columns={[{ title: "Line", dataIndex: "label", render: (t: string, r: any) => <span className={r.strong ? "font-semibold" : ""}>{t}</span> },
              { title: "Amount", dataIndex: "value", align: "right", width: 140, render: (t: string, r: any) => <span className={cn("tnum", r.strong && "font-semibold", r.amount != null && (r.amount > 0 ? "text-good" : r.amount < 0 ? "text-critical" : ""))}>{t}</span> },
              { title: "Why", dataIndex: "why", render: (t: string) => <span className="text-muted">{t}</span> }]} />
        ) : (
          <div className="text-muted">Not used in the value{c.fails?.length ? `: ${c.fails.join("; ")}` : ""}. Include it to see its adjustment grid.</div>
        )}
      </div>
      <div className="space-y-2">
        {c.deductions?.length ? <div><div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1">Why the fit is {c.similarity}/100</div><ul className="space-y-0.5 text-muted">{c.deductions.map((d, i) => <li key={i}>−{d.points} {d.reason}</li>)}</ul></div> : null}
        {note && <div className="rounded-lg border border-line bg-elev p-2.5"><div className="text-[11px] uppercase tracking-wide text-muted font-medium mb-1">Analyst</div><div>{note.note}</div>{note.suggested_adjustments?.length ? <ul className="mt-1 text-muted">{note.suggested_adjustments.map((sa, i) => <li key={i}>• {sa.label}: {signed(sa.amount)} — {sa.why}</li>)}</ul> : null}</div>}
        {c.note && <div className="text-muted">{c.note}</div>}
        <div className="flex items-center gap-2 text-[11px] text-faint">
          {c.lot_sqft ? <span>lot {num(c.lot_sqft)} sqft</span> : null}{c.garage_spaces !== null && c.garage_spaces !== undefined ? <span>· garage {c.garage_spaces}</span> : null}{c.basement && c.basement !== "unknown" ? <span>· basement {c.basement}</span> : null}{c.dom ? <span>· {c.dom} DOM</span> : null}
          {(c.source === "manual" || c.source === "document") && <button disabled={busy} onClick={onRemove} className="ml-auto flex items-center gap-1 text-critical hover:underline"><Trash2 size={11} /> remove</button>}
        </div>
      </div>
    </div>
  );
}

function AddComp({ open, onOpenChange, doc, setDoc }: { open: boolean; onOpenChange: (v: boolean) => void; doc: AnalysisDoc; setDoc: (d: AnalysisDoc) => void }) {
  const [f, setF] = React.useState<Partial<Comp>>({ price_basis: "closed", property_type: doc.subject.property_type });
  const [busy, setBusy] = React.useState(false);
  const set = (k: keyof Comp, v: any) => setF((st) => ({ ...st, [k]: v }));
  const submit = async () => {
    if (!f.address || !f.sale_price) { toast.error("Address and sale price are needed"); return; }
    setBusy(true);
    try { setDoc(await arvApi.addComp(doc.analysis_id, f)); onOpenChange(false); setF({ price_basis: "closed", property_type: doc.subject.property_type }); toast.success("Comp added and the value recomputed"); }
    catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Add a comparable sale" description="A sale you know from the MLS, a realtor's CMA or the county record. It is geocoded and scored like the others.">
        <div className="grid grid-cols-2 gap-3">
          <label className="col-span-2 block"><span className="block text-[12.5px] font-medium text-fg/85 mb-1">Address</span><Input value={f.address || ""} onChange={(e) => set("address", e.target.value)} placeholder="1234 Street NW, Washington, DC 20011" /></label>
          <NumField label="Sale price" value={f.sale_price ?? null} onCommit={(v) => set("sale_price", v)} prefix="$" />
          <label className="block"><span className="block text-[12.5px] font-medium text-fg/85 mb-1">Sale date</span><Input type="date" value={f.sale_date || ""} onChange={(e) => set("sale_date", e.target.value)} /></label>
          <NumField label="Beds" value={f.beds ?? null} onCommit={(v) => set("beds", v)} />
          <NumField label="Baths" value={f.baths ?? null} onCommit={(v) => set("baths", v)} step={0.5} />
          <NumField label="Living area" value={f.sqft ?? null} onCommit={(v) => set("sqft", v)} suffix="sqft" />
          <NumField label="Lot" value={f.lot_sqft ?? null} onCommit={(v) => set("lot_sqft", v)} suffix="sqft" />
          <NumField label="Year built" value={f.year_built ?? null} onCommit={(v) => set("year_built", v)} />
          <SelectField label="Condition" value={f.condition || ""} onChange={(v) => set("condition", v || null)} options={[["", "Unknown (C3 assumed)"], ...CONDITION_OPTIONS]} />
          <SelectField label="Price basis" value={f.price_basis || "closed"} onChange={(v) => set("price_basis", v)} options={[["closed", "Closed sale"], ["listed", "Listing / asking price"], ["estimated", "Estimate"]]} />
          <label className="block"><span className="block text-[12.5px] font-medium text-fg/85 mb-1">Source URL</span><Input value={f.url || ""} onChange={(e) => set("url", e.target.value)} placeholder="https://…" /></label>
          <label className="col-span-2 block"><span className="block text-[12.5px] font-medium text-fg/85 mb-1">Note</span><Input value={f.note || ""} onChange={(e) => set("note", e.target.value)} placeholder="renovated 2025, sold off-market, realtor CMA…" /></label>
        </div>
        <div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={submit}><Plus size={14} /> Add and recompute</Button></div>
      </DialogContent>
    </Dialog>
  );
}
