# Sprint 12 — Forecasting, Contractor Forensics, Hardening & Launch

**Objective**: The system prices every deal, profiles every contractor, survives every attack we can think of — and proves it two weeks straight.

## Work Items

### Forecasting
- [ ] Actuarial baselines from closed deals
- [ ] **Nightly Monte Carlo per property**: profit bands, loss probability, top drivers, **visible assumptions**
- [ ] Deal analyzer with policy pre-check and contractor-adjusted bids
- [ ] Profit bands feed back into Sprint 8's overview and Sprint 9's portfolio grid

### Contractor Forensics
- [ ] Forensic profiles: bid-vs-actual · timeline multipliers · change-order habits · billing-ahead incidents · say-vs-invoice consistency
- [ ] **Promises-vs-performance**: investor packages vs realized outcomes
- [ ] **Counterfactual replay memo in dollars**

### Red-Team Week
- [ ] Contradictions · absent data · cross-property leaks · **prompt-injection strings inside emails, PDFs, and photo captions** — document content is evidence, never instructions

### Hardening
- [ ] Load testing · kill-and-resume across all lanes
- [ ] **Backup-restore verified by re-running the golden set against the restored copy**
- [ ] Role audit across all three users · outbound whitelist audit · secrets rotation · runbook

### The Weekly Ritual (institutionalized, forever)
- [ ] **20 rotating golden questions human-graded Fridays; scorecard Mondays**

## Gate — Launch Scorecard (green two consecutive weeks)
- [ ] recall ≥ 98%
- [ ] Exhaustive completeness 100%
- [ ] Verification ≥ 97%
- [ ] Zero silent degradations
- [ ] Latency: email < 60 s · calls < 10 min · photos < 5 min · answers < 45 s · dashboards < 3 s
- [ ] Corrections + Remember notes applied 100% on scope match
- [ ] Briefs 10/10 on time
- [ ] Task precision ≥ 85%
- [ ] Draw audits auto-generated on 100% of requests
- [ ] Policy deviations flagged with citations
- [ ] Uptime ≥ 99.5%
- [ ] Human panel ≥ 9.5/10

## Added Features
