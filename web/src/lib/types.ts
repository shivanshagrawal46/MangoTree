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
/* An email the AI wrote for a next step, on the team's behalf. */
export type EmailDraft = { to: string; to_email?: string | null; from: string; subject: string; body: string; for_action?: string };
/* A flow chart the writer produced when a process is the answer (Mermaid code, rendered client-side). */
export type Diagram = { kind: "mermaid"; title?: string; code: string };
export type Source = { index: number; chunk_id: string; artifact_sha: string; citation: string; display_name: string;
  property_ids: string[]; placement: string; label: string; date: string; text: string; context: string; origin: string };

export type Answer = {
  question: string; scope: string; headline: string; summary?: string; points: AnswerPoint[]; details: string;
  shape?: "brief" | "actions" | "draft" | "list" | "figure" | "explain" | "followup" | "process"; composed?: string | null; diagram?: Diagram | null;
  mode?: "full" | "fast";
  disagreements: string[]; next_actions: NextAction[]; emails?: EmailDraft[]; second_opinion: string;
  second_reader: { provider?: string; model?: string; answer?: string; missed?: string[]; wrong?: string[]; disagree?: string[]; error?: string };
  risks: string[]; verification: { facts?: number; verified?: number; rate?: number; unverified?: any[] };
  verdict: { verdict: string; confidence: number; notes: string[]; dissent: string[]; revised?: boolean };
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
  draft_email?: EmailDraft | null;
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

/* ---------------------------------------------------- next steps / desks */
export type NextPerson = "wes" | "manjunath" | "jp" | "rakesh";
export type NextStep = {
  title: string; detail: string; why_critical: string; due?: string | null; urgency: "critical" | "high";
  evidence: { source_sha: string; quote: string }[]; verified: boolean; done?: boolean; done_by?: string | null; done_at?: string | null;
  carried_from?: string | null; carried_days?: number; first_seen?: string | null;
  property_id?: string; address?: string; index?: number; run_id?: string;
};
export type NextStepsProperty = { address: string; headline: string; error?: string; elapsed_s?: number } & Partial<Record<NextPerson, NextStep[]>>;
export type NextStepsRun = {
  run_id: string; day: string; started_at: string; finished_at?: string; by: string; status: "running" | "complete" | "failed";
  order: string[]; properties: Record<string, NextStepsProperty>; progress?: { done: number; total: number };
  counts?: Record<NextPerson, number>; errors?: string[]; elapsed_s?: number; subtitle?: string;
  sent?: { at: string; by: string; outbox: Record<string, { outbox_id: string; status: string }> };
};
export type SendStatus = { mailbox: string | null; can_send: boolean; signed_in: boolean; how_to_enable?: string; error?: string };
export type OutboxItem = {
  outbox_id: string; kind: string; ref: string; to: { name: string; address: string }[]; subject: string; text?: string;
  status: "queued" | "sent" | "needs_consent" | "failed" | "replied" | "superseded"; attempts: number; queued_at: string; sent_at?: string | null;
  error?: string | null; replied_at?: string | null; replied_by?: string | null; reply_preview?: string | null;
  meta?: Record<string, any>; attachments?: { filename: string; content_type: string; size: number }[];
};
export type NextStepsLatest = { run: NextStepsRun | null; sent?: OutboxItem[]; send_status?: SendStatus | null; running?: boolean; person?: NextPerson; auto_send?: boolean;
  in_progress?: { run_id: string; started_at: string; by: string; progress?: { done: number; total: number } } | null };
export type SheetSection = { property_id: string; address: string; headline: string; steps: NextStep[]; others: Partial<Record<NextPerson, NextStep[]>> };
export type Sheet = { run_id: string; day: string; person: NextPerson; subtitle: string; sections: SheetSection[]; status: string };

export type Followup = {
  followup_id: string; kind: "ask_internal" | "ask_external" | "report_ack"; property_ids: string[]; thread_key?: string | null; subject?: string;
  owner: "rakesh" | "jp" | "manjunath"; counterparty: { name: string; email: string; person_id?: string | null };
  what: string; topic: string; source_sha?: string | null; asked_at: string; created_at: string; due: string;
  status: "open" | "replied" | "done" | "dismissed" | "escalated"; reminders: { at: string; mode: string; to?: string; outbox_id?: string }[];
  last_reminder_at?: string | null; escalated_at?: string | null; closed_at?: string | null; closed_by?: string | null; closed_reason?: string | null;
  draft?: { subject: string; body: string; to?: string; at: string } | null; updates?: { at: string; sha: string; what: string }[];
};
export type FollowupsResponse = { items: Followup[]; counts: Record<string, Record<string, number>>; last_tick?: string | null; since: string;
  rules: { external_due_business_days: number; internal_due_business_days: number; escalate_after_business_days: number } };
export type Desk = {
  user: { user_id: string; name: string; role: string; full_name?: string }; person: NextPerson | null;
  run: { run_id: string; day: string; status: string; finished_at?: string } | null; subtitle?: string | null;
  steps: NextStep[]; followups: Followup[]; tasks: Task[];
  sheet_mail?: { status: string; sent_at?: string; replied_at?: string; subject?: string } | null;
  team?: Record<string, { steps: number; followups: number }> | null;
};

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
