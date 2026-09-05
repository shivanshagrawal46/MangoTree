"use client";

/* Money as a ledger, and the daily Wes agenda. Both from Fable 5.1, both
   quote-verified. The rule on every figure here: shown only if a document says
   it; otherwise the words "not established" — never 0, never an estimate. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldAlert, HardHat, ChevronDown, ChevronUp } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, CardHeader, Checkbox, Skeleton, Dialog, DialogContent } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { cn, fmtDate, ago, money, propertyLabel } from "@/lib/utils";
import type { LedgerEntry, LedgerSummary, WesAgendaDoc, WesIssue } from "@/lib/types";

export function Figure({ value, sub, tone, big }: { value: number | null | undefined; sub?: React.ReactNode; tone?: string; big?: boolean }) {
  const established = typeof value === "number";
  return (
    <div>
      <div className={cn("tnum font-semibold tracking-tight", big ? "text-2xl" : "text-lg", established ? tone || "text-fg" : "text-muted text-base font-medium")}>
        {established ? money(value as number) : "not established"}
      </div>
      {sub && <div className="text-[11px] text-faint mt-0.5">{sub}</div>}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  closing_funding: "Closing — funded", closing_allocation: "  ↳ paid out of closing", draw: "Draw", interest_billed: "Interest billed",
  interest_received: "Interest received", principal_received: "Principal received", payoff_received: "Payoff received",
  fee_billed: "Fee billed", fee_received: "Fee received", lien_payoff: "Lien payoff", tax: "Tax", legal: "Legal", other: "Other",
};
const CONF_TONE: Record<string, "good" | "neutral" | "high"> = { confirmed: "good", stated: "neutral", mentioned: "high" };

export function LedgerView({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ledger", pid], queryFn: () => api.get<{ summary: LedgerSummary | null; entries: LedgerEntry[]; names: Record<string, string> }>(`/properties/${pid}/ledger`) });
  const [busy, setBusy] = React.useState<string | null>(null);
  const [showAlloc, setShowAlloc] = React.useState(false);
  const rebuild = async () => {
    setBusy("Fable 5.1 is re-reading the money documents…");
    try {
      const { job_id } = await api.post<{ job_id: string }>(`/ledger/rebuild?property_id=${pid}`);
      await new Promise<void>((r) => subscribeJob(job_id, (ev) => { if (ev.kind === "status") setBusy(ev.data?.text); }, r));
    } catch (e: any) { setBusy(e?.message || "failed"); await new Promise((r) => setTimeout(r, 2000)); }
    setBusy(null); qc.invalidateQueries({ queryKey: ["ledger", pid] }); qc.invalidateQueries({ queryKey: ["property", pid] });
  };
  if (!q.data) return <Skeleton className="h-64" />;
  const s = q.data.summary;
  const names = q.data.names || {};
  const entries = q.data.entries || [];
  const movements = entries.filter((e) => e.kind !== "closing_allocation");
  const allocations = entries.filter((e) => e.kind === "closing_allocation");
  const sevTone = (x: string) => (x === "critical" ? "critical" : x === "high" ? "high" : "neutral");

  return (
    <div className="space-y-4">
      <Card>
        <div className="p-5 flex flex-wrap items-start justify-between gap-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
            <Figure big value={s?.invested} tone="text-money-in" sub="Invested — closing funding + draws, confirmed rows" />
            <Figure big value={s?.owed?.owed_total} sub={s?.owed ? <>Owed to RKB as of <b className="text-fg">{fmtDate(s.owed.as_of)}</b>{(s.owed as any).confidence === "stated" ? <> · <span className="text-high">quoted by email</span></> : null} · <button className="text-accent hover:underline" onClick={() => open({ sha: s.owed!.source_sha, highlight: s.owed!.quote.slice(0, 60) })}>source</button></> : "Owed — no balance statement on file"} />
            <Figure big value={s?.returned} tone="text-money-back" sub="Returned — confirmed receipts" />
            <Figure big value={s?.billed} sub="Billed — interest & fees invoiced, not proof of receipt" />
          </div>
          <div className="flex flex-col items-end gap-2">
            <Button size="sm" variant="ghost" onClick={rebuild} disabled={!!busy}><RefreshCw size={13} className={busy ? "animate-spin" : ""} /> {busy ? "Working…" : "Rebuild with Fable 5.1"}</Button>
            {s?.built_at && <div className="text-[11px] text-faint">built {ago(s.built_at)} · {s.model}</div>}
          </div>
        </div>
        {busy && <div className="px-5 pb-3 text-xs text-accent">{busy}</div>}
        {s?.derived_today && (
          <div className="border-t border-line px-5 py-3 text-xs flex flex-wrap items-center gap-2">
            <Badge tone="info">derived</Badge>
            <span>Owed today would be <b className="tnum">{money(s.derived_today.amount)}</b> if the stated per-diem still applies:</span>
            <span className="text-muted tnum">{s.derived_today.formula}</span>
            <span className="text-faint">— arithmetic, not a document figure.</span>
          </div>
        )}
        {s?.notes && <div className="border-t border-line px-5 py-3 text-sm text-muted leading-relaxed">{s.notes}</div>}
        {!s?.established && (
          <div className="border-t border-line px-5 py-4 text-sm">
            <div className="font-semibold">No authoritative money record for this property</div>
            <div className="text-muted text-xs mt-0.5">A figure will appear once one of these is filed: RKB loan-details workbook, the ALTA/HUD settlement statement, an RKB payoff statement. Upload it in the Files tab and rebuild.</div>
          </div>
        )}
      </Card>

      {(s?.risks?.length || 0) > 0 && (
        <Card>
          <CardHeader title="Risks in the money record" sub="What threatens repayment or the collateral, each with its line in the documents" />
          <ul className="px-5 pb-4 space-y-2">
            {s!.risks.map((r, i) => (
              <li key={i} className="flex gap-3 items-start text-sm">
                <ShieldAlert size={15} className={cn("mt-0.5 shrink-0", r.severity === "critical" ? "text-critical" : r.severity === "high" ? "text-high" : "text-muted")} />
                <div className="min-w-0">
                  <div className="flex items-center gap-2"><span className="font-medium">{r.title}</span><Badge tone={sevTone(r.severity) as any}>{r.severity}</Badge></div>
                  <button onClick={() => open({ sha: r.source_sha, highlight: r.quote.slice(0, 60) })} className="text-xs text-muted italic text-left hover:text-accent mt-0.5">“{r.quote.slice(0, 220)}{r.quote.length > 220 ? "…" : ""}”</button>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        {(s?.discrepancies?.length || 0) > 0 && (
          <Card>
            <CardHeader title="Sources disagree" sub="Both figures shown; nothing chosen for you. For JP Sir to reconcile." />
            <ul className="px-5 pb-4 space-y-3">
              {s!.discrepancies.map((d, i) => (
                <li key={i} className="text-sm">
                  <div className="font-medium">{d.topic}</div>
                  <div className="flex flex-wrap gap-2 mt-1">{d.values.map((v, j) => <button key={j} onClick={() => open({ sha: v.source_sha, highlight: v.quote.slice(0, 60) })} className="rounded-lg bg-sunken px-2.5 py-1 text-xs hover:ring-1 ring-accent"><b className="tnum">{money(v.amount)}</b> <span className="text-muted">· {names[v.source_sha] || v.source_sha.slice(0, 8)}</span></button>)}</div>
                  {d.note && <div className="text-xs text-muted mt-1">{d.note}</div>}
                </li>
              ))}
            </ul>
          </Card>
        )}
        {(s?.gaps?.length || 0) > 0 && (
          <Card>
            <CardHeader title="What the documents do not establish" sub="Each with the document that would settle it" />
            <ul className="px-5 pb-4 space-y-2">
              {s!.gaps.map((g, i) => <li key={i} className="text-sm"><div>{g.missing}</div><div className="text-xs text-accent mt-0.5">→ {g.would_settle}</div></li>)}
            </ul>
          </Card>
        )}
      </div>

      <Card>
        <CardHeader title={`Ledger · ${movements.length} movements`} sub="One row per movement of money or billing, with the exact line it came from. Click a row to open the document at that line." />
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-faint text-left"><tr className="border-b border-line"><th className="px-5 py-2 font-medium">Date</th><th className="py-2 font-medium">What</th><th className="py-2 font-medium">Counterparty</th><th className="py-2 font-medium">Source</th><th className="py-2 font-medium">Confidence</th><th className="py-2 pr-5 font-medium text-right">Amount</th></tr></thead>
            <tbody>
              {movements.map((e, i) => (
                <tr key={i} className="border-b border-line/60 hover:bg-sunken cursor-pointer" onClick={() => open({ sha: e.source_sha, highlight: e.quote.slice(0, 60) })}>
                  <td className="px-5 py-2 tnum text-muted whitespace-nowrap">{e.date ? fmtDate(e.date) : "undated"}</td>
                  <td className="py-2"><div className="font-medium">{KIND_LABEL[e.kind] || e.kind}</div><div className="text-faint truncate max-w-[360px]">{e.description}</div></td>
                  <td className="py-2 text-muted truncate max-w-[180px]">{e.counterparty}</td>
                  <td className="py-2 text-muted truncate max-w-[200px]">{names[e.source_sha] || e.source_sha.slice(0, 10)}</td>
                  <td className="py-2"><Badge tone={CONF_TONE[e.confidence]}>{e.confidence}</Badge></td>
                  <td className={cn("py-2 pr-5 text-right tnum font-semibold", e.direction === "in" ? "text-money-back" : e.direction === "billed" ? "text-muted" : "text-money-in")}>{e.direction === "in" ? "+" : e.direction === "billed" ? "" : "−"}{money(e.amount)}</td>
                </tr>
              ))}
              {movements.length === 0 && <tr><td colSpan={6} className="px-5 py-6 text-center text-muted">No documented movements.</td></tr>}
            </tbody>
          </table>
        </div>
        {allocations.length > 0 && (
          <div className="border-t border-line px-5 py-3">
            <button onClick={() => setShowAlloc((v) => !v)} className="text-xs text-muted flex items-center gap-1 hover:text-fg">{showAlloc ? <ChevronUp size={13} /> : <ChevronDown size={13} />} Where the closing money went — {allocations.length} payees on the settlement statement (not additional money out)</button>
            {showAlloc && <ul className="mt-2 space-y-1">{allocations.map((e, i) => <li key={i} className="text-xs flex justify-between gap-3 cursor-pointer hover:text-accent" onClick={() => open({ sha: e.source_sha, highlight: e.quote.slice(0, 60) })}><span className="truncate">{e.counterparty} <span className="text-faint">— {e.description}</span></span><span className="tnum shrink-0">{money(e.amount)}</span></li>)}</ul>}
          </div>
        )}
      </Card>

      {(s?.sources?.length || 0) > 0 && (
        <Card>
          <CardHeader title="Documents read" sub="Authoritative documents may produce rows; context documents may not." />
          <ul className="px-5 pb-4 grid sm:grid-cols-2 gap-1">
            {s!.sources.map((d, i) => <li key={i} className="text-xs flex items-center gap-2 cursor-pointer hover:text-accent" onClick={() => open({ sha: d.sha256 })}><Badge tone={d.role === "authoritative" ? "accent" : "neutral"}>{d.role === "authoritative" ? "source" : "context"}</Badge><span className="truncate">{d.filename}</span><span className="text-faint tnum ml-auto shrink-0">{fmtDate(d.date, "MMM yy")}</span></li>)}
          </ul>
        </Card>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- Wes agenda */

/* One slim line under the property header: a hard-hat, "Raise with Wes", and
   the day's three issues as chips — coloured dot for urgency, title only.
   Click a chip for the why, the ask, the evidence and the "discussed" tick.
   Takes ~36px of height and no attention until you look at it. */
export function WesAgendaStrip({ pid }: { pid: string }) {
  const { open } = useEvidence();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["wes-agenda", pid], queryFn: () => api.get<WesAgendaDoc>(`/properties/${pid}/wes-agenda`) });
  const [sel, setSel] = React.useState<number | null>(null);
  const d = q.data;
  const mark = async (index: number, discussed: boolean) => {
    if (!d?.day) return;
    await api.post(`/properties/${pid}/wes-agenda/mark`, { day: d.day, index, discussed });
    qc.invalidateQueries({ queryKey: ["wes-agenda", pid] }); qc.invalidateQueries({ queryKey: ["wes-agenda-all"] });
  };
  if (!d || d.issues.length === 0) return null;
  const dot = (u: string) => (u === "critical" ? "bg-critical" : u === "high" ? "bg-high" : "bg-line-strong");
  const closed = (i: WesIssue) => !!(i.discussed || i.resolved || i.reported_done);
  const openCount = d.issues.filter((i) => !closed(i)).length;
  const it = sel != null ? d.issues[sel] : null;
  return (
    <>
      <div className="mt-3 flex items-center gap-2 overflow-x-auto scrollbar-thin">
        <span className="flex items-center gap-2 shrink-0">
          <span className="flex items-center gap-1.5 text-xs font-semibold text-fg"><HardHat size={13} className="text-fg" /> Raise with Wes</span>
          <span className={cn("inline-flex items-center gap-1 rounded-md px-1.5 h-5 text-[10px] font-bold uppercase tracking-wide border",
            openCount ? "bg-white text-critical border-critical/40" : "bg-white text-muted border-line")}>Issues{openCount ? <span className="tnum">{openCount}</span> : null}</span>
        </span>
        {d.issues.map((i, idx) => (
          <button key={idx} onClick={() => setSel(idx)}
            title={i.resolved ? `Resolved by records${i.resolution?.date ? ` on ${fmtDate(i.resolution.date)}` : ""}${i.resolution?.document ? ` — ${i.resolution.document}` : ""}` : i.reported_done ? `Reported done by ${i.reported_done.by_name || i.reported_done.by} — awaiting a record` : undefined}
            className={cn("group flex items-center gap-2 rounded-full border border-line bg-elev pl-2 pr-3 h-7 text-xs whitespace-nowrap hover:border-accent hover:bg-accent-soft/40 transition shrink-0",
              closed(i) && "opacity-50")}>
            <span className={cn("h-2 w-2 rounded-full shrink-0", closed(i) ? "bg-good" : dot(i.urgency), i.urgency === "critical" && !closed(i) && "pulse-new")} />
            <span className={cn("font-medium", closed(i) && "line-through")}>{i.title}</span>
            {i.resolved ? <span className="text-[10px] text-good font-semibold">resolved</span> : i.reported_done ? <span className="text-[10px] text-high font-semibold">reported done</span> : i.discussed ? <CheckCircle2 size={12} className="text-good" /> : null}
          </button>
        ))}
        <span className="text-[11px] text-faint shrink-0 ml-1">{d.day ? fmtDate(d.day, "d MMM") : ""}</span>
      </div>
      <Dialog open={sel != null} onOpenChange={(o) => !o && setSel(null)}>
        {it && (
          <DialogContent title={<span className="flex items-center gap-2"><span className={cn("h-2.5 w-2.5 rounded-full", dot(it.urgency))} />{it.title}</span>}
            description={<span className="capitalize">{it.urgency}{it.carried_from ? " · carried forward from an earlier day" : ""}</span>}>
            <div className="mt-4 space-y-4 text-sm">
              {it.resolved && it.resolution && (
                <div className="rounded-xl bg-good/10 border border-good/30 px-4 py-3">
                  <div className="text-[11px] uppercase tracking-wide text-good mb-1">Resolved{it.resolution.date ? ` · ${fmtDate(it.resolution.date)}` : ""}{it.resolution.verdict === "superseded" ? " · superseded" : ""}</div>
                  {it.resolution.document && <div className="text-xs">{it.resolution.document}</div>}
                  {it.resolution.statement && <div className="text-xs">Stated by {it.resolution.by_name || it.resolution.by}: “{it.resolution.statement}”</div>}
                  {it.resolution.quote && it.resolution.source_sha && <button onClick={() => { setSel(null); open({ sha: it.resolution!.source_sha!, highlight: it.resolution!.quote!.slice(0, 60) }); }} className="text-xs text-muted italic text-left hover:text-accent mt-1">“{it.resolution.quote.slice(0, 200)}” <span className="not-italic text-accent">open →</span></button>}
                  {it.resolution.note && <div className="text-xs text-muted mt-1">{it.resolution.note}</div>}
                </div>
              )}
              {!it.resolved && it.reported_done && (
                <div className="rounded-xl bg-high-soft border border-high/30 px-4 py-3 text-xs">
                  <div className="text-[11px] uppercase tracking-wide text-high mb-1">Reported done · awaiting a record</div>
                  {it.reported_done.by_name || it.reported_done.by} said on {fmtDate(it.reported_done.at)}: “{it.reported_done.statement}”. It will be marked resolved when an email or document confirms it.
                </div>
              )}
              <div><div className="text-[11px] uppercase tracking-wide text-faint mb-1">Why now</div><p className="leading-relaxed">{it.why_now}</p></div>
              <div className="rounded-xl bg-accent-soft/50 border border-accent/20 px-4 py-3"><div className="text-[11px] uppercase tracking-wide text-accent mb-1">Ask Wes</div><p className="font-medium leading-relaxed">{it.ask}</p></div>
              {it.evidence.length > 0 && (
                <div><div className="text-[11px] uppercase tracking-wide text-faint mb-1">From the records</div>
                  <ul className="space-y-1.5">{it.evidence.map((e, j) => <li key={j}><button onClick={() => { setSel(null); open({ sha: e.source_sha, highlight: e.quote.slice(0, 60) }); }} className="text-xs text-muted italic text-left hover:text-accent">“{e.quote.slice(0, 260)}{e.quote.length > 260 ? "…" : ""}” <span className="not-italic text-accent">open →</span></button></li>)}</ul>
                </div>
              )}
              <div className="flex items-center justify-between pt-2 border-t border-line">
                <label className="flex items-center gap-2 cursor-pointer text-sm"><Checkbox checked={it.discussed} onCheckedChange={(v) => mark(sel!, !!v)} size={20} /> Discussed with Wes</label>
                <div className="text-[11px] text-faint">{sel! + 1} of {d.issues.length}</div>
              </div>
            </div>
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}

const URG: Record<string, { edge: string; pill: "critical" | "high" | "neutral" }> = {
  critical: { edge: "border-l-critical", pill: "critical" }, high: { edge: "border-l-high", pill: "high" }, normal: { edge: "border-l-line-strong", pill: "neutral" },
};

export function WesAgendaCard({ pid, compact, showHeader = true }: { pid: string; compact?: boolean; showHeader?: boolean }) {
  const { open } = useEvidence();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["wes-agenda", pid], queryFn: () => api.get<WesAgendaDoc>(`/properties/${pid}/wes-agenda`) });
  const [busy, setBusy] = React.useState<string | null>(null);
  const refresh = async () => {
    setBusy("Fable 5.1 is reading this week's records…");
    try {
      const { job_id } = await api.post<{ job_id: string }>(`/properties/${pid}/wes-agenda/refresh`);
      await new Promise<void>((r) => subscribeJob(job_id, (ev) => { if (ev.kind === "status") setBusy(ev.data?.text); }, r));
    } catch (e: any) { setBusy(e?.message || "failed"); await new Promise((r) => setTimeout(r, 2000)); }
    setBusy(null); qc.invalidateQueries({ queryKey: ["wes-agenda", pid] }); qc.invalidateQueries({ queryKey: ["wes-agenda-all"] });
  };
  const mark = async (index: number, discussed: boolean) => {
    if (!q.data?.day) return;
    await api.post(`/properties/${pid}/wes-agenda/mark`, { day: q.data.day, index, discussed });
    qc.invalidateQueries({ queryKey: ["wes-agenda", pid] }); qc.invalidateQueries({ queryKey: ["wes-agenda-all"] });
  };
  if (!q.data) return <Skeleton className="h-40" />;
  const d = q.data;
  return (
    <Card>
      {showHeader && (
        <CardHeader title={<span className="flex items-center gap-2"><HardHat size={15} className="text-accent" /> To raise with Wes today</span>} sub={d.day ? `Top ${d.issues.length} for ${fmtDate(d.day, "EEEE d MMMM")} · fresh each morning by Fable 5.1 · tick when discussed` : "Not generated yet"}
          right={<Button size="sm" variant="ghost" onClick={refresh} disabled={!!busy}><RefreshCw size={13} className={busy ? "animate-spin" : ""} /> {busy ? "Working…" : "Refresh"}</Button>} />
      )}
      {busy && <div className="px-5 pb-2 text-xs text-accent">{busy}</div>}
      {d.issues.length === 0 ? (
        <div className="px-5 pb-5 text-sm text-muted">{d.quiet ? "Nothing on this property needs Wes today." : d.day ? "No verifiable issue today." : "Press Refresh to generate today's agenda."}{d.note ? ` ${d.note}` : ""}</div>
      ) : (
        <ol className="px-5 pb-4 space-y-3">
          {d.issues.map((it: WesIssue, i: number) => {
            const u = URG[it.urgency] || URG.normal;
            return (
              <li key={i} className={cn("rounded-xl border border-line border-l-4 bg-elev px-4 py-3 transition", it.resolved ? "border-l-good" : u.edge, (it.discussed || it.resolved || it.reported_done) && "opacity-60")}>
                <div className="flex items-start gap-3">
                  <Checkbox checked={it.discussed || !!it.resolved} onCheckedChange={(v) => mark(i, !!v)} className="mt-0.5" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[11px] tnum text-faint">{i + 1}.</span>
                      <span className={cn("text-sm font-semibold", (it.discussed || it.resolved) && "line-through")}>{it.title}</span>
                      {it.resolved ? <Badge tone="good">resolved{it.resolution?.date ? ` · ${fmtDate(it.resolution.date, "MMM d")}` : ""}</Badge>
                        : it.reported_done ? <Badge tone="high">reported done · awaiting record</Badge>
                        : <Badge tone={u.pill}>{it.urgency}</Badge>}
                      {it.carried_from && !it.resolved && <Badge tone="info">carried forward</Badge>}
                    </div>
                    {it.resolved && it.resolution && <p className="text-xs text-good mt-1">Resolved by {it.resolution.document ? `“${it.resolution.document}”` : it.resolution.statement ? `${it.resolution.by_name || it.resolution.by}'s statement` : "the records"}{it.resolution.note ? ` — ${it.resolution.note}` : ""}</p>}
                    {!compact && <p className="text-xs text-muted mt-1 leading-relaxed">{it.why_now}</p>}
                    <p className="text-xs mt-1.5"><span className="text-faint uppercase tracking-wide text-[10px] mr-1.5">Ask Wes</span><span className="font-medium">{it.ask}</span></p>
                    {!compact && it.evidence.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1.5">{it.evidence.map((e, j) => <button key={j} onClick={() => open({ sha: e.source_sha, highlight: e.quote.slice(0, 60) })} className="text-[11px] text-muted italic bg-sunken rounded-md px-2 py-1 hover:text-accent text-left max-w-full truncate">“{e.quote.slice(0, 120)}{e.quote.length > 120 ? "…" : ""}”</button>)}</div>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
}
