# Sprint 10 — Detectors, Policy-Deviation Engine, Automatic Analysis Complete

**Objective**: Written standards become enforced standards. Ten detectors plus the rulebook, running per-artifact with a nightly safety net — every finding evidence-quoted and verifier-checked.

## Work Items

### Ten Deterministic Detectors (event-driven per artifact + nightly full-pass safety net)
- [ ] 1. Budget drift
- [ ] 2. Billing-ahead
- [ ] 3. Unapproved change orders
- [ ] 4. Verbal-vs-invoice (fueled by Sprint 7's verbal dollar claims)
- [ ] 5. Open loops
- [ ] 6. Review stalls
- [ ] 7. Timeline slip
- [ ] 8. Insurance gaps
- [ ] 9. Anachronisms
- [ ] 10. Commitment decay
- [ ] **Deterministic finding IDs**; confirm/reject survives re-runs; every finding **evidence-quoted and verifier-checked**

### Policy-Deviation Engine (company-policy memory, part 2)
- [ ] Every active deal continuously checked against the Sprint-3 rulebook
- [ ] **Machine-checkable rules evaluated from the ledger** ("rehab is 41% of ARV; your standard caps at 35%")
- [ ] **Judgment rules evaluated by the reasoning model with evidence**
- [ ] Every deviation = a finding citing **both** the violated guideline (chapter and verse from the company's own document) **and** the deal evidence
- [ ] **Policy pre-check on new deals** in the underwriting memo
- [ ] Rulebook-version-aware: deviations judged against the rule version in force at the time

### Dark-Data Sweep
- [ ] Weekly: never-retrieved high-authority documents per property reviewed for materiality — what no human eye got to

## Gate
- [ ] **8/8 planted anomalies caught, zero false-criticals**
- [ ] **3 planted policy violations flagged with correct guideline citations**
- [ ] A full **artifact-to-finding chain runs untouched end-to-end**

## Added Features
*(2026-08-12, admin request)*
- [ ] All severity-4+ findings and every policy-deviation finding ship through the **AI Expert Panel** (Sprint 6 machinery) — dissent visible, disagreement → human review
