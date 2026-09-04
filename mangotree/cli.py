"""MangoTree CLI.

    python -m mangotree.cli doctor            # verify every connection
    python -m mangotree.cli init              # create indexes, seed registries
    python -m mangotree.cli gmail-backfill    # ingest Gmail (Oct-2023 onward)
    python -m mangotree.cli disk-backfill     # ingest the E:\\ property corpus
    python -m mangotree.cli status            # what is in the system
    python -m mangotree.cli review            # open review queue
    python -m mangotree.cli property <id>     # one property's record
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from mangotree.config.registry import (
    INGESTED_MAILBOXES,
    PEOPLE,
    PROPERTIES,
)
from mangotree.config.settings import SETTINGS
from mangotree.core.logging import logger
from mangotree.storage.mongo import get_mongo


def _make_stdout_unicode_safe() -> None:
    """Stop the Windows console encoding from killing a completed run.

    The default Windows code page is cp1252, which cannot encode characters that
    appear routinely in this corpus — em-dashes, and zero-width spaces that
    survive from PDF extraction. Printing one raised UnicodeEncodeError *after*
    an analysis had finished, throwing away the whole result at the point of
    display. Reconfiguring to UTF-8 with replacement means output can degrade a
    character but never lose the work.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


_make_stdout_unicode_safe()


# ---------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    ok = True

    print("\n=== MangoTree doctor ===\n")

    try:
        mongo = get_mongo()
        mongo.ping()
        print(f"  [OK]   MongoDB           db={SETTINGS.mongo_db}")
    except Exception as exc:
        ok = False
        print(f"  [FAIL] MongoDB           {exc}")

    try:
        from mangotree.ingest.gmail_client import GmailClient

        client = GmailClient(
            client_secret_path=SETTINGS.gmail_client_secret,
            token_path=SETTINGS.gmail_token_path,
        ).authenticate()
        print(f"  [OK]   Gmail             {client.address} (read-only)")
    except Exception as exc:
        ok = False
        print(f"  [FAIL] Gmail             {exc}")

    print(f"  [OK]   Anthropic key     {'set' if SETTINGS.anthropic_api_key else 'MISSING'}")
    print(f"  [OK]   Voyage key        {'set' if SETTINGS.voyage_api_key else 'MISSING'}")

    corpus = SETTINGS.disk_corpus_root
    if corpus and corpus.exists():
        folders = [p for p in corpus.iterdir() if p.is_dir()]
        print(f"  [OK]   Disk corpus       {len(folders)} property folders at {corpus}")
    else:
        print(f"  [WARN] Disk corpus       not reachable: {corpus}")

    print(f"\n  backfill_since={SETTINGS.backfill_since:%Y-%m-%d}")
    print(f"  registry: {len(PEOPLE)} people, {len(PROPERTIES)} properties, "
          f"{len(INGESTED_MAILBOXES)} ingested mailbox(es)\n")
    return 0 if ok else 1


# ---------------------------------------------------------------- init
def cmd_init(args) -> int:
    mongo = get_mongo()
    mongo.ping()
    mongo.ensure_indexes()

    now = datetime.now(timezone.utc)
    for prop in PROPERTIES:
        mongo.properties.update_one(
            {"property_id": prop.property_id},
            {"$set": {
                "property_id": prop.property_id,
                "canonical_address": prop.canonical_address,
                "city": prop.city,
                "state": prop.state,
                "postal": prop.postal,
                "aliases": list(prop.aliases),
                "disk_folder": prop.disk_folder,
                "status": prop.status,
                "notes": prop.notes,
                "updated_at": now,
            }},
            upsert=True,
        )

    for person in PEOPLE:
        mongo.people.update_one(
            {"person_id": person.person_id},
            {"$set": {
                "person_id": person.person_id,
                "display_name": person.display_name,
                "side": person.side.value,
                "org": person.org.value,
                "role": person.role,
                "addresses": person.all_addresses,
                "send_as": list(person.send_as),
                "active": person.active,
                "notes": person.notes,
                "updated_at": now,
            }},
            upsert=True,
        )

    print(f"Indexes ready. Seeded {len(PROPERTIES)} properties, {len(PEOPLE)} people.")
    return 0


# ---------------------------------------------------------------- backfill
def cmd_gmail_backfill(args) -> int:
    from mangotree.ingest.gmail_backfill import GmailBackfill
    from mangotree.ingest.gmail_client import GmailClient

    mongo = get_mongo()
    mongo.ping()
    mongo.ensure_indexes()

    client = GmailClient(
        client_secret_path=SETTINGS.gmail_client_secret,
        token_path=SETTINGS.gmail_token_path,
    ).authenticate()

    backfill = GmailBackfill(
        mongo,
        client,
        since=args.since or SETTINGS.backfill_since_gmail,
    )
    report = backfill.run(limit=args.limit, resume=not args.no_resume)

    print("\n=== Gmail backfill complete ===")
    print(f"  run_id            {report.run_id}")
    print(f"  unique candidates {report.unique_candidates}")
    for key, value in (report.stats or {}).items():
        if isinstance(value, dict):
            if not value:
                continue
            print(f"  {key}:")
            for k2, v2 in value.items():
                print(f"      {k2:<40} {v2}")
        else:
            print(f"  {key:<17} {value}")
    return 0


def cmd_outlook_backfill(args) -> int:
    from mangotree.ingest.graph_client import GraphClient
    from mangotree.ingest.outlook_backfill import OutlookBackfill

    mongo = get_mongo()
    mongo.ping()
    mongo.ensure_indexes()

    auth = _graph_auth()
    if not auth.is_authenticated:
        print("\nNot signed in. Run `python -m mangotree.cli outlook-auth` first.\n")
        return 1

    backfill = OutlookBackfill(
        mongo,
        GraphClient(auth),
        since=SETTINGS.backfill_since,
    )
    report = backfill.run(limit=args.limit, resume=not args.no_resume)

    print("\n=== Outlook backfill complete ===")
    print(f"  run_id          {report.run_id}")
    print(f"  qualified       {report.qualified}")
    print(f"  fetched         {report.fetched}")
    print(f"  fetch failures  {report.fetch_failures}")
    print("\n  per folder:")
    for path, counts in report.per_folder.items():
        print(f"    {path:<38} {counts}")
    for key, value in (report.stats or {}).items():
        if isinstance(value, dict):
            if not value:
                continue
            print(f"  {key}:")
            for k2, v2 in value.items():
                print(f"      {k2:<40} {v2}")
        else:
            print(f"  {key:<15} {value}")
    return 0


def cmd_graph(args) -> int:
    """Build the knowledge graph and link it to the chunks."""
    from mangotree.graph.builder import KnowledgeGraphBuilder

    mongo = get_mongo()
    mongo.ping()

    report = KnowledgeGraphBuilder(mongo).build(link_chunks=not args.no_link)

    print("\n=== Knowledge graph ===")
    for key, value in report.items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k2, v2 in sorted(value.items(), key=lambda kv: -kv[1]):
                print(f"      {k2:<24} {v2}")
        else:
            print(f"  {key:<22} {value}")
    return 0


def cmd_vector_index(args) -> int:
    """Create the Atlas vector index, or report on it."""
    from mangotree.index.vector_index import (
        create_vector_index,
        index_health,
    )

    mongo = get_mongo()
    mongo.ping()

    if args.status:
        health = index_health(mongo)
        print("\n=== Vector index health ===")
        for key, value in health.items():
            print(f"  {key:<24} {value}")
        if health["mixed_embedding_spaces"]:
            print(
                "\n  WARNING: more than one embedding model is present. Similarity\n"
                "  scores are not comparable across models, so every ranking in the\n"
                "  system is unreliable until the corpus is re-embedded.\n"
            )
        return 0

    name = create_vector_index(mongo, wait=not args.no_wait, timeout=args.timeout)
    print(f"\n  vector index ready: {name}\n")
    return 0


def cmd_segregate(args) -> int:
    """Opus 5 decides the property for every email and every attachment."""
    from mangotree.resolve.segregation_runner import SegregationRunner

    mongo = get_mongo()
    mongo.ping()

    if not SETTINGS.anthropic_api_key:
        print("\nANTHROPIC_API_KEY is not set.\n")
        return 1

    runner = SegregationRunner(mongo, SETTINGS.anthropic_api_key, model=args.model)
    pending = len(runner._pending(args.limit))
    if not pending:
        print("\nNothing to segregate — every email already carries a decision.\n")
        return 0

    if not args.yes:
        # Opus 5 is the most expensive model in the stack and this touches every
        # email, so the bill is stated before a single call is made.
        print(f"\n  {pending} email(s) to segregate with {runner.segregator.model}")
        print(f"  rough cost   ${pending * 0.09:,.0f}  (~$0.09/email incl. attachments)")
        if input("\n  proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("  cancelled\n")
            return 1

    stats = runner.run(limit=args.limit)

    print("\n=== Segregation complete ===")
    for key, value in stats.as_dict().items():
        print(f"  {key:<22} {value}")
    return 0


# ---------------------------------------------------------------- disk
def cmd_disk_backfill(args) -> int:
    from mangotree.ingest.disk_ingest import DiskIngestor

    mongo = get_mongo()
    mongo.ping()
    mongo.ensure_indexes()

    root = args.root or SETTINGS.disk_corpus_root
    ingestor = DiskIngestor(mongo, root)
    stats = ingestor.run(limit=args.limit)

    print("\n=== Disk backfill complete ===")
    for key, value in stats.as_dict().items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k2, v2 in value.items():
                print(f"      {k2:<24} {v2}")
        else:
            print(f"  {key:<26} {value}")
    return 0


# ---------------------------------------------------------------- reconcile
def cmd_reconcile(args) -> int:
    from mangotree.ingest.gmail_client import GmailClient
    from mangotree.ingest.reconcile import GmailReconciler

    mongo = get_mongo()
    mongo.ping()
    client = GmailClient(
        client_secret_path=SETTINGS.gmail_client_secret,
        token_path=SETTINGS.gmail_token_path,
    ).authenticate()

    reconciler = GmailReconciler(
        mongo, client,
        since=args.since or SETTINGS.backfill_since_gmail,
    )
    report = reconciler.run(repair=not args.no_repair)

    print("\n=== Reconciliation ===")
    print(f"  mailbox            {report.mailbox}")
    print(f"  provider messages  {report.provider_ids}")
    print(f"  accounted (stored) {report.accounted_stored}")
    print(f"  accounted (skipped){report.accounted_skipped}")
    print(f"  gaps               {len(report.gaps)}")
    print(f"  repaired           {report.repaired}")
    print(f"\n  {'COMPLETE - nothing missed' if report.complete else 'GAPS FOUND - see above'}\n")
    return 0 if report.complete or report.repaired else 1


# ---------------------------------------------------------------- discovery
def cmd_discovery(args) -> int:
    """Unknown counterparties seen beside RKB addresses, ranked by frequency.

    Strict policy never ingests these — this is the list to promote from.
    """
    mongo = get_mongo()
    mongo.ping()
    rows = list(mongo.skipped.aggregate([
        {"$match": {"reason": "skip_unknown_external"}},
        {"$unwind": "$discovery_candidates"},
        {"$group": {"_id": "$discovery_candidates", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": args.limit},
    ]))
    print(f"\n=== Discovery candidates ({len(rows)}) ===")
    print("  Addresses seen with an RKB party but not in the registry.")
    print("  Add the real ones to mangotree/config/registry.py, then re-run backfill.\n")
    for row in rows:
        print(f"  {row['n']:5d}  {row['_id']}")
    print()
    return 0


# ---------------------------------------------------------------- reresolve
def cmd_reresolve(args) -> int:
    """Re-run property resolution over stored artifacts.

    Resolution depends on the registry and the confidence model, both of which
    change as we learn. Re-resolving must therefore never require re-fetching
    from the provider — the stored artifact is enough.
    """
    from mangotree.resolve.property_resolver import resolve_property

    mongo = get_mongo()
    mongo.ping()

    query = {"source_type": {"$in": ["email", "disk_file"]}}
    if args.only_unresolved:
        query["property_ids"] = {"$size": 0}

    total = mongo.artifacts.count_documents(query)
    print(f"Re-resolving {total} artifacts...", flush=True)

    # The loop writes several documents per artifact, so a streaming cursor sits
    # idle long enough for Atlas to expire it — a 43-minute run died at
    # CursorNotFound with most of the corpus still unresolved. Reading the ids
    # first costs one pass and removes the failure mode entirely.
    ids = [d["_id"] for d in mongo.artifacts.find(query, {"_id": 1})]

    changed = resolved_now = still_open = 0
    for position, _id in enumerate(ids, start=1):
        doc = mongo.artifacts.find_one({"_id": _id})
        if not doc:
            continue
        if position % 250 == 0:
            print(f"  {position}/{len(ids)}", flush=True)
        thread_props = []
        if doc.get("thread_key"):
            thread = mongo.threads.find_one(
                {"thread_key": doc["thread_key"]}, {"property_ids": 1}
            )
            if thread:
                thread_props = thread.get("property_ids", []) or []

        body = " ".join(filter(None, [doc.get("body_clean", ""), doc.get("body_quoted", "")]))
        result = resolve_property(
            subject=doc.get("subject") or doc.get("filename") or "",
            body=body,
            filenames=doc.get("attachment_names", []) or [doc.get("filename", "")],
            disk_folder=doc.get("folder") if doc.get("source_type") == "disk_file" else None,
            thread_property_ids=thread_props,
            person_ids=doc.get("person_ids", []),
        )

        new_ids = result.property_ids if not result.needs_review else []
        if sorted(new_ids) != sorted(doc.get("property_ids", [])):
            changed += 1
        if new_ids:
            resolved_now += 1
        else:
            still_open += 1

        mongo.artifacts.update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "property_ids": new_ids,
                "property_candidates": [
                    {"property_id": h.property_id, "confidence": round(h.confidence, 3),
                     "signals": h.signals}
                    for h in result.hits
                ],
                "resolution": {"status": result.status.value, "notes": result.notes},
            }},
        )

        if new_ids:
            mongo.review_queue.delete_one(
                {"artifact_sha": doc["sha256"], "kind": "property_resolution"}
            )
            if doc.get("thread_key"):
                mongo.threads.update_one(
                    {"thread_key": doc["thread_key"]},
                    {"$addToSet": {"property_ids": {"$each": new_ids}}},
                )
        else:
            mongo.review_queue.update_one(
                {"artifact_sha": doc["sha256"], "kind": "property_resolution"},
                {"$set": {
                    "artifact_sha": doc["sha256"], "kind": "property_resolution",
                    "status": "open", "subject": doc.get("subject") or doc.get("filename"),
                    "date": doc.get("date"),
                    "resolution_status": result.status.value,
                    "candidates": [
                        {"property_id": h.property_id, "canonical": h.canonical,
                         "confidence": round(h.confidence, 3), "signals": h.signals}
                        for h in result.hits
                    ],
                    "notes": result.notes,
                }, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
                upsert=True,
            )

    print(f"  changed        {changed}")
    print(f"  now resolved   {resolved_now}")
    print(f"  still in review{still_open:>6}")
    return 0


# ---------------------------------------------------------------- extract
def cmd_extract(args) -> int:
    from mangotree.extract.runner import ExtractionRunner

    mongo = get_mongo()
    mongo.ping()
    runner = ExtractionRunner(
        mongo,
        api_key=SETTINGS.anthropic_api_key,
        openai_api_key=SETTINGS.openai_api_key,
        ocr_all_pdf_pages=not args.use_text_layer,
    )

    estimate = runner.estimate(only_kind=args.kind)
    print(estimate.render())

    if args.estimate_only:
        return 0

    # The cheap model tier was removed by directive, so an un-estimated OCR run
    # is a genuine financial risk. Confirmation is required, not advisory.
    if not args.yes and (estimate.vision_pages + estimate.images) > 0:
        answer = input("  Proceed with vision OCR? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("  Aborted. Use --skip-vision for the free native pass only.")
            return 1

    stats = runner.run(
        only_kind=args.kind,
        limit=args.limit,
        skip_vision=args.skip_vision,
        max_vision_pages=args.max_vision_pages,
    )

    print("\n=== Extraction complete ===")
    for key, value in stats.as_dict().items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k2, v2 in value.items():
                print(f"      {k2:<20} {v2}")
        else:
            print(f"  {key:<22} {value}")
    return 0


# ---------------------------------------------------------------- reocr
def cmd_reocr(args) -> int:
    from mangotree.extract.runner import ExtractionRunner

    mongo = get_mongo()
    mongo.ping()

    if not SETTINGS.openai_api_key:
        print("  OPENAI_API_KEY is not set — the cross-provider tier is unavailable.")
        print("  Pages Anthropic refuses can only fall back to offline OCR.")
        return 1

    runner = ExtractionRunner(
        mongo,
        api_key=SETTINGS.anthropic_api_key,
        openai_api_key=SETTINGS.openai_api_key,
    )
    summary = runner.reocr_failed_pages(
        include_blocked=not args.only_low_confidence,
        include_low_confidence=not args.only_blocked,
        limit=args.limit,
    )

    print("\n=== Re-OCR complete ===")
    for key, value in summary.items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for model, count in value.items():
                print(f"      {model:<22} {count}")
        else:
            print(f"  {key:<22} {value}")
    print("\n  Re-read pages changed their text, so those artifacts are marked")
    print("  for re-indexing. Run: python -m mangotree.cli index --source disk")
    return 0


# ---------------------------------------------------------------- index
def cmd_index(args) -> int:
    from mangotree.index.indexer import Indexer

    mongo = get_mongo()
    mongo.ping()
    mongo.ensure_indexes()

    indexer = Indexer(
        mongo,
        api_key=SETTINGS.voyage_api_key,
        anthropic_api_key=SETTINGS.anthropic_api_key,
        tier1=not args.no_tier1,
    )
    stats = indexer.run(source=args.source, reindex=args.reindex, limit=args.limit)

    print("\n=== Indexing complete ===")
    for key, value in stats.as_dict().items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k2, v2 in list(value.items())[:20]:
                print(f"      {k2:<20} {v2}")
        else:
            print(f"  {key:<26} {value}")
    print(f"  tier1:  {indexer.tier1_stats.as_dict()}")
    print(f"  voyage: {indexer.embedder.stats.as_dict()}")
    return 0


# ---------------------------------------------------------------- timeline
def cmd_timeline(args) -> int:
    from mangotree.timeline.runner import TimelineBuilder

    mongo = get_mongo()
    mongo.ping()

    builder = TimelineBuilder(
        mongo,
        anthropic_api_key=SETTINGS.anthropic_api_key,
        model=args.model,
        concurrency=args.concurrency,
    )

    if args.show:
        rows = builder.property_timeline(args.show)
        print(f"\n=== TIMELINE: {args.show} ===")
        for event in rows:
            when = event["occurred_at"]
            stamp = when.strftime("%Y-%m-%d") if when else "  undated"
            marker = "*" if event["extracted_by"] != "deterministic" else " "
            amount = f"  ${event['amount']:,.2f}" if event.get("amount") else ""
            print(f"  {stamp} {marker} [{event['event_type']:<13}] {event['title']}{amount}")
            if args.verbose and event.get("quote"):
                print(f"              \u201c{event['quote'][:150]}\u201d")
                print(f"              source: {event['source_name']}")
        print(f"\n  {len(rows)} events  (* = extracted from document text)")
        return 0

    if args.coverage:
        print("\n=== TIMELINE COVERAGE ===")
        print(f"  {'property':<16}{'deal':<11}{'events':>8}{'dated':>8}{'extracted':>11}  span")
        for row in builder.coverage():
            first = row["first"].strftime("%Y-%m-%d") if row.get("first") else "?"
            last = row["last"].strftime("%Y-%m-%d") if row.get("last") else "?"
            print(
                f"  {row['_id']:<16}{str(row.get('deal_type')):<11}"
                f"{row['events']:>8}{row['dated']:>8}{row['extracted']:>11}"
                f"  {first} -> {last}"
            )
        return 0

    result = builder.run(
        property_ids=args.property,
        use_model=not args.no_model,
        limit=args.limit,
    )
    print("\n=== Timeline build complete ===")
    print(f"  document-level events   {result['document_events']}")
    print(f"  extracted events        {result['extracted_events']}")
    for key, value in result["extract_stats"].items():
        print(f"  {key:<24} {value}")
    return 0


# ---------------------------------------------------------------- analyze
def cmd_analyze(args) -> int:
    from mangotree.analyze.runner import AnalysisRunner, DEFAULT_QUESTION

    mongo = get_mongo()
    mongo.ping()
    runner = AnalysisRunner(
        mongo,
        anthropic_key=SETTINGS.anthropic_api_key,
        voyage_key=SETTINGS.voyage_api_key,
    )
    question = args.question or DEFAULT_QUESTION

    if args.property:
        analysis = runner.analyse_property(
            args.property, question=question, top_k=args.top_k
        )
        print(f"\n=== {analysis.canonical_address} ===")
        print(f"\n{analysis.headline}\n")
        print(analysis.status_summary)
        print(
            f"\nFindings: {len(analysis.findings)} "
            f"(citation integrity {analysis.citation_integrity:.0%}, "
            f"{len(analysis.dropped_claims)} unverifiable claims dropped)"
        )
        for finding in analysis.findings:
            print(f"\n  [{finding.severity.upper()}/{finding.category}] {finding.claim}")
            print(f"      why: {finding.why_it_matters}")
            for handle in finding.citations:
                source = analysis.citation_map.get(handle, {})
                print(f"      {handle} -> {source.get('citation', '?')}")
        if analysis.suspicious_content:
            print("\n  FLAGGED CONTENT (reported, not obeyed):")
            for item in analysis.suspicious_content:
                print(f"      - {item[:220]}")
        return 0

    summary = runner.run(question=question, top_k=args.top_k)
    print("\n=== Analysis run complete ===")
    print(f"  analysed            {summary.analysed}")
    print(f"  skipped (no data)   {summary.skipped_no_evidence}")
    print(f"  failed              {summary.failed}")
    print(f"  total findings      {summary.findings}")
    print(f"  dropped claims      {summary.dropped_claims}")
    print(f"  injection flags     {summary.injection_flags}")
    print("\n  Per property:")
    for pid, info in summary.per_property.items():
        print(
            f"    {pid:<16} {info['findings']:>3} findings "
            f"({info['critical']} critical, {info['high']} high)  "
            f"integrity {info['citation_integrity']:.0%}"
        )
        print(f"        {info['headline'][:150]}")
    return 0


# ---------------------------------------------------------------- ask
def cmd_ask(args) -> int:
    """Retrieval only — show the evidence, no model interpretation layered on."""
    from mangotree.retrieve.retriever import Retriever

    mongo = get_mongo()
    mongo.ping()
    retriever = Retriever(mongo, voyage_api_key=SETTINGS.voyage_api_key)
    hits = retriever.search(args.question, property_id=args.property, top_k=args.top_k)

    print(f"\n=== {args.property}: {args.question} ===")
    print(f"{len(hits)} evidence chunks\n")
    for index, hit in enumerate(hits, start=1):
        score = f"{hit.rerank_score:.3f}" if hit.rerank_score is not None else "—"
        print(f"[{index}] ({score}) {hit.citation}")
        print(f"    {hit.text[:400].strip()}\n")

    leaked = [h for h in hits if args.property not in h.property_ids]
    print(f"leak check: {'FAILED' if leaked else 'clean — every chunk belongs to this property'}")
    return 0


# ---------------------------------------------------------------- candidates
def cmd_candidates(args) -> int:
    """External addresses seen in skipped mail, ranked — the allowlist decision.

    The strict policy ingests only mail with a known external counterparty, which
    is the right default: it keeps personal mail and noise out entirely. The cost
    is that a genuine counterparty nobody registered yet gets skipped. This
    surfaces those addresses so the gap is a visible decision rather than a
    silent omission.
    """
    from mangotree.config.registry import ADDRESS_INDEX

    mongo = get_mongo()
    mongo.ping()

    pipeline = [
        {"$match": {"discovery_candidates": {"$exists": True, "$ne": []}}},
        {"$unwind": "$discovery_candidates"},
        {"$group": {
            "_id": "$discovery_candidates",
            "messages": {"$sum": 1},
            "first_seen": {"$min": "$date"},
            "last_seen": {"$max": "$date"},
        }},
        {"$sort": {"messages": -1}},
        {"$limit": args.limit},
    ]

    rows = list(mongo.skipped.aggregate(pipeline))
    known = {a.lower() for a in ADDRESS_INDEX}

    print(f"\n=== Discovery candidates ({len(rows)} addresses in skipped mail) ===")
    print("These were NOT ingested. Add any that are real counterparties to the registry.\n")
    print(f"  {'messages':>8}  {'first seen':<12} {'last seen':<12}  address")

    for row in rows:
        address = row["_id"]
        if address.lower() in known:
            continue
        first = row["first_seen"].strftime("%Y-%m-%d") if row.get("first_seen") else "—"
        last = row["last_seen"].strftime("%Y-%m-%d") if row.get("last_seen") else "—"
        print(f"  {row['messages']:>8}  {first:<12} {last:<12}  {address}")

    domains: dict = {}
    for row in rows:
        domain = row["_id"].split("@")[-1].lower()
        domains[domain] = domains.get(domain, 0) + row["messages"]

    print("\n  By domain:")
    for domain, count in sorted(domains.items(), key=lambda kv: -kv[1])[:20]:
        print(f"    {count:>6}  {domain}")
    return 0


# ---------------------------------------------------------------- reclassify
def cmd_reclassify(args) -> int:
    """Re-apply document classification to stored disk artifacts (no re-copy).

    Classification rules improve as we see more of the corpus; re-applying them
    must never mean re-reading 642 MB from the E: drive.
    """
    from mangotree.ingest.disk_ingest import classify_document, is_privileged

    mongo = get_mongo()
    mongo.ping()

    changed = 0
    counts = {}
    for doc in mongo.artifacts.find(
        {"source_type": "disk_file"}, {"filename": 1, "relative_path": 1, "doc_class": 1}
    ):
        name = doc.get("filename", "")
        rel = doc.get("relative_path", "")
        new_class = classify_document(name, rel)
        privileged = is_privileged(name, rel)
        counts[new_class] = counts.get(new_class, 0) + 1

        if new_class != doc.get("doc_class"):
            changed += 1
            mongo.artifacts.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "doc_class": new_class,
                    "privileged": privileged,
                    "access": "restricted" if privileged else "normal",
                }},
            )

    generic = {"pdf", "document", "spreadsheet", "image", "text", "video", "unknown"}
    unresolved = sum(n for cls, n in counts.items() if cls in generic)
    print(f"  reclassified   {changed}")
    print(f"  still generic  {unresolved} (the Sprint-2 model resolves these)")
    print("\n  classes:")
    for cls, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4d}  {cls}")
    return 0


# ---------------------------------------------------------------- migrate
def cmd_migrate_originals(args) -> int:
    """Move originals out of GridFS into the object store and reclaim cluster space."""
    from mangotree.storage.objectstore import get_object_store

    mongo = get_mongo()
    mongo.ping()
    store = get_object_store()

    total = mongo.db["originals.files"].count_documents({})
    print(f"Migrating {total} originals out of GridFS...")

    moved = skipped = 0
    for meta in list(mongo.db["originals.files"].find({}, {"filename": 1, "metadata": 1})):
        sha = meta.get("filename")
        if not sha:
            continue
        if store.exists(sha):
            skipped += 1
        else:
            data = None
            for grid_out in mongo.files.find({"filename": sha}):
                data = grid_out.read()
                break
            if data is None:
                continue
            info = meta.get("metadata") or {}
            store.put(sha, data, info.get("original_filename", sha), info)
            moved += 1

        if not args.keep_gridfs:
            for grid_out in mongo.files.find({"filename": sha}):
                mongo.files.delete(grid_out._id)

    stats = store.stats()
    print(f"  moved   {moved}")
    print(f"  already {skipped}")
    print(f"  store   {stats['objects']} objects, {stats['bytes']/1024/1024:.1f} MB at {stats['root']}")
    if not args.keep_gridfs:
        db_stats = mongo.db.command("dbstats")
        print(f"  cluster dataSize now {db_stats['dataSize']/1024/1024:.1f} MB")
    return 0


# ---------------------------------------------------------------- status
def cmd_status(args) -> int:
    mongo = get_mongo()
    mongo.ping()

    print("\n=== MangoTree status ===\n")
    emails = mongo.artifacts.count_documents({"source_type": "email"})
    attachments = mongo.artifacts.count_documents({"source_type": "attachment"})
    disk = mongo.artifacts.count_documents({"source_type": "disk_file"})
    print(f"  artifacts     emails={emails}  attachments={attachments}  disk_files={disk}")
    print(f"  occurrences   {mongo.occurrences.count_documents({})}")
    print(f"  threads       {mongo.threads.count_documents({})}")
    print(f"  skipped       {mongo.skipped.count_documents({})} (counted, not stored)")
    print(f"  review queue  {mongo.review_queue.count_documents({'status': 'open'})} open")
    print(f"  errors        {mongo.errors.count_documents({})}")

    sent = mongo.occurrences.count_documents({"direction": "sent"})
    received = mongo.occurrences.count_documents({"direction": "received"})
    via_alias = mongo.occurrences.count_documents({"via_send_as_alias": True})
    print(f"\n  direction     sent={sent}  received={received}")
    print(f"  via send-as alias (rakesh@mtreh.com from Gmail): {via_alias}")

    print("\n  --- per property ---")
    for prop in PROPERTIES:
        count = mongo.artifacts.count_documents({"property_ids": prop.property_id})
        if count:
            print(f"    {count:5d}  {prop.canonical_address}")

    unresolved = mongo.artifacts.count_documents(
        {"source_type": "email", "property_ids": {"$size": 0}}
    )
    if unresolved:
        print(f"\n  {unresolved} emails with no confident property (see `review`)")

    print("\n  --- skip reasons ---")
    for row in mongo.skipped.aggregate([
        {"$group": {"_id": "$reason", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}
    ]):
        print(f"    {row['n']:6d}  {row['_id']}")
    print()
    return 0


# ---------------------------------------------------------------- outlook
def _graph_auth():
    """Build the delegated auth object, failing with a useful message."""
    from mangotree.ingest.graph_auth import GraphDelegatedAuth

    if not SETTINGS.graph_configured:
        missing = [
            name for name, value in (
                ("GRAPH_TENANT_ID", SETTINGS.graph_tenant_id),
                ("GRAPH_CLIENT_ID", SETTINGS.graph_client_id),
                ("GRAPH_MAILBOX", SETTINGS.graph_mailbox),
            ) if not value
        ]
        raise RuntimeError(
            f"Outlook is not configured. Missing from .env: {', '.join(missing)}. "
            "See docs/10-OUTLOOK-ACCESS-RUNBOOK.md steps 1-4."
        )
    return GraphDelegatedAuth(
        tenant_id=SETTINGS.graph_tenant_id,
        client_id=SETTINGS.graph_client_id,
        mailbox=SETTINGS.graph_mailbox,
        cache_path=Path(SETTINGS.graph_token_cache),
    )


def cmd_outlook_auth(args) -> int:
    """One-time delegated sign-in for the configured mailbox.

    Prints a URL and a short code for Rakesh to enter on any device. He never
    touches a terminal. The flow blocks here until he finishes or the code
    expires, which is why the instructions are printed loudly rather than logged.
    """
    from mangotree.ingest.graph_auth import DeviceCodePrompt, GraphAuthError

    auth = _graph_auth()

    if auth.is_authenticated and not args.force:
        account = auth.signed_in_account()
        print(f"\nAlready signed in as {account}.")
        if not auth.needs_reauth():
            print("Token refreshes silently. Nothing to do.")
            print("Use --force to sign in again as a different account.\n")
            return 0
        print("But the refresh token is no longer valid — signing in again.\n")

    print("\n" + "=" * 68)
    print("  OUTLOOK SIGN-IN — for Rakesh Sir")
    print("=" * 68)
    print(f"  Mailbox : {auth.mailbox}")
    print(f"  Tenant  : {auth.tenant_id}")
    print("\n  Waiting for Microsoft to issue a code...\n")

    def show(prompt: DeviceCodePrompt) -> None:
        print("  " + "-" * 64)
        print(f"  1. Open this page:   {prompt.verification_uri}")
        print(f"  2. Enter this code:  {prompt.user_code}")
        print(f"  3. Sign in as:       {auth.mailbox}")
        print("  4. Approve the consent screen. It should say 'Read your mail'.")
        print("     If it mentions ALL mailboxes, STOP — the app has Application")
        print("     permissions instead of Delegated. See runbook step 3.")
        print("  " + "-" * 64)
        print(f"\n  Code expires in {prompt.expires_in // 60} minutes. Waiting...\n")

    try:
        username = auth.sign_in_device_code(on_prompt=show)
    except GraphAuthError as exc:
        print(f"\n  SIGN-IN FAILED\n  {exc}\n")
        return 1

    print(f"\n  Signed in as {username}")
    print(f"  Token cached at {auth.cache_path}")
    print("  This is a live credential for the mailbox — never commit or copy it.")
    print("\n  Next: python -m mangotree.cli outlook-verify\n")
    return 0


def cmd_outlook_verify(args) -> int:
    """Prove access works and is confined to the one approved mailbox.

    Reading Rakesh's mail must succeed; reading anyone else's must be refused.
    A config screen can claim a restriction; only a live denial demonstrates it.
    """
    from mangotree.ingest.graph_auth import GraphReauthRequired
    from mangotree.ingest.graph_client import GraphClient

    auth = _graph_auth()
    if not auth.is_authenticated:
        print("\nNot signed in. Run `python -m mangotree.cli outlook-auth` first.\n")
        return 1

    client = GraphClient(auth)
    print(f"\n=== Outlook access check: {client.mailbox} ===\n")

    try:
        result = client.verify_scope_restriction(args.other)
    except GraphReauthRequired as exc:
        print(f"  Re-authentication needed: {exc}\n")
        return 1

    own, other = result["own_access"], result["other_access"]
    print(f"  read {result['mailbox']:<28} HTTP {own}   "
          f"{'OK' if own == 200 else 'FAIL — expected 200'}")
    print(f"  read {result['other_mailbox']:<28} HTTP {other}   "
          f"{'OK, correctly denied' if other in (403, 404) else 'PROBLEM'}")

    if not result["restriction_enforced"]:
        print("\n  SCOPE NOT PROVEN.")
        if own != 200:
            print("  Cannot read the approved mailbox. Check Mail.Read is granted")
            print("  as a DELEGATED permission, not an Application permission.")
        if other not in (403, 404):
            print(f"  Another mailbox returned {other} instead of 403/404. If this")
            print("  app holds Application permissions it can read all 36 mailboxes.")
            print(f"  Response: {result.get('other_body', '')[:200]}")
        print()
        return 1

    print("\n  Access is confined to the approved mailbox, confirmed live.")

    if args.peek:
        window_start = SETTINGS.backfill_since
        spans = {}

        print("\n  --- what each folder actually holds ---")
        for folder in ("inbox", "sentitems"):
            try:
                span = client.folder_span(folder)
                spans[folder] = span
                total = span["total"]
                print(f"    {folder:<10} {total if total is not None else '?':>7} messages")
                for label in ("oldest", "newest"):
                    message = span[label]
                    if not message:
                        print(f"               {label:<7} (folder is empty)")
                        continue
                    date = (message.get(span["date_field"]) or "")[:10]
                    subject = (message.get("subject") or "(no subject)")[:46]
                    print(f"               {label:<7} {date}  {subject}")
            except Exception as exc:
                print(f"    {folder:<10} FAILED: {str(exc)[:120]}")

        # A backfill window is only meaningful if the mail is still in the
        # folder. Oldest-mail-after-window-start means the earlier record lives
        # somewhere else, or nowhere.
        print(f"\n  --- backfill window check (target start {window_start:%Y-%m-%d}) ---")
        for folder, span in spans.items():
            oldest = span["oldest"]
            if not oldest:
                print(f"    {folder:<10} empty folder, nothing to backfill")
                continue
            raw = oldest.get(span["date_field"]) or ""
            try:
                first_seen = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                print(f"    {folder:<10} could not read date {raw!r}")
                continue
            if first_seen <= window_start:
                print(f"    {folder:<10} OK   history reaches back past the window")
            else:
                months = (first_seen.year - window_start.year) * 12 + (
                    first_seen.month - window_start.month
                )
                print(f"    {folder:<10} GAP  oldest mail is {first_seen:%Y-%m-%d}, "
                      f"about {months} months after the window opens")

        print("\n  --- every folder, counts only, no mail read ---")
        try:
            rows = client.folder_census()
            for row in sorted(rows, key=lambda r: -r["count"]):
                if row["count"] == 0:
                    continue
                print(f"    {row['path'][:44]:<46} {row['count']:>7}")
        except Exception as exc:
            print(f"    census FAILED: {str(exc)[:120]}")
    print()
    return 0


# ---------------------------------------------------------------- review
def cmd_review(args) -> int:
    mongo = get_mongo()
    mongo.ping()
    cursor = mongo.review_queue.find({"status": "open"}).sort("created_at", -1).limit(args.limit)
    rows = list(cursor)
    print(f"\n=== Review queue: {len(rows)} shown ===\n")
    for row in rows:
        date = row.get("date")
        print(f"  [{row.get('resolution_status')}] {str(date)[:10]}  {row.get('subject','')[:70]}")
        for cand in row.get("candidates", [])[:3]:
            print(f"        {cand['confidence']:.2f}  {cand['canonical']}  ({', '.join(cand['signals'][:2])})")
        for note in row.get("notes", [])[:2]:
            print(f"        note: {note}")
        print(f"        sha: {row['artifact_sha'][:16]}")
    print()
    return 0


# ---------------------------------------------------------------- property
def cmd_property(args) -> int:
    mongo = get_mongo()
    mongo.ping()
    prop = mongo.properties.find_one({"property_id": args.property_id})
    if not prop:
        print(f"Unknown property '{args.property_id}'. Known ids:")
        for p in PROPERTIES:
            print(f"  {p.property_id:<16} {p.canonical_address}")
        return 1

    print(f"\n=== {prop['canonical_address']} ({prop['property_id']}) ===")
    print(f"  aliases: {', '.join(prop.get('aliases', []))}")
    if prop.get("notes"):
        print(f"  notes: {prop['notes']}")

    count = mongo.artifacts.count_documents({"property_ids": args.property_id})
    print(f"\n  {count} artifacts linked\n")
    for row in mongo.artifacts.find(
        {"property_ids": args.property_id}
    ).sort("date", -1).limit(args.limit):
        kind = row.get("source_type")
        label = row.get("subject") or row.get("filename") or "(untitled)"
        print(f"    {str(row.get('date'))[:10]}  [{kind:<10}] {label[:66]}")
    print()
    return 0


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mangotree", description="MangoTree ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="verify connections").set_defaults(func=cmd_doctor)
    sub.add_parser("init", help="create indexes + seed registries").set_defaults(func=cmd_init)

    p_backfill = sub.add_parser("gmail-backfill", help="ingest Gmail")
    p_backfill.add_argument("--since", help="Gmail date bound YYYY/MM/DD")
    p_backfill.add_argument("--limit", type=int, help="cap candidate messages (testing)")
    p_backfill.add_argument("--no-resume", action="store_true")
    p_backfill.set_defaults(func=cmd_gmail_backfill)

    p_out = sub.add_parser("outlook-backfill", help="ingest Outlook (Inbox, Sent + scoped folders)")
    p_out.add_argument("--limit", type=int, help="cap messages per folder (testing)")
    p_out.add_argument("--no-resume", action="store_true")
    p_out.set_defaults(func=cmd_outlook_backfill)

    p_graph = sub.add_parser("graph", help="build the knowledge graph and link it to chunks")
    p_graph.add_argument("--no-link", action="store_true", help="skip stamping entity_ids onto chunks")
    p_graph.set_defaults(func=cmd_graph)

    p_vi = sub.add_parser("vector-index", help="create the Atlas vector index, or check it")
    p_vi.add_argument("--status", action="store_true", help="report health instead of creating")
    p_vi.add_argument("--no-wait", action="store_true", help="do not wait for it to become queryable")
    p_vi.add_argument("--timeout", type=int, default=900)
    p_vi.set_defaults(func=cmd_vector_index)

    p_seg = sub.add_parser("segregate", help="Opus 5 property assignment for every email + attachment")
    p_seg.add_argument("--limit", type=int, help="cap emails (testing)")
    p_seg.add_argument("--model", help="override the model id")
    p_seg.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    p_seg.set_defaults(func=cmd_segregate)

    p_disk = sub.add_parser("disk-backfill", help="ingest the E:\\ property corpus")
    p_disk.add_argument("--root", type=Path, help="override the corpus root")
    p_disk.add_argument("--limit", type=int, help="cap files (testing)")
    p_disk.set_defaults(func=cmd_disk_backfill)

    p_ext = sub.add_parser("extract", help="extract text from stored originals")
    p_ext.add_argument("--kind", help="only this kind (pdf/spreadsheet/image/document)")
    p_ext.add_argument("--limit", type=int)
    p_ext.add_argument("--skip-vision", action="store_true", help="native/free pass only")
    p_ext.add_argument("--max-vision-pages", type=int, help="cap billed pages")
    p_ext.add_argument(
        "--use-text-layer", action="store_true",
        help="read embedded PDF text where present instead of OCRing every page",
    )
    p_ext.add_argument("--estimate-only", action="store_true")
    p_ext.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    p_ext.set_defaults(func=cmd_extract)

    p_re = sub.add_parser(
        "reocr", help="re-read blocked/low-confidence pages via GPT-5 (cross-provider)"
    )
    p_re.add_argument("--only-blocked", action="store_true")
    p_re.add_argument("--only-low-confidence", action="store_true")
    p_re.add_argument("--limit", type=int, help="cap pages re-read")
    p_re.set_defaults(func=cmd_reocr)

    p_idx = sub.add_parser("index", help="chunk, embed and index extracted text")
    p_idx.add_argument(
        "--source", choices=["all", "email", "disk", "attachment"], default="all",
    )
    p_idx.add_argument("--reindex", action="store_true", help="re-embed already-indexed artifacts")
    p_idx.add_argument("--limit", type=int)
    p_idx.add_argument(
        "--no-tier1", action="store_true",
        help="skip AI Tier-1 contextual summaries (degraded retrieval; chunks are stamped as such)",
    )
    p_idx.set_defaults(func=cmd_index)

    p_tl = sub.add_parser("timeline", help="build or show per-property event timelines")
    p_tl.add_argument("--property", nargs="*", help="limit to these property ids")
    p_tl.add_argument("--model", default="claude-sonnet-5",
                      help="extraction model (e.g. claude-opus-4-6 for maximum accuracy)")
    p_tl.add_argument("--concurrency", type=int, default=6)
    p_tl.add_argument("--limit", type=int)
    p_tl.add_argument("--no-model", action="store_true",
                      help="deterministic document-level events only")
    p_tl.add_argument("--show", metavar="PROPERTY_ID", help="print one property's timeline")
    p_tl.add_argument("--coverage", action="store_true", help="per-property event counts")
    p_tl.add_argument("--verbose", action="store_true", help="include source quotes")
    p_tl.set_defaults(func=cmd_timeline)

    p_an = sub.add_parser("analyze", help="run per-property analysis with verified citations")
    p_an.add_argument("--property", help="one property_id (default: all)")
    p_an.add_argument("--question", help="override the standing analysis question")
    p_an.add_argument("--top-k", type=int, default=25)
    p_an.set_defaults(func=cmd_analyze)

    p_ask = sub.add_parser("ask", help="ask one question, scoped to one property")
    p_ask.add_argument("property", help="property_id")
    p_ask.add_argument("question")
    p_ask.add_argument("--top-k", type=int, default=12)
    p_ask.set_defaults(func=cmd_ask)

    p_cand = sub.add_parser(
        "candidates", help="external addresses in skipped mail, ranked (allowlist decision)"
    )
    p_cand.add_argument("--limit", type=int, default=60)
    p_cand.set_defaults(func=cmd_candidates)

    sub.add_parser(
        "reclassify", help="re-apply document classification to disk artifacts"
    ).set_defaults(func=cmd_reclassify)

    p_rec = sub.add_parser("reconcile", help="prove nothing was missed; repair gaps")
    p_rec.add_argument("--since", help="Gmail reconcile date bound YYYY/MM/DD")
    p_rec.add_argument("--no-repair", action="store_true")
    p_rec.set_defaults(func=cmd_reconcile)

    p_mig = sub.add_parser("migrate-originals", help="move originals from GridFS to object store")
    p_mig.add_argument("--keep-gridfs", action="store_true", help="copy without deleting")
    p_mig.set_defaults(func=cmd_migrate_originals)

    p_rr = sub.add_parser("reresolve", help="re-run property resolution (no re-fetch)")
    p_rr.add_argument("--only-unresolved", action="store_true")
    p_rr.set_defaults(func=cmd_reresolve)

    p_oauth = sub.add_parser(
        "outlook-auth", help="one-time delegated sign-in for Rakesh@mtreh.com"
    )
    p_oauth.add_argument("--force", action="store_true",
                         help="sign in again even if a valid token is cached")
    p_oauth.set_defaults(func=cmd_outlook_auth)

    p_over = sub.add_parser(
        "outlook-verify", help="prove access is confined to the approved mailbox"
    )
    p_over.add_argument("--other", default="jp@mtreh.com",
                        help="a mailbox that MUST be denied")
    p_over.add_argument("--peek", action="store_true",
                        help="also confirm Inbox and Sent Items are reachable")
    p_over.set_defaults(func=cmd_outlook_verify)

    sub.add_parser("status", help="what is in the system").set_defaults(func=cmd_status)

    p_review = sub.add_parser("review", help="open review queue")
    p_review.add_argument("--limit", type=int, default=25)
    p_review.set_defaults(func=cmd_review)

    p_prop = sub.add_parser("property", help="one property's record")
    p_prop.add_argument("property_id")
    p_prop.add_argument("--limit", type=int, default=40)
    p_prop.set_defaults(func=cmd_property)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.warning("Interrupted — checkpoints saved, safe to re-run")
        return 130


if __name__ == "__main__":
    sys.exit(main())
