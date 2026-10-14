"use client";

/* Documents for an ARV file: the drop zone that uploads them (antd Upload),
   the job that reads each one (Claude Opus 5.5), and the card that shows what
   was read — facts about the house, dollar figures, priced repair items,
   defects, the comps in an appraisal grid. Shared by the intake wizard
   (documents are staged before the analysis exists) and the workspace. */
import * as React from "react";
import { Select, Table, Upload } from "antd";
import { AlertTriangle, Camera, Check, ChevronDown, ChevronUp, ExternalLink, FileText, Loader2, Plus, Trash2, Upload as UploadIcon } from "lucide-react";
import { toast } from "sonner";
import { cn, money } from "@/lib/utils";
import { arvApi, subscribeArvJob, FACT_LABEL, fmtFact, type ArvDocument } from "@/lib/arv";
import { fileSize, Pill } from "./bits";

const ACCEPT = ".pdf,.docx,.doc,.xlsx,.xlsm,.xls,.csv,.txt,.md,.jpg,.jpeg,.png,.webp,.heic,.heif,.gif";

export const DOC_HINTS: [string, string][] = [["", "Let the model decide"], ["inspection", "Inspection report"], ["appraisal", "Appraisal"], ["contractor_bid", "Contractor bid / scope"],
  ["listing", "Listing sheet"], ["photos", "Photos"], ["title", "Title / survey"], ["tax", "Tax bill"], ["purchase_contract", "Purchase contract"], ["bpo", "BPO / CMA"], ["other", "Other"]];

export const TYPE_TONE: Record<string, "accent" | "high" | "good" | "info" | "critical" | "neutral"> = { inspection: "high", appraisal: "accent", contractor_bid: "good", listing: "info", photos: "info", title: "neutral", tax: "neutral", bpo: "accent", purchase_contract: "neutral", permit: "critical" };
const SEV_TONE: Record<string, "critical" | "high" | "neutral" | "info"> = { critical: "critical", high: "high", watch: "neutral", info: "info" };

/* Upload + follow the reading job. `onDocument` fires for every record change
   (first the placeholder, later the read); `onSettled` when the job ends. */
export function useDocUploader(opts: { analysisId?: string; address?: string; onDocument: (d: ArvDocument) => void; onSettled?: () => void }) {
  const [busy, setBusy] = React.useState(false);
  const [status, setStatus] = React.useState("");
  const { analysisId, address, onDocument, onSettled } = opts;
  const send = async (files: File[], hint?: string) => {
    if (!files.length || busy) return;
    setBusy(true);
    setStatus(`Uploading ${files.length} file${files.length > 1 ? "s" : ""}…`);
    try {
      const out = await arvApi.uploadDocuments(files, { analysisId, hint: hint || undefined, address });
      out.documents.forEach(onDocument);
      setStatus("Stored. Reading…");
      await new Promise<void>((resolve) => subscribeArvJob(out.job_id, (e) => {
        if (e.kind === "status" && e.data?.text) setStatus(e.data.text);
        if (e.kind === "partial" && e.data?.key === "document") onDocument(e.data.value);
        if (e.kind === "error") toast.error(e.data?.error || "Reading failed");
      }, resolve));
      setStatus("");
      onSettled?.();
    } catch (e: any) {
      toast.error(e?.message || "Upload failed");
      setStatus("");
    } finally {
      setBusy(false);
    }
  };
  return { send, busy, status };
}

export function DropZone({ onFiles, busy, status, compact, className }: { onFiles: (files: File[], hint: string) => void; busy?: boolean; status?: string; compact?: boolean; className?: string }) {
  const [hint, setHint] = React.useState("");
  return (
    <div className={className}>
      <Upload.Dragger multiple accept={ACCEPT} showUploadList={false} disabled={busy} fileList={[]}
        beforeUpload={(file, list) => {
          // called once per file with the whole batch: send the batch on its first file
          if (file === list[0]) onFiles(list as unknown as File[], hint);
          return false;
        }}
        className={cn("arv-dragger", compact && "arv-dragger-compact")}>
        <div className={cn("text-center", compact ? "py-1" : "py-4")}>
          <div className={cn("mx-auto rounded-2xl grid place-items-center bg-accent-soft text-accent", compact ? "h-9 w-9" : "h-12 w-12")}>{busy ? <Loader2 size={compact ? 16 : 20} className="animate-spin" /> : <UploadIcon size={compact ? 16 : 20} />}</div>
          <div className={cn("font-semibold mt-3", compact ? "text-[13px]" : "text-[15px]")}>{busy ? status || "Working…" : "Drop documents here, or click to choose"}</div>
          {!compact && <div className="text-xs text-muted mt-1 max-w-md mx-auto">Inspection reports, appraisals, contractor bids, listing sheets, photos, tax bills, title work, purchase contracts. PDF, Word, Excel, images. Each file is read once by AI and its facts are offered to you, never applied silently.</div>}
          {busy && status && compact && <div className="text-[11px] text-accent mt-1">{status}</div>}
        </div>
      </Upload.Dragger>
      <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-muted">
        <span className="flex items-center gap-2">What are these? <Select size="small" value={hint} onChange={setHint} options={DOC_HINTS.map(([v, l]) => ({ value: v, label: l }))} className="!w-48" popupMatchSelectWidth={false} /></span>
        <span className="text-faint">Up to 40 MB a file · duplicates are stored once</span>
      </div>
    </div>
  );
}

/* One document: header line always; details on demand. The action callbacks
   are optional so the card works both before and after an analysis exists. */
export function DocCard({ d, onApplyFacts, onApplyRehab, onApplyComps, onRemove, appliedFacts, isBudget, defaultOpen, className }: {
  d: ArvDocument; onApplyFacts?: (keys: string[]) => void; onApplyRehab?: () => void; onApplyComps?: () => void; onRemove?: () => void;
  appliedFacts?: Record<string, string>; isBudget?: boolean; defaultOpen?: boolean; className?: string;
}) {
  const [open, setOpen] = React.useState(!!defaultOpen);
  const ex = d.extracted;
  const facts = Object.entries(ex?.subject_facts || {});
  const isPhoto = ex?.doc_type === "photos" || /\.(jpe?g|png|webp|heic|heif|gif)$/i.test(d.filename);
  const crit = (ex?.findings || []).filter((f) => f.severity === "critical").length;
  const high = (ex?.findings || []).filter((f) => f.severity === "high").length;
  return (
    <div className={cn("rounded-xl border bg-elev transition", d.status === "failed" ? "border-critical/40" : d.status === "reading" ? "border-accent/40" : "border-line", className)}>
      <div className="flex items-start gap-3 p-3.5">
        <div className={cn("h-9 w-9 rounded-xl grid place-items-center shrink-0", d.status === "failed" ? "bg-critical-soft text-critical" : "bg-sunken text-muted")}>
          {d.status === "reading" ? <Loader2 size={16} className="animate-spin text-accent" /> : isPhoto ? <Camera size={16} /> : <FileText size={16} />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[13px] font-semibold truncate max-w-[60%]" title={d.filename}>{ex?.title || d.filename}</span>
            {ex?.doc_type && <Pill tone={TYPE_TONE[ex.doc_type] || "neutral"}>{ex.doc_type_label || ex.doc_type}</Pill>}
            {d.status === "reading" && <Pill tone="accent">reading…</Pill>}
            {d.status === "failed" && <Pill tone="critical">failed</Pill>}
            {ex?.legibility && ex.legibility !== "clear" && <Pill tone="high">{ex.legibility === "partial" ? "partly legible" : "poorly legible"}</Pill>}
          </div>
          <div className="text-[11px] text-muted mt-0.5 flex flex-wrap gap-x-2">
            <span>{d.filename} · {fileSize(d.size)}{d.pages ? ` · ${d.pages} page${d.pages > 1 ? "s" : ""}` : ""}{d.vision_pages ? ` · ${d.vision_pages} read as images` : ""}</span>
            {ex?.document_date && <span>· dated {ex.document_date}</span>}
            {ex?.prepared_by && <span>· {ex.prepared_by}</span>}
          </div>
          {d.status === "failed" && <div className="text-[12px] text-critical mt-1.5 flex items-center gap-1.5"><AlertTriangle size={12} /> {d.error}</div>}
          {ex && <div className="text-[12.5px] mt-1.5 leading-snug">{ex.summary}</div>}
          {ex && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {facts.length > 0 && <Pill tone="accent">{facts.length} fact{facts.length > 1 ? "s" : ""} about the house</Pill>}
              {ex.values.length > 0 && <Pill tone="info">{ex.values.length} dollar figure{ex.values.length > 1 ? "s" : ""}</Pill>}
              {ex.rehab_items.length > 0 && <Pill tone="good">{ex.rehab_items.length} priced items · {money(ex.rehab_total, true)}</Pill>}
              {ex.findings.length > 0 && <Pill tone={crit ? "critical" : high ? "high" : "neutral"}>{ex.findings.length} finding{ex.findings.length > 1 ? "s" : ""}{crit ? ` · ${crit} critical` : high ? ` · ${high} high` : ""}</Pill>}
              {ex.comps.length > 0 && <Pill tone="accent">{ex.comps.length} comparable sale{ex.comps.length > 1 ? "s" : ""}</Pill>}
              {(ex.photo_notes?.length || 0) > 0 && <Pill tone="info">{ex.photo_notes!.length} area{ex.photo_notes!.length > 1 ? "s" : ""} seen</Pill>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {d.sha256 && <a href={arvApi.docFileUrl(d.doc_id)} target="_blank" rel="noreferrer" className="h-7 w-7 grid place-items-center rounded-lg text-muted hover:bg-sunken hover:text-fg" title="Open the file"><ExternalLink size={13} /></a>}
          {onRemove && <button onClick={onRemove} className="h-7 w-7 grid place-items-center rounded-lg text-muted hover:bg-critical-soft hover:text-critical" title="Remove"><Trash2 size={13} /></button>}
          {ex && <button onClick={() => setOpen((o) => !o)} className="h-7 w-7 grid place-items-center rounded-lg text-muted hover:bg-sunken hover:text-fg">{open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}</button>}
        </div>
      </div>
      {open && ex && (
        <div className="border-t border-line px-4 py-3 grid gap-4 md:grid-cols-2 text-[12.5px]">
          {facts.length > 0 && (
            <section>
              <div className="flex items-center justify-between mb-1.5"><div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">Facts about the house</div>
                {onApplyFacts && <button onClick={() => onApplyFacts(facts.map(([k]) => k))} className="text-[11px] text-accent hover:underline flex items-center gap-1"><Plus size={11} /> use all</button>}</div>
              <ul className="space-y-1">
                {facts.map(([k, v]) => {
                  const applied = appliedFacts && appliedFacts[k] === d.filename;
                  return (
                    <li key={k} className="flex items-center justify-between gap-2">
                      <span className="text-muted">{FACT_LABEL[k] || k}</span>
                      <span className="flex items-center gap-2"><b className="tnum">{fmtFact(k, v)}</b>
                        {onApplyFacts && (applied ? <span className="text-[10.5px] text-good">in use</span> : <button onClick={() => onApplyFacts([k])} className="text-[10.5px] text-accent hover:underline">use</button>)}</span>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
          {ex.values.length > 0 && (
            <section>
              <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium mb-1.5">Dollar figures in the document</div>
              <ul className="space-y-1">{ex.values.map((v, i) => <li key={i} className="flex items-center justify-between gap-2"><span className="text-muted">{v.label}{v.as_of ? <span className="text-faint"> · {v.as_of}</span> : null}</span><b className="tnum">{money(v.amount)}</b></li>)}</ul>
            </section>
          )}
          {ex.rehab_items.length > 0 && (
            <section className="md:col-span-2">
              <div className="flex items-center justify-between mb-1.5"><div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">Priced work · {money(ex.rehab_total)}</div>
                {isBudget ? <span className="text-[11px] text-accent flex items-center gap-1"><Check size={11} /> the rehab budget follows this bid</span>
                  : onApplyRehab && <button onClick={onApplyRehab} className="text-[11px] text-accent hover:underline flex items-center gap-1"><Plus size={11} /> use as the rehab budget</button>}</div>
              <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={ex.rehab_items}
                columns={[{ title: "Item", dataIndex: "label" }, { title: "Trade", dataIndex: "category", render: (c: string) => c.replace(/_/g, " ") }, { title: "Amount", dataIndex: "amount", align: "right", render: (v: number) => <span className="tnum">{money(v)}</span> }]} />
            </section>
          )}
          {ex.findings.length > 0 && (
            <section className="md:col-span-2">
              <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium mb-1.5">Findings</div>
              <ul className="grid gap-1.5 sm:grid-cols-2">{ex.findings.map((f, i) => <li key={i} className="flex items-start gap-2"><Pill tone={SEV_TONE[f.severity] || "neutral"} className="mt-0.5 shrink-0">{f.severity}</Pill><div><b>{f.title}.</b> <span className="text-muted">{f.detail}</span>{f.cost_hint && <span className="text-faint"> · {f.cost_hint}</span>}</div></li>)}</ul>
            </section>
          )}
          {ex.comps.length > 0 && (
            <section className="md:col-span-2">
              <div className="flex items-center justify-between mb-1.5"><div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">Comparable sales in the document</div>
                {onApplyComps && <button onClick={onApplyComps} className="text-[11px] text-accent hover:underline flex items-center gap-1"><Plus size={11} /> add to the comps grid</button>}</div>
              <Table size="small" pagination={false} rowKey={(_, i) => String(i)} dataSource={ex.comps}
                columns={[{ title: "Address", dataIndex: "address" }, { title: "Sold", dataIndex: "sale_date" },
                  { title: "Size", render: (_: any, c: any) => [c.beds != null ? `${c.beds} bd` : null, c.baths != null ? `${c.baths} ba` : null, c.sqft ? `${c.sqft.toLocaleString()} sf` : null].filter(Boolean).join(" · ") },
                  { title: "Price", dataIndex: "sale_price", align: "right", render: (v: number, c: any) => <span className="tnum"><b>{money(v, true)}</b>{c.adjusted_price ? <span className="text-faint"> → {money(c.adjusted_price, true)}</span> : null}</span> }]} />
            </section>
          )}
          {(ex.photo_notes?.length || 0) > 0 && (
            <section className="md:col-span-2">
              <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium mb-1.5">What the photos show</div>
              <ul className="grid gap-1 sm:grid-cols-2">{ex.photo_notes!.map((p, i) => <li key={i} className="flex items-start gap-2">{p.condition && <Pill className="shrink-0 mt-0.5">{p.condition}</Pill>}<span><b>{p.area}.</b> <span className="text-muted">{p.note}</span></span></li>)}</ul>
            </section>
          )}
          {ex.open_questions.length > 0 && (
            <section className="md:col-span-2">
              <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium mb-1.5">What it leaves open</div>
              <ul className="space-y-0.5 text-muted">{ex.open_questions.map((q, i) => <li key={i}>• {q}</li>)}</ul>
            </section>
          )}
          {ex.subject_address && <div className="md:col-span-2 text-[11px] text-faint">Address in the document: {ex.subject_address}</div>}
          {(d.extract_errors?.length || 0) > 0 && <div className="md:col-span-2 text-[11px] text-high">{d.extract_errors!.join(" · ")}</div>}
        </div>
      )}
    </div>
  );
}

export function NoDocs({ compact }: { compact?: boolean }) {
  return <div className={cn("text-center text-muted", compact ? "text-[12px] py-3" : "text-sm py-6")}>No documents yet. An inspection report or a contractor bid changes the number more than anything typed.</div>;
}

/* A contractor bid's items as rehab lines, summed per category (mirrors documents.rehab_lines_from on the server). */
export function rehabLinesFrom(d: ArvDocument): { key: string; label: string; amount: number }[] {
  const totals: Record<string, number> = {};
  for (const it of d.extracted?.rehab_items || []) { const k = it.category || "other"; totals[k] = (totals[k] || 0) + (Number(it.amount) || 0); }
  return Object.entries(totals).filter(([, v]) => v).map(([k, v]) => ({ key: k, label: k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()), amount: Math.round(v) }));
}
