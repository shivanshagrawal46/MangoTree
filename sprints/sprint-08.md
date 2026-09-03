# Sprint 8 — Per-Property Workspace: Full History, Timeline, Chat, Live Updates

**Objective**: Every ongoing property's complete history — auto-updating, chattable, everything linked. All documents for a property, then and there. This page *is* the AI's context, rendered for humans (see `docs/03-CONTEXT-AND-MEMORY.md` §5).

## Work Items

### Overview
- [ ] Status · day count · budget bar · health with reasons · profit band (populates when Sprint 12 forecasts land) · open tasks · active findings · policy-deviation flags

### Timeline — the full history
- [ ] **Every event ever**: purchase, phases, draws, invoices, insurance, permits, calls, decisions, commitments, findings, photos — typed, dated, evidence-linked
- [ ] Click any event → **evidence drawer opens the exact source**
- [ ] Filterable by type/date/entity
- [ ] **As-of slider**: the property as it was known on any past date (bitemporal replay)
- [ ] New events appear **live** as artifacts arrive (WebSocket/SSE, no refresh)

### Property Chat (beside the timeline)
- [ ] **Hard-filtered to this property** (chunk-level property filter, enforced in retrieval, not prompt)
- [ ] **One persistent chat per property** — a single conversation UI, full history retained forever *(2026-08-12, admin direction — replaces per-session model)*
- [ ] **Rolling contextual summary** maintained automatically per property chat: decisions made in chat, open questions, admin instructions given — so month-6 questions still know week-2 context; rides with every message alongside the Tier-3 card
- [ ] Tier-3 card + matching Remember notes **always in context**
- [ ] Remember, save-answer, and make-task buttons inline

### Tabs
- [ ] **Money** (ledger view) · **Comms** (emails/calls) · **Docs** (all documents for the property, with the same inventory counts the AI uses) · **Photos** (visual timeline) · **Tasks** · **Findings** · **Commitments**
- [ ] **Thread view in Comms**: any email opens its complete cross-provider thread start-to-end, attachments inline, each message's timeline position marked *(2026-08-12, admin direction)*
- [ ] UI/UX built to `docs/05-UI-UX-PRINCIPLES.md` — command palette, live-by-default, speed budgets, evidence drawer on every claim

### Change-Detection Agent
- [ ] Every new artifact → delta analysis vs the property's last state → "what's new" cards by significance
- [ ] Live push to open pages; severe → immediate notify
- [ ] **Suppression rule: no card without new evidence behind it**
- [ ] Dismissals-with-remarks feed correction memory

### Everywhere
- [ ] Full citation UI on every claim; **PDF export from any view**

## Gate
- [ ] Email → card + timeline event + task on-screen **< 90 s with no refresh**
- [ ] Scoped chat **leak-free under adversarial testing** (multi-property emails, cross-property questions)
- [ ] As-of slider verified against known historical states
- [ ] **Rakesh Sir live week** — *Rakesh Sir session #8* — card dismissal **< 30%**

## Added Features
*(2026-08-12, admin request)*
- [ ] **Per-property dashboard panel — "what's left"**: open decisions, unmet deadlines, remaining phases/milestones, pending verifications — each with an **admin-checkable checkbox** (done/verified); new items appear automatically as artifacts arrive; every tick audited
- [ ] **Deadlines board per property**: every extracted deadline (permits, insurance, draws, closings, promised dates) with countdown, owner, evidence link, and checkbox
- [ ] **Excel export of financials**: Money tab (budget lines, invoices, draws, holding costs) exports to formatted Excel with source references per row — alongside the existing PDF export (v1 `export_to_excel.py` as the seed)
- [ ] **Manual instruction inline**: admin can attach an instruction/correction to any card, event, or answer right where they see it — flows into Remember/correction memory with scope pre-filled
