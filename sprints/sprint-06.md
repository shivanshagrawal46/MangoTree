# Sprint 6 — Retrieval Complete, **Agent v3** Complete, Trust Machinery, Remember Memory, Saved Answers

**Objective**: The full retrieval stack and agent — every technique, nothing dropped — plus the memory systems that make it correctable and teachable.

> 🔒 **RAG v3 ONLY (admin directive, 2026-08-30).** The system is built on the **v3 agentic architecture** (`src_reference/rag/v3/`: `agent.py`, `tools.py`, `scratchpad.py`, `cross_critic.py`, `hardening.py`, `injection_guard.py`, `prompts.py`). **We do not build on `rag/v2/`.**
>
> Retrieval still needs the capabilities v2 contained — hybrid search, verification, coverage, entailment, contextual summaries. Those are **re-implemented as v3 agent tools the agent calls and can re-call**, not imported as a fixed v2 pipeline that runs once before the agent sees anything. The difference is architectural, not cosmetic: in v2 the pipeline decides what the agent gets; in v3 the agent decides what it needs, observes what came back, and goes again. That is what makes "exhaustive completeness" and the sufficiency gate achievable.

## Work Items

### Retrieval as v3 tools (upgrades Sprint 2's baseline; all stages guarded, every degrade visibly noted)
- [ ] Six hybrid channels → **RRF (k=60, cap 200)**
- [ ] Deterministic query understanding: money, dates, filenames, quotes, IDs, intent, complexity, creation-date preference, boost terms
- [ ] HyDE + multi-query rewrite (fail-safe) → deterministic decomposition
- [ ] Rescoring: RRF × 120-day recency × MTI authority tiers × exact-match cap 1.5
- [ ] Diversification + temporal diversification → adaptive-K
- [ ] Voyage rerank with recursive half-split → optional premium-model reranker pass
- [ ] Full-doc / parent / neighbor expansion → interleave-for-attention → token cap trimming from the middle

### MTI Additions
- [ ] **Enumeration router**: exhaustive questions bypass similarity — complete sets from ledger/inventory fetched by ID; **answers carry their denominator**
- [ ] Automatic graph-expansion hop

### Agent v3 — the only agent architecture
- [ ] Port `src_reference/rag/v3/` as the foundation; **no v2 pipeline imports** (lint rule enforces it)
- [ ] SEED with prompt-cached scratchpad + stable [#N] indices
- [ ] PLAN / ACT / OBSERVE streaming planner (premium reasoning model)
- [ ] Sufficiency gate with recall self-check → VERIFY + retry
- [ ] Hardening flags: deal-risk skeptic critic, cross-critic, entailment/coverage/injection checks
- [ ] Budget tracker with forced finalization
- [ ] Full tool palette: all thirteen legal tools + ledger queries, `enumerate_set`, `list_tasks`, `list_commitments`, `contractor_profile`, **`check_policy` (the rulebook as a tool)**, web search — every tool honoring role-based filters
- [ ] Output: structured memo + verbatim-quoted facts + persisted streamed trace

### Trust Machinery
- [ ] Verifier **byte-for-byte** with critical-token gates
- [ ] Coverage gate: **coverage statement on every answer**

### Correction Memory
- [ ] Rakesh Sir-instant / analyst-pending hierarchy; scope-matched application; weekly consolidation

### Remember Memory (see `docs/03-CONTEXT-AND-MEMORY.md` §3)
- [ ] Remember button in every chat + natural-language trigger ("remember this: …")
- [ ] Structured notes: scope (**global | property | contractor**), author, date
- [ ] Injected into **every matching AI call** — chats, briefs, detectors, auto-analysis — visibly attributed ("per your note from Jul 20…")
- [ ] Admin page shared with corrections: view, edit, retire; global notes capped + consolidated weekly
- [ ] Rakesh Sir instant / analyst pending

### Saved Answers
- [ ] Answer Library per user + shared; full citations and verdicts preserved; searchable; property-tagged
- [ ] Staleness-aware with one-click re-run

## Gate
- [ ] Exhaustive completeness **100%** (enumeration questions)
- [ ] Verification **≥ 97%**
- [ ] recall@20 **≥ 98%**
- [ ] **Planted correction AND planted Remember note** demonstrably shape all matching answers, with attribution
- [ ] Coverage statements on **100%** of answers
- [ ] Saved answer reopens live and detects staleness

## Added Features
*(2026-08-12, admin request)*
- [ ] **Hallucination-proof Remember guarantees** (see `docs/03-CONTEXT-AND-MEMORY.md` §3.1): verbatim storage, deterministic scope matching (DB lookup, never similarity), verbatim injection as ground-truth block, **echo-check in the verifier** (note citations byte-for-byte), consolidation only with admin approval, note-vs-evidence conflicts surfaced never silently resolved — gate: a planted note can NEVER be contradicted or misquoted by any matching answer
- [ ] **AI Expert Panel machinery** (see `docs/01-AI-MODEL-STACK.md`): producer + cross-provider critic + skeptic + deterministic verifier as a reusable pipeline; panel verdicts and dissents stored; disagreement → human review with the dissent shown; wired here, consumed by Sprints 10–12 for findings, draw audits, forecasts

*(2026-08-30, admin directive)*
- [ ] **v3-only enforcement**: the v2 capabilities we still need are rebuilt as agent tools — `hybrid_search`, `verify_entailment`, `check_coverage`, `contextual_summary` — each callable and re-callable by the agent mid-reasoning; add an import guard so nothing in `mangotree/` can import a v2 pipeline module
