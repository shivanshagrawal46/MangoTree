"use client";

/* ARV & underwriting, the front door: start a file from an address, and the
   files already on the desk as one sortable table (Ant Design). */
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Input, Popconfirm, Segmented, Statistic, Table, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ArrowRight, Calculator, FileText, Loader2, MapPin, Search, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button, Card } from "@/components/arv/ui";
import { money, ago } from "@/lib/utils";
import { arvApi, GRADE_TONE, PROPERTY_TYPE_LABEL, type AnalysisSummary, type PropertyType } from "@/lib/arv";
import { Pill } from "@/components/arv/bits";

export default function ArvPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["arv-status"], queryFn: arvApi.status, staleTime: 10 * 60_000 });
  const list = useQuery({ queryKey: ["arv-list"], queryFn: () => arvApi.list(100), staleTime: 60_000, refetchOnWindowFocus: false,
    refetchInterval: (q) => ((q.state.data?.items || []).some((i) => i.status === "running" || i.status === "queued") ? 10_000 : false) });
  const items = list.data?.items || [];
  const p = status.data?.providers;
  React.useEffect(() => {
    if (!items.length) return;
    const t = setTimeout(() => { for (const it of items.slice(0, 3)) void qc.prefetchQuery({ queryKey: ["arv", it.analysis_id], queryFn: () => arvApi.get(it.analysis_id), staleTime: 5 * 60_000 }); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.map((i) => i.analysis_id).join(",")]);
  const [addr, setAddr] = React.useState("");
  const [filter, setFilter] = React.useState<"all" | "running" | "done" | "failed">("all");
  const [q, setQ] = React.useState("");
  const start = () => router.push(addr.trim() ? `/arv/new?address=${encodeURIComponent(addr.trim())}` : "/arv/new");

  const running = items.filter((i) => i.status === "running" || i.status === "queued").length;
  const valued = items.filter((i) => i.valuation?.arv);
  const strong = valued.filter((i) => ["A", "B"].includes(i.valuation?.confidence?.grade || "")).length;
  const shown = items.filter((i) => (filter === "all" ? true : filter === "running" ? i.status === "running" || i.status === "queued" : i.status === filter))
    .filter((i) => !q.trim() || `${i.geocode?.formatted || i.subject?.address || ""} ${i.label || ""} ${i.created_by_name || ""}`.toLowerCase().includes(q.trim().toLowerCase()));

  const remove = async (it: AnalysisSummary) => {
    try {
      await arvApi.remove(it.analysis_id);
      qc.setQueryData(["arv-list"], (old: any) => old ? { ...old, items: old.items.filter((x: AnalysisSummary) => x.analysis_id !== it.analysis_id) } : old);
      toast.success("Deleted");
    } catch (err: any) { toast.error(err?.message || "Could not delete"); }
  };

  const columns: ColumnsType<AnalysisSummary> = [
    {
      title: "Property", key: "property", sorter: (a, b) => (a.geocode?.formatted || a.subject?.address || "").localeCompare(b.geocode?.formatted || b.subject?.address || ""),
      render: (_, it) => {
        const isRunning = it.status === "running" || it.status === "queued";
        const facts = [it.subject?.property_type ? PROPERTY_TYPE_LABEL[it.subject.property_type as PropertyType] : null, it.subject?.beds != null ? `${it.subject.beds} bd` : null, it.subject?.baths != null ? `${it.subject.baths} ba` : null, it.subject?.sqft ? `${it.subject.sqft.toLocaleString()} sqft` : null].filter(Boolean).join(" · ");
        return (
          <Link href={`/arv/${it.analysis_id}`} className="block min-w-0 text-fg">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-[13px] font-semibold truncate">{it.geocode?.formatted || it.subject?.address}</span>
              {it.label && <Pill tone="accent">{it.label}</Pill>}
              {isRunning && <Pill tone="high"><Loader2 size={10} className="animate-spin" /> running</Pill>}
              {it.status === "failed" && <Pill tone="critical">failed</Pill>}
            </div>
            <div className="text-[11px] text-muted mt-0.5 truncate max-w-[520px]">{facts || "details pending"}{it.analysis?.headline ? <span className="text-faint"> · {it.analysis.headline}</span> : null}</div>
          </Link>
        );
      },
    },
    {
      title: "After repair value", key: "arv", width: 200, sorter: (a, b) => (a.valuation?.arv || 0) - (b.valuation?.arv || 0),
      render: (_, it) => {
        const v = it.valuation;
        const span = v?.arv_low && v?.arv_high && v.arv_high > v.arv_low ? v : null;
        return (
          <div>
            <div className="text-[15px] font-semibold tnum tracking-tight">{v?.arv ? money(v.arv) : it.status === "running" ? <span className="text-faint text-[12px]">computing…</span> : "—"}</div>
            {span && <div className="relative h-1.5 mt-1 rounded-full bg-sunken overflow-hidden"><div className="absolute inset-y-0 bg-accent/30" style={{ left: "8%", right: "8%" }} /><div className="absolute inset-y-0 w-[2px] bg-accent" style={{ left: `${8 + ((v!.arv! - v!.arv_low!) / (v!.arv_high! - v!.arv_low!)) * 84}%` }} /></div>}
            {span && <div className="text-[10px] text-faint tnum mt-0.5">{money(v!.arv_low, true)} – {money(v!.arv_high, true)}</div>}
          </div>
        );
      },
    },
    { title: "Max offer", key: "mao", width: 110, align: "right", sorter: (a, b) => (a.underwriting?.mao?.recommended || 0) - (b.underwriting?.mao?.recommended || 0), render: (_, it) => <span className="text-[13px] font-semibold tnum">{money(it.underwriting?.mao?.recommended, true)}</span> },
    { title: "Rehab", key: "rehab", width: 100, align: "right", sorter: (a, b) => (a.rehab_budget?.total || 0) - (b.rehab_budget?.total || 0), render: (_, it) => <span className="text-[13px] tnum text-muted">{money(it.rehab_budget?.total, true)}</span> },
    { title: "Grade", key: "grade", width: 100, align: "right", sorter: (a, b) => (a.valuation?.confidence?.score || 0) - (b.valuation?.confidence?.score || 0), render: (_, it) => { const c = it.valuation?.confidence; return c ? <Pill tone={GRADE_TONE[c.grade] || "neutral"}>{c.grade} · {c.score}</Pill> : <span className="text-faint text-[11px]">—</span>; } },
    { title: "Who · when", key: "who", width: 170, align: "right", sorter: (a, b) => String(a.created_at).localeCompare(String(b.created_at)), render: (_, it) => <span className="text-[11px] text-muted">{it.created_by_name} · {ago(it.created_at)}</span> },
    {
      key: "actions", width: 44, align: "right",
      render: (_, it) => (
        <Popconfirm title="Delete this file?" description="This cannot be undone." okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => remove(it)}>
          <Tooltip title="Delete"><button aria-label="Delete this file" onClick={(e) => e.stopPropagation()} className="h-7 w-7 grid place-items-center rounded-lg text-faint hover:text-critical hover:bg-critical-soft transition"><Trash2 size={14} /></button></Tooltip>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div className="p-6 max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2"><Calculator size={18} /> ARV &amp; underwriting</h1>
          <div className="text-xs text-muted mt-1">After repair value by sales comparison, the MAO Rehab Calculator, and an AI review of every file. United States only.</div>
        </div>
        {p && (
          <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
            <Pill tone="good">Census geocoder</Pill>
            <Pill tone="good">AI web search</Pill>
            <Pill tone={p.rentcast ? "good" : "neutral"}>RentCast {p.rentcast ? "on" : "no key"}</Pill>
            <Pill tone="good">FRED · Zillow · FEMA · ACS</Pill>
            <Pill tone="accent">AI analyst</Pill>
          </div>
        )}
      </div>

      {/* ------------------------------------------------------- start */}
      <Card className="overflow-hidden">
        <div className="p-6 md:p-8 bg-gradient-to-br from-accent-soft/70 via-transparent to-transparent">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-2xl bg-accent text-white grid place-items-center shrink-0"><Sparkles size={18} /></div>
            <div className="min-w-0 flex-1">
              <h2 className="text-[17px] font-semibold tracking-tight">Start a file</h2>
              <p className="text-xs text-muted mt-0.5">Six short steps: address, documents, the house, the plan, the deal, review. Drop the inspection report and the contractor&apos;s bid and the model reads them into the file.</p>
              <form onSubmit={(e) => { e.preventDefault(); start(); }} className="mt-4 flex flex-col sm:flex-row gap-2">
                <Input size="large" value={addr} onChange={(e) => setAddr(e.target.value)} placeholder="Property address in the United States" prefix={<MapPin size={15} className="text-faint" />} autoFocus className="flex-1" />
                <Button variant="primary" size="lg" type="submit">Continue <ArrowRight size={15} /></Button>
              </form>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-muted">
                <span className="flex items-center gap-1.5"><FileText size={12} className="text-accent" /> Inspection, appraisal, bids and photos are read by the model</span>
                <span className="flex items-center gap-1.5"><Search size={12} className="text-accent" /> Comps found, adjusted and weighted like a Form 1004</span>
                <span className="flex items-center gap-1.5"><Calculator size={12} className="text-accent" /> MAO, profit, ROI, sensitivity, lender checks</span>
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* -------------------------------------------------------- desk */}
      {items.length > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Card className="p-4"><Statistic title="Files" value={items.length} /><div className="text-[11px] text-muted mt-0.5">{valued.length} with a value</div></Card>
          <Card className="p-4"><Statistic title="Running now" value={running} styles={{ content: { color: running ? "var(--accent)" : undefined } }} /><div className="text-[11px] text-muted mt-0.5">{running ? "stages stream in live" : "nothing in progress"}</div></Card>
          <Card className="p-4"><Statistic title="Strong files" value={strong} styles={{ content: { color: "var(--good)" } }} /><div className="text-[11px] text-muted mt-0.5">confidence grade A or B</div></Card>
          <Card className="p-4"><Statistic title="Value on the desk" value={money(valued.reduce((a, i) => a + (i.valuation?.arv || 0), 0), true)} /><div className="text-[11px] text-muted mt-0.5">sum of ARVs</div></Card>
        </div>
      )}

      <div>
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <Segmented value={filter} onChange={(v) => setFilter(v as typeof filter)} options={[{ value: "all", label: `All · ${items.length}` }, { value: "running", label: `Running · ${running}` }, { value: "done", label: "Done" }, { value: "failed", label: "Failed" }]} />
          <Input allowClear value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an address, label or person" prefix={<Search size={13} className="text-faint" />} className="!w-72" />
        </div>
        <Card className="overflow-hidden">
          <Table<AnalysisSummary> rowKey="analysis_id" columns={columns} dataSource={shown} loading={list.isLoading} pagination={shown.length > 25 ? { pageSize: 25, showSizeChanger: false } : false} size="middle"
            locale={{ emptyText: items.length === 0 ? "No files yet. Start with an address above." : "Nothing matches this filter." }}
            onRow={(it) => ({
              onClick: (e) => { if ((e.target as HTMLElement).closest("button, a, .ant-popover, .ant-popconfirm")) return; router.push(`/arv/${it.analysis_id}`); },
              onMouseEnter: () => { void qc.prefetchQuery({ queryKey: ["arv", it.analysis_id], queryFn: () => arvApi.get(it.analysis_id), staleTime: 5 * 60_000 }); },
              className: "cursor-pointer",
            })} />
        </Card>
      </div>
    </div>
  );
}
