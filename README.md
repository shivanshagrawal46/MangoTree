# MangoTree Intelligence System

**The fully-automated renovation-lending intelligence platform** for **RKB Consulting Group** (formerly MangoTree), which funds renovation budgets for properties whose owners can't finance the work — **earning interest on the money lent**. Our counterparty is **ROI Blocks LLC / LP Remodeling** (one company, two names), run by contractors Wes and Kelly who subcontract the renovation.

The system's job: ingest *everything* (email, photos, videos, documents, calls, guidelines), understand it per-property, analyze it automatically before anyone asks, and surface what needs a human — with evidence behind every claim. The core risk it exists to catch: **money released against work not actually done.**

> 📌 **Read `docs/06-BUSINESS-CONTEXT-MEMORY.md` first** — it is ground truth for who we are, the people, the properties, and the email rules. It overrides any conflicting statement elsewhere.

---

## Repository Map

| Path | What it is |
|---|---|
| `docs/00-ARCHITECTURE.md` | Full system architecture — components, data flow, storage, pipelines |
| `docs/01-AI-MODEL-STACK.md` | The 5-model AI stack: which model does what, and why |
| `docs/02-INGESTION-SOP.md` | The full automated ingestion pipeline SOP — mailboxes, photos, multi-property fan-out |
| `docs/03-CONTEXT-AND-MEMORY.md` | 3-tier context, property-wise Remember memory, how analysis never misses context |
| `docs/04-CONSULTANT-REVIEW.md` | Expert review of the sprint plan — what's strong, what's risky, what was changed |
| `docs/05-UI-UX-PRINCIPLES.md` | The trust-first UI/UX contract — evidence drawers, one chat per property, fingertips design |
| `docs/06-BUSINESS-CONTEXT-MEMORY.md` | **GROUND TRUTH** — who we are, the team, the property registry, the email rules, data inventory |
| `sprints/SPRINT-TRACKER.md` | **The master tracker** — every sprint, status, gate, completion ticks |
| `sprints/sprint-00.md` … `sprint-12.md` | Detailed per-sprint work items with `[ ]` checkboxes and gate criteria |
| `mangotree/` | **The running system** — Sprint 0–1 ingestion (Gmail + disk corpus), live against MongoDB Atlas |
| `tests/` | Test suite for the rules that must never silently break |
| `src_reference/` | **The v1 reference implementation** (ingestion, **RAG v3**, timeline, graph, dossiers) — port and harden, don't rewrite blind. 🔒 *We build on `rag/v3/` only; `rag/v2/` is not used.* |
| `scripts_reference/` | v1 operational scripts — backfills, dedup, OCR, detectors, eval harness, golden regression, gate checks |

## How to use this repo

1. **Track work** in `sprints/SPRINT-TRACKER.md` (high level) and the per-sprint files (task level). Tick `[x]` as items complete.
2. **Add new features** to any sprint under its `## Added Features` section — every sprint file has one. New scope gets a date and a reason.
3. **Never delete** a work item — mark it `[x]` done, `~~struck~~ (descoped, date, reason)`, or moved.
4. **Gates are hard**: a sprint is not done until every gate criterion is ticked and evidenced.

## The One-Paragraph System

Emails (Outlook + Gmail), field photos (WhatsApp/Dropbox), call recordings (Granola/Zoom), and company guidelines flow into a single ingestion pipeline: cleaned, deduplicated, OCR'd, classified into 17 document classes, resolved to properties, chunked with 3-tier context, and indexed six ways. A deal ledger holds every dollar source-linked; a knowledge graph holds every entity; a versioned policy rulebook holds every company standard as a checkable rule. A scheduler drives the auto-analysis pipeline — every new artifact triggers extraction, detection, timeline updates, task creation, and change cards for its properties, automatically, in arrival order. Users get per-property workspaces (full timeline, scoped chat, docs, photos, money), a pending-vs-handled dashboard, 6 a.m. briefs, and an agent that answers with verbatim-quoted, verified, coverage-stated evidence. Detectors and the policy-deviation engine watch every deal continuously; photo forensics audits every draw before a human opens it; forecasting prices every active deal nightly.

## Key People / Mailboxes

See `docs/02-INGESTION-SOP.md` for the authoritative mailbox registry.

- **Rakesh Sir** — principal decision-maker; guidelines owner; instant-activation corrections/memories
- **JP Sir** — reviewer; the review-routing state machine is built around his thread
- **Analyst** — verification queues, pending corrections

## Status

Planning complete — execution starts at Sprint 1. Current status lives in `sprints/SPRINT-TRACKER.md`.
