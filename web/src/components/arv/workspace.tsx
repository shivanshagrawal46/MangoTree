"use client";

/* One analysis, one screen. While the job runs, the stages land one by one
   (geocode, records, market, comps, compute, analyst) and the page fills in
   from the partial events. Afterwards every input is editable and the
   deterministic half recomputes on the server in one round trip. */
import * as React from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Bot, Calculator, CheckCircle2, ChevronRight, Circle, FileDown, FileSpreadsheet, FileText, LayoutGrid, Loader2, MapPin, Pencil, RotateCcw, Trash2, TrendingUp, XCircle } from "lucide-react";
import { toast } from "sonner";
import { Alert, Dropdown, Popconfirm } from "antd";
import { Badge, Button, Card, Skeleton, Dialog, DialogContent, Input, Textarea } from "@/components/arv/ui";
import { cn, money, ago } from "@/lib/utils";
import { arvApi, subscribeArvJob, PROPERTY_TYPE_LABEL, VERDICT_LABEL, VERDICT_TONE, num, type AnalysisDoc, type Phase, type PropertyType } from "@/lib/arv";
import { Gauge, GradeBadge, RangeBar, NumField, SelectField, CONDITION_OPTIONS, TabStrip, Mini } from "./bits";
import { Overview } from "./overview";

const TabLoading = () => <div className="space-y-3"><Skeleton className="h-10 w-64" /><Skeleton className="h-[48vh]" /></div>;
const CompsTab = dynamic(() => import("./comps").then((m) => m.CompsTab), { ssr: false, loading: TabLoading });
const CalculatorTab = dynamic(() => import("./calculator").then((m) => m.CalculatorTab), { ssr: false, loading: TabLoading });
const MarketTab = dynamic(() => import("./market").then((m) => m.MarketTab), { ssr: false, loading: TabLoading });
const AnalystTab = dynamic(() => import("./analyst").then((m) => m.AnalystTab), { ssr: false, loading: TabLoading });
const DocumentsTab = dynamic(() => import("./documents").then((m) => m.DocumentsTab), { ssr: false, loading: TabLoading });

export function Workspace({ id }: { id: string }) {
  const qc = useQueryClient();
  const router = useRouter();
  const q = useQuery({ queryKey: ["arv", id], queryFn: () => arvApi.get(id), staleTime: 5 * 60_000, refetchOnWindowFocus: false });
  const status = useQuery({ queryKey: ["arv-status"], queryFn: arvApi.status, staleTime: 10 * 60_000 });
  const [doc, setDocState] = React.useState<AnalysisDoc | null>(q.data ?? null);
  const [log, setLog] = React.useState<string[]>([]);
  const [saving, setSaving] = React.useState(false);
  const [tab, setTabState] = React.useState("overview");
  const setTab = React.useCallback((k: string) => React.startTransition(() => setTabState(k)), []);
  const [editing, setEditing] = React.useState(false);
  const jobRef = React.useRef<string | null>(null);
  const unsubRef = React.useRef<(() => void) | null>(null);

  // The fetched document becomes the working copy (adjusted during render, not in an effect).
  const [seen, setSeen] = React.useState(q.data);
  if (q.data && q.data !== seen) { setSeen(q.data); setDocState(q.data); }
  const setDoc = (d: AnalysisDoc) => { setDocState(d); qc.setQueryData(["arv", id], d); };

  const running = !!doc && (doc.status === "running" || doc.status === "queued");
  const [slow, setSlow] = React.useState(false);
  React.useEffect(() => { if (doc) return; const t = setTimeout(() => setSlow(true), 6000); return () => clearTimeout(t); }, [doc]);

  // Follow the job that is (or was) running this analysis; reconnecting replays what landed.
  const follow = (jobId: string) => {
    if (jobRef.current === jobId) return;
    unsubRef.current?.();
    jobRef.current = jobId;
    unsubRef.current = subscribeArvJob(jobId, (e) => {
      if (e.kind === "phase") setDocState((d) => d ? { ...d, status: "running", phases: upsertPhase(d.phases || [], e.data) } : d);
      if (e.kind === "status" && e.data?.text) setLog((l) => [...l.slice(-40), e.data.text]);
      if (e.kind === "partial") setDocState((d) => d ? applyPartial(d, e.data.key, e.data.value) : d);
      if (e.kind === "error") { toast.error(e.data?.error || "The analysis failed"); }
    }, async () => {
      jobRef.current = null;
      try { const fresh = await arvApi.get(id); setDoc(fresh); } catch {}
      qc.invalidateQueries({ queryKey: ["arv-list"] });
    });
  };
  const followRef = React.useRef(follow);
  React.useEffect(() => { followRef.current = follow; });
  const jobId = doc?.job_id;
  React.useEffect(() => { if (jobId && running) followRef.current(jobId); }, [jobId, running]);
  React.useEffect(() => () => { unsubRef.current?.(); }, []);
  // Warm the other tabs' code while the person reads the overview, so a tab click is instant.
  React.useEffect(() => {
    const t = setTimeout(() => { void Promise.all([import("./comps"), import("./calculator"), import("./market"), import("./analyst"), import("./documents")]); }, 400);
    return () => clearTimeout(t);
  }, []);

  const patch = async (body: Parameters<typeof arvApi.patch>[1]) => {
    setSaving(true);
    try { setDoc(await arvApi.patch(id, body)); } catch (e: any) { toast.error(e?.message || "Could not save"); throw e; } finally { setSaving(false); }
  };

  const rerun = async (stages: string[]) => {
    try {
      const { job_id } = await arvApi.rerun(id, stages);
      setDocState((d) => d ? { ...d, status: "running", job_id, phases: (d.phases || []).filter((p) => !stages.includes(p.key) && !(stages.includes("comps") && ["avm", "web"].includes(p.key)) && p.key !== "compute" && !(stages.includes("analyst") && p.key === "analyst")) } : d);
      setLog([]);
      follow(job_id);
    } catch (e: any) { toast.error(e?.message || "Could not start"); }
  };
  const remove = async () => {
    try { await arvApi.remove(id); qc.invalidateQueries({ queryKey: ["arv-list"] }); router.push("/arv"); } catch (e: any) { toast.error(e.message); }
  };

  if (q.isError) {
    const status = (q.error as any)?.status;
    return (
      <div className="p-6 max-w-xl">
        <Card className="p-6 border-critical/40">
          <div className="text-[15px] font-semibold tracking-tight">{status === 404 ? "This file no longer exists" : "This file could not be loaded"}</div>
          <div className="text-[12.5px] text-muted mt-1">{status === 404 ? "It was deleted, or the link is wrong." : String((q.error as any)?.message || "")}</div>
          <div className="mt-3 flex gap-2"><Link href="/arv"><Button size="sm" variant="primary"><ArrowLeft size={13} /> Back to ARV</Button></Link>{status !== 404 && <Button size="sm" variant="ghost" onClick={() => q.refetch()}><RotateCcw size={13} /> Try again</Button>}</div>
        </Card>
      </div>
    );
  }
  if (q.isLoading || !doc) return <LoadingFile slow={slow} onRetry={() => q.refetch()} />;

  const s = doc.subject;
  const v = doc.valuation;
  const an = doc.analysis;
  const uw = doc.underwriting;
  const title = doc.geocode?.formatted || s.address;
  const facts = [PROPERTY_TYPE_LABEL[s.property_type as PropertyType], s.beds != null ? `${s.beds}${s.beds_after && s.beds_after !== s.beds ? `→${s.beds_after}` : ""} bd` : null,
    s.baths != null ? `${s.baths}${s.baths_after && s.baths_after !== s.baths ? `→${s.baths_after}` : ""} ba` : null, s.sqft ? `${num(s.sqft)}${s.sqft_after && s.sqft_after !== s.sqft ? `→${num(s.sqft_after)}` : ""} sqft` : null,
    s.lot_sqft ? `${num(s.lot_sqft)} lot` : null, s.year_built ? `built ${s.year_built}` : null, `${s.condition_current || "?"} → ${s.condition_after}`].filter(Boolean);
  const nDocs = (doc.documents || []).length;

  return (
    <div className="min-h-screen">
      <div className="px-6 pt-5 pb-0 border-b border-line bg-elev/60 backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <Link href="/arv" className="text-[11px] text-muted hover:text-fg flex items-center gap-1 mb-1"><ArrowLeft size={11} /> ARV &amp; underwriting</Link>
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-[22px] font-semibold tracking-tight flex items-center gap-2"><span className="h-7 w-7 rounded-lg bg-accent-soft text-accent grid place-items-center"><MapPin size={15} /></span> {title}</h1>
              {doc.label && <Badge tone="accent">{doc.label}</Badge>}
              {doc.property_id && <Link href={`/property/${doc.property_id}`}><Badge tone="info">portfolio property <ChevronRight size={10} /></Badge></Link>}
              {an?.verdict && <Badge tone={VERDICT_TONE[an.verdict] || "neutral"}>{VERDICT_LABEL[an.verdict]}</Badge>}
              {nDocs > 0 && <button onClick={() => setTab("documents")}><Badge tone="info"><FileText size={10} /> {nDocs} document{nDocs > 1 ? "s" : ""}</Badge></button>}
              {running && <Badge tone="high"><Loader2 size={11} className="animate-spin" /> running</Badge>}
              {doc.status === "failed" && <Badge tone="critical">failed</Badge>}
            </div>
            <div className="text-xs text-muted mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
              {facts.map((f, i) => <span key={i}>{i > 0 && <span className="text-faint mr-2">·</span>}{f}</span>)}
              <button onClick={() => setEditing(true)} className="flex items-center gap-1 text-accent hover:underline ml-1"><Pencil size={11} /> edit subject</button>
              <span className="text-faint">· by {doc.created_by_name || doc.created_by} {ago(doc.created_at)}{doc.cost_usd ? ` · AI spend $${doc.cost_usd.toFixed(2)}` : ""}</span>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <a href={arvApi.pdfUrl(id)} target="_blank" rel="noreferrer"><Button size="sm"><FileDown size={13} /> PDF report</Button></a>
            <a href={arvApi.xlsxUrl(id)}><Button size="sm" variant="ghost"><FileSpreadsheet size={13} /> Calculator</Button></a>
            <Dropdown disabled={running} menu={{ items: [
              { key: "both", label: "Re-run comps + review", onClick: () => rerun(["comps", "analyst"]) },
              { key: "analyst", label: "Re-run the review only", onClick: () => rerun(["analyst"]) },
              { key: "comps", label: "Search comps again", onClick: () => rerun(["comps"]) },
              { key: "market", label: "Refresh market data", onClick: () => rerun(["market"]) },
              { key: "all", label: "Run everything again", onClick: () => rerun(["geocode", "records", "market", "comps", "analyst"]) },
            ] }}><Button size="sm" variant="ghost" disabled={running}><RotateCcw size={13} /> Re-run comps + review</Button></Dropdown>
            <Popconfirm title="Delete this file?" description="This cannot be undone." okText="Delete" okButtonProps={{ danger: true }} onConfirm={remove}><Button size="sm" variant="ghost" title="Delete"><Trash2 size={13} /></Button></Popconfirm>
          </div>
        </div>

        {/* -------------------------------------------------------- hero: one scoreboard, four segments */}
        <Card className="mt-4 overflow-hidden">
          <div className="grid md:grid-cols-[1.7fr_0.75fr_1fr_1fr] divide-y md:divide-y-0 md:divide-x divide-line">
            <div className="p-5 min-w-0">
              <div className="flex items-center justify-between gap-2"><div className="text-[11px] uppercase tracking-wide text-muted font-medium">After repair value</div>{v?.confidence && <GradeBadge conf={v.confidence} />}</div>
              <div className="flex items-end gap-3 mt-1.5">
                <div className="text-[38px] leading-none font-semibold tracking-tight tnum">{v?.arv ? money(v.arv) : running ? <span className="text-faint text-xl">computing…</span> : "—"}</div>
                {v?.arv && <div className="text-[11.5px] text-muted pb-1 leading-snug">{money(v.arv_low, true)} – {money(v.arv_high, true)}<br />${num(v.reconciliation?.value_per_sqft, 0)}/sqft · {v.reconciliation?.comps_used} comps</div>}
              </div>
              <RangeBar low={v?.arv_low} high={v?.arv_high} value={v?.arv} compLow={v?.reconciliation?.comp_low} compHigh={v?.reconciliation?.comp_high}
                marks={[{ label: "analyst", value: an?.arv_opinion, tone: "text-high" }, { label: doc.avm?.source?.split(" ")[0] || "AVM", value: doc.avm?.value, tone: "text-info" }]} className="mt-2" />
            </div>
            <div className="p-5 flex flex-col items-center justify-center">
              <Gauge conf={v?.confidence} size={128} />
              <div className="text-[10.5px] text-muted text-center leading-snug">{v?.confidence?.label || "Confidence"}{v?.confidence?.band_pct ? ` · expect ±${v.confidence.band_pct}%` : ""}</div>
            </div>
            <div className="p-5 min-w-0">
              <div className="text-[11px] uppercase tracking-wide text-muted font-medium">Max offer</div>
              <div className="text-[26px] leading-none font-semibold tracking-tight tnum mt-1.5">{money(uw?.mao.recommended)}</div>
              {uw && <div className="mt-3 space-y-1">
                <Mini k={`${num(uw.mao.rule_pct, 0)}% rule`} v={money(uw.mao.rule, true)} />
                <Mini k="Profit target" v={money(uw.mao.profit_target, true)} />
                <Mini k={`Rehab · ${doc.rehab_budget?.tier.label || ""}`} v={money(uw.rehab_total, true)} />
              </div>}
            </div>
            <div className="p-5 min-w-0">
              <div className="text-[11px] uppercase tracking-wide text-muted font-medium">Deal at {money(uw?.deal.purchase_price, true)}</div>
              <div className={cn("text-[26px] leading-none font-semibold tracking-tight tnum mt-1.5", (uw?.deal.profit ?? 0) < 0 ? "text-critical" : "text-good")}>{uw ? money(uw.deal.profit) : "—"}</div>
              {uw && <div className="mt-3 space-y-1">
                <Mini k="Profit · of ARV" v={`${uw.deal.profit_pct_of_arv ?? "—"}%`} tone={(uw.deal.profit ?? 0) < 0 ? "text-critical" : "text-good"} />
                <Mini k="Annualized ROI" v={`${uw.deal.annualized_roi_pct ?? "—"}%`} />
                <Mini k={`Cash in · LTARV ${uw.deal.financing.ltarv_pct}%`} v={money(uw.deal.cash_needed, true)} />
              </div>}
            </div>
          </div>
        </Card>

        <TabStrip value={tab} onChange={setTab} className="mt-4" tabs={[
          { key: "overview", label: "Overview", icon: <LayoutGrid size={14} /> },
          { key: "comps", label: "Comps", icon: <MapPin size={14} />, count: doc.comps?.filter((c) => c.selected).length || 0 },
          { key: "calculator", label: "Rehab & MAO", icon: <Calculator size={14} /> },
          { key: "documents", label: "Documents", icon: <FileText size={14} />, count: nDocs },
          { key: "market", label: "Market", icon: <TrendingUp size={14} /> },
          { key: "analyst", label: an ? "Analyst review" : "Analyst", icon: <Bot size={14} />, busy: running && !an },
        ]} />
      </div>

      <div className="p-6 space-y-4">
        {(running || doc.status === "failed" || (doc.phases || []).some((p) => p.status === "failed")) && <Progress phases={doc.phases || []} log={log} running={running} error={doc.error} />}
        <BudgetGap doc={doc} setDoc={setDoc} setTab={setTab} />
        {tab === "overview" && <Overview doc={doc} setTab={setTab} />}
        {tab === "comps" && <CompsTab doc={doc} patch={patch} setDoc={setDoc} saving={saving} running={running} />}
        {tab === "calculator" && <CalculatorTab doc={doc} patch={patch} setDoc={setDoc} saving={saving} running={running} status={status.data} />}
        {tab === "documents" && <DocumentsTab doc={doc} patch={patch} setDoc={setDoc} saving={saving} running={running} onRerun={rerun} />}
        {tab === "market" && <MarketTab doc={doc} />}
        {tab === "analyst" && <AnalystTab doc={doc} patch={patch} setDoc={setDoc} saving={saving} running={running} onRerun={rerun} />}
      </div>
      <EditSubject key={editing ? "open" : "closed"} open={editing} onOpenChange={setEditing} doc={doc} patch={patch} onRerun={rerun} />
    </div>
  );
}

/* The tier estimate is $/sqft × living area: with no area it is $0, and every number
   downstream (MAO, profit, LTARV) is then wrong. Say so, and offer the bid on file. */
function BudgetGap({ doc, setDoc, setTab }: { doc: AnalysisDoc; setDoc: (d: AnalysisDoc) => void; setTab: (t: string) => void }) {
  const [busy, setBusy] = React.useState(false);
  const total = doc.rehab_budget?.total || 0;
  const mode = doc.rehab?.mode || doc.rehab_budget?.mode || "tier";
  if (total > 0 || doc.status === "running") return null;
  const bids = (doc.documents || []).filter((d) => d.status === "done" && (d.extracted?.rehab_items.length || 0) > 0);
  const useBid = async (docId: string) => {
    setBusy(true);
    try { setDoc(await arvApi.applyDocument(doc.analysis_id, docId, "rehab")); toast.success("Budget now follows the bid; the deal recomputed"); }
    catch (e: any) { toast.error(e?.message || "Could not apply the bid"); } finally { setBusy(false); }
  };
  return (
    <Alert type="warning" showIcon title="The rehab budget is $0, so the maximum offer and profit are overstated"
      description={<div className="space-y-2">
        <div className="text-[12px] text-muted">{mode === "tier" && !(doc.subject.sqft_after || doc.subject.sqft) ? "The tier estimate is a rate per square foot and the living area is unknown." : "No repair cost has been set."}</div>
        <div className="flex flex-wrap gap-2">
          {bids.map((b) => <Button key={b.doc_id} size="sm" variant="primary" disabled={busy} onClick={() => useBid(b.doc_id)}>Use the bid on file · {money(b.extracted?.rehab_total, true)} ({b.filename.length > 28 ? b.filename.slice(0, 26) + "…" : b.filename})</Button>)}
          <Button size="sm" onClick={() => setTab("calculator")}>Enter a budget</Button>
          <Button size="sm" variant="ghost" onClick={() => setTab("documents")}>Add a contractor bid</Button>
        </div>
      </div>} />
  );
}

function LoadingFile({ slow, onRetry }: { slow: boolean; onRetry: () => void }) {
  return (
    <div className="p-6 space-y-3">
      {slow && <Card className="p-4 !border-high/40 flex items-center justify-between gap-3"><div className="text-[12.5px]"><b>Still loading this file.</b> <span className="text-muted">The server has not answered yet.</span></div><Button size="sm" variant="primary" onClick={onRetry}><RotateCcw size={13} /> Try again</Button></Card>}
      <Skeleton className="h-28" /><Skeleton className="h-40" /><Skeleton className="h-[50vh]" />
    </div>
  );
}

function upsertPhase(list: Phase[], p: Phase): Phase[] {
  const i = list.findIndex((x) => x.key === p.key);
  if (i === -1) return [...list, p];
  const next = list.slice(); next[i] = { ...next[i], ...p }; return next;
}

function applyPartial(d: AnalysisDoc, key: string, value: any): AnalysisDoc {
  if (key.startsWith("market_")) return { ...d, market: { ...(d.market || { gaps: [] }), [key.slice(7)]: value } as any };
  if (key === "document") { const list = (d.documents || []).slice(); const i = list.findIndex((x) => x.doc_id === value.doc_id); if (i === -1) list.push(value); else list[i] = { ...list[i], ...value }; return { ...d, documents: list }; }
  if (["geocode", "subject", "records", "market", "avm", "comps", "valuation", "rehab_budget", "underwriting", "analysis"].includes(key)) return { ...d, [key]: value } as AnalysisDoc;
  return d;
}

const PHASE_ORDER = ["geocode", "records", "market", "avm", "web", "compute", "analyst"];

function Progress({ phases, log, running, error }: { phases: Phase[]; log: string[]; running: boolean; error?: string | null }) {
  const sorted = [...phases].sort((a, b) => PHASE_ORDER.indexOf(a.key) - PHASE_ORDER.indexOf(b.key));
  const icon = (p: Phase) => p.status === "running" ? <Loader2 size={14} className="animate-spin text-accent" /> : p.status === "done" ? <CheckCircle2 size={14} className="text-good" /> : p.status === "failed" ? <XCircle size={14} className="text-critical" /> : <Circle size={14} className="text-faint" />;
  return (
    <Card className={cn("p-4", running && "!border-accent/40")}>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] uppercase tracking-wide text-muted font-medium flex items-center gap-2">{running && <Loader2 size={12} className="animate-spin text-accent" />} {running ? "Working" : error ? "Stopped" : "Last run"}</div>
        {log.length > 0 && <div className="text-[11px] text-muted truncate max-w-[60%]">{log[log.length - 1]}</div>}
      </div>
      <ol className="grid gap-x-6 gap-y-2 md:grid-cols-2 xl:grid-cols-3">
        {sorted.map((p) => (
          <li key={p.key} className="flex items-start gap-2 min-w-0">
            <span className="mt-[2px] shrink-0">{icon(p)}</span>
            <div className="min-w-0">
              <div className={cn("text-[12.5px] font-medium leading-snug", p.status === "failed" && "text-critical", p.status === "skipped" && "text-muted")}>{p.label}{p.status === "skipped" && <span className="text-faint font-normal"> · skipped</span>}</div>
              {p.detail && <div className="text-[11px] text-muted truncate" title={p.detail}>{p.detail}</div>}
            </div>
          </li>
        ))}
      </ol>
      {error && <div className="mt-3 text-[12px] text-critical flex items-center gap-1.5"><AlertTriangle size={13} /> {error}</div>}
    </Card>
  );
}

function EditSubject({ open, onOpenChange, doc, patch, onRerun }: { open: boolean; onOpenChange: (v: boolean) => void; doc: AnalysisDoc; patch: (b: any) => Promise<void>; onRerun: (s: string[]) => void }) {
  const s = doc.subject;
  // Remounted by the parent (key) each time it opens, so the draft starts empty.
  const [f, setF] = React.useState<Record<string, unknown>>({});
  const val = (k: string) => (k in f ? f[k] : (s as unknown as Record<string, unknown>)[k]) as any;
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const save = async (rerun: boolean) => {
    if (Object.keys(f).length) await patch({ subject: f });
    onOpenChange(false);
    if (rerun) onRerun(["comps", "analyst"]);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Edit the subject" description="Facts about the house. Saving recomputes the value with the current comps; re-running also searches for comps again and asks the analyst to re-read." wide>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <SelectField label="Type" value={val("property_type")} onChange={(v) => set("property_type", v)} options={Object.entries(PROPERTY_TYPE_LABEL) as [string, string][]} className="col-span-2" />
          <NumField label="Bedrooms" value={val("beds")} onCommit={(v) => set("beds", v)} />
          <NumField label="Bathrooms" value={val("baths")} onCommit={(v) => set("baths", v)} step={0.5} />
          <NumField label="Living area" value={val("sqft")} onCommit={(v) => set("sqft", v)} suffix="sqft" />
          <NumField label="Lot" value={val("lot_sqft")} onCommit={(v) => set("lot_sqft", v)} suffix="sqft" />
          <NumField label="Year built" value={val("year_built")} onCommit={(v) => set("year_built", v)} />
          <NumField label="Garage" value={val("garage_spaces")} onCommit={(v) => set("garage_spaces", v)} suffix="cars" />
          <SelectField label="Basement" value={val("basement") || "unknown"} onChange={(v) => set("basement", v)} options={[["unknown", "Unknown"], ["none", "None"], ["unfinished", "Unfinished"], ["finished", "Finished"]]} />
          <SelectField label="Pool" value={val("pool") === true ? "yes" : val("pool") === false ? "no" : "unknown"} onChange={(v) => set("pool", v === "unknown" ? null : v === "yes")} options={[["unknown", "Unknown"], ["no", "No"], ["yes", "Yes"]]} />
          <SelectField label="Condition today" value={val("condition_current") || ""} onChange={(v) => set("condition_current", v || null)} options={[["", "Not sure"], ...CONDITION_OPTIONS]} />
          <SelectField label="Condition after" value={val("condition_after")} onChange={(v) => set("condition_after", v)} options={CONDITION_OPTIONS.slice(0, 4)} />
          <NumField label="Bedrooms after" value={val("beds_after")} onCommit={(v) => set("beds_after", v)} placeholder="same" />
          <NumField label="Bathrooms after" value={val("baths_after")} onCommit={(v) => set("baths_after", v)} step={0.5} placeholder="same" />
          <NumField label="Living area after" value={val("sqft_after")} onCommit={(v) => set("sqft_after", v)} suffix="sqft" placeholder="same" />
          <SelectField label="Occupancy" value={val("occupancy") || ""} onChange={(v) => set("occupancy", v || null)} options={[["", "Unknown"], ["vacant", "Vacant"], ["owner", "Owner occupied"], ["tenant", "Tenant in place"]]} />
          <NumField label="Roof age" value={val("roof_age_years")} onCommit={(v) => set("roof_age_years", v)} suffix="yrs" />
          <NumField label="HOA" value={val("hoa_monthly")} onCommit={(v) => set("hoa_monthly", v)} prefix="$" suffix="/mo" />
          <label className="col-span-2 md:col-span-4 block"><span className="block text-xs text-muted mb-1">Notes for the analyst</span><Textarea rows={3} value={val("notes") || ""} onChange={(e) => set("notes", e.target.value)} /></label>
          <label className="col-span-2 block"><span className="block text-xs text-muted mb-1">Label</span><Input value={"label" in f ? String(f.label ?? "") : (doc.label || "")} onChange={(e) => set("label", e.target.value)} placeholder="e.g. Offer round 2" /></label>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => save(false)}>Save and recompute</Button>
          <Button variant="primary" onClick={() => save(true)}>Save, re-run comps and review</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
