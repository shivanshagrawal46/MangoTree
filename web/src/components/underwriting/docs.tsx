"use client";

/* Step 5 · Documents. The set for this state and this kind of owner, written from
   the file. Left: the documents, one line each. Right: the selected one laid out as
   the paper it will be. Rakesh Sir reads it, corrects the wording in place if
   needed (a correction makes a new version), downloads it as Word, and ticks it.
   Only his tick approves a document; a correction or a change on page 1 takes the
   tick away. A value the file does not hold yet is printed as [what is missing]
   and listed above the paper. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button, Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";
import { day, errText, uw, type Block, type Doc } from "@/lib/uw";
import { PageHead, Panel, Status, td, th } from "./bits";
import type { PageProps } from "./workspace";

const serif = { fontFamily: '"Source Serif 4", Georgia, "Times New Roman", serif' } as const;
const MISSING = /(\[[a-z][^\]]{2,90}\])/g;
const IS_MISSING = /^\[[a-z][^\]]{2,90}\]$/;

/** Text with every [missing value] marked, so a blank cannot be signed by accident. */
function T({ s }: { s?: string | null }) {
  if (!s) return null;
  return <>{s.split(MISSING).map((p, i) => (IS_MISSING.test(p) ?<mark key={i} className="bg-[#FBEFD9] text-[#8A5200] px-0.5 rounded-sm">{p}</mark> : <React.Fragment key={i}>{p}</React.Fragment>))}</>;
}

function Area({ value, onChange, className, bold }: { value: string; onChange: (v: string) => void; className?: string; bold?: boolean }) {
  const ref = React.useRef<HTMLTextAreaElement>(null);
  React.useLayoutEffect(() => { const el = ref.current; if (el) { el.style.height = "0px"; el.style.height = el.scrollHeight + 2 + "px"; } }, [value]);
  return <textarea ref={ref} value={value} onChange={(e) => onChange(e.target.value)} rows={1} style={serif}
    className={cn("w-full resize-none bg-[#FFFDF5] text-[#111] border border-dashed border-[#C9B98A] rounded px-1.5 py-1 focus:outline-none focus:border-[#1E5E46] leading-[1.55]", bold && "font-bold", className)} />;
}

function Sheet({ blocks, edit, onChange, foot, title }: { blocks: Block[]; edit: boolean; onChange: (b: Block[]) => void; foot?: string; title: string }) {
  const set = (i: number, patch: Partial<Block>) => onChange(blocks.map((b, n) => (n === i ? { ...b, ...patch } : b)));
  return (
    <div className="bg-white text-[#111] mx-auto max-w-[680px] px-6 sm:px-12 py-9 sm:py-11 shadow-[0_1px_4px_rgba(0,0,0,.18)] text-[13px] leading-[1.55]" style={serif}>
      {blocks.map((b, i) => {
        if (b.t === "title") return <div key={i} className={cn("text-center", i > 0 && "mt-6")}>{edit ? <Area bold className="text-center text-[16px]" value={b.text || ""} onChange={(v) => set(i, { text: v })} /> : <h1 className="text-[17px] font-bold tracking-[.04em]">{b.text}</h1>}
          {b.sub && <div className="text-[12px] mb-3.5"><T s={b.sub} /></div>}</div>;
        if (b.t === "terms") return (
          <table key={i} className="w-full border-collapse my-2.5 text-[12.5px]"><tbody>
            {(b.rows || []).map((r, n) => <tr key={n}><td className="border border-[#999] px-2 py-1 align-top font-semibold w-[32%]">{r[0]}</td>
              <td className="border border-[#999] px-2 py-1 align-top">{edit ? <Area value={r[1]} onChange={(v) => set(i, { rows: (b.rows || []).map((x, m) => (m === n ? [x[0], v] : x)) })} /> : <T s={r[1]} />}</td></tr>)}
          </tbody></table>);
        if (b.t === "h") return edit ? <Area key={i} bold className="mt-3" value={b.text || ""} onChange={(v) => set(i, { text: v })} /> : <h4 key={i} className="text-[12.5px] font-bold tracking-[.03em] mt-3.5 mb-1">{b.text}</h4>;
        if (b.t === "p") return edit
          ? <div key={i} className="mb-1.5">{b.lead != null && <Area bold value={b.lead || ""} onChange={(v) => set(i, { lead: v })} />}<Area value={b.text || ""} onChange={(v) => set(i, { text: v })} /></div>
          : <p key={i} className="mb-2 text-justify">{b.lead && <b>{b.lead} </b>}<T s={b.text} /></p>;
        if (b.t === "list") return <ol key={i} className="mb-2 pl-6 list-decimal">{(b.items || []).map((x, n) => <li key={n} className="mb-1 text-justify">{edit ? <Area value={x} onChange={(v) => set(i, { items: (b.items || []).map((y, m) => (m === n ? v : y)) })} /> : <T s={x} />}</li>)}</ol>;
        if (b.t === "sign") return <div key={i} className="mt-6 break-inside-avoid"><div className="font-bold mb-2.5"><T s={b.party} /></div>{(b.lines || []).map((x, n) => <div key={n} className="min-h-[1.2em]"><T s={x} /></div>)}</div>;
        if (b.t === "notary") return <div key={i} className="mt-5 border-t border-[#bbb] pt-2.5"><div className="font-bold text-[12px] tracking-[.03em] mb-1">ACKNOWLEDGMENT</div>
          {edit ? <Area value={b.text || ""} onChange={(v) => set(i, { text: v })} /> : <div className="whitespace-pre-line"><T s={b.text} /></div>}</div>;
        if (b.t === "note") return edit ? <Area key={i} className="text-center text-[11.5px] my-2" value={b.text || ""} onChange={(v) => set(i, { text: v })} /> : <p key={i} className="text-center text-[11.5px] my-2.5"><T s={b.text} /></p>;
        if (b.t === "break") return <div key={i} className="my-7 border-t border-dashed border-[#bbb] text-center text-[10px] text-[#888] font-sans"><span className="relative -top-2 bg-white px-2">new page</span></div>;
        return null;
      })}
      <div className="mt-7 pt-1.5 border-t border-[#bbb] flex justify-between text-[10.5px] text-[#555]"><span>{foot}</span><span>{title}</span></div>
    </div>
  );
}

export function DocumentsPage({ f, run, go }: PageProps) {
  const qc = useQueryClient();
  const docs = f.documents || [];
  const [sel, setSel] = React.useState<string | null>(null);
  const cur = docs.find((d) => d.key === sel) ?? docs.find((d) => !d.approved?.at) ?? docs[0];
  const key = ["uw", "doc", f.file_id, cur?.key, cur?.version, cur?.saved_at];
  const q = useQuery({ queryKey: key, queryFn: () => uw.document(f.file_id, cur!.key), enabled: !!cur, staleTime: 60_000 });
  const [draft, setDraft] = React.useState<Block[] | null>(null);
  const [busy, setBusy] = React.useState(false);
  const done = docs.filter((d) => d.approved?.at).length;
  const waiting = f.gates.waiting_file.length;

  const after = async (p: Promise<{ document: Doc } & typeof f>, ok: string) => {
    setBusy(true);
    try { const res = await p; const { document, ...file } = res; qc.setQueryData(["uw", "file", f.file_id], file); qc.setQueryData(["uw", "doc", f.file_id, document.key, document.version, document.saved_at], document); toast.success(ok); return true; }
    catch (e) { toast.error(errText(e)); return false; } finally { setBusy(false); }
  };

  if (!docs.length) {
    return (
      <>
        <PageHead n={5} title="Documents" line="The loan papers for this state and this kind of owner, written from the file." holds={<>Holding the next step: <b className="text-fg">the documents are not written yet</b></>} />
        <Panel className="px-4 py-6 flex flex-col items-start gap-3">
          <div><b className="font-semibold">Write the documents from the file.</b><p className="text-muted mt-0.5 max-w-[70ch]">The commitment letter can go out at once: it lists every compulsory line still open. The other documents are written at the same time and fill in as the file fills; a value the file does not hold yet is printed as [what is missing].</p></div>
          <Button variant="primary" disabled={busy} onClick={async () => { setBusy(true); await run(uw.makeDocuments(f.file_id), "The documents are written."); setBusy(false); }}>Write the documents</Button>
        </Panel>
      </>
    );
  }

  const doc = q.data;
  const blocks = draft ?? doc?.blocks ?? [];
  const approved = !!cur?.approved?.at;
  const mayTick = f.me.can_sign && (cur?.early || f.gates.file_complete) && !(cur?.missing || []).length;
  return (
    <>
      <PageHead n={5} title="Documents" line={`The set for ${f.assessment.state_name}, ${f.assessment.kind_label.toLowerCase()}. Written from the file. Rakesh Sir reads each one, corrects it if needed, and ticks it.`}
        holds={<>Holding the next step: <b className="text-fg">{done === docs.length ? "nothing" : `${docs.length - done} document${docs.length - done === 1 ? "" : "s"} to approve`}</b></>} />
      <Panel className="px-3.5 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="font-semibold">{done} of {docs.length} approved.</span>
        <span className="flex h-2 rounded-full overflow-hidden bg-sunken min-w-[140px] flex-1"><span className="bg-good" style={{ width: `${(100 * done) / docs.length}%` }} /></span>
        <span className="text-[12px] text-muted">{waiting ? `${waiting} needed line${waiting === 1 ? "" : "s"} of the file are still open: only the commitment letter can be approved until the file is complete.` : "Only Rakesh Sir's tick approves a document. A correction, or a change on page 1, takes the tick away."}</span>
        <a className="inline-flex items-center h-8 px-3 text-xs rounded-lg font-medium bg-elev border border-line hover:bg-sunken" href={uw.zipUrl(f.file_id)}
          title="Every document as a Word file, in two folders: the closing set, and apart from it the papers that are not for the Borrower or the title company.">Download the whole set (Word)</a>
        {done === docs.length && <Button variant="primary" size="sm" className="h-8" onClick={() => go(6)}>Go to review and sign</Button>}
      </Panel>
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,5fr)_minmax(0,8fr)] gap-3 mt-3 items-start">
        <Panel className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead><tr><th className={th}></th><th className={th}>Document</th><th className={th}>Status</th></tr></thead>
            <tbody>{docs.map((d, i) => (
              <tr key={d.key} onClick={() => { setSel(d.key); setDraft(null); }} className={cn("cursor-pointer", cur?.key === d.key ? "bg-accent-soft" : "hover:bg-sunken")}>
                <td className={cn(td, "text-muted tnum w-8")}>{String(i + 1).padStart(2, "0")}</td>
                <td className={td}><span className="font-medium">{d.title}</span>{d.edited && <span className="text-faint ml-1.5">Corrected</span>}
                  {d.audience === "not_borrower" && <span className="block text-[12px] text-muted">Not for the Borrower or the title company</span>}
                  {d.missing.length > 0 && <span className="block text-[12px] text-high">{d.missing.length} value{d.missing.length === 1 ? "" : "s"} still to come from the file</span>}</td>
                <td className={td}>{d.approved?.at ? <Status tone="done">Approved</Status> : <Status tone="needed">To approve</Status>}</td>
              </tr>))}</tbody>
          </table>
        </Panel>
        {cur && (
          <Panel className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-line">
              <div className="min-w-0"><b className="font-semibold">{cur.title}</b><div className="text-[12px] text-muted">Signed by: {cur.signers} · version {cur.version}{cur.saved_by ? ` · corrected by ${cur.saved_by} ${day(cur.saved_at)}` : ""}</div></div>
              <div className="flex flex-wrap items-center gap-1.5">
                {draft ? <><Button size="sm" disabled={busy} onClick={() => setDraft(null)}>Cancel</Button>
                    <Button size="sm" variant="primary" disabled={busy} onClick={async () => { if (await after(uw.editDocument(f.file_id, cur.key, draft), "Correction saved as a new version.")) setDraft(null); }}>Save the correction</Button></>
                  : <>{!f.me.contractor && <Button size="sm" disabled={busy || !doc} onClick={() => setDraft(doc!.blocks)}>Edit</Button>}
                    {cur.edited && !f.me.contractor && <Button size="sm" disabled={busy} onClick={() => after(uw.restoreDocument(f.file_id, cur.key), "Written again from the file.")}>Write again from the file</Button>}
                    <a className="inline-flex items-center h-7 px-2.5 text-xs rounded-lg font-medium bg-elev border border-line hover:bg-sunken" href={uw.wordUrl(f.file_id, cur.key)}>Word</a>
                    <label className={cn("inline-flex items-center gap-1.5 font-semibold pl-1.5", !mayTick && !approved && "opacity-50")} title={!f.me.can_sign ? "Rakesh Sir approves the documents" : (cur.missing || []).length ? "Values are still missing from the file" : !cur.early && !f.gates.file_complete ? "The file is not complete yet" : ""}>
                      <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={approved} disabled={busy || (!mayTick && !approved) || !f.me.can_sign}
                        onChange={(e) => after(uw.approveDocument(f.file_id, cur.key, e.target.checked), e.target.checked ? `${cur.title}: approved.` : "Approval taken back.")} />
                      Approved by Rakesh Sir</label></>}
              </div>
            </div>
            {(cur.missing.length > 0 || (cur.for_title || []).length > 0 || cur.audience === "not_borrower" || cur.behind_the_file || cur.approved?.voided_because || draft) && (
              <div className="px-3 py-2 border-b border-line text-[12.5px] flex flex-col gap-1">
                {draft && <span className="text-info">Correcting. Change the wording in the boxes; saving makes a new version and takes the tick away.</span>}
                {cur.audience === "not_borrower" && <span className="text-muted"><b className="text-fg">Not a borrower document.</b> It states the contractor&apos;s cost. It is signed by RKB, ROI Blocks and the contractor only, and is never sent to the Borrower or the title company.</span>}
                {(cur.for_title || []).length > 0 && <span className="text-muted"><b className="text-fg">Left for the title company to fill at closing:</b> {(cur.for_title || []).join("; ")}. The set can be approved and sent with these blank; enter them on the remaining-details page when they are known.</span>}
                {cur.missing.length > 0 && <span className="text-high"><b>Still to come from the file:</b> {cur.missing.join("; ")}. Enter them on the remaining-details page or on the file page and this document fills itself.</span>}
                {cur.behind_the_file && <span className="text-high">This document was corrected by hand, and the file has changed since. Check its figures, or write it again from the file.</span>}
                {cur.approved?.voided_because && <span className="text-high">To be approved again: {cur.approved.voided_because}.</span>}
              </div>
            )}
            <div className="bg-sunken p-3 sm:p-4 max-h-[78vh] overflow-auto">
              {q.isLoading || !doc ? <Skeleton className="h-[600px] max-w-[680px] mx-auto" /> : <Sheet blocks={blocks} edit={!!draft} onChange={setDraft} foot={doc.foot} title={cur.title} />}
            </div>
            <div className="px-3 py-1.5 border-t border-line text-[11.5px] text-muted">{cur.basis}</div>
          </Panel>
        )}
      </div>
      <p className="text-[12px] text-muted mt-2 px-0.5 max-w-[110ch]">The agreement between RKB and ROI Blocks on ROI&apos;s share is signed outside the closing and is never part of the owner&apos;s set. At the sale the title company pays RKB the whole payoff; RKB then pays ROI Blocks its share and the contractor&apos;s hold-back.</p>
    </>
  );
}
