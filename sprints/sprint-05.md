# Sprint 5 — Task Engine, To-Do, **Two-Stage Bill Approval** (Manjunath Sir → JP Sir), Auto Email Summaries

**Objective**: Tasks with audit-grade evidence; the approval state of every bill and draw known from classification at email time, never guessed at query time; every user's to-do automated *and* manual.

## Work Items

### Task Engine
- [ ] Full task lifecycle with **append-only, evidence-linked audit events**
- [ ] Email reader on every inbound message: summary, intent, implied tasks, commitments, review signals — with **task dedup**

### JP Sir Review Workflow (fully automatic)
- [ ] Outbound to JP Sir **automatically** flips linked tasks to `sent-for-review`
- [ ] His replies classified: approved / changes-requested / question
- [ ] **3-business-day aging** surfaces in his brief and the sender's waiting-on view, with nudge drafts
- [ ] "What's with JP Sir right now" = a database lookup with evidence
- [ ] Labeled thread set built for the state-machine gate (historical JP Sir threads)

### Add-Tasks & To-Do
- [ ] Manual creation from anywhere: any answer, finding, or card → task in one click
- [ ] Quick-add on property pages
- [ ] **My To-Do per user**: their tasks, waiting-on, review queue

### Auto Email Summaries
- [ ] Stored per-email summaries at ingest
- [ ] Scheduled per-user digests (end-of-day or interval) **delivered to their inbox by the system**

### Reply Drafter
- [ ] Drafts in each user's voice, cited, saved to their Drafts folder (send stays flagged off)

## Gate
- [ ] **2-week shadow run**: task precision **≥ 85%**
- [ ] JP Sir state machine **≥ 95%** on labeled threads
- [ ] "Sent to JP Sir" audit trail complete
- [ ] Digests on time for **all three users**
- [ ] **Zero autonomous sends**

## Added Features
*(2026-08-12, admin request)*
- [ ] **Comms Tracker UI** — one screen tracking every email across Gmail + Outlook for the team: thread status (needs reply / waiting on them / handled), pending drafts per user, who owes whom a response, aging; filterable by property, person, mailbox
- [ ] **Task assignment**: any task assignable to any team member (auto-suggested assignee, manually overridable); assignee sees it in My To-Do; reassignment audited
- [ ] **Decision & deadline checkboxes**: every decision and deadline the system extracts becomes a checkable item — admin ticks done/verified, unticked ones surface by urgency, new ones appear automatically as emails/calls arrive

*(2026-08-30, from `docs/06-BUSINESS-CONTEXT-MEMORY.md` §7 — this role was missing entirely)*

### Manjunath Sir — Civil Verification & Bill Approval
Manjunath Sir is the **Civil Work Advisor**: he verifies what civil work has actually been done and **approves the bills**. Because RKB is a lender, the money leaves on a *draw*, and the single largest risk in the business is **money released against work not actually done**. He is the human control on exactly that — so his approval is a distinct, earlier gate than JP Sir's accounting approval, not a variant of it.

- [ ] **Two-stage approval state machine**: `submitted → civil-verified (Manjunath Sir) → accounting-approved (JP Sir) → released`; a bill or draw can never reach `released` without passing civil verification first, and the stage that blocked it is always visible
- [ ] Auto-detect bills, invoices, draw requests and change orders at ingest → open a civil-verification task addressed to Manjunath Sir, with the claimed scope and amount extracted and quoted
- [ ] **Evidence pack assembled automatically** for each verification: the draw schedule line items, the change orders against them, site photos and daily logs for the period, and the prior draw's verified state — so he reviews evidence, not a bare number
- [ ] His verdicts captured as structured decisions (verified / partially verified / rejected, with the disputed line items named) and written to the ledger as the authority for release
- [ ] **Disagreement surfaced, never averaged**: where his verdict conflicts with the photo-forensics or draw-audit finding (Sprint 11), both are shown side by side and the item is held — the human verdict is authoritative for release, the machine finding is never silently discarded
- [ ] Aging + nudges on pending civil verifications, mirroring the JP Sir 3-business-day rule

**Gate additions**
- [ ] No draw reaches `released` in the shadow run without a recorded civil verification
- [ ] Two-stage state machine **≥ 95%** on labeled historical bill/draw threads
- [ ] Every release in the ledger traces to both approvals in ≤ 2 clicks
