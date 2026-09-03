# MangoTree — System Architecture

> The architecture principle that governs everything:
> **Automatic analysis before any command.** Every artifact that arrives is fully processed, analyzed, and surfaced *before* any human asks about it. Humans review; the system works.

---

## 1. High-Level Component Map

```
┌─────────────────────────────  SOURCES  ─────────────────────────────┐
│ Outlook (MS Graph)   Gmail (API)   WhatsApp/Dropbox photos          │
│ Granola/Zoom calls   Guidelines & SOP docs (manual upload)          │
└──────────────┬──────────────────────────────────────────────────────┘
               ▼
┌────────────────────────  INGESTION LAYER  ──────────────────────────┐
│ Connectors (webhooks + subscriptions + nightly reconciliation)      │
│ → Raw store (immutable originals, SHA-256 addressed)                │
│ → Cleaning (mojibake, quotes, signatures, whitespace)               │
│ → 3-way dedup (provider ID / internet-message-ID / content SHA)     │
│ → Property resolution (deterministic → AI → review queue)           │
│ → 17-class classification                                           │
└──────────────┬──────────────────────────────────────────────────────┘
               ▼
┌────────────────────────  PROCESSING LAYER  ─────────────────────────┐
│ Force-vision OCR cascade   Chunking (1000/200, paragraph-first)     │
│ 3-tier context writer      Embeddings (1024-d) + hypothetical Qs    │
│ Extraction contracts (money classes) → verification queue          │
│ Entity resolution → knowledge graph                                 │
│ Photo cataloging / forensics                                        │
└──────────────┬──────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────  STORAGE CORE  ───────────────────────────┐
│ Postgres (system of record):                                        │
│   artifacts, chunks, occurrences[], deal ledger, bitemporal events, │
│   knowledge graph, tasks, findings, commitments, memories,          │
│   corrections, policy rulebook (versioned), audit log               │
│ Object store: originals, attachments, photos, exports               │
│ Indexes: pgvector (chunks + question vectors), weighted BM25,       │
│   full b-tree set incl. occurrence fan-out                          │
└──────────────┬──────────────────────────────────────────────────────┘
               ▼
┌────────────────────  AUTO-ANALYSIS PIPELINE  ───────────────────────┐
│ Scheduler: 3 lanes (heavy / light / realtime), job contracts,       │
│ dead-letter alerts. Per-property sequencing, properties parallel.   │
│ Chain per artifact: extract → ledger update → detectors →           │
│ policy check → timeline event → tasks → change cards → live push    │
└──────────────┬──────────────────────────────────────────────────────┘
               ▼
┌──────────────────  RETRIEVAL + AGENT (v2/v3)  ──────────────────────┐
│ 6 hybrid channels → RRF → query understanding → HyDE/multi-query    │
│ → decomposition → rescoring → diversification → adaptive-K →        │
│ rerank → expansion → interleave → token cap                         │
│ Agent: SEED → PLAN/ACT/OBSERVE → sufficiency gate → VERIFY →        │
│ structured memo with verbatim quotes + coverage statement           │
│ Enumeration router for exhaustive questions (ledger-backed, with    │
│ denominators). check_policy tool. Role-based filters everywhere.    │
└──────────────┬──────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────  SURFACES  ───────────────────────────────┐
│ Per-property workspace (timeline, chat, docs, money, photos, tasks) │
│ Dashboard (Needs Attention vs Handled)   Morning briefs 6 a.m.      │
│ Task engine + My To-Do   Reports library   Admin (memory, queues)   │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. Core Design Decisions

### 2.1 Storage: right store for each shape of data *(updated 2026-08-12 per admin direction)*

| Store | Holds | Why |
|---|---|---|
| **S3 (object store)** | Every original file **as-is, byte-for-byte** — emails (raw MIME), attachments, photos, exports — addressed by SHA-256, never mutated | Original evidence must be reproducible forever |
| **MongoDB** | The document layer: artifacts, cleaned bodies, OCR output (per-page, confidence-stamped), chunks with full v2 schema, summaries, threads, timeline events, chat histories, Remember notes | Flexible document shapes; the natural home for email/OCR/chunk JSON; fast per-property reads |
| **Vector DB** (Qdrant, or MongoDB Atlas Vector Search if we run Atlas — decision gate in Sprint 0) | 1024-d chunk embeddings + question vectors, with metadata filter paths (`property_ids`, `doc_class`, dates) | Filtered ANN at scale; one embedding space |
| **Postgres** | **Money and state machines only**: the deal ledger (budget lines, invoices, draws, loans, outcomes), tasks, policy rulebook versions, job runs, audit log | Consultant's firm advice: sums that must reconcile, foreign keys that must hold, and audits that must be provable belong in SQL. A draw audit built on numbers without constraints is a trust risk we don't take |

Rule: **every record in any store carries the S3 SHA-256 of its original** — any claim traces to the untouched source file in two hops. BM25/keyword search via MongoDB Atlas Search or a Typesense sidecar (same Sprint 0 decision gate).

### 2.2 Immutability + provenance
- Originals are never mutated. Cleaning/OCR produce derived records pointing at the original.
- Every extracted number, every timeline event, every finding carries `source_artifact_id + chunk/page/line span`. **Nothing exists in the system without a click-through to its evidence.**
- Audit events are append-only.

### 2.3 Bitemporal events
Every event stores `occurred_at` (when it happened in the world) and `recorded_at` (when we learned it). This powers the as-of slider ("what did we know on July 3rd") and "what was our rule at the time" against the versioned rulebook.

### 2.4 Multi-property fan-out (`occurrences[]`)
One email can concern multiple properties. The artifact is stored **once**; an `occurrences[]` array fans it out to every property (and mailbox/recipient) it belongs to, with earliest-primary mirror fields for sorting. Every property's workspace shows the artifact; **analysis for a property uses only the segments/lines resolved to that property** (segment-level property tags on chunks, not just artifact-level). Cross-property leakage is structurally impossible because retrieval filters on `property_ids[]` at the chunk level.

### 2.5 Deterministic before AI, always
Every pipeline stage tries deterministic logic first (address regexes, known-sender maps, exact-match entity resolution, machine-checkable rules), falls back to AI, and falls back from AI to a **human review queue**. Money never enters the ledger unverified below the confidence bar. Every AI stage has a deterministic final fallback and a visible degrade flag — **zero silent degradations**.

### 2.6 Event-driven with a nightly safety net
Realtime paths (Graph subscriptions, Gmail push, Granola webhooks, detector-per-artifact) give speed; **nightly reconciliation sweeps and full-pass detectors guarantee correctness**. A missed notification can never mean a missed email; a missed trigger can never mean a missed finding.

### 2.7 Prompt-injection firewall
Document content is **evidence, never instructions**. All ingested text (emails, PDFs, photo captions, transcripts) is wrapped in delimited evidence blocks; the agent's system prompts treat evidence as untrusted data. Red-teamed in Sprint 12.

### 2.8 100% automated, 100% overridable
Every automated behavior has a manual twin, and the manual twin always wins:

| Automated | Manual override |
|---|---|
| Property resolution, classification, extraction | Review queues; one-click reassign/correct; corrections learned |
| Timeline events, decisions, deadlines | Admin **checkboxes** — mark done/verified; add manual events; edits audited |
| Tasks auto-created from emails/calls | Manual task creation and **assignment to any team member** from anywhere |
| Change cards, findings | Confirm / dismiss-with-remark (feeds correction memory) |
| Scheduled jobs | Manual trigger on every job |
| AI analysis behavior | **Admin instruction channel**: Remember notes + corrections, injected into every matching call — "the admin's manual instruction when the AI is not doing it correctly" |
| Drafts | Human always sends (until autonomy flag is deliberately flipped) |

Nothing the system does is final until a human could have seen it; nothing a human does is forgotten by the system (every manual action is an audited event that feeds learning).

### 2.9 Role & access firewall
Read/send restricted by access policy to designated mailboxes only. Every tool honors role-based filters. Outbound send is whitelist-gated, autonomous send is built but **flagged off** until explicitly enabled per the autonomy policy. All outbound logged; sent items re-ingested.

## 3. Data Model — Core Tables (summary)

| Table | Purpose |
|---|---|
| `artifacts` | One row per unique ingested item (email, attachment, photo, call, guideline doc). SHA-256, source_type, provider IDs, internet-message-ID, doc_class, confidence stamps |
| `occurrences` | Fan-out: artifact × (mailbox, folder, property) — provenance of who received what, where it applies |
| `chunks` | Full v2 schema: positions, embedded text (Tier-1 + Tier-2 + header + chunk), raw body, token counts, 1024-d embedding, page spans, property_ids[], contractor_ids[], doc_class, tier2_version, latest_date |
| `chunk_questions` | Hypothetical questions per chunk (sixth retrieval channel) |
| `properties`, `deals` | Deal ledger root; status, phases, day counts |
| `budget_lines`, `invoices`, `invoice_lines`, `draws`, `loans`, `holding_costs`, `outcomes` | Money — every row source-linked |
| `entities`, `aliases`, `entity_edges`, `chunk_entities` | Knowledge graph |
| `events` | Bitemporal property timeline events, typed, evidence-linked |
| `policy_rules` | Versioned rulebook: statement, citation, machine-checkable condition, scope, status |
| `findings` | Detector + policy-deviation output; deterministic IDs; confirm/reject survives re-runs |
| `tasks`, `task_audit` | Task lifecycle, append-only evidence-linked audit |
| `commitments` | Who promised what by when, from emails and calls |
| `memories` | Remember notes: scope (global/property/contractor), author, date, status |
| `corrections` | Correction memory with Rakesh-instant / analyst-pending hierarchy |
| `saved_answers`, `reports` | Answer library, reports library with versions |
| `jobs`, `job_runs` | Scheduler contracts, SLAs, cost tracking, dead letters |

## 4. The Auto-Analysis Pipeline (the spine)

### 4.0 The Email Reflex — the first automation we ship

The very first end-to-end loop, live before anything else matures (thin slice in Sprint 1, full engine in Sprint 5):

```
New email lands (Gmail or Outlook)
  → read + ingested automatically (raw MIME to S3, document to MongoDB)
  → thread stitched (cross-provider, start-to-end)
  → property resolved → timeline event created
  → criticality assessed:
      CRITICAL / needs reply → to-do item created + cited reply draft
                               placed in the user's Drafts + notification
      informational          → timeline + to-do list updated quietly
  → visible in the property workspace, live, no refresh
```

No human triggers any step. The email is read, filed, threaded, on the timeline, and — if it matters — answered in draft before anyone opens their inbox.

Per new artifact, orchestrated as one resumable flow, **per-property sequenced / cross-property parallel**:

1. Ingest → clean → dedup → **property-resolve** → classify
2. OCR (if needed) → chunk → context → embed → index  *(email indexed < 60s)*
3. Extraction contract (if money-bearing class) → verification queue if below confidence
4. Ledger + knowledge graph + bitemporal events update
5. Event-driven detectors + policy-deviation check for affected properties
6. Timeline event(s) created; change-detection delta vs property's last state
7. Tasks / commitments / review-routing updates (JP Sir state machine)
8. Change cards pushed live to open pages; severe → notify
9. Everything stamped, logged, idempotent, resumable

Every stage idempotent (keyed by artifact SHA + stage), every stage resumable, every failure dead-lettered and alerted.

## 5. Technology Stack (recommended)

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python (FastAPI) | Best AI/SDK ecosystem; typed with Pydantic contracts |
| Workers/scheduler | Celery or Temporal | Temporal preferred: durable, resumable, per-key sequencing built-in — matches the pipeline contract exactly |
| Document store | MongoDB (Atlas preferred) | Artifacts, OCR, chunks, threads, timelines, chats, memories |
| Money & state | Postgres 16 | Deal ledger, tasks, rulebook versions, job runs, audit log |
| Vector index | Qdrant or Atlas Vector Search | 1024-d, filtered ANN — decision gate Sprint 0 |
| Object store | S3 | Originals as-is, SHA-256 addressed, immutable |
| Keyword search | Atlas Search or Typesense | Weighted BM25 — same Sprint 0 decision gate |
| Frontend | Next.js + WebSocket/SSE live push | Live cards, no-refresh timeline |
| Email | Microsoft Graph + Gmail API | Subscriptions/watch + nightly sweeps |
| AI models | See `01-AI-MODEL-STACK.md` | 5 models, each with a role |
| Observability | Structured logs + job dashboard + red-banner scheduler health | Zero silent degradations |

## 6. Environments & Safety

- **dev / staging / prod** with separate mailbox app registrations; staging reads a copy, never sends.
- Outbound whitelist enforced at the send service, not in the agent.
- Secrets in a vault; rotation drill in Sprint 12.
- Nightly backups; restore verified by re-running the golden question set against the restored copy (Sprint 12).
