"use client";

/* Underwriting: the shapes the API returns and the few words every page shares.
   The rules themselves (the 65% rule, the foreclosure test, which lines a deal
   needs) live on the server; nothing here decides anything. */

import { api } from "@/lib/api";

export type Lien = { holder: string; balance: number | string; status: string; payoff_at_closing: boolean; monthly_payment?: number | string; arrears?: number | string };
export type Facts = {
  address?: string; state?: string; county?: string; arv?: number; as_is_value?: number; arv_analysis_id?: string;
  sqft_above?: number; sqft_below?: number; measured?: boolean; beds?: number; baths?: number; scope?: string;
  owner_kind?: string; owner_name?: string; owner_lives_there?: boolean; has_mortgage?: boolean; liens?: Lien[];
  builder_cost?: number; contingency?: number; contract_price?: number; closing_costs?: number; cash_to_owner?: number;
  rate?: number; points?: number; term_months?: number; interest_free_months?: number; first_six_inside_note?: boolean; minimum_interest_months?: number;
  contractor_name?: string; contractor_licence?: string; title_company?: string; closing_date?: string; tax_id?: string; legal_description?: string;
  borrower_address?: string; signer_name?: string; guarantor_name?: string; completion_date?: string;
};
export type MoneyLine = { key: string; label: string; amount: number; note?: string | null };
export type ForcedSale = { value: number; marked_down: number; after_selling: number; mortgage_ahead: number; mortgage_growth: number; holding: number; foreclosure_cost: number; months: number;
  left_for_rkb: number; rkb_out: number; passed: boolean | null; cover: number | null; checked?: boolean; applies?: boolean; why?: string };
export type Assessment = {
  verdict: "fits" | "exception" | "does_not_fit" | "incomplete"; headline: string; reasons: string[]; notices: string[]; missing: string[];
  kind: string; kind_label: string; lien_position: "first" | "second"; state: string | null; state_name: string | null; new_state?: boolean; rejected_second_mortgage?: boolean;
  /* `principal` is the note: the owner's number, what the documents carry. `at_risk` is RKB's own money in the deal
     (90% of the builder's cost, RKB's points and six months' interest on it, ROI Blocks at closing, and any payoff):
     the figure the 65% rule and the foreclosure test use. `commission` is ROI Blocks' share, worked out on the server. */
  money: { lines: MoneyLine[]; cash_out: number; day_one: number; principal: number; principal_is_contract: boolean; staying: number; work: number; contingency: number; liens: Lien[];
    funded: number; rkb_points: number; rkb_interest: number; roi_at_closing: number; at_risk: number;
    commission: { known: boolean; note: number; beside: number; retail: number; builder_cost: number; points: number; interest: number; margin: number; at_closing: number; at_sale: number; to_roi: number } };
  rule_65: { passed: boolean; ratio: number | null; limit: number; exposure: number; limit_amount: number; room: number; rkb_money: number; parts: { label: string; amount: number }[];
    rate: number; points: number; term_months: number; interest_free_months: number };
  /* One case only, at the after-repair value. The house's value today is not used. */
  foreclosure: { passed: boolean; finished: ForcedSale; state_route: string; estimate: boolean; worst_case?: boolean };
  budget: { passed: boolean; looks: string[]; per_sqft: number | null; range: [number, number] | null; area: number; scope: string | null; contingency_share: number | null };
};
export type Field = { key: string; label: string; type: string };
export type Paper = { sha256: string; filename: string; size?: number; uploaded_at?: string; uploaded_by_name?: string; error?: string | null;
  reading?: { what_it_is: string; right_paper: boolean; passed: boolean; checks: { n: number; check: string; result: "met" | "not_met" | "not_shown"; quote: string; note: string }[];
    figures: { label: string; value: string }[]; also_noticed: string[]; legibility: string; cost_usd?: number } | null };
export type Item = {
  key: string; party: string; name: string; label: string; why: string; when: "file" | "closing" | "after"; needs_file: boolean; hard: boolean; foreclosure: boolean; required: boolean;
  checks: string[]; fields: Field[]; confirm?: string | null; verify: "reading" | "rkb" | "filled"; status: "open" | "received" | "ok" | "rejected"; reason?: string | null;
  values?: Record<string, string | boolean>; files?: Paper[]; updated_at?: string;
  confirmed?: { by: string; by_name: string; at: string; note?: string | null; without_paper: boolean; over_a_failed_reading: boolean } | null;
};
export type Named = { key: string; party: string; name: string; label: string; status: string };
export type Gates = {
  step: number; steps: { n: number; name: string; done: boolean; open: boolean }[]; verdict_ok: boolean; details_missing: string[]; file_complete: boolean;
  documents: { total: number; approved: number }; ready_to_sign: boolean; signed: boolean; can_fund: boolean;
  waiting_file: Named[]; waiting_closing: Named[]; waiting_after: Named[]; optional_open: Named[]; counts: Record<string, number>; confirmed_on_rkbs_word: Named[];
  foreclosure_file: { total: number; held: number; missing: Named[]; complete: boolean }; by_party: Record<string, { total: number; done: number; compulsory_left: number }>;
};
export type Block = { t: "title" | "terms" | "h" | "p" | "list" | "sign" | "notary" | "note" | "break"; text?: string; sub?: string | null; lead?: string | null; rows?: string[][]; items?: string[]; party?: string; lines?: string[] };
export type DocMeta = { key: string; title: string; signers: string; early: boolean; missing: string[]; basis: string; version: number; edited?: boolean; behind_the_file?: boolean; foot?: string;
  saved_by?: string; saved_at?: string; approved?: { at?: string; by_name?: string; version?: number; voided_because?: string } | null };
export type Doc = DocMeta & { blocks: Block[] };
export type UwEvent = { at: string; by_name?: string; side?: string; what: string };
export type UwFile = {
  file_id: string; facts: Facts; from_arv?: Facts; assessment: Assessment; items: Item[]; gates: Gates; documents: DocMeta[]; events: UwEvent[];
  edited: { field: string; arv_file: number | string; now: number | string }[]; created_by_name?: string; created_side?: string; created_at?: string; updated_at?: string;
  signed?: { at?: string; by_name?: string; ratio?: number; principal?: number; on_rkbs_word?: string[]; voided_because?: string } | null;
  me: { user_id: string; name?: string; contractor: boolean; can_sign: boolean; can_confirm: boolean; confirm_password_set: boolean };
};
export type UwRow = { file_id: string; facts: Facts; assessment?: Partial<Assessment> & { money?: { principal: number } }; gates?: Gates; signed?: { at?: string } | null; created_by_name?: string; created_side?: string; updated_at?: string };
export type UwConfig = {
  states: { code: string; name: string; instrument: string; foreclosure: { route: string; months: number; cost: number; estimate: boolean }; licence_register: string; notes: string[]; mechanics_lien: string }[];
  md_counties: string[]; owner_kinds: { key: string; label: string }[]; lien_status: { key: string; label: string }[]; scopes: { key: string; label: string; covers: string }[];
  limits: { arv: number; rate: number; points: number; term_months: number };
};
export type ArvChoice = { analysis_id: string; address: string; state?: string; arv: number; as_is?: number; sqft?: number; by?: string; at?: string };

export const uw = {
  config: () => api.get<UwConfig>("/uw/config"),
  list: () => api.get<{ items: UwRow[]; can_delete?: boolean }>("/uw/files"),
  arvFiles: () => api.get<{ items: ArvChoice[] }>("/uw/arv-files"),
  create: (facts: Facts, arv_analysis_id?: string) => api.post<UwFile>("/uw/files", { facts, arv_analysis_id }),
  get: (id: string) => api.get<UwFile>(`/uw/files/${id}`),
  remove: (id: string) => api.del<{ ok: boolean }>(`/uw/files/${id}`),
  setFacts: (id: string, facts: Facts) => api.patch<UwFile>(`/uw/files/${id}`, { facts }),
  fill: (id: string, key: string, values: Record<string, string | boolean>) => api.patch<UwFile>(`/uw/files/${id}/items/${key}`, { values }),
  sendBack: (id: string, key: string, reason: string) => api.patch<UwFile>(`/uw/files/${id}/items/${key}`, { status: "rejected", reason }),
  reopen: (id: string, key: string) => api.patch<UwFile>(`/uw/files/${id}/items/${key}`, { status: "open" }),
  confirm: (id: string, key: string, password: string, note?: string) => api.post<UwFile>(`/uw/files/${id}/items/${key}/confirm`, { password, note }),
  upload: (id: string, key: string, files: File[]) => { const f = new FormData(); files.forEach((x) => f.append("files", x)); return api.upload<UwFile & { job_id: string }>(`/uw/files/${id}/items/${key}/upload`, f); },
  paperUrl: (id: string, sha: string) => `/api/uw/files/${id}/papers/${sha}`,
  makeDocuments: (id: string) => api.post<UwFile>(`/uw/files/${id}/documents`),
  document: (id: string, key: string) => api.get<Doc>(`/uw/files/${id}/documents/${key}`),
  editDocument: (id: string, key: string, blocks: Block[]) => api.patch<UwFile & { document: Doc }>(`/uw/files/${id}/documents/${key}`, { blocks }),
  restoreDocument: (id: string, key: string) => api.patch<UwFile & { document: Doc }>(`/uw/files/${id}/documents/${key}`, { restore: true }),
  approveDocument: (id: string, key: string, approved: boolean) => api.patch<UwFile & { document: Doc }>(`/uw/files/${id}/documents/${key}`, { approved }),
  wordUrl: (id: string, key: string) => `/api/uw/files/${id}/documents/${key}/word`,
  sign: (id: string) => api.post<UwFile>(`/uw/files/${id}/sign`),
};

export type StateSheet = {
  code: string; status: "proposed" | "approved"; proposed_by_name?: string; proposed_at?: string; approved_by_name?: string; approved_at?: string; cost_usd?: number;
  sheet: { name: string; instrument: string; instrument_must_have: string[]; trustee_rule?: string; foreclosure: { route: string; months: number; months_owner_occupied: number; cost: number; steps: string[] };
    redemption?: string; licence_register: string; lender_licence: string; rate_limits: string[]; recording_tax: { basis: string; percent_of_amount: number }; mechanics_lien: string;
    contract_must_have: string[]; notes: string[]; extra_lines: { key: string; name: string; label: string; why: string; party: string; when: string; required: boolean; needs_file: boolean }[];
    sources: { rule: string; citation: string; url: string }[]; unsure: string[] };
};
export const uwStates = {
  get: (code: string) => api.get<StateSheet>(`/uw/states/${code}`),
  propose: (code: string) => api.post<{ job_id: string; code: string }>(`/uw/states/${code}/propose`),
  approve: (code: string, password: string, drop: string[] = []) => api.post<StateSheet>(`/uw/states/${code}/approve`, { password, drop }),
};

export const PARTY: Record<string, string> = { owner: "Owner", roi: "Wes · ROI Blocks", contractor: "Contractor", title: "Title company", insurance: "Insurance agent", attorney: "RKB's attorney", rkb: "RKB" };
export const PARTY_ORDER = ["owner", "roi", "contractor", "title", "insurance", "attorney", "rkb"];

/** How a line stands, in the five words the pages use. */
export type LineState = "needed" | "check" | "back" | "done" | "confirmed";
export const lineState = (i: Item): LineState => (i.status === "ok" ? (i.confirmed ? "confirmed" : "done") : i.status === "received" ? "check" : i.status === "rejected" ? "back" : "needed");
export const isDone = (i: Item) => i.status === "ok";
export const LINE_ORDER: Record<LineState, number> = { back: 0, needed: 1, check: 2, done: 3, confirmed: 3 };

export const usd = (n?: number | string | null, cents = false) =>
  n === null || n === undefined || n === "" || Number.isNaN(Number(n)) ? "" : Number(n).toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 });
export const pct = (x?: number | null, d = 1) => (x === null || x === undefined ? "" : (x * 100).toFixed(d) + "%");
export const num = (n?: number | string | null) => (n === null || n === undefined || n === "" ? "" : Number(n).toLocaleString("en-US"));
export const errText = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");
/** A day from an ISO string, in US Eastern, as "20 Nov". */
export const day = (v?: string | null) => {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "America/New_York" });
};
