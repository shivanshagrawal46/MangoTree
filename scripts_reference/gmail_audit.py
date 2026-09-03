"""Gmail completeness audit — prove NO message in a label is missing.

Treats the Gmail label (e.g. "Boris Lawsuit") as the AUTHORITATIVE list of what
must exist, then checks every single message against our corpus. A message is
counted PRESENT if we hold it by:
  • its RFC822 Message-ID  (matches PST/.eml/Gmail-pulled copies), OR
  • its Gmail id           (matches anything already pulled from Gmail).

Anything not matched is reported as MISSING (written to a CSV with date / from /
subject / Message-ID), so it can be pulled. Re-running after a `pull --live`
should show 0 missing — that zero is your completeness certificate.

This is READ-ONLY (metadata only; no body download, no writes).

Usage:
  python -m scripts.gmail_audit --label "Boris Lawsuit"
  python -m scripts.gmail_audit --label "Boris Lawsuit" --after 2021-01-01 --before 2026-12-31
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.ingest.gmail_client import GmailClient


def _norm_mid(m: str) -> str:
    """Normalize a Message-ID for matching: strip spaces, angle brackets, case."""
    return (m or "").strip().strip("<>").strip().lower()


def _parse_day(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc) if s else None


def main() -> int:
    ap = argparse.ArgumentParser(description="Gmail label completeness audit (read-only).")
    ap.add_argument("--label", required=True, help="Label/folder NAME to audit.")
    ap.add_argument("--after", default=None, help="Only messages on/after YYYY-MM-DD.")
    ap.add_argument("--before", default=None, help="Only messages before YYYY-MM-DD.")
    ap.add_argument("--client-secret", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--out", default=None, help="CSV path for the missing list.")
    args = ap.parse_args()

    kwargs = {}
    if args.client_secret:
        kwargs["client_secret_path"] = args.client_secret
    if args.token:
        kwargs["token_path"] = args.token
    client = GmailClient(**kwargs).authenticate()

    label_id = list(client.resolve_labels([args.label]).values())[0]
    after, before = _parse_day(args.after), _parse_day(args.before)

    # 1) authoritative list from Gmail
    print(f"Enumerating messages in label '{args.label}'…", flush=True)
    ids = list(client.iter_message_ids(label_ids=[label_id], after=after, before=before))
    print(f"Gmail reports {len(ids):,} messages in this label.\n", flush=True)
    if not ids:
        print("Nothing to audit.")
        return 0

    # 2) what we already hold (Message-IDs + Gmail ids)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()
    held_mid = set()
    held_gid = set()
    for d in m.emails.find({}, {"internet_message_id": 1, "source.gmail_id": 1,
                                "gmail_id": 1, "pst_entry_id": 1}):
        if d.get("internet_message_id"):
            held_mid.add(_norm_mid(d["internet_message_id"]))
        gid = (d.get("source") or {}).get("gmail_id") or d.get("gmail_id")
        if gid:
            held_gid.add(gid)
        pid = d.get("pst_entry_id") or ""
        if pid.startswith("gmail:"):
            held_gid.add(pid.split("gmail:", 1)[1])
    print(f"Corpus currently holds {len(held_mid):,} distinct Message-IDs "
          f"and {len(held_gid):,} Gmail ids.\n", flush=True)

    # 3) reconcile
    missing = []
    present = 0
    total_attachments = 0
    for mid in tqdm(ids, desc="Auditing", unit="msg"):
        h = client.get_headers(mid)
        total_attachments += h.get("n_attachments", 0)
        mhdr = _norm_mid(h.get("message_id_header"))
        if (mhdr and mhdr in held_mid) or (mid in held_gid):
            present += 1
        else:
            missing.append(h)

    m.close()

    # 4) report + certificate
    out_path = Path(args.out) if args.out else Path(
        f"gmail_audit_{args.label.replace(' ', '_')}.csv")
    if missing:
        with open(out_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["gmail_id", "date", "from", "subject",
                        "message_id_header", "n_attachments"])
            for h in missing:
                w.writerow([h.get("gmail_id"), h.get("date"), h.get("from"),
                            h.get("subject"), h.get("message_id_header"),
                            h.get("n_attachments")])

    print("\n================ GMAIL COMPLETENESS AUDIT ================")
    print(f"Label                         : {args.label}")
    print(f"Date range                    : {args.after or 'ALL'} -> {args.before or 'ALL'}")
    print(f"Messages in Gmail (authority) : {len(ids):,}")
    print(f"  already in our corpus       : {present:,}")
    print(f"  MISSING                     : {len(missing):,}")
    print(f"Attachments seen in Gmail     : {total_attachments:,}")
    if missing:
        print(f"\nMissing list written to       : {out_path.resolve()}")
        print("\n>>> NOT COMPLETE YET. Pull the label, then re-run this audit:")
        print(f'    python -m scripts.ingest_gmail pull --label "{args.label}" --live')
    else:
        print("\n>>> CERTIFICATE: 0 unaccounted — every message in this label is "
              "present in the corpus.")
    print("=" * 57)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
