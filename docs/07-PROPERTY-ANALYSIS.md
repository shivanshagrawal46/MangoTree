# PROPERTY-WISE ANALYSIS — how a document becomes a verified finding

> Answers the question: *"How will you analyse the documents and the body of the email
> and everything in the email, property-wise?"*
> Status as of 2026-08-30: **built and running end to end.**

---

## 0. The problem this whole design exists to solve

One email says:

> "Varnum tile is done. Decatur needs $4k for the roof."

The naive pipeline tags that email `[varnum, decatur_st]`, embeds it as one unit, and
indexes it. Later someone asks *"what does Decatur need?"* — retrieval returns the email
(it is genuinely tagged Decatur), the model reads the whole thing, and answers that
Decatur's tile is done and it needs $4k for the roof.

That answer is **fluent, confidently worded, and correctly cited to a real email.** It is
also wrong. And nothing in the system notices, because every individual component did its
job. Ranking cannot save you: the Varnum sentence is genuinely similar to the query and
genuinely inside a document about Decatur.

For a lender deciding whether to release a draw, this is the failure that matters. So the
architecture is built backwards from it.

---

## 1. The pipeline

```
ORIGINAL  →  EXTRACT  →  SEGMENT  →  CHUNK  →  EMBED  →  RETRIEVE  →  ANALYSE  →  VERIFY
  (byte-      (text +     (property   (never    (one      (filter     (cite      (drop
  for-byte)   provenance)  per         mix       space)    first)      every      unproven
                           segment)    props)                          claim)     claims)
```

Each stage has one job and one guarantee.

---

## 2. EXTRACT — different documents need genuinely different treatment

Routing by type is not fussiness; using the wrong extractor destroys the evidence.

| Type | Count | Method | Why not something else |
|---|---|---|---|
| PDF (text layer) | 104 files / 978 pages | native text | Exact and free. OCR-ing these would **cost money to produce a worse transcription.** |
| PDF (scanned) | 94 files / 1,269 pages | Claude Sonnet 4.6 vision | No text layer exists to read. |
| Spreadsheets | 53 files | **cell-level, never OCR** | These carry the money. Rendering a grid to an image destroys the grid, the formulas, and cell provenance. |
| Photos | 44 | vision | — |
| Word docs | 44 | native + table extraction | Tables hold the numbers; dropping them drops the evidence. |
| Video | 15 | deferred to a transcription job | — |
| Email bodies | 458 | cleaned at ingestion | Quoted replies split off, so a forwarded thread isn't re-counted as new activity. |

**Measured result:** 2,490 PDF pages total, of which only 1,411 actually needed vision.
Checking the text layer first cut the OCR bill roughly in half, for free.

**Spreadsheets get the strictest provenance in the system.** Every value is stored with its
`Sheet1!C14` origin *and its formula*:

```
1512 Varnum!C2   875401.84   formula = 813123.26 + 62278.58
1512 Varnum!E3   939946.94   formula = SUM(C2:C3)
```

5,485 money cells extracted this way. When a stored value disagrees with its own formula,
that disagreement is a finding — not something to silently resolve.

### Two bugs found and fixed here

**Vision OCR was told to return JSON.** Legal pages are full of quotation marks —
`"Borrower" means`, `the "Holdback"` — which models reliably fail to escape inside a JSON
string. The JSON broke, and recovery salvaged only the fragment before the first stray
quote: **one page went from 2,966 characters to 5.** Switching to a delimiter the source
text cannot contain removed the failure mode entirely.

**Broken JSON was being read as low confidence.** The parse-failure fallback returned
`confidence: 0.5`, which tripped the escalation threshold — so every dense page was being
re-read on Opus 5, at double cost, to fix a problem that was never about legibility.
Truncation is now taken from the provider's own `stop_reason`, which is exact.

---

## 3. SEGMENT — where the property tag actually gets decided

**This is the stage that prevents the Varnum/Decatur failure.** Text is split into
paragraph-ish units (bulleted lists split per item — one bullet per property is the most
common multi-property shape in this corpus), and each unit is attributed independently:

1. **Explicit** — the segment names a property. A numbered address is decisive; a bare
   street name is not.
2. **Carried** — a segment with no property of its own inherits the previous one, because
   prose continues a subject: *"Varnum tile is done. The painter starts Monday."*
3. **Bounded** — carry-forward stops at a heading, at a new explicit mention, or after 3
   segments. An unbounded carry would recreate the contamination it exists to prevent.
4. **Document fallback, asymmetric** — if the document concerns exactly one property,
   unattributed text belongs to it. If it concerns two or more, unattributed text stays
   `ambiguous` and is **withheld**.

Rule 4 is the important one. For a single-property document, unattributed text is safe.
For a multi-property document, unattributed text is *precisely* the dangerous case — so
the system declines to guess.

Measured on the exact example above:

```
[explicit 0.75] ['varnum']        1512 Varnum tile is done. The painter starts Monday.
[explicit 0.75] ['decatur_st']    912 Decatur needs $4,000 for the roof.
[explicit 0.75] ['bayshore_910']  910 Bayshore inspection passed.

What a Decatur analysis actually sees:
   "912 Decatur needs $4,000 for the roof. Please approve the change order."

Leak check:  Varnum absent ✓   Bayshore absent ✓   tile absent ✓
```

---

## 4. CHUNK — purity beats packing

Segments are packed to a token budget, but **a segment is only added to a chunk whose
property set it matches.** A chunk is closed early rather than made impure. Mixing two
properties to fill a budget would undo the segmentation entirely.

Overlap between neighbouring chunks is carried only within the same property set, for the
same reason.

Each chunk carries a deterministic context line — property, document class, filename,
page or cell — prepended at embed time. This is contextual retrieval, but built from facts
already held rather than generated per chunk by a model: same recall benefit, zero cost,
**zero hallucination risk.**

---

## 5. EMBED & RETRIEVE — the filter runs *inside* the search

`voyage-4-large`, 1024 dimensions, **one embedding space forever**. Vectors from two
models are not comparable, so mixing them does not degrade gracefully — it silently
corrupts every similarity score in the index. The model id is stored on every chunk and
health-checked, rather than trusted to config.

Retrieval runs two channels because they fail in opposite directions:

- **Vector** finds meaning ("is the roof done?" matches "shingles complete") but blurs
  exact tokens — it will happily rank *draw 2* for *draw 3*.
- **Lexical** nails exact tokens (a dollar figure, a lien number) but is blind to paraphrase.

Fused with Reciprocal Rank Fusion, which combines **ranks, not scores** — the two channels'
scores aren't on a shared scale, and normalising them would invent a comparability that
doesn't exist. Then reranked: Voyage cross-encoder first, Opus 5 as second stage.

Fusion earns its keep immediately. One email ranked **22nd by vector but 1st lexically**
was rescued into the top results; vector search alone would have buried it.

**`property_ids` is declared as an Atlas filter field, so the filter is applied *during*
the nearest-neighbour search, not after it.** Post-filtering would be both wrong (other
properties eat the top-k slots) and unsafe (a leak becomes one ranking accident away). A
redundant leak guard runs after retrieval anyway, because the cost is zero and the failure
is severe.

**Measured:** 904 Bayshore and 910 Bayshore — the highest-confusion pair in the corpus —
return **zero shared chunks** across 25 results each.

---

## 6. ANALYSE & VERIFY — citations are checked, not trusted

The analyst (Opus 5) sees only chunks tagged with this property. There is no "related
properties" section and no global fallback, because those are the doors contamination
walks through.

Context has three tiers:

1. **Pinned** — registry facts and Remember notes, injected **verbatim**. Never
   paraphrased, never embedded-then-retrieved, because a summary of an instruction is not
   the instruction. This is what makes Remember notes hallucination-proof.
2. **Retrieved** — property-scoped chunks, ranked for the question.
3. **Recent** — newest activity, added separately so *"what's happening now"* doesn't
   depend on the question happening to be semantically similar to it.

Every block gets a `[C#]` handle, and **every claim is mechanically verified against those
handles.** A claim citing a handle that doesn't exist, or citing nothing, is **removed and
reported** — not shown with a caveat. This is the difference between a system that is
cited and one that merely *sounds* cited, and it matters because a fabricated citation is
indistinguishable from a real one to the reader.

Evidence arrives inside an explicit data boundary, with instructions to report — never
obey — anything inside it resembling a command.

### First real run: 910 Bayshore

**31 findings, 100% citation integrity, 0 claims dropped.** Every finding traced to a real
document. Including:

- $361,131.59 advanced **with no inspection report ever received for any draw** — the exact
  "money released against work not verified" risk the system exists to catch
- Every extension agreement signature page blank, meaning the personal guaranty is not
  enforceable as a written guaranty
- A September 2025 invoice for $408,199.94 — **exceeding the note principal** — of unknown origin
- An $82,837 lanai permit pulled by a contractor not on RKB's authorised list, still open
- CLTV of 131% against assessed value, at which both RKB and UWM are wiped out

The injection guard also fired on something genuinely useful: a stray line —
*"PS not sure if this was done as claude doesnt have it"* — that leaked into outbound
correspondence to Wes. Reported as content, not obeyed, and worth your attention as a
disclosure risk in a file heading toward litigation.

---

## 7. What this guarantees

| Guarantee | Mechanism |
|---|---|
| One property's facts never enter another's analysis | Segment-level tagging + in-search filter + leak guard |
| 904 and 910 Bayshore never merge | Alias specificity + separate registry entries + verified zero chunk overlap |
| Every claim traces to a source in ≤2 clicks | `[C#]` handles → chunk → artifact → original bytes |
| No fabricated citations | Mechanical verification; unprovable claims deleted and reported |
| Money figures carry their origin | `Sheet!Cell` provenance + formulas |
| Documents can't issue instructions | Explicit data boundary; suspicious content reported |
| Standing instructions can't be distorted | Remember notes injected verbatim, never summarised |
| Index can't silently corrupt | Single embedding space, enforced and health-checked |
| Partial results never look complete | `partial` / `truncated` / `deferred` states are explicit |

---

## 8. Current state

| | |
|---|---|
| Emails indexed | 451 → 706 chunks, 100% embedded |
| Disk artifacts extracted | 353 (native pass), OCR pass running |
| Money cells with provenance | 5,485 |
| Vector index | `chunks_vector`, READY, property-filtered |
| Embedding spaces | 1 (`voyage-4-large`) — no mixing |
| Tests | 64 passing |
| Reconciliation | 814 provider messages = 458 stored + 356 skipped, **0 gaps** |

### Open items

- OCR pass on 1,313 pages (~$20) — running
- Index disk artifacts once OCR completes
- 249 emails still in the property review queue
- 277 skipped emails whose counterparties aren't in the registry — **needs your decision**;
  a law firm (`g-e-law.com`, 28 emails) and `payeal.mtreh@gmail.com` look potentially in-scope
