"""Recover attachments the ORIGINAL PST import missed, from the Gmail twin.

For each email already in the corpus (the PST set, matched to Gmail by RFC822
Message-ID), compare the REAL document attachments Gmail holds against what we
stored, and (in --live) add any missing ones onto the SAME email — by SHA-256,
so nothing is duplicated and nothing is deleted.

"REAL document" = uses the SAME filter ingestion uses (drops signature logos)
PLUS excludes `.ics` calendar invites, forwarded `.eml` wrappers, and
`winmail.dat` — so the count reflects genuine evidence documents only.

Modes:
  --dry-run (default) : report the EXACT number of missing documents. No writes.
                        (filename-based comparison; fast — no attachment download)
  --live              : fetch the Gmail message, extract the missing attachments,
                        and store+link them (SHA-256 dedup). Idempotent.

Targets the PST set by default (corpus=legal_correspondence, not gmail-origin);
the freshly Gmail-pulled emails already have correct attachments.

Usage:
  python -m scripts.backfill_gmail_attachments --dry-run
  python -m scripts.backfill_gmail_attachments --live
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.db.repository import EmailRepository
from src.ingest.gmail_client import GmailClient
from scripts.ingest_eml_folder import _is_signature_logo
from src.ingest.gmail_ingest import parse_raw_email
from src.utils.hashing import sha256_bytes
from src.utils.logger import logger

# Explicit Outlook auto-named inline-image (logo) pattern — excluded regardless
# of the MIME the source reports.
_LOGO_NAME_RE = re.compile(r"^image\d+\.(png|jpe?g|gif|bmp|tif|tiff|webp)$", re.I)


def _norm_fn(fn: str) -> str:
    fn = fn or ""
    # decode MIME-encoded filenames (=?iso-8859-1?Q?...?=) so an encoded stored
    # name matches Gmail's decoded name (the .eml import stored some encoded).
    if "=?" in fn:
        try:
            from email.header import decode_header, make_header
            fn = str(make_header(decode_header(fn)))
        except Exception:  # noqa: BLE001
            pass
    fn = fn.replace("\xa0", " ").replace("\r", " ").replace("\n", " ")
    fn = re.sub(r"\s+", " ", fn).strip().lower()
    return fn.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _is_real_doc_part(part: dict) -> bool:
    fn = (part.get("filename") or "").strip()
    low = fn.lower()
    if not low:
        return False
    if low.endswith((".ics", ".eml")) or low == "winmail.dat":
        return False
    if _LOGO_NAME_RE.match(low):
        return False
    if _is_signature_logo(filename=fn, content_type=part.get("mime", ""),
                          size=part.get("size", 0) or 0, disposition=part.get("disposition", ""),
                          content_id=part.get("content_id")):
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill PST-missed attachments from Gmail.")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--corpus", default="legal_correspondence",
                    help="Which corpus to reconcile (default legal_correspondence; "
                         "use fraud_communications for the David/AA_Fund folder).")
    ap.add_argument("--include-gmail", action="store_true",
                    help="Also check gmail-origin emails (normally already complete).")
    ap.add_argument("--from-report", default=None,
                    help="Targeted mode: only process the emails listed in a prior "
                         "dry-run report CSV (by pst_entry_id) — skips the full re-scan.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--client-secret", default=None)
    ap.add_argument("--token", default=None)
    args = ap.parse_args()

    kwargs = {}
    if args.client_secret:
        kwargs["client_secret_path"] = args.client_secret
    if args.token:
        kwargs["token_path"] = args.token
    client = GmailClient(**kwargs).authenticate()

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    repo = EmailRepository(m)

    if args.from_report:
        import csv as _csv
        with open(args.from_report, newline="", encoding="utf-8") as fh:
            pids = sorted({row["pst_entry_id"] for row in _csv.DictReader(fh)
                           if row.get("pst_entry_id")})
        q = {"pst_entry_id": {"$in": pids}, "internet_message_id": {"$nin": [None, ""]}}
        logger.info(f"Targeted mode: {len(pids)} emails from {args.from_report}")
    else:
        q = {"corpus": args.corpus, "internet_message_id": {"$nin": [None, ""]}}
        if not args.include_gmail:
            q["source.origin"] = {"$ne": "gmail_api"}
    targets = list(m.emails.find(q, {"_id": 1, "internet_message_id": 1,
                                     "pst_entry_id": 1, "subject": 1, "date_ymd": 1}))
    if args.limit:
        targets = targets[: args.limit]
    logger.info(f"{len(targets):,} target emails to reconcile against Gmail "
                f"({'DRY RUN' if args.dry_run else 'LIVE'})")

    run_id = None
    if not args.dry_run:
        run_id = repo.start_run(pst_meta={"origin": "gmail_attachment_backfill"})

    checked = not_in_gmail = no_mid = 0
    emails_with_missing = 0
    total_missing_docs = 0
    added_docs = 0
    rows_out = []

    for e in tqdm(targets, desc="Reconciling", unit="email"):
        mid = e.get("internet_message_id")
        if not mid:
            no_mid += 1
            continue
        try:
            gid = client.find_by_message_id(mid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"search failed for {mid}: {exc}")
            continue
        if not gid:
            not_in_gmail += 1
            continue
        checked += 1

        # stored attachment names for this email
        stored = list(m.attachments.find({"email_id": e["_id"]},
                                          {"filename": 1, "sha256": 1}))
        stored_names = {_norm_fn(a.get("filename")) for a in stored}
        stored_shas = {a.get("sha256") for a in stored if a.get("sha256")}

        # what Gmail really has (logo/.ics/.eml filtered)
        summary = client.get_full_summary(gid)
        gmail_docs = [p for p in summary.get("parts", []) if _is_real_doc_part(p)]
        missing_names = [p for p in gmail_docs if _norm_fn(p["filename"]) not in stored_names]

        if not missing_names:
            continue
        emails_with_missing += 1
        total_missing_docs += len(missing_names)
        for p in missing_names:
            rows_out.append({"pst_entry_id": e.get("pst_entry_id"),
                             "subject": e.get("subject"), "date": e.get("date_ymd"),
                             "missing_file": p["filename"], "size": p.get("size")})

        if args.dry_run:
            continue

        # LIVE: download each missing doc from Gmail's DECODED view (unpacks
        # winmail.dat/TNEF) by attachment-id, store by SHA-256.
        new_ids = []
        raw_atts = None  # lazy: filename -> bytes, only built if a part lacks an id
        for p in missing_names:
            att_id = p.get("attachment_id")
            data = None
            if att_id:
                try:
                    data = client.get_attachment(gid, att_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"attachment download failed ({p.get('filename')}): {exc}")
                    continue
            else:
                # Gmail inlined the part (small, no attachment-id) -> get it from
                # the raw message instead.
                if raw_atts is None:
                    try:
                        parsed = parse_raw_email(client.get_raw(gid))
                        raw_atts = {_norm_fn(a.filename): a.data for a in parsed["attachments"]}
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"raw fallback failed for {mid}: {exc}")
                        raw_atts = {}
                data = raw_atts.get(_norm_fn(p["filename"]))
            if not data:
                continue
            sha = sha256_bytes(data)
            if sha in stored_shas:
                continue
            if len(data) > s.attachment_max_bytes:
                logger.warning(f"skip oversize {p.get('filename')} ({len(data):,}B)")
                continue
            aid = repo.store_attachment(
                email_id=e["_id"], email_pst_entry_id=e.get("pst_entry_id") or str(e["_id"]),
                filename=p["filename"], display_name=p["filename"],
                content_type=p.get("mime"), data=data, sha256=sha,
                is_inline=False, content_id=p.get("content_id"))
            new_ids.append(aid)
            stored_shas.add(sha)
            added_docs += 1
        # relink (append to existing attachment_ids)
        if new_ids:
            cur = m.emails.find_one({"_id": e["_id"]}, {"attachment_ids": 1})
            allids = (cur.get("attachment_ids") or []) + new_ids
            m.emails.update_one({"_id": e["_id"]}, {"$set": {
                "attachment_ids": allids, "attachment_count": len(allids),
                "has_attachments": True}})

    if run_id:
        repo.finish_run(run_id, {"checked": checked, "emails_with_missing": emails_with_missing,
                                 "missing_docs": total_missing_docs, "added_docs": added_docs},
                        status="completed")

    out = Path("gmail_attachment_backfill_report.csv")
    if rows_out:
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["pst_entry_id", "subject", "date",
                                               "missing_file", "size"])
            w.writeheader(); w.writerows(rows_out)

    print("\n========= GMAIL ATTACHMENT RECONCILIATION =========")
    print(f"Target emails                 : {len(targets):,}")
    print(f"  matched in Gmail            : {checked:,}")
    print(f"  not found in Gmail          : {not_in_gmail:,}")
    print(f"  no Message-ID (unmatchable) : {no_mid:,}")
    print(f"Emails missing >=1 document   : {emails_with_missing:,}")
    print(f"TOTAL missing documents       : {total_missing_docs:,}")
    if not args.dry_run:
        print(f"Documents ADDED (backfilled)  : {added_docs:,}")
    if rows_out:
        print(f"Detail CSV                    : {out.resolve()}")
    if args.dry_run and total_missing_docs:
        print("\n(DRY RUN — re-run with --live to recover these documents.)")
    print("=" * 51)
    m.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
