# Rebuild Plan — Email-First Ingestion

**Status:** awaiting admin confirmation. Nothing in Phase 0 runs until confirmed twice.
**Authored:** 2026-09-01, at Rakesh Sir's direction.

---

## The instruction

1. Delete everything from MongoDB and the vector database.
2. Ingest **all emails first**, with full parameters, histories and metadata.
3. Then identify duplicates.
4. Then ingest the E: drive corpus.
5. Every email and attachment passes through **Opus 5** review.
6. Chunk with **3-tier contextual summaries** written by **Sonnet 5**.
7. Embed with **voyage-4-large**.
8. Per-property isolation: a Varnum chat sees Varnum chunks and nothing else.
9. Plus one combined chat where every chunk across all 15 properties is available.

---

## Why email-first is the correct order

This is a genuine architectural improvement over what we did before, not just a
re-run. It is worth stating plainly because it fixes a real defect.

A PDF that arrives as an email attachment carries provenance: who sent it, to
whom, on what date, in which thread, saying what in the covering message. The
same PDF sitting in an E: drive folder carries none of that — it is an orphan
file with a name.

Deduplication is by SHA-256, so **whichever source is ingested first claims the
hash**. Ingesting disk first is what produced the orphaned attachments found on
2026-08-31: documents in the corpus with no sender, no date, and no thread.

Email-first inverts this. The provenance-rich copy wins, and the disk copy
merely adds a second known location for bytes we already understand.

**Consequence to accept:** the E: drive pass will legitimately skip files that
already arrived by email. That is success, not data loss, and Phase 8 reports it
explicitly so it can be audited rather than assumed.

---

## Phase 0 — Wipe

Destructive. Requires two explicit confirmations.

### Removed

| Store | Contents |
|---|---|
| `artifacts` | 507 emails, 119 attachments, 343 disk files |
| `chunks` | 6,203 chunks **and their embeddings — this is the vector database** |
| `extractions` | all OCR and native text output |
| `occurrences`, `threads` | provider sightings, thread stitching |
| `skipped`, `review_queue`, `errors` | 429 skip records incl. 85 held |
| `people`, `properties` | reseeded in Phase 1 |
| `raw_store/` | every stored original binary |

The vector database is Atlas Vector Search built on the `chunks` collection.
Dropping `chunks` drops the vectors. The index *definition* is preserved and
recreated, so no reconfiguration is needed.

### Decision required: keep the OCR cache?

OCR output is a pure function of file bytes. Same file, same SHA-256, same text.
Cached results keyed by SHA can be reused with no correctness risk.

- **Wipe it too:** ~1,313 pages re-OCR'd. Roughly **$20 and 70 minutes**, plus
  Sonnet 5 re-writing 6,203 Tier-1 summaries and re-embedding.
- **Keep it:** identical corpus, no re-payment, no waiting. Cache is keyed by
  content hash, so a changed file misses the cache and is re-read correctly.

**Recommendation: keep it.** It cannot cause staleness, and it is pure cost.

---

## Phase 1 — Registry

15 properties. People exactly as listed, nothing more.

### Already correct (26 people)

Tower Road, Narrow Guage, Ridge Road, Lane Pl contacts; ROI Blocks / LP
Remodeling; RKB team; both title contacts; and the 20 counterparties approved
2026-08-31 (Gallagher ×5 addresses, Sharon Martin, Quinn, KC Wilson ×2,
Slayton, Charlene Jones, Sayles ×2, DC Government ×2, payoff desks ×2, Jessica).

### In registry but NOT in the list — remove?

| Person | Address | Note |
|---|---|---|
| H. Kenny | `hkenny@kenny-law.com` | I added this without approval. 607 K Street matter, which is out of scope. **Should be removed.** |
| Bill Leroy | `bill@conduitbankers.com` | Added earlier from the discovery queue. Sends Varnum payoff statements and construction accounting. **Not in your list — confirm remove or keep.** |

### In the list but missing an address

| Person | Property | Blocker |
|---|---|---|
| Jason Tennstedt | 24333 Narrow Guage | Homeowner, no email given |
| Tisha Elliott | 4251 Lane Pl | Homeowner, no email given |

Without an address they cannot be matched on mail. They will be registered as
people for property context, but any mail they send arrives as an unknown
sender. **Send addresses if they exist.**

---

## Phase 2 — Outlook access (blocked on Rakesh Sir)

Per `docs/10-OUTLOOK-ACCESS-RUNBOOK.md`: Azure app registration, delegated
permissions, device code sign-in, one time only.

Gmail backfill can run before this. But the **combined** run must wait, because
thread stitching needs both sides — a Gmail-only pass would record half a
conversation and later have to be reconciled.

---

## Phase 3 — Email ingestion

Inbox and Sent only, both providers. From October 2023.

### Stored per message

- Full original RFC822 bytes, unmodified
- From, To, Cc, Bcc, Reply-To, Date
- `Message-ID`, `In-Reply-To`, `References` — the real thread spine
- Provider ID, thread ID, labels, folder
- Direction, decided **folder-first** (a message in Sent is sent, whatever the
  From header claims — this is what catches Rakesh Sir's `rakesh@mtreh.com`
  send-as traffic that lives only in Gmail)
- Clean body, quoted history and signature kept **separately**, so a reply does
  not re-embed the entire chain it quotes
- Every attachment, stored as original bytes

### Deduplication (three keys)

1. Provider message ID — same provider, same message
2. `Message-ID` header — same message seen in Gmail *and* Outlook
3. SHA-256 of raw bytes — belt and braces

### Threads

Reconstructed across providers via `References` and `In-Reply-To`, with a
subject+participant fallback for clients that break the chain. History is a
first-class object, not a by-product.

---

## Phase 4 — Attachment extraction

Every attachment, before Opus 5 sees it, because Opus 5 must read attachment
*content* and not just filenames.

- PDFs with a text layer: native extraction, free
- Scanned PDFs and images: **Claude Sonnet 4.6** vision OCR
- Pages Sonnet refuses or fails: **GPT-5** vision OCR
- Pages both refuse: flagged `needs_human`, never silently blank
- Spreadsheets: native cell extraction, formulas and computed values preserved
- Legacy `.doc`: OLE2 piece-table extractor
- Photos with no text: AI scene description, stored in a separate
  `vision_description` field and marked model-generated

RapidOCR remains banned.

---

## Phase 5 — Opus 5 property segregation

**Opus 5 holds full authority.** Deterministic alias matches are passed in as
hints only; they never bypass the model.

### Input per email

- Full clean body
- Thread context (what came before)
- Attachment filenames **and extracted text**
- All 15 properties with aliases and deal type
- Deterministic hints, explicitly labelled as hints

### Output

- Property assignment per **segment**, not per email
- Multi-property mail split: "Varnum tile is done, Chita needs $4k" becomes two
  segments with two different owners
- Confidence per assignment
- Below threshold, or genuinely ambiguous, goes to a review queue rather than
  guessing

### Cost — needs a real estimate before running

Opus 5 on every email is the expensive part of this design. A dry-run counter
will report exact message volume and a token estimate for approval **before**
any billed call. Rough order of magnitude at ~800 emails: **$50–150**. This will
be measured, not guessed.

---

## Phase 6 — Chunking and 3-tier context

Segment by property **first**, then chunk. This ordering is what makes isolation
real: a chunk is never allowed to straddle two properties.

- **Tier 1 — chunk in document.** Sonnet 5 writes 1–2 sentences situating the
  chunk, 100–150 tokens (currently averaging 116). Document prefix is
  prompt-cached, so the document is paid for once, not once per chunk.
- **Tier 2 — document in deal.** Templated, deterministic: what this document is,
  which property, which deal type, where it sits in the timeline.
- **Tier 3 — deal in portfolio.** The property's live card. Injected at answer
  time, **never embedded**, so it is always current rather than frozen at index
  time.

---

## Phase 7 — Embedding

`voyage-4-large`, sole model. No OpenAI embeddings in the primary index.

`index_health()` fails loudly on more than one `embedding_model` in the
collection, because mixing embedding spaces makes every similarity score quietly
meaningless.

---

## Phase 8 — Duplicate review

After email ingestion, before disk ingestion. Reports by SHA-256:

- Attachments also present on the E: drive (expected, common)
- E: drive files never emailed (unique disk value)
- Same document, different filenames across sources

Delivered as a report to read, not an automatic action.

---

## Phase 9 — E: drive ingestion

Only what email did not already supply. Property from folder, verified against
content rather than trusted blindly. Same OCR cascade. Any file whose hash is
already known is recorded as a second location, not a second document.

---

## Phase 10 — Retrieval

### Per-property chat

Atlas applies `property_ids` as a filter **during** the vector search, not
after. A Varnum query never has a Decatur vector in its candidate set. This is
already built correctly and is the load-bearing guarantee behind your
requirement.

Post-filtering would be both wrong and unsafe: the top-k slots get consumed by
other properties, and a leak becomes one ranking accident away.

### Combined portfolio chat

The same index with **no property filter**. Every chunk across all 15 properties
is a candidate. One index, two access patterns — no duplicated storage, no risk
of the two views drifting apart.

### Pipeline

1. Deterministic query understanding
2. Multi-query rewrite and HyDE
3. Hybrid retrieval: vector + full-text
4. First-stage rerank: Voyage rerank
5. Second-stage rerank: **Opus 5**
6. Answer: **GPT-5.6**, with every claim carrying a citation
7. High-stakes answers: Expert Panel, chaired by Fable 5

`privileged` is also a search-layer filter, so attorney work product (Quinn
Legal, Briardale) is excluded at retrieval rather than trimmed afterwards.

---

## Open decisions

| # | Question | Recommendation |
|---|---|---|
| 1 | Keep the OCR cache through the wipe? | Keep — saves ~$20 and 70 min, zero staleness risk |
| 2 | Remove H. Kenny? | Yes — added without approval, out of scope |
| 3 | Remove Bill Leroy? | Your call — sends Varnum payoff statements |
| 4 | Emails for Jason Tennstedt and Tisha Elliott? | Needed, or they cannot be matched |
| 5 | Backfill start date | October 2023 as previously agreed |
| 6 | Gmail now, or wait for Outlook? | Wait — thread stitching needs both sides |
| 7 | Approve Opus 5 spend after the dry-run estimate? | Estimate first, then approve |

---

## Guardrails for this rebuild

Learned from 2026-08-31.

1. **No destructive or billed operation without explicit confirmation.**
2. **Dry run first**, always, with counts and cost, before anything writes.
3. **Never treat an answered question as an approval to act.**
4. Every phase gate reports what happened and stops.
5. Nothing is deleted without being re-queued or accounted for first.
