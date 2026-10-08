"use client";

/**
 * The permit register on screen. `PermitBoardView` is the portfolio page:
 * alerts first (expired, failed inspections, estimated expiries), then every
 * property's permits. `PropertyPermits` is the property tab. Every number
 * opens the record it came from; expiry says whether it is stated or estimated.
 */
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ChevronRight, FileBadge, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Card, Empty } from "@/components/ui";
import { useEvidence } from "@/components/evidence";
import { useUser } from "@/components/providers";
import { cn, fmtDate, ago } from "@/lib/utils";
import type { Permit, PermitAlert, PermitBoard } from "@/lib/types";

const LEVEL_TONE: Record<PermitAlert["level"], "critical" | "high" | "info" | "neutral"> = { critical: "critical", high: "high", normal: "info", watch: "neutral" };
const STATUS_TONE = (s?: string | null): "good" | "critical" | "high" | "neutral" => {
  const l = (s || "").toLowerCase();
  if (!l) return "neutral";
  if (l.includes("expired") || l.includes("cancel") || l.includes("denied") || l.includes("revoked")) return "critical";
  if (l.includes("issued") || l.includes("completed") || l.includes("approved")) return "good";
  return "high";
};

export function AlertList({ alerts, withAddress }: { alerts: PermitAlert[]; withAddress?: boolean }) {
  if (!alerts.length) return null;
  return (
    <ul className="space-y-1.5">
      {alerts.map((a, i) => (
        <li key={i} className="flex items-start gap-2 text-[12.5px]">
          <Badge tone={LEVEL_TONE[a.level]} className="mt-0.5 shrink-0">{a.level}</Badge>
          <span className="min-w-0">{withAddress && a.address && <span className="font-medium">{a.address} · </span>}{a.text}</span>
        </li>
      ))}
    </ul>
  );
}

export function PermitRow({ p }: { p: Permit }) {
  const { open } = useEvidence();
  const [more, setMore] = React.useState(false);
  const o = p.official;
  const exp = p.expiry;
  return (
    <div className="rounded-xl border border-line bg-elev">
      <button onClick={() => setMore((v) => !v)} className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-sunken/50 rounded-xl">
        {more ? <ChevronDown size={14} className="mt-1 text-faint shrink-0" /> : <ChevronRight size={14} className="mt-1 text-faint shrink-0" />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[13px] font-semibold">{p.permit_no}</span>
            <span className="text-[12px] text-muted">{p.kind}</span>
            {o.status && <Badge tone={STATUS_TONE(o.status)}>{o.status}</Badge>}
            {!o.status && <Badge tone="neutral">reported only — not on a portal export</Badge>}
            {!p.anchored && <Badge tone="high">property uncertain</Badge>}
          </div>
          <div className="text-[12px] text-muted mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
            {o.type && <span>{o.type}</span>}
            {o.issued && <span>issued {fmtDate(o.issued, "d MMM yyyy")}</span>}
            {!o.issued && o.filed && <span>filed {fmtDate(o.filed, "d MMM yyyy")}</span>}
            {o.as_of && <span>portal export of {fmtDate(o.as_of, "d MMM yyyy")}</span>}
            {exp?.date && <span className={cn("font-medium", exp.basis?.startsWith("estimated") ? "text-high" : "text-fg")}>{exp.basis?.startsWith("stated") ? "expired" : "expiry"} {fmtDate(exp.date, "d MMM yyyy")} <span className="text-faint font-normal">({exp.basis})</span></span>}
            {!exp?.date && exp?.basis && <span className="text-faint">{exp.basis}</span>}
          </div>
          {p.alerts.length > 0 && <div className="mt-2"><AlertList alerts={p.alerts} /></div>}
        </div>
      </button>
      {more && (
        <div className="px-4 pb-4 pt-1 space-y-3 text-[12.5px] border-t border-line">
          {o.description && <div><span className="text-faint">Scope on the permit: </span>{o.description}</div>}
          {o.quote && o.source_sha && <button onClick={() => open({ sha: o.source_sha!, highlight: p.permit_no })} className="text-left text-muted hover:text-accent">Portal export row → “{o.quote.slice(0, 160)}…”</button>}
          {p.inspections.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-faint mb-1">Inspection results (Tertius screenshots)</div>
              <ul className="space-y-1">{p.inspections.map((i, k) => (
                <li key={k} className="flex gap-2"><span className="tnum text-faint w-20 shrink-0">{fmtDate(i.date, "d MMM yyyy")}</span>
                  <button onClick={() => open({ sha: i.source_sha })} className="text-left hover:text-accent"><b>{i.type}</b> — <span className={cn(/approved|passed/i.test(i.outcome) ? "text-good" : "text-critical", "font-medium")}>{i.outcome}</span>{i.note ? ` — ${i.note}` : ""}</button></li>))}
              </ul>
            </div>
          )}
          {p.notices.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-faint mb-1">Inspection notices (Tertius emails)</div>
              <ul className="space-y-1">{p.notices.map((n, k) => (
                <li key={k} className="flex gap-2"><span className="tnum text-faint w-20 shrink-0">{fmtDate(n.notice_date, "d MMM yyyy")}</span>
                  <button onClick={() => open({ sha: n.source_sha })} className="text-left hover:text-accent">{n.type} · event {n.event_no || "—"} on {n.event_date ? fmtDate(n.event_date, "d MMM yyyy") : "—"}{n.accepted_reply ? " (calendar reply)" : ""}{n.stale ? " · re-sent with a past date" : ""}</button></li>))}
              </ul>
            </div>
          )}
          {p.statements.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-faint mb-1">What people said (reported, not official)</div>
              <ul className="space-y-1">{p.statements.slice(0, 6).map((s, k) => (
                <li key={k} className="flex gap-2"><span className="tnum text-faint w-20 shrink-0">{fmtDate(s.date, "d MMM yyyy")}</span>
                  <button onClick={() => open({ sha: s.source_sha, highlight: p.permit_no })} className="text-left hover:text-accent"><b>{s.by}</b>: “{s.quote.slice(0, 220)}{s.quote.length > 220 ? "…" : ""}”</button></li>))}
              </ul>
            </div>
          )}
          <div className="text-[11px] text-faint">{p.jurisdiction_name}. {exp?.rule}</div>
        </div>
      )}
    </div>
  );
}

export function PropertyPermits({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["permits", pid], queryFn: () => api.get<{ items: Permit[] }>(`/properties/${pid}/permits`) });
  const items = q.data?.items || [];
  if (q.isLoading) return <div className="text-sm text-muted">Loading…</div>;
  if (!items.length) return <Empty title="No permit on record for this property" sub="The register reads DOB portal exports, Tertius inspection notices and result screenshots, and what Kelly, Wes and the expeditor wrote. Nothing names a permit number here yet." />;
  const alerts = items.flatMap((p) => p.alerts);
  return (
    <div className="space-y-4">
      {alerts.length > 0 && <Card className="p-4"><div className="text-[11px] uppercase tracking-wide text-faint mb-2 flex items-center gap-1.5"><AlertTriangle size={12} /> Needs attention</div><AlertList alerts={alerts} /></Card>}
      <div className="space-y-2">{items.map((p) => <PermitRow key={p.permit_no} p={p} />)}</div>
      <div className="text-[11px] text-faint">Official status comes from the DOB portal export and Tertius; expiry is an estimate from the jurisdiction's rule unless the portal states it. Every line opens its record.</div>
    </div>
  );
}

export function PermitBoardView() {
  const { user } = useUser();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["permits-board"], queryFn: () => api.get<PermitBoard>("/permits") });
  const [running, setRunning] = React.useState(false);
  const rebuild = useMutation({
    mutationFn: () => api.post<{ job_id: string }>("/permits/rebuild"),
    onSuccess: ({ job_id }) => {
      setRunning(true);
      subscribeJob(job_id, (e) => { if (e.kind === "error") toast.error(e.data?.error || "Rebuild failed"); }, () => { setRunning(false); qc.invalidateQueries({ queryKey: ["permits-board"] }); toast.success("Permit register rebuilt"); });
    },
    onError: (e: any) => toast.error(e.message || "Could not start"),
  });
  const b = q.data;
  const [openProp, setOpenProp] = React.useState<string | null>(null);
  return (
    <div className="p-6 max-w-5xl space-y-5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2"><FileBadge size={18} /> Permits</h1>
          <div className="text-xs text-muted mt-1">{b ? `${b.count} permits across ${b.properties.filter((p) => p.permits.length).length} properties` : "Loading…"}{b?.updated_at ? ` · register rebuilt ${ago(b.updated_at)}` : ""} · rebuilt every morning with the cycle</div>
        </div>
        {user?.role === "ceo" && <Button size="sm" disabled={running || rebuild.isPending} onClick={() => rebuild.mutate()}>{running ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} Rebuild now</Button>}
      </div>
      {b && b.alerts.length > 0 && (
        <Card className="p-4">
          <div className="text-[11px] uppercase tracking-wide text-faint mb-2 flex items-center gap-1.5"><AlertTriangle size={12} /> Needs attention — {b.alerts.filter((a) => a.level === "critical").length} critical, {b.alerts.filter((a) => a.level === "high").length} high</div>
          <AlertList alerts={b.alerts.filter((a) => a.level !== "watch")} withAddress />
          {b.alerts.some((a) => a.level === "watch") && <details className="mt-2"><summary className="text-xs text-muted cursor-pointer hover:text-fg">{b.alerts.filter((a) => a.level === "watch").length} more to watch (old estimated expiries, re-sent notices)</summary><div className="mt-2"><AlertList alerts={b.alerts.filter((a) => a.level === "watch")} withAddress /></div></details>}
        </Card>
      )}
      {b && b.properties.map((p) => (
        <Card key={p.property_id} className="overflow-hidden">
          <button onClick={() => setOpenProp(openProp === p.property_id ? null : p.property_id)} className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-sunken/50">
            {openProp === p.property_id ? <ChevronDown size={14} className="text-faint" /> : <ChevronRight size={14} className="text-faint" />}
            <div className="flex-1 min-w-0"><div className="font-medium text-sm">{p.address}</div><div className="text-[11px] text-faint">{p.jurisdiction}</div></div>
            <div className="text-xs text-muted tnum">{p.permits.length} permit{p.permits.length === 1 ? "" : "s"}</div>
            {p.permits.some((x) => x.alerts.some((a) => a.level === "critical")) && <Badge tone="critical">attention</Badge>}
          </button>
          {openProp === p.property_id && (
            <div className="px-4 pb-4 space-y-2 border-t border-line pt-3">
              {p.permits.length === 0 ? <div className="text-xs text-muted">No permit number on record.</div> : p.permits.map((x) => <PermitRow key={x.permit_no} p={x} />)}
            </div>
          )}
        </Card>
      ))}
      <div className="text-[11px] text-faint">Jurisdictions: DC (DOB / Tertius) for the DC houses, City of Alexandria for Ridge Road, Manatee County for the two Bayshores. The portal export has no expiry column, so expiry is estimated from the jurisdiction's rule and labelled as such; a portal export showing Expired, or a person's explicit statement, overrides it.</div>
    </div>
  );
}
