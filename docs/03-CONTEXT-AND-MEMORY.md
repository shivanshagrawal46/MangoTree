# Context & Memory — How Analysis Never Misses Anything

This document answers the two hardest requirements directly:
1. **Per-property full context + high-level portfolio context**, always current, automatically.
2. **Any analysis (by the reasoning model) sees every relevant fact, change, and user-taught note for that property — nothing missed.**

---

## 1. The 3-Tier Context System

Every chunk in the index is embedded *with* its context, and every analysis call is assembled *from* these tiers:

| Tier | What | Written by | Refresh |
|---|---|---|---|
| **Tier 1 — chunk-in-document** | 1–2 sentences situating this chunk inside its document ("This is the payment-terms clause of the Maple St scope agreement…") | AI workhorse at index time, prompt-cached document prefix | Once, at ingest |
| **Tier 2 — document-in-deal** | Templated paragraph situating the document inside its deal ("Invoice #214 from LP Remodeling against the Maple St rehab budget, phase 2 of 4, received during the flooring dispute…") | Templated from ledger + graph | **Incremental only** — refreshed when its deal changes (`tier2_version` on chunks tells stale from fresh) |
| **Tier 3 — deal-in-portfolio** | The property's live card: status, day count, budget position, health, open findings, key entities, recent events | Materialized from ledger/events/dossier | Continuous; **injected at answer/analysis time, never embedded** |

Embedded text per chunk = `Tier-1 + Tier-2 + header + chunk`. Raw body kept separately for highlighting.

## 2. The Per-Property Context Assembly (used by every AI analysis)

Any time the reasoning model analyzes anything for property P — a chat question, a detector judgment call, a change card, a draw audit, a brief section — the context assembler builds the same standard package:

```
1. Tier-3 card for P                     (live state: money, phase, health, day count)
2. Matching Remember notes               (global + property-P + involved-contractor scoped)
3. Matching corrections                  (active corrections that touch P or the topic)
4. Policy rules in scope for P           (from the versioned rulebook)
5. The artifact/chunks under analysis    (Tier-1/2 context inline, verbatim raw for quoting)
6. Delta context                         (P's last-known state vs now — what changed)
7. Document inventory counts for P       (the coverage denominator — the model KNOWS
                                          how many invoices/draws/permits exist, so it
                                          can state coverage and notice absence)
```

**Why nothing gets missed:**
- The Tier-3 card and inventory counts give the model the *denominator* — it can say "I checked 14 of 14 invoices" and it can notice a *missing* document, not just a present one.
- Retrieval is hard-filtered to `property_ids[]` at chunk level, then the **enumeration router** bypasses similarity entirely for exhaustive questions — complete sets fetched from the ledger by ID. Similarity search can miss; `SELECT ... WHERE property_id = P` cannot.
- The **change-detection agent** runs per artifact against P's last state, so "any change related to that property" is computed at ingest time and stored — never re-derived (and possibly missed) at question time.
- The **coverage gate** forces every answer to carry a coverage statement; the **verifier** checks quotes byte-for-byte. An analysis that skipped context fails its own gate.
- Nightly **dark-data sweep** reviews high-authority documents that were never retrieved — the safety net for "the retriever never surfaced it."

## 3. Remember Memory (property-wise user-fed knowledge)

Distinct from corrections: **corrections fix the system; Remember teaches it things it couldn't know.**

- **Capture**: a Remember button in every chat/card/page + natural-language trigger ("remember this: Wes always underestimates flooring timelines").
- **Stored as a structured note**: text, **scope** (`global` | `property:<id>` | `contractor:<id>`), author, date, status.
- **Injection**: every matching AI call — chats, briefs, detectors, auto-analysis pipeline, draw audits — receives matching notes in its context package (§2, slot 2). *Property-scoped notes ride with every analysis of that property automatically.*
- **Attribution**: when a note shapes an answer, the answer says so — "per your note from Jul 20…".
- **Governance**: same admin page as corrections — view, edit, retire. Global notes are **capped and consolidated weekly** so always-on context stays sharp. **Rakesh Sir's notes activate instantly; analyst notes pend his approval.**

### 3.1 Hallucination-proof guarantees (the note is law, never a suggestion)

Remember notes are the admin telling the system something it must never get wrong. Five mechanical guarantees:

1. **Stored verbatim.** The note's text is never paraphrased, summarized, or "cleaned up" at storage time. What the admin typed is what the database holds.
2. **Matched deterministically.** Scope matching is a database lookup (`scope = global OR property_id = P OR contractor_id = C`) — never similarity search. A property-scoped note *cannot* fail to be retrieved for its property.
3. **Injected verbatim.** The note rides into the prompt as an exact quoted block with author and date, in a dedicated "Admin knowledge — treat as ground truth, cite when used" section — above retrieved evidence in priority.
4. **Echo-checked.** The verifier confirms that when an answer cites a note ("per your note from Jul 20…"), the quoted content matches the stored note byte-for-byte — same machinery as evidence quotes. An answer that contradicts an in-scope active note fails verification and is retried with the conflict surfaced.
5. **Consolidation is human-approved.** The weekly consolidation of global notes produces a *proposed* merge; it only replaces the originals after admin approval. Property-scoped notes are never auto-consolidated.

If a note conflicts with document evidence, the system does not silently pick one: the answer states both ("your note says X; invoice #214 shows Y") and a review item is raised.

## 4. Correction Memory

- Any dismissed card, corrected answer, or flagged mistake becomes a correction record with scope and evidence.
- Hierarchy: **Rakesh Sir → instant-active; analyst → pending approval.**
- Applied at retrieval/answer time on scope match, 100% application is a launch-scorecard line.
- Weekly consolidation merges overlapping corrections.

## 5. The Property Workspace = the Context, Visible

The per-property page (Sprint 8) is deliberately the *same* context the AI uses, rendered for humans:
- **Docs tab** shows every document for the property with the same inventory counts the AI uses as denominators — *your "all documents for that property, then and there" requirement*.
- **Timeline** = the events store; **Money** = the ledger; **Photos** = the photo timeline.
- **Property chat** is hard-filtered to the property, with the Tier-3 card + matching Remember notes always in context, and Remember / save-answer / make-task buttons inline.
- The **as-of slider** replays the bitemporal store: the property as known on any past date.

If a human can see it on the property page, the AI had it in context — and vice versa. One source of truth, two renderings.

## 6. Portfolio-Level (high-level) Context

- A **portfolio card** (Tier-3's big brother): active deal count, capital deployed, aggregate health, top risks, recent significant events. Injected for portfolio-scope questions and the dashboard's ad-hoc AI column.
- Cross-property questions route through the enumeration router (per-property sub-queries, then aggregate with denominators) — never one big similarity search across everything.
