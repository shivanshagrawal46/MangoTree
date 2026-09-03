# AI Model Stack — Named Assignments (locked 2026-08-12, admin-final)

Principle: **the manager manages, the analyst produces, the critic attacks, the writer renders, the workhorse grinds.** Every AI call routes to exactly one seat. Providers in the stack: **Anthropic + OpenAI + Voyage. No Gemini, no Haiku** (admin decision).

> **Verified live on our keys (2026-08-31):** `claude-fable-5` · `claude-opus-5` · `claude-sonnet-5` ·
> `claude-sonnet-4-6` · `gpt-5` (through `gpt-5.2`) · `voyage-4-large` (1024-d) · `rerank-2.5`.
> Seats are pinned in `mangotree/config/models.py`, which **refuses to route to any model whose name
> contains `gemini` or `haiku`** — the directive is enforced in code, not left to convention.

## The Frontier Three — who does what and why

Verified against third-party benchmarks (Artificial Analysis Index v4.1, CursorBench, Hebbia Finance Benchmark, CodeRabbit review lanes, n1n 18-task verifiable reasoning suite):

| Model | Seat | Why the research says so |
|---|---|---|
| **Fable 5 (high thinking)** — Anthropic, Mythos-class | **The Manager / Mastermind.** Runs the v3 agent loop (PLAN/ACT/OBSERVE), the sufficiency gate ("do I have everything?"), recall self-check, forced finalization — and **chairs the Expert Panel**: receives the producer's analysis + both critics' objections + the verifier's report and issues the final verdict. Also draw-audit verdicts and counterfactual memos | A tier **above** Opus, built for long-horizon work: "holds intent across very long sessions," "checks its own work," "kills its incorrect beliefs." **Top scorer on Hebbia's Finance Benchmark** for senior-level document/chart/table reasoning — exactly our deal-analysis workload. Leads CursorBench. At ~2× Opus 5 cost ($10/$50 per M), it manages and judges; it doesn't grind |
| **Claude Opus 5** — Anthropic | **The Deep Analyst / Producer.** Writes every deep analysis with verbatim-quoted evidence: detector judgment calls, policy-deviation reasoning, underwriting memos, forecast narratives, change-card significance, contradiction analysis. Long-context beast (1M tokens at standard pricing) for whole-thread / whole-corpus reads. **Also the second-stage reranker and the hard-page OCR escalation** (see support tiers) | Edges the field on the only same-harness composite (AA Index 61 vs 59). **Most stable logic over long 6–12 step chains** (76.3% vs 69.8%). **Highest precision in review testing** — when Opus objects, it's almost always real. Cheapest frontier output ($25/M), fastest to first token |
| **GPT-5.6 (high)** — OpenAI | **The High-Recall Critic + The Writer.** (a) The Panel's adversary: attacks every high-stakes output for missed evidence, unsupported claims, arithmetic errors, contradictions — cross-provider by design. (b) **The Answer Writer**: renders verified facts into the final structured, easy-readable memo/brief/report | **Highest recall in review testing — it finds more issues**; leads math/symbolic checking (81.4% vs 76.7%) so it owns arithmetic audit; **best strict instruction-following and structured output** — exactly the answer-writing skill. Different provider, so an Anthropic error family never grades itself |

**The trust chain on every high-stakes output:**

```
Opus 5 produces (precision, depth, verbatim quotes)
   → GPT-5.6 attacks (recall — misses nothing, checks every number)
   → deterministic verifier (byte-for-byte quotes, coverage, note echo-check)
   → Fable 5 judges (sufficiency: "is anything missing?" — final verdict, dissents attached)
   → GPT-5.6 writes the user-facing rendering from VERIFIED facts only
```

The writer only formats facts that already survived the panel — beautiful prose can never introduce an unverified claim.

## The Support Tiers (admin-final 2026-08-12)

| # | Role | Model | Fallback | Used for |
|---|------|-------|----------|----------|
| 4 | **OCR** | **Claude Sonnet 4.6** — the accuracy-critical document-extraction pick (its document understanding beats Sonnet 5's, which regressed to 67% on doc tasks); 1M context, $3/$15 | **3-tier cascade — see below.** Opus 5 vision → **GPT-5 vision (cross-provider)**. **No offline OCR** (admin directive 2026-08-31). Never a silent gap | Force-vision OCR over the corpus, per-page cascade, photo forensics reading (Sprint 11), photo-vs-invoice evidence |
| 5 | **Contextual summaries + workhorse** | **Claude Sonnet 5** — near-Opus reasoning at Sonnet pricing ($2/$10), 1M context, adaptive thinking effort levels | Sonnet 4.6, then GPT-5.6-mini-class if Anthropic is down | Tier-1 chunk context, Tier-2 templated refresh, **the rolling per-property chat contextual summary**, email summaries/intents/criticality, 17-class classification, hypothetical questions, property-resolution AI fallback, HyDE/multi-query rewrites, entity grey-zone judge |
| 6 | **Embeddings** | **Voyage `voyage-4-large` @ 1024-d — the ONLY embedding model.** We do **not** run OpenAI embeddings alongside it: embedding spaces cannot be mixed in one index. OpenAI embeddings exist solely as a documented contingency — switching would mean re-embedding the entire corpus, a deliberate migration, never a blend | (contingency only, full re-embed) | Chunk + question vectors, saved-answer search |
| 7 | **Rerank — two stages** | **Stage 1: Voyage rerank** over the RRF candidate pool (~200) — fast, cheap, recursive half-split. **Stage 2: Opus 5 listwise rerank** over the surviving top-K (~40) — the admin-directed quality pass that reads candidates like an analyst, not a similarity score | Stage 2 skippable under latency pressure (visibly flagged, per zero-silent-degradation) | Retrieval v2 rerank stage; better ordering into the agent's context |

Why two stages and not Opus-only: Opus 5 reranking all ~200 candidates on every question would add tens of seconds and real cost per query; Voyage cheaply eliminates the obvious noise so Opus 5 spends its judgment only where ranking actually decides the answer. Same quality where it matters, a fraction of the latency.

### The OCR cascade — frontier vision only (admin-final 2026-08-31)

**Only Claude vision and GPT-5 may produce text that enters the corpus.** Offline OCR is
**prohibited**: its output is layout-blind and space-mangled, so a page transcribed that way *reads* as
evidence while actually being a guess. A page no permitted engine can read is left **empty and flagged
`needs_human`** — an acknowledged gap is safe, a bad transcription is not. The rule is enforced in code by
`ALLOWED_ENGINES` in `mangotree/extract/ocr.py`, not left to convention.

**Cheapest correct path first**, and each tier exists because the one above it demonstrably fails on real pages in this corpus:

| Tier | Engine | Runs when | Measured on the 377-file corpus |
|---|---|---|---|
| 0 | **native text layer** (PyMuPDF) | the PDF has a real text layer | **1,079 of 2,490 pages.** Exact and free — OCR-ing a digital PDF costs money to produce a *worse* transcription than the text already embedded in it |
| 1 | **Claude Sonnet 4.6 vision** | no usable text layer | 1,205 pages, ~0.93 mean confidence |
| 2 | **Claude Opus 5 vision** | tier-1 confidence < 0.75 | 57 pages escalated |
| 3 | **GPT-5 vision** ← *admin directive 2026-08-31* | Anthropic **refuses** the page, or both Claude tiers still read it poorly | **51 pages recovered.** See below |
| — | *(no offline tier)* | — | A page all three tiers fail is left blank and flagged. 5 pages currently sit here, all genuinely poor scans |

**Final engine audit (2026-08-31):** 1,313 vision-read pages — `claude-sonnet-4-6` 1,205 · `claude-opus-5` 57 ·
`gpt-5` 51 · **offline 0**. The single page that had fallen back to offline OCR (page 27 of the Briardale title
search) was re-read on GPT-5 at 3,837 characters and the offline text discarded.

**Why tier 3 must be a different provider, not another Claude model.** 47 pages were refused outright with
`Output blocked by content filtering policy` — every one a **title report, title policy, or owner search**,
documents dense with personal identifiers. That refusal is a property of the *provider's policy*, not of the
page's legibility, so an Opus 5 retry returns the identical refusal. Only a non-Anthropic model can read them.
This is the first place provider diversity paid for itself in hard terms: without GPT-5, 47 pages of lien,
encumbrance, vesting and exception detail would be permanently degraded to offline-OCR text awaiting manual
typing.

**Re-OCR result (2026-08-31):** 66 pages targeted (47 blocked + 19 low-confidence) → **54 improved, 0 failed,
6 still flagged for a human.** By model: gpt-5 **50**, claude-opus-5 3, claude-sonnet-4-6 1. GPT-5 read the
blocked title-policy pages at 0.93 confidence and 3,000–5,000 characters each, versus ~2,600 characters of
space-mangled text from the offline fallback — which is the measurement that justified banning the offline
tier outright rather than keeping it as a safety net.

Two cascade bugs found and fixed by measurement, both of which had been *wasting* the expensive tiers:

1. **Output format was JSON.** Legal pages are full of quoted defined terms — `the "Holdback"`, `"Borrower"
   means` — which models fail to escape inside a JSON string. Recovery salvaged only the fragment before the
   first stray quote: **one page went from 2,966 characters to 5.** Switched to a delimited format
   (`###META` / `###TEXT`) that the source text cannot break.
2. **A broken parse was being read as low confidence.** The JSON fallback returned `confidence: 0.5`, which
   tripped the tier-2 threshold — so *every dense page* was re-read on Opus 5 at double cost for a problem
   that was never about legibility. Truncation now comes from the provider's own `stop_reason`.

Pages are read **6-way concurrently** (the run is latency-bound, not compute-bound): 20s/page → **3.1s/page**,
turning a ~7-hour corpus pass into ~70 minutes. Whole-corpus vision cost: **~$20**.

## Rules of the stack

1. **Model routing is config, not code.** Every call site declares a *seat* (`manager`, `producer`, `critic`, `writer`, `ocr`, `workhorse`, `embed`, `rerank1`, `rerank2`); one routing table maps seat → provider/model/version. Swapping is a one-line change + eval re-run.
2. **Pin versions.** Every AI output stamped `model@version + prompt_version`. Model/prompt changes ship only after the recall harness and golden set pass.
3. **Two providers live** (Anthropic + OpenAI) plus Voyage — an outage degrades speed, never correctness; deterministic fallbacks under every AI stage. Since Anthropic now carries manager, producer, OCR, and workhorse, the **GPT-5.6 fallback path for each Anthropic seat is pre-wired and tested quarterly** (outage drill).
4. **The critic is never the producer's provider.** Anthropic produces → OpenAI attacks. The routing table refuses any seat swap that puts them on one provider.
5. **One embedding space, forever-or-migration.** voyage-4-large only; any change is a full corpus re-embed behind a decision-log entry.
6. **Budget guard per lane** (OCR backfill, Tier-1 writing, detectors, agent, rerank-2). Guard trips → pause + alert; never silent truncation. Note: Sonnet 5's updated tokenizer yields ~30% more tokens for the same text — budget math uses measured per-task cost, not per-token price.
7. **Prompt caching everywhere it pays**: Tier-1 document prefix, agent SEED scratchpad, Tier-3 card, Remember-note block, rolling chat summary. With an all-Anthropic volume tier, caching is a first-class cost lever, not an optimization.
8. **Confidence stamping** on every AI output — the number that decides auto-accept vs review queue.

## Panel routing by stakes

| Stakes | Path |
|---|---|
| Routine (summaries, Tier-1 context, classification) | Sonnet 5 + confidence bar + review queue |
| Standard answers/chat | Opus 5 produces → verifier → coverage gate → GPT-5.6 renders |
| **High-stakes**: draw audits, policy-deviation findings, money extraction above threshold, severity-4+ cards, forecasts, underwriting memos | **Full trust chain** (all five links above). Any dissent → not shipped; human review queue with the dissent shown |

Panel verdicts and dissents are stored with every output; a human can always see who objected and why.

## Cost posture (revisit quarterly)

- Dropping the cheap tier (Gemini/Haiku) puts all volume on Sonnet 4.6/5 — a real cost step-up on OCR backfill and per-email processing. Mitigations already in the design: prompt caching, batch APIs where offered, the per-lane budget guards, and the pre-flight OCR cost estimate (Sprint 2) which is now **mandatory before the backfill run**.
- Fable 5 and the panel run per-decision, low volume, capped by the budget tracker — the most expensive models sit exactly where the money and the trust are.
- Rerank stage 2 (Opus 5) is per-question on ~40 candidates — bounded and worth it.
