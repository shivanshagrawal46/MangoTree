"use client";

/* What the contractor enters itself (2026-11-22): bills, daily logs and change orders, straight into
   RKB's system — in place of RKB copying them out of the contractor's Buildertrend each morning.

   Three tabs, laid out as the mock agreed that day:
     · Bills          — the proposal's lines with what is left on each; an amount per line; the papers and
                        photos a bill needs; a checklist that keeps "Send to RKB" off until all is there.
     · Daily logs     — one narrow column, made for a phone on site: tick the work, a sentence, photos.
     · Change orders  — extra work the homeowner has approved in Buildertrend; RKB agrees here before it
                        can be billed.
   Every rule shown here is checked again on the server when the record is sent (brain/entry.py). */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Camera, Check, Loader2, Paperclip, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn, fmtDate } from "@/lib/utils";

type Line = { key: string; group: string; title: string; proposal: number; billed: number; left: number; change_order: string | null };
type FileRec = { file_id: number; role: string; name: string; ext: string; bytes: number; is_photo: boolean; taken_at?: string | null };
type Bill = {
  entry_id: string; kind: string; kind_name: string; pay_to: string; invoice_no: string; invoice_date?: string | null; invoice_total: number;
  work_from?: string | null; work_to?: string | null; note: string; lines: Record<string, number>; total: number; holdback: number; pay_now: number;
  files: FileRec[]; problems: { code: string; text: string }[]; can_send: boolean; status: string; number?: string | null; stands: string; stage: string;
  can_withdraw: boolean; created_at?: string | null; sent_at?: string | null;
};
type Home = {
  property_id: string; property: string; jobs: { property_id: string; name: string }[]; totals: { proposal: number; billed: number; left: number }; held: number;
  lines: Line[]; pay_to: string[]; kinds: { key: string; name: string; holdback: number }[]; min_photos: number; holdback: number; bills: Bill[];
};
type LogRow = { log_id: number; date?: string | null; by: string; title: string; notes: string; photos: number; here: boolean };
type Logs = { logs: LogRow[]; today: string; logged_today: boolean; tags: string[]; min_photos: number };
type CoWaiting = { co_id: number; number: string; title: string; cost: number; price: number; status: string; stands: string; sent_at?: string | null; declined_why?: string | null; approved_by: string; approved_on: string };
type CoApproved = { co_id: number; number: string; title: string; cost: number; price: number; approved_at?: string | null; approved_by: string; rkb_agreed: boolean; billed: number; billed_in_full: boolean };
type Cos = { approved: CoApproved[]; waiting: CoWaiting[]; groups: string[]; min_photos: number };

const usd = (n?: number | null) => (n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));
const num = (s: string) => { const v = Number(String(s).replace(/[$,\s]/g, "")); return Number.isFinite(v) && v > 0 ? Math.round(v * 100) / 100 : 0; };
const r2 = (v: number) => Math.round(v * 100) / 100;
const errText = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");

const PANEL = "overflow-hidden rounded-md border border-line bg-elev";
const HEAD = "flex items-center justify-between gap-3 border-b border-line px-4 py-2.5";
const H2 = "text-[14px] font-semibold";
const LABEL = "mb-1 block text-[11.5px] font-medium text-muted";
const INPUT = "h-8 w-full rounded-[5px] border border-line-strong bg-elev px-2.5 text-[13px] outline-none focus:border-accent disabled:bg-sunken disabled:text-faint";
const BTN = "inline-flex h-[30px] items-center justify-center gap-1.5 whitespace-nowrap rounded-[5px] border px-3 text-[12.5px] font-medium transition-colors";
const BTN_PLAIN = cn(BTN, "border-line-strong bg-elev text-fg hover:bg-sunken");
const BTN_MAIN = cn(BTN, "border-accent bg-accent text-white hover:opacity-90");
const BTN_OFF = cn(BTN, "cursor-not-allowed border-line bg-sunken text-faint");
const TH = "whitespace-nowrap border-b border-line bg-sunken/50 px-3 py-1.5 text-left text-[11.5px] font-medium text-muted";
const TD = "h-[38px] border-b border-line/70 px-3 text-[13px]";

function State({ tone, children }: { tone?: "red" | "amber" | "green"; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[12.5px]", tone === "red" ? "font-medium text-critical" : tone === "amber" ? "font-medium text-high" : "text-fg/80")}>
      <i className={cn("h-[7px] w-[7px] shrink-0 rounded-full", tone === "red" ? "bg-critical" : tone === "amber" ? "bg-high" : tone === "green" ? "bg-good" : "bg-line-strong")} />
      {children}
    </span>
  );
}

/* Add files of one kind. The bytes go to the server at once; the record names them when it is saved. */
function useUpload(pid: string) {
  const [busy, setBusy] = React.useState<string | null>(null);
  const send = async (role: string, list: FileList | null): Promise<FileRec[]> => {
    if (!list || !list.length) return [];
    setBusy(role);
    try {
      const form = new FormData();
      form.append("role", role);
      Array.from(list).forEach((f) => form.append("files", f));
      const out = await api.upload<{ files: FileRec[] }>(`/portal/work/${pid}/files`, form);
      return out.files;
    } catch (e) { toast.error(errText(e)); return []; } finally { setBusy(null); }
  };
  return { busy, send };
}

function Pick({ label, busy, onFiles, accept, capture, multiple = true, icon }: { label: string; busy?: boolean; onFiles: (l: FileList | null) => void; accept: string; capture?: boolean; multiple?: boolean; icon?: React.ReactNode }) {
  const ref = React.useRef<HTMLInputElement>(null);
  return (
    <>
      <input ref={ref} type="file" className="hidden" accept={accept} multiple={multiple} {...(capture ? { capture: "environment" as const } : {})}
        onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} />
      <button type="button" disabled={busy} onClick={() => ref.current?.click()} className="inline-flex items-center gap-1 text-[12.5px] font-medium text-accent hover:underline disabled:opacity-50">
        {busy ? <Loader2 size={12} className="animate-spin" /> : icon}{label}
      </button>
    </>
  );
}

function Thumbs({ files, onRemove }: { files: FileRec[]; onRemove?: (id: number) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {files.map((f) => (
        <span key={f.file_id} className="group relative block h-[46px] w-[58px] overflow-hidden rounded-[4px] border border-line bg-sunken">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/api/portal/work/files/${f.file_id}?thumb=1`} alt={f.name} className="h-full w-full object-cover" />
          {onRemove && <button type="button" aria-label={`Remove ${f.name}`} onClick={() => onRemove(f.file_id)} className="absolute right-0 top-0 hidden h-4 w-4 place-items-center bg-black/60 text-white group-hover:grid"><X size={10} /></button>}
        </span>
      ))}
    </div>
  );
}

function Chips({ files, onRemove }: { files: FileRec[]; onRemove: (id: number) => void }) {
  if (!files.length) return <span className="text-[12.5px] text-muted">None</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {files.map((f) => (
        <span key={f.file_id} className="inline-flex items-center gap-1.5 rounded-[4px] border border-line bg-sunken/50 px-2 py-[3px] text-[12px]">
          <a href={`/api/portal/work/files/${f.file_id}`} target="_blank" rel="noreferrer" className="max-w-[220px] truncate text-fg hover:underline">{f.name}</a>
          <button type="button" aria-label={`Remove ${f.name}`} onClick={() => onRemove(f.file_id)} className="text-faint hover:text-fg"><X size={11} /></button>
        </span>
      ))}
    </div>
  );
}

/* ───────────────────────── Bills ───────────────────────── */

type Draft = { entry_id?: string; kind: string; pay_to: string; invoice_no: string; invoice_date: string; invoice_total: string; work_from: string; work_to: string; lines: Record<string, string>; files: FileRec[] };
const blank = (): Draft => ({ kind: "work", pay_to: "", invoice_no: "", invoice_date: "", invoice_total: "", work_from: "", work_to: "", lines: {}, files: [] });

function Bills({ home, refresh }: { home: Home; refresh: () => void }) {
  const pid = home.property_id;
  const [d, setD] = React.useState<Draft>(blank);
  const [all, setAll] = React.useState(false);
  const [busy, setBusy] = React.useState<string | null>(null);
  const up = useUpload(pid);
  const set = (patch: Partial<Draft>) => setD((cur) => ({ ...cur, ...patch }));
  const add = async (role: string, list: FileList | null) => { const got = await up.send(role, list); if (got.length) setD((cur) => ({ ...cur, files: [...cur.files, ...got] })); };
  const drop = (id: number) => setD((cur) => ({ ...cur, files: cur.files.filter((f) => f.file_id !== id) }));
  const of = (role: string) => d.files.filter((f) => f.role === role);

  // The same sums and the same rules the server applies when the bill is sent — shown as the person types.
  // (A bill that is still being written is not in "billed so far", so what is left is the whole room for it.)
  const rows = home.lines.map((l) => ({ ...l, amount: num(d.lines[l.key] || ""), over: num(d.lines[l.key] || "") > l.left + 0.005 }));
  const total = r2(rows.reduce((n, r) => n + r.amount, 0));
  const held = d.kind === "work" ? r2(rows.reduce((n, r) => n + (r.amount - r2(r.amount * (1 - home.holdback))), 0)) : 0;
  const need: { ok: boolean; text: string; sub?: string }[] = [
    { ok: total > 0, text: "An amount on at least one line" },
    ...rows.filter((r) => r.over).map((r) => ({ ok: false, text: `${r.title} is over what is left`, sub: `Lower it to ${usd(r.left)}, or enter a change order and wait for RKB to agree` })),
    { ok: !!d.pay_to.trim() && !!d.invoice_no.trim(), text: "Who is paid, and the invoice number" },
    { ok: of("invoice").length > 0, text: "Invoice attached" },
    ...(d.kind === "materials" ? [{ ok: of("receipt").length > 0, text: "Receipts for the materials attached" }] : []),
    ...(d.kind !== "pm" ? [{ ok: of("photo").length >= home.min_photos, text: "Photos of this work", sub: of("photo").length >= home.min_photos ? undefined : `${home.min_photos - of("photo").length} more needed (at least ${home.min_photos})` }] : []),
    { ok: total > 0 && Math.abs(num(d.invoice_total) - total) < 0.005, text: "Invoice total matches the lines", sub: total > 0 && Math.abs(num(d.invoice_total) - total) >= 0.005 ? `The invoice says ${usd(num(d.invoice_total))}; the lines add to ${usd(total)}` : undefined },
  ];
  const ready = need.every((n) => n.ok);
  const shown = rows.filter((r) => all || r.left > 0.005 || r.amount > 0);
  const hidden = rows.length - shown.length;

  const body = () => ({ kind: d.kind, pay_to: d.pay_to, invoice_no: d.invoice_no, invoice_date: d.invoice_date || null, invoice_total: num(d.invoice_total), work_from: d.work_from || null,
    work_to: d.work_to || null, lines: Object.fromEntries(rows.filter((r) => r.amount > 0).map((r) => [r.key, r.amount])), files: d.files.map((f) => f.file_id) });
  const save = async (): Promise<Bill | null> => {
    try {
      const v = d.entry_id ? await api.put<Bill>(`/portal/work/${pid}/bills/${d.entry_id}`, body()) : await api.post<Bill>(`/portal/work/${pid}/bills`, body());
      set({ entry_id: v.entry_id });
      return v;
    } catch (e) { toast.error(errText(e)); return null; }
  };
  const later = async () => { setBusy("save"); const v = await save(); setBusy(null); if (v) { toast.success("Saved. Nothing has gone to RKB yet."); refresh(); } };
  const send = async () => {
    setBusy("send");
    const v = await save();
    if (v && !v.can_send) toast.error(v.problems.map((p) => p.text).join(" "));
    if (v && v.can_send) {
      try { const sent = await api.post<Bill>(`/portal/work/bills/${v.entry_id}/send`); toast.success(`Sent to RKB as ${sent.number}. RKB has been told.`); setD(blank()); }
      catch (e) { toast.error(errText(e)); }
    }
    setBusy(null); refresh();
  };
  const open = (b: Bill) => { setD({ entry_id: b.entry_id, kind: b.kind, pay_to: b.pay_to, invoice_no: b.invoice_no, invoice_date: b.invoice_date || "", invoice_total: b.invoice_total ? String(b.invoice_total) : "",
    work_from: b.work_from || "", work_to: b.work_to || "", lines: Object.fromEntries(Object.entries(b.lines).map(([k, v]) => [k, String(v)])), files: b.files }); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const back = async (b: Bill) => { try { const v = await api.post<Bill>(`/portal/work/bills/${b.entry_id}/withdraw`); toast.success("Taken back. Change it and send it again."); refresh(); open(v); } catch (e) { toast.error(errText(e)); } };
  const discard = async (b: Bill) => { try { await api.del(`/portal/work/bills/${b.entry_id}`); if (d.entry_id === b.entry_id) setD(blank()); refresh(); } catch (e) { toast.error(errText(e)); } };

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[19px] font-semibold tracking-tight">{d.entry_id ? "Bill, not sent yet" : "New bill"}</h1>
          <div className="text-[13px] text-muted">{home.property} · proposal {usd(home.totals.proposal)} · billed so far {usd(home.totals.billed)}</div>
        </div>
        <div className="flex items-center gap-2 text-[12px] text-muted">
          {["You enter it", "RKB checks", "Approved", "Paid"].map((s, i) => (
            <React.Fragment key={s}>{i > 0 && <span className="h-px w-5 bg-line-strong" />}
              <span className={cn("inline-flex items-center gap-1.5", i === 0 && "font-semibold text-fg")}><i className={cn("grid h-[18px] w-[18px] place-items-center rounded-full border text-[11px] not-italic", i === 0 ? "border-accent bg-accent text-white" : "border-line-strong")}>{i + 1}</i>{s}</span>
            </React.Fragment>
          ))}
        </div>
      </div>

      <div className="mt-3.5 grid items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="space-y-3.5">
          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>Bill details</h2></div>
            <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-[1.45fr_1.5fr_0.8fr_0.9fr_0.8fr]">
              <div><label className={LABEL}>This bill is for</label>
                <select className={INPUT} value={d.kind} onChange={(e) => set({ kind: e.target.value })}>{home.kinds.map((k) => <option key={k.key} value={k.key}>{k.name}{k.holdback ? " (10% held)" : ""}</option>)}</select></div>
              <div><label className={LABEL}>Pay to</label>
                <input className={INPUT} list="pay-to" value={d.pay_to} onChange={(e) => set({ pay_to: e.target.value })} placeholder="Who is paid" />
                <datalist id="pay-to">{home.pay_to.map((p) => <option key={p} value={p} />)}</datalist></div>
              <div><label className={LABEL}>Invoice number</label><input className={INPUT} value={d.invoice_no} onChange={(e) => set({ invoice_no: e.target.value })} /></div>
              <div><label className={LABEL}>Invoice date</label><input type="date" className={INPUT} value={d.invoice_date} onChange={(e) => set({ invoice_date: e.target.value })} /></div>
              <div><label className={LABEL}>Invoice total</label><input className={cn(INPUT, "text-right tnum")} inputMode="decimal" value={d.invoice_total} onChange={(e) => set({ invoice_total: e.target.value })} placeholder="0.00" /></div>
            </div>
          </section>

          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>What is this bill for <span className="ml-2 text-[12.5px] font-normal text-muted">Put an amount on each line this bill charges</span></h2>
              <button type="button" onClick={() => setAll((v) => !v)} className="text-[12.5px] font-medium text-accent hover:underline">{all ? "Hide the lines that are fully billed" : `Show all ${rows.length} lines`}</button></div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] border-collapse">
                <thead><tr><th className={cn(TH, "pl-4")}>Proposal line</th><th className={cn(TH, "text-right")}>Proposal</th><th className={cn(TH, "text-right")}>Billed so far</th><th className={cn(TH, "text-right")}>Left</th><th className={cn(TH, "pr-4 text-right")}>This bill</th></tr></thead>
                <tbody>
                  {shown.map((r, i) => {
                    const head = i === 0 || shown[i - 1].group !== r.group;
                    return (
                      <React.Fragment key={r.key}>
                        {head && <tr><td colSpan={5} className="h-7 border-b border-line/70 bg-sunken/50 pl-4 text-[11.5px] font-medium text-muted">{r.group}</td></tr>}
                        <tr>
                          <td className={cn(TD, "pl-4 font-medium")}>{r.title}{r.over && <div className="pb-1 text-[11.5px] font-normal leading-tight text-critical">More than the {usd(r.left)} left on this line. Extra work needs a change order RKB has agreed to.</div>}</td>
                          <td className={cn(TD, "text-right tnum")}>{usd(r.proposal)}</td>
                          <td className={cn(TD, "text-right tnum")}>{usd(r.billed)}</td>
                          <td className={cn(TD, "text-right tnum")}>{usd(r.left)}</td>
                          <td className={cn(TD, "pr-4 text-right")}>
                            <input inputMode="decimal" disabled={r.left <= 0.005 && !r.amount} value={d.lines[r.key] || ""} placeholder="0.00" aria-label={`This bill — ${r.title}`}
                              onChange={(e) => setD((cur) => ({ ...cur, lines: { ...cur.lines, [r.key]: e.target.value } }))}
                              className={cn("h-7 w-[112px] rounded-[5px] border bg-elev px-2 text-right text-[13px] tnum outline-none focus:border-accent disabled:bg-sunken", r.over ? "border-critical text-critical" : "border-line-strong")} />
                          </td>
                        </tr>
                      </React.Fragment>
                    );
                  })}
                  {hidden > 0 && <tr><td colSpan={5} className="h-[34px] border-b border-line/70 pl-4 text-[12.5px] text-muted">{hidden} line{hidden === 1 ? " is" : "s are"} fully billed and not shown.</td></tr>}
                  <tr><td className="h-[38px] bg-sunken/50 pl-4 text-[13px] font-semibold">This bill</td><td colSpan={3} className="bg-sunken/50" /><td className="bg-sunken/50 pr-4 text-right text-[13px] font-semibold tnum">{usd(total)}</td></tr>
                </tbody>
              </table>
            </div>
          </section>

          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>Papers and photos <span className="ml-2 text-[12.5px] font-normal text-muted">A bill cannot be sent without these</span></h2></div>
            {[{ role: "invoice", name: "Invoice", sub: "Required", show: true },
              { role: "receipt", name: "Receipts for materials", sub: d.kind === "materials" ? "Required for a materials bill" : "If there are any", show: true },
              { role: "waiver", name: "Lien waiver", sub: "If your sub gave one", show: true }].map((k, i) => (
              <div key={k.role} className={cn("grid items-center gap-3 px-4 py-2.5 sm:grid-cols-[210px_minmax(0,1fr)_auto]", i > 0 && "border-t border-line/70")}>
                <div className="text-[13px] font-medium">{k.name}<span className="block text-[11.5px] font-normal text-muted">{k.sub}</span></div>
                <Chips files={of(k.role)} onRemove={drop} />
                <Pick label="Attach" busy={up.busy === k.role} accept="application/pdf,image/*" onFiles={(l) => add(k.role, l)} icon={<Paperclip size={12} />} />
              </div>
            ))}
            {d.kind !== "pm" && (
              <div className="grid items-center gap-3 border-t border-line/70 px-4 py-2.5 sm:grid-cols-[210px_minmax(0,1fr)_auto]">
                <div className="text-[13px] font-medium">Photos of this work<span className="block text-[11.5px] font-normal text-muted">At least {home.min_photos}, showing what is billed</span></div>
                <div className="flex flex-wrap items-center gap-2"><Thumbs files={of("photo")} onRemove={drop} />
                  <State tone={of("photo").length >= home.min_photos ? "green" : "red"}>{of("photo").length} of {home.min_photos}</State></div>
                <div className="flex items-center gap-3"><Pick label="Take photo" busy={up.busy === "photo"} accept="image/*" capture multiple={false} onFiles={(l) => add("photo", l)} icon={<Camera size={12} />} />
                  <Pick label="Add photos" busy={up.busy === "photo"} accept="image/*" onFiles={(l) => add("photo", l)} /></div>
              </div>
            )}
          </section>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-[12.5px] text-muted">RKB is told the moment you send. You can see every step of the bill below.</div>
            <div className="flex gap-2">
              {d.entry_id && <button type="button" className={BTN_PLAIN} onClick={() => setD(blank())}>Start a new bill</button>}
              <button type="button" className={BTN_PLAIN} disabled={!!busy} onClick={later}>{busy === "save" ? "Saving…" : "Save and finish later"}</button>
              <button type="button" className={ready && !busy ? BTN_MAIN : BTN_OFF} disabled={!ready || !!busy} onClick={send}>{busy === "send" ? "Sending…" : "Send to RKB"}</button>
            </div>
          </div>
        </div>

        <div className="space-y-3.5">
          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>This bill</h2></div>
            <div className="p-4 text-[13px]">
              <div className="flex justify-between py-1"><span>Work billed</span><span className="tnum">{usd(total)}</span></div>
              {d.kind === "work" && <div className="flex justify-between py-1"><span>Hold-back, 10%</span><span className="tnum">− {usd(held)}</span></div>}
              <div className="mt-1.5 flex justify-between border-t border-line pt-2.5 text-[15px] font-semibold"><span>To be paid now</span><span className="tnum">{usd(r2(total - held))}</span></div>
              {d.kind === "work" && <div className="flex justify-between py-1 text-[12.5px] text-muted"><span>Held until the house is sold</span><span className="tnum">{usd(held)}</span></div>}
              <div className="mt-2 flex justify-between border-t border-line pt-2.5 text-[12.5px] text-muted"><span>Billed after this bill</span><span className="tnum">{usd(r2(home.totals.billed + total))} of {usd(home.totals.proposal)}</span></div>
              <div className="flex justify-between py-1 text-[12.5px] text-muted"><span>Held so far, with this bill</span><span className="tnum">{usd(r2(home.held + held))}</span></div>
            </div>
          </section>
          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>Before you can send</h2></div>
            <div className="space-y-2 p-4">
              {need.map((n, i) => (
                <div key={i} className="grid grid-cols-[16px_1fr] gap-2 text-[12.5px]">
                  {n.ok ? <Check size={14} className="mt-px text-good" /> : <X size={14} className="mt-px text-critical" />}
                  <div>{n.text}{n.sub && <span className="block text-[11.5px] text-muted">{n.sub}</span>}</div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>

      <section className={cn(PANEL, "mt-3.5")}>
        <div className={HEAD}><h2 className={H2}>Your bills on this job <span className="ml-2 text-[12.5px] font-normal text-muted">Entered here · newest first</span></h2></div>
        {home.bills.length === 0 ? <div className="px-4 py-5 text-[13px] text-muted">No bill has been entered here yet.</div> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse">
              <thead><tr><th className={cn(TH, "pl-4")}>Bill</th><th className={TH}>Pay to</th><th className={cn(TH, "text-right")}>Amount</th><th className={TH}>Entered</th><th className={TH}>Where it stands</th><th className={cn(TH, "pr-4")} /></tr></thead>
              <tbody>
                {home.bills.map((b) => (
                  <tr key={b.entry_id}>
                    <td className={cn(TD, "pl-4 font-medium")}>{b.number ? `${b.number} · ` : ""}{b.kind_name}{b.invoice_no ? `, invoice ${b.invoice_no}` : ""}</td>
                    <td className={cn(TD, "text-fg/80")}>{b.pay_to || "—"}</td>
                    <td className={cn(TD, "text-right tnum")}>{usd(b.total)}</td>
                    <td className={cn(TD, "text-fg/80")}>{fmtDate(b.sent_at || b.created_at, "d MMM, HH:mm")}</td>
                    <td className={TD}><State tone={b.stage === "paid" ? "green" : b.stage === "ready" || b.stage === "approved" ? "amber" : undefined}>{b.stands}</State></td>
                    <td className={cn(TD, "pr-4 text-right")}>
                      {b.status === "draft" && <><button type="button" className="mr-3 text-[12.5px] font-medium text-accent hover:underline" onClick={() => open(b)}>Continue</button><button type="button" className="text-[12.5px] font-medium text-muted hover:underline" onClick={() => discard(b)}>Discard</button></>}
                      {b.status === "sent" && b.can_withdraw && <button type="button" className="text-[12.5px] font-medium text-accent hover:underline" onClick={() => back(b)}>Take back to change</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

/* ───────────────────────── Daily logs ───────────────────────── */

function DailyLogs({ pid, property }: { pid: string; property: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["portal-work", pid, "logs"], queryFn: () => api.get<Logs>(`/portal/work/${pid}/logs`) });
  const up = useUpload(pid);
  const [tags, setTags] = React.useState<string[]>([]);
  const [notes, setNotes] = React.useState("");
  const [blocked, setBlocked] = React.useState("");
  const [date, setDate] = React.useState("");
  const [photos, setPhotos] = React.useState<FileRec[]>([]);
  const [busy, setBusy] = React.useState(false);
  const data = q.data;
  if (!data) return <div className="text-[13px] text-muted">Loading…</div>;
  const add = async (l: FileList | null) => { const got = await up.send("photo", l); if (got.length) setPhotos((cur) => [...cur, ...got]); };
  const missing = [tags.length === 0 && "tick which work was done", notes.trim().length < 10 && "say what was done", photos.length < data.min_photos && `${data.min_photos - photos.length} more photo${data.min_photos - photos.length === 1 ? "" : "s"}`].filter(Boolean) as string[];
  const send = async () => {
    setBusy(true);
    try {
      const out = await api.post<Logs>(`/portal/work/${pid}/logs`, { date: date || data.today, tags, notes, blocked, files: photos.map((p) => p.file_id) });
      qc.setQueryData(["portal-work", pid, "logs"], out);
      toast.success("Log sent. RKB has it now.");
      setTags([]); setNotes(""); setBlocked(""); setPhotos([]); setDate("");
    } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  return (
    <>
      <h1 className="text-[19px] font-semibold tracking-tight">Daily logs</h1>
      <div className="text-[13px] text-muted">{property} · one log for each day there is work, written at the site</div>
      <div className="mt-3.5 grid items-start gap-5 lg:grid-cols-[400px_minmax(0,1fr)]">
        <section className={cn(PANEL, "w-full max-w-[420px]")}>
          <div className={HEAD}><h2 className="text-[15px] font-semibold">New daily log</h2>
            <input type="date" aria-label="Day of the log" max={data.today} value={date || data.today} onChange={(e) => setDate(e.target.value)} className="h-8 rounded-[5px] border border-line-strong bg-elev px-2 text-[13px]" /></div>
          <div className="space-y-4 p-4">
            <div><label className="mb-1.5 block text-[12.5px] font-medium text-muted">Which work was done</label>
              <div className="flex flex-wrap gap-1.5">{data.tags.map((t) => { const on = tags.includes(t); return (
                <button key={t} type="button" onClick={() => setTags((cur) => (on ? cur.filter((x) => x !== t) : [...cur, t]))}
                  className={cn("rounded-[5px] border px-2.5 py-1.5 text-[13px]", on ? "border-accent bg-accent-soft font-semibold text-accent" : "border-line-strong")}>{t}</button>); })}</div></div>
            <div><label className="mb-1.5 block text-[12.5px] font-medium text-muted">What was done</label>
              <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} maxLength={4000} placeholder="A sentence or two on what was done today"
                className="w-full rounded-[5px] border border-line-strong bg-elev px-2.5 py-2 text-[14px] leading-normal outline-none focus:border-accent" /></div>
            <div><label className="mb-1.5 block text-[12.5px] font-medium text-muted">Photos · at least {data.min_photos}</label>
              <label className="flex h-[46px] cursor-pointer items-center justify-center gap-2 rounded-md border border-dashed border-line-strong text-[14px] font-semibold text-accent">
                {up.busy ? <Loader2 size={15} className="animate-spin" /> : <Camera size={15} />} Take photo
                <input type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
              </label>
              <div className="mt-2 flex items-center justify-between gap-2"><Thumbs files={photos} onRemove={(id) => setPhotos((cur) => cur.filter((p) => p.file_id !== id))} />
                <Pick label="Add from the phone" accept="image/*" onFiles={add} /></div>
              <div className="mt-1.5 text-[12px] text-muted">{photos.length} photo{photos.length === 1 ? "" : "s"} · the time each was taken is kept with it</div></div>
            <div><label className="mb-1.5 block text-[12.5px] font-medium text-muted">Anything holding the work up? <span className="font-normal text-faint">(optional)</span></label>
              <input value={blocked} onChange={(e) => setBlocked(e.target.value)} maxLength={600} placeholder="Waiting on a delivery, an inspection, a decision…" className={cn(INPUT, "h-10 text-[14px]")} /></div>
            <button type="button" disabled={busy || missing.length > 0} onClick={send}
              className={cn("flex h-[46px] w-full items-center justify-center rounded-md text-[15px] font-semibold", busy || missing.length ? "bg-sunken text-faint" : "bg-accent text-white")}>{busy ? "Sending…" : "Send log"}</button>
            {missing.length > 0 && <div className="text-[12px] text-muted">Still needed: {missing.join(", ")}.</div>}
          </div>
        </section>

        <div className="min-w-0">
          {!data.logged_today && <div className="mb-3 rounded-[4px] border border-l-[3px] border-high/25 border-l-high bg-high-soft/60 px-3 py-2 text-[12.5px]"><b className="font-semibold">No log yet for today.</b> A day with work and no log shows as missing to RKB.</div>}
          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>Logs on this job <span className="ml-2 text-[12.5px] font-normal text-muted">Newest first</span></h2></div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] table-fixed border-collapse">
                <colgroup><col style={{ width: 84 }} /><col style={{ width: 150 }} /><col /><col style={{ width: 76 }} /></colgroup>
                <thead><tr><th className={cn(TH, "pl-4")}>Date</th><th className={TH}>Written by</th><th className={TH}>What was done</th><th className={cn(TH, "pr-4 text-right")}>Photos</th></tr></thead>
                <tbody>{data.logs.map((l) => (
                  <tr key={l.log_id}><td className={cn(TD, "whitespace-nowrap pl-4 text-fg/80")}>{fmtDate(l.date ? String(l.date).slice(0, 10) : null, "d MMM")}</td><td className={cn(TD, "whitespace-nowrap text-fg/80")}>{l.by}</td>
                    <td className={cn(TD, "truncate font-medium")} title={l.notes}>{l.notes || l.title}</td><td className={cn(TD, "pr-4 text-right tnum")}>{l.photos}</td></tr>))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </>
  );
}

/* ───────────────────────── Change orders ───────────────────────── */

type CoLine = { title: string; kind: string; cost: string; price: string };

function ChangeOrders({ pid, property }: { pid: string; property: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["portal-work", pid, "cos"], queryFn: () => api.get<Cos>(`/portal/work/${pid}/change-orders`) });
  const up = useUpload(pid);
  const fresh = () => ({ title: "", group: "", days: "", why: "", approved_by: "", approved_on: "", lines: [{ title: "", kind: "labour", cost: "", price: "" }] as CoLine[], files: [] as FileRec[] });
  const [f, setF] = React.useState(fresh);
  const [busy, setBusy] = React.useState(false);
  const data = q.data;
  if (!data) return <div className="text-[13px] text-muted">Loading…</div>;
  const set = (patch: Partial<ReturnType<typeof fresh>>) => setF((cur) => ({ ...cur, ...patch }));
  const add = async (role: string, l: FileList | null) => { const got = await up.send(role, l); if (got.length) setF((cur) => ({ ...cur, files: [...cur.files, ...got] })); };
  const drop = (id: number) => setF((cur) => ({ ...cur, files: cur.files.filter((x) => x.file_id !== id) }));
  const of = (role: string) => f.files.filter((x) => x.role === role);
  const line = (i: number, patch: Partial<CoLine>) => setF((cur) => ({ ...cur, lines: cur.lines.map((l, n) => (n === i ? { ...l, ...patch } : l)) }));
  const cost = r2(f.lines.reduce((n, l) => n + num(l.cost), 0)), price = r2(f.lines.reduce((n, l) => n + num(l.price), 0));
  const need = [{ ok: f.title.trim().length >= 4 && f.why.trim().length >= 20, text: "A title, and what is changing and why" }, { ok: f.lines.some((l) => l.title.trim() && num(l.cost) > 0), text: "At least one line with its cost" },
    { ok: of("approval").length > 0, text: "The approved change order from Buildertrend" }, { ok: !!f.approved_by.trim() && !!f.approved_on, text: "Who approved it, and when" },
    { ok: of("photo").length >= data.min_photos, text: `At least ${data.min_photos} photos of what was found` }];
  const ready = need.every((n) => n.ok);
  const send = async () => {
    setBusy(true);
    try {
      const out = await api.post<Cos>(`/portal/work/${pid}/change-orders`, { title: f.title, group: f.group, days: Math.round(num(f.days)), why: f.why, approved_by: f.approved_by, approved_on: f.approved_on || null,
        lines: f.lines.filter((l) => l.title.trim() && num(l.cost) > 0).map((l) => ({ title: l.title, kind: l.kind, cost: num(l.cost), price: num(l.price) })), files: f.files.map((x) => x.file_id) });
      qc.setQueryData(["portal-work", pid, "cos"], out);
      toast.success("Sent to RKB. It can be billed once RKB agrees.");
      setF(fresh());
    } catch (e) { toast.error(errText(e)); }
    setBusy(false);
  };
  return (
    <>
      <h1 className="text-[19px] font-semibold tracking-tight">New change order</h1>
      <div className="text-[13px] text-muted">{property} · extra work that is not in the proposal. The homeowner still approves it in Buildertrend; here RKB agrees to pay for it.</div>
      <div className="mt-3.5 grid items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="space-y-3.5">
          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>The change</h2></div>
            <div className="space-y-3 p-4">
              <div className="grid gap-3 sm:grid-cols-[2fr_1.3fr_0.7fr]">
                <div><label className={LABEL}>Title</label><input className={INPUT} value={f.title} onChange={(e) => set({ title: e.target.value })} placeholder="A few words, for example “Sewer line replacement”" /></div>
                <div><label className={LABEL}>Adds to which part of the proposal</label><select className={INPUT} value={f.group} onChange={(e) => set({ group: e.target.value })}><option value="">Choose…</option>{data.groups.map((g) => <option key={g} value={g}>{g}</option>)}</select></div>
                <div><label className={LABEL}>Days added to the job</label><input className={INPUT} inputMode="numeric" value={f.days} onChange={(e) => set({ days: e.target.value })} placeholder="0" /></div>
              </div>
              <div><label className={LABEL}>What is changing, and why it was not in the proposal</label>
                <textarea rows={3} value={f.why} onChange={(e) => set({ why: e.target.value })} placeholder="What was found, what has to be done, and why it could not be seen when the proposal was written."
                  className="w-full rounded-[5px] border border-line-strong bg-elev px-2.5 py-2 text-[13px] leading-normal outline-none focus:border-accent" /></div>
            </div>
          </section>
          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>What it costs</h2><button type="button" className="text-[12.5px] font-medium text-accent hover:underline" onClick={() => set({ lines: [...f.lines, { title: "", kind: "labour", cost: "", price: "" }] })}>Add a line</button></div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] border-collapse">
                <thead><tr><th className={cn(TH, "pl-4")}>Work</th><th className={TH}>Labour or materials</th><th className={cn(TH, "text-right")}>Cost to RKB</th><th className={cn(TH, "pr-4 text-right")}>Price to the homeowner</th></tr></thead>
                <tbody>
                  {f.lines.map((l, i) => (
                    <tr key={i}>
                      <td className={cn(TD, "pl-4")}><input className={cn(INPUT, "h-7")} value={l.title} onChange={(e) => line(i, { title: e.target.value })} placeholder="Describe the work" /></td>
                      <td className={TD}><select className={cn(INPUT, "h-7 w-[130px]")} value={l.kind} onChange={(e) => line(i, { kind: e.target.value })}><option value="labour">Labour</option><option value="materials">Materials</option></select></td>
                      <td className={cn(TD, "text-right")}><input className={cn(INPUT, "h-7 w-[120px] text-right tnum")} inputMode="decimal" value={l.cost} onChange={(e) => line(i, { cost: e.target.value })} placeholder="0.00" /></td>
                      <td className={cn(TD, "pr-4 text-right")}><input className={cn(INPUT, "h-7 w-[120px] text-right tnum")} inputMode="decimal" value={l.price} onChange={(e) => line(i, { price: e.target.value })} placeholder="0.00" /></td>
                    </tr>
                  ))}
                  <tr><td className="h-[38px] bg-sunken/50 pl-4 text-[13px] font-semibold">Total</td><td className="bg-sunken/50" /><td className="bg-sunken/50 px-3 text-right text-[13px] font-semibold tnum">{usd(cost)}</td><td className="bg-sunken/50 pr-4 text-right text-[13px] font-semibold tnum">{usd(price)}</td></tr>
                </tbody>
              </table>
            </div>
          </section>
          <section className={PANEL}>
            <div className={HEAD}><h2 className={H2}>The homeowner&rsquo;s approval <span className="ml-2 text-[12.5px] font-normal text-muted">Done in Buildertrend, shown here</span></h2></div>
            <div className="grid items-center gap-3 px-4 py-2.5 sm:grid-cols-[210px_minmax(0,1fr)_auto]">
              <div className="text-[13px] font-medium">Approved change order<span className="block text-[11.5px] font-normal text-muted">Required · the PDF from Buildertrend with the approval on it</span></div>
              <Chips files={of("approval")} onRemove={drop} /><Pick label="Attach" busy={up.busy === "approval"} accept="application/pdf,image/*" onFiles={(l) => add("approval", l)} icon={<Paperclip size={12} />} />
            </div>
            <div className="grid items-center gap-3 border-t border-line/70 px-4 py-2.5 sm:grid-cols-[210px_minmax(0,1fr)]">
              <div className="text-[13px] font-medium">Approved by, and when</div>
              <div className="grid gap-2 sm:grid-cols-2"><input className={INPUT} value={f.approved_by} onChange={(e) => set({ approved_by: e.target.value })} placeholder="Name" /><input type="date" className={INPUT} value={f.approved_on} onChange={(e) => set({ approved_on: e.target.value })} /></div>
            </div>
            <div className="grid items-center gap-3 border-t border-line/70 px-4 py-2.5 sm:grid-cols-[210px_minmax(0,1fr)_auto]">
              <div className="text-[13px] font-medium">Photos of what was found<span className="block text-[11.5px] font-normal text-muted">At least {data.min_photos}</span></div>
              <Thumbs files={of("photo")} onRemove={drop} />
              <div className="flex items-center gap-3"><Pick label="Take photo" busy={up.busy === "photo"} accept="image/*" capture multiple={false} onFiles={(l) => add("photo", l)} icon={<Camera size={12} />} /><Pick label="Add photos" accept="image/*" onFiles={(l) => add("photo", l)} /></div>
            </div>
          </section>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-[12.5px] text-muted">Until RKB agrees, this change order cannot be put on a bill.</div>
            <button type="button" className={ready && !busy ? BTN_MAIN : BTN_OFF} disabled={!ready || busy} onClick={send}>{busy ? "Sending…" : "Send to RKB"}</button>
          </div>
        </div>
        <section className={PANEL}>
          <div className={HEAD}><h2 className={H2}>Before you can send</h2></div>
          <div className="space-y-2 p-4">{need.map((n, i) => (
            <div key={i} className="grid grid-cols-[16px_1fr] gap-2 text-[12.5px]">{n.ok ? <Check size={14} className="mt-px text-good" /> : <X size={14} className="mt-px text-critical" />}<div>{n.text}</div></div>))}
          </div>
        </section>
      </div>

      {data.waiting.length > 0 && (
        <section className={cn(PANEL, "mt-3.5")}>
          <div className={HEAD}><h2 className={H2}>Sent to RKB <span className="ml-2 text-[12.5px] font-normal text-muted">{data.waiting.length}</span></h2></div>
          <div className="overflow-x-auto"><table className="w-full min-w-[640px] border-collapse">
            <thead><tr><th className={cn(TH, "pl-4")}>No.</th><th className={TH}>Change order</th><th className={cn(TH, "text-right")}>Cost to RKB</th><th className={TH}>Sent</th><th className={cn(TH, "pr-4")}>Where it stands</th></tr></thead>
            <tbody>{data.waiting.map((c) => (
              <tr key={c.co_id}><td className={cn(TD, "pl-4 text-fg/80")}>{c.number}</td><td className={cn(TD, "font-medium")}>{c.title}</td><td className={cn(TD, "text-right tnum")}>{usd(c.cost)}</td><td className={cn(TD, "text-fg/80")}>{fmtDate(c.sent_at, "d MMM, HH:mm")}</td>
                <td className={cn(TD, "pr-4")}><State tone={c.status === "declined" ? "red" : "amber"}>{c.stands}{c.declined_why ? `: ${c.declined_why}` : ""}</State></td></tr>))}
            </tbody></table></div>
        </section>
      )}
      <section className={cn(PANEL, "mt-3.5")}>
        <div className={HEAD}><h2 className={H2}>Approved change orders on this job <span className="ml-2 text-[12.5px] font-normal text-muted">{data.approved.length} · {usd(r2(data.approved.reduce((n, c) => n + c.cost, 0)))} cost to RKB</span></h2></div>
        {data.approved.length === 0 ? <div className="px-4 py-5 text-[13px] text-muted">None on file.</div> : (
          <div className="overflow-x-auto"><table className="w-full min-w-[820px] border-collapse">
            <thead><tr><th className={cn(TH, "pl-4")}>No.</th><th className={TH}>Change order</th><th className={cn(TH, "text-right")}>Cost to RKB</th><th className={cn(TH, "text-right")}>Price to homeowner</th><th className={TH}>Approved in Buildertrend</th><th className={TH}>RKB agreed</th><th className={cn(TH, "pr-4")}>Billed</th></tr></thead>
            <tbody>{data.approved.map((c) => (
              <tr key={c.co_id}><td className={cn(TD, "pl-4 text-fg/80")}>{c.number}</td><td className={cn(TD, "font-medium")}>{c.title}</td><td className={cn(TD, "text-right tnum")}>{usd(c.cost)}</td><td className={cn(TD, "text-right tnum")}>{usd(c.price)}</td>
                <td className={cn(TD, "text-fg/80")}>{fmtDate(c.approved_at ? String(c.approved_at).slice(0, 10) : null, "d MMM")}{c.approved_by ? ` · by ${c.approved_by}` : ""}</td>
                <td className={TD}>{c.rkb_agreed ? <State tone="green">Agreed</State> : <State tone="amber">Not recorded</State>}</td>
                <td className={cn(TD, "pr-4")}>{c.billed_in_full ? <State tone="green">In full</State> : <State>{usd(c.billed)}</State>}</td></tr>))}
            </tbody></table></div>
        )}
      </section>
    </>
  );
}

/* ───────────────────────── the page ───────────────────────── */

export function WorkPage() {
  const qc = useQueryClient();
  const jobs = useQuery({ queryKey: ["portal-work", "jobs"], queryFn: () => api.get<{ jobs: { property_id: string; name: string }[] }>("/portal/work") });
  const [pid, setPid] = React.useState<string>("");
  // A link can open a tab directly: /portal/work#logs on the crew's phones, #cos for change orders.
  const [tab, setTab] = React.useState<"bills" | "logs" | "cos">(() => {
    const h = typeof window === "undefined" ? "" : window.location.hash.slice(1);
    return h === "logs" || h === "cos" ? h : "bills";
  });
  const job = pid || jobs.data?.jobs[0]?.property_id || "";
  const home = useQuery({ queryKey: ["portal-work", job, "home"], queryFn: () => api.get<Home>(`/portal/work/${job}`), enabled: !!job, refetchInterval: 60_000 });
  if (jobs.isLoading) return <div className="text-[13px] text-muted">Loading…</div>;
  if (!job) return <div className="rounded-md border border-line bg-elev px-4 py-6 text-[13.5px] text-muted">No job is open to your account for bills and daily logs yet. Ask RKB to open one.</div>;
  const tabs: [typeof tab, string, number | null][] = [["bills", "Bills", home.data ? home.data.bills.filter((b) => b.status === "sent" && b.stage !== "paid").length : null], ["logs", "Daily logs", null], ["cos", "Change orders", null]];
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-x-3 border-b border-line">
        <div className="flex min-w-0 overflow-x-auto">
          {tabs.map(([k, name, n]) => (
            <button key={k} type="button" onClick={() => setTab(k)} className={cn("-mb-px border-b-2 px-3.5 py-2.5 text-[13px]", tab === k ? "border-accent font-semibold text-fg" : "border-transparent text-muted hover:text-fg")}>
              {name}{n ? <span className="ml-1.5 rounded-[3px] bg-sunken px-1.5 text-[11.5px] text-muted">{n}</span> : null}
            </button>
          ))}
        </div>
        <select aria-label="Job" value={job} onChange={(e) => setPid(e.target.value)} className="mb-1.5 h-8 max-w-full rounded-[5px] border border-line-strong bg-elev px-2 text-[12.5px]">
          {(jobs.data?.jobs || []).map((j) => <option key={j.property_id} value={j.property_id}>{j.name}</option>)}
        </select>
      </div>
      {tab === "bills" && (home.data ? <Bills key={job} home={home.data} refresh={() => qc.invalidateQueries({ queryKey: ["portal-work", job, "home"] })} /> : <div className="text-[13px] text-muted">{home.isError ? "This job could not be opened." : "Loading…"}</div>)}
      {tab === "logs" && <DailyLogs key={job} pid={job} property={home.data?.property || ""} />}
      {tab === "cos" && <ChangeOrders key={job} pid={job} property={home.data?.property || ""} />}
    </div>
  );
}
