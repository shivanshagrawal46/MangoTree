"""How much mail actually qualifies for ingestion, across Gmail and Outlook.

Read-only. Counts only — no bodies fetched, nothing stored, no model called.

The rule being measured (admin, 2026-09-02)
-------------------------------------------
Window is 2023-10-01 to today, Inbox and Sent only, both providers. A message
qualifies when a registry contact appears in From/To/Cc, **or** its subject
names one of the 15 properties. Mail passing only between RKB addresses is
excluded however it matches, because internal traffic is out of scope.

Because "the person's list" could reasonably mean the external counterparties
alone or every registered person including RKB staff, the two readings are
reported separately rather than silently resolved: bucket A is mail touching a
known external contact, bucket B is mail with no known external contact whose
subject still names a property. The qualifying total is A + B under either
reading; what changes is nothing, because RKB-only mail is excluded regardless.
Bucket C is what that exclusion costs, printed so the price of the rule is
visible rather than assumed.

Usage
-----
    python scripts/mail_scope_count.py
    python scripts/mail_scope_count.py --json out/mail_scope.json
    python scripts/mail_scope_count.py --gmail-only
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import getaddresses
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, ".")

from mangotree.config.registry import (  # noqa: E402
    ADDRESS_INDEX,
    ALIAS_PATTERNS,
    AMBIGUOUS_ALIASES,
    RKB_DOMAINS,
    Side,
    normalize_text,
)
from mangotree.config.settings import SETTINGS  # noqa: E402

#: Outlook folders in scope. The three beyond Inbox/Sent Items were found by the
#: folder survey: `Briardale Tampa` is a property folder sitting at the mailbox
#: root, and the two forwarding folders hold sent mail that Graph's `sentitems`
#: does not return because it excludes child folders.
OUTLOOK_CORE = ("Inbox", "Sent Items")
OUTLOOK_EXTRA = (
    "Briardale Tampa",
    "Sent Items/Forwarded to JP Sir",
    "Sent Items/Forwarded to Neha",
    "Sent Items/RB Sir to guide",
)


# ------------------------------------------------------------------ shared
def properties_in(subject: str) -> Set[str]:
    """Property ids named in a subject line.

    Bare ambiguous aliases are ignored: "Bayshore" alone cannot distinguish 904
    from 910, and attributing mail to the wrong one of two live deals is worse
    than counting it as unmatched.
    """
    norm = normalize_text(subject or "")
    if not norm:
        return set()
    found: Set[str] = set()
    for property_id, pattern, _tokens, alias in ALIAS_PATTERNS:
        if normalize_text(alias) in AMBIGUOUS_ALIASES:
            continue
        if pattern.search(norm):
            found.add(property_id)
    return found


def side_of(address: str) -> Optional[Side]:
    person = ADDRESS_INDEX.get(address)
    if person is not None:
        return person.side
    if address.rsplit("@", 1)[-1] in RKB_DOMAINS:
        return Side.RKB
    return None


def classify(addresses: Sequence[str], subject: str) -> dict:
    sides = [side_of(a) for a in addresses]
    known_external = {a for a, s in zip(addresses, sides) if s == Side.EXTERNAL}
    unknown = {a for a, s in zip(addresses, sides) if s is None}
    props = properties_in(subject)

    if addresses and all(s == Side.RKB for s in sides):
        bucket = "C_internal"
    elif known_external:
        bucket = "A_known_contact"
    elif props:
        bucket = "B_property_subject"
    else:
        bucket = "D_no_signal"

    return {
        "bucket": bucket,
        "properties": props,
        "known_external": known_external,
        "unknown": unknown,
    }


class Tally:
    """Counts per bucket, kept identical for both providers so totals combine."""

    def __init__(self) -> None:
        self.messages: Counter = Counter()
        self.attachments: Counter = Counter()
        self.properties: Counter = Counter()
        self.property_attachments: Counter = Counter()
        self.unknown_domains: Counter = Counter()
        self.qualifying_ids: Set[str] = set()
        self.qualifying_with_attachments: Set[str] = set()

    def add(self, verdict: dict, *, has_attachment: bool, message_id: str) -> None:
        bucket = verdict["bucket"]
        self.messages[bucket] += 1
        if has_attachment:
            self.attachments[bucket] += 1
        if bucket in ("A_known_contact", "B_property_subject"):
            for property_id in verdict["properties"]:
                self.properties[property_id] += 1
                if has_attachment:
                    self.property_attachments[property_id] += 1
            if message_id:
                self.qualifying_ids.add(message_id)
                if has_attachment:
                    self.qualifying_with_attachments.add(message_id)
        if bucket == "D_no_signal":
            for addr in verdict["unknown"]:
                self.unknown_domains[addr.rsplit("@", 1)[-1]] += 1

    @property
    def qualifying(self) -> int:
        return self.messages["A_known_contact"] + self.messages["B_property_subject"]

    @property
    def qualifying_attachments(self) -> int:
        return (self.attachments["A_known_contact"]
                + self.attachments["B_property_subject"])

    def as_dict(self) -> dict:
        return {
            "messages": dict(self.messages),
            "attachments": dict(self.attachments),
            "qualifying": self.qualifying,
            "qualifying_attachments": self.qualifying_attachments,
            "properties": dict(self.properties),
            "property_attachments": dict(self.property_attachments),
            "unique_message_ids": len(self.qualifying_ids),
        }


# ------------------------------------------------------------------ gmail
def survey_gmail(window_start: datetime, workers: int) -> Tuple[Dict[str, Tally], Tally]:
    from mangotree.ingest.gmail_client import GmailClient

    client = GmailClient().authenticate()
    print(f"\nGMAIL   {client.address}")

    # Gmail's `after:` works on whole days in the account's timezone, so it is
    # set a day early and the exact cut is applied below on internalDate.
    day_before = window_start.strftime("%Y/%m/%d")
    base = f"after:{day_before} -in:chats"

    per_folder: Dict[str, Tally] = {}
    combined = Tally()

    for label in ("INBOX", "SENT"):
        ids = [m["id"] for m in client.iter_message_ids(query=base, label_ids=[label])]
        with_attachment = {
            m["id"] for m in client.iter_message_ids(
                query=f"{base} has:attachment", label_ids=[label]
            )
        }
        print(f"  {label:<7} {len(ids):>6} messages listed, "
              f"{len(with_attachment):>5} with attachments — reading headers...")

        tally = Tally()
        cutoff_ms = int(window_start.timestamp() * 1000)

        def fetch(message_id: str) -> Optional[dict]:
            try:
                return client.get_metadata(message_id)
            except Exception:
                return None

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for record in pool.map(fetch, ids):
                done += 1
                if done % 500 == 0:
                    print(f"          {done:>6}/{len(ids)}", flush=True)
                if not record or record["internal_date_ms"] < cutoff_ms:
                    continue
                headers = record["headers"]
                addresses = [
                    addr.strip().lower()
                    for _name, addr in getaddresses([
                        headers.get("from", ""), headers.get("to", ""),
                        headers.get("cc", ""), headers.get("bcc", ""),
                    ])
                    if addr and "@" in addr
                ]
                verdict = classify(addresses, headers.get("subject", ""))
                tally.add(
                    verdict,
                    has_attachment=record["id"] in with_attachment,
                    message_id=(headers.get("message-id") or "").strip().lower(),
                )

        per_folder[f"gmail/{label}"] = tally
        for attr in ("messages", "attachments", "properties",
                     "property_attachments", "unknown_domains"):
            getattr(combined, attr).update(getattr(tally, attr))
        combined.qualifying_ids |= tally.qualifying_ids
        combined.qualifying_with_attachments |= tally.qualifying_with_attachments
        print(f"  {label:<7} qualifying {tally.qualifying:>6}   "
              f"attachments {tally.qualifying_attachments:>5}")

    return per_folder, combined


# ------------------------------------------------------------------ outlook
def survey_outlook(
    window_start: datetime, include_extra: bool
) -> Tuple[Dict[str, Tally], Tally]:
    from mangotree.ingest.graph_auth import GraphDelegatedAuth
    from mangotree.ingest.graph_client import GraphClient

    auth = GraphDelegatedAuth(
        tenant_id=SETTINGS.graph_tenant_id,
        client_id=SETTINGS.graph_client_id,
        mailbox=SETTINGS.graph_mailbox,
    )
    if not auth.is_authenticated:
        raise RuntimeError("Outlook not signed in — run: python -m mangotree.cli outlook-auth")

    client = GraphClient(auth)
    print(f"\nOUTLOOK {client.mailbox}")

    census = {f["path"]: f for f in client.folder_census()}
    wanted = list(OUTLOOK_CORE) + (list(OUTLOOK_EXTRA) if include_extra else [])

    per_folder: Dict[str, Tally] = {}
    combined = Tally()

    for path in wanted:
        folder = census.get(path)
        if not folder:
            print(f"  {path:<34} NOT FOUND, skipped")
            continue
        date_field = "sentDateTime" if path.lower().startswith("sent") else "receivedDateTime"
        tally = Tally()
        seen = 0
        for message in client.survey_messages(
            folder["id"], date_field=date_field, since=window_start
        ):
            seen += 1
            addresses = []
            sender = (message.get("from") or {}).get("emailAddress", {}).get("address")
            if sender:
                addresses.append(sender.strip().lower())
            for key in ("toRecipients", "ccRecipients", "bccRecipients"):
                for entry in message.get(key) or []:
                    addr = (entry.get("emailAddress") or {}).get("address")
                    if addr:
                        addresses.append(addr.strip().lower())

            verdict = classify(addresses, message.get("subject") or "")
            tally.add(
                verdict,
                has_attachment=bool(message.get("hasAttachments")),
                message_id=(message.get("internetMessageId") or "").strip().lower(),
            )

        per_folder[f"outlook/{path}"] = tally
        for attr in ("messages", "attachments", "properties",
                     "property_attachments", "unknown_domains"):
            getattr(combined, attr).update(getattr(tally, attr))
        combined.qualifying_ids |= tally.qualifying_ids
        combined.qualifying_with_attachments |= tally.qualifying_with_attachments
        print(f"  {path:<34} {seen:>6} in window   qualifying {tally.qualifying:>5}   "
              f"attachments {tally.qualifying_attachments:>5}")

    return per_folder, combined


# ------------------------------------------------------------------ report
BUCKET_LABELS = {
    "A_known_contact": "A  known contact in From/To/Cc",
    "B_property_subject": "B  no known contact, property in subject",
    "C_internal": "C  RKB-internal only  (EXCLUDED by rule)",
    "D_no_signal": "D  no contact, no property  (EXCLUDED)",
}


def print_buckets(title: str, tally: Tally) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)
    print(f"  {'bucket':<44}{'messages':>10}{'w/ attach':>12}")
    for key in ("A_known_contact", "B_property_subject", "C_internal", "D_no_signal"):
        print(f"  {BUCKET_LABELS[key]:<44}"
              f"{tally.messages.get(key, 0):>10,}{tally.attachments.get(key, 0):>12,}")
    print("  " + "-" * 74)
    print(f"  {'QUALIFYING  (A + B)':<44}"
          f"{tally.qualifying:>10,}{tally.qualifying_attachments:>12,}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="", help="write the raw numbers here")
    parser.add_argument("--workers", type=int, default=8,
                        help="parallel Gmail header fetches")
    parser.add_argument("--gmail-only", action="store_true")
    parser.add_argument("--outlook-only", action="store_true")
    parser.add_argument("--core-folders-only", action="store_true",
                        help="Outlook: Inbox and Sent Items only, no subfolders")
    args = parser.parse_args()

    window_start = SETTINGS.backfill_since
    print("\n" + "=" * 78)
    print("  QUALIFYING MAIL COUNT — Gmail + Outlook, Inbox and Sent")
    print(f"  window {window_start:%Y-%m-%d} to {datetime.now():%Y-%m-%d}"
          "   read-only, nothing stored")
    print("=" * 78)

    folders: Dict[str, Tally] = {}
    gmail_total = Tally()
    outlook_total = Tally()

    if not args.outlook_only:
        try:
            per_folder, gmail_total = survey_gmail(window_start, args.workers)
            folders.update(per_folder)
        except Exception as exc:
            print(f"\n  GMAIL FAILED: {exc}")

    if not args.gmail_only:
        try:
            per_folder, outlook_total = survey_outlook(
                window_start, include_extra=not args.core_folders_only
            )
            folders.update(per_folder)
        except Exception as exc:
            print(f"\n  OUTLOOK FAILED: {exc}")

    if not args.outlook_only:
        print_buckets("GMAIL  rakesh.bhargava@gmail.com", gmail_total)
    if not args.gmail_only:
        print_buckets("OUTLOOK  rakesh@mtreh.com", outlook_total)

    both = Tally()
    for attr in ("messages", "attachments", "properties",
                 "property_attachments", "unknown_domains"):
        getattr(both, attr).update(getattr(gmail_total, attr))
        getattr(both, attr).update(getattr(outlook_total, attr))
    both.qualifying_ids = gmail_total.qualifying_ids | outlook_total.qualifying_ids
    both.qualifying_with_attachments = (
        gmail_total.qualifying_with_attachments
        | outlook_total.qualifying_with_attachments
    )
    print_buckets("COMBINED", both)

    overlap = gmail_total.qualifying_ids & outlook_total.qualifying_ids
    print(f"\n  Same message present in both mailboxes: {len(overlap):,}")
    print(f"  UNIQUE qualifying messages after dedup:  {len(both.qualifying_ids):,}")
    print(f"  UNIQUE qualifying with attachments:      "
          f"{len(both.qualifying_with_attachments):,}")

    print("\n" + "=" * 78)
    print("  QUALIFYING MAIL BY PROPERTY  (subject-line mentions)")
    print("=" * 78)
    print(f"  {'property':<18}{'messages':>10}{'w/ attach':>12}")
    for property_id, count in both.properties.most_common():
        print(f"  {property_id:<18}{count:>10,}"
              f"{both.property_attachments.get(property_id, 0):>12,}")
    if not both.properties:
        print("  none")

    print("\n" + "=" * 78)
    print("  TOP UNKNOWN SENDERS IN EXCLUDED MAIL  (bucket D, for review)")
    print("=" * 78)
    for domain, count in both.unknown_domains.most_common(20):
        print(f"  {domain:<44}{count:>8,}")

    print()

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "window_start": window_start.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "gmail": gmail_total.as_dict(),
            "outlook": outlook_total.as_dict(),
            "combined": both.as_dict(),
            "unique_qualifying": len(both.qualifying_ids),
            "unique_qualifying_with_attachments": len(both.qualifying_with_attachments),
            "cross_provider_overlap": len(overlap),
            "per_folder": {name: t.as_dict() for name, t in folders.items()},
        }, indent=2), encoding="utf-8")
        print(f"  raw numbers written to {path}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
