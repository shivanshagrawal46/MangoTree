"use client";

/* How the work is going — an AI reading of the contractor's last month of site
   logs and photos. Every remark sits beside the photos it came from, so the
   reader judges with their own eyes. It is a reading of photos the contractor
   chose to post; it is not an inspection, and the page says so. */

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Card, Dialog, DialogContent, Skeleton } from "@/components/ui";
import { useUser } from "@/components/providers";
import { TabBody, SiteLogsTab } from "@/components/brain/tabs";
import { ago, cn, fmtDate } from "@/lib/utils";
import { errText, type ReadingHead } from "@/lib/brain";

type Obs = { kind: "good" | "concern" | "defect" | "safety"; what: string; where: string; photo_ids: number[]; log_date: string; confidence: "high" | "medium" | "low" };
type Work = { work: string; area: string; status: "done" | "in_progress" | "started" | "claimed_not_shown"; dates: string; photo_ids: number[] };
type Analysis = ReadingHead & {
  summary?: string; workmanship_reason?: string; pace_reason?: string; documentation_reason?: string; work?: Work[]; observations?: Obs[];
  mismatches?: { log_says: string; photos_show: string; log_date: string; photo_ids: number[] }[]; not_shown?: string[]; questions?: string[];
  period_days?: number; pulled_at?: string | null; cost_usd?: number;
  done_points?: string[]; raise_points?: string[]; workmanship_short?: string; pace_short?: string; record_short?: string;
};
type Resp = { analysis: Analysis | null; latest_photos: { photo_id: number; date?: string; title?: string }[]; in_buildertrend: boolean; last_log?: string | null; logs_total?: number; pulled_at?: string | null };

type Tone = "good" | "info" | "high" | "critical" | "neutral";
const TONE: Record<Tone, string> = { good: "bg-good-soft text-good", info: "bg-info-soft text-info", high: "bg-high-soft text-high", critical: "bg-critical-soft text-critical", neutral: "bg-sunken text-muted" };
export const VERDICT: Record<string, [string, Tone]> = {
  going_well: ["Going well", "good"], acceptable: ["Acceptable", "info"], concerns: ["Concerns", "high"], stalled: ["Stalled", "critical"], cannot_tell: ["Cannot tell", "neutral"],
  good: ["Good", "good"], on_track: ["On track", "good"], at_risk: ["At risk", "high"], behind: ["Behind", "critical"], thin: ["Thin", "high"], poor: ["Poor", "critical"],
};
const KIND: Record<string, [string, Tone]> = { good: ["Done well", "good"], concern: ["Concern", "high"], defect: ["Defect", "critical"], safety: ["Safety", "critical"] };
const STATUS: Record<string, [string, Tone]> = { done: ["Done", "good"], in_progress: ["In progress", "info"], started: ["Started", "info"], claimed_not_shown: ["Said in a log, in no photo", "high"] };

export function Chip({ value, label }: { value?: string | null; label?: string }) {
  const [text, tone] = VERDICT[value || ""] || [value || "—", "neutral" as Tone];
  return <span className={cn("inline-flex items-center h-[20px] px-2 rounded-md text-[11px] font-semibold whitespace-nowrap", TONE[tone])}>{label ? `${label}: ` : ""}{text}</span>;
}

function Photo({ id, onZoom, className }: { id: number; onZoom: (id: number) => void; className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={`/api/brain/site/photo/${id}`} alt="" loading="lazy" onClick={() => onZoom(id)} className={cn("object-cover rounded-md border border-line cursor-zoom-in hover:opacity-90 bg-sunken", className)} />;
}

function Block({ title, sub, right, children }: { title: string; sub?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode }) {
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

function Verdict({ label, value, reason }: { label: string; value?: string; reason?: string }) {
  return (
    <div className="flex items-baseline gap-2.5 py-[5px]">
      <span className="w-[92px] shrink-0 text-[11.5px] text-muted">{label}</span>
      <span className="w-[92px] shrink-0"><Chip value={value} /></span>
      <span className="min-w-0 text-[12.5px] leading-snug">{reason}</span>
    </div>
  );
}

function Points({ title, tone, items }: { title: string; tone: "good" | "high"; items: string[] }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint mb-1.5">{title}</div>
      <ul className="space-y-1.5">
        {items.map((x, i) => (
          <li key={i} className="flex gap-2 text-[12.5px] leading-snug"><span className={cn("mt-[6px] h-1.5 w-1.5 rounded-full shrink-0", tone === "good" ? "bg-good" : "bg-high")} />{x}</li>
        ))}
      </ul>
    </div>
  );
}

export function SiteWorkTab({ pid }: { pid: string }) {
  const { user } = useUser();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["brain", "site-analysis", pid], queryFn: () => api.get<Resp>(`/brain/properties/${pid}/site/analysis`) });
  const [zoom, setZoom] = React.useState<number | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [allObs, setAllObs] = React.useState(false);
  const [full, setFull] = React.useState(false);       // the long write-up, the work table and the full lists

  const run = () => {
    setBusy(true);
    api.post<{ job_id: string }>(`/brain/properties/${pid}/site/analysis`).then(({ job_id }) => {
      subscribeJob(job_id, (e) => { if (e.kind === "error") toast.error(e.data?.error || "The reading failed"); if (e.kind === "result" && e.data?.none) toast.message("No site log in the last month to read."); },
        () => { setBusy(false); qc.invalidateQueries({ queryKey: ["brain"] }); });
    }).catch((e) => { toast.error(errText(e)); setBusy(false); });
  };

  if (!q.data) return <TabBody><Card className="p-4 space-y-2.5"><Skeleton className="h-16" /><Skeleton className="h-40" /></Card></TabBody>;
  const d = q.data;
  if (!d.in_buildertrend) return <TabBody><Card className="px-6 py-10 text-center"><div className="text-[13.5px] font-semibold">This property has no job in Buildertrend.</div><div className="text-[12px] text-muted mt-1">Site logs, photos and the work analysis appear once the contractor opens a job for it.</div></Card></TabBody>;
  const a = d.analysis;
  const again = user?.role === "ceo" && <button type="button" onClick={run} disabled={busy} className="h-7 px-2.5 rounded-lg border border-line text-[12px] inline-flex items-center gap-1.5 hover:bg-sunken disabled:opacity-60"><RefreshCw size={12} className={cn(busy && "animate-spin")} /> {busy ? "Reading the photos… (1–3 min)" : a ? "Read again" : "Read the last month"}</button>;
  const obs = [...(a?.observations || [])].sort((x, y) => Number(x.kind === "good") - Number(y.kind === "good"));
  const flagged = obs.filter((o) => o.kind !== "good");
  const shownObs = allObs ? obs : (flagged.length ? flagged : obs).slice(0, 5);
  const gallery = a?.best_photos?.length ? a.best_photos.map((b) => ({ id: b.photo_id, caption: b.caption })) : d.latest_photos.map((p) => ({ id: p.photo_id, caption: `${fmtDate(p.date, "d MMM yyyy")} – ${p.title || "daily log"}` }));

  return (
    <TabBody wide>
      {a ? (
        <Block title="How the work is going"
          sub={<>AI reading of {a.stats?.logs} logs and {a.photos_shown} of {a.photos_posted} photos, {fmtDate(a.stats?.first_log, "d MMM")}–{fmtDate(a.stats?.last_log, "d MMM")} · {ago(a.at)} · <b className="font-semibold text-high">not an inspection</b></>}
          right={again}>
          <div className="px-4 py-3">
            <p className="text-[14px] font-semibold leading-snug">{a.headline || a.one_line}</p>
            <div className="mt-2.5 divide-y divide-line/70 border-y border-line/70">
              <Verdict label="Overall" value={a.overall} reason={`${a.stats?.days_with_a_log} days with a log · longest gap ${a.stats?.longest_gap_days} days · last log ${a.stats?.days_since_last_log === 0 ? "today" : a.stats?.days_since_last_log === 1 ? "yesterday" : `${a.stats?.days_since_last_log} days ago`}`} />
              <Verdict label="Workmanship" value={a.workmanship} reason={a.workmanship_short} />
              <Verdict label="Pace" value={a.pace} reason={a.pace_short} />
              <Verdict label="Their record" value={a.documentation} reason={a.record_short} />
            </div>
            {((a.raise_points || []).length > 0 || (a.done_points || []).length > 0) && (
              <div className="mt-3.5 grid gap-x-8 gap-y-4 md:grid-cols-2">
                {(a.raise_points || []).length > 0 && <Points title="To raise with the contractor" tone="high" items={a.raise_points!} />}
                {(a.done_points || []).length > 0 && <Points title="Done this month" tone="good" items={a.done_points!} />}
              </div>
            )}
            <button type="button" onClick={() => setFull((v) => !v)} className="mt-3 text-[11.5px] text-accent hover:underline">{full ? "Hide the detail" : "Show the full write-up and work by area"}</button>
            {full && a.summary && <p className="mt-2 text-[12.5px] leading-[1.65] whitespace-pre-line text-fg/85">{a.summary}</p>}
          </div>
        </Block>
      ) : (
        <Block title="How the work is going" sub={d.last_log ? `No daily log in the last month — the last one is dated ${fmtDate(d.last_log, "d MMM yyyy")}. The photos below are the newest the contractor posted.` : "No daily log has been written for this job."} right={again}>
          <div className="px-4 py-3 text-[12.5px] text-muted">A reading is made when there are site logs from the last month to read.</div>
        </Block>
      )}

      {gallery.length > 0 && (
        <Block title={a?.best_photos?.length ? "Where the work stands — photos" : "Latest photos"} sub="Click a photo to enlarge it">
          <div className="p-3 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2.5">
            {gallery.map((g) => (
              <figure key={g.id} className="min-w-0">
                <Photo id={g.id} onZoom={setZoom} className="w-full aspect-[4/3]" />
                <figcaption className="mt-1 text-[11.5px] leading-snug text-muted line-clamp-2">{g.caption}</figcaption>
              </figure>
            ))}
          </div>
        </Block>
      )}

      {a && obs.length > 0 && (
        <Block title="Seen in the photos" sub={`${flagged.length} to raise · ${obs.length - flagged.length} done well`}
          right={obs.length > shownObs.length || allObs ? <button type="button" onClick={() => setAllObs((v) => !v)} className="text-[12px] text-accent hover:underline">{allObs ? "Show only what to raise" : `Show all ${obs.length}`}</button> : undefined}>
          <ul className="divide-y divide-line">
            {shownObs.map((o, i) => {
              const [label, tone] = KIND[o.kind] || [o.kind, "neutral" as Tone];
              return (
                <li key={i} className="px-4 py-2.5 flex gap-3">
                  <div className="flex gap-1.5 shrink-0">{o.photo_ids.slice(0, 2).map((id) => <Photo key={id} id={id} onZoom={setZoom} className="h-[68px] w-[68px]" />)}</div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className={cn("inline-flex items-center h-[18px] px-1.5 rounded text-[10.5px] font-semibold", TONE[tone])}>{label}</span>
                      <span className="text-[12px] font-medium">{o.where}</span>
                      <span className="text-[11px] text-faint">log of {o.log_date} · {o.confidence} confidence{o.photo_ids.length > 2 ? ` · ${o.photo_ids.length} photos` : ""}</span>
                    </div>
                    <p className="mt-0.5 text-[12.5px] leading-snug line-clamp-2" title={o.what}>{o.what}</p>
                  </div>
                </li>
              );
            })}
          </ul>
        </Block>
      )}

      {a && (a.mismatches || []).length > 0 && (
        <Block title="Where the logs and the photos do not agree">
          <ul className="divide-y divide-line">
            {a.mismatches!.slice(0, full ? 99 : 3).map((m, i) => (
              <li key={i} className="px-4 py-2.5 flex gap-3">
                <div className="flex gap-1.5 shrink-0">{m.photo_ids.slice(0, 2).map((id) => <Photo key={id} id={id} onZoom={setZoom} className="h-[68px] w-[68px]" />)}</div>
                <div className="min-w-0 text-[12.5px] leading-relaxed">
                  <div><span className="text-muted">The log of {m.log_date} says:</span> {m.log_says}</div>
                  <div><span className="text-muted">The photos show:</span> {m.photos_show}</div>
                </div>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {a && (
        <div className="grid gap-3 lg:grid-cols-2 items-start">
          {(a.questions || []).length > 0 && (
            <Block title="Questions to put to the contractor">
              <ol className="px-4 py-3 space-y-1.5 list-decimal list-inside text-[12.5px] leading-snug marker:text-faint">{a.questions!.slice(0, full ? 99 : 4).map((x, i) => <li key={i}>{x}</li>)}</ol>
            </Block>
          )}
          {(a.not_shown || []).length > 0 && (
            <Block title="Not shown in any photo" sub="Unknown — neither good nor bad">
              <ul className="px-4 py-3 space-y-1.5 text-[12.5px] leading-snug">{a.not_shown!.slice(0, full ? 99 : 4).map((x, i) => <li key={i} className="flex gap-2"><span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-line-strong shrink-0" />{x}</li>)}</ul>
            </Block>
          )}
        </div>
      )}

      {a && full && (a.work || []).length > 0 && (
        <Block title="Work in the period" sub="By area, as the logs and photos show it">
          <table className="w-full text-[12.5px]"><tbody>
            {a.work!.map((w, i) => {
              const [label, tone] = STATUS[w.status] || [w.status, "neutral" as Tone];
              return (
                <tr key={i} className="border-b border-line/70 last:border-0 align-top">
                  <td className="px-4 py-2 w-[150px] text-muted">{w.area}</td>
                  <td className="py-2 pr-3">{w.work}<div className="text-[11px] text-faint">{w.dates}</div></td>
                  <td className="py-2 pr-3 whitespace-nowrap"><span className={cn("inline-flex items-center h-[18px] px-1.5 rounded text-[10.5px] font-semibold", TONE[tone])}>{label}</span></td>
                  <td className="py-2 pr-4"><div className="flex gap-1 justify-end">{w.photo_ids.slice(0, 3).map((id) => <Photo key={id} id={id} onZoom={setZoom} className="h-9 w-9" />)}</div></td>
                </tr>
              );
            })}
          </tbody></table>
        </Block>
      )}

      <SiteLogsTab pid={pid} bare />

      <Dialog open={zoom != null} onOpenChange={(o) => !o && setZoom(null)}>
        <DialogContent wide title="Site photo">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {zoom != null && <img src={`/api/brain/site/photo/${zoom}`} alt="" className="max-h-[78vh] mx-auto rounded-lg" />}
        </DialogContent>
      </Dialog>
    </TabBody>
  );
}

/* In the details panel: the verdicts and the newest photos, at a glance. */
export function SiteStrip({ reading, photos, onOpen }: { reading?: ReadingHead | null; photos?: { photo_id: number; date?: string }[]; onOpen: () => void }) {
  const ids = (reading?.best_photos?.length ? reading.best_photos.map((b) => b.photo_id).slice(-4) : (photos || []).map((p) => p.photo_id)).slice(0, 4);
  if (!reading && ids.length === 0) return null;
  return (
    <button type="button" onClick={onOpen} className="w-full text-left rounded-lg border border-line bg-elev px-2.5 py-2 hover:border-accent/40 transition">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[9.5px] font-semibold uppercase tracking-[0.09em] text-faint">{reading ? "How the work is going" : "Latest site photos"}</span>
        <span className="text-[10.5px] text-accent">{reading?.flagged ? `${reading.flagged} to raise · open` : "open"}</span>
      </div>
      {reading && <div className="mt-1.5 flex flex-wrap gap-1"><Chip value={reading.overall} /><Chip value={reading.workmanship} label="Work" /><Chip value={reading.pace} label="Pace" /></div>}
      {ids.length > 0 && (
        <div className="mt-2 grid grid-cols-4 gap-1">
          {ids.map((id) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={id} src={`/api/brain/site/photo/${id}`} alt="" loading="lazy" className="aspect-square w-full object-cover rounded-md border border-line bg-sunken" />
          ))}
        </div>
      )}
      {!reading && photos?.[0]?.date && <div className="mt-1 text-[10.5px] text-faint">newest dated {fmtDate(photos[0].date, "d MMM yyyy")}</div>}
    </button>
  );
}
