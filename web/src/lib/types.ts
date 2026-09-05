export type User = { user_id: string; name: string; full_name?: string; role: string; home: string };

export type PropertySummary = {
  property_id: string; address: string; city?: string; state?: string; deal_type?: string; status: string;
  documents: { total: number; email?: number; attachment?: number; disk_file?: number; upload?: number };
  first_activity?: string; last_activity?: string; day_count?: number; started?: string; events: number;
  health: { level: "critical" | "watch" | "good"; reasons: string[]; derived_from: string };
  money: LedgerMoney;
  upcoming: { date: string; type: string; title: string; source_sha?: string }[];
  risk_events: { date: string; type: string; title: string; source_sha?: string }[];
  tasks: { open: number; suggested: number; done: number };
  wes: { total: number; done: number; remaining: number };
};

export type TimelineEvent = {
  event_id: string; property_id: string; occurred_at?: string; event_type: string; title: string; detail?: string;
  amount?: number | null; source_sha?: string; source_ref?: string; source_name?: string; quote?: string;
  confidence?: number; extracted_by?: string; date_basis?: string;
};

export type ArtifactRow = {
  sha256: string; name: string; subject?: string; filename?: string; source_type: string; doc_class?: string;
  date?: string; property_ids: string[]; placement?: string; topics: string[]; from?: string; to?: string[];
  attachments: number; attachment_names: string[]; thread_key?: string; size?: number; extension?: string;
  confidence?: number; reasoning?: string; resolution_status?: string; deal_address?: string;
  body?: string; body_excerpt?: string; candidates?: string[]; thread_size?: number;
  attachments_list?: ArtifactRow[]; timeline_event?: TimelineEvent | null;
};

export type AnswerPoint = { text: string; urgency: "critical" | "high" | "normal" | "info" | "good"; sources: number[] };
export type NextAction = { title: string; owner: string; due?: string | null; why?: string; sources: number[] };
export type Source = { index: number; chunk_id: string; artifact_sha: string; citation: string; display_name: string;
  property_ids: string[]; placement: string; label: string; date: string; text: string; context: string; origin: string };

export type Answer = {
  question: string; scope: string; headline: string; points: AnswerPoint[]; details: string;
  shape?: "brief" | "actions" | "draft" | "list" | "figure" | "explain" | "followup"; composed?: string | null;
  disagreements: string[]; next_actions: NextAction[]; second_opinion: string;
  second_reader: { provider?: string; model?: string; answer?: string; missed?: string[]; wrong?: string[]; disagree?: string[]; error?: string };
  risks: string[]; verification: { facts?: number; verified?: number; rate?: number; unverified?: any[] };
  verdict: { verdict: string; confidence: number; notes: string[]; dissent: string[] };
  coverage: string; draft: string; sources: Source[]; steps: any[]; budget: any; outcome: string;
  degrades: string[]; elapsed_ms: number; models: Record<string, string>; suggested_task_ids?: string[];
};

export type ChatMessage =
  | { role: "user"; content: string; by: string; at: string }
  | { role: "assistant"; job_id: string; at: string; answer: Answer };

export type Task = {
  task_id: string; title: string; owner: string; property_id?: string | null; status: "suggested" | "open" | "done" | "dismissed";
  priority: "critical" | "high" | "normal" | "low"; source: string; due?: string | null; why?: string;
  evidence?: { quote: string; source_sha?: string }[]; created_by: string; created_at: string; done_at?: string | null; done_by?: string | null;
};

export type WesItem = { title: string; status: "done" | "in_progress" | "remaining" | "blocked"; due?: string | null; quote: string; source_sha?: string };

export type Dashboard = {
  user: User;
  needs_attention: {
    unplaced: { count: number; oldest?: string }; low_confidence: number; overdue_tasks: Task[]; suggested_tasks: number; my_open_tasks: number;
    deadlines: TimelineEvent[]; risk_events: TimelineEvent[]; answers_with_unverified: any[];
  };
  handled: { label: string; count: number; kind: string }[];
  portfolio: PropertySummary[];
  tasks: { by_owner: Record<string, Record<string, number>>; by_status: Record<string, number>; by_property: Record<string, Record<string, number>> };
  money: { series: any[]; by_type: Record<string, number>; cost: number; returned: number; net: number };
  degrades: string[];
  intake?: Intake;
};

/* Money from the ledger. null = the documents do not establish it; render as such, never as 0. */
export type LedgerMoney = {
  established: boolean;
  invested: number | null; returned: number | null; billed: number | null;
  owed: number | null; owed_as_of?: string | null; owed_source_sha?: string | null;
  derived_today?: { amount: number; days: number; formula: string; label: string } | null;
  risks: number; gaps: number; discrepancies: number; critical_risks?: string[];
  built_at?: string | null; entries?: number; derived_from: string;
};

export type LedgerEntry = {
  kind: string; direction: "out" | "in" | "billed"; amount: number; date?: string | null;
  counterparty: string; description: string; source_sha: string; quote: string;
  confidence: "confirmed" | "stated" | "mentioned"; also_in?: string[];
};

export type LedgerSummary = {
  property_id: string; built_at: string; model: string; established: boolean;
  invested: number | null; returned: number | null; billed: number | null;
  owed: { as_of?: string; owed_total: number; principal?: number | null; interest_accrued?: number | null; fees?: number | null; per_diem?: number | null; source_sha: string; quote: string; label?: string } | null;
  derived_today?: { amount: number; days: number; formula: string; label: string } | null;
  balances?: any[]; entries: number;
  discrepancies: { topic: string; values: { amount: number; source_sha: string; quote: string }[]; note: string }[];
  gaps: { missing: string; would_settle: string }[];
  risks: { title: string; source_sha: string; quote: string; severity: "critical" | "high" | "watch" }[];
  sources: { sha256: string; filename?: string; date?: string; role: "authoritative" | "context" }[];
  notes: string;
};

export type LedgerPortfolio = {
  properties: number; established: number;
  invested: number | null; returned: number | null; billed: number | null; owed: number | null; owed_properties: number;
  risks: { title: string; severity: "critical" | "high" | "watch"; property_id: string; source_sha: string; quote: string }[];
  per_property: any[];
};

export type WesIssue = {
  title: string; why_now: string; ask: string; urgency: "critical" | "high" | "normal";
  carried_from?: string | null; evidence: { source_sha: string; quote: string }[];
  discussed: boolean; outcome?: string | null;
  resolved?: boolean; resolved_at?: string;
  resolution?: { verdict: string; document?: string; date?: string; quote?: string; source_sha?: string; statement?: string; by?: string; by_name?: string; note?: string };
  reported_done?: { by?: string; by_name?: string; at?: string; statement?: string };
  checked_at?: string;
};
export type WesAgendaDoc = { property_id: string; day: string | null; generated_at?: string; issues: WesIssue[]; quiet?: boolean; note?: string };

export type Intake = {
  error?: string;
  poll_minutes: number;
  gmail_last_ok?: string; outlook_last_ok?: string;
  today: { runs: number; seen: number; ingested: number; errors: number };
  last_run?: { started_at?: string; finished_at?: string; kind?: string; seen?: number; fetched?: number; ingested?: number; errors?: number;
    skipped?: Record<string, number>; source_errors?: Record<string, string>; new_emails?: number; per_source?: Record<string, { seen: number; fetched: number; ingested: number }> } | null;
  last_arrival?: { started_at?: string; finished_at?: string; elapsed_s?: number; emails?: number; properties?: string[]; errors?: string[] } | null;
  pending_debounce?: number;
};
