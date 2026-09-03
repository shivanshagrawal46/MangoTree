# Ingestion Spec — authoritative

Admin directive, 2026-09-02. This supersedes any conflicting statement in the
sprint files or `11-REBUILD-PLAN.md`. Where this document and the code disagree,
this document is right and the code is a bug.

## Frozen scope

Measured 2026-09-02, read-only, before any ingestion:

| | |
|---|---|
| Window | 2023-10-01 → today |
| Sources | Gmail (Inbox, Sent) + Outlook (Inbox, Sent Items, Briardale Tampa, 3 Sent subfolders) |
| **Qualifying messages** | **3,417 unique** |
| **With attachments** | **1,083 unique** |
| Excluded — RKB-internal only | 5,903 |
| Excluded — no registry contact, no property in subject | 37,741 |
| Excluded — Deleted Items / Junk / Outbox | 12,220 |

### The rule, in order — first match wins

1. every address is RKB → **excluded**, internal mail
2. otherwise any **external** address present → **qualifies**
3. otherwise the subject names one of the 15 → **qualifies**
4. otherwise → **excluded**

Implemented in `ingest/participants.decide`, which is the only place the
decision is made. It replaced an earlier rule that also demanded a visible RKB
address on every message; that requirement discarded mail where Rakesh Sir was
Bcc'd, since Bcc is stripped from the recipient's copy. `tests/test_rule_agreement.py`
runs this rule and the counting script's rule over the same inputs and fails if
they ever disagree, because a corpus that comes out short looks exactly like a
corpus that is correct.

Explicitly ruled out by the admin (2026-09-02) and **not** to be revisited
without instruction:

- adding unknown domains to the registry (`westermanllp.com` and the rest)
- the 141 send-as messages to unregistered people
- the 5,903 internal messages
- daily/live ingestion — this is a one-time backfill; delta sync comes later

## The twelve requirements

1. **OCR** — every PDF through Claude vision OCR first. GPT-5 OCR only as
   fallback for pages Claude fails or refuses. Nothing else. No RapidOCR.
2. **Storage** — every email and attachment stored with full metadata in
   MongoDB; entity linkage carried into the vector database.
3. **Property assignment** — Opus 5 analyses *every* email and *every*
   attachment to decide which property it belongs to.
4. **Unresolved** — when Opus 5 cannot resolve, the item goes to the human
   review list **and** is attached to whichever property is named in the subject
   or body. Both, not either.
5. **Routing** — content about the 15 properties goes to that property. Content
   about anything else goes to the common all-properties store.
6. **Chat isolation** — a per-property chat sees that property and nothing else.
   The global chat sees everything, including non-registered properties.
7. **Originals** — PDFs and Excels kept as-is per property. DigitalOcean is on
   the table; space can be bought if needed.
8. **Native extraction** — Excel and Word files use the native extractor, not OCR.
9. **Chunking** — 1000/200 with the 3-tier contextual summary. Sonnet 5 writes
   the summaries.
10. **Embeddings** — `voyage-4-large`, sole model.
11. **Vector data** — connected to full metadata, entity linkage and a knowledge
    graph.
12. **Deduplication** — SHA-256 on every file. No duplicates.

## Current state against each requirement

| # | Requirement | Code today |
|---|---|---|
| 1 | Claude → GPT-5 OCR cascade | built (`extract/ocr.py`, `extract/openai_ocr.py`) |
| 2 | Mongo + metadata | built; entity linkage **missing** |
| 3 | Opus 5 per email/attachment | **not built** |
| 4 | Review list + subject/body fallback | partially — review queue exists |
| 5 | 15 properties vs common store | routing **not built** |
| 6 | Chat isolation | filter field exists on the vector index |
| 7 | Originals per property | local object store; S3 class stubbed, DO **not built** |
| 8 | Native Excel/Word extraction | built (`extract/spreadsheet.py`, `legacy_doc.py`) |
| 9 | 1000/200 chunking | **differs** — 1800 chars, no fixed overlap |
| 10 | voyage-4-large | built (`embed/embedder.py`) |
| 11 | Knowledge graph | **not built** |
| 12 | SHA-256 dedup | built (`core/hashing.py`) |

## Decisions taken (admin, 2026-09-02)

1. **Chunking is 1000 tokens with 200 tokens of overlap** — tokens, not
   characters.
2. **Property boundaries are respected.** Segment by property first, then apply
   the 1000/200 window inside each part. No chunk may ever contain two
   properties, because a chunk that does will leak 904 Bayshore into a 910
   Bayshore answer and there is no way to detect that after the fact.
3. **One Opus 5 call per email**, carrying the body, the thread context and the
   text of every attachment. The call returns a separate property decision for
   the email and for each attachment, so requirement 3 is satisfied without
   paying for an isolated call per file — and the model sees the covering email,
   which is usually the only thing that says which property an invoice is for.
4. **Originals stay on local disk** for this backfill. `S3ObjectStore` remains
   the migration path to DigitalOcean; moving is a config change, not a
   re-ingest.
5. **The knowledge graph is built during this ingestion run**, not deferred.

## Immediate task

Ingest emails and attachments into MongoDB and the vector database. Nothing
else. Full monitoring throughout.
