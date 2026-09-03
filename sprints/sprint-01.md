# Sprint 1 — Ingestion: Outlook + Gmail (Read & Write), Photos, Guidelines

**Objective**: Every email (past and future), field photo, and guideline document flows into the system automatically, deduplicated, property-resolved, with provenance. A missed notification can never mean a missed email.

## Work Items

### Email — Outlook via Microsoft Graph
- [ ] Full per-user backfill: **all folders** (incl. Sent, Archive, Deleted, custom), **all attachments**; resumable checkpointed jobs with per-mailbox cursors (throttle-safe)
- [ ] Graph change subscriptions per mailbox, **auto-renewed** inside expiry by cron core; renewal failure = red-banner alert

### Email — Gmail
- [ ] Full backfill per mailbox, resumable
- [ ] Multi-mailbox `watch` + Pub/Sub push, auto-renewed inside 7-day expiry; `historyId` gap detection on every push

### Safety Net
- [ ] **Nightly reconciliation sweep** per mailbox: provider list diffed vs store; missing items ingested + logged as incidents
- [ ] Pipeline stage timestamps on every artifact; ingestion latency dashboard (email → indexed target < 60s)

### Cleaning Pipeline
- [ ] Mojibake repair · quoted-reply stripping (quoted thread retained separately) · signature stripping (learned per sender) · whitespace normalization · signature-logo/tracking-pixel filtering (never enter photo pipeline)

### Dedup (3-way)
- [ ] Provider-ID dedup
- [ ] **Internet-Message-ID cross-provider bridge**: one artifact, `occurrences[]` fan-out records who received what, in which folder
- [ ] Attachment binaries deduped by SHA-256

### Identity, Aliases & Direction *(added 2026-08-25 — the Rakesh send-as scenario)*
- [ ] **Identity & Alias Registry** (SOP §1b): person ↔ all addresses and send-as aliases; all attribution keys on the person, never the raw address
- [ ] **Direction attribution per occurrence**: `sent`/`received` computed from Sent-folder membership or alias-set match against the mailbox owner — never the From header alone
- [ ] **Send-as alias handling**: mail sent from Gmail as `Rakesh@mtreh.com` exists only in Gmail's Sent folder — captured from Gmail, attributed to Rakesh Sir, direction `sent`, deduped against any monitored recipient's copy
- [ ] **Cross-mailbox thread stitching test**: thread starts in Gmail (sent-as alias), reply arrives in Outlook → one thread, correct sent/received on every message
- [ ] **Business-relevance filter for personal Gmail**: only business-matching messages ingested from `rakesh.bhargava@gmail.com` (registry participants, business domains, property matches, thread continuations); personal mail skipped and only counted, borderline → review queue
- [ ] Unknown aliases discovered in traffic → review queue → learned into the registry

### Property Resolution (every email + attachment)
- [ ] Seed **property registry** with Rakesh Sir (canonical names, addresses, aliases, parcels) — *Rakesh Sir session #1a*
- [ ] Deterministic pass: address/parcel regex, subject conventions, thread inheritance, sender-context rules
- [ ] AI fallback (workhorse model) with confidence stamping
- [ ] Review queue for below-confidence; assignments feed alias learning
- [ ] **Multi-property support**: resolution returns a set; artifact fans out via `occurrences[]`; design for **chunk-level property tags** so per-property analysis uses only that property's content (implemented at chunking, Sprint 2 — schema ready now)

### Photo Intake (moved up so Sprint 11 has months of history)
- [ ] Watched Dropbox folder per crew; auto-ingest on file event
- [ ] EXIF timestamp extraction (fallback to file time, flagged); SHA-256 dedup; thumbnails
- [ ] Property auto-resolution: folder/sender identity → recent-context (crew's active property from phases + comms) → AI → review queue
- [ ] Target proven: photo → property-linked in system **< 5 min**

### Guidelines Ingestion
- [ ] Collect company guidelines, underwriting standards, SOPs from Rakesh Sir — *Rakesh Sir session #1b*
- [ ] Ingest as `company_policy` class, **versioned** (updates create new versions, old retained — "what was our rule at the time" answerable)

### Outbound (write path)
- [ ] Draft creation in the user's own mailbox
- [ ] Autonomous send **built, feature-flagged OFF**; whitelist enforced at send service; every send logged
- [ ] Sent items re-ingested through the pipeline

### Prep for Sprint 2 gates
- [ ] Start labeling a document-classification set (≥ 300 docs across the 17 classes)

## Gate
- [ ] Backfill complete for all registry mailboxes (verified vs provider counts)
- [ ] New email indexed **< 60 s** (p95, demonstrated)
- [ ] Dedup demonstrated: same email in 3 mailboxes = 1 artifact, 3 occurrences; same attachment ×5 = 1 binary
- [ ] Property resolution **≥ 90%**, all misses in the review queue (none dropped)
- [ ] A phone photo appears property-linked in the system **< 5 min** (live demo)
- [ ] Guidelines corpus confirmed complete by **Rakesh Sir**
- [ ] *(added)* **Email Reflex demo**: a critical test email produces a to-do item + cited reply draft in Drafts automatically; an informational one updates the timeline quietly — both untouched by human hands
- [ ] *(added 2026-08-25)* **Alias-sent capture demo**: Rakesh Sir sends a test email from Gmail as `Rakesh@mtreh.com` → it appears in the system as **sent by Rakesh Sir**, deduped, and its Outlook-arriving reply stitches into the same thread; a personal Gmail message is demonstrably NOT ingested

## Added Features
- [ ] *(fast-follow)* WhatsApp Business group intake — pursue only if crew won't adopt the Dropbox folder (2026-08-12, consultant review §1.3)

*(2026-08-12, admin direction — automate this FIRST)*
- [ ] **The Email Reflex, thin slice** (see `docs/00-ARCHITECTURE.md` §4.0): new email → auto-read → ingested → thread-stitched → property timeline event → criticality assessment → **critical/needs-reply emails get a to-do item + cited reply draft in the user's Drafts + notification; informational emails update timeline/to-do quietly**. Simple version now; Sprint 5's full task engine replaces the internals without changing the behavior
- [ ] **Cross-provider thread reconstruction**: every thread stitched start-to-end across Gmail + Outlook and across mailboxes (internet-message-ID + references headers) — the complete conversation for a property viewable as one thread from first email to last

---

## 2026-08-30 — Built and running

Admin directives locked this sprint's scope (see `docs/06-BUSINESS-CONTEXT-MEMORY.md`):
**Gmail first** (Outlook next) · **Inbox + Sent folders ONLY** · **strict allowlist** (RKB + a *registered* external counterparty) · **backfill from October 2023**.

### Shipped — `mangotree/`
| Module | What it does |
|---|---|
| `config/registry.py` | People, aliases, send-as, mailboxes; property registry with **canonical address + folder alias + doc aliases** |
| `config/settings.py` | `.env`-backed settings; secrets never in code |
| `storage/mongo.py` | Collections + indexes; **GridFS originals stored byte-for-byte, addressed by SHA-256** |
| `ingest/participants.py` | The ingest/skip rule — RKB + external required; internal-only RKB mail skipped; **skips counted, never stored** |
| `ingest/direction.py` | **Folder-first** sent/received + send-as alias attribution to `person_id` |
| `ingest/gmail_client.py` | Read-only Gmail; retry/backoff; **thread-local service** (httplib2 is not thread-safe) |
| `ingest/gmail_backfill.py` | Registry-driven targeted queries, concurrent fetch, resumable checkpoints, **Inbox/Sent enforced twice** |
| `ingest/mime_parser.py` | One RFC822 parser for Gmail, `.eml` and later Outlook — no divergence between sources |
| `ingest/threading.py` | Union-find thread stitching on Message-ID/References — **works across providers** |
| `ingest/pipeline.py` | The end-to-end journey; idempotent on `sha256`; occurrences fan out per mailbox |
| `ingest/disk_ingest.py` | The `E:\` corpus; folder-as-property; doc classification; privileged flagging |
| `ingest/reconcile.py` | Re-lists the provider and proves **provider set == stored ∪ skipped**; gaps repaired |
| `clean/cleaner.py` | Mojibake → HTML→text → quoted-reply split → signature strip → whitespace |
| `resolve/property_resolver.py` | Deterministic alias resolution, **multi-property fan-out**, confidence bar, review queue |
| `cli.py` | `doctor · init · gmail-backfill · disk-backfill · status · review · property` |
| `tests/` | **36 tests**, all passing |

### Verified against live data
- [x] Gmail read-only auth against `rakesh.bhargava@gmail.com` (255,707 messages total)
- [x] Discovery narrows to **814 in-scope business messages** — the strict filter does its job
- [x] **Send-as alias capture proven**: messages `From: rakesh@mtreh.com` sitting in Gmail's `SENT` are correctly attributed `direction=sent`, `via_send_as_alias=true`, `author=rakesh`. The alias query alone contributes **515** of the 814. A From-header rule would have misfiled every one of them as *received from a stranger*.
- [x] **904 vs 910 Bayshore resolve separately** at 0.93 confidence with no cross-contamination; a bare "Bayshore" is held as ambiguous rather than guessed
- [x] Attachments stored and deduped by SHA-256; signature logos and tracking pixels excluded

### Bugs found and fixed by the live run
- Subject lines were not mojibake-repaired, so damaged subjects (`Tower Road � EOD Update`) degraded a primary property signal. Subjects now go through the same repair as bodies.
- The signature stripper deleted the **entire body** of any email opening with "Thanks for the update." A sign-off is now required to *be* the line, not merely start it.
- Attachment artifacts lacked a `property_ids` array, breaking array queries.
- The Gmail service object is not thread-safe; concurrent fetching now builds one service per thread.
