"use client";

/** ARV / underwriting — types mirroring `mangotree/arv`, the API calls, and
 *  the job stream (its own event kinds: phase, partial, status, delta). */
import { api, ApiError } from "@/lib/api";

export type PropertyType = "single_family" | "townhouse" | "condo" | "multi_2_4" | "manufactured";
export type Condition = "C1" | "C2" | "C3" | "C4" | "C5" | "C6";
export type Basement = "unknown" | "none" | "unfinished" | "finished";

export type Occupancy = "vacant" | "owner" | "tenant";
export type Subject = {
  address: string; unit?: string | null; property_type: PropertyType; beds?: number | null; baths?: number | null; sqft?: number | null; lot_sqft?: number | null;
  year_built?: number | null; stories?: number | null; garage_spaces?: number | null; basement: Basement; pool?: boolean | null;
  condition_current?: Condition | null; condition_after: Condition; quality?: string | null; notes?: string | null; lat?: number | null; lon?: number | null;
  // as repaired, when the plan changes the house itself
  beds_after?: number | null; baths_after?: number | null; sqft_after?: number | null;
  // structure, systems, site, situation
  roof_age_years?: number | null; heating?: string | null; cooling?: string | null; foundation?: string | null; exterior?: string | null; parking?: string | null;
  zoning?: string | null; view?: string | null; waterfront?: boolean | null; occupancy?: Occupancy | null; hoa_monthly?: number | null;
  known_issues?: string[]; planned_work?: string[]; fact_sources?: Record<string, string>;
  filled_from_records?: string[];
};

/* A document attached to the file, as read by the model. */
export type DocFinding = { title: string; severity: "critical" | "high" | "watch" | "info"; detail: string; cost_hint?: string };
export type DocExtract = {
  doc_type: string; doc_type_label?: string; title: string; summary: string; document_date?: string; prepared_by?: string; subject_address?: string;
  subject_facts: Record<string, any>; values: { label: string; amount: number; as_of?: string }[];
  rehab_items: { label: string; amount: number; category: string; note?: string }[]; rehab_total?: number; findings: DocFinding[];
  comps: { address: string; sale_price: number; sale_date?: string; beds?: number; baths?: number; sqft?: number; lot_sqft?: number; year_built?: number; distance_mi?: number; condition?: string; adjusted_price?: number; note?: string }[];
  photo_notes?: { area: string; condition?: string; note: string }[]; open_questions: string[]; legibility: "clear" | "partial" | "poor";
};
export type ArvDocument = {
  doc_id: string; analysis_id?: string | null; sha256?: string; filename: string; size: number; content_type?: string | null; hint?: string | null;
  uploaded_by?: string; uploaded_by_name?: string; uploaded_at: string; status: "reading" | "done" | "failed"; error?: string | null;
  pages?: number; vision_pages?: number; chars?: number; extract_errors?: string[]; extracted?: DocExtract | null; cost_usd?: number; read_at?: string; text_excerpt?: string;
};

export type Geocode = { provider: string; formatted: string; matched_address?: string; lat: number; lon: number; line1?: string; city?: string; state?: string; state_name?: string;
  zip?: string; county?: string; tract?: string; tract_geoid?: string; precision?: string };

export type AdjLine = { key: string; label: string; amount: number; why: string; rate?: number | null };
export type Adjustment = { lines: AdjLine[]; net: number; gross: number; net_pct: number; gross_pct: number; adjusted_price: number; adjusted_ppsf?: number | null; flags: string[] };
export type Comp = {
  comp_id: string; source: "rentcast" | "web" | "manual" | "attom" | "document"; address?: string | null; lat?: number | null; lon?: number | null; distance_mi?: number | null;
  sale_price?: number | null; sale_date?: string | null; price_basis: "closed" | "listed" | "estimated"; listing_price?: number | null; dom?: number | null;
  property_type?: PropertyType | null; beds?: number | null; baths?: number | null; sqft?: number | null; lot_sqft?: number | null; year_built?: number | null;
  garage_spaces?: number | null; basement?: Basement; pool?: boolean | null; condition?: Condition | null; url?: string | null; note?: string | null;
  included?: boolean | null; manual_adjustments?: { label: string; amount: number; why?: string }[];
  similarity?: number; deductions?: { points: number; reason: string }[]; fails?: string[]; selected?: boolean; forced?: boolean; weight?: number;
  months_since_sale?: number | null; adjustment?: Adjustment | null;
};

export type Rates = Record<string, any> & { gla_per_sqft: number; gla_basis: string; market_monthly_pct: number; market_basis: string; median_ppsf: number; median_sale_price: number };
export type Reconciliation = {
  indicated_value: number | null; sales_comparison?: number; weighted_mean?: number; weighted_median?: number; range_low?: number; range_high?: number; comp_low?: number; comp_high?: number;
  std_dev?: number; cv_pct?: number | null; ppsf_indication?: number | null; ppsf_used?: number | null; avm?: Avm | null; avm_note?: string | null;
  bracketing?: { size: boolean; value: boolean; unadjusted: boolean }; comps_used?: number; value_per_sqft?: number | null; error?: string;
};
export type Confidence = { score: number; grade: string; band_pct: number | null; reasons: string[]; label?: string };
export type Valuation = {
  arv: number | null; arv_low: number | null; arv_high: number | null; rates: Rates; reconciliation: Reconciliation; confidence: Confidence;
  selection: { radius_used: number; months_used: number; notes: string[]; candidates: number; qualified: number }; computed_at: string;
};
export type Avm = { source: string; value: number | null; low?: number | null; high?: number | null; as_of?: string; note?: string };

export type RehabLine = { key: string; label: string; amount: number; estimated?: boolean };
export type RehabInput = { mode?: "tier" | "lines" | "lump"; scope?: string; level?: "low" | "mid" | "high"; lines?: RehabLine[]; lump_sum?: number | null;
  source?: string | null; source_doc_id?: string | null; auto?: boolean;
  adders?: { location_multiplier?: number; permit_pct?: number; oh_and_p_pct?: number; contingency_pct?: number } };
export type RehabBudget = {
  mode: string; scope: string; level: string; basis: string; tier: { label: string; rate_per_sqft: number; state_multiplier: number; subtotal: number; range: { low: number; high: number }; covers: string };
  lines: RehabLine[]; adders: Record<string, number>; subtotal: number; location_adjustment: number; permit_cost: number; oh_and_p: number; contingency: number; total_adders: number; total: number; per_sqft?: number | null;
  source?: string | null; source_doc_id?: string | null;
};

export type Assumptions = Record<string, any>;
export type Deal = {
  purchase_price: number; fixed_costs: number; financing: { loan: number; interest: number; points: number; fees: number; total: number; ltarv_pct: number; ltc_pct: number; binding: string };
  holding_total: number; selling_total: number; total_project_cost: number; profit: number; profit_pct_of_arv: number | null; profit_per_month: number | null; cash_needed: number;
  cash_roi_pct: number | null; annualized_roi_pct: number | null; capital: { debt: number; equity: number; total: number };
};
export type Underwriting = {
  assumptions: Assumptions; arv: number; rehab_total: number; buying: Record<string, number>; holding: Record<string, any>; selling: Record<string, any>;
  mao: { rule: number; rule_pct: number; profit_target: number; target_profit: number; recommended: number; basis: string }; deal: Deal; price_basis: string;
  checks: { key: string; label: string; value: number | null; limit: number | null; ok: boolean; note: string }[];
  sensitivity: { arv_delta_pct: number; cells: { rehab_delta_pct: number; profit: number; profit_pct: number | null }[] }[]; value_per_sqft?: number | null; rehab_per_sqft?: number | null;
};

export type Market = {
  gathered_at?: string; gaps: string[];
  mortgage?: { as_of: string; rate_pct: number; year_ago_pct?: number | null; history: { date: string; rate: number }[]; source: string } | null;
  zhvi?: { zip: string; as_of: string; value: number; metro?: string | null; county?: string | null; change_12m_pct?: number | null; change_3m_pct?: number | null; change_36m_pct?: number | null; monthly_rate_pct?: number | null; history: { date: string; value: number }[]; source: string } | null;
  flood?: { zone: string; subtype?: string | null; sfha: boolean; bfe?: any; note: string; source: string } | null;
  acs?: { name?: string; vintage?: string; median_home_value?: number | null; median_household_income?: number | null; median_year_built?: number | null; owner_share_pct?: number | null; vacancy_pct?: number | null; population?: number | null; source?: string } | null;
};

export type Analysis = {
  headline: string; arv_opinion: number; arv_low: number; arv_high: number; agrees_with_engine: boolean; confidence: string; confidence_reason: string; summary: string;
  summary_points?: string[]; value_drivers?: { factor: string; impact_usd: number; why: string }[];
  verdict?: "fund" | "fund_with_conditions" | "renegotiate" | "pass"; verdict_reason?: string;
  comp_notes: { comp_id: string; verdict: "keep" | "drop" | "caution" | "add"; note: string; suggested_condition?: string; suggested_adjustments?: { label: string; amount: number; why: string }[] }[];
  adjustment_critique?: { rate: string; engine_value?: string; your_view: string; why: string }[]; market_read: string; rehab_read: string; mao_opinion: number; mao_reason: string;
  risks: { title: string; severity: "critical" | "high" | "watch"; detail: string }[]; questions: { question: string; why_it_matters: string; impact: "high" | "medium" | "low" }[];
  next_steps: string[]; model?: string; effort?: string; usage?: { total_cost_usd?: number }; at?: string;
};

export type Phase = { key: string; label: string; status: "running" | "done" | "skipped" | "failed"; detail?: string; at?: string };
export type ChatMsg = { role: "user" | "assistant"; content: string; by?: string; by_name?: string; at: string; model?: string };
export type Records = Record<string, any> | null;

export type AnalysisDoc = {
  analysis_id: string; status: "queued" | "running" | "done" | "failed"; error?: string | null; job_id?: string; created_at: string; updated_at: string;
  created_by?: string; created_by_name?: string; property_id?: string | null; label?: string | null;
  subject: Subject; subject_input?: Subject; geocode?: Geocode; records?: Records; market?: Market; avm?: Avm | null; comps: Comp[]; documents?: ArvDocument[];
  web_search?: { notes?: string; searches?: number; found?: number } | null;
  rehab: RehabInput; rehab_budget?: RehabBudget; assumptions: Assumptions; settings: Record<string, any>; valuation?: Valuation; underwriting?: Underwriting;
  analysis?: Analysis; analysis_error?: string | null; answers: { question: string; answer: string }[]; chat: ChatMsg[]; phases: Phase[]; data_sources: string[]; cost_usd?: number;
};

export type AnalysisSummary = {
  analysis_id: string; created_at: string; updated_at?: string; created_by_name?: string; status: string; error?: string | null; label?: string | null; property_id?: string | null;
  subject?: Partial<Subject>; geocode?: Partial<Geocode>; valuation?: { arv?: number | null; arv_low?: number | null; arv_high?: number | null; confidence?: Confidence };
  analysis?: { headline?: string; arv_opinion?: number }; underwriting?: { mao?: Underwriting["mao"] }; rehab_budget?: { total?: number }; comps_count?: number;
};

export type ArvStatus = {
  providers: { census_geocoder: boolean; web_search: boolean; rentcast: boolean; attom: boolean; google_maps: boolean; model: string; effort: string }; doc_types?: Record<string, string>;
  defaults: { assumptions: Assumptions; adders: Record<string, number>; radius_mi: number; months: number; target: number };
  scopes: Record<string, { label: string; low: number; mid: number; high: number; covers: string }>; line_items: { key: string; label: string }[];
  conditions: Record<string, string>; property_types: PropertyType[]; state_cost_index: Record<string, number>; properties: { property_id: string; address: string }[];
};

export const arvApi = {
  status: () => api.get<ArvStatus>("/arv/status"),
  geocode: (q: string) => api.get<Geocode>(`/arv/geocode?q=${encodeURIComponent(q)}`),
  list: (limit = 50) => api.get<{ items: AnalysisSummary[] }>(`/arv/analyses?limit=${limit}`),
  create: (body: { subject: Partial<Subject>; assumptions?: Assumptions; rehab?: RehabInput; settings?: Record<string, any>; property_id?: string | null; label?: string | null; document_ids?: string[] }) =>
    api.post<{ analysis_id: string; job_id: string }>("/arv/analyses", body),
  /** Upload documents. Without an analysis id they are staged for the wizard; the job reads each one. */
  uploadDocuments: (files: File[], opts: { analysisId?: string; hint?: string; address?: string } = {}) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    if (opts.analysisId) form.append("analysis_id", opts.analysisId);
    if (opts.hint) form.append("hint", opts.hint);
    if (opts.address) form.append("address", opts.address);
    return api.upload<{ job_id: string; documents: ArvDocument[] }>("/arv/documents", form);
  },
  document: (docId: string) => api.get<ArvDocument>(`/arv/documents/${docId}`),
  docFileUrl: (docId: string) => `/api/arv/documents/${docId}/file`,
  detachDocument: (id: string, docId: string) => api.del<AnalysisDoc>(`/arv/analyses/${id}/documents/${docId}`),
  applyDocument: (id: string, docId: string, what: "facts" | "rehab" | "comps", keys?: string[]) => api.post<AnalysisDoc>(`/arv/analyses/${id}/documents/${docId}/apply`, { what, keys }),
  get: (id: string) => api.get<AnalysisDoc>(`/arv/analyses/${id}`),
  patch: (id: string, body: { subject?: Partial<Subject>; assumptions?: Assumptions; rehab?: RehabInput; settings?: Record<string, any>; comps?: Partial<Comp>[]; answers?: { question: string; answer: string }[]; label?: string }) =>
    api.patch<AnalysisDoc>(`/arv/analyses/${id}`, body),
  addComp: (id: string, comp: Partial<Comp>) => api.post<AnalysisDoc>(`/arv/analyses/${id}/comps`, { comp }),
  deleteComp: (id: string, compId: string) => api.del<AnalysisDoc>(`/arv/analyses/${id}/comps/${compId}`),
  rerun: (id: string, stages: string[]) => api.post<{ job_id: string }>(`/arv/analyses/${id}/rerun`, { stages }),
  chat: (id: string, question: string) => api.post<{ job_id: string }>(`/arv/analyses/${id}/chat`, { question }),
  remove: (id: string) => api.del<{ ok: boolean }>(`/arv/analyses/${id}`),
  pdfUrl: (id: string) => `/api/arv/analyses/${id}/report.pdf`,
  xlsxUrl: (id: string) => `/api/arv/analyses/${id}/calculator.xlsx`,
};

export type ArvEvent = { seq: number; t: number; kind: string; data: any };

/** Subscribe to an ARV job. The ARV pipeline emits `phase`, `partial`, `status`
 *  and (for chat) `delta`, which the generic subscriber does not listen for. */
export function subscribeArvJob(jobId: string, onEvent: (e: ArvEvent) => void, onEnd?: () => void): () => void {
  const es = new EventSource(`/api/jobs/${jobId}/stream`, { withCredentials: true });
  const handler = (ev: MessageEvent) => { try { onEvent(JSON.parse(ev.data)); } catch {} };
  ["job", "phase", "partial", "status", "delta", "result", "error", "end"].forEach((k) => es.addEventListener(k, handler as EventListener));
  es.addEventListener("end", () => { es.close(); onEnd?.(); });
  es.onerror = () => {
    if (es.readyState === EventSource.CLOSED) {
      onEvent({ seq: -1, t: 0, kind: "error", data: { error: "The server restarted before this finished. Re-run the stage." } });
      onEnd?.();
    }
  };
  return () => es.close();
}

export { ApiError };

/* ---------------------------------------------------------------- labels */
export const PROPERTY_TYPE_LABEL: Record<PropertyType, string> = { single_family: "Single family", townhouse: "Townhouse / rowhouse", condo: "Condo", multi_2_4: "2–4 units", manufactured: "Manufactured" };
export const SOURCE_LABEL: Record<string, string> = { rentcast: "RentCast (MLS-derived)", web: "Web search", manual: "Entered by hand", attom: "ATTOM", document: "From a document" };

/* Structured intake vocab. Short labels; the value is what is stored. */
export const KNOWN_ISSUES: [string, string][] = [["foundation", "Foundation cracks / settlement"], ["water", "Water intrusion / flooding"], ["roof_leak", "Roof leaks"], ["mold", "Mold"],
  ["termite", "Termite / pest damage"], ["knob_tube", "Knob-and-tube or aluminum wiring"], ["galvanized", "Galvanized or polybutylene plumbing"], ["sewer", "Sewer line / septic"],
  ["asbestos_lead", "Asbestos / lead paint"], ["fire", "Fire or smoke damage"], ["unpermitted", "Unpermitted work"], ["violations", "Code violations / liens"], ["hoarder", "Hoarder / heavy cleanout"], ["structural", "Sagging floors / structural"]];
export const PLANNED_WORK: [string, string][] = [["kitchen", "Kitchen"], ["baths", "Bathrooms"], ["roof", "Roof"], ["hvac", "HVAC"], ["electrical", "Electrical"], ["plumbing", "Plumbing"], ["windows", "Windows & doors"],
  ["flooring", "Flooring"], ["paint", "Paint (in & out)"], ["exterior", "Siding / exterior"], ["landscaping", "Landscaping / hardscape"], ["basement", "Finish basement"], ["layout", "Open the layout"],
  ["add_bath", "Add a bathroom"], ["add_bed", "Add a bedroom"], ["addition", "Addition / pop-up"], ["adu", "ADU / garage conversion"], ["foundation_repair", "Foundation repair"]];
export const HEATING: [string, string][] = [["", "Unknown"], ["forced_air_gas", "Forced air (gas)"], ["forced_air_electric", "Forced air (electric)"], ["heat_pump", "Heat pump"], ["boiler_radiators", "Boiler / radiators"], ["baseboard", "Electric baseboard"], ["mini_split", "Mini-split"], ["none", "None"]];
export const COOLING: [string, string][] = [["", "Unknown"], ["central", "Central air"], ["heat_pump", "Heat pump"], ["mini_split", "Mini-split"], ["window", "Window units"], ["none", "None"]];
export const FOUNDATION: [string, string][] = [["", "Unknown"], ["basement", "Full basement"], ["crawl", "Crawl space"], ["slab", "Slab"], ["pier", "Pier & beam"], ["mixed", "Mixed"]];
export const EXTERIOR: [string, string][] = [["", "Unknown"], ["brick", "Brick"], ["vinyl", "Vinyl"], ["wood", "Wood / clapboard"], ["stucco", "Stucco"], ["fiber_cement", "Fiber cement"], ["stone", "Stone"], ["block", "Block"], ["mixed", "Mixed"]];
export const PARKING: [string, string][] = [["", "Unknown"], ["street", "Street only"], ["driveway", "Driveway"], ["carport", "Carport"], ["garage_attached", "Attached garage"], ["garage_detached", "Detached garage"], ["none", "None"]];
export const OCCUPANCY: [string, string][] = [["", "Unknown"], ["vacant", "Vacant"], ["owner", "Owner occupied"], ["tenant", "Tenant in place"]];
export const EXIT: [string, string][] = [["flip", "Fix and flip"], ["brrrr", "Fix and refinance (BRRRR)"], ["wholetail", "Wholetail"], ["rental", "Hold as rental"]];
export const ACQUISITION: [string, string][] = [["mls", "MLS listing"], ["off_market", "Off market / direct"], ["wholesale", "Wholesaler assignment"], ["auction", "Auction / REO"], ["owned", "Already owned"]];
export const VERDICT_LABEL: Record<string, string> = { fund: "Fund", fund_with_conditions: "Fund with conditions", renegotiate: "Renegotiate", pass: "Pass" };
export const VERDICT_TONE: Record<string, "good" | "high" | "critical" | "neutral"> = { fund: "good", fund_with_conditions: "high", renegotiate: "high", pass: "critical" };
export const FACT_LABEL: Record<string, string> = { property_type: "Type", beds: "Bedrooms", baths: "Bathrooms", sqft: "Living area", lot_sqft: "Lot", year_built: "Year built", stories: "Stories", garage_spaces: "Garage",
  basement: "Basement", pool: "Pool", condition_current: "Condition today", roof_age_years: "Roof age", heating: "Heating", cooling: "Cooling", foundation: "Foundation", exterior: "Exterior",
  occupancy: "Occupancy", hoa_monthly: "HOA / month", property_taxes_annual: "Taxes / year", zoning: "Zoning", waterfront: "Waterfront" };
export function fmtFact(k: string, v: any): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (k === "sqft" || k === "lot_sqft") return `${Number(v).toLocaleString()} sqft`;
  if (k === "hoa_monthly" || k === "property_taxes_annual") return `$${Number(v).toLocaleString()}`;
  if (k === "roof_age_years") return `${v} yrs`;
  if (k === "property_type") return PROPERTY_TYPE_LABEL[v as PropertyType] || String(v);
  return String(v).replace(/_/g, " ");
}
export const GRADE_TONE: Record<string, "good" | "accent" | "high" | "critical" | "neutral"> = { A: "good", B: "accent", C: "high", D: "critical", E: "critical", F: "critical" };

export function pct(v?: number | null, nd = 1) { return v === null || v === undefined || isNaN(v) ? "—" : `${v.toFixed(nd)}%`; }
export function num(v?: number | null, nd = 0) { return v === null || v === undefined || isNaN(v) ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: nd, minimumFractionDigits: nd }); }
export function signed(v?: number | null) { if (v === null || v === undefined || isNaN(v)) return "—"; const s = Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 }); return v < 0 ? `−$${s}` : v > 0 ? `+$${s}` : "$0"; }
