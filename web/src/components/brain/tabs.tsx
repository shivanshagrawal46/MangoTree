"use client";

/* Everything else about a property, each in its own tab of the same page:
   emails, documents, files, site logs, bills, money, people. One look for all. */

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, FileText, Mail, Paperclip, Search } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, Card, Dialog, DialogContent, Empty, Input, Select, Skeleton } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { UploadBox } from "@/components/upload";
import { BillLines, Kpi, NumberTile, daysBetween, usd, usd0 } from "@/components/brain/side";
import { ago, cn, fmtDate, PLACEMENT_LABEL } from "@/lib/utils";
import type { ArtifactRow } from "@/lib/types";
import type { ArvHead, Bill, PropertyFile, SiteLog } from "@/lib/brain";

/* ------------------------------------------------------------ shared pieces */
export function TabBody({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return <div className="h-full overflow-y-auto"><div className={cn("mx-auto px-5 py-4 space-y-3", wide ? "max-w-6xl" : "max-w-4xl")}>{children}</div></div>;
}

function Panel({ title, sub, right, children }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5 border-b border-line">
        <div className="min-w-0"><div className="text-[13px] font-semibold tracking-tight">{title}</div>{sub && <div className="text-[11.5px] text-muted mt-0.5">{sub}</div>}</div>
        {right}
      </div>
      {children}
    </Card>
  );
}

function Find({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return <div className="relative"><Search size={13} className="absolute left-2.5 top-2.5 text-faint" /><Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} className="pl-8 w-60 h-8 text-[12.5px]" /></div>;
}

const Loading = () => <div className="p-5 space-y-2.5"><Skeleton className="h-9" /><Skeleton className="h-9" /><Skeleton className="h-9" /></div>;

/* --------------------------------------------------------------- one thread */
type ThreadMsg = { sha256: string; from?: string; to?: string[]; date?: string; body?: string; attachments_list?: { sha256: string; name: string }[] };
type Thread = { subject?: string; messages: ThreadMsg[]; property_ids?: string[] };

export function ThreadDialog({ threadKey, onClose }: { threadKey: string | null; onClose: () => void }) {
  const { open } = useEvidence();
  const t = useQuery({ queryKey: ["thread", threadKey], queryFn: () => api.get<Thread>(`/threads/${encodeURIComponent(threadKey!)}`), enabled: !!threadKey });
  return (
    <Dialog open={!!threadKey} onOpenChange={(o) => !o && onClose()}>
      <DialogContent wide title={t.data?.subject || "Conversation"} description={t.data ? `${t.data.messages.length} message${t.data.messages.length === 1 ? "" : "s"}` : ""} className="max-h-[85vh] overflow-y-auto">
        {!t.data && <Skeleton className="h-40" />}
        <div className="space-y-3">
          {(t.data?.messages || []).map((m) => (
            <div key={m.sha256} className="rounded-2xl border border-line bg-bg p-4">
              <div className="flex items-center justify-between gap-3 text-xs mb-2">
                <div className="min-w-0 truncate"><span className="font-semibold">{m.from}</span> <span className="text-faint">to {(m.to || []).join(", ")}</span></div>
                <div className="flex items-center gap-2 text-faint shrink-0"><span className="tnum">{fmtDate(m.date, "d MMM yyyy, HH:mm")}</span><button type="button" onClick={() => open({ sha: m.sha256 })} className="hover:text-fg" title="Open the original"><ExternalLink size={12} /></button></div>
              </div>
              <div className="text-[13px] whitespace-pre-wrap leading-relaxed max-h-72 overflow-y-auto">{m.body || "(empty)"}</div>
              {(m.attachments_list || []).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{m.attachments_list!.map((a) => <button type="button" key={a.sha256} onClick={() => open({ sha: a.sha256 })} className="inline-flex items-center gap-1 h-6 px-2 rounded-lg border border-line bg-elev text-[11px] hover:border-accent"><Paperclip size={10} /> {a.name}</button>)}</div>}
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------- emails */
export function EmailsTab({ pid }: { pid: string }) {
  const [q, setQ] = React.useState("");
  const [thread, setThread] = React.useState<string | null>(null);
  const d = useQuery({ queryKey: ["comms", pid, q], queryFn: () => api.get<ArtifactRow[]>(`/properties/${pid}/comms?q=${encodeURIComponent(q)}`), placeholderData: (p) => p });
  return (
    <TabBody>
      <Panel title="Emails" sub="Every email filed to this property. Open one to read its whole conversation." right={<Find value={q} onChange={setQ} placeholder="Subject or sender…" />}>
        {!d.data ? <Loading /> : d.data.length === 0 ? <Empty title="No email matches." /> : (
          <ul className="divide-y divide-line">
            {d.data.map((a) => (
              <li key={a.sha256}>
                <button type="button" onClick={() => setThread(a.thread_key || a.sha256)} className="w-full text-left flex items-center gap-3 px-4 py-2 hover:bg-sunken/60 transition">
                  <div className="h-7 w-7 rounded-full bg-sunken grid place-items-center text-[10px] font-semibold text-muted shrink-0">{(a.from || "?").slice(0, 2).toUpperCase()}</div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] font-medium truncate">{a.subject || "(no subject)"}</div>
                    <div className="text-[11.5px] text-muted truncate">{a.from}{a.attachments > 0 && <span className="ml-2 inline-flex items-center gap-0.5 text-faint"><Paperclip size={10} /> {a.attachment_names.slice(0, 2).join(", ")}{a.attachments > 2 ? ` +${a.attachments - 2}` : ""}</span>}</div>
                  </div>
                  <span className="text-[11.5px] tnum text-faint whitespace-nowrap">{fmtDate(a.date, "d MMM yy, HH:mm")}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <ThreadDialog threadKey={thread} onClose={() => setThread(null)} />
    </TabBody>
  );
}

/* ---------------------------------------------------------------- documents */
export function DocumentsTab({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const [placement, setPlacement] = React.useState("");
  const [q, setQ] = React.useState("");
  const d = useQuery({ queryKey: ["docs", pid, placement, q], placeholderData: (p) => p,
    queryFn: () => api.get<{ items: ArtifactRow[]; inventory: Record<string, number>; total: number }>(`/properties/${pid}/documents?placement=${placement}&q=${encodeURIComponent(q)}`) });
  return (
    <TabBody wide>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_240px] items-start">
        <Panel title="Documents" sub={`${d.data?.total ?? "…"} on file — the same records the AI reads. Click one to open it.`}
          right={<div className="flex items-center gap-2"><Find value={q} onChange={setQ} placeholder="Filter…" />
            <Select value={placement} onChange={(e) => setPlacement(e.target.value)} className="h-8 text-[12.5px]"><option value="">This property</option><option value="portfolio">Portfolio (shared)</option><option value="unplaced">Not yet filed</option></Select></div>}>
          {!d.data ? <Loading /> : d.data.items.length === 0 ? <Empty title="Nothing matches." /> : (
            <ul className="divide-y divide-line">
              {d.data.items.map((a) => (
                <li key={a.sha256} className="flex items-center gap-3 px-4 py-2 hover:bg-sunken/60 cursor-pointer transition" onClick={() => open({ sha: a.sha256 })}>
                  <div className="h-7 w-7 rounded-lg bg-sunken grid place-items-center shrink-0 text-muted">{a.source_type === "email" ? <Mail size={14} /> : <FileText size={14} />}</div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[12.5px] truncate">{a.name}</div>
                    <div className="text-[11.5px] text-faint flex gap-2 truncate">{a.from && <span className="truncate">{a.from}</span>}{a.doc_class && <span>· {a.doc_class}</span>}{a.attachments > 0 && <span className="flex items-center gap-0.5">· <Paperclip size={10} /> {a.attachments}</span>}</div>
                  </div>
                  {a.placement && a.placement !== "property" && <Badge tone={a.placement === "unplaced" ? "high" : "info"}>{PLACEMENT_LABEL[a.placement]}</Badge>}
                  <span className="text-[11.5px] tnum text-faint whitespace-nowrap">{fmtDate(a.date, "d MMM yy")}</span>
                  <a href={`/api/evidence/original/${a.sha256}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-faint hover:text-fg" title="Open the original file"><ExternalLink size={13} /></a>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="By type">
          <ul className="px-5 py-3 space-y-1.5">{Object.entries(d.data?.inventory || {}).map(([k, v]) => <li key={k} className="flex justify-between text-[12.5px]"><span className="text-muted">{k}</span><span className="tnum font-medium">{v}</span></li>)}</ul>
        </Panel>
      </div>
    </TabBody>
  );
}

/* -------------------------------------------------------------------- files */
type FileRow = { sha256: string; filename?: string; name?: string; doc_class?: string; size?: number; date?: string; uploaded_by?: string; uploaded_at?: string };
type FileNode = { name: string; count: number; files: FileRow[]; children: FileNode[] };

const kindOf = (name: string) => { const e = (name.split(".").pop() || "").toLowerCase(); return e === "pdf" ? "PDF" : ["xlsx", "xls", "csv"].includes(e) ? "XLS" : ["doc", "docx"].includes(e) ? "DOC" : ["jpg", "jpeg", "png", "heic"].includes(e) ? "IMG" : e.toUpperCase().slice(0, 4) || "FILE"; };

function FileLine({ f, depth, folder, onOpen }: { f: FileRow; depth: number; folder?: string; onOpen: (sha: string) => void }) {
  const name = f.filename || f.name || "file";
  return (
    <div className="flex items-center gap-3 pr-4 py-1.5 hover:bg-sunken/60 cursor-pointer group transition" style={{ paddingLeft: depth * 18 + 20 }} onClick={() => onOpen(f.sha256)}>
      <span className="h-6 w-9 rounded-md bg-sunken border border-line text-[9px] font-bold text-muted grid place-items-center shrink-0">{kindOf(name)}</span>
      <span className="min-w-0 flex-1">
        <span className="text-[12.5px] truncate block">{name}</span>
        {(folder || f.uploaded_by) && <span className="text-[11.5px] text-faint truncate block">{folder}{folder && f.uploaded_by ? " · " : ""}{f.uploaded_by ? `uploaded by ${f.uploaded_by} on ${fmtDate(f.uploaded_at, "d MMM yyyy")}` : ""}</span>}
      </span>
      {f.doc_class && <Badge>{f.doc_class}</Badge>}
      <span className="text-[11.5px] tnum text-faint w-16 text-right">{f.size ? `${(f.size / 1024).toFixed(0)} KB` : ""}</span>
      <span className="text-[11.5px] tnum text-faint w-20 text-right">{fmtDate(f.date, "d MMM yy")}</span>
      <a href={`/api/evidence/original/${f.sha256}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="text-faint hover:text-fg opacity-0 group-hover:opacity-100"><ExternalLink size={13} /></a>
    </div>
  );
}

function Folder({ node, depth, path, shut, toggle, onOpen }: { node: FileNode; depth: number; path: string; shut: Record<string, boolean>; toggle: (p: string) => void; onOpen: (sha: string) => void }) {
  const isOpen = !shut[path];
  return (
    <div>
      {depth > 0 && (
        <button type="button" onClick={() => toggle(path)} className="flex items-center gap-2 w-full text-left pr-4 py-1.5 hover:bg-sunken/60 text-[12.5px]" style={{ paddingLeft: (depth - 1) * 18 + 20 }}>
          <ChevronRight size={13} className={cn("text-faint transition", isOpen && "rotate-90")} /><span className="font-medium">{node.name}</span><span className="text-[11.5px] text-faint">{node.count}</span>
        </button>
      )}
      {isOpen && (
        <div>
          {node.children.map((c) => <Folder key={c.name} node={c} depth={depth + 1} path={`${path}/${c.name}`} shut={shut} toggle={toggle} onOpen={onOpen} />)}
          {node.files.map((f) => <FileLine key={f.sha256} f={f} depth={depth} onOpen={onOpen} />)}
        </div>
      )}
    </div>
  );
}

export function FilesTab({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const q = useQuery({ queryKey: ["files", pid], queryFn: () => api.get<{ tree: FileNode; total: number }>(`/properties/${pid}/files`) });
  const [shut, setShut] = React.useState<Record<string, boolean>>({});
  const [query, setQuery] = React.useState("");
  const needle = query.trim().toLowerCase();
  const flat = React.useMemo(() => {
    const out: { f: FileRow; folder: string }[] = [];
    const walk = (node: FileNode, path: string[]) => { node.files.forEach((f) => out.push({ f, folder: path.join(" / ") })); node.children.forEach((c) => walk(c, [...path, c.name])); };
    if (q.data) walk(q.data.tree, []);
    return out;
  }, [q.data]);
  const matches = needle ? flat.filter(({ f }) => (f.filename || f.name || "").toLowerCase().includes(needle)).sort((a, b) => String(b.f.date || "").localeCompare(String(a.f.date || ""))) : [];
  return (
    <TabBody wide>
      <UploadBox pid={pid} onDone={() => q.refetch()} />
      <Panel title="Files" sub={`${q.data?.total ?? "…"} files — the drive folders, every email attachment, and anything uploaded here. Click one to see the original.`} right={<Find value={query} onChange={setQuery} placeholder="Search by file name…" />}>
        {!q.data ? <Loading /> : (
          <div className="py-1.5">
            {needle ? (matches.length ? matches.map(({ f, folder }) => <FileLine key={f.sha256} f={f} depth={0} folder={folder || "(top folder)"} onOpen={(sha) => open({ sha })} />)
              : <div className="px-5 py-6 text-[13px] text-muted">No file name contains “{query}”.</div>)
              : <Folder node={q.data.tree} depth={0} path="" shut={shut} toggle={(p) => setShut((c) => ({ ...c, [p]: !c[p] }))} onOpen={(sha) => open({ sha })} />}
          </div>
        )}
      </Panel>
    </TabBody>
  );
}

/* ---------------------------------------------------------------- site logs */
type LogsPage = { in_buildertrend: boolean; total: number; rows: SiteLog[]; pulled_at?: string | null };

export function SiteLogsTab({ pid, bare }: { pid: string; bare?: boolean }) {
  const Wrap = bare ? React.Fragment : TabBody;
  const [zoom, setZoom] = React.useState<number | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["brain", "site-logs", pid], initialPageParam: 0,
    queryFn: ({ pageParam }) => api.get<LogsPage>(`/brain/properties/${pid}/site/logs?skip=${pageParam}&limit=20`),
    getNextPageParam: (last, all) => { const n = all.reduce((a, p) => a + p.rows.length, 0); return n < last.total ? n : undefined; },
  });
  const first = q.data?.pages[0];
  const rows = (q.data?.pages || []).flatMap((p) => p.rows);
  const quiet = daysBetween(rows[0]?.date);
  if (first && !first.in_buildertrend) return <Wrap><Card><Empty title="This property has no job in Buildertrend." sub="Site logs appear here once the contractor opens a job for it." /></Card></Wrap>;
  return (
    <Wrap>
      <Panel title="Daily logs" sub={first ? `${first.total} daily logs written by the contractor in Buildertrend · last read ${ago(first.pulled_at)}` : "…"}>
        {!first ? <Loading /> : rows.length === 0 ? <Empty title="No daily log has been written for this job." /> : (
          <>
            {quiet != null && quiet > 14 && <div className="mx-5 mt-4 rounded-xl bg-high-soft border border-high/25 px-3 py-2 text-[12.5px] text-high">No daily log for {quiet} days — the last one is dated {fmtDate(rows[0].date, "d MMM yyyy")}.</div>}
            <ol className="px-4 py-1.5">
              {rows.map((l) => (
                <li key={l.log_id} className="relative pl-6 py-2.5 border-l border-line ml-1.5 last:pb-2">
                  <span className="absolute -left-[5px] top-[15px] h-[9px] w-[9px] rounded-full bg-accent ring-4 ring-elev" />
                  <div className="flex flex-wrap items-baseline gap-x-2"><span className="text-[12.5px] font-semibold">{fmtDate(l.date, "EEE d MMM yyyy")}</span><span className="text-[13px]">{l.title || "Daily log"}</span></div>
                  <div className="text-[11.5px] text-faint">{l.by}{l.photo_count ? ` · ${l.photo_count} photo${l.photo_count === 1 ? "" : "s"}${l.photos.length === 0 ? " in Buildertrend (not brought over)" : ""}` : ""}</div>
                  {l.notes && <p className="mt-1.5 text-[12.5px] leading-relaxed text-fg/90 whitespace-pre-line">{l.notes}</p>}
                  {l.photos.length > 0 && (
                    <div className="mt-2 grid grid-cols-4 sm:grid-cols-6 gap-1.5">
                      {l.photos.map((id) => (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img key={id} src={`/api/brain/site/photo/${id}`} alt="" loading="lazy" onClick={() => setZoom(id)} className="aspect-square w-full object-cover rounded-lg border border-line cursor-zoom-in hover:opacity-90" />
                      ))}
                    </div>
                  )}
                </li>
              ))}
            </ol>
            {q.hasNextPage && <div className="px-5 pb-5"><button type="button" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage} className="h-9 px-4 rounded-lg border border-line text-[13px] hover:bg-sunken">{q.isFetchingNextPage ? "Loading…" : `Show older logs (${first.total - rows.length} more)`}</button></div>}
          </>
        )}
      </Panel>
      <Dialog open={zoom != null} onOpenChange={(o) => !o && setZoom(null)}>
        <DialogContent wide title="Site photo">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {zoom != null && <img src={`/api/brain/site/photo/${zoom}`} alt="" className="max-h-[78vh] mx-auto rounded-lg" />}
        </DialogContent>
      </Dialog>
    </Wrap>
  );
}

/* -------------------------------------------------------------------- bills */
type BillRow = Bill & { paid?: number | null; date_paid?: string | null; kind?: string; in_review?: boolean };
type BillsPage = { in_buildertrend: boolean; totals: { count: number; billed: number; paid: number; by_status: Record<string, { count: number; amount: number }> }; rows: BillRow[]; pulled_at?: string | null };

const STATUS_TONE: Record<string, "good" | "high" | "info" | "neutral"> = { "Paid": "good", "In review": "high", "Ready for payment": "info" };

export function BillsTab({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["brain", "site-bills", pid], queryFn: () => api.get<BillsPage>(`/brain/properties/${pid}/site/bills`) });
  const [status, setStatus] = React.useState("In review");
  const [open, setOpen] = React.useState<number | null>(null);
  if (!q.data) return <TabBody wide><Card><Loading /></Card></TabBody>;
  const d = q.data;
  if (!d.in_buildertrend) return <TabBody><Card><Empty title="This property has no job in Buildertrend." sub="Bills appear here once the contractor opens a job for it." /></Card></TabBody>;
  const statuses = ["In review", "Ready for payment", "Paid", ...Object.keys(d.totals.by_status).filter((s) => !["In review", "Ready for payment", "Paid"].includes(s))];
  const rows = status === "All" ? d.rows : d.rows.filter((b) => b.status === status);
  const sum = (s: string) => d.totals.by_status[s] || { count: 0, amount: 0 };
  return (
    <TabBody wide>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
        {[["Billed", usd(d.totals.billed), `${d.totals.count} bills`], ["Paid", usd(d.totals.paid), `${sum("Paid").count} bills`],
          ["Ready for payment", usd(sum("Ready for payment").amount), `${sum("Ready for payment").count} approved, not paid`], ["In review", usd(sum("In review").amount), `${sum("In review").count} waiting for approval`]].map(([l, v, s]) => (
          <Card key={l} className="px-3 py-2"><div className="text-[9.5px] font-semibold uppercase tracking-[0.09em] text-faint">{l}</div><div className="mt-0.5 text-[15px] font-semibold tracking-tight tnum">{v}</div><div className="text-[11.5px] text-muted">{s}</div></Card>
        ))}
      </div>
      <Panel title="Bills" sub={`Entered by the contractor in Buildertrend · last read ${ago(d.pulled_at)}`}
        right={<div className="inline-flex flex-wrap rounded-lg bg-sunken p-0.5 border border-line">
          {[...statuses, "All"].map((s) => <button key={s} type="button" onClick={() => setStatus(s)} className={cn("h-7 px-2.5 rounded-md text-[12px] font-medium transition", status === s ? "bg-elev text-fg shadow-[var(--shadow-sm)]" : "text-muted hover:text-fg")}>{s}{s !== "All" ? ` · ${sum(s).count}` : ""}</button>)}
        </div>}>
        {rows.length === 0 ? <Empty title={status === "In review" ? "No bill is waiting for approval." : "No bills with this status."} /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead><tr className="text-left text-[10.5px] font-semibold uppercase tracking-[0.08em] text-faint border-b border-line">
                <th className="px-4 py-1.5 font-semibold">Bill</th><th className="py-2 font-semibold">Pay to</th><th className="py-2 font-semibold">Invoiced</th><th className="py-2 font-semibold">Due</th>
                <th className="py-2 font-semibold text-right">Amount</th><th className="py-2 font-semibold text-right">Paid</th><th className="py-1.5 pr-4 pl-4 font-semibold">Status</th></tr></thead>
              <tbody>
                {rows.map((b) => (
                  <React.Fragment key={b.bill_id}>
                    <tr className={cn("border-b border-line/70 align-top", (b.line_items || []).length > 0 && "cursor-pointer hover:bg-sunken/60")} onClick={() => (b.line_items || []).length > 0 && setOpen(open === b.bill_id ? null : b.bill_id)}>
                      <td className="px-4 py-2 max-w-[340px]"><div className="font-medium truncate">{b.title || b.number}</div><div className="text-[11.5px] text-faint">{b.number}{b.approvers?.length ? ` · approver: ${b.approvers.join(", ")}` : ""}</div></td>
                      <td className="py-2 pr-3 text-muted max-w-[200px] truncate">{b.pay_to || "—"}</td>
                      <td className="py-2 pr-3 tnum text-muted whitespace-nowrap">{fmtDate(b.invoice_date, "d MMM yy")}</td>
                      <td className={cn("py-2 pr-3 tnum whitespace-nowrap", b.past_due && b.status !== "Paid" ? "text-critical font-medium" : "text-muted")}>{fmtDate(b.due_date, "d MMM yy")}</td>
                      <td className="py-2 text-right tnum font-medium whitespace-nowrap">{usd(b.amount)}</td>
                      <td className="py-2 text-right tnum text-muted whitespace-nowrap">{usd(b.paid)}</td>
                      <td className="py-2 pr-4 pl-4"><Badge tone={STATUS_TONE[b.status || ""] || "neutral"}>{b.status}</Badge></td>
                    </tr>
                    {open === b.bill_id && <tr className="border-b border-line/70 bg-sunken/40"><td colSpan={7} className="px-5 py-3"><BillLines b={b} /></td></tr>}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </TabBody>
  );
}

/* ---------------------------------------------------------------------- arv */
type ArvDetail = ArvHead & {
  exists: boolean; address?: string; reasons?: string[]; assumptions?: Record<string, string | number | boolean | null>;
  rehab?: { basis?: string; source?: string; subtotal?: number; permit_cost?: number; oh_and_p?: number; contingency?: number; total?: number; per_sqft?: number; lines: { label: string; amount: number; estimated: boolean }[] };
  others?: { analysis_id: string; value?: number | null; by?: string; from_contractor?: boolean; org?: string; at?: string }[];
};

export function ArvTab({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["brain", "property-arv", pid], queryFn: () => api.get<ArvDetail>(`/brain/properties/${pid}/arv`) });
  if (!q.data) return <TabBody><Card><Loading /></Card></TabBody>;
  const a = q.data;
  if (!a.exists) {
    return (
      <TabBody>
        <Card className="px-6 py-10 text-center">
          <div className="text-[14px] font-semibold">No ARV analysis has been run for this property.</div>
          <div className="text-[12.5px] text-muted mt-1">An analysis pulls comparable sales, values the finished house and prices the rehab.</div>
          <Link href="/arv/new" className="mt-4 inline-flex h-8 px-3.5 items-center rounded-lg bg-accent text-white text-[12.5px] font-medium hover:opacity-90">Run an ARV analysis</Link>
        </Card>
      </TabBody>
    );
  }
  const r = a.rehab;
  const as = a.assumptions || {};
  const pct = (k: string) => (as[k] != null ? `${as[k]}%` : "—");
  return (
    <TabBody>
      {a.from_contractor && <div className="rounded-lg bg-high-soft border border-high/25 px-3.5 py-2 text-[12.5px] text-high">This analysis was run by {a.by || "the contractor"}{a.org ? ` (${a.org})` : ""}, not by RKB. RKB has not run its own for this property.</div>}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
        <Kpi label="ARV" value={usd0(a.value)} sub={`${usd0(a.low)} – ${usd0(a.high)}`} />
        <Kpi label="Confidence" value={a.confidence?.grade ? `${a.confidence.grade} · ${a.confidence.score}/100` : "—"} sub={a.confidence?.label || undefined} />
        <Kpi label="Rehab budget" value={usd0(r?.total)} sub={r?.per_sqft ? `${usd(r.per_sqft)} per sq ft` : undefined} />
        <Kpi label="Comparable sales" value={a.comps ?? "—"} sub={a.at ? `run ${fmtDate(a.at, "d MMM yyyy")} by ${a.by || "—"}` : undefined} />
      </div>
      <Panel title="How the value was reached" sub={a.basis || undefined}
        right={<Link href={`/arv/${a.analysis_id}`} className="h-8 px-3 inline-flex items-center rounded-lg border border-line text-[12.5px] hover:bg-sunken">Open the full analysis</Link>}>
        <table className="w-full text-[12.5px]"><tbody>
          <tr className="border-b border-line/70"><td className="px-4 py-1.5 text-muted w-[38%]">From the comparable sales (calculated)</td><td className="py-1.5 pr-4 tnum">{usd0(a.engine)}</td></tr>
          <tr className="border-b border-line/70"><td className="px-4 py-1.5 text-muted">Analyst opinion</td><td className="py-1.5 pr-4 tnum">{usd0(a.analyst)}</td></tr>
          <tr className="border-b border-line/70"><td className="px-4 py-1.5 text-muted">Hold · rule · profit target</td><td className="py-1.5 pr-4 tnum">{as.months_held ?? "—"} months · {pct("rule_pct")} · {pct("profit_target_pct")}</td></tr>
          <tr><td className="px-4 py-1.5 text-muted align-top">Why this confidence</td><td className="py-1.5 pr-4"><ul className="space-y-0.5">{(a.reasons || []).map((x, i) => <li key={i} className="text-[12.5px]">{x}</li>)}</ul></td></tr>
        </tbody></table>
      </Panel>
      {r && r.lines.length > 0 && (
        <Panel title="Rehab budget, line by line" sub={r.basis || (r.source ? `From ${r.source}` : undefined)}>
          <table className="w-full text-[12.5px]"><tbody>
            {r.lines.map((l, i) => <tr key={i} className="border-b border-line/70"><td className="px-4 py-1.5">{l.label}{l.estimated && <span className="ml-2 text-[11px] text-high">estimated</span>}</td><td className="py-1.5 pr-4 text-right tnum">{usd0(l.amount)}</td></tr>)}
            <tr className="border-b border-line/70 text-muted"><td className="px-4 py-1.5">Subtotal</td><td className="py-1.5 pr-4 text-right tnum">{usd0(r.subtotal)}</td></tr>
            {!!r.permit_cost && <tr className="border-b border-line/70 text-muted"><td className="px-4 py-1.5">Permits</td><td className="py-1.5 pr-4 text-right tnum">{usd0(r.permit_cost)}</td></tr>}
            {!!r.oh_and_p && <tr className="border-b border-line/70 text-muted"><td className="px-4 py-1.5">Overhead and profit</td><td className="py-1.5 pr-4 text-right tnum">{usd0(r.oh_and_p)}</td></tr>}
            {!!r.contingency && <tr className="border-b border-line/70 text-muted"><td className="px-4 py-1.5">Contingency</td><td className="py-1.5 pr-4 text-right tnum">{usd0(r.contingency)}</td></tr>}
            <tr className="font-semibold"><td className="px-4 py-2">Total</td><td className="py-2.5 pr-5 text-right tnum">{usd0(r.total)}</td></tr>
          </tbody></table>
        </Panel>
      )}
      {(a.others || []).length > 0 && (
        <Panel title="Other analyses of this address">
          <ul className="divide-y divide-line">{a.others!.map((o) => (
            <li key={o.analysis_id}><Link href={`/arv/${o.analysis_id}`} className="flex items-center gap-3 px-4 py-2 hover:bg-sunken/60 text-[13px]">
              <span className="font-medium tnum w-28">{usd0(o.value)}</span><span className="flex-1 text-muted truncate">{o.by}{o.from_contractor ? " · contractor" : ""}</span><span className="text-[11.5px] text-faint tnum">{fmtDate(o.at, "d MMM yyyy")}</span></Link></li>))}</ul>
        </Panel>
      )}
    </TabBody>
  );
}

/* -------------------------------------------------------------------- money */
export function MoneyTab({ f }: { f: PropertyFile }) {
  const nums = f.numbers?.main || [];
  const t = f.site?.in_buildertrend ? f.site.bills?.totals : undefined;
  return (
    <TabBody>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">{nums.map((n) => <NumberTile key={n.key} n={n} />)}</div>
      {!f.numbers?.in_workbooks && <div className="rounded-xl bg-high-soft border border-high/25 px-4 py-2.5 text-[13px] text-high">This property is not in RKB&apos;s loan workbooks yet. Figures marked “from the emails” were read out of the records and should be confirmed.</div>}
      {(f.money || []).map((src, i) => (
        <Panel key={i} title={src.file} sub={src.as_of}>
          <table className="w-full text-[12.5px]"><tbody>
            {src.rows.map((r, j) => (
              <tr key={j} className="border-b border-line/70 last:border-0 align-top"><td className="px-4 py-1.5 text-muted w-[38%]">{r.item}</td>
                <td className="py-1.5 pr-4 tnum">{r.value}{r.note ? <div className="text-[11.5px] text-faint mt-0.5">{r.note}</div> : null}</td></tr>
            ))}
          </tbody></table>
        </Panel>
      ))}
      {t && (
        <Panel title="Contractor's bills (Buildertrend)" sub="What the contractor has entered to be paid — not RKB's own ledger">
          <table className="w-full text-[12.5px]"><tbody>
            <tr className="border-b border-line/70"><td className="px-4 py-1.5 text-muted w-[38%]">Billed, all bills</td><td className="py-1.5 pr-4 tnum">{usd(t.billed)} <span className="text-faint">· {t.count} bills</span></td></tr>
            <tr className="border-b border-line/70"><td className="px-4 py-1.5 text-muted">Paid</td><td className="py-1.5 pr-4 tnum">{usd(t.paid)}</td></tr>
            {Object.entries(t.by_status).filter(([k]) => k !== "Paid").map(([k, v]) => <tr key={k} className="border-b border-line/70 last:border-0"><td className="px-4 py-1.5 text-muted">{k}</td><td className="py-1.5 pr-4 tnum">{usd(v.amount)} <span className="text-faint">· {v.count}</span></td></tr>)}
          </tbody></table>
        </Panel>
      )}
      {(f.money || []).length === 0 && !t && <Card><Empty title="No figures on file for this property yet." /></Card>}
    </TabBody>
  );
}

/* ------------------------------------------------------------------- people */
type PersonRow = { person_id: string; display_name?: string; role?: string; org?: string; side?: string; mentions?: number };

export function PeopleTab({ pid, f }: { pid: string; f: PropertyFile }) {
  const router = useRouter();
  const p = useQuery({ queryKey: ["people", pid], queryFn: () => api.get<PersonRow[]>(`/people?property_id=${pid}`) });
  return (
    <TabBody>
      <Panel title="On this deal" sub="From RKB's loan workbook and the property file">
        {(f.people || []).length === 0 ? <Empty title="No parties on record yet." /> : (
          <dl className="divide-y divide-line">
            {f.people!.map((x, i) => (
              <div key={i} className="px-5 py-3 grid sm:grid-cols-[170px_minmax(0,1fr)] gap-x-4 gap-y-1">
                <dt className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-faint pt-0.5">{x.role}</dt>
                <dd className="text-[13px] leading-relaxed">{x.value}
                  {x.contacts.map((c, j) => <div key={j} className="text-[12px] text-muted">{c.name}{c.emails.map((e) => <a key={e} href={`mailto:${e}`} className="ml-1.5 text-accent hover:underline">{e}</a>)}</div>)}
                  <div className="text-[11px] text-faint">{x.source}</div></dd>
              </div>
            ))}
          </dl>
        )}
      </Panel>
      <Panel title="Everyone in the records" sub="People who appear in this property's emails and documents">
        {!p.data ? <Loading /> : p.data.length === 0 ? <Empty title="No linked people yet." /> : (
          <ul className="divide-y divide-line">{p.data.map((x) => (
            <li key={x.person_id} className="px-4 py-2 flex items-center gap-3 hover:bg-sunken/60 cursor-pointer transition" onClick={() => router.push(`/people/${x.person_id}`)}>
              <div className="h-7 w-7 rounded-full bg-accent-soft text-accent grid place-items-center text-[11px] font-semibold shrink-0">{(x.display_name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("")}</div>
              <div className="min-w-0 flex-1"><div className="text-[12.5px] font-medium truncate">{x.display_name}</div><div className="text-[11.5px] text-faint truncate">{[x.role, x.org].filter(Boolean).join(" · ")}</div></div>
              {x.side && <Badge tone={x.side === "rkb" ? "accent" : "neutral"}>{x.side === "rkb" ? "RKB" : x.side}</Badge>}
              <span className="text-[11.5px] tnum text-faint w-24 text-right">{x.mentions} mentions</span>
            </li>))}</ul>
        )}
      </Panel>
    </TabBody>
  );
}
