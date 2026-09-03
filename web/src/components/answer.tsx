"use client";

/* AnswerCard — one short answer, each point coloured by urgency, every number
   clickable to its source. The long form, the second opinion, the panel's
   dissent and the trace are all one click away, never in the way. */

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ShieldCheck, ShieldAlert, Sparkles, ChevronDown, Eye, Bookmark, ListChecks, Clock, Cpu, AlertOctagon, FileDown } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { Cited } from "@/components/evidence";
import { cn, URGENCY, fmtDate } from "@/lib/utils";
import type { Answer } from "@/lib/types";
import type { SSEEvent } from "@/lib/api";

export function AnswerCard({ answer, onSave, onAcceptTasks, compact, pdfHref }: { answer: Answer; onSave?: () => void; onAcceptTasks?: () => void; compact?: boolean; pdfHref?: string }) {
  const [tab, setTab] = React.useState<"none" | "details" | "second" | "trace" | "sources">("none");
  const v = answer.verification || {};
  const verdict = answer.verdict?.verdict || "";
  const sr = answer.second_reader || {};
  const srCount = (sr.missed?.length || 0) + (sr.wrong?.length || 0) + (sr.disagree?.length || 0);
  const worst = answer.points.reduce((acc, p) => Math.min(acc, ["critical", "high", "normal", "info", "good"].indexOf(p.urgency)), 9);
  const tone = ["critical", "high", "normal", "info", "good"][worst] || "normal";

  return (
    <div className={cn("rounded-2xl border bg-elev shadow-[var(--shadow-sm)] overflow-hidden", tone === "critical" ? "border-critical/40" : tone === "high" ? "border-high/40" : "border-line")}>
      <div className={cn("h-1", { critical: "bg-critical", high: "bg-high", normal: "bg-accent", info: "bg-info", good: "bg-good" }[tone])} />
      <div className="px-5 pt-4 pb-3">
        <div className="text-[15px] font-semibold leading-snug tracking-tight"><Cited text={answer.headline} sources={answer.sources} /></div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px]">
          {v.facts ? (
            <Badge tone={v.verified === v.facts ? "good" : "high"}>{v.verified === v.facts ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />} {v.verified}/{v.facts} facts verified</Badge>
          ) : <Badge tone="neutral"><ShieldAlert size={11} /> no facts list</Badge>}
          <Badge tone={verdict === "approve" ? "good" : verdict === "revise" ? "critical" : "high"}>Panel: {verdict.replace(/_/g, " ") || "—"}</Badge>
          <button onClick={() => setTab(tab === "second" ? "none" : "second")}>
            <Badge tone={sr.error ? "neutral" : srCount ? "info" : "good"}><Sparkles size={11} /> GPT-5.6: {sr.error ? "unavailable" : srCount ? `${srCount} note${srCount > 1 ? "s" : ""}` : "agrees"}</Badge>
          </button>
          <span className="text-faint flex items-center gap-1 ml-auto"><Clock size={11} /> {Math.round((answer.elapsed_ms || 0) / 1000)}s · {answer.budget?.tool_calls_used ?? "—"} tool calls</span>
        </div>
      </div>

      <ul className="px-5 pb-3 space-y-2">
        {answer.points.map((p, i) => {
          const u = URGENCY[p.urgency] || URGENCY.normal;
          return (
            <motion.li key={i} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
              className={cn("flex gap-3 rounded-xl border border-l-[3px] bg-bg border-line px-3.5 py-2.5", u.edge)}>
              <div className="min-w-0 flex-1 text-[13.5px] leading-relaxed">
                <Cited text={p.text + (p.sources.length && !/\[#\d+\]/.test(p.text) ? " " + p.sources.map((s) => `[#${s}]`).join("") : "")} sources={answer.sources} />
              </div>
              <span className={cn("shrink-0 mt-0.5 h-5 px-2 rounded-full text-[10px] font-semibold uppercase tracking-wide grid place-items-center", u.pill)}>{u.label}</span>
            </motion.li>
          );
        })}
      </ul>

      {answer.disagreements?.length > 0 && (
        <div className="mx-5 mb-3 rounded-xl border border-high/30 bg-high-soft px-3 py-2 text-xs text-high">
          <div className="font-semibold mb-1 flex items-center gap-1"><AlertOctagon size={12} /> Records disagree / second reader differs</div>
          <ul className="list-disc ml-4 space-y-0.5">{answer.disagreements.map((d, i) => <li key={i}><Cited text={d} sources={answer.sources} /></li>)}</ul>
        </div>
      )}

      {answer.risks?.length > 0 && (
        <div className="mx-5 mb-3 rounded-xl border border-line bg-sunken/60 px-3 py-2 text-xs">
          <div className="font-semibold mb-1 text-muted">Open risks (skeptic, each cited)</div>
          <ul className="list-disc ml-4 space-y-0.5 text-fg/90">{answer.risks.map((r, i) => <li key={i}><Cited text={r} sources={answer.sources} /></li>)}</ul>
        </div>
      )}

      {answer.next_actions?.length > 0 && !compact && (
        <div className="mx-5 mb-3 rounded-xl border border-accent/25 bg-accent-soft/50 px-3 py-2 text-xs">
          <div className="flex items-center justify-between mb-1"><div className="font-semibold text-accent flex items-center gap-1"><ListChecks size={12} /> Suggested next steps</div>{onAcceptTasks && <Button size="sm" variant="soft" onClick={onAcceptTasks}>Review in Tasks</Button>}</div>
          <ul className="space-y-1">{answer.next_actions.map((a, i) => (
            <li key={i} className="flex items-start gap-2"><Badge tone="accent">{a.owner}</Badge><span className="flex-1"><Cited text={a.title} sources={answer.sources} />{a.due && <span className="text-faint"> · by {fmtDate(a.due)}</span>}</span></li>
          ))}</ul>
        </div>
      )}

      <div className="px-5 py-2.5 border-t border-line flex flex-wrap items-center gap-1 text-xs">
        {[["details", "Details"], ["sources", `Sources (${answer.sources?.length || 0})`], ["second", "Second opinion"], ["trace", `How it worked (${answer.steps?.length || 0} steps)`]].map(([k, label]) => (
          <button key={k} onClick={() => setTab(tab === k ? "none" : (k as any))} className={cn("h-7 px-2.5 rounded-lg flex items-center gap-1 transition", tab === k ? "bg-sunken text-fg" : "text-muted hover:text-fg")}>{label}<ChevronDown size={12} className={cn("transition", tab === k && "rotate-180")} /></button>
        ))}
        <span className="flex-1" />
        {pdfHref && <a href={pdfHref} target="_blank" rel="noreferrer"><Button size="sm" variant="ghost"><FileDown size={13} /> PDF</Button></a>}
        {onSave && <Button size="sm" variant="ghost" onClick={onSave}><Bookmark size={13} /> Save</Button>}
      </div>

      <AnimatePresence initial={false}>
        {tab !== "none" && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden border-t border-line bg-sunken/40">
            <div className="px-5 py-4 text-[13px]">
              {tab === "details" && (
                <div>
                  <div className="prose-mt"><Cited text={answer.details || "(no further detail)"} sources={answer.sources} /></div>
                  <div className="mt-4 text-xs text-muted border-t border-line pt-3"><span className="font-semibold">Coverage.</span> {answer.coverage}</div>
                  {answer.degrades?.length > 0 && <div className="mt-2 text-xs text-high">Degraded: {answer.degrades.join("; ")}</div>}
                  <div className="mt-2 text-[11px] text-faint flex items-center gap-1"><Cpu size={11} /> {Object.entries(answer.models || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
                </div>
              )}
              {tab === "second" && (
                <div className="space-y-3">
                  <div className="text-xs text-muted">{answer.second_opinion || (sr.error ? `Second reader unavailable: ${sr.error}` : "")}</div>
                  {(["missed", "wrong", "disagree"] as const).map((k) => sr[k]?.length ? (
                    <div key={k}><div className="text-[11px] uppercase tracking-wide text-faint mb-1">{k === "missed" ? "Points Opus missed" : k === "wrong" ? "Sentences GPT challenged" : "Disagreements"}</div>
                      <ul className="list-disc ml-4 space-y-0.5">{sr[k]!.map((s, i) => <li key={i}><Cited text={s} sources={answer.sources} /></li>)}</ul></div>
                  ) : null)}
                  {sr.answer && <details className="text-xs"><summary className="cursor-pointer text-muted">GPT-5.6's independent answer</summary><div className="prose-mt mt-2"><Cited text={sr.answer} sources={answer.sources} /></div></details>}
                  {answer.verdict?.notes?.length > 0 && <div className="text-xs"><div className="text-[11px] uppercase tracking-wide text-faint mb-1">Panel notes</div><ul className="list-disc ml-4">{answer.verdict.notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>}
                  {answer.verdict?.dissent?.length > 0 && <div className="text-xs text-high"><div className="text-[11px] uppercase tracking-wide mb-1">Dissent</div><ul className="list-disc ml-4">{answer.verdict.dissent.map((n, i) => <li key={i}>{n}</li>)}</ul></div>}
                  {(v.unverified?.length ?? 0) > 0 && <div className="text-xs text-critical"><div className="text-[11px] uppercase tracking-wide mb-1">Not verified byte-for-byte</div><ul className="list-disc ml-4">{(v.unverified || []).map((u: any, i: number) => <li key={i}>{u.claim} <span className="text-faint">({u.verdict})</span></li>)}</ul></div>}
                </div>
              )}
              {tab === "sources" && <SourceList answer={answer} />}
              {tab === "trace" && <Trace steps={answer.steps} />}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function SourceList({ answer }: { answer: Answer }) {
  const cited = new Set<number>();
  const all = [answer.headline, answer.details, ...answer.points.map((p) => p.text), ...answer.points.flatMap((p) => p.sources.map((s) => `[#${s}]`))].join(" ");
  for (const m of all.matchAll(/\[#(\d+)\]/g)) cited.add(Number(m[1]));
  const rows = [...answer.sources].sort((a, b) => Number(cited.has(b.index)) - Number(cited.has(a.index)) || a.index - b.index);
  return (
    <ul className="divide-y divide-line text-xs">
      {rows.slice(0, 80).map((s) => <li key={s.chunk_id} className="py-1.5 flex items-center gap-2"><Cited text={`[#${s.index}]`} sources={answer.sources} /><span className="text-faint tnum w-20">{fmtDate(s.date, "yyyy-MM-dd")}</span><span className="truncate flex-1">{s.citation}</span>{cited.has(s.index) && <Badge tone="accent">cited</Badge>}{s.placement !== "property" && <Badge tone={s.placement === "unplaced" ? "high" : "info"}>{s.placement}</Badge>}</li>)}
    </ul>
  );
}

export function Trace({ steps }: { steps: any[] }) {
  return (
    <ol className="space-y-1 text-xs font-mono">
      {(steps || []).map((s, i) => (
        <li key={i} className="flex gap-2 items-start">
          <span className="text-faint w-6 text-right">{s.step_num}</span>
          <span className={cn("w-28 shrink-0", s.type === "sufficiency_gate" ? "text-high" : s.type === "final" ? "text-good" : "text-muted")}>{s.type === "tool" ? s.tool_name : s.type}</span>
          <span className="flex-1 text-fg/80 break-words">{s.summary}{s.new_indices?.length ? <span className="text-accent"> +{s.new_indices.length}</span> : null}{s.error && <span className="text-critical"> {s.error}</span>}</span>
          <span className="text-faint">{s.elapsed_ms ? `${(s.elapsed_ms / 1000).toFixed(1)}s` : ""}</span>
        </li>
      ))}
    </ol>
  );
}

/** Live view while a job runs. */
export function LiveTrace({ events }: { events: SSEEvent[] }) {
  const phase = [...events].reverse().find((e) => e.kind === "phase")?.data?.label || "Starting…";
  const steps = events.filter((e) => e.kind === "agent_step").map((e) => e.data);
  const gate = events.some((e) => e.kind === "agent_sufficiency_gate");
  const sr = events.find((e) => e.kind === "second_reader")?.data;
  const last = steps[steps.length - 1];
  return (
    <div className="rounded-2xl border border-line bg-elev p-4 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 text-sm">
        <span className="relative flex h-2.5 w-2.5"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-60" /><span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-accent" /></span>
        <span className="font-medium">{phase}</span>
        <span className="text-faint text-xs ml-auto tnum">{steps.length} steps{events.length ? ` · ${Math.round(events[events.length - 1].t)}s` : ""}</span>
      </div>
      {last && <div className="mt-2 text-xs text-muted font-mono truncate">{last.tool_name || last.type}: {last.summary}</div>}
      {gate && <div className="mt-2 text-xs text-high flex items-center gap-1"><Eye size={12} /> Completeness check: first answer held, checklist returned</div>}
      {sr && <div className="mt-1 text-xs text-info flex items-center gap-1"><Sparkles size={12} /> GPT-5.6 read the same evidence: {sr.error ? "unavailable" : `${sr.missed} missed · ${sr.wrong} challenged · ${sr.disagree} disagree`}</div>}
      <details className="mt-2 text-xs"><summary className="cursor-pointer text-faint">Show every step</summary><div className="mt-2 max-h-56 overflow-y-auto"><Trace steps={steps} /></div></details>
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  return <div className="prose-mt"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}
