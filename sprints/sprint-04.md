# Sprint 4 — Scheduler & the Auto-Analysis Pipeline

**Objective**: "Automatic analysis before any command" becomes physical infrastructure. The cron core from Sprint 0 is replaced by the real scheduler; the full event chain is one orchestrated, resumable flow.

## Work Items

### Job Runtime
- [ ] Three lanes: **heavy / light / realtime** — realtime never blocked by the others
- [ ] Full job contracts: schedule, retries, idempotency keys, SLA, **cost tracking** per run
- [ ] Dead-letter queue with alerts; manual trigger for every job
- [ ] **Scheduler health as a red-banner UI element** (any expired subscription, stuck lane, dead-lettered job = banner)
- [ ] Migrate all Sprint 0–3 cron jobs onto the runtime; retire the cron core

### The Auto-Analysis Pipeline (the spine)
- [ ] One orchestrated flow per artifact: ingest → resolve → classify → OCR → chunk/context/index → extraction → ledger/graph/events → detectors (as they exist) → timeline event → tasks/commitments → change cards → live push
- [ ] **Per-property sequencing**: analyses for one property run in arrival order; properties run in parallel
- [ ] Every stage **idempotent** (artifact SHA + stage key) and **resumable**
- [ ] Stage-by-stage verification tooling (inspect any artifact's chain, see each stage's output + timing)

### Standing Schedule (all registered with contracts)
- [ ] Subscription/watch renewals · nightly reconciliation sweeps · Tier-2 refreshes · recall sampling · nightly detector full-pass (safety net; event-driven detectors run per-artifact) · briefs · commitment sweeps · dark-data review · correction consolidation · forecasts · backups

## Gate
- [ ] **All jobs green 5 consecutive days**
- [ ] **Kill-a-worker test**: mid-pipeline kill resumes clean, no duplicates, no gaps
- [ ] **A single test email triggers the complete chain end-to-end automatically, verified stage by stage**

## Added Features
