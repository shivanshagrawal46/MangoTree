# Ingestion Pipeline — Standard Operating Procedure (Fully Automated)

This is the authoritative SOP for how anything enters MangoTree. **No manual step exists in the steady state** — humans only appear in review queues.

---

## 1. Mailbox Registry (authoritative)

All read/send access is restricted by application access policy to exactly these mailboxes. Adding a mailbox = registry entry + access-policy update + backfill job; nothing else.

### LP Remodeling — Listing Prophet LLC dba Listing Profit LLC
| Mailbox | Person | Role | Status |
|---|---|---|---|
| `wes@lpremodel.com` | Wes | Owner/Manager — **also treated as a contractor under ROI Blocks LLC** | Active |
| `Kelly@lpremodel.com` | Kelly (Wes's brother) | Construction | Active |
| `alicia@lpremodel.com` | Alicia | Transaction Coordinator / Bookkeeper | **Departed — backfill history, no live watch needed once confirmed inactive; keep in provenance** |

### ROI Blocks LLC
| Mailbox | Person | Role | Status |
|---|---|---|---|
| `wes@roiblocks.com` | Wes | Owner/Manager | Active |
| `Panos@roiblocks.com` | Panos | Marketing & Growth Development | Active |

### MangoTree (our side) *(added 2026-08-25)*
| Mailbox | Person | Role | Status |
|---|---|---|---|
| `Rakesh@mtreh.com` | Rakesh Sir | Principal / CEO — Outlook (Microsoft 365) | Active |
| `rakesh.bhargava@gmail.com` | Rakesh Sir | Principal / CEO — personal Gmail; **sends business mail from here using the `Rakesh@mtreh.com` "Send mail as" alias via Gmail's dropdown** | Active |

> ⚠ **Critical behavior**: when Rakesh Sir sends from Gmail as `Rakesh@mtreh.com`, that sent message exists **only in Gmail's Sent folder — Outlook never sees it**. Both mailboxes must be fully ingested (all folders, sent and received) or his outbound mail silently disappears from the record. Replies to those messages arrive in **Outlook** (they go to `Rakesh@mtreh.com`), so a single thread routinely spans both providers.
>
> **Capture mechanics** *(2026-08-25)*: Gmail's "Send mail as" only rewrites the From header — the message is composed, transmitted, and stored entirely inside the `rakesh.bhargava@gmail.com` account, filed under its `SENT` label. **It is captured via the Gmail API** (backfill + watch push + nightly sweep on that account), which returns every message in the account regardless of the From address used. **The Graph/Outlook API can never return it** — the message never touches Microsoft's servers. Direction is decided folder-first (`SENT` label → `sent`), and the alias registry attributes it to the person Rakesh Sir; the From header alone decides nothing. If his send-as is ever configured with an external SMTP route (making a copy appear in Outlook too), the internet-message-ID dedup collapses both copies into one artifact — correct in either configuration, no need to know which is in use.

> Note: `wes@lpremodel.com` and `Kelly@lpremodel.com` appear under both entities. They are **one mailbox each** in ingestion (deduped), with entity edges in the knowledge graph linking them to both LP Remodeling and ROI Blocks roles. Wes-as-LP-Remodeling is modeled as a *contractor entity* under ROI Blocks per company instruction.

## 1b. Identity & Alias Registry *(added 2026-08-25)*

Mailboxes are not people. A separate identity registry maps **person → all their addresses and send-as aliases**:

| Person | Addresses / aliases |
|---|---|
| Rakesh Sir | `Rakesh@mtreh.com` (Outlook mailbox **and** Gmail send-as alias), `rakesh.bhargava@gmail.com` |
| Wes | `wes@roiblocks.com`, `wes@lpremodel.com` |
| Kelly | `Kelly@lpremodel.com` |
| Panos | `Panos@roiblocks.com` |
| Alicia | `alicia@lpremodel.com` (departed) |

Rules:
1. All attribution (who said what, who promised what, whose voice for drafts, whose to-do) keys on the **person**, never the raw address.
2. **Direction is determined per occurrence, per mailbox** — never from the From header alone: a message occurrence is `sent` if it sits in that mailbox's Sent folder **or** its From matches one of the mailbox owner's registered aliases; otherwise `received`. So the same artifact can correctly be `sent` in Rakesh Sir's Gmail and `received` in Wes's inbox.
3. New aliases discovered in traffic (a From address that replies land on, an unknown send-as) go to the review queue and, once confirmed, join the registry as learned aliases.
4. **Business-relevance filter for personal Gmail**: `rakesh.bhargava@gmail.com` also carries personal mail. Only messages matching business rules (participant in the identity/contact registry, known business domains, property-registry match, or thread continuation of an ingested thread) are ingested; everything else is skipped and only counted (never stored), with borderline cases in a review queue. Personal mail never enters the system.

## 2. Email Ingestion

### 2.1 Connect
- **Outlook / Microsoft Graph**: app registration, application permissions scoped by access policy to the registry mailboxes only. Full per-user backfill: **all folders** (Inbox, Sent, Archive, custom, Deleted), **all attachments**.
- **Gmail**: OAuth per mailbox, multi-mailbox `watch` (Pub/Sub push). Full backfill via history/list.

### 2.2 Real-time + safety net (a missed notification can never mean a missed email)
- Graph change subscriptions, **auto-renewed well inside their ~3-day expiry** by a scheduled job; renewal failure = red-banner alert.
- Gmail `watch` **auto-renewed inside its 7-day expiry**; `historyId` gap detection on every push.
- **Nightly reconciliation sweep** per mailbox: provider message list diffed against our store; anything missing is ingested and the miss is logged as an incident metric.

### 2.3 Clean
Order matters; each step produces a derived text, original untouched:
1. Mojibake repair (encoding detection + fix, e.g. ftfy-class repair)
2. Quoted-reply stripping (keep the new content; quoted thread retained separately for context)
3. Signature stripping (learned per-sender signature blocks)
4. Signature-logo / tracking-pixel image filtering (tiny images, known logo SHAs — never enter the photo pipeline)
5. Whitespace normalization

### 2.4 Dedup (3-way) + direction attribution
1. **Provider ID** — same message re-delivered by same provider → skip.
2. **Internet-Message-ID bridge** — the same email seen in Outlook and Gmail (or in 3 recipients' mailboxes) is **one artifact** with multiple `occurrences[]` rows recording exactly who received it, in which folder — provenance never lost.
3. **Content SHA-256** — attachment binaries deduped by SHA; identical PDF sent five times is stored once, occurs five times.

Every `occurrences[]` row also carries **direction** (`sent` | `received`), computed per SOP §1b rule 2 (Sent-folder membership or alias-set match against the mailbox owner — never the From header alone). The alias-sent case is the canonical test: Rakesh Sir sends from Gmail as `Rakesh@mtreh.com` → one artifact, direction `sent` in his Gmail occurrence, `received` in any monitored recipient's occurrence, attributed to the person Rakesh Sir, thread-stitched with the Outlook-arriving replies.

### 2.5 Property resolution (every email, every attachment)
1. **Deterministic**: address/parcel regexes against the property registry, known subject-line conventions, thread inheritance (replies inherit the thread's resolution unless contradicted), sender-context rules.
2. **AI fallback** (workhorse model): full email + attachment names + Tier-3 portfolio card → property guess(es) with confidence.
3. **Below confidence → review queue.** One click assigns; the assignment becomes a learned rule where a pattern exists (alias learning).

**Multi-property emails**: resolution returns a *set*. The artifact fans out to every resolved property via `occurrences[]` and appears in each property's workspace/timeline. **Chunk-level tagging**: during chunking, each chunk carries only the `property_ids[]` its content actually concerns (per-segment resolution), so *analysis for property A never uses property B's lines from the same email*.

**Fact-level assignment (the analysis answer)** *(added 2026-08-25)*: chunk tags handle retrieval isolation; extraction goes one level finer. Every extracted fact — an invoice line, a dollar claim, a commitment, a deadline — is assigned to **exactly one property**, inherited from the segment it was extracted from. A sentence like "Maple St tile is done, and Oak Ave needs $4k more for the roof" produces two facts on two properties, each citing only its own words. Facts whose property cannot be determined from their segment (e.g. "the total for both jobs is $12k" with no split given) are **never divided by guess** — they go to the verification queue, and the timeline shows them as pending-assignment. General/greeting segments tag to all resolved properties for retrieval but produce no property-assigned facts.

### 2.6 Outbound (write path)
- Drafts created **in the user's own mailbox** (their voice, cited).
- Autonomous send: **built, feature-flagged OFF**. Whitelist enforced at the send service. Every send logged. Sent items re-ingested through the same pipeline (so the record is complete even for human-sent mail).

## 3. Photo Intake (field channel)

Design goal: **dead simple for the crew — zero new apps.**

- **Primary: watched Dropbox folder per crew** (simplest, most reliable API). **Secondary: shared WhatsApp group** via WhatsApp Business API (higher setup friction — see consultant review; ship Dropbox first, WhatsApp fast-follow).
- Auto-ingest on file event: EXIF timestamp extracted (fallback: file/message time, flagged), SHA-256 dedup, thumbnail derivatives.
- **Property auto-resolution**: sender/folder identity → recent-context (what property is this crew on this week, from ledger phases + recent comms) → AI vision guess → review queue for ambiguity.
- Target: **a phone photo appears property-linked in the system < 5 minutes.**
- Sprint 2 adds cataloging (room/area, work stage, timestamp timeline); Sprint 11 adds full forensics. Photos accumulate property-linked from week 3 so forensics has months of history.

## 4. Call Recordings (Sprint 7)

- Granola connector: webhook primary, polling fallback, **idempotent by meeting ID**. Zoom through the same pipeline.
- Property segmentation of every call; segments property-tagged; low confidence → review; leakage structurally impossible (chunk-level property_ids, same rule as email).

## 5. Guidelines / Company Policy

- Collected from Rakesh Sir; ingested as the `company_policy` doc class.
- **Versioned**: a policy update creates a new version; old versions retained — "what was our rule at the time" stays answerable (bitemporal join against events).
- Sprint 3 decomposes these into the checkable Policy Rulebook.

## 6. Pipeline SLAs & Instrumentation

| Stage | SLA | Alarm |
|---|---|---|
| Email arrival → indexed & searchable | **< 60 s** | p95 breach → red banner |
| Photo → property-linked in system | **< 5 min** | same |
| Call end → analyzed | **< 10 min** | same |
| Nightly reconciliation | 0 missing artifacts | any miss logged as incident |
| Subscription/watch renewals | never expired | expiry-minus-24h warning |

Every artifact carries pipeline stage timestamps; the ingestion dashboard shows per-stage latency and queue depth. Resume-safe: any stage can be killed and re-run without duplicates (idempotency keys = SHA + stage).

---

## 7. Implementation as built *(2026-08-30 — `mangotree/`)*

### 7.0 Scope, locked by admin directive
**Gmail first** (Outlook next) · **Inbox + Sent folders ONLY** · **strict allowlist** · **from October 2023**.
`ARCHIVE`, `TRASH`, `SPAM` and `DRAFTS` are out of scope. The record of record is what was actually received and what was actually sent.

### 7.1 The ingest decision — one rule, not a pile of heuristics
Ingest **iff at least one RKB address AND at least one *registered* external address** appear across From/To/Cc/Bcc (`ingest/participants.py`).

This single rule does three jobs at once, which is why it is stated once rather than as three filters:
- **Internal-only RKB mail is skipped** — Rakesh Sir → JP Sir / Manjunath Sir / Shivansh with no outside party is an internal discussion, not deal evidence.
- **Personal mail is skipped with no special-casing** — of the 255,707 messages in Rakesh Sir's Gmail, personal mail simply has no RKB business counterparty, so it never qualifies. No personal-content heuristic is needed, and none exists.
- **Unlisted `@mtreh.com` staff are still recognised as us** via a domain rule, so a new hire never looks like an external counterparty.

Live result: **255,707 messages → 814 in scope.**

### 7.2 Skipped ≠ missed
A skipped message records **provider id + reason only** in `skipped` — never subject, never body. That is what makes the completeness proof possible while guaranteeing personal mail leaves no content in the system.

### 7.3 Unknown counterparties become discovery candidates
Under `strict`, an unregistered external address does not silently enter the corpus **and does not silently vanish**: it is counted as a discovery candidate for deliberate promotion into the registry. Misses are made visible rather than assumed away.

### 7.4 Direction — folder-first (`ingest/direction.py`)
1. Provider sent marker (Gmail `SENT`) ⇒ `sent`. The provider writes that label as part of the send operation, so membership is sufficient evidence on its own.
2. Else From resolves to the **mailbox owner's person** via the alias registry ⇒ `sent` (archived sent mail).
3. Else ⇒ `received`.

Direction lives on the **occurrence**, never the artifact: one email is legitimately `sent` in Rakesh Sir's Gmail and `received` in a counterparty mailbox.

**Why this ordering matters.** Rakesh Sir composes in Gmail and sends under the `rakesh@mtreh.com` alias. A From-header rule calls that *received from a stranger* — a corrupted record, which is worse than a missing one. **515 of the 814 in-scope messages come from the alias query**, so this is the common case, not an edge case.

### 7.5 Property resolution (`resolve/property_resolver.py`)
Signals, strongest first: disk folder → subject alias → filename alias → body alias → thread inheritance → deal-contact hint. Confidence is `signal_weight × alias_specificity`, and **specificity matters more than location**: `"910 Bayshore"` in a body outranks `"Bayshore"` in a subject, because a street number names exactly one loan while a street name may name several.

- Multi-property emails **fan out to every property that clears the bar**.
- Below the bar ⇒ **review queue**, never a guess.
- **904 vs 910 Bayshore** are separate registry entries and resolve independently; a bare `"Bayshore"` is held as ambiguous. This is the standing regression test.
- Folder names omit street numbers (`9th St NW` is really **3731** 9th St), so the registry carries canonical address + folder alias + document aliases.

### 7.6 Storage split
Originals go to a **content-addressed object store** (`storage/objectstore.py`, local now, S3 behind the same interface later); MongoDB keeps the document layer and the pointer. GridFS was the first cut and cost **175 MB of cluster storage for ~370 emails** — the wrong home for 684 MB of PDFs and video.

### 7.7 Completeness proof (`ingest/reconcile.py`)
Re-list the provider (ids only, cheap) and assert **provider set == stored ∪ skipped**. Anything in neither is a **gap** — a message we never made a decision about. Gaps are reported with their ids and repaired in the same pass. Zero gaps is the proof; non-zero is an alarm, never a silent degradation.

### 7.8 Re-runnability
Resolution depends on the registry and the confidence model, both of which change as we learn. `reresolve` re-runs it over stored artifacts **without re-fetching** — adding a property or an alias never means re-downloading a mailbox.

### 7.9 CLI
`doctor` · `init` · `gmail-backfill` · `disk-backfill` · `migrate-originals` · `reresolve` · `reconcile` · `status` · `review` · `property`
