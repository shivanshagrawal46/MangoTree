# Consultant Review — What's Strong, What I'd Change, What Could Sink Us

Honest assessment of the 12-sprint plan, as your architecture consultant. The plan is unusually good — evidence-linked everything, deterministic-before-AI, hard gates, human queues under every confidence bar. I changed little. What follows is what I *did* change, and the risks nobody's gate currently catches.

---

## 0. The v1 reference implementation changes the execution posture

The workspace already contains a working prior build — `src_reference/` (Gmail ingestion, PST parsing, the v2 chunk/retrieval stack with hybrid search, verifier, coverage, entailment; the v3 agent with scratchpad, tools, injection guard, cross-critic; timeline builder, knowledge graph, dossiers) and `scripts_reference/` (backfills, dedup, OCR cascade with repair, detectors, eval harness, golden regression, old sprint-gate scripts). The 12-sprint plan is therefore a **productionization and completion**, not a greenfield build. Execution rule per sprint: **read the reference module first, port what passed its gates, rebuild only what the new architecture contract (Postgres system-of-record, occurrences fan-out, chunk-level property tags, job contracts) makes obsolete.** The eval harness and golden-regression scripts are the seeds of Sprint 2's recall harness and Sprint 12's weekly ritual — don't rewrite those, extend them. What v1 visibly lacks and the sprints must add fresh: Outlook/Graph ingestion, the photo channel, the scheduler lanes, the policy rulebook/deviation engine, the task engine + JP Sir routing, Remember memory, the per-property workspace UI, forecasting.

## 1. Changes I made to the plan (already reflected in the sprint files)

### 1.1 Added a Sprint 0 (one week, infrastructure)
The original plan has Sprint 1 doing app registrations *and* backfill *and* realtime *and* cleaning *and* dedup *and* resolution *and* photos *and* guidelines. App registration + access policies alone can take days of admin-consent back-and-forth. Sprint 0 pulls out: repo/CI, environments, Postgres + object store, Graph app registration + access policy, Gmail OAuth + Pub/Sub, secrets vault, and a **minimal cron core**. Sprint 1 then starts with working credentials on day one.

### 1.2 Minimal scheduler in Sprint 0, formalized in Sprint 4
The original plan builds the scheduler in Sprint 4, but Sprints 1–3 already *need* scheduled jobs (subscription renewals, nightly reconciliation, Tier-2 refresh, recall sampling). Without this fix, Sprints 1–3 grow ad-hoc cron hacks that Sprint 4 must then untangle. So: dumb-but-reliable cron core early; the three lanes, contracts, dead-letter, and health UI land in Sprint 4 as planned.

### 1.3 WhatsApp demoted to fast-follow; Dropbox is the week-3 photo channel
WhatsApp Business (Cloud API) has real friction: Meta business verification, and **group-chat ingestion is poorly supported** — the API is built for 1:1 business messaging. A watched Dropbox (or Google Drive) folder per crew is a two-day build with none of that risk. The gate ("phone photo property-linked < 5 min") stays; the channel that first meets it is Dropbox. WhatsApp remains in Sprint 1's Added Features as a fast-follow if the crew won't adopt the folder.

### 1.4 Baseline retrieval moved into Sprint 2
The recall harness (Sprint 2 gate: recall@20 ≥ 95%) cannot measure recall without a retriever. Sprint 2 therefore includes a *baseline* hybrid retrieval (vector + BM25 + RRF + filters). Sprint 6 upgrades it to the full v2 stack. Bonus: property chat and briefs get useful early, and the golden-question set starts accumulating months before Sprint 12's launch scorecard.

### 1.5 Alicia's mailbox is a day-one action
`alicia@lpremodel.com` — she has left. Departed-employee mailboxes get deleted or their licenses reclaimed, often on a 30-day timer. **Backfill her mailbox first, before any other work**, and confirm a litigation-hold/retention setting so history can't vanish mid-project. This is the single most perishable asset in the plan.

### 1.6 Golden sets and labeled data start in Sprint 1, not when gates need them
Nearly every gate needs labeled data: classification ≥ 95% needs labeled docs, segmentation ≥ 90% needs labeled calls, task precision ≥ 85% needs labeled threads, photo accuracy needs a labeled photo set. Each sprint file now has an explicit "build the labeled set" item *one sprint before* its gate consumes it.

## 2. Risks the gates don't catch (watch these)

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Rakesh Sir is on the critical path** ~6 times (guidelines corpus, rulebook half-day, 10-property reconciliation, live week, draw approval, weekly grading). If his availability slips, gates slip. | Book all sessions now, in the calendar, as sprint-gate ceremonies. Sprint files mark each one. |
| 2 | **Property registry is assumed but never built.** Resolution "≥ 90%" needs a canonical list of properties with addresses/aliases/parcels before Sprint 1 ends. | Explicit Sprint 1 work item: seed the registry with Rakesh Sir from the deal list. |
| 3 | **Chunk-level multi-property tagging** is subtly hard (one paragraph mentioning two properties). If done lazily at artifact level, cross-property leakage happens and Sprint 8's adversarial gate fails late. | Designed in from Sprint 1 (per-segment resolution), tested adversarially in Sprint 2, not Sprint 8. |
| 4 | **OCR backfill cost blowout.** "Force-vision over the entire corpus" on years of attachments can be startlingly expensive. | Budget guard + cheapest-capable-model-first cascade (already in plan); add a pre-flight cost estimate on the corpus *before* the run, as a Sprint 2 item. |
| 5 | **Gmail/Graph quota + throttling during backfill.** Full-folder backfills hit rate limits; naive code loses messages silently. | Backfill as resumable checkpointed jobs with per-mailbox cursors; reconciliation sweep is the proof. |
| 6 | **Model/prompt drift.** A provider model update can silently move classification or extraction accuracy. | Version-stamp every AI output; recall harness + golden set re-run on any model/prompt change (in model-stack rules). |
| 7 | **Two-name company ambiguity** (ROI Blocks vs LP Remodeling, Wes in both, Wes-as-contractor). Entity resolution will want to merge what the business treats as distinct roles. | The knowledge graph's **role firewall** (already in Sprint 3) must encode: one human, multiple role-entities; auto-merge across roles forbidden. |
| 8 | **Scope temptation.** Twelve sprints of this density is 6+ months at 2-week sprints even with a strong team. | The tracker forbids starting sprint N+1 while N's gate is red. Added Features sections exist so new ideas are *captured* without derailing the current gate. |

## 3. What I explicitly endorse (don't water these down)

- **Nightly reconciliation as a guarantee, not a nicety.** Push notifications from both providers *will* be missed. The sweep is what makes "never a missed email" true.
- **Money never enters unverified.** The verification queue below confidence is what makes the ledger trustworthy enough for draw audits and forecasting.
- **The enumeration router.** Similarity search fundamentally cannot answer "list ALL invoices" — the by-ID ledger path with denominators is the only honest exhaustive answer.
- **Photos from week 3** (moved up for Sprint 11's benefit) — months of property-linked history is exactly what makes draw audits land.
- **Coverage statements + verbatim verifier** — this is what makes the system auditable rather than plausible.
- **Zero silent degradations** — every fallback visible. Non-negotiable.

## 4. Sequencing at a glance (with my changes)

```
S0  Infra, credentials, cron core, Alicia backfill      (1 wk)
S1  Email in/out, photos (Dropbox), guidelines, registry
S2  OCR, chunks, 3-tier context, indexes, BASELINE retrieval, harness
S3  Ledger, graph, rulebook, dossiers
S4  Scheduler formalized, auto-analysis spine
S5  Tasks, JP Sir routing, digests
S6  Retrieval v2, agent v3, memory (corrections + Remember), saved answers
S7  Calls (Granola/Zoom)
S8  Per-property workspace
S9  Dashboard, briefs, reports
S10 Detectors, policy-deviation engine
S11 Photo forensics, draw audits
S12 Forecasting, contractor forensics, red-team, hardening, launch
```

Dependency shape is sound: data (0–3) → automation spine (4–5) → intelligence (6–7) → surfaces (8–9) → enforcement (10–11) → prediction + hardening (12).
