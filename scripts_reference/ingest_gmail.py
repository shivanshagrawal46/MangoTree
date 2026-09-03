"""Phase 4 Sprint 1 — pull email from Gmail (read-only) into the corpus.

Subcommands
-----------
  profile                 Show the authenticated mailbox + total message count
                          (sanity check that OAuth works and we're on the right
                          account).

  labels                  List every Gmail label ("folder") with message counts,
                          so you can give the EXACT folder names to pull.

  pull                    Pull messages for one or more labels + an optional date
                          range, running each through the full idempotent
                          ingestion (3-way dedup). Dry-run by default.

Examples
--------
  # 1) confirm the account
  python -m scripts.ingest_gmail profile

  # 2) see the folder names
  python -m scripts.ingest_gmail labels

  # 3) dry-run a label (counts only, no writes)
  python -m scripts.ingest_gmail pull --label "Boris_lawsuit" --dry-run

  # 4) pull the May-26 -> present gap, live, as adverse-party (David) corpus
  python -m scripts.ingest_gmail pull --label "AA_Fund" --after 2026-05-26 \
      --corpus fraud_communications --privilege adverse_party --live

Auth: needs an OAuth client-secret JSON (Desktop app) from Google Cloud Console.
Point GMAIL_CLIENT_SECRET at it (default 'client_secret.json'); the token is
cached at GMAIL_TOKEN_PATH (default 'gmail_token.json') after the one-time
consent. Scope is gmail.readonly — this cannot modify the mailbox.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.db.repository import EmailRepository
from src.ingest.gmail_client import GmailClient
from src.ingest.gmail_ingest import ingest_one_email, OUT_INSERTED
from src.rag import evidence_schema as ev
from src.utils.logger import logger


def _parse_day(s: str | None):
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def cmd_profile(client: GmailClient) -> int:
    p = client.get_profile()
    print(f"\nAuthenticated mailbox : {p.get('emailAddress')}")
    print(f"Total messages        : {p.get('messagesTotal'):,}")
    print(f"Total threads         : {p.get('threadsTotal'):,}")
    return 0


def cmd_labels(client: GmailClient) -> int:
    labels = client.list_labels()
    print(f"\n{'NAME':<40} {'TYPE':<8} {'MESSAGES':>10}")
    print("-" * 60)
    for lb in labels:
        print(f"{(lb['name'] or ''):<40} {(lb['type'] or ''):<8} "
              f"{(lb['messagesTotal'] or 0):>10,}")
    print(f"\n{len(labels)} labels. Use the NAME(s) above with `pull --label`.")
    return 0


def cmd_pull(client: GmailClient, args) -> int:
    after, before = _parse_day(args.after), _parse_day(args.before)
    label_names = args.label or []

    if args.ids_csv:
        # Targeted mode: ingest exactly the gmail_ids listed in an audit CSV
        # (the precise MISSING set), instead of re-scanning the whole label.
        import csv as _csv
        with open(args.ids_csv, newline="", encoding="utf-8") as fh:
            ids = [row["gmail_id"] for row in _csv.DictReader(fh) if row.get("gmail_id")]
        logger.info(f"Targeted pull from {args.ids_csv}: {len(ids):,} message ids")
    else:
        label_ids = None
        if label_names:
            resolved = client.resolve_labels(label_names)
            label_ids = list(resolved.values())
            logger.info(f"Resolved labels {list(resolved.keys())} -> {label_ids}")
        logger.info("Listing matching message ids…")
        ids = list(client.iter_message_ids(
            label_ids=label_ids, query=args.query, after=after, before=before))
    if args.limit:
        ids = ids[: args.limit]
    logger.info(f"{len(ids):,} messages to process "
                f"(labels={label_names or 'ANY'}, after={args.after}, before={args.before})")

    if not ids:
        print("Nothing to pull.")
        return 0

    if args.dry_run:
        # Parse a sample so the user sees what WOULD be ingested AND can confirm
        # the cleanliness filter (signature logos / inline images are dropped;
        # only real attachments are kept).
        from src.ingest.gmail_ingest import parse_raw_email
        sample_n = args.limit if args.limit else min(40, len(ids))
        print(f"\nDRY RUN — no writes. Inspecting a sample of {sample_n} message(s):\n")
        kept_total = logos_total = 0
        ext_counter = Counter()
        kept_names = []
        for mid in tqdm(ids[:sample_n], desc="Sampling", unit="msg"):
            try:
                parsed = parse_raw_email(client.get_raw(mid))
            except Exception as exc:  # noqa: BLE001
                print(f"  - {mid}: parse error {exc}")
                continue
            kept = parsed["attachments"]
            kept_total += len(kept)
            logos_total += parsed["skipped_logos"]
            for a in kept:
                fn = a.filename or "(unnamed)"
                ext = ("." + fn.rsplit(".", 1)[-1].lower()) if "." in fn else "(none)"
                ext_counter[ext] += 1
                if len(kept_names) < 25:
                    kept_names.append(f"{fn} ({a.size_bytes:,}B)")

        print("\n---------- CLEANLINESS CHECK (sample) ----------")
        print(f"  messages sampled            : {sample_n}")
        print(f"  REAL attachments KEPT       : {kept_total}")
        print(f"  signature logos/inline DROP : {logos_total}")
        print(f"  kept by extension           : {dict(ext_counter)}")
        if kept_names:
            print("  sample of kept filenames (confirm these are real docs, not logos):")
            for nm in kept_names:
                print(f"      - {nm}")
        print("------------------------------------------------")
        print(f"\nWould process {len(ids):,} messages total. "
              f"Re-run with --live to ingest.")
        return 0

    # LIVE
    settings = Settings.load()
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    mongo.ping()
    repo = EmailRepository(mongo)
    run_id = repo.start_run(pst_meta={
        "origin": "gmail_api", "labels": label_names,
        "after": args.after, "before": args.before,
        "corpus": args.corpus,
    })

    tally = Counter()
    attachments = 0
    errors = 0
    for mid in tqdm(ids, desc="Ingesting Gmail", unit="msg"):
        try:
            meta = client.get_metadata(mid)
            raw = client.get_raw(mid)
            res = ingest_one_email(
                raw, gmail_id=mid, thread_id=meta.get("thread_id"),
                label_names=label_names, mongo=mongo, repo=repo, run_id=run_id,
                settings=settings, corpus=args.corpus,
                privilege_status=args.privilege, custodian=args.custodian)
            tally[res["outcome"]] += 1
            attachments += res.get("attachments_stored", 0)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.error(f"ingest failed for {mid}: {exc}")
            repo.log_error(run_id, "gmail:" + mid, "ingest_gmail", str(exc))

    totals = {
        "messages_seen": len(ids),
        "messages_inserted": tally.get(OUT_INSERTED, 0),
        "messages_skipped": sum(v for k, v in tally.items() if k != OUT_INSERTED),
        "attachments_inserted": attachments,
        "errors": errors,
        "by_outcome": dict(tally),
    }
    repo.finish_run(run_id, totals, status="completed")
    mongo.close()

    print("\n================ GMAIL PULL COMPLETE ================")
    for k, v in totals.items():
        print(f"  {k}: {v}")
    print("\nNext: run the chunk/embed step to make new mail searchable:")
    print("  python -m scripts.build_email_chunks_v2   (resumable; SHA-deduped)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only Gmail ingestion (Phase 4 Sprint 1).")
    ap.add_argument("--client-secret", default=None,
                    help="OAuth client-secret JSON (overrides GMAIL_CLIENT_SECRET).")
    ap.add_argument("--token", default=None,
                    help="Token cache path (overrides GMAIL_TOKEN_PATH).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("profile", help="Show the authenticated mailbox.")
    sub.add_parser("labels", help="List Gmail labels (folders) + counts.")

    pp = sub.add_parser("pull", help="Pull messages for label(s) + date range.")
    pp.add_argument("--label", action="append", help="Label/folder NAME (repeatable).")
    pp.add_argument("--ids-csv", default=None,
                    help="Ingest exactly the gmail_ids in this audit CSV (the "
                         "precise MISSING set) instead of scanning the label.")
    pp.add_argument("--after", default=None, help="Only messages on/after YYYY-MM-DD.")
    pp.add_argument("--before", default=None, help="Only messages before YYYY-MM-DD.")
    pp.add_argument("--query", default=None, help="Extra raw Gmail search query.")
    pp.add_argument("--limit", type=int, default=0, help="Cap messages (smoke test).")
    pp.add_argument("--corpus", default=ev.CORPUS_LEGAL_CORRESPONDENCE,
                    help=f"Evidentiary corpus (default {ev.CORPUS_LEGAL_CORRESPONDENCE}).")
    pp.add_argument("--privilege", default=None,
                    help="Override privilege_status (e.g. adverse_party for David folders).")
    pp.add_argument("--custodian", default="Gmail mailbox (read-only API pull)")
    pp.add_argument("--dry-run", action="store_true", default=True,
                    help="Count + sample only; no writes (default).")
    pp.add_argument("--live", dest="dry_run", action="store_false",
                    help="Actually ingest.")

    args = ap.parse_args()

    kwargs = {}
    if args.client_secret:
        kwargs["client_secret_path"] = args.client_secret
    if args.token:
        kwargs["token_path"] = args.token
    client = GmailClient(**kwargs).authenticate()

    if args.cmd == "profile":
        return cmd_profile(client)
    if args.cmd == "labels":
        return cmd_labels(client)
    if args.cmd == "pull":
        return cmd_pull(client, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
