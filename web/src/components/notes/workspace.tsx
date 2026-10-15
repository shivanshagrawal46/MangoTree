"use client";

/* The Notes analysis workspace: header statistics, then tabs. The tab lives
   in the URL hash so a link opens the right place. */
import * as React from "react";
import { Statistic, Tabs } from "antd";
import { Activity, BarChart3, FileText, Landmark, LineChart, ListChecks, Map, Percent } from "lucide-react";
import { Delta, Section } from "./shared";
import { Summary } from "./verdict";
import { History } from "./history";
import { ZipExplorer } from "./zip-explorer";
import { Outlook } from "./outlook";
import { Texas } from "./texas";
import { Rates } from "./rates";
import { NotesTable } from "./notes-table";
import { money, pct, type Dataset } from "@/lib/notes";

const TABS = ["summary", "history", "zips", "outlook", "texas", "rates", "notes", "sources"] as const;
type Tab = (typeof TABS)[number];

export function NotesWorkspace({ d }: { d: Dataset }) {
  const [tab, setTab] = React.useState<Tab>(() => { if (typeof window === "undefined") return "summary"; const h = window.location.hash.replace("#", "") as Tab; return TABS.includes(h) ? h : "summary"; });
  const [zip, setZip] = React.useState<string | undefined>(undefined);
  const go = (t: Tab) => { setTab(t); try { history.replaceState(null, "", "#" + t); } catch {} window.scrollTo({ top: 0 }); };
  const pickZip = (z: string) => { if (!z) return; setZip(z); go("zips"); };
  const s = d.summary;
  const label = (Icon: any, text: string) => <span className="inline-flex items-center gap-1.5"><Icon size={14} />{text}</span>;
  const S = ({ title, value, suffix, prefix, sub, color }: any) => <div className="px-4 py-3 min-w-0"><Statistic title={title} value={value} suffix={suffix} prefix={prefix} valueStyle={{ fontSize: 22, color }} /><div className="text-[11px] text-muted leading-snug">{sub}</div></div>;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wider text-muted font-medium">Notes analysis · {d.title}</div>
          <h1 className="text-[22px] font-semibold tracking-tight mt-0.5">Where the collateral behind the notes is heading</h1>
        </div>
        <div className="text-[11.5px] text-muted text-right shrink-0"><div>Analysis {d.as_of} · data through {d.data_through}</div><div className="truncate max-w-[260px]" title={d.source_file}>{d.source_file}</div></div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 divide-x divide-y xl:divide-y-0 divide-line rounded-2xl border border-line bg-elev overflow-hidden">
        <S title="Notes" value={s.notes} sub={`${money(s.balance)} · ${money(s.payment)} a month`} />
        <S title="Weighted note rate" value={s.war.toFixed(2)} suffix="%" sub={`mortgage ${s.mortgage_now.toFixed(2)}%`} />
        <S title="Loan to value" value={Math.round(s.ltv * 100)} suffix="%" sub={`on ${money(s.value, true)} collateral`} />
        <S title="Collateral ZIPs, 1 yr" value={(s.wyoy * 100).toFixed(1)} suffix="%" color="var(--critical)" sub={<>5 yrs <Delta v={s.w5y} /></>} />
        <S title="Next 5 yrs, base" value={"+" + (s.outlook.base * 100).toFixed(1)} suffix="%" color="var(--good)" sub={<>bear <Delta v={s.outlook.bear} /> · bull <Delta v={s.outlook.bull} /></>} />
        <S title="Payments late" value={s.late_n} color="var(--high)" sub={`${money(s.late_balance, true)} · ${s.balloons.length} balloons by 2030`} />
      </div>

      <Tabs className="arv-tabs" activeKey={tab} onChange={(k) => go(k as Tab)} items={[
        { key: "summary", label: label(FileText, "Summary"), children: <Summary d={d} go={go} pickZip={pickZip} /> },
        { key: "history", label: label(LineChart, "Five-year history"), children: <History d={d} pickZip={pickZip} /> },
        { key: "zips", label: label(Map, "ZIP explorer"), children: <ZipExplorer d={d} initial={zip} key={zip || "init"} /> },
        { key: "outlook", label: label(Activity, "Next five years"), children: <Outlook d={d} onPickZip={pickZip} /> },
        { key: "texas", label: label(Landmark, "Texas"), children: <Texas d={d} /> },
        { key: "rates", label: label(Percent, "Rates & loans"), children: <Rates d={d} /> },
        { key: "notes", label: label(ListChecks, "Note by note"), children: <NotesTable d={d} onPickZip={pickZip} /> },
        { key: "sources", label: label(BarChart3, "Sources"), children: <Sources d={d} /> },
      ]} />
    </div>
  );
}

function Sources({ d }: { d: Dataset }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Section title="Data and sources"><ul className="list-disc pl-5 text-[13px] space-y-1.5 text-fg/90">{d.narrative.sources.map((s) => <li key={s}>{s}</li>)}</ul>
        <p className="text-[12px] text-muted mt-3">Fair value = August 2019 value × {(d.cpi["2026-08"] / d.cpi["2019-08"]).toFixed(3)} (CPI to August 2026). Scripts and extracts: scripts/notes_analysis.</p></Section>
      <Section title="Caveats"><ul className="list-disc pl-5 text-[13px] space-y-1.5 text-fg/90">{d.narrative.caveats.map((s) => <li key={s}>{s}</li>)}</ul></Section>
    </div>
  );
}
