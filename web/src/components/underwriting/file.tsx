"use client";

/* Step 4 · The file. Everything owed before closing, in ONE table for the whole
   file. One sentence on top says where it stands; the count buttons filter the
   table; lines are grouped by the person who owes them (or by status). A row is
   one line high and carries a short name and a tag: red "Needed" while a compulsory
   line is open, green "Optional" for a line that never holds the file, green "Done"
   once the paper is uploaded and verified. A line that was sent back shows its
   reason in one red line. Clicking a line opens its detail on the right: what is
   asked, why, what the system checks on the paper, the paper, and RKB's
   confirmation, which works with or without a paper and always asks for RKB's
   password. */

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Paperclip, X } from "lucide-react";
import { subscribeJob } from "@/lib/api";
import { Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { LINE_ORDER, PARTY, PARTY_ORDER, day, isDone, lineState, uw, type Item, type UwFile } from "@/lib/uw";
import { Lab, PageHead, Panel, Tag, input, linkBtn, td, th, type TagKind } from "./bits";
import type { PageProps } from "./workspace";

type Filter = "needed" | "back" | "check" | "done" | "optional" | null;

/* The tag a line carries: red "Needed" while a compulsory line is open, green "Optional" while an
   optional one is, and green "Done" once its paper is uploaded and verified (or its record is in). */
const tagOf = (i: Item): TagKind => { const k = lineState(i); return k === "needed" && !i.required ? "optional" : k; };

function last(i: Item): string {
  const p = (i.files || [])[(i.files || []).length - 1];
  if (i.confirmed) return `${i.confirmed.by_name}${i.confirmed.without_paper ? ", without a paper" : ""} · ${day(i.confirmed.at)}`;
  if (i.status === "rejected") return `Sent back${p ? ` · paper of ${day(p.uploaded_at)}` : ""}`;
  if (i.status === "received") return p ? `Uploaded ${day(p.uploaded_at)} · ${p.uploaded_by_name ?? ""}` : "";
  if (i.status === "ok") return p ? `Passed · ${day(p.uploaded_at)}` : "Filled in";
  return "";
}

/** Upload papers for a line, then follow the reading job and bring the file up to date when it ends. */
export function useUpload(f: UwFile, save: (n: UwFile) => void) {
  const qc = useQueryClient();
  const [reading, setReading] = React.useState<string | null>(null);
  const upload = React.useCallback(async (key: string, files: FileList | null) => {
    if (!files || !files.length) return;
    try {
      const res = await uw.upload(f.file_id, key, Array.from(files));
      save(res); setReading(key);
      subscribeJob(res.job_id, () => {}, async () => {
        setReading(null);
        try { const fresh = await uw.get(f.file_id); save(fresh); const it = fresh.items.find((x) => x.key === key);
          if (it?.status === "ok") toast.success(`${it.name}: read and passed.`);
          else if (it?.status === "rejected") toast.error(`${it.name}: sent back. ${it.reason ?? ""}`, { duration: 9000 });
          else toast.message(`${it?.name ?? "The paper"}: read. RKB confirms this line.`);
        } catch { qc.invalidateQueries({ queryKey: ["uw", "file", f.file_id] }); }
      });
    } catch (e) { toast.error(e instanceof Error ? e.message : "The upload failed"); }
  }, [f.file_id, save, qc]);
  return { upload, reading };
}

export function LinePanel({ f, item, onClose, run, save }: { f: UwFile; item: Item; onClose: () => void; run: PageProps["run"]; save: PageProps["save"] }) {
  const k = lineState(item), done = isDone(item), paper = (item.files || [])[(item.files || []).length - 1];
  const [vals, setVals] = React.useState<Record<string, string>>({});
  const [pw, setPw] = React.useState(""); const [note, setNote] = React.useState(""); const [why, setWhy] = React.useState(""); const [busy, setBusy] = React.useState(false);
  const fileRef = React.useRef<HTMLInputElement>(null);
  const { upload, reading } = useUpload(f, save);
  const rd = paper?.reading;
  const v = (key: string) => vals[key] ?? String(item.values?.[key] ?? "");
  const rkbOnly = item.verify === "rkb";
  const canFill = !f.me.contractor || !rkbOnly;
  const act = async (p: Promise<UwFile>, ok?: string) => { setBusy(true); const r = await run(p, ok); setBusy(false); return r; };
  const confirm = async () => {
    if (!pw) { toast.error("Enter RKB's confirmation password."); return; }
    if (Object.keys(vals).length) { const filled = await act(uw.fill(f.file_id, item.key, vals)); if (!filled) return; }
    const r = await act(uw.confirm(f.file_id, item.key, pw, note || undefined), `${item.name}: confirmed by RKB.`);
    if (r) { setPw(""); setNote(""); }
  };
  return (
    <Panel className="p-3.5 flex flex-col gap-3 lg:sticky lg:top-3 max-h-[calc(100vh-1.5rem)] overflow-y-auto">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0"><h3 className="text-[14px] font-semibold">{item.name}</h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1"><Tag kind={tagOf(item)} /><span className="text-[12px] text-muted">{PARTY[item.party]}{item.foreclosure ? " · Foreclosure file" : ""}</span></div></div>
        <button type="button" aria-label="Close" className="h-7 w-7 grid place-items-center rounded-lg text-muted hover:bg-sunken" onClick={onClose}><X size={15} /></button>
      </div>
      {k === "back" && <div className="bg-critical-soft text-critical rounded-lg px-2.5 py-2 text-[12.5px]"><b>Sent back.</b>{" "}
        {rd && rd.right_paper ? `${rd.checks.filter((c) => c.result !== "met").length} of ${rd.checks.length} checks did not pass. Each is marked below with what must change.` : item.reason}</div>}
      {item.confirmed && <div className="text-[12.5px] text-info">Confirmed by {item.confirmed.by_name}{item.confirmed.without_paper ? " without a paper" : item.confirmed.over_a_failed_reading ? " over the system's reading" : ""}{item.confirmed.note ? `: ${item.confirmed.note}` : ""}. Rakesh Sir sees this at review.</div>}
      <div><Lab>What is asked</Lab><div className="mt-0.5">{item.label}</div></div>
      <div><Lab>Why RKB needs it</Lab><div className="mt-0.5">{item.why}</div></div>
      {item.confirm && !done && <div className="text-[12.5px] text-info">{item.confirm}</div>}
      {item.checks.length > 0 && (
        <div><Lab>What the system checks on the paper</Lab>
          <ul className="mt-1 flex flex-col gap-1 text-[12.5px]">
            {item.checks.map((c, n) => { const r = rd?.checks.find((x) => x.n === n + 1); return (
              <li key={c} className="flex gap-1.5"><span className={cn("shrink-0 w-3.5 text-center font-bold", !r ? "text-faint" : r.result === "met" ? "text-good" : "text-critical")}>{!r ? "·" : r.result === "met" ? "✓" : "!"}</span>
                <span className={cn(!r && "text-muted")}>{c}{r && r.result !== "met" && <span className="block text-critical">{r.note || (r.result === "not_shown" ? "The paper does not show this." : "")}{r.quote ? ` The paper says: “${r.quote}”` : ""}</span>}</span></li>); })}
          </ul></div>
      )}
      {paper && (
        <div><Lab>The paper</Lab>
          <a href={uw.paperUrl(f.file_id, paper.sha256)} target="_blank" rel="noreferrer" className="mt-1 flex items-center gap-2 border border-line rounded-lg px-2.5 py-1.5 text-[12.5px] hover:bg-sunken">
            <Paperclip size={13} className="text-muted shrink-0" /><span className="flex-1 min-w-0 truncate">{paper.filename}</span><span className="text-muted whitespace-nowrap">{day(paper.uploaded_at)} · {paper.uploaded_by_name}</span></a>
          {paper.error && <div className="text-[12px] text-critical mt-1">The system could not read this paper: {paper.error}</div>}
          {rd?.also_noticed?.length ? <div className="mt-1.5 text-[12.5px] bg-high-soft text-high rounded-lg px-2.5 py-1.5"><b>Also on this paper.</b> {rd.also_noticed.join(" ")}</div> : null}
          {rd?.figures?.length ? <div className="mt-1.5 text-[12px] text-muted">{rd.figures.slice(0, 6).map((x) => `${x.label}: ${x.value}`).join(" · ")}</div> : null}
        </div>
      )}
      {item.fields.length > 0 && (
        <div><Lab>{done ? "On record" : "To fill in"}</Lab>
          {item.fields.map((x) => (
            <div key={x.key} className="mt-1.5"><div className="text-[12px] text-muted">{x.label}</div>
              {done || !canFill ? <div className="font-medium">{String(item.values?.[x.key] ?? "") || <span className="text-faint font-normal">Not entered</span>}</div>
                : <input className={input} type={x.type === "date" ? "date" : "text"} value={v(x.key)} onChange={(e) => setVals((s) => ({ ...s, [x.key]: e.target.value }))} autoComplete="off" />}
            </div>
          ))}
        </div>
      )}
      {!done && (
        <div className="flex flex-wrap gap-2">
          {item.needs_file && <><input ref={fileRef} type="file" multiple hidden onChange={(e) => { upload(item.key, e.target.files); e.target.value = ""; }} />
            <Button variant="primary" size="sm" className="h-8" disabled={busy || reading === item.key} onClick={() => fileRef.current?.click()}>{reading === item.key ? "Reading the paper…" : k === "back" ? "Upload again" : "Upload the paper"}</Button></>}
          {!item.needs_file && canFill && item.verify === "filled" && item.fields.length > 0 && <Button variant="primary" size="sm" className="h-8" disabled={busy || !Object.keys(vals).length} onClick={() => act(uw.fill(f.file_id, item.key, vals), "Saved.")}>Save</Button>}
          {!item.needs_file && item.confirm && !item.fields.length && item.verify === "filled" && <Button variant="primary" size="sm" className="h-8" disabled={busy} onClick={() => act(uw.fill(f.file_id, item.key, { _confirmed: true }), "Confirmed.")}>Confirm this</Button>}
        </div>
      )}
      {f.me.can_confirm && paper && !done && (
        <div className="flex gap-1.5"><input className={input} placeholder="Reason for sending it back" value={why} onChange={(e) => setWhy(e.target.value)} />
          <Button size="sm" className="h-8 shrink-0" disabled={busy || !why.trim()} onClick={() => act(uw.sendBack(f.file_id, item.key, why.trim()), "Sent back.")}>Send back</Button></div>
      )}
      {f.me.can_confirm && !done && (
        <div className="border border-line rounded-xl p-2.5 flex flex-col gap-1.5 bg-bg">
          <b className="font-semibold">RKB confirms this line</b>
          <span className="text-[12px] text-muted">{item.needs_file ? "With or without a paper." : "The fields above are the record and are needed."} Asks for RKB&apos;s password every time, and is written to the log with your name.</span>
          {!f.me.confirm_password_set && <span className="text-[12px] text-critical">RKB&apos;s confirmation password has not been set yet.</span>}
          <input className={input} type="password" placeholder="RKB's confirmation password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") confirm(); }} />
          <input className={input} placeholder="Note: where the paper is held, or why" value={note} onChange={(e) => setNote(e.target.value)} />
          <div><Button size="sm" className="h-8" disabled={busy} onClick={confirm}>Confirm</Button></div>
        </div>
      )}
      {f.me.can_confirm && done && <button type="button" className={cn(linkBtn, "self-start font-normal text-[12.5px]")} disabled={busy} onClick={() => act(uw.reopen(f.file_id, item.key), "Reopened.")}>Reopen this line</button>}
    </Panel>
  );
}

export function FilePage({ f, run, save, go }: PageProps) {
  const [sel, setSel] = React.useState<string | null>(null);
  const [filter, setFilter] = React.useState<Filter>(null);
  const [group, setGroup] = React.useState<"person" | "status">("person");
  const [closed, setClosed] = React.useState<Record<string, boolean>>({});
  const all = f.items.filter((i) => i.when === "file");
  const comp = all.filter((i) => i.required);
  const n = (k: string) => comp.filter((i) => (k === "done" ? isDone(i) : lineState(i) === k)).length;
  const optOpen = all.filter((i) => !i.required && !isDone(i)).length;
  const left = comp.length - n("done");
  const w = (k: string) => `${(100 * n(k)) / Math.max(1, comp.length)}%`;
  const shown = all.filter((i) => !filter || (filter === "optional" ? !i.required && !isDone(i) : i.required && (filter === "done" ? isDone(i) : lineState(i) === filter)));
  const sorted = (xs: Item[]) => xs.slice().sort((a, b) => LINE_ORDER[lineState(a)] - LINE_ORDER[lineState(b)]);
  const groups: { key: string; label: string; rows: Item[]; whole: Item[] | null }[] = group === "person"
    ? PARTY_ORDER.map((p) => ({ key: p, label: PARTY[p], rows: shown.filter((i) => i.party === p), whole: all.filter((i) => i.party === p) }))
    : ([["back", "Sent back"], ["needed", "Needed"], ["check", "Being checked"], ["done", "Done"]] as const).map(([k, l]) => ({ key: k, label: l, rows: shown.filter((i) => (k === "done" ? isDone(i) : lineState(i) === k)), whole: null }));
  const item = sel ? f.items.find((i) => i.key === sel) : null;
  const cols = (group === "status" ? 6 : 5) - (item ? 2 : 0);
  const cards: [Exclude<Filter, null>, string, number][] = [["needed", "Needed", n("needed")], ["back", "Sent back", n("back")], ["check", "Being checked", n("check")], ["done", "Done", n("done")], ["optional", "Optional, open", optOpen]];

  return (
    <>
      <PageHead n={4} title="The file" line="Everything owed before closing, in one list. A paper is read by the system and passes, or comes back with the reason. Click a line for its detail."
        holds={<>Holding the next step: <b className="text-fg">{left ? `${left} needed line${left === 1 ? "" : "s"}` : "nothing"}</b></>} />
      <Panel className="px-3.5 py-2.5 flex flex-wrap items-center gap-x-4 gap-y-2.5">
        <span className="font-semibold">{n("done")} of {comp.length} needed lines done. {n("back")} sent back, {n("check")} being checked.</span>
        <span className="flex h-2 rounded-full overflow-hidden bg-sunken min-w-[140px] flex-1"><span className="bg-good" style={{ width: w("done") }} /><span className="bg-high" style={{ width: w("check") }} /><span className="bg-critical" style={{ width: w("back") }} /></span>
        <span className="flex flex-wrap gap-1.5">
          {cards.map(([k, l, v]) => <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(filter === k ? null : k)}
            className={cn("border rounded-lg px-2.5 py-1 flex items-baseline gap-1.5", filter === k ? "border-accent bg-accent-soft" : "border-line bg-elev hover:bg-sunken")}><b className="text-[15px] tnum">{v}</b>{l}</button>)}
        </span>
        <span className="inline-flex border border-line rounded-lg overflow-hidden">
          {(["person", "status"] as const).map((g) => <button key={g} type="button" onClick={() => setGroup(g)} className={cn("px-2.5 py-1 border-r border-line last:border-r-0", group === g ? "bg-accent-soft text-accent font-semibold" : "text-muted")}>{g === "person" ? "By person" : "By status"}</button>)}
        </span>
        {left === 0 && <Button variant="primary" size="sm" className="h-8" onClick={() => go(5)}>Go to the documents</Button>}
      </Panel>
      <div className={cn("grid gap-3 mt-3 items-start", item ? "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_370px]" : "grid-cols-1")}>
        <Panel className="overflow-x-auto">
          <table className={cn("w-full border-collapse", !item && "min-w-[720px]")}>
            <thead><tr>
              <th className={th}>Line</th>
              {group === "status" && <th className={th}>Owed by</th>}<th className={th}>Status</th>
              {!item && <><th className={th}>Last</th><th className={th}>Paper</th></>}
              <th className={cn(th, "text-right")}>{filter && <button type="button" className={cn(linkBtn, "normal-case tracking-normal")} onClick={() => setFilter(null)}>Clear the filter</button>}</th>
            </tr></thead>
            <tbody>
              {groups.filter((g) => g.rows.length).map((g) => {
                const d = g.whole ? g.whole.filter(isDone).length : 0, bk = g.whole ? g.whole.filter((i) => lineState(i) === "back").length : 0;
                const isClosed = closed[g.key] ?? !!(g.whole && !filter && d === g.whole.length);
                return (
                  <React.Fragment key={g.key}>
                    <tr className="cursor-pointer" onClick={() => setClosed((c) => ({ ...c, [g.key]: !isClosed }))}>
                      <td colSpan={cols} className="px-3 h-[34px] bg-sunken border-b border-line font-semibold">{isClosed ? "▸" : "▾"} {g.label}
                        <span className="font-normal text-muted ml-2">{g.whole ? <>{d} of {g.whole.length} done{bk > 0 && <> · <span className="text-critical">{bk} sent back</span></>}</> : g.rows.length}</span></td>
                    </tr>
                    {!isClosed && sorted(g.rows).map((i) => {
                      const k = lineState(i), p = (i.files || [])[(i.files || []).length - 1];
                      return (
                        <tr key={i.key} onClick={() => setSel(sel === i.key ? null : i.key)} className={cn("cursor-pointer", sel === i.key ? "bg-accent-soft" : "hover:bg-sunken")}>
                          <td className={td}><span className="font-medium">{i.name}</span>
                            {k === "back" && <span className="block text-critical text-[12px] truncate max-w-[52ch]">{i.reason}</span>}</td>
                          {group === "status" && <td className={cn(td, "text-muted")}>{PARTY[i.party]}</td>}
                          <td className={td}><Tag kind={tagOf(i)} /></td>
                          {!item && <><td className={cn(td, "text-muted text-[12px] whitespace-nowrap")}>{last(i)}</td>
                            <td className={td}>{p && <span className="flex items-center gap-1 text-muted text-[12px] max-w-[26ch]"><Paperclip size={12} className="shrink-0" /><span className="truncate">{p.filename}</span></span>}</td></>}
                          <td className={cn(td, "text-right whitespace-nowrap")}>
                            {!isDone(i) && f.me.can_confirm && <span className="text-info text-[12px] font-semibold mr-2">RKB confirm</span>}
                            <span className="inline-flex items-center h-7 px-2.5 rounded-lg border border-line bg-elev text-[12px] font-semibold">{k === "needed" ? (i.needs_file ? "Upload" : "Fill in") : k === "back" ? "Upload again" : "View"}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </React.Fragment>
                );
              })}
              {!shown.length && <tr><td colSpan={cols} className="px-3 py-6 text-muted">No line in this view.</td></tr>}
            </tbody>
          </table>
        </Panel>
        {item && <LinePanel key={item.key} f={f} item={item} onClose={() => setSel(null)} run={run} save={save} />}
      </div>
    </>
  );
}
