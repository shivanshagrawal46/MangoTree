# UI/UX Principles — Everything at the Fingertips, Trust Never Breaks

The UI is not a viewer on the system; it **is** the trust contract with the CEO. One bad number shown confidently costs more than a week of missing features. These principles bind every screen.

---

## 1. The Trust Rules (non-negotiable, every screen)

1. **Every number, claim, and card is clickable → the evidence drawer opens the exact source** (email, invoice line, photo, transcript line) with the original from S3 one more click away. Nothing is asserted that can't be shown.
2. **Degrades are visible.** If any AI stage fell back, if a subscription lapsed, if a job is dead-lettered — a banner says so. The CEO never unknowingly looks at partial data.
3. **The panel's dissent is visible.** High-stakes outputs show the Expert Panel verdict; if a critic objected, the objection is one click away.
4. **Confidence is shown, not hidden.** Below-bar items visibly sit in review queues — "the system wasn't sure" is displayed as a strength, not concealed as a weakness.
5. **What the system did on its own is always visible** (the Handled column, the audit trail) — automation earns trust by being seen.

## 2. The Property Workspace — one property, one place, one chat

- **One chat per property.** A single persistent conversation UI per property — not sessions, not tabs. The full chat history is retained forever; a **rolling contextual summary** (maintained automatically, next-level: it carries decisions made in chat, open questions, admin instructions given) means the chat never "forgets" what was discussed in week 2 when you ask in month 6. The Tier-3 card, matching Remember notes, and the rolling summary ride with every message.
- **The unified timeline** interleaves *everything* — emails (whole threads), documents, photos, calls, draws, decisions, findings — one scrollable, filterable stream with the as-of slider. **What changed is first-class**: delta badges on events, a "changes since I last looked" mode per user.
- **Thread view**: any email opens into its complete thread, stitched start-to-end across Gmail and Outlook and across mailboxes (internet-message-ID + references headers), with attachments inline and each message's timeline position marked.

## 3. Fingertips Design Language

- **Command palette (Ctrl+K) everywhere**: jump to any property, ask any question, create any task, from anywhere in ≤ 2 keystrokes + typing.
- **Live by default**: WebSocket push — new events, cards, and to-dos appear without refresh, with a subtle "new" pulse, never a jarring reflow.
- **Speed budgets**: dashboards < 3 s, page-to-page < 500 ms, evidence drawer < 1 s. Slow is untrustworthy.
- **Progressive density**: glance → scan → drill. Every screen answers its question at a glance (health color, one number), rewards a scan (the row detail), and permits a drill (the evidence).
- **One-click actions on every surfaced item**: approve, dismiss-with-remark, make-task, remember-this, draft-reply, export.
- **Checkbox discipline**: decisions and deadlines are checkable items everywhere they appear; ticking in one place ticks everywhere; every tick is audited.
- **Exports everywhere**: PDF from any view, Excel from any financial view — formatted, referenced, ready to forward.
- **Aesthetic**: calm, modern, information-dense without clutter — muted base palette, color reserved for meaning (health, severity, money-at-risk), consistent typography scale, generous evidence-panel layouts. Interesting without being loud; the CEO should *enjoy* opening it at 6 a.m.

## 4. Per-User Fit

Rakesh Sir opens to decisions and money. JP Sir opens to his review queue. The analyst opens to verification queues. Same system, three home screens — nobody hunts for their next action (the <10 s gate in Sprint 9).

## 5. Where this is enforced

- Sprint 8 gates: live updates < 90 s, leak-free scoped chat, as-of slider, live-week dismissal < 30%
- Sprint 9 gates: next-action < 10 s for all three users
- Sprint 12 launch scorecard: dashboards < 3 s, human panel ≥ 9.5/10
