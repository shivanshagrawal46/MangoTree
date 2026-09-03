# MangoTree — Master Sprint Tracker

> **Rules**: A sprint is DONE only when every gate criterion in its file is ticked with evidence. Do not start sprint N+1 while sprint N's gate is red. New scope goes in the sprint file's "Added Features" section with a date — it never silently inflates a gate.

**Legend**: ⬜ not started · 🟨 in progress · 🟩 done (gate passed) · 🟥 blocked

| # | Sprint | Status | Gate (one line) | Gate passed |
|---|--------|--------|-----------------|-------------|
| 0 | [Infrastructure & Credentials](sprint-00.md) | ⬜ | Envs live, mailbox access proven, Alicia backfill secured, cron core running | [ ] |
| 1 | [Ingestion: Email, Photos, Guidelines](sprint-01.md) | 🟨 | Backfill complete · indexed <60s · dedup shown · resolution ≥90% · photo linked <5min · guidelines confirmed | [ ] |
| 2 | [OCR, Context, Chunk Schema, Indexing](sprint-02.md) | 🟨 | 100% OCR'd · classification ≥95% · recall@20 ≥95% (no class <90%) · Tier-2 incremental · photo timeline | [ ] |
| 3 | [Deal Ledger, Knowledge Graph, Rulebook](sprint-03.md) | ⬜ | 10 properties reconciled · money ≥97% · entities ≥95% · rulebook cited & confirmed | [ ] |
| 4 | [Scheduler & Auto-Analysis Pipeline](sprint-04.md) | ⬜ | All jobs green 5 days · kill-a-worker resumes · one email drives full chain end-to-end | [ ] |
| 5 | [Task Engine, JP Sir Routing, Digests](sprint-05.md) | ⬜ | Task precision ≥85% · JP state machine ≥95% · digests on time ×3 users · zero autonomous sends | [ ] |
| 6 | [Retrieval (v3 tools), Agent v3, Memory, Saved Answers](sprint-06.md) | ⬜ | Exhaustive 100% · verification ≥97% · recall@20 ≥98% · planted correction+Remember shape answers · coverage 100% · **no v2 imports** | [ ] |
| 7 | [Granola & Call Intelligence](sprint-07.md) | ⬜ | Segmentation ≥90% · commitments aging · citations to transcript lines · live call fully auto | [ ] |
| 8 | [Per-Property Workspace](sprint-08.md) | ⬜ | Email→card+event+task <90s no-refresh · leak-free scoped chat · as-of verified · dismissal <30% live week | [ ] |
| 9 | [Dashboard, Briefing, Reports](sprint-09.md) | ⬜ | 10 on-time briefs · inbox-replacement week audited · next-action <10s ×3 users · scheduled report on time | [ ] |
| 10 | [Detectors & Policy-Deviation Engine](sprint-10.md) | ⬜ | 8/8 planted anomalies, 0 false-criticals · 3 policy violations cited · artifact→finding untouched | [ ] |
| 11 | [Photo Forensics & Draw Audits](sprint-11.md) | ⬜ | Vision ≥90% on labeled set · planted contradiction caught · live draw auto-audited · Rakesh approves from packet | [ ] |
| 12 | [Forecasting, Contractor Forensics, Hardening](sprint-12.md) | ⬜ | Launch scorecard green 2 consecutive weeks (full list in file) | [ ] |

## Cross-Sprint Standing Commitments

- [ ] **Rakesh Sir sessions booked in calendar**: guidelines corpus (S1) · rulebook half-day (S3) · 10-property reconciliation (S3) · workspace live week (S8) · inbox-replacement audit (S9) · draw approval (S11) · Friday golden-question grading (S12→forever)
- [ ] Labeled sets built one sprint ahead of the gate that needs them (classification, calls, tasks, photos)
- [ ] Every AI output version-stamped (`model@version + prompt_version`) from day one
- [ ] Zero silent degradations: every fallback path emits a visible flag, from Sprint 1 onward
- [ ] Weekly ritual from Sprint 12: 20 rotating golden questions graded Fridays, scorecard Mondays

## Decision Log

| Date | Decision | Why |
|---|---|---|
| 2026-08-12 | Added Sprint 0; minimal cron core before Sprint 4's full scheduler | Sprints 1–3 need scheduled renewals/sweeps |
| 2026-08-12 | Dropbox is the primary photo channel; WhatsApp fast-follow | WhatsApp Business API group support is weak; Dropbox meets the <5 min gate with far less risk |
| 2026-08-12 | Baseline retrieval built in Sprint 2 (full v2 in Sprint 6) | Recall harness can't measure recall without a retriever |
| 2026-08-12 | Alicia mailbox backfill is a day-one Sprint 0 action | Departed-employee mailbox is the most perishable data asset |
| 2026-08-12 | Hallucination-proof Remember guarantees (verbatim storage/injection, deterministic scope match, verifier echo-check) → S6 | Admin notes must be law, never a suggestion (`docs/03` §3.1) |
| 2026-08-12 | AI Expert Panel: cross-provider producer/critic/skeptic/verifier for all high-stakes outputs → built S6, consumed S10–12 | "Many experts in every analysis" made mechanical (`docs/01`) |
| 2026-08-12 | "100% automated, 100% overridable" adopted as architecture principle 2.8 | Every automated behavior gets a manual twin; manual wins |
| 2026-08-12 | Added: Comms Tracker UI + task assignment (S5), decision/deadline admin checkboxes (S5/S8/S9), per-property "what's left" + deadlines board + Excel financial export (S8/S9) | Admin request |
| 2026-08-12 | Storage revised to hybrid: S3 (originals as-is) + MongoDB (document layer) + vector DB + **Postgres kept for money/ledger/audit only** | Admin directed S3+MongoDB+vector; consultant holds SQL for sums that must reconcile |
| 2026-08-12 | Claude vision promoted to primary in the OCR cascade (Gemini as cascade partner) | Admin direction |
| 2026-08-12 | **Email Reflex thin slice pulled into Sprint 1** (critical email → to-do + cited draft; informational → quiet timeline/to-do update) + cross-provider thread reconstruction | Admin: "automate this first" |
| 2026-08-12 | One persistent chat per property with rolling contextual summary (replaces per-session model); UI/UX principles doc added (`docs/05`) | Admin direction |
| 2026-08-12 | Frontier seats locked after research: **Fable 5 = manager/panel chair · Opus 5 = deep producer · GPT-5.6 = high-recall critic + answer writer** | Benchmarks: Fable tops Hebbia Finance + long-horizon; Opus highest precision + long-chain stability; GPT-5.6 highest recall + best structured writing |
| 2026-08-12 | Support tiers admin-final: **OCR = Sonnet 4.6** (doc extraction beats Sonnet 5, which regressed on docs) · **summaries/workhorse = Sonnet 5** · **embeddings = voyage-4-large ONLY (no OpenAI embeddings — spaces can't mix)** · **rerank = Voyage stage-1 + Opus 5 stage-2** · **Gemini and Haiku removed** | Admin direction + research; cost step-up accepted, mitigated by caching/batch/budget guards; OCR pre-flight cost estimate now mandatory |
| 2026-08-25 | Rakesh Sir's mailboxes added (`Rakesh@mtreh.com` Outlook + `rakesh.bhargava@gmail.com` Gmail); **Identity & Alias Registry** (SOP §1b), per-occurrence sent/received direction, send-as alias capture, personal-Gmail business filter → S0/S1; **fact-level single-property assignment** in extraction → S3 | Rakesh sends via Gmail's `Rakesh@mtreh.com` send-as dropdown — those sent mails exist only in Gmail's Sent folder; multi-property emails need per-fact assignment, never guess-splits |
| 2026-08-30 | **Business model corrected: RKB is a LENDER earning interest, not an equity investor** (`docs/06` §1) | Earlier docs said "we are the investors"; every money model, profit band and risk detector re-framed around loan exposure, draws, payoffs and recovery |
| 2026-08-30 | **`docs/06-BUSINESS-CONTEXT-MEMORY.md` created as ground truth** — people, property registry with address aliases, email rules, data inventory; overrides any conflicting doc | The system must never forget who everyone is or which property is which |
| 2026-08-30 | **Manjunath Sir's civil-verification + bill-approval workflow added → S5** | Role was entirely missing from the 12 sprints; bills need civil verification *before* accounting approval |
| 2026-08-30 | Property registry carries **canonical address + folder alias + doc aliases**; **904 and 910 Bayshore are distinct and must never merge** | Disk folders omit street numbers ("9th St NW" is really 3731 9th St); two loans on one street is the top contamination hazard |
| 2026-08-30 | 🔒 **RAG v3 ONLY — no v2 pipeline.** v2 capabilities (hybrid search, verification, coverage, entailment, contextual summary) rebuilt as **v3 agent tools** → S6 | Admin directive. In v3 the agent decides what it needs and re-queries; a fixed v2 pre-pipeline can't deliver the exhaustive-completeness gate |
| 2026-08-30 | 🔒 **Gmail scope = Inbox + Sent ONLY** (Archive/Trash/Spam/Drafts excluded), enforced in the query *and* again at write time | Admin directive: the record of record is what was actually received and actually sent |
| 2026-08-30 | Ingestion policy **strict allowlist**: RKB + a *registered* external counterparty; unknown counterparties become discovery candidates, never silent ingests. Backfill from **Oct 2023** | Admin directive; keeps 255k personal messages out while making misses visible |
| 2026-08-30 | New gaps opened by real data → sprints: **spreadsheet extraction as a first-class path** (53 files carry the money), **video ingestion** (15 .mp4), **privileged/legal doc class with restricted access**, **disk-corpus pipeline** | Found by inventorying `E:\LP Remodeling Projects\Hold Properties` (377 files, 684 MB) |
| 2026-08-30 | Originals moved out of **GridFS** into a content-addressed **object store** (S3-shaped interface, local backend) | Atlas cluster storage fell 218 MB → 4.2 MB; 908 MB of binaries do not belong in the document DB, and the interface swaps to real S3 without touching callers |
| 2026-08-30 | **PDF text layer checked before vision OCR** | Measured: only 1,411 of 2,490 pages actually need vision. OCR-ing a digital PDF costs money to produce a *worse* transcription than the exact text already embedded in it |
| 2026-08-30 | **OCR output format changed from JSON to delimited** (`###META` / `###TEXT`) | Contracts are full of quoted defined terms (`the "Holdback"`) which models fail to escape inside JSON strings; recovery salvaged only the fragment before the first stray quote — one page went from 2,966 characters to **5** |
| 2026-08-30 | OCR truncation taken from the provider's `stop_reason`, not inferred from confidence | The JSON parse-failure fallback returned `confidence 0.5`, which tripped the escalation threshold — every dense page was being re-read on Opus 5 at double cost for a problem that was never about legibility |
| 2026-08-30 | **OCR pages read concurrently** (6-way) | The run is latency-bound, not compute-bound; 20s/page → 3.1s/page, turning a ~7 hour corpus pass into ~70 minutes |
| 2026-08-30 | **Provider content-policy blocks handled as a distinct state** with offline RapidOCR fallback + `needs_human` flag | Title policies and lien packages carry dense personal identifiers and get refused. It is permanent, not transient, so it must never be retried and never silently dropped — a missing page in a lien package is exactly what makes a later answer confidently incomplete. 39 blocked pages, **all recovered locally** |
| 2026-08-30 | **Property tagging pushed down to the segment**, and chunks never mix property sets | Artifact-level tags let a Varnum sentence answer a Decatur question. Verified: 904 vs 910 Bayshore return **zero shared chunks** across 25 results each |
| 2026-08-30 | Retrieval filters by property **inside** the vector search (Atlas filter field), not after | Post-filtering lets other properties consume the top-k slots and turns any filter bug into a silent cross-property leak |
| 2026-08-30 | **Every analyst claim is mechanically verified against its citation handle**; unprovable claims are deleted and reported | A fabricated citation is indistinguishable from a real one to the reader. First live run: 31 findings, 100% integrity, 0 dropped |
| 2026-08-30 | Analyst JSON salvaged on truncation and marked `truncated` | A long analysis can exhaust the token ceiling mid-object; discarding it would throw away findings that are complete and correct — but a partial analysis must never be mistaken for a complete one |
