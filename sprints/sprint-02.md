# Sprint 2 — OCR, 3-Tier Context, Full Chunk Schema, Indexing

**Objective**: The entire corpus readable, classified, chunked with context, and indexed six ways — with a recall harness proving it nightly.

## Work Items

### Force-Vision OCR (entire corpus)
- [ ] **Pre-flight cost estimate** on the corpus before the run — **mandatory** now that the cheap tier is removed (consultant review risk #4)
- [ ] Vision cascade: **Sonnet 4.6 per page → Opus 5 vision on low confidence**; per-page fallback recovery; deterministic final fallback (Tesseract/Azure DI)
- [ ] Budget guard (pauses + alerts, never truncates silently) · SHA dedup (never OCR the same binary twice) · resume-safe
- [ ] Confidence stamping per page + repair pass over low-confidence pages

### Structured Spreadsheet Extraction *(added 2026-08-30 — NOT an OCR path)*
**53 of the 377 disk files are `.xlsx`/`.xls`, and they carry the actual money**: draw schedules, budgets, payoff calculations, construction status, the Equity Rescue underwriting templates, "Accounting for RKB". Rendering these to images and OCR-ing them would destroy exactly what makes them valuable — the cell grid, the formulas, and the sheet structure.
- [ ] Native cell-level extraction (`openpyxl` for `.xlsx`, `xlrd` for legacy `.xls`), **per sheet, preserving cell coordinates**
- [ ] Capture **both the formula and the computed value** for every money cell — a payoff that is a formula is evidence of how it was derived, and the two disagreeing is itself a finding
- [ ] Header/merged-cell aware row reconstruction so a draw-schedule line survives as a line, not as loose tokens
- [ ] Provenance to `sheet!cell` so every extracted number cites its exact origin — the ledger's evidence standard applies here most of all
- [ ] Multi-sheet workbooks fan out per sheet; hidden sheets flagged, never silently read or silently dropped

### Video Ingestion *(added 2026-08-30)*
**15 `.mp4` files exist** (site walkthroughs, "Charlene Jones Video", "Wes Photo" folders) and no sprint covered them.
- [ ] Store originals; large files stay in object storage and are referenced, never inlined
- [ ] Audio transcription with timestamps → transcript chunks join the same retrieval index
- [ ] Keyframe sampling at interval + scene change → frames enter the **photo/vision pipeline**, so a walkthrough video becomes searchable site evidence for draw verification
- [ ] Frames and transcript segments carry the video's property link and their offset, so a claim cites "walkthrough at 04:12"

### Classification
- [ ] **Lender-specific document classes** (replaces the generic 17-class list — see `docs/06` §5): deed of trust · assignment & allonge · ALTA/title policy · title report/owner search · lien package · draw schedule · budget · construction status · inspection report · payoff · modification · extension · change order · investor package · underwriting · operating agreement · wire instructions/buy direction · closing letter · contract/scope of work · daily log · legal demand · CMA · vendor document · photo · video · email
- [ ] Filename and folder rules run **before** the model (these documents are named with remarkable discipline — `classify_document()` in `mangotree/ingest/disk_ingest.py` already encodes the observed patterns); the model resolves only what rules cannot
- [ ] Confidence-stamped, review queue below bar
- [ ] Evaluated against the labeled set built in Sprint 1

### Privileged & Legal Material *(added 2026-08-30)*
The corpus contains attorney work product (Quinn Legal cover memos, an "Evidence Pack", files explicitly marked *privileged*, demand letters). Treating these like ordinary documents risks leaking privileged content into a routine answer.
- [ ] `privileged` flag + `access: restricted` set at ingest from folder/filename markers (already emitted by the disk ingestor)
- [ ] Restricted artifacts **excluded from general retrieval by default**; reachable only in an explicitly privileged context by an authorized user
- [ ] Any answer that draws on restricted material is labeled as such, and the exclusion is visible ("3 restricted documents were not used") — never a silent omission

### Chunking
- [ ] Locked at **1000/200**, paragraph-first; email headers included; page-aware attachments
- [ ] **Chunk-level `property_ids[]`** from per-segment resolution (multi-property emails: each chunk tagged only with the properties its content concerns) — adversarial leak test *now*, not Sprint 8

### v2 Chunk Schema — every field
- [ ] `source_type`, `sha256`, chunk positions, embedded text (**Tier-1 + Tier-2 + header + chunk**), raw body for highlighting, token counts, **1024-d embeddings**, page spans, full `occurrences[]` fan-out with earliest-primary mirror fields, `latest_date`, `property_ids[]`, `contractor_ids[]`, `doc_class`, `tier2_version`

### 3-Tier Context
- [ ] Tier 1: AI-written chunk-in-document (prompt-cached document prefix)
- [ ] Tier 2: templated document-in-deal; **incremental refresh only for changed deals** (`tier2_version` marks staleness)
- [ ] Tier 3: deal-in-portfolio card, injected at answer time, **never embedded**

### Indexes
- [ ] Multi-representation: **hypothetical questions per chunk** (sixth retrieval channel)
- [ ] Vector index with filter paths · weighted BM25 · question-vector index · full b-tree set incl. occurrence fan-out
- [ ] Decision gate: Postgres FTS quality sufficient, or adopt Typesense sidecar (record in tracker Decision Log)

### Baseline Retrieval *(added — consultant review §1.4)*
- [ ] Vector + BM25 + question-vector → RRF → property/class filters → top-K (full v2 stack lands Sprint 6)

### Photo Cataloging Pass
- [ ] Room/area guess, work-stage guess, timestamp per photo → searchable **photo timeline per property** (full forensics Sprint 11)

### Recall Harness
- [ ] Synthetic Q&A per document; nightly sampling; **recall per doc-class on a permanent dashboard tile**

### Prep for Sprint 3 gates
- [ ] Pick the 10 properties for line-by-line reconciliation; book Rakesh Sir's rulebook half-day and reconciliation session

## Gate
- [ ] Corpus **100% OCR'd** (every page confidence-stamped or repaired)
- [ ] Classification **≥ 95%** on the labeled set
- [ ] recall@20 **≥ 95%**, **no class below 90%**
- [ ] Tier-2 refresh proven incremental (change one deal → only its chunks re-versioned)
- [ ] Photo timeline browsable per property
- [ ] *(added)* Chunk-level property tagging passes adversarial multi-property leak test
- [ ] *(added 2026-08-30)* Every draw-schedule and payoff spreadsheet extracts to line items whose totals **reconcile to the workbook's own totals**; each cited to `sheet!cell`
- [ ] *(added 2026-08-30)* All 15 videos transcribed and keyframed, frames searchable and property-linked
- [ ] *(added 2026-08-30)* Privileged-material leak test: a general question that would be answered by restricted content returns the answer **without** it and says so

## Added Features
