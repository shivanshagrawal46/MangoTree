"use client";

/* The details of a property, beside the chat: the four numbers, where it stands,
   the replies that are written, the bills waiting for approval, what is open and
   what happened. Always in the same order. */

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronDown, Gavel, HardHat, Info, Mail, Plus, RefreshCw } from "lucide-react";
import { api, subscribeJob } from "@/lib/api";
import { Badge, Button, Dialog, DialogContent, Input, Select } from "@/components/ui";
import { useUser } from "@/components/providers";
import { DraftCard } from "@/components/brain/draft";
import { sortReplies } from "@/components/brain/replies";
import { ActivityList, ItemRow } from "@/components/brain/file";
import { ago, cn, fmtDate } from "@/lib/utils";
import { daysFrom, recoveryLabel, timeLeft } from "@/components/brain/header";
import { SiteStrip } from "@/components/brain/sitework";
import { OWNER_LABEL, errText, type Bill, type Draft, type KeyNumber, type OpenItem, type PropertyFile, type Stage } from "@/lib/brain";

/* Whole dollars for large figures (an ARV), to the cent for bills. */
export const usd0 = (n?: number | null) => (n == null || Number.isNaN(n) ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }));
/* Amounts are shown to the cent. */
export const usd = (n?: number | null) => (n == null || Number.isNaN(n) ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" }));

export function daysBetween(a: string | null | undefined, b = new Date()) {
  if (!a) return null;
  const d = new Date(a);
  return Number.isNaN(d.getTime()) ? null : Math.round((b.getTime() - d.getTime()) / 86_400_000);
}

/* One section, one look: a small heading with a count, and a body that folds. */
export function Section({ title, count, tone, defaultOpen = true, right, children }: { title: string; count?: number; tone?: "accent" | "high" | "critical"; defaultOpen?: boolean; right?: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const pill = { accent: "bg-accent text-white", high: "bg-high-soft text-high", critical: "bg-critical-soft text-critical" }[tone || "high"];
  return (
    <section className="border-t border-line first:border-t-0">
      <div className="flex items-center gap-2 px-4 h-9">
        <button type="button" onClick={() => setOpen((v) => !v)} className="flex-1 flex items-center gap-2 text-left">
          <ChevronDown size={13} className={cn("text-faint transition", !open && "-rotate-90")} />
          <span className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-muted">{title}</span>
          {count != null && count > 0 && <span className={cn("text-[10px] font-semibold rounded-full px-1.5 h-4 min-w-4 grid place-items-center", pill)}>{count}</span>}
        </button>
        {right}
      </div>
      {open && <div className="px-4 pb-3.5">{children}</div>}
    </section>
  );
}

export function NumberTile({ n }: { n: KeyNumber }) {
  const missing = n.from === "missing" || !n.value;
  // A date reads the same on every property ("13 Dec 2026"), whichever way the workbook wrote it.
  const us = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(n.headline || "");
  const iso = us ? `${us[3]}-${us[1].padStart(2, "0")}-${us[2].padStart(2, "0")}` : /^\d{4}-\d{2}-\d{2}$/.test(n.headline || "") ? n.headline! : null;
  const head = iso ? fmtDate(iso, "d MMM yyyy") : n.headline || "";
  const passed = n.key === "maturity" && iso != null && (daysBetween(iso) ?? 0) > 0;
  const detail = head ? n.detail : n.value;
  return (
    <div className="rounded-lg border border-line bg-elev px-3 py-2 min-w-0" title={[n.detail, n.source ? `Source: ${n.source}${n.as_of ? " — " + n.as_of : ""}` : ""].filter(Boolean).join("\n") || undefined}>
      <div className="text-[9.5px] font-semibold uppercase tracking-[0.09em] text-faint truncate">{n.label}</div>
      {missing ? <div className="mt-0.5 text-[12.5px] text-faint">Not on record</div>
        : head ? <div className={cn("mt-0.5 text-[15px] font-semibold tracking-tight tnum truncate", passed && "text-critical")}>{head}</div> : null}
      {passed && <div className="text-[10px] font-semibold text-critical">Maturity has passed</div>}
      {!missing && !head && detail && <div className="mt-1 text-[12px] leading-snug text-muted line-clamp-3">{detail}</div>}
      {!missing && n.from === "file" && <div className="text-[10px] text-high truncate">from the emails — confirm</div>}
      {!missing && !passed && n.from === "workbook" && head && (n.note || detail) && <div className="text-[10px] text-faint truncate">{n.note && n.note.length < 30 ? n.note : detail}</div>}
    </div>
  );
}

export function Kpi({ label, value, sub, tone, onClick }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "muted"; onClick?: () => void }) {
  return (
    <div className={cn("rounded-lg border border-line bg-elev px-3 py-2 min-w-0", onClick && "cursor-pointer hover:border-accent/40 transition")} onClick={onClick}>
      <div className="text-[9.5px] font-semibold uppercase tracking-[0.09em] text-faint truncate">{label}</div>
      <div className={cn("mt-0.5 tnum truncate", tone === "muted" ? "text-[12.5px] text-faint" : "text-[15px] font-semibold tracking-tight")}>{value}</div>
      {sub && <div className="text-[10px] text-faint truncate">{sub}</div>}
    </div>
  );
}

/* Where the deal is right now: foreclosure if that is where it is, the latest
   thing done on site if it is being built, and the three dates that matter. */
function StatusBlock({ s }: { s: Stage }) {
  const building = s.stage === "construction" || s.stage === "closing";
  const since = daysFrom(s.closing_date);
  const build = timeLeft(s.construction_expected);
  const sale = timeLeft(s.sale_expected);
  const row = (label: string, value: React.ReactNode, title?: string) => (
    <div className="flex gap-2 py-[3px]" title={title}><dt className="w-[86px] shrink-0 text-muted">{label}</dt><dd className="min-w-0 truncate">{value}</dd></div>
  );
  return (
    <div className="px-4 pt-3.5 pb-3 space-y-2">
      {s.recovery && (
        <div className="rounded-lg bg-critical-soft border border-critical/25 px-2.5 py-2 text-[12px] leading-relaxed">
          <div className="flex items-center gap-1.5 font-semibold text-critical"><Gavel size={12} /> {recoveryLabel(s)}</div>
          <p className="mt-0.5 line-clamp-4" title={s.recovery_note}>{s.recovery_note || s.one_line}</p>
        </div>
      )}
      {building && s.site_now ? (
        <div className="rounded-lg bg-accent-soft/60 border border-accent/20 px-2.5 py-2 text-[12px] leading-relaxed">
          <div className="flex items-center gap-1.5 font-semibold text-accent"><HardHat size={12} /> Latest on site</div>
          <p className="mt-0.5 line-clamp-4" title={s.site_now}>{s.site_now}</p>
        </div>
      ) : !s.recovery && s.one_line ? (
        <div className="rounded-lg bg-sunken/70 border border-line px-2.5 py-2 text-[12px] leading-relaxed">
          <div className="flex items-center gap-1.5 font-semibold"><Info size={12} className="text-muted" /> Now</div>
          <p className="mt-0.5 line-clamp-4" title={s.one_line}>{s.one_line}</p>
        </div>
      ) : null}
      <dl className="text-[12px]">
        {row("Closed", s.closing_date ? <>{fmtDate(s.closing_date, "d MMM yyyy")}{since != null && since >= 0 && <span className="text-faint"> · day {since}</span>}</> : <span className="text-faint">no date on record</span>)}
        {row("Construction", !building ? <span className="text-good font-medium">complete</span>
          : s.construction_expected ? <><span className="font-medium">due {fmtDate(s.construction_expected, "d MMM yyyy")}</span>{build && <span className={cn(build.late ? "text-critical font-medium" : "text-muted")}> · {build.long}</span>}</>
            : <span className="text-high">no completion date on record</span>, s.construction_basis)}
        {row("Sale / payoff", s.stage === "sold_repaid" ? <span className="text-good font-medium">sold and repaid</span>
          : s.sale_expected ? <><span className="font-medium">due {fmtDate(s.sale_expected, "d MMM yyyy")}</span>{sale && <span className={cn(sale.late ? "text-critical font-medium" : "text-muted")}> · {sale.long}</span>}</>
            : <span className="text-high">no date on record</span>, s.sale_basis)}
      </dl>
    </div>
  );
}

function DraftLine({ d, onOpen }: { d: Draft; onOpen: () => void }) {
  return (
    <button type="button" onClick={onOpen} className="w-full text-left flex items-start gap-2.5 rounded-xl border border-line bg-elev px-3 py-2.5 hover:border-accent/40 transition">
      <div className="h-7 w-7 rounded-lg bg-accent-soft text-accent grid place-items-center shrink-0"><Mail size={13} /></div>
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-medium leading-snug line-clamp-2">{d.in_reply_to?.subject || d.subject}</div>
        <div className="text-[11.5px] text-muted mt-0.5">From {d.in_reply_to?.from_name || "—"}{d.in_reply_to?.date ? ` · ${fmtDate(d.in_reply_to.date, "d MMM")}` : ""}</div>
        <div className="mt-1 flex flex-wrap gap-1">
          {d.stale?.why && <Badge tone="critical">Partly answered — rewrite</Badge>}
          {!d.stale?.why && !d.verification && <Badge tone="high">Not yet checked</Badge>}
          {!d.stale?.why && (d.verification?.open?.length || 0) > 0 && <Badge tone="high">{d.verification!.open.length} to check</Badge>}
          {!d.stale?.why && d.verification?.ran && !d.verification.open.length && <Badge tone="good">Ready to send</Badge>}
        </div>
      </div>
    </button>
  );
}

export function BillLines({ b }: { b: Bill }) {
  if (!(b.line_items || []).length) return <div className="text-[12px] text-muted">No line items were brought over for this bill.</div>;
  return (
    <>
      <table className="w-full text-[12px]"><tbody>
        {b.line_items!.map((li, i) => (
          <tr key={i} className="align-top border-b border-line/60 last:border-0">
            <td className="py-1.5 pr-3"><div>{li.title || li.cost_code}</div>
              <div className="text-[10.5px] text-faint">{li.cost_code}{li.variance ? ` · variance: ${li.variance}` : ""}</div>
              {li.description && <div className="text-[11px] text-muted mt-0.5">{li.description}</div>}</td>
            <td className="py-1.5 text-right tnum whitespace-nowrap">{usd(li.amount)}</td>
          </tr>
        ))}
      </tbody></table>
      {b.created_by && <div className="mt-1.5 text-[10.5px] text-faint">Entered by {b.created_by}{b.documents ? ` · ${b.documents} document${b.documents === 1 ? "" : "s"} attached in Buildertrend` : ""}</div>}
    </>
  );
}

export function PropertyDetails({ pid, f, days, setDays, onTab }: { pid: string; f: PropertyFile; days: number; setDays: (n: number) => void; onTab: (t: "bills" | "site" | "money" | "arv") => void }) {
  const { user } = useUser();
  const qc = useQueryClient();
  const key = ["brain", "property", pid, days];
  const [openDraft, setOpenDraft] = React.useState<Draft | null>(null);
  const [openBill, setOpenBill] = React.useState<number | null>(null);
  const [adding, setAdding] = React.useState(false);
  const [title, setTitle] = React.useState("");
  const [owner, setOwner] = React.useState("rakesh");
  const [more, setMore] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["brain"] });

  const build = () => {
    setBusy(true);
    api.post<{ job_id: string }>(`/brain/properties/${pid}/rebuild`).then(({ job_id }) => {
      subscribeJob(job_id, (e) => { if (e.kind === "error") toast.error(e.data?.error || "Could not build the file"); }, () => { setBusy(false); refresh(); });
    }).catch((e) => { toast.error(errText(e)); setBusy(false); });
  };
  if (!f.exists) {
    return (
      <div className="p-5">
        <p className="text-[13px] text-muted leading-relaxed">This property&apos;s file has not been opened yet. Opening it reads the recent mail and call notes once, then keeps itself current.</p>
        {user?.role === "ceo" && <Button variant="primary" className="mt-3" onClick={build} disabled={busy}>{busy ? "Reading the record…" : "Open the file"}</Button>}
      </div>
    );
  }

  const close = async (item: OpenItem, status: "done" | "dropped") => {
    qc.setQueryData<PropertyFile>(key, (cur) => cur ? { ...cur, open_items: (cur.open_items || []).filter((i) => i.id !== item.id) } : cur);
    try {
      await api.patch(`/brain/properties/${pid}/items/${item.id}`, { status });
      toast.success(status === "done" ? "Done" : "Removed", { action: { label: "Undo", onClick: async () => { await api.patch(`/brain/properties/${pid}/items/${item.id}`, { status: "open" }); refresh(); } } });
    } catch (e) { toast.error(errText(e)); }
    refresh();
  };
  const add = async () => {
    if (!title.trim()) return;
    await api.post(`/brain/properties/${pid}/items`, { title: title.trim(), owner });
    setTitle(""); setAdding(false); refresh();
  };

  const items = f.open_items || [];
  const me = user?.user_id || "rakesh";
  const mine = items.filter((i) => i.kind !== "waiting" && i.owner === me);
  const team = items.filter((i) => i.kind !== "waiting" && i.owner !== me);
  const waiting = items.filter((i) => i.kind === "waiting");
  const drafts = f.drafts || [];
  const nums = f.numbers?.main || [];
  const extra = f.numbers?.extra || {};
  const acts = f.activity || [];
  const bills = f.site?.in_buildertrend ? f.site.bills : undefined;
  const list = (rows: OpenItem[]) => <ul className="divide-y divide-line -my-1">{rows.map((i) => <ItemRow key={i.id} item={i} onDone={() => close(i, "done")} onDrop={() => close(i, "dropped")} />)}</ul>;

  return (
    <div>
      {/* ------------------------------------------------------- right now */}
      {f.stage && <StatusBlock s={f.stage} />}
      {(f.sitework || (f.latest_photos || []).length > 0) && <div className="px-4 pb-3"><SiteStrip reading={f.sitework} photos={f.latest_photos} onOpen={() => onTab("site")} /></div>}

      {/* ------------------------------------------------------- the numbers */}
      {!f.is_general && (
        <div className={cn("px-4 pb-3.5", !f.stage && "pt-3.5")}>
          <div className="grid grid-cols-2 gap-2">
            {nums.map((n) => <NumberTile key={n.key} n={n} />)}
            {f.arv?.value != null
              ? <Kpi label="ARV" value={usd0(f.arv.value)} onClick={() => onTab("arv")}
                  sub={<>{usd0(f.arv.low)}–{usd0(f.arv.high)}{f.arv.from_contractor ? " · run by the contractor" : ""}</>} />
              : <Kpi label="ARV" value="No analysis run" tone="muted" onClick={() => onTab("arv")} />}
            <Kpi label="Total due" value={extra.total_due?.value || "Not on record"} tone={extra.total_due ? undefined : "muted"} sub={extra.rate ? `rate ${extra.rate.value}` : undefined} onClick={() => onTab("money")} />
          </div>
          <div className="mt-2 flex items-center justify-between text-[10.5px] text-faint">
            <span>{f.numbers?.in_workbooks ? <>From RKB&apos;s loan workbooks</> : <span className="text-high">Not in RKB&apos;s loan workbooks yet</span>}</span>
            <button type="button" onClick={() => onTab("money")} className="text-accent hover:underline">All figures</button>
          </div>
        </div>
      )}

      {/* ---------------------------------------------------- where it stands */}
      <Section title="Where it stands" right={<span className="text-[10.5px] text-faint">updated {ago(f.updated_at)}</span>}>
        <p className="text-[12.5px] leading-relaxed font-medium">{f.headline}</p>
        {f.status && (
          <>
            <p className={cn("mt-1.5 text-[12px] leading-[1.6] text-fg/85 whitespace-pre-line", !more && "line-clamp-3")}>{f.status}</p>
            <button type="button" onClick={() => setMore((v) => !v)} className="mt-1 text-[11.5px] text-accent hover:underline">{more ? "Show less" : "Read the full status"}</button>
          </>
        )}
      </Section>

      {/* ----------------------------------------------------- emails to reply */}
      <Section title="Emails to reply" count={drafts.length} tone="accent">
        {drafts.length === 0 ? <div className="text-[12.5px] text-muted">No email is waiting for a reply.</div>
          : <div className="space-y-2">{sortReplies(drafts).map((d) => <DraftLine key={d.draft_id} d={d} onOpen={() => setOpenDraft(d)} />)}</div>}
      </Section>

      {/* ----------------------------------------------------- bills in review */}
      {bills && (
        <Section title="Bills in review" count={bills.in_review.length} right={<span className="text-[11.5px] font-semibold tnum">{bills.in_review.length ? usd(bills.in_review_amount) : ""}</span>}>
          {bills.in_review.length === 0 ? <div className="text-[12.5px] text-muted">No bill is waiting for approval.</div> : (
            <ul className="space-y-2">
              {bills.in_review.map((x) => (
                <li key={x.bill_id} className="rounded-xl border border-line bg-elev">
                  <button type="button" onClick={() => setOpenBill(openBill === x.bill_id ? null : x.bill_id)} className="w-full text-left px-3 py-2.5">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[13px] font-medium leading-snug">{x.title || x.number}</span>
                      <span className="text-[13.5px] font-semibold tnum shrink-0">{usd(x.amount)}</span>
                    </div>
                    <div className="text-[11.5px] text-muted mt-0.5 truncate">{x.number} · {x.pay_to || "payee not stated"}</div>
                    <div className="text-[11.5px] mt-0.5"><span className={cn(x.past_due ? "text-critical font-medium" : "text-muted")}>Due {fmtDate(x.due_date, "d MMM")}{x.past_due ? " — past due" : ""}</span>
                      {x.approvers?.length ? <span className="text-muted"> · approver: {x.approvers.join(", ")}</span> : null}</div>
                  </button>
                  {openBill === x.bill_id && <div className="border-t border-line px-3 py-2"><BillLines b={x} /></div>}
                </li>
              ))}
            </ul>
          )}
          {bills.held_back && bills.held_back.count > 0 && (
            <div className="mt-2 text-[11.5px] text-muted">Held back so far (10% of each draw, paid at completion): <b className="text-fg font-semibold tnum">{usd(bills.held_back.amount)}</b></div>
          )}
          <button type="button" onClick={() => onTab("bills")} className="mt-2 text-[11.5px] text-accent hover:underline">All {bills.totals.count} bills</button>
        </Section>
      )}

      {/* ------------------------------------------------------------ to-dos */}
      <Section title="Needs you" count={mine.length} right={<button type="button" onClick={() => setAdding((v) => !v)} className="h-7 w-7 grid place-items-center rounded-lg text-faint hover:text-fg hover:bg-sunken" title="Add"><Plus size={14} /></button>}>
        {adding && (
          <div className="flex gap-1.5 mb-2">
            <Input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What needs doing?" onKeyDown={(e) => { if (e.key === "Enter") add(); }} className="h-8 text-[13px]" />
            <Select value={owner} onChange={(e) => setOwner(e.target.value)} className="h-8 text-[12px]">{Object.entries(OWNER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
          </div>
        )}
        {mine.length === 0 ? <div className="text-[12.5px] text-muted">Nothing is waiting on you here.</div> : list(mine)}
      </Section>
      <Section title="With the team" count={team.length} defaultOpen={false}>{team.length ? list(team) : <div className="text-[12.5px] text-muted">Nothing open with JP Sir or Manjunath Sir.</div>}</Section>
      <Section title="Waiting on others" count={waiting.length} defaultOpen={false}>{waiting.length ? list(waiting) : <div className="text-[12.5px] text-muted">Nothing outstanding from anyone else.</div>}</Section>

      {/* ------------------------------------------------------ what happened */}
      <Section title={`Last ${days} days`} count={acts.length} defaultOpen={false} right={
        <div className="flex rounded-md border border-line overflow-hidden text-[10.5px]">
          {[7, 14, 30].map((n) => <button key={n} type="button" onClick={() => setDays(n)} className={cn("px-2 h-6", days === n ? "bg-fg text-bg font-semibold" : "text-muted hover:bg-sunken")}>{n}d</button>)}
        </div>}>
        <ActivityList rows={acts} />
      </Section>

      {(f.watch || []).length > 0 && (
        <Section title="Keep in view" count={f.watch!.length} defaultOpen={false}>
          <ul className="space-y-1.5">{f.watch!.map((w, i) => <li key={i} className="flex gap-2 text-[12.5px] leading-relaxed"><span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-high shrink-0" /><span className="whitespace-pre-line">{w}</span></li>)}</ul>
        </Section>
      )}
      {(f.facts || []).length > 0 && (
        <Section title="Key facts" defaultOpen={false}>
          <dl className="space-y-2">{f.facts!.map((x, i) => <div key={i}><dt className="text-[10.5px] uppercase tracking-wide text-faint">{x.key}</dt><dd className="text-[12.5px] leading-relaxed">{x.value}</dd></div>)}</dl>
        </Section>
      )}
      {(f.closed_items || []).length > 0 && (
        <Section title="Recently closed" defaultOpen={false}>
          <ul className="space-y-1">{f.closed_items!.slice(0, 10).map((i) => <li key={i.id} className="text-[12px] text-muted"><span className="text-good">✓</span> <span className="line-through decoration-line-strong">{i.title}</span>{i.closed_note ? <span className="text-faint"> — {i.closed_note}</span> : null}</li>)}</ul>
        </Section>
      )}
      {user?.role === "ceo" && !f.is_general && (
        <div className="border-t border-line px-5 py-3 text-[11px] text-faint">
          Built from the record {fmtDate(f.built_at, "MMM d")}; updated by every email since.
          <button type="button" onClick={() => { if (window.confirm("Rebuild this file from the records? Open items and waiting drafts are re-derived.")) build(); }} disabled={busy} className="ml-2 inline-flex items-center gap-1 text-accent hover:underline"><RefreshCw size={10} /> {busy ? "Rebuilding…" : "Rebuild"}</button>
        </div>
      )}

      <Dialog open={!!openDraft} onOpenChange={(o) => !o && setOpenDraft(null)}>
        <DialogContent wide className="max-h-[90vh] overflow-y-auto" title="Reply">
          {openDraft && <DraftCard draft={openDraft} showProperty={false} onChange={(next) => { if (next.status !== "ready") setOpenDraft(null); }} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
