"use client";

/**
 * Sold deals — Deal Intelligence for the CEO.
 *
 * Reading order on the portfolio page: one sentence → the money as a picture →
 * four numbers → the 29 deals on a time axis → what went wrong, what the second
 * analyst found, what worked, what to do. Every dollar is the summary workbook as
 * written. Each deal was analysed twice (Claude Fable 5.1, then GPT-6 Astra,
 * independently, same files) and the two accounts were reconciled by a reviewer.
 * Long text is behind folds.
 */
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Col, Collapse, Modal, Row, Segmented, Skeleton, Table, Tag, Timeline, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { SoldDeal, SoldExplanation, SoldPortfolio, SoldRow, SoldSection } from "@/lib/types";

const { Text, Title } = Typography;

// ----------------------------------------------------------------- helpers
const usd0 = (n?: number | null) => (n === null || n === undefined || isNaN(n)) ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = (n?: number | null) => (n === null || n === undefined || isNaN(n)) ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const usdK = (n?: number | null) => (n === null || n === undefined || isNaN(n)) ? "—" : Math.abs(n) >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : `$${Math.round(n / 1000)}K`;
const pct1 = (n?: number | null) => (n === null || n === undefined || isNaN(n)) ? "—" : `${(n * 100).toFixed(1)}%`;
const short = (addr: string) => addr.replace(/,.*$/, "").replace(/\b(Street|St\.?|Avenue|Ave\.?|Drive|Dr\.?|Lane|Ln\.?|Court|Ct\.?|Place|Pl\.?|Road|Rd\.?)\b.*$/i, (m) => m.split(" ")[0]);

const V: Record<string, { color: string; label: string; css: string }> = {
  good: { color: "success", label: "Good", css: "var(--good)" },
  mixed: { color: "warning", label: "Mixed", css: "var(--high)" },
  bad: { color: "error", label: "Bad", css: "var(--critical)" },
};
const VerdictTag = ({ v, size }: { v?: string | null; size?: "big" }) => v && V[v]
  ? <Tag color={V[v].color} style={{ marginInlineEnd: 0, ...(size === "big" ? { fontSize: 13, padding: "2px 10px", lineHeight: "22px" } : {}) }}>{V[v].label}</Tag>
  : <Tag style={{ marginInlineEnd: 0 }}>—</Tag>;

const PAD = { body: { padding: 28 } };

/** Inline emphasis: money, percentages and bracketed deal lists get their own treatment. */
const TOKEN = /(\$[\d,]+(?:\.\d+)?(?:\s?(?:million|thousand|[KkMm]))?|\b\d+(?:\.\d+)?%|\[[^\]]+\])/g;
function Rich({ text }: { text: string }) {
  const parts = text.split(TOKEN);
  return (
    <>
      {parts.map((p, i) => {
        if (!p) return null;
        if (/^\$/.test(p)) {
          const before = parts.slice(0, i).join("").slice(-50);
          const neg = /short|owed|lost|kept|never|not recovered|cost/i.test(before);
          return <span key={i} className={"money" + (neg ? " neg" : "")}>{p}</span>;
        }
        if (/%$/.test(p)) return <span key={i} className="pct">{p}</span>;
        if (/^\[/.test(p)) return <span key={i}>{p.slice(1, -1).split(/,\s*/).map((d, j) => <span key={j} className="deal">{d}</span>)}</span>;
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}
/** A point = optional bold lead (up to the first colon / em-dash / semicolon, if short) + the rest. */
function splitLead(t: string): [string | null, string] {
  const m = t.match(/^(.{8,70}?)(:|\s—\s|;)\s*(.+)$/);
  if (m) return [m[1].trim(), m[3].trim()];
  const s = t.match(/^([^.]{10,60}\.)\s+(.+)$/);
  if (s) return [s[1].trim(), s[2].trim()];
  return [null, t];
}
const Points = ({ items, numbered = true, onPick }: { items?: string[] | null; numbered?: boolean; onPick?: (i: number) => void }) => {
  if (!items?.length) return <Text type="secondary">Nothing to report.</Text>;
  return (
    <ol className="pts">
      {items.map((t, i) => {
        const [lead, rest] = splitLead(t);
        return (
          <li key={i} className={"pt" + (onPick ? " pt-click" : "")} onClick={onPick ? () => onPick(i) : undefined} role={onPick ? "button" : undefined} tabIndex={onPick ? 0 : undefined}
            onKeyDown={onPick ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(i); } } : undefined}>
            {numbered ? <span className="pt-n">{String(i + 1).padStart(2, "0")}</span> : <span><span className="pt-dot" /></span>}
            <div>{lead && <><span className="pt-lead"><Rich text={lead} /></span>{" "}</>}<Rich text={rest} />{onPick && <span className="pt-more">Details</span>}</div>
          </li>
        );
      })}
    </ol>
  );
};

/** The detail behind one point, as a modal: what it means, deal by deal, why it matters, the rule. */
function ExplainModal({ open, onClose, point, x, loading }: { open: boolean; onClose: () => void; point: string; x?: SoldExplanation | null; loading?: boolean }) {
  return (
    <Modal open={open} onCancel={onClose} footer={null} width={820} centered styles={{ body: { padding: "8px 8px 16px" } }}
      title={<div style={{ paddingRight: 24 }}><div className="sold-eyebrow">In detail</div><div style={{ fontSize: 18, fontWeight: 650, letterSpacing: "-0.01em", lineHeight: 1.35, marginTop: 2 }}><Rich text={x?.title || point} /></div></div>}>
      {!x ? (
        <div style={{ padding: 12 }}>{loading ? <Skeleton active paragraph={{ rows: 6 }} /> : <Text type="secondary">The detailed note for this point is being written. Try again in a few minutes.</Text>}</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 20, padding: "8px 12px" }}>
          {x.what_it_means && <div className="pt" style={{ display: "block", fontSize: 14.5, lineHeight: 1.65 }}><Rich text={x.what_it_means} /></div>}
          {x.deals?.length ? (
            <div>
              <div className="sold-eyebrow" style={{ marginBottom: 10 }}>Deal by deal</div>
              <div style={{ display: "flex", flexDirection: "column" }}>
                {x.deals.map((dl, i) => (
                  <div key={i} style={{ padding: "14px 0", borderTop: "1px solid var(--line)" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
                      <Link href={`/sold/${dl.property_id}`} onClick={onClose} style={{ fontWeight: 650, fontSize: 14.5 }}>{dl.address || dl.property_id}</Link>
                      {dl.headline && <span style={{ fontSize: 13, color: "var(--fg-muted)" }}><Rich text={dl.headline} /></span>}
                    </div>
                    {dl.detail && <div className="pt" style={{ display: "block", fontSize: 14, lineHeight: 1.65, marginTop: 6 }}><Rich text={dl.detail} /></div>}
                    {dl.evidence && <div className="pt-sub" style={{ marginTop: 4 }}>Shown by: {dl.evidence}</div>}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {x.why_it_matters && <div style={{ borderTop: "1px solid var(--line)", paddingTop: 14 }}><div className="sold-eyebrow" style={{ marginBottom: 6 }}>Why it matters</div><div className="pt" style={{ display: "block", fontSize: 14, lineHeight: 1.65 }}><Rich text={x.why_it_matters} /></div></div>}
          {x.rule && <div style={{ background: "var(--accent-soft)", borderRadius: 12, padding: "12px 16px" }}><div className="sold-eyebrow" style={{ marginBottom: 4 }}>The rule</div><div style={{ fontSize: 14.5, fontWeight: 600, lineHeight: 1.5 }}><Rich text={x.rule} /></div></div>}
        </div>
      )}
    </Modal>
  );
}
const Panel = ({ eyebrow, title, sub, children, extra, tone }: { eyebrow?: string; title: React.ReactNode; sub?: React.ReactNode; children: React.ReactNode; extra?: React.ReactNode; tone?: "review" }) => (
  <Card styles={{ body: { padding: "24px 28px 28px" } }} style={{ height: "100%", ...(tone === "review" ? { borderColor: "color-mix(in srgb, var(--accent) 45%, var(--line))", background: "color-mix(in srgb, var(--accent-soft) 35%, var(--bg-elev))" } : {}) }}>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 18 }}>
      <div>
        {eyebrow && <div className="sold-eyebrow">{eyebrow}</div>}
        <div className="sold-h">{title}</div>
        {sub && <div className="sold-sub">{sub}</div>}
      </div>
      {extra}
    </div>
    {children}
  </Card>
);
const Kpi = ({ label, value, sub, tone }: { label: string; value: string; sub?: React.ReactNode; tone?: "good" | "bad" }) => (
  <Card styles={{ body: { padding: "16px 20px" } }}>
    <div style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--fg-muted)", fontWeight: 600 }}>{label}</div>
    <div style={{ fontSize: 28, fontWeight: 650, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums", marginTop: 2, lineHeight: 1.15, color: tone === "bad" ? "var(--critical)" : tone === "good" ? "var(--good)" : "var(--fg)" }}>{value}</div>
    {sub && <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 4 }}>{sub}</div>}
  </Card>
);
const FullText = ({ sections, label }: { sections: SoldSection[]; label: string }) => (
  <Collapse ghost items={[{ key: "full", label: <Text strong>{label}</Text>, children: (
    <Collapse size="small" items={sections.map((s) => ({ key: s.n, label: `${s.n}. ${s.title}`, children: <div className="prose-mt sold-md" style={{ fontSize: 13 }} dangerouslySetInnerHTML={{ __html: s.html }} /> }))} />
  ) }]} />
);

// ------------------------------------------------------- the money picture
function MoneyBar({ label, parts, caption, total }: { label: string; parts: { n: number; color: string; text: string }[]; caption: string; total: number }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", alignItems: "center", gap: 14 }}>
      <div style={{ fontSize: 12.5, fontWeight: 600 }}>{label}</div>
      <div>
        <div style={{ display: "flex", height: 30, borderRadius: 8, overflow: "hidden", background: "var(--bg-sunken)" }}>
          {parts.map((p, i) => <div key={i} title={p.text} style={{ width: `${(p.n / total) * 100}%`, background: p.color, display: "flex", alignItems: "center", paddingLeft: 10, color: "#fff", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden" }}>{p.text}</div>)}
        </div>
        <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 4 }}>{caption}</div>
      </div>
    </div>
  );
}
function MoneyBars({ invested, billed, collected, owed, profit }: { invested: number; billed: number; collected: number; owed: number; profit: number }) {
  const total = Math.max(invested + billed, collected + owed);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <MoneyBar total={total} label="What we billed" caption={`Principal ${usd0(invested)} + points, interest and fees ${usd0(billed)} = ${usd0(invested + billed)} due to RKB`}
        parts={[{ n: invested, color: "var(--money-in)", text: `Put in ${usdK(invested)}` }, { n: billed, color: "color-mix(in srgb, var(--money-in) 45%, var(--bg-elev))", text: `Yield billed ${usdK(billed)}` }]} />
      <MoneyBar total={total} label="What came in" caption={`${usd0(collected)} received. That is principal back plus ${usd0(profit)} — the remaining ${usd0(owed)} billed was never collected.`}
        parts={[{ n: invested, color: "var(--money-back)", text: `Principal back ${usdK(invested)}` }, { n: profit, color: "color-mix(in srgb, var(--money-back) 55%, var(--bg-elev))", text: `Profit ${usdK(profit)}` }, { n: owed, color: "var(--critical)", text: `Still owed ${usdK(owed)}` }]} />
    </div>
  );
}

// ------------------------------------------------------------------ portfolio
export function SoldDashboard() {
  const q = useQuery({ queryKey: ["sold-portfolio"], queryFn: () => api.get<SoldPortfolio>("/sold/portfolio"), staleTime: 60_000 });
  const [filter, setFilter] = React.useState<string>("all");
  const [pick, setPick] = React.useState<{ section: string; i: number } | null>(null);
  if (q.isLoading || !q.data) return <Skeleton active paragraph={{ rows: 12 }} />;
  const d = q.data, dv = d.derived, b = d.brief;
  const pickSec = (section: string) => (i: number) => setPick({ section, i });
  const picked = pick ? { point: ((b as unknown as Record<string, string[]>)?.[pick.section] || [])[pick.i] || "", x: d.explanations?.[pick.section]?.[pick.i] } : null;
  const grand = d.totals_as_written.find((t) => t.which === "grand");
  const deals = d.rows.filter((r) => r.kind === "deal");
  const shown = filter === "all" ? deals : filter === "owed" ? deals.filter((r) => (r.excess_shortage ?? 0) < -1) : deals.filter((r) => r.verdict === filter);
  const dealsOwed = deals.filter((r) => (r.excess_shortage ?? 0) < -1).sort((a, b2) => (a.excess_shortage ?? 0) - (b2.excess_shortage ?? 0));
  const invested = grand?.cash_invested ?? dv.deals_cash_invested, collected = grand?.cash_collected ?? dv.deals_cash_collected;
  const owedTotal = -(grand?.excess_shortage ?? 0), billed = grand?.rkb_yield ?? dv.deals_yield_billed, profit = collected - invested;

  const cols: ColumnsType<SoldRow> = [
    { title: "Property", dataIndex: "address", key: "address", fixed: "left", width: 230, render: (v, r) => r.property_id ? <Link href={`/sold/${r.property_id}`} style={{ fontWeight: 500 }}>{v}</Link> : v, sorter: (a, b2) => a.address.localeCompare(b2.address) },
    { title: "St", dataIndex: "state", key: "state", width: 46 },
    { title: "Funded", dataIndex: "investment_date", key: "investment_date", width: 90, render: (v) => fmtDate(v, "MMM yyyy"), sorter: (a, b2) => String(a.investment_date).localeCompare(String(b2.investment_date)), defaultSortOrder: "ascend" },
    { title: "Days", dataIndex: "days_invested", key: "days", width: 60, align: "right", sorter: (a, b2) => (a.days_invested ?? 0) - (b2.days_invested ?? 0) },
    { title: "Put in", dataIndex: "cash_invested", key: "in", align: "right", width: 100, render: usd0, sorter: (a, b2) => (a.cash_invested ?? 0) - (b2.cash_invested ?? 0) },
    { title: "Got back", key: "out", width: 190, sorter: (a, b2) => (a.cash_collected ?? 0) - (b2.cash_collected ?? 0), render: (_, r) => {
      const due = r.total_to_collect ?? 0, got = r.cash_collected ?? 0, f = due ? Math.min(got / due, 1) : 0;
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--bg-sunken)", overflow: "hidden" }}><div style={{ width: `${f * 100}%`, height: "100%", background: f >= 0.999 ? "var(--money-back)" : got < (r.cash_invested ?? 0) ? "var(--critical)" : "var(--high)" }} /></div>
          <span style={{ fontVariantNumeric: "tabular-nums", minWidth: 70, textAlign: "right" }}>{usd0(got)}</span>
        </div>
      );
    } },
    { title: "Still owed", dataIndex: "excess_shortage", key: "owed", align: "right", width: 100, sorter: (a, b2) => (a.excess_shortage ?? 0) - (b2.excess_shortage ?? 0),
      render: (v: number) => v < -1 ? <Text type="danger" strong>{usd0(-v)}</Text> : v > 1 ? <Text type="success">+{usd0(v)}</Text> : <Text type="secondary">—</Text> },
    { title: "Return / yr", dataIndex: "annualized_roi", key: "roi", align: "right", width: 90, render: pct1, sorter: (a, b2) => (a.annualized_roi ?? 0) - (b2.annualized_roi ?? 0) },
    { title: "Verdict", dataIndex: "verdict", key: "verdict", width: 90, render: (v) => <VerdictTag v={v} /> },
    { title: "Why", dataIndex: "reason", key: "reason", render: (v) => <Text type="secondary" style={{ fontSize: 12.5 }}>{v}</Text> },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 1240 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>Sold deals</Title>
          <Text type="secondary">{dv.deals} finished deals with Listing Prophet · figures from {d.workbook.file}, dated {fmtDate(d.workbook.file_mtime, "d MMM yyyy")}</Text>
        </div>
        <div style={{ fontSize: 12, color: "var(--fg-muted)", textAlign: "right" }}>
          Every deal read from its own emails, documents and photos · click any point for the detail
        </div>
      </div>
      {b?.headline && <div className="pt" style={{ display: "block", fontSize: 20, lineHeight: 1.45, fontWeight: 500, letterSpacing: "-0.012em", maxWidth: 940, padding: "4px 0 2px" }}><Rich text={b.headline} /></div>}

      <Card styles={PAD}><MoneyBars invested={invested} billed={billed} collected={collected} owed={owedTotal} profit={profit} /></Card>

      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}><Kpi label="We put in" value={usd0(invested)} sub={`${dv.deals} deals · ${Math.round(dv.capital_weighted_days)} days out on average`} /></Col>
        <Col xs={12} lg={6}><Kpi label="We got back" value={usd0(collected)} sub={`${dv.deals - dv.deals_below_principal} of ${dv.deals} deals returned at least the principal`} /></Col>
        <Col xs={12} lg={6}><Kpi label="Still owed to us" value={usd0(owedTotal)} tone="bad" sub={`${dealsOwed.length} deals · ${usd0(dv.principal_lost)} of it is principal`} /></Col>
        <Col xs={12} lg={6}><Kpi label="Profit actually made" value={usd0(profit)} tone={profit >= 0 ? "good" : "bad"} sub={`${pct1(dv.cash_profit_annualised)} a year — we billed ${pct1(dv.billed_yield_annualised)}`} /></Col>
      </Row>

      <Row gutter={[20, 20]}>
        <Col xs={24} lg={15}>
          <Panel eyebrow="The picture" title="In plain words" sub="what the money did, in five lines">
            <Points onPick={b?.in_plain_words ? pickSec("in_plain_words") : undefined} items={b?.in_plain_words ?? [
              `We put ${usd0(invested)} into ${dv.deals} deals and got ${usd0(collected)} back.`,
              `${usd0(owedTotal)} we billed never came in, across ${dealsOwed.length} deals.`,
              `Real profit ${usd0(profit)} — ${pct1(dv.cash_profit_annualised)} a year — against ${pct1(dv.billed_yield_annualised)} a year billed.`]} />
          </Panel>
        </Col>
        <Col xs={24} lg={9}>
          <Panel eyebrow="Receivable" title="Still owed to RKB" sub="largest first · click for the deal">
            <div style={{ display: "flex", flexDirection: "column" }}>
              {dealsOwed.map((r) => (
                <Link key={r.label} href={`/sold/${r.property_id}`} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, fontSize: 14, padding: "9px 0", borderBottom: "1px solid var(--line)", color: "inherit" }}>
                  <span style={{ fontWeight: 500 }}>{short(r.address)} <span style={{ color: "var(--fg-faint)", fontWeight: 400, fontSize: 12 }}>{r.state}</span></span>
                  <span style={{ fontVariantNumeric: "tabular-nums", color: "var(--critical)", fontWeight: 650 }}>{usd0(-(r.excess_shortage ?? 0))}</span>
                </Link>
              ))}
              <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 12, fontSize: 15, fontWeight: 650 }}>
                <span>Total still owed</span><span style={{ color: "var(--critical)", fontVariantNumeric: "tabular-nums" }}>{usd0(owedTotal)}</span>
              </div>
              {dv.deals_excess_sum > 1 && <div className="pt-sub" style={{ marginTop: 6 }}>Net of {usd0(dv.deals_excess_sum)} received above the amount due on other deals.</div>}
            </div>
          </Panel>
        </Col>
      </Row>

      {b && (
        <>
          <Row gutter={[20, 20]}>
            <Col xs={24} lg={14}><Panel eyebrow="Mistakes" title="What we did wrong" sub="the repeated mistakes, ordered by money lost"><Points items={b.biggest_mistakes} onPick={pickSec("biggest_mistakes")} /></Panel></Col>
            <Col xs={24} lg={10}><Panel eyebrow="Good practice" title="What worked" sub="the controls that held"><Points items={b.what_worked} numbered={false} onPick={pickSec("what_worked")} /></Panel></Col>
          </Row>
          <Row gutter={[20, 20]}>
            <Col xs={24} lg={14}><Panel eyebrow="Rules" title="Before we fund the next one" sub="in order"><Points items={b.checklist} onPick={pickSec("checklist")} /></Panel></Col>
            <Col xs={24} lg={10}><Panel eyebrow="Now" title="Decisions for Rakesh Sir"><Points items={b.decisions} onPick={pickSec("decisions")} /></Panel></Col>
          </Row>
          <ExplainModal open={!!pick} onClose={() => setPick(null)} point={picked?.point || ""} x={picked?.x} />
        </>
      )}

      <Card title="Every deal" styles={{ body: { padding: 0 } }} extra={
        <Segmented size="small" value={filter} onChange={(v) => setFilter(String(v))} options={[
          { label: `All ${deals.length}`, value: "all" }, { label: `Good ${d.verdict_counts.good}`, value: "good" },
          { label: `Mixed ${d.verdict_counts.mixed}`, value: "mixed" }, { label: `Bad ${d.verdict_counts.bad}`, value: "bad" }, { label: `Owed ${dealsOwed.length}`, value: "owed" }]} />
      }>
        <Table<SoldRow> rowKey="label" size="small" columns={cols} dataSource={shown} pagination={false} scroll={{ x: 1100 }}
          expandable={{ expandedRowRender: (r) => (
            <div style={{ fontSize: 13, padding: "4px 8px", display: "flex", flexDirection: "column", gap: 6 }}>
              {r.one_line && <div className="pt" style={{ display: "block" }}><Rich text={r.one_line} /></div>}
            </div>
          ), rowExpandable: (r) => !!r.one_line }}
          summary={() => (
            <Table.Summary fixed>
              <Table.Summary.Row style={{ fontWeight: 600 }}>
                <Table.Summary.Cell index={0} colSpan={5}>{filter === "all" ? `${deals.length} deals (sheet total)` : `${shown.length} shown`}</Table.Summary.Cell>
                <Table.Summary.Cell index={5} align="right">{usd0(shown.reduce((s, r) => s + (r.cash_invested ?? 0), 0))}</Table.Summary.Cell>
                <Table.Summary.Cell index={6} align="right">{usd0(shown.reduce((s, r) => s + (r.cash_collected ?? 0), 0))}</Table.Summary.Cell>
                <Table.Summary.Cell index={7} align="right"><Text type="danger" strong>{usd0(-shown.reduce((s, r) => s + Math.min(r.excess_shortage ?? 0, 0), 0))}</Text></Table.Summary.Cell>
                <Table.Summary.Cell index={8} colSpan={3} />
              </Table.Summary.Row>
            </Table.Summary>
          )} />
      </Card>

      {d.synthesis && <Card styles={{ body: { padding: "4px 8px" } }}><FullText sections={d.synthesis.sections} label={`Read the full cross-deal analysis (${d.synthesis.sections.length} sections)`} /></Card>}
    </div>
  );
}

// ------------------------------------------------------------------- deal page
export function SoldDealView({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["sold-deal", pid], queryFn: () => api.get<SoldDeal>(`/sold/deals/${pid}`), staleTime: 60_000 });
  if (q.isLoading || !q.data) return <Skeleton active paragraph={{ rows: 10 }} />;
  const d = q.data, r = d.row, b = d.brief, pm = d.postmortem;
  const owed = -(r?.excess_shortage ?? 0);
  const verdict = d.verdict || b?.verdict;
  const due = r?.total_to_collect ?? 0, got = r?.cash_collected ?? 0, inv = r?.cash_invested ?? 0;
  const scale = Math.max(due, got) || 1;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 1100 }}>
      <div>
        <Link href="/sold" style={{ fontSize: 12.5 }}>← Sold deals</Link>
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 4, flexWrap: "wrap" }}>
          <Title level={3} style={{ margin: 0 }}>{d.address}</Title>
          <VerdictTag v={verdict} size="big" />
          <Text type="secondary">{[d.city, d.state].filter(Boolean).join(", ")}{r?.section === "lp_sold_kept_cash" ? " · LP sold and kept the cash" : ""}</Text>
        </div>
        {b?.one_line && <div className="pt" style={{ display: "block", fontSize: 17, lineHeight: 1.5, marginTop: 10, maxWidth: 880 }}><Rich text={b.one_line} /></div>}
      </div>

      {r && (
        <Card styles={PAD}>
          <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 12, alignItems: "center" }}>
            <Text strong style={{ fontSize: 12.5 }}>Due to RKB</Text>
            <div style={{ display: "flex", height: 26, borderRadius: 7, overflow: "hidden", background: "var(--bg-sunken)", width: `${(due / scale) * 100}%` }}>
              <div style={{ width: `${(inv / due) * 100}%`, background: "var(--money-in)", color: "#fff", fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", paddingLeft: 10, whiteSpace: "nowrap" }}>Principal {usd0(inv)}</div>
              <div style={{ flex: 1, background: "color-mix(in srgb, var(--money-in) 45%, var(--bg-elev))", color: "#fff", fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", paddingLeft: 10, whiteSpace: "nowrap", overflow: "hidden" }}>Billed {usd0(r.rkb_yield)}</div>
            </div>
            <Text strong style={{ fontSize: 12.5 }}>Received</Text>
            <div style={{ display: "flex", height: 26, borderRadius: 7, overflow: "hidden", background: "var(--bg-sunken)", width: `${(Math.max(got, 0) / scale) * 100}%`, minWidth: 4 }}>
              <div style={{ width: "100%", background: got >= due - 1 ? "var(--money-back)" : got < inv - 1 ? "var(--critical)" : "var(--high)", color: "#fff", fontSize: 12, fontWeight: 600, display: "flex", alignItems: "center", paddingLeft: 10, whiteSpace: "nowrap" }}>{usd0(got)}</div>
            </div>
          </div>
          <Row gutter={[16, 8]} style={{ marginTop: 16 }}>
            <Col xs={12} md={6}><div style={{ fontSize: 11, color: "var(--fg-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Put in</div><div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{usd2(inv)}</div><div style={{ fontSize: 12, color: "var(--fg-muted)" }}>funded {fmtDate(r.investment_date, "d MMM yyyy")}</div></Col>
            <Col xs={12} md={6}><div style={{ fontSize: 11, color: "var(--fg-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Got back</div><div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{usd2(got)}</div><div style={{ fontSize: 12, color: "var(--fg-muted)" }}>received {fmtDate(r.received_date, "d MMM yyyy")}</div></Col>
            <Col xs={12} md={6}><div style={{ fontSize: 11, color: "var(--fg-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{owed > 1 ? "Still owed" : owed < -1 ? "Received above due" : "Still owed"}</div><div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums", color: owed > 1 ? "var(--critical)" : "var(--good)" }}>{owed > 1 ? usd2(owed) : owed < -1 ? "+" + usd2(-owed) : "Paid in full"}</div><div style={{ fontSize: 12, color: "var(--fg-muted)" }}>{got < inv - 1 ? `${usd0(inv - got)} of principal lost` : "principal recovered"}</div></Col>
            <Col xs={12} md={6}><div style={{ fontSize: 11, color: "var(--fg-muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Time &amp; return</div><div style={{ fontSize: 18, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>{r.days_invested} days</div><div style={{ fontSize: 12, color: "var(--fg-muted)" }}>{pct1(r.annualized_roi)} a year billed · {pct1(r.roi)} on cash</div></Col>
          </Row>
        </Card>
      )}

      {(b?.verdict_reason || d.reason) && <Alert type={verdict === "bad" ? "error" : verdict === "good" ? "success" : "warning"} showIcon style={{ padding: "14px 18px" }} message={<Text strong style={{ fontSize: 14 }}>Why {V[verdict || "mixed"]?.label.toLowerCase()}</Text>} description={<span className="pt" style={{ display: "block", fontSize: 14 }}><Rich text={b?.verdict_reason || d.reason || ""} /></span>} />}

      {b ? (
        <>
          <Row gutter={[20, 20]}>
            <Col xs={24} lg={10}>
              <Panel eyebrow="The story" title="What happened" sub="in order">
                <Timeline style={{ marginTop: 6 }} items={(b.what_happened || []).map((t, i) => ({ color: i === (b.what_happened!.length - 1) ? (verdict === "bad" ? "red" : verdict === "good" ? "green" : "orange") : "blue", children: <div style={{ fontSize: 14, lineHeight: 1.6, paddingBottom: 6 }}><Rich text={t} /></div> }))} />
              </Panel>
            </Col>
            <Col xs={24} lg={14}>
              <Panel eyebrow="Mistakes" title="What we did wrong" sub={b.reviewed ? "ordered by cost, with the document that shows it" : undefined}>
                {b.mistakes?.length ? (
                  <ol className="pts">
                    {b.mistakes.map((m, i) => {
                      const [lead, rest] = splitLead(m.text);
                      return (
                        <li key={i} className="pt">
                          <span className="pt-n">{String(i + 1).padStart(2, "0")}</span>
                          <div>
                            <div>{lead && <><span className="pt-lead"><Rich text={lead} /></span>{" "}</>}<Rich text={rest} /></div>
                            {m.evidence && <div className="pt-sub">{m.evidence}</div>}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                ) : <Points items={b.went_wrong} />}
              </Panel>
            </Col>
          </Row>

          <Row gutter={[20, 20]}>
            <Col xs={24} lg={12}><Panel eyebrow="Good practice" title="What went right"><Points items={b.went_right} numbered={false} /></Panel></Col>
            <Col xs={24} lg={12}><Panel eyebrow="Rules" title="Lessons for next time"><Points items={b.lessons} /></Panel></Col>
          </Row>

          {b.money_check && <Alert type="info" showIcon message={<Text strong>Money check against the workbook</Text>} description={<span style={{ fontSize: 13.5 }}><Rich text={b.money_check} /></span>} />}
        </>
      ) : <Alert type="info" showIcon message="Short brief not generated yet for this deal." />}

      {pm && (
        <Card styles={{ body: { padding: "4px 8px" } }}>
          <div style={{ fontSize: 12, color: "var(--fg-muted)", padding: "8px 16px 0" }}>The files behind this: {d.counts.emails} emails, {d.counts.attachments} attachments, {d.counts.files} folder documents.</div>
          <FullText sections={pm.sections} label={`Read the full post-mortem (${pm.sections.length} sections, ${pm.sources.length} sources)`} />
        </Card>
      )}
    </div>
  );
}
