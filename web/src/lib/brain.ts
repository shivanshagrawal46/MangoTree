/* Types for the property files, drafts and chat (the "brain" API). */

export type Person = { name?: string; email?: string };

export type OpenItem = {
  id: string; title: string; detail?: string; owner: string; kind: "reply" | "action" | "waiting" | "decision";
  due?: string | null; status: "open" | "done" | "dropped"; opened_at?: string; source_sha?: string | null;
  closed_at?: string | null; closed_note?: string | null; closed_by?: string | null;
  property_id: string; property: string; overdue?: boolean;
};

export type Activity = {
  property_id: string; property?: string; at: string; kind: string; who?: string; title: string; summary?: string;
  sha?: string | null; relevance?: string;
};

export type OpenPoint = { kind: string; label: string; statement: string; record_shows: string; fix?: string };
/** The second reading: every statement checked against the records before the person sees it. */
export type Verification = {
  ran: boolean; passes: number; statements_checked: number; found: number; corrected: number; open: OpenPoint[]; error?: string | null;
};
export type Lesson = {
  lesson_id: string; rule: string; applies_to: string; applies_now: string; kind: string; shown_by?: string; times_seen?: number;
  recipient_names?: string; learned_on?: string; properties?: string[]; created_at?: string; source?: string;
  from?: { subject?: string; what_he_changed?: string; at?: string; before?: string; after?: string; instruction?: string };
};

export type Draft = {
  draft_id: string; status: "ready" | "sent" | "dismissed" | "superseded"; subject: string; body: string; note?: string;
  to: Person[]; cc: Person[]; property_ids: string[]; properties?: { property_id: string; name: string }[];
  reply_to_sha?: string | null; origin?: "auto" | "chat"; why?: string; urgency?: string; sender?: string; edited?: boolean;
  created_at: string; updated_at?: string; lookups?: string[]; kept_ratio?: number; sent_as_written?: boolean;
  verification?: Verification; checks?: { figures?: number; not_found?: string[] }; stale?: { since?: string; why?: string; kind?: string } | null;
  /** The email this draft answers. */
  in_reply_to?: { subject?: string; from?: string[]; from_name?: string; date?: string; sha?: string };
  /** Set when RKB sent the reply and the draft closed itself. */
  sent_by?: string; sent_at?: string; closed_note?: string; auto_closed?: boolean; answered_before_draft?: boolean;
  answer_check?: { status?: string; candidates?: number; checked_at?: string; what_rkb_said?: string; still_owed?: string[] };
  sent_sha?: string | null;
  outlook?: { web_link?: string; saved_at?: string; as_reply?: boolean; mailbox?: string; why_not?: string; to?: string[]; cc?: string[] };
  original?: { sha256: string; subject?: string; date?: string; from?: string; body?: string; attachments?: string[] };
};

export type MoneySource = { file: string; kind?: string; as_of?: string; rows: { item: string; value: string; note?: string }[] };

export type KeyNumber = { key: string; label: string; value?: string | null; headline?: string; detail?: string; note?: string; source?: string | null; as_of?: string | null; from: "workbook" | "file" | "missing" };
export type PersonOnDeal = { role: string; value: string; source: string; contacts: { name: string; emails: string[]; org?: string }[] };
export type Stage = {
  stage: string; index: number; recovery: boolean; recovery_note?: string; closing_date?: string | null;
  construction_percent?: number | null; construction_expected?: string | null; construction_basis?: string;
  sale_expected?: string | null; sale_basis?: string; one_line?: string; read_at?: string; site_now?: string; last_log?: string | null;
  /* On the market: the listing date a record gave (never worked out), and the days since. */
  group?: "construction" | "market" | "nonperforming" | "sold"; on_market?: boolean; listed_date?: string | null; listed_basis?: string; days_on_market?: number | null;
  steps: { key: string; label: string; state: "done" | "current" | "ahead" }[];
};

export type Bill = {
  bill_id: number; number?: string; title?: string; pay_to?: string; amount?: number | null; balance?: number | null; status?: string;
  invoice_date?: string | null; due_date?: string | null; past_due?: boolean; approvers?: string[]; created_by?: string; documents?: number;
  line_items?: { title: string; description?: string; cost_code?: string; amount?: number | null; variance?: string | null }[]; file_names?: string[];
};
export type SiteLog = { log_id: number; date?: string; title?: string; notes?: string; by?: string; photo_count: number; photos: number[] };
export type Site = {
  in_buildertrend: boolean; job_name?: string; owner_name?: string; logs_total?: number; first_log?: string | null; last_log?: string | null; pulled_at?: string | null;
  recent?: SiteLog[];
  bills?: { totals: { count: number; billed: number; paid: number; by_status: Record<string, { count: number; amount: number }> }; in_review: Bill[]; in_review_amount: number; ready: Bill[]; held_back?: { count: number; amount: number } };
};

export type ArvHead = {
  analysis_id: string; value?: number | null; low?: number | null; high?: number | null; basis?: string; engine?: number | null; analyst?: number | null;
  confidence?: { score?: number | null; grade?: string | null; label?: string | null }; comps?: number | null; rehab_total?: number | null; rehab_source?: string | null;
  by?: string | null; from_contractor?: boolean; org?: string | null; at?: string | null; analyses?: number;
};

export type ReadingHead = {
  at?: string; overall?: string; one_line?: string; headline?: string; phase?: string; workmanship?: string; pace?: string; documentation?: string;
  photos_shown?: number; photos_posted?: number; flagged?: number; best_photos?: { photo_id: number; caption: string }[];
  stats?: { logs: number; days_with_a_log: number; longest_gap_days: number; days_since_last_log: number | null; first_log?: string | null; last_log?: string | null; period_days: number };
};

export type PropertyFile = {
  property_id: string; name: string; address: string; is_general: boolean; exists: boolean;
  headline?: string; status?: string; facts?: { key: string; value: string }[]; watch?: string[];
  open_items?: OpenItem[]; closed_items?: OpenItem[]; activity?: Activity[]; drafts?: Draft[]; money?: MoneySource[];
  updated_at?: string; built_at?: string; last_event_at?: string | null;
  numbers?: { main: KeyNumber[]; extra: Record<string, { label: string; value: string; note?: string; source?: string }>; in_workbooks: boolean };
  people?: PersonOnDeal[]; stage?: Stage | null; site?: Site; arv?: ArvHead | null;
  sitework?: ReadingHead | null; latest_photos?: { photo_id: number; date?: string; title?: string }[];
};

export type PropertyLine = {
  property_id: string; name: string; headline: string; updated_at?: string; last_event_at?: string | null; open: number;
  open_mine: Record<string, number>; waiting: number; drafts: number; watch: number; is_general: boolean;
};

export type Home = {
  me: string; properties: PropertyLine[]; missing: { property_id: string; name: string }[]; drafts: Draft[];
  todos: OpenItem[]; waiting: OpenItem[]; activity: Activity[]; pending_records: number;
  stats: { drafts_sent: number; sent_as_written: number; avg_kept: number | null };
  settings: { enabled: boolean; outlook_drafts: boolean };
};

export type Source = { index: number; chunk_id: string; artifact_sha: string; citation: string; display_name?: string; date?: string; placement?: string };

export type ChatFile = { file_id: string; name: string; kind: string; size?: number; readable?: boolean };

export type ChatMessage = {
  message_id: string; thread_id: string; seq: number; role: "user" | "assistant"; text: string; at: string;
  by?: string; by_name?: string; files?: ChatFile[];
  sources?: Source[]; tool_log?: { name: string; label: string; error?: boolean; ms?: number }[]; drafts?: Draft[];
  actions?: { type: string; title?: string; property_id?: string }[]; cost_usd?: number; elapsed_ms?: number; rounds?: number;
  checks?: { amounts: number; not_found: string[] }; refused?: boolean; verification?: Verification; records_as_of?: string;
};

export type ChatThread = {
  thread_id: string; property_id?: string | null; property?: string; title?: string; created_at?: string; updated_at?: string;
  n_messages?: number; messages?: ChatMessage[]; active?: { job_id: string; text?: string }[];
};

export const OWNER_LABEL: Record<string, string> = {
  rakesh: "Rakesh Sir", jp: "JP Sir", manjunath: "Manjunath Sir", wes: "Wes", kelly: "Kelly", other: "Other",
};

export const KIND_LABEL: Record<string, string> = {
  email_in: "Email in", email_out: "Email sent", call: "Call", document: "Document", said_in_chat: "Said in chat",
  todo_closed: "To-do closed", note: "Note",
};

/** A readable message from whatever a failed request threw. */
export function errText(e: unknown, fallback = "Something went wrong"): string {
  return e instanceof Error && e.message ? e.message : fallback;
}
