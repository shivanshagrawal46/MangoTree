"use client";

/* The analyst's review and the conversation. Claude Opus 5.5's structured
   reading of the file: verdict, its own value, numbered points, what moves
   the number, then the long material folded under one-line headers (market
   read, rehab read, adjustment rates, comp by comp, risks, next steps). The
   questions whose answers would move the number stay open with an answer
   box each; the chat streams. */
import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertTriangle, Bot, CheckCircle2, ChevronDown, ChevronUp, HelpCircle, Loader2, MessageSquare, RotateCcw, Send } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button, Card, Textarea } from "@/components/arv/ui";
import { cn, money, ago } from "@/lib/utils";
import { arvApi, subscribeArvJob, VERDICT_LABEL, VERDICT_TONE, type AnalysisDoc } from "@/lib/arv";
import { Disclosure, H } from "./bits";
import { Drivers, emphasise, sentences } from "./overview";
import type { TabProps } from "./comps";

const CONF_TONE: Record<string, "good" | "accent" | "high" | "critical"> = { high: "good", good: "accent", moderate: "high", low: "critical", very_low: "critical" };
const SEV_TONE: Record<string, "critical" | "high" | "neutral"> = { critical: "critical", high: "high", watch: "neutral" };
const VERDICT_BADGE: Record<string, "good" | "critical" | "high" | "info"> = { keep: "good", drop: "critical", caution: "high", add: "info" };

export function AnalystTab({ doc, patch, setDoc, running, onRerun }: TabProps & { onRerun: (stages: string[]) => void }) {
  const an = doc.analysis;
  const [answers, setAnswers] = React.useState<Record<string, string>>(() => Object.fromEntries((doc.answers || []).map((a) => [a.question, a.answer])));
  const [savingAns, setSavingAns] = React.useState(false);
  const submitAnswers = async () => {
    const list = Object.entries(answers).filter(([, v]) => v.trim()).map(([q, a]) => ({ question: q, answer: a.trim() }));
    setSavingAns(true);
    try { await patch({ answers: list }); onRerun(["analyst"]); toast.success("Answers saved; the analyst is re-reading the file"); } catch (e: any) { toast.error(e.message); } finally { setSavingAns(false); }
  };
  if (!an) {
    return (
      <Card className="p-6 text-sm text-muted">
        {running ? <span className="flex items-center gap-2"><Loader2 size={14} className="animate-spin" /> The analyst has not finished yet — the review lands here when it does.</span>
          : doc.analysis_error ? <span className="text-critical">The review failed: {doc.analysis_error}. <button className="underline" onClick={() => onRerun(["analyst"])}>Try again</button>.</span>
          : <span>No review yet. <button className="underline" onClick={() => onRerun(["analyst"])}>Run the analyst</button>.</span>}
      </Card>
    );
  }
  const engineArv = doc.valuation?.arv;
  const diff = engineArv ? ((an.arv_opinion - engineArv) / engineArv) * 100 : null;
  const points = an.summary_points?.length ? an.summary_points : sentences(an.summary).slice(0, 6);
  const drivers = (an.value_drivers || []).slice().sort((a, b) => Math.abs(b.impact_usd) - Math.abs(a.impact_usd));
  const notes = an.comp_notes || [];
  const count = (v: string) => notes.filter((n) => n.verdict === v).length;
  const crit = (an.risks || []).filter((r) => r.severity === "critical").length;

  return (
    <div className="grid gap-5 xl:grid-cols-[1.35fr_1fr]">
      <div className="space-y-3 min-w-0">
        {/* ------------------------------------------------ verdict and points */}
        <Card className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-3 min-w-0 flex-1">
              <div className="h-9 w-9 rounded-xl bg-accent-soft text-accent grid place-items-center shrink-0"><Bot size={17} /></div>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  {an.verdict && <Badge tone={VERDICT_TONE[an.verdict] || "neutral"} className="h-6 px-2.5 text-[12px]">{VERDICT_LABEL[an.verdict]}</Badge>}
                  <Badge tone={CONF_TONE[an.confidence] || "neutral"}>{an.confidence.replace("_", " ")} confidence</Badge>
                  <span className="text-[11px] text-muted">AI{an.at ? ` · ${ago(an.at)}` : ""}</span>
                  <button onClick={() => onRerun(["analyst"])} disabled={running} className="text-[11px] text-muted flex items-center gap-1 hover:text-fg"><RotateCcw size={11} /> re-run</button>
                </div>
                <div className="text-[15.5px] font-semibold tracking-tight leading-snug mt-2">{an.headline}</div>
              </div>
            </div>
            <div className="text-right shrink-0">
              <div className="text-[10.5px] uppercase tracking-wide text-muted font-medium">Analyst&apos;s ARV</div>
              <div className="text-2xl font-semibold tracking-tight tnum">{money(an.arv_opinion)}</div>
              <div className="text-[11px] text-muted tnum">{money(an.arv_low, true)} – {money(an.arv_high, true)}{diff !== null ? <span className={cn("ml-1", Math.abs(diff) > 5 ? "text-high" : "text-good")}>({diff > 0 ? "+" : ""}{diff.toFixed(1)}% vs engine)</span> : null}</div>
            </div>
          </div>
          <div className="text-[12px] text-muted mt-3 flex items-start gap-1.5">{an.agrees_with_engine ? <CheckCircle2 size={13} className="text-good mt-0.5 shrink-0" /> : <AlertTriangle size={13} className="text-high mt-0.5 shrink-0" />} <span>{an.confidence_reason}{an.verdict_reason ? ` ${an.verdict_reason}` : ""}</span></div>
          <ol className="mt-4 space-y-2">
            {points.map((p, i) => <li key={i} className="flex items-start gap-3 text-[13px] leading-snug"><span className="mt-[2px] h-5 w-5 rounded-full bg-sunken text-muted grid place-items-center text-[10.5px] font-semibold shrink-0">{i + 1}</span><span>{emphasise(p)}</span></li>)}
          </ol>
          <Reasoning text={an.summary} />
        </Card>

        {drivers.length > 0 && (
          <Card className="p-5">
            <H right={<span className="text-[11px] text-muted">signed effect on the ARV</span>}>What moves the number</H>
            <Drivers drivers={drivers} limit={6} />
          </Card>
        )}

        {/* ------------------------------------------------ the long reads, folded */}
        <Disclosure title="Comp by comp" badge={<span className="flex gap-1">{count("keep") > 0 && <Badge tone="good">{count("keep")} keep</Badge>}{count("caution") > 0 && <Badge tone="high">{count("caution")} caution</Badge>}{count("drop") > 0 && <Badge tone="critical">{count("drop")} drop</Badge>}{count("add") > 0 && <Badge tone="info">{count("add")} add</Badge>}</span>} defaultOpen={count("drop") + count("caution") > 0}>
          <ul className="divide-y divide-line">
            {notes.map((n, i) => {
              const c = doc.comps.find((x) => x.comp_id === n.comp_id);
              return (
                <li key={i} className="py-2.5 grid grid-cols-[auto_1fr] gap-3 text-[12.5px]">
                  <Badge tone={VERDICT_BADGE[n.verdict] || "neutral"} className="mt-0.5">{n.verdict}</Badge>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-x-2"><b>{c?.address?.split(",")[0] || n.comp_id}</b>{c ? <span className="text-muted tnum">{money(c.sale_price, true)}{c.adjustment ? ` → ${money(c.adjustment.adjusted_price, true)}` : ""}{c.weight ? ` · ${c.weight}%` : ""}</span> : null}</div>
                    <Clamp text={n.note} />
                    <div className="flex gap-3 mt-1">
                      {n.suggested_condition && c && n.suggested_condition !== (c.condition || "C3") && <button className="text-accent underline text-[11.5px]" onClick={() => patch({ comps: [{ comp_id: n.comp_id, condition: n.suggested_condition as any }] })}>set condition {n.suggested_condition}</button>}
                      {n.verdict === "drop" && c?.selected && <button className="text-critical underline text-[11.5px]" onClick={() => patch({ comps: [{ comp_id: n.comp_id, included: false }] })}>exclude</button>}
                      {n.verdict === "add" && c && !c.selected && <button className="text-accent underline text-[11.5px]" onClick={() => patch({ comps: [{ comp_id: n.comp_id, included: true }] })}>include</button>}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        </Disclosure>

        {an.adjustment_critique?.length ? (
          <Disclosure title="On the engine's adjustment rates" badge={<span className="text-[11px] text-muted">{an.adjustment_critique.length} rates</span>} preview={an.adjustment_critique[0]?.rate}>
            <table className="w-full text-[12.5px]">
              <thead><tr className="text-[10.5px] uppercase tracking-wide text-muted"><th className="text-left font-medium py-1 pr-3">Rate</th><th className="text-left font-medium py-1 pr-3">Engine</th><th className="text-left font-medium py-1">Analyst</th></tr></thead>
              <tbody>{an.adjustment_critique.map((x, i) => (
                <tr key={i} className="border-t border-line align-top">
                  <td className="py-2 pr-3 font-medium whitespace-nowrap">{x.rate}</td>
                  <td className="py-2 pr-3 text-muted tnum">{x.engine_value || "—"}</td>
                  <td className="py-2"><div>{x.your_view}</div><div className="text-[11.5px] text-muted mt-0.5">{x.why}</div></td>
                </tr>
              ))}</tbody>
            </table>
          </Disclosure>
        ) : null}

        <Disclosure title="Market read" preview={sentences(an.market_read)[0]}><Md text={an.market_read} /></Disclosure>
        <Disclosure title="Rehab read" preview={sentences(an.rehab_read)[0]}><Md text={an.rehab_read} /></Disclosure>
        <Disclosure title={`Maximum offer · analyst ${money(an.mao_opinion, true)} · engine ${money(doc.underwriting?.mao.recommended, true)}`} preview={sentences(an.mao_reason)[0]}><Md text={an.mao_reason} /></Disclosure>
      </div>

      <div className="space-y-3 min-w-0">
        {an.questions?.length ? (
          <Card className="p-5 border-high/40">
            <H right={<Button size="sm" variant="primary" disabled={savingAns || running || !Object.values(answers).some((v) => v.trim())} onClick={submitAnswers}>{savingAns ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} Answer and re-run</Button>}>
              <span className="flex items-center gap-1.5"><HelpCircle size={13} className="text-high" /> Questions that would move the number</span>
            </H>
            <ul className="space-y-3">
              {an.questions.map((q, i) => (
                <li key={i} className="text-[12.5px]">
                  <div className="flex items-start gap-2"><Badge tone={q.impact === "high" ? "critical" : q.impact === "medium" ? "high" : "neutral"} className="mt-0.5 shrink-0">{q.impact}</Badge><div><b>{q.question}</b><div className="text-muted text-[11.5px]">{q.why_it_matters}</div></div></div>
                  <Textarea rows={1} value={answers[q.question] || ""} onChange={(e) => setAnswers((s) => ({ ...s, [q.question]: e.target.value }))} placeholder="Your answer…" className="mt-1.5 text-[12px] min-h-[34px]" />
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        {an.risks?.length ? (
          <Disclosure title={`Risks · ${an.risks.length}`} badge={crit > 0 ? <Badge tone="critical">{crit} critical</Badge> : undefined} preview={an.risks[0]?.title} defaultOpen={crit > 0}>
            <ul className="space-y-2">{an.risks.map((r, i) => <li key={i} className="text-[12.5px] flex items-start gap-2"><Badge tone={SEV_TONE[r.severity] || "neutral"} className="mt-0.5 shrink-0">{r.severity}</Badge><div><b>{r.title}.</b> <span className="text-muted">{r.detail}</span></div></li>)}</ul>
          </Disclosure>
        ) : null}

        {an.next_steps?.length ? (
          <Disclosure title={`Next steps · ${an.next_steps.length}`} preview={an.next_steps[0]}>
            <ol className="space-y-1.5 text-[12.5px]">{an.next_steps.map((s, i) => <li key={i} className="flex gap-2"><span className="text-muted tnum w-4 shrink-0">{i + 1}.</span><span>{s}</span></li>)}</ol>
          </Disclosure>
        ) : null}

        <Chat doc={doc} setDoc={setDoc} />
      </div>
    </div>
  );
}

function Md({ text }: { text: string }) {
  return <div className="prose-mt text-[12.5px] leading-relaxed"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}

function Clamp({ text }: { text: string }) {
  const [open, setOpen] = React.useState(false);
  const long = text.length > 180;
  return (
    <div className="text-[12.5px] text-fg/90 mt-0.5">
      <span>{open || !long ? text : text.slice(0, 180).replace(/\s+\S*$/, "") + "…"}</span>
      {long && <button onClick={() => setOpen((o) => !o)} className="ml-1 text-[11.5px] text-accent hover:underline">{open ? "less" : "more"}</button>}
    </div>
  );
}

function Reasoning({ text }: { text: string }) {
  const [open, setOpen] = React.useState(false);
  if (!text?.trim()) return null;
  return (
    <div className="mt-3 border-t border-line pt-2">
      <button onClick={() => setOpen((o) => !o)} className="text-[11.5px] text-muted hover:text-fg flex items-center gap-1">{open ? <ChevronUp size={12} /> : <ChevronDown size={12} />} {open ? "Hide" : "Show"} the full reasoning</button>
      {open && <div className="prose-mt text-[13px] mt-2 leading-relaxed"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>}
    </div>
  );
}

export function Chat({ doc, setDoc }: { doc: AnalysisDoc; setDoc: (d: AnalysisDoc) => void }) {
  const [q, setQ] = React.useState("");
  const [live, setLive] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const endRef = React.useRef<HTMLDivElement>(null);
  const msgs = doc.chat || [];
  React.useEffect(() => { endRef.current?.scrollIntoView({ block: "nearest" }); }, [msgs.length, live]);
  const ask = async (text?: string) => {
    const question = (text ?? q).trim();
    if (!question || busy) return;
    setBusy(true); setQ(""); setLive("");
    setDoc({ ...doc, chat: [...msgs, { role: "user", content: question, at: new Date().toISOString() }] });
    try {
      const { job_id } = await arvApi.chat(doc.analysis_id, question);
      subscribeArvJob(job_id, (e) => {
        if (e.kind === "delta") setLive((s) => (s || "") + (e.data?.text || ""));
        if (e.kind === "error") toast.error(e.data?.error || "The analyst could not answer");
      }, async () => {
        try { setDoc(await arvApi.get(doc.analysis_id)); } catch {}
        setLive(null); setBusy(false);
      });
    } catch (e: any) { toast.error(e.message); setBusy(false); setLive(null); }
  };
  const suggestions = [
    "Which single comp drives the value most, and what if I drop it?",
    "What ARV would you defend to a lender's appraiser, and why?",
    "Where is the rehab budget most likely to be wrong?",
    "What offer makes this a 15% deal if the ARV comes in 5% light?",
  ];
  return (
    <Card className="p-5 flex flex-col">
      <H><span className="flex items-center gap-1.5"><MessageSquare size={13} /> Ask the analyst about this file</span></H>
      <div className="space-y-3 max-h-[440px] overflow-y-auto pr-1">
        {msgs.length === 0 && live === null && <div className="text-[12px] text-muted">The analyst has the whole file: subject, documents, comps and adjustments, market, rehab, deal. Ask it anything; it streams back.</div>}
        {msgs.map((m, i) => (
          <div key={i} className={cn("text-[12.5px] rounded-xl px-3 py-2", m.role === "user" ? "bg-sunken ml-6" : "bg-accent-soft/40 mr-2")}>
            <div className="text-[10px] uppercase tracking-wide text-muted mb-0.5">{m.role === "user" ? (m.by_name || "You") : "Analyst"} · {ago(m.at)}</div>
            {m.role === "user" ? <div>{m.content}</div> : <div className="prose-mt"><ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown></div>}
          </div>
        ))}
        {live !== null && <div className="text-[12.5px] rounded-xl px-3 py-2 bg-accent-soft/40 mr-2"><div className="text-[10px] uppercase tracking-wide text-muted mb-0.5 flex items-center gap-1">Analyst <Loader2 size={10} className="animate-spin" /></div>{live ? <div className="prose-mt"><ReactMarkdown remarkPlugins={[remarkGfm]}>{live}</ReactMarkdown></div> : <div className="text-muted">thinking…</div>}</div>}
        <div ref={endRef} />
      </div>
      {msgs.length === 0 && <div className="flex flex-wrap gap-1.5 mt-3">{suggestions.map((s) => <button key={s} onClick={() => ask(s)} disabled={busy} className="text-[11px] px-2.5 h-7 rounded-full border border-line text-muted hover:border-accent hover:text-accent transition text-left">{s}</button>)}</div>}
      <div className="mt-3 flex items-end gap-2">
        <Textarea rows={2} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }} placeholder="Ask about a comp, an adjustment, the budget, the offer…" className="text-[12.5px]" />
        <Button variant="primary" size="icon" disabled={busy || !q.trim()} onClick={() => ask()} className="h-10 w-10 rounded-xl shrink-0">{busy ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}</Button>
      </div>
    </Card>
  );
}
