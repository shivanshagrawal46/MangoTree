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
          {answer.mode === "fast" ? (
            <Badge tone="info"><Sparkles size={11} /> Fast · GPT-6 Astra alone · no second reader or panel</Badge>
          ) : (
            <>
              <Badge tone={verdict === "approve" ? "good" : verdict === "revise" ? "critical" : "high"}>Panel: {verdict.replace(/_/g, " ") || "—"}{answer.verdict?.revised ? " (revised once)" : ""}</Badge>
              <button onClick={() => setTab(tab === "second" ? "none" : "second")}>
                <Badge tone={sr.error ? "neutral" : srCount ? "info" : "good"}><Sparkles size={11} /> GPT-6 Astra: {sr.error ? "unavailable" : srCount ? `${srCount} note${srCount > 1 ? "s" : ""}` : "agrees"}</Badge>
              </button>
            </>
          )}
          <span className="text-faint flex items-center gap-1 ml-auto"><Clock size={11} /> {Math.round((answer.elapsed_ms || 0) / 1000)}s · {answer.budget?.tool_calls_used ?? "—"} tool calls</span>
        </div>
      </div>

      {/* A ready-to-send draft, when that is what was asked for. */}
      {answer.composed && <DraftBlock text={answer.composed} />}

      {/* Points are numbered so the reader can answer back with "point 2". List
          answers hide the urgency pill: an inventory of invoices is not a list of
          alarms. */}
      <ol className="px-5 pb-3 space-y-2">
        {answer.points.map((p, i) => {
          const u = URGENCY[p.urgency] || URGENCY.normal;
          const listy = answer.shape === "list" || answer.shape === "figure";
          return (
            <motion.li key={i} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}
              className={cn("flex gap-3 rounded-xl border border-l-[3px] bg-bg border-line px-3.5 py-2.5", listy ? "border-l-line-strong" : u.edge)}>
              <span className="shrink-0 tnum text-[12px] font-semibold text-faint w-5 pt-0.5 text-right">{i + 1}.</span>
              <div className="min-w-0 flex-1 text-[13.5px] leading-relaxed">
                <Cited text={p.text + (p.sources.length && !/\[#\d+\]/.test(p.text) ? " " + p.sources.map((s) => `[#${s}]`).join("") : "")} sources={answer.sources} />
              </div>
              {!listy && <span className={cn("shrink-0 mt-0.5 h-5 px-2 rounded-full text-[10px] font-semibold uppercase tracking-wide grid place-items-center", u.pill)}>{u.label}</span>}
            </motion.li>
          );
        })}
      </ol>

      {/* Reconciliation notes ("I dropped my line that…") are review detail, not the
          answer; they live under the Second opinion tab. */}

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
                  {answer.disagreements?.length > 0 && <div className="text-xs"><div className="text-[11px] uppercase tracking-wide text-faint mb-1">What Opus changed after the second read</div><ul className="list-disc ml-4 space-y-0.5">{answer.disagreements.map((d, i) => <li key={i}><Cited text={d} sources={answer.sources} /></li>)}</ul></div>}
                  {sr.answer && <details className="text-xs"><summary className="cursor-pointer text-muted">GPT-6 Astra's independent answer</summary><div className="prose-mt mt-2"><Cited text={sr.answer} sources={answer.sources} /></div></details>}
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

/* A composed email / letter: monospace-free, readable, one click to copy. */
function DraftBlock({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {} };
  const lines = text.split("\n");
  const subject = lines.find((l) => /^subject\s*:/i.test(l));
  return (
    <div className="mx-5 mb-3 rounded-xl border border-line bg-bg">
      <div className="flex items-center justify-between px-4 pt-3 pb-2 border-b border-line">
        <div className="text-[11px] uppercase tracking-wide text-faint">Draft — ready to send</div>
        <button onClick={copy} className="text-[11px] text-accent hover:underline">{copied ? "Copied" : "Copy"}</button>
      </div>
      <div className="px-4 py-3 text-[13.5px] leading-relaxed whitespace-pre-wrap">
        {subject && <div className="font-semibold mb-2">{subject}</div>}
        {lines.filter((l) => l !== subject).join("\n").trim()}
      </div>
    </div>
  );
}

/* What each tool means, in the reader's words. Shown live so a 15-minute run
   reads as a person working through the file, not as a spinner. */
const TOOL_LABEL: Record<string, (s: any) => string> = {
  seed_search: () => "Ran the opening search across every channel",
  search: (s) => `Searched: ${quoteArg(s)}`,
  search_timeframe: (s) => `Searched a date range: ${s.summary?.replace(/^search_timeframe\s*/, "") || ""}`,
  decompose_search: () => "Split the question into sub-questions and searched each",
  fetch_full_document: (s) => `Read a document in full: ${after(s.summary, ":")}`,
  fetch_documents: (s) => `Listed the matching documents: ${after(s.summary, ":")}`,
  enumerate_set: (s) => `Counted a document type across the file: ${after(s.summary, ":")}`,
  timeline: (s) => `Checked the timeline: ${after(s.summary, ":")}`,
  flow_of_funds: (s) => `Traced the money: ${after(s.summary, ":")}`,
  thread_context: (s) => `Read the whole email conversation: ${after(s.summary, ":")}`,
  find_quote: (s) => `Looked for an exact phrase: ${after(s.summary, ":")}`,
  verify_claim: (s) => `Verified a claim against the source — ${after(s.summary, ":")}`,
  check_policy: () => "Checked the firm's rulebook",
  graph_neighbors: (s) => `Followed the people and organisations involved: ${after(s.summary, ":")}`,
  show_passage: () => "Re-read a passage",
  submit_final_answer: () => "Wrote the answer",
};
function after(s: string | undefined, sep: string) { const i = (s || "").indexOf(sep); return i >= 0 ? (s || "").slice(i + 1).trim() : (s || ""); }
function quoteArg(s: any) { const m = /'([^']+)'/.exec(s.summary || ""); return m ? `“${m[1]}”` : after(s.summary, ":"); }
function describeStep(s: any): string {
  if (s.type === "seed") return TOOL_LABEL.seed_search(s) + ` — ${after(s.summary, ":") || s.summary}`;
  if (s.type === "sufficiency_gate") return "Completeness check: first draft held back, checklist of gaps returned";
  if (s.type === "final") return "Final answer accepted";
  if (s.type === "reasoning") return `Thinking: ${s.summary}`;
  const f = TOOL_LABEL[s.tool_name];
  return f ? f(s) : `${s.tool_name}: ${s.summary}`;
}

const PHASES = [["investigate", "Investigate"], ["second_reader", "Second read"], ["reconcile", "Write"], ["panel", "Panel check"]] as const;

/** Live view while a job runs: the phase pipeline, a ticking clock, and every
    step as it happens in plain words, newest at the bottom. */
export function LiveTrace({ events }: { events: SSEEvent[] }) {
  const [now, setNow] = React.useState(Date.now());
  const started = React.useRef(Date.now());
  React.useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, []);
  const listRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight }); }, [events.length]);

  const phaseKey = [...events].reverse().find((e) => e.kind === "phase")?.data?.phase || "investigate";
  const phaseIdx = Math.max(0, PHASES.findIndex(([k]) => k === phaseKey));
  const steps = events.filter((e) => e.kind === "agent_step").map((e) => e.data);
  const sr = events.find((e) => e.kind === "second_reader")?.data;
  const lastT = events.length ? events[events.length - 1].t : 0;
  const elapsed = events.length ? Math.max(lastT, (now - started.current) / 1000) : (now - started.current) / 1000;
  const sinceLast = elapsed - lastT;
  const passages = steps.reduce((n, s) => n + (s.new_indices?.length || 0), 0);
  const mm = Math.floor(elapsed / 60), ss = Math.floor(elapsed % 60);

  const doing = phaseKey !== "investigate"
    ? PHASES[phaseIdx][1] + (phaseKey === "second_reader" ? ": GPT-6 Astra is reading the same evidence independently" : phaseKey === "reconcile" ? ": Opus 5 is writing the short final answer" : ": verifying every figure, skeptic review, verdict")
    : steps.length === 0 ? "Opening search across every channel — first results in about a minute"
    : sinceLast > 15 ? "Deciding the next step from what it has read so far…" : "Reading results…";

  return (
    <div className="rounded-2xl border border-line bg-elev p-4 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-3 text-xs">
        {PHASES.map(([k, label], i) => (
          <div key={k} className="flex items-center gap-1.5">
            <span className={cn("h-2 w-2 rounded-full", i < phaseIdx ? "bg-good" : i === phaseIdx ? "bg-accent animate-pulse" : "bg-line-strong")} />
            <span className={cn(i === phaseIdx ? "text-fg font-medium" : i < phaseIdx ? "text-muted" : "text-faint")}>{label}</span>
            {i < PHASES.length - 1 && <span className="text-faint mx-1">›</span>}
          </div>
        ))}
        <span className="ml-auto tnum text-faint">{mm}:{String(ss).padStart(2, "0")} · {steps.length} steps · {passages} passages read</span>
      </div>
      <div className="mt-2 flex items-center gap-2 text-sm">
        <span className="relative flex h-2.5 w-2.5"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-60" /><span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-accent" /></span>
        <span className="font-medium">{doing}</span>
      </div>
      {steps.length > 0 && (
        <div ref={listRef} className="mt-3 max-h-48 overflow-y-auto rounded-xl bg-sunken/60 px-3 py-2 space-y-1">
          {steps.map((s, i) => (
            <div key={i} className={cn("flex gap-2 text-xs", i === steps.length - 1 ? "text-fg" : "text-muted")}>
              <span className="tnum text-faint w-5 shrink-0 text-right">{s.step_num}</span>
              <span className={cn("flex-1 min-w-0 break-words", s.type === "sufficiency_gate" && "text-high", s.type === "final" && "text-good", s.error && "text-critical")}>{describeStep(s)}{s.new_indices?.length ? <span className="text-accent"> +{s.new_indices.length}</span> : null}</span>
            </div>
          ))}
        </div>
      )}
      {sr && <div className="mt-2 text-xs text-info flex items-center gap-1"><Sparkles size={12} /> GPT-6 Astra read the same evidence: {sr.error ? "unavailable" : `${sr.missed} points missed · ${sr.wrong} challenged · ${sr.disagree} disagree`}</div>}
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  return <div className="prose-mt"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}
