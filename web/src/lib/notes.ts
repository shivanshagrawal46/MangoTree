"use client";

/** Notes analysis: types mirroring the dataset JSON served by
 *  `mangotree/notes/routes.py`, plus the two API calls. */
import { api } from "@/lib/api";

export type Pt = [string, number];
export type Scenario = "base" | "bear" | "bull";
export type Proj = Record<Scenario, { path: number[]; values: number[]; cum: number }>;

export type ZipEntry = {
  zip: string; name: string; blurb: string; kind: "rural" | "builder belt" | "established" | "inner city" | "premium"; city: string; county: string; metro: string;
  by_year: Record<string, number>; peak_date: string; peak: number; drawdown: number; yoy: number; chg5: number; chg10: number;
  rent?: number | null; rent_chg5?: number | null; gross_yield?: number | null; zhvf: Record<string, number>; fhfa: Record<string, number>;
  fair: number; over: number; proj: Proj; expected: number; series: Pt[]; notes: string[]; balance: number;
};
export type Aggregate = {
  key: string; label: string; kind: "metro" | "state" | "nation"; by_year: Record<string, number>; peak_date: string; peak: number; drawdown: number; series: Pt[]; above_trend: number;
  fair?: number; over?: number; proj?: Proj;
};
export type SupplyMetric = { now: number; yr_ago: number; y2019: number; y2022: number; series: Pt[] };
export type NoteRow = {
  address: string; short: string; zip: string; area: string; rate: number; payment: number; balance: number; orig_balance: number; sales_price: number; est_value: number;
  ltv: number | null; ltv_at_orig: number; originated: string; due_in_full: string; balloon_year: number; next_due: string; last_paid: string; paid_to: string;
  ytd_interest: number; ytd_principal: number; note?: string | null; behind: number; status: string; zip_since_note: number | null; ltv_2031_base: number | null; ltv_2031_bear: number | null; flags: string[];
};
export type Dataset = {
  id: string; title: string; as_of: string; source_file: string; data_through: string;
  summary: { notes: number; balance: number; value: number; ltv: number; war: number; payment: number; late_n: number; late_balance: number; w5y: number; wyoy: number; outlook: Record<Scenario, number>;
    mortgage_now: number; mortgage_date: string; mortgage_yr_ago: number; fed_funds: string; balloons: { short: string; due: string; balance: number; rate: number }[] };
  zips: ZipEntry[]; aggregates: Aggregate[]; supply: Record<string, Record<"inventory" | "days_to_pending" | "price_cuts", SupplyMetric>>;
  rates: { mortgage30: Pt[]; fed_funds: Pt[]; forecasts: { who: string; when: string; q4_2026: number | null; y2027: number; note: string }[]; investor: { product: string; range: string; note: string }[]; events: [string, string][] };
  hpi: { texas: Pt[]; san_antonio: Pt[] }; cpi: Record<string, number>; notes_table: NoteRow[];
  model: Record<string, string>;
  narrative: { headline: string; points: string[]; texas_up: string[]; texas_down: string[]; loans: string[]; actions: string[]; outside: { who: string; what: string }[]; sources: string[]; caveats: string[] };
};
export type DatasetSummary = { id: string; title: string; as_of: string; data_through: string; source_file: string; summary: Dataset["summary"]; zips: number; headline: string };

export const notesApi = {
  list: () => api.get<{ items: DatasetSummary[] }>("/notes/analyses"),
  get: (id: string) => api.get<Dataset>(`/notes/analyses/${id}`),
};

export const SCENARIO: Record<Scenario, { label: string; color: string; dash?: string }> = {
  bull: { label: "Bull", color: "var(--money-in)", dash: "6 4" },
  base: { label: "Base", color: "var(--accent)" },
  bear: { label: "Bear", color: "var(--critical)", dash: "6 4" },
};
export const KIND_TONE: Record<ZipEntry["kind"], "neutral" | "accent" | "critical" | "high" | "good" | "info"> = {
  rural: "neutral", "builder belt": "high", established: "good", "inner city": "info", premium: "accent",
};

export function pct(x?: number | null, signed = true, digits = 1) {
  if (x === null || x === undefined || isNaN(x)) return "—";
  const v = x * 100;
  return (signed && v > 0 ? "+" : "") + v.toFixed(digits) + "%";
}
export function money(n?: number | null, compact = false) {
  if (n === null || n === undefined || isNaN(n)) return "—";
  if (compact) {
    const a = Math.abs(n);
    if (a >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
    if (a >= 1_000) return `$${Math.round(n / 1_000)}K`;
  }
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}
/** Merge several [date, value] series into chart rows keyed by date (YYYY-MM). */
export function merge(series: Record<string, Pt[]>, from?: string): Record<string, any>[] {
  const rows = new Map<string, Record<string, any>>();
  for (const [name, pts] of Object.entries(series)) {
    for (const [d, v] of pts) {
      if (from && d < from) continue;
      const k = d.slice(0, 7);
      const r = rows.get(k) || { date: k };
      r[name] = v; rows.set(k, r);
    }
  }
  return [...rows.values()].sort((a, b) => (a.date < b.date ? -1 : 1));
}
/** Rebase a series so the value at `at` (YYYY-MM) is 100. */
export function rebase(pts: Pt[], at: string): Pt[] {
  const b = pts.find(([d]) => d.startsWith(at))?.[1];
  if (!b) return pts;
  return pts.map(([d, v]) => [d, Math.round((v / b) * 1000) / 10]);
}
export function pmt(principal: number, annualRate: number, months = 360) {
  const m = annualRate / 12;
  if (m === 0) return principal / months;
  return (principal * m) / (1 - Math.pow(1 + m, -months));
}
