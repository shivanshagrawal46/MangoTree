# Sprint 11 — Photo Forensics & Draw Audits

**Objective**: With months of property-linked photos accumulated since Sprint 1, photos become forensic evidence — and no draw gets approved without the system's packet already waiting.

## Work Items

### Vision Forensics Pass
- [ ] Every photo (and **retroactively the whole backlog**): room/area identification, work stage, visible materials, completion signals — structured, dated, property-linked observations
- [ ] Labeled photo set built (rooms/stages) for the accuracy gate

### Contradiction Detectors
- [ ] **Photo-vs-invoice**: invoiced work cross-examined against photographic evidence by date — "tile invoiced complete on the 12th; the 15th's photo shows unfinished backer board" — severity-weighted, evidence = photos side-by-side with invoice lines
- [ ] **Photo-vs-claim**: contractors' stated percent-complete (from emails and calls) vs what the photos show

### The Automated Draw Audit
- [ ] Before any draw approval, the packet assembles **automatically**: claimed milestones vs photographic evidence vs transcript claims vs the scope agreement vs the ledger → **verdict with exhibits** in the evidence-panel format
- [ ] Draw requests arriving by email trigger the audit through the auto-analysis pipeline — **the audit is waiting in the task before anyone opens it**

### Evidence-Supply Protection
- [ ] **Photo-coverage nudges**: phases with thin photo evidence prompt a request-photos draft to the crew — the system protects its own evidence supply

## Gate
- [ ] Vision pass **≥ 90% room/stage accuracy** on the labeled photo set
- [ ] A **planted photo-invoice contradiction caught**
- [ ] A **live draw request produces a complete audit packet automatically** before human review
- [ ] **Rakesh Sir approves one real draw from the packet alone** — *Rakesh Sir session #11*

## Added Features
*(2026-08-12, admin request)*
- [ ] Every draw-audit verdict ships through the **AI Expert Panel** — a draw packet is never single-model; the packet shows the panel's verdict and any dissent
