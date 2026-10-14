"use client";

/* The documents tab of one file: add more, see what each one said, and take
   it into the file — facts onto the subject, a bid as the budget, an
   appraisal's comps onto the grid. Each action recomputes at once; the
   analyst re-reads on request. */
import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Alert } from "antd";
import { Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button, Card } from "@/components/arv/ui";
import { money } from "@/lib/utils";
import { arvApi, type ArvDocument } from "@/lib/arv";
import { Tile } from "./bits";
import { DocCard, DropZone, NoDocs, useDocUploader } from "./documents-drop";
import type { TabProps } from "./comps";

export function DocumentsTab({ doc, setDoc, running, onRerun }: TabProps & { onRerun: (stages: string[]) => void }) {
  const qc = useQueryClient();
  const docs = doc.documents || [];
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [changed, setChanged] = React.useState(false);

  const upsert = (d: ArvDocument) => {
    const list = (doc.documents || []).slice();
    const i = list.findIndex((x) => x.doc_id === d.doc_id);
    if (i === -1) list.push(d); else list[i] = { ...list[i], ...d };
    setDoc({ ...doc, documents: list });
  };
  const uploader = useDocUploader({
    analysisId: doc.analysis_id, onDocument: upsert,
    onSettled: async () => { try { setDoc(await arvApi.get(doc.analysis_id)); } catch {} qc.invalidateQueries({ queryKey: ["arv-list"] }); setChanged(true); },
  });

  const act = async (d: ArvDocument, what: "facts" | "rehab" | "comps", keys?: string[]) => {
    setBusyId(d.doc_id);
    try {
      setDoc(await arvApi.applyDocument(doc.analysis_id, d.doc_id, what, keys));
      setChanged(true);
      toast.success(what === "facts" ? `${keys?.length || "All"} fact${(keys?.length || 2) > 1 ? "s" : ""} taken from ${d.filename}` : what === "rehab" ? `Budget now follows ${d.filename}` : `Comps from ${d.filename} added to the grid`);
    } catch (e: any) { toast.error(e?.message || "Could not apply"); } finally { setBusyId(null); }
  };
  const remove = async (d: ArvDocument) => {
    if (!confirm(`Remove ${d.filename} from this file?`)) return;
    setBusyId(d.doc_id);
    try { setDoc(await arvApi.detachDocument(doc.analysis_id, d.doc_id)); setChanged(true); } catch (e: any) { toast.error(e?.message || "Could not remove"); } finally { setBusyId(null); }
  };

  const read = docs.filter((d) => d.status === "done");
  const findings = read.flatMap((d) => d.extracted?.findings || []);
  const crit = findings.filter((f) => f.severity === "critical").length;
  const high = findings.filter((f) => f.severity === "high").length;
  const bids = read.filter((d) => (d.extracted?.rehab_items.length || 0) > 0);
  const compsAvail = read.reduce((a, d) => a + (d.extracted?.comps.length || 0), 0);
  const factsInUse = Object.keys(doc.subject.fact_sources || {}).length;

  return (
    <div className="space-y-4">
      {docs.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <Tile label="Documents" value={docs.length} sub={`${read.length} read${docs.some((d) => d.status === "reading") ? " · reading…" : ""}`} />
          <Tile label="Findings" value={findings.length} sub={crit ? `${crit} critical · ${high} high` : high ? `${high} high` : "none critical"} tone={crit ? "text-critical" : high ? "text-high" : undefined} />
          <Tile label="Priced scopes" value={bids.length} sub={bids.length ? bids.map((b) => money(b.extracted?.rehab_total, true)).join(" · ") : "no bid yet"} />
          <Tile label="Comps in documents" value={compsAvail} sub="appraisal or CMA grids" />
          <Tile label="Facts in use" value={factsInUse} sub="on the subject, from documents" tone="text-accent" />
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
        <div className="space-y-2 min-w-0">
          {docs.length === 0 ? <Card className="p-6"><NoDocs /></Card> : docs.map((d) => (
            <div key={d.doc_id} className={busyId === d.doc_id ? "opacity-60 pointer-events-none" : undefined}>
              <DocCard d={d} appliedFacts={doc.subject.fact_sources} defaultOpen={docs.length <= 3}
                onApplyFacts={(keys) => act(d, "facts", keys)}
                onApplyRehab={(d.extracted?.rehab_items.length || 0) > 0 ? () => act(d, "rehab") : undefined}
                onApplyComps={(d.extracted?.comps.length || 0) > 0 ? () => act(d, "comps") : undefined}
                onRemove={() => remove(d)} />
            </div>
          ))}
        </div>
        <aside className="space-y-3 xl:sticky xl:top-4 self-start">
          <Card className="p-4">
            <DropZone compact onFiles={(files, hint) => uploader.send(files, hint)} busy={uploader.busy} status={uploader.status} />
          </Card>
          {changed && (
            <Alert type="warning" showIcon title="The file changed" description={<div><div className="text-[11.5px] text-muted">The value and the calculator already reflect it. The analyst has not re-read the file yet.</div>
              <Button size="sm" variant="primary" className="mt-2" disabled={running} onClick={() => { onRerun(["analyst"]); setChanged(false); }}>{running ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />} Re-run the review</Button></div>} />
          )}
          <Card className="p-4 text-[11.5px] text-muted space-y-1.5">
            <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">How documents are used</div>
            <div><Badge tone="accent">facts</Badge> go onto the subject when you click <b>use</b>; the engine recomputes at once and the source is kept beside the fact.</div>
            <div><Badge tone="good">priced items</Badge> from a bid can replace the tier estimate as the rehab budget, summed per trade.</div>
            <div><Badge tone="high">findings</Badge> and <Badge tone="info">dollar figures</Badge> go to the analyst as evidence; it cites the document by name.</div>
            <div><Badge tone="accent">comps</Badge> from an appraisal grid can be added as closed sales and adjusted like any other.</div>
          </Card>
        </aside>
      </div>
    </div>
  );
}
