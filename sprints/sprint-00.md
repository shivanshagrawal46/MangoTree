# Sprint 0 — Infrastructure & Credentials (1 week)

**Objective**: Everything Sprint 1 needs on day one — environments, credentials, storage, and the perishable data secured. *(Added by consultant review §1.1.)*

## Work Items

### v1 Reference Audit (do alongside setup)
- [ ] Inventory `src_reference/` + `scripts_reference/`: per module, decide **port / extend / rebuild** against the new architecture contract (record in tracker Decision Log)
- [ ] Identify which v1 gate scripts (`eval_harness.py`, `golden_regression.py`, `final_gates.py`) become the seeds of the Sprint 2 recall harness and Sprint 12 ritual

### Repo & Environments
- [ ] Monorepo scaffold (backend FastAPI, workers, frontend Next.js), CI, lint/test hooks
- [ ] dev / staging / prod environments; staging can never send email (hard block, not config)
- [ ] Storage provisioned per the hybrid decision (`docs/00-ARCHITECTURE.md` §2.1): **S3** (originals as-is) + **MongoDB** (document layer) + **Postgres 16** (money & state machines)
- [ ] **Decision gate**: vector index (Qdrant vs Atlas Vector Search) and keyword search (Atlas Search vs Typesense) — pick, record in tracker Decision Log
- [ ] Secrets vault set up; no credentials in code from commit #1

### Credentials & Access
- [ ] Microsoft Graph app registration; application permissions; **access policy restricting to registry mailboxes only** (see `docs/02-INGESTION-SOP.md` §1)
- [ ] Admin consent obtained; read + send scopes verified against a test mailbox
- [ ] Gmail: OAuth per mailbox; Google Cloud Pub/Sub topic for watch/push — **includes `rakesh.bhargava@gmail.com`** (carries his `Rakesh@mtreh.com` send-as alias traffic)
- [ ] Graph access policy includes **`Rakesh@mtreh.com`** (Rakesh Sir's Outlook) *(2026-08-25)*
- [ ] Dropbox app + watched-folder access for photo channel

### Perishable Data — DO FIRST
- [ ] **Backfill `alicia@lpremodel.com` completely (all folders, all attachments) into raw store** — departed employee, mailbox may be reclaimed
- [ ] Confirm retention/litigation-hold on Alicia's mailbox with the tenant admin

### Cron Core (minimal, replaced by Sprint 4's scheduler)
- [ ] Dumb-but-reliable scheduled job runner: run registry, last-run status, failure alert to a channel
- [ ] First two jobs registered: Graph subscription renewal, Gmail watch renewal (fire even before Sprint 1 wiring, as no-ops)

### Registries
- [ ] Mailbox registry table seeded per SOP
- [ ] **Property registry** schema (canonical name, addresses, aliases, parcel, status) — seeded with Rakesh Sir in Sprint 1

## Gate
- [ ] Read a message from every registry mailbox via API in prod
- [ ] Alicia backfill complete and verified against provider counts
- [ ] Cron core running with alerting proven (kill a job, see the alert)
- [ ] Staging send-block proven

## Added Features
*(new scope goes here with date + reason)*
