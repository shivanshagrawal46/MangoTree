# Sprint 3 — Deal Ledger, Knowledge Graph, Policy Rulebook, Dossiers

**Objective**: Every dollar source-linked, every entity resolved, every company standard a checkable rule.

## Work Items

### Deal Ledger
- [ ] Schema: deals, budget lines by trade, invoices + invoice lines, draws, phases, holding costs, loans, outcomes — **every row source-linked forever** (artifact + span)
- [ ] Ledger UI: click any number → evidence drawer opens the exact source

### Extraction Contracts (per money-bearing class)
- [ ] Invoices (**sum-validated**: lines must reconcile to totals) · draw schedules · underwritings (the baseline) · scope agreements with exclusions · insurance · loans · investor-package promises
- [ ] Confidence bar + **human verification queue — money never enters unverified**
- [ ] Every extraction stamped `model@version + prompt_version`
- [ ] *(added 2026-08-25)* **Fact-level property assignment** (SOP §2.5): every extracted fact assigned to exactly **one** property, inherited from its source segment; multi-property emails yield per-property facts each citing only their own words; unsplittable facts ("$12k total for both jobs") → verification queue, **never divided by guess**

### Policy Rulebook (company-policy memory, part 1)
- [ ] **Half-day session with Rakesh Sir** — *Rakesh Sir session #3a (booked in Sprint 2)*
- [ ] Decompose ingested guidelines into checkable rules; each rule: plain-language statement · **source citation into the guideline doc** · machine-checkable condition where possible ("max rehab budget 35% of ARV", "change orders require written approval", "insurance bound before demo day") · scope
- [ ] Confirmed rules become active; rulebook **versioned alongside the guidelines**

### Knowledge Graph
- [ ] Resolution pipeline: exact → fuzzy → create · **role-firewalled auto-merge** · grey-zone AI judge · human review + alias learning
- [ ] **Role firewall encodes the two-name reality**: Wes = one human, distinct role-entities (ROI Blocks owner / LP Remodeling contractor); cross-role auto-merge forbidden (consultant review risk #7)
- [ ] Chunk ↔ entity backfill across the whole corpus

### Events & Dossiers
- [ ] **Bitemporal events store** (`occurred_at` + `recorded_at`)
- [ ] Materialized dossiers with **document-inventory counts** (the coverage denominators every analysis uses)

## Gate
- [ ] **10 properties reconciled line-by-line with Rakesh Sir** — *Rakesh Sir session #3b*
- [ ] Money extraction **≥ 97%**
- [ ] Entity resolution **≥ 95%**
- [ ] Rulebook confirmed, **every rule source-cited**

## Added Features
