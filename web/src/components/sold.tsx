"use client";

/**
 * Sold deals — what the CEO needs on one screen.
 *
 * Four numbers, the story in plain words, one table, the lessons. Every dollar
 * figure is RKB's summary workbook as written. The short text comes from the
 * briefs (deal_briefs / sold_ceo_brief); the full post-mortems and the cross-deal
 * analysis sit behind a single "Read the full analysis" fold.
 */
import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Alert, Card, Col, Collapse, Row, Segmented, Skeleton, Statistic, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/utils";
import type { SoldDeal, SoldPortfolio, SoldRow, SoldSection } from "@/lib/types";

const { Text, Title, Paragraph } = Typography;

const usd0 = (n?: number | null) => (n === null || n === undefined || isNaN(n)) ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = (n?: number | null) => (n === null || n === undefined || isNaN(n)) ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct1 = (n?: number | null) => (n === null || n === undefined || isNaN(n)) ? "—" : `${(n * 100).toFixed(1)}%`;
const VERDICT: Record<string, { color: string; label: string }> = { good: { color: "success", label: "Good" }, mixed: { color: "warning", label: "Mixed" }, bad: { color: "error", label: "Bad" } };
const VerdictTag = ({ v }: { v?: string | null }) => v && VERDICT[v] ? <Tag color={VERDICT[v].color} style={{ marginInlineEnd: 0 }}>{VERDICT[v].label}</Tag> : <Tag style={{ marginInlineEnd: 0 }}>—</Tag>;

const PAD = { body: { padding: 20 } };
const Bullets = ({ items, numbered }: { items?: string[] | null; numbered?: boolean }) => {
  if (!items?.length) return <Text type="secondary">—</Text>;
  const Tagname = numbered ? "ol" : "ul";
  return <Tagname style={{ margin: 0, paddingLeft: 20, fontSize: 13.5, lineHeight: 1.6 }}>{items.map((t, i) => <li key={i} style={{ marginBottom: 6 }}>{t}</li>)}</Tagname>;
};
const Section = ({ title, children, extra }: { title: React.ReactNode; children: React.ReactNode; extra?: React.ReactNode }) => (
  <Card title={title} extra={extra} styles={PAD} style={{ height: "100%" }}>{children}</Card>
);
const FullText = ({ sections, label }: { sections: SoldSection[]; label: string }) => (
  <Collapse ghost items={[{ key: "full", label: <Text strong>{label}</Text>, children: (
    <Collapse size="small" items={sections.map((s) => ({ key: s.n, label: `${s.n}. ${s.title}`, children: <div className="prose-mt sold-md" style={{ fontSize: 13 }} dangerouslySetInnerHTML={{ __html: s.html }} /> }))} />
  ) }]} />
);

// ------------------------------------------------------------------ portfolio
export function SoldDashboard() {
  const q = useQuery({ queryKey: ["sold-portfolio"], queryFn: () => api.get<SoldPortfolio>("/sold/portfolio"), staleTime: 60_000 });
  const [filter, setFilter] = React.useState<string>("all");
  if (q.isLoading || !q.data) return <Skeleton active paragraph={{ rows: 10 }} />;
  const d = q.data, dv = d.derived, b = d.brief;
  const grand = d.totals_as_written.find((t) => t.which === "grand");
  const deals = d.rows.filter((r) => r.kind === "deal");
  const shown = filter === "all" ? deals : filter === "owed" ? deals.filter((r) => (r.excess_shortage ?? 0) < -1) : deals.filter((r) => r.verdict === filter);
  const owed = d.rows.filter((r) => (r.excess_shortage ?? 0) < -1).sort((a, b2) => (a.excess_shortage ?? 0) - (b2.excess_shortage ?? 0));
  const dealsOwed = owed.filter((r) => r.kind === "deal");
  const advancesOwed = -dv.advances_outstanding;
  const dealsNet = (grand?.excess_shortage ?? 0) - advancesOwed;

  const cols: ColumnsType<SoldRow> = [
    { title: "Property", dataIndex: "address", key: "address", fixed: "left", render: (v, r) => r.property_id ? <Link href={`/sold/${r.property_id}`} style={{ fontWeight: 500 }}>{v}</Link> : v, sorter: (a, b2) => a.address.localeCompare(b2.address) },
    { title: "St", dataIndex: "state", key: "state", width: 50 },
    { title: "Funded", dataIndex: "investment_date", key: "investment_date", width: 95, render: (v) => fmtDate(v, "MMM yyyy"), sorter: (a, b2) => String(a.investment_date).localeCompare(String(b2.investment_date)), defaultSortOrder: "ascend" },
    { title: "Days", dataIndex: "days_invested", key: "days", width: 65, align: "right", sorter: (a, b2) => (a.days_invested ?? 0) - (b2.days_invested ?? 0) },
    { title: "Put in", dataIndex: "cash_invested", key: "in", align: "right", width: 110, render: usd0, sorter: (a, b2) => (a.cash_invested ?? 0) - (b2.cash_invested ?? 0) },
    { title: "Got back", dataIndex: "cash_collected", key: "out", align: "right", width: 110, render: usd0, sorter: (a, b2) => (a.cash_collected ?? 0) - (b2.cash_collected ?? 0) },
    { title: "Still owed", dataIndex: "excess_shortage", key: "owed", align: "right", width: 110, sorter: (a, b2) => (a.excess_shortage ?? 0) - (b2.excess_shortage ?? 0),
      render: (v: number) => v < -1 ? <Text type="danger" strong>{usd0(-v)}</Text> : v > 1 ? <Text type="success">+{usd0(v)}</Text> : <Text type="secondary">paid in full</Text> },
    { title: "Return / yr", dataIndex: "annualized_roi", key: "roi", align: "right", width: 95, render: pct1, sorter: (a, b2) => (a.annualized_roi ?? 0) - (b2.annualized_roi ?? 0) },
    { title: "Verdict", dataIndex: "verdict", key: "verdict", width: 80, render: (v) => <VerdictTag v={v} /> },
    { title: "Why", dataIndex: "reason", key: "reason", render: (v) => <Text type="secondary" style={{ fontSize: 12.5 }}>{v}</Text> },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 1240 }}>
      <div>
        <Title level={3} style={{ margin: 0 }}>Sold deals</Title>
        <Text type="secondary">{dv.deals} finished deals · figures from {d.workbook.file} ({fmtDate(d.workbook.file_mtime, "d MMM yyyy")})</Text>
        {b?.headline && <Paragraph style={{ fontSize: 15, marginTop: 10, marginBottom: 0 }}>{b.headline}</Paragraph>}
      </div>

      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}><Card styles={PAD}><Statistic title="We put in" value={grand?.cash_invested ?? 0} formatter={(v) => usd0(Number(v))} /><Text type="secondary" style={{ fontSize: 12 }}>29 deals + 5 DC advances</Text></Card></Col>
        <Col xs={12} lg={6}><Card styles={PAD}><Statistic title="We got back" value={grand?.cash_collected ?? 0} formatter={(v) => usd0(Number(v))} /><Text type="secondary" style={{ fontSize: 12 }}>cash actually received</Text></Card></Col>
        <Col xs={12} lg={6}><Card styles={PAD}><Statistic title="Still owed to us" value={-(grand?.excess_shortage ?? 0)} formatter={(v) => usd0(Number(v))} valueStyle={{ color: "var(--ant-color-error)" }} /><Text type="secondary" style={{ fontSize: 12 }}>{usd0(-dealsNet)} on {dealsOwed.length} deals + {usd0(-advancesOwed)} DC advances</Text></Card></Col>
        <Col xs={12} lg={6}><Card styles={PAD}><Statistic title="Profit actually made" value={dv.deals_cash_profit} formatter={(v) => usd0(Number(v))} valueStyle={{ color: dv.deals_cash_profit >= 0 ? "var(--ant-color-success)" : "var(--ant-color-error)" }} /><Text type="secondary" style={{ fontSize: 12 }}>{pct1(dv.cash_profit_annualised)} a year · we billed {pct1(dv.billed_yield_annualised)}</Text></Card></Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Section title="In plain words">
            <Bullets items={b?.in_plain_words ?? [
              `We put ${usd0(grand?.cash_invested)} into these deals and got ${usd0(grand?.cash_collected)} back.`,
              `${usd0(-(grand?.excess_shortage ?? 0))} is still owed: ${usd0(-dealsNet)} across ${dealsOwed.length} deals and ${usd0(-advancesOwed)} of DC advances.`,
              `On the ${dv.deals} finished deals we made ${usd0(dv.deals_cash_profit)} — ${pct1(dv.cash_profit_annualised)} a year — against ${pct1(dv.billed_yield_annualised)} a year we billed.`,
            ]} />
          </Section>
        </Col>
        <Col xs={24} lg={10}>
          <Section title="Still owed to RKB">
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {dealsOwed.map((r) => (
                <div key={r.label} style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 13.5 }}>
                  <Link href={`/sold/${r.property_id}`}>{r.address}</Link>
                  <Text type="danger" strong style={{ whiteSpace: "nowrap" }}>{usd0(-(r.excess_shortage ?? 0))}</Text>
                </div>
              ))}
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 13.5, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
                <Text>Advances against DC properties ({dv.advances})</Text><Text type="danger" strong>{usd0(-advancesOwed)}</Text>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 13.5, fontWeight: 600 }}>
                <span>Total still owed</span><Text type="danger" strong>{usd0(-(grand?.excess_shortage ?? 0))}</Text>
              </div>
              <Text type="secondary" style={{ fontSize: 12 }}>Net of {usd0(dv.deals_excess_sum)} collected above the amount due on other deals.</Text>
            </div>
          </Section>
        </Col>
      </Row>

      <Card title="Every deal" styles={{ body: { padding: 0 } }} extra={
        <Segmented size="small" value={filter} onChange={(v) => setFilter(String(v))} options={[
          { label: `All ${deals.length}`, value: "all" }, { label: `Good ${d.verdict_counts.good}`, value: "good" },
          { label: `Mixed ${d.verdict_counts.mixed}`, value: "mixed" }, { label: `Bad ${d.verdict_counts.bad}`, value: "bad" }, { label: `Owed ${dealsOwed.length}`, value: "owed" }]} />
      }>
        <Table<SoldRow> rowKey="label" size="small" columns={cols} dataSource={shown} pagination={false} scroll={{ x: 1000 }}
          summary={() => (
            <Table.Summary fixed>
              <Table.Summary.Row style={{ fontWeight: 600 }}>
                <Table.Summary.Cell index={0} colSpan={4}>{filter === "all" ? "29 deals (sheet total)" : `${shown.length} shown`}</Table.Summary.Cell>
                <Table.Summary.Cell index={4} align="right">{usd0(shown.reduce((s, r) => s + (r.cash_invested ?? 0), 0))}</Table.Summary.Cell>
                <Table.Summary.Cell index={5} align="right">{usd0(shown.reduce((s, r) => s + (r.cash_collected ?? 0), 0))}</Table.Summary.Cell>
                <Table.Summary.Cell index={6} align="right"><Text type="danger" strong>{usd0(-shown.reduce((s, r) => s + Math.min(r.excess_shortage ?? 0, 0), 0))}</Text></Table.Summary.Cell>
                <Table.Summary.Cell index={7} colSpan={3} />
              </Table.Summary.Row>
            </Table.Summary>
          )} />
      </Card>

      {b && (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}><Section title="The mistakes we kept repeating"><Bullets items={b.biggest_mistakes} numbered /></Section></Col>
            <Col xs={24} lg={12}><Section title="What worked on the good deals"><Bullets items={b.what_worked} /></Section></Col>
          </Row>
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}><Section title="Before we fund the next one"><Bullets items={b.checklist} numbered /></Section></Col>
            <Col xs={24} lg={12}><Section title="Decisions for the CEO"><Bullets items={b.decisions} numbered /></Section></Col>
          </Row>
        </>
      )}

      {d.synthesis && <Card styles={{ body: { padding: "4px 8px" } }}><FullText sections={d.synthesis.sections} label={`Read the full cross-deal analysis (${d.synthesis.sections.length} sections)`} /></Card>}
    </div>
  );
}

// ------------------------------------------------------------------- deal page
export function SoldDealView({ pid }: { pid: string }) {
  const q = useQuery({ queryKey: ["sold-deal", pid], queryFn: () => api.get<SoldDeal>(`/sold/deals/${pid}`), staleTime: 60_000 });
  if (q.isLoading || !q.data) return <Skeleton active paragraph={{ rows: 8 }} />;
  const d = q.data, r = d.row, b = d.brief, pm = d.postmortem;
  const owed = -(r?.excess_shortage ?? 0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20, maxWidth: 1100 }}>
      <div>
        <Link href="/sold" style={{ fontSize: 12.5 }}>← Sold deals</Link>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 4, flexWrap: "wrap" }}>
          <Title level={3} style={{ margin: 0 }}>{d.address}</Title>
          <VerdictTag v={d.verdict || b?.verdict} />
          <Text type="secondary">{[d.city, d.state].filter(Boolean).join(", ")}{r?.section === "lp_sold_kept_cash" ? " · LP sold and kept the cash" : ""}</Text>
        </div>
        {b?.one_line && <Paragraph style={{ fontSize: 15, marginTop: 8, marginBottom: 0 }}>{b.one_line}</Paragraph>}
      </div>

      {r && (
        <Row gutter={[16, 16]}>
          <Col xs={12} lg={6}><Card styles={PAD}><Statistic title="We put in" value={r.cash_invested ?? 0} formatter={(v) => usd2(Number(v))} /><Text type="secondary" style={{ fontSize: 12 }}>funded {fmtDate(r.investment_date, "d MMM yyyy")}</Text></Card></Col>
          <Col xs={12} lg={6}><Card styles={PAD}><Statistic title="We were due" value={r.total_to_collect ?? 0} formatter={(v) => usd2(Number(v))} /><Text type="secondary" style={{ fontSize: 12 }}>principal + {usd0(r.rkb_yield)} billed</Text></Card></Col>
          <Col xs={12} lg={6}><Card styles={PAD}><Statistic title="We got back" value={r.cash_collected ?? 0} formatter={(v) => usd2(Number(v))} /><Text type="secondary" style={{ fontSize: 12 }}>received {fmtDate(r.received_date, "d MMM yyyy")}</Text></Card></Col>
          <Col xs={12} lg={6}><Card styles={PAD}>
            {owed > 1 ? <Statistic title="Still owed" value={owed} formatter={(v) => usd2(Number(v))} valueStyle={{ color: "var(--ant-color-error)" }} />
              : owed < -1 ? <Statistic title="Collected above due" value={-owed} formatter={(v) => "+" + usd2(Number(v))} valueStyle={{ color: "var(--ant-color-success)" }} />
              : <Statistic title="Still owed" value="Paid in full" valueStyle={{ color: "var(--ant-color-success)" }} />}
            <Text type="secondary" style={{ fontSize: 12 }}>{r.days_invested} days · {pct1(r.annualized_roi)} a year billed</Text>
          </Card></Col>
        </Row>
      )}

      {(b?.verdict_reason || d.reason) && <Alert type={d.verdict === "bad" ? "error" : d.verdict === "good" ? "success" : "warning"} showIcon message={<Text strong>Why {VERDICT[d.verdict || b?.verdict || "mixed"]?.label.toLowerCase()}</Text>} description={b?.verdict_reason || d.reason} />}

      {b ? (
        <>
          <Section title="What happened"><Bullets items={b.what_happened} numbered /></Section>
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}><Section title="What went wrong"><Bullets items={b.went_wrong} /></Section></Col>
            <Col xs={24} lg={12}><Section title="What went right"><Bullets items={b.went_right} /></Section></Col>
          </Row>
          <Section title="Lessons for next time"><Bullets items={b.lessons} numbered /></Section>
        </>
      ) : <Alert type="info" showIcon message="Short brief not generated yet for this deal." />}

      {pm && (
        <Card styles={{ body: { padding: "4px 8px" } }}>
          <FullText sections={pm.sections} label={`Read the full post-mortem (${pm.sections.length} sections · ${d.counts.emails} emails, ${d.counts.attachments} attachments, ${d.counts.files} documents read)`} />
        </Card>
      )}
    </div>
  );
}
