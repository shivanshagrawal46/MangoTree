"""Read-only census of Rakesh's Outlook mailbox, to decide ingestion scope.

Nothing here writes to Mongo, the object store, or any model. It reads envelope
metadata only — sender, recipients, subject, date — and prints a report.

Why this exists
---------------
The admin directive is "Inbox and Sent only". Graph's well-known ``inbox`` and
``sentitems`` folders exclude their own subfolders, and this mailbox turned out
to be heavily foldered: thousands of sent messages sit in ``Sent Items/...``
children, and there is a top-level folder named after one of the 15 properties.
Obeying the directive literally would have quietly dropped them. This measures
exactly what each folder holds so the scope decision is made on numbers.

Usage
-----
    python scripts/outlook_survey.py
    python scripts/outlook_survey.py --json out/outlook_survey.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

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
from mangotree.ingest.graph_auth import GraphDelegatedAuth  # noqa: E402
from mangotree.ingest.graph_client import GraphClient  # noqa: E402

#: Excluded by admin instruction (deleted mail) or because they are structurally
#: not correspondence. Still counted and shown, never walked.
SKIP_PREFIXES = (
    "deleted items",
    "trash",
    "junk email",
    "sync issues",
    "outbox",
    "conversation history",
)


def is_skipped(path: str) -> bool:
    head = path.split("/", 1)[0].strip().lower()
    return head in SKIP_PREFIXES


def addresses_of(message: dict) -> List[str]:
    out: List[str] = []
    sender = (message.get("from") or {}).get("emailAddress", {}).get("address")
    if sender:
        out.append(sender.strip().lower())
    for key in ("toRecipients", "ccRecipients"):
        for entry in message.get(key) or []:
            addr = (entry.get("emailAddress") or {}).get("address")
            if addr:
                out.append(addr.strip().lower())
    return out


def properties_in(subject: str) -> Set[str]:
    """Property ids whose alias appears in the subject.

    A bare ambiguous alias ("Bayshore", shared by two properties) is not enough
    on its own — 904 and 910 are different deals and guessing between them is
    worse than reporting nothing.
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


def classify(message: dict) -> dict:
    addrs = addresses_of(message)
    known_external = set()
    unknown = set()
    internal = 0
    for addr in addrs:
        person = ADDRESS_INDEX.get(addr)
        if person is not None:
            if person.side == Side.EXTERNAL:
                known_external.add(addr)
            else:
                internal += 1
        elif addr.rsplit("@", 1)[-1] in RKB_DOMAINS:
            internal += 1
        else:
            unknown.add(addr)

    subject = message.get("subject") or ""
    props = properties_in(subject)
    return {
        "known_external": known_external,
        "unknown": unknown,
        "internal_only": internal > 0 and not known_external and not unknown,
        "properties": props,
        "has_attachments": bool(message.get("hasAttachments")),
    }


def survey_folder(client: GraphClient, folder: dict, since: datetime) -> dict:
    date_field = (
        "sentDateTime" if folder["path"].lower().startswith("sent")
        else "receivedDateTime"
    )
    result = {
        "path": folder["path"],
        "total": folder["count"],
        "in_window": 0,
        "with_known_external": 0,
        "internal_only": 0,
        "with_property": 0,
        "with_attachments": 0,
        "properties": Counter(),
        "top_unknown_domains": Counter(),
        "oldest": None,
        "newest": None,
        "error": None,
    }

    try:
        stream: Iterable[dict] = client.survey_messages(
            folder["id"], date_field=date_field, since=since
        )
        for message in stream:
            date = (message.get(date_field) or "")[:10]
            if date:
                if result["oldest"] is None or date < result["oldest"]:
                    result["oldest"] = date
                if result["newest"] is None or date > result["newest"]:
                    result["newest"] = date

            result["in_window"] += 1
            verdict = classify(message)
            if verdict["known_external"]:
                result["with_known_external"] += 1
            if verdict["internal_only"]:
                result["internal_only"] += 1
            if verdict["properties"]:
                result["with_property"] += 1
                for property_id in verdict["properties"]:
                    result["properties"][property_id] += 1
            if verdict["has_attachments"]:
                result["with_attachments"] += 1
            for addr in verdict["unknown"]:
                result["top_unknown_domains"][addr.rsplit("@", 1)[-1]] += 1
    except Exception as exc:  # a folder failing must not lose the whole survey
        result["error"] = str(exc)[:200]

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default="", help="also write the raw report here")
    parser.add_argument("--min-count", type=int, default=1,
                        help="ignore folders holding fewer than this many messages")
    args = parser.parse_args()

    since = SETTINGS.backfill_since
    auth = GraphDelegatedAuth(
        tenant_id=SETTINGS.graph_tenant_id,
        client_id=SETTINGS.graph_client_id,
        mailbox=SETTINGS.graph_mailbox,
    )
    if not auth.is_authenticated:
        print("Not signed in. Run: python -m mangotree.cli outlook-auth")
        return 1

    client = GraphClient(auth)
    print(f"\n=== Outlook survey: {client.mailbox} ===")
    print(f"    window opens {since:%Y-%m-%d}   (read-only, nothing stored)\n")

    folders = client.folder_census()
    walk = [
        f for f in folders
        if f["count"] >= args.min_count and not is_skipped(f["path"])
    ]
    skipped = [f for f in folders if is_skipped(f["path"]) and f["count"]]

    print(f"  {len(walk)} folders to survey, "
          f"{sum(f['count'] for f in walk):,} messages before the date filter")
    print(f"  {len(skipped)} folders excluded "
          f"({sum(f['count'] for f in skipped):,} messages: deleted, junk, outbox)\n")

    reports: List[dict] = []
    for index, folder in enumerate(sorted(walk, key=lambda f: -f["count"]), 1):
        print(f"  [{index:>2}/{len(walk)}] {folder['path'][:46]:<48}", end="", flush=True)
        report = survey_folder(client, folder, since)
        reports.append(report)
        if report["error"]:
            print(f" ERROR {report['error'][:40]}")
        else:
            print(f" {report['in_window']:>6} in window, "
                  f"{report['with_known_external']:>5} known, "
                  f"{report['with_property']:>4} property")

    # ---------------------------------------------------------------- report
    print("\n" + "=" * 78)
    print("  FOLDERS WORTH INGESTING  (in-window mail involving known contacts)")
    print("=" * 78)
    print(f"  {'folder':<40}{'window':>8}{'known':>8}{'prop':>7}{'attach':>8}")
    ranked = sorted(reports, key=lambda r: -(r["with_known_external"] + r["with_property"]))
    for report in ranked:
        signal = report["with_known_external"] + report["with_property"]
        if signal == 0:
            continue
        print(f"  {report['path'][:38]:<40}{report['in_window']:>8}"
              f"{report['with_known_external']:>8}{report['with_property']:>7}"
              f"{report['with_attachments']:>8}")

    silent = [r for r in ranked if r["with_known_external"] + r["with_property"] == 0]
    if silent:
        print(f"\n  {len(silent)} folders carry no known contact and no property "
              f"mention ({sum(r['in_window'] for r in silent):,} messages):")
        for report in silent[:16]:
            print(f"    {report['path'][:44]:<46} {report['in_window']:>6}")

    print("\n" + "=" * 78)
    print("  PROPERTY MENTIONS IN SUBJECT LINES  (across surveyed folders)")
    print("=" * 78)
    totals: Counter = Counter()
    for report in reports:
        totals.update(report["properties"])
    if totals:
        for property_id, count in totals.most_common():
            where = sorted(
                ((r["properties"][property_id], r["path"]) for r in reports
                 if r["properties"].get(property_id)),
                reverse=True,
            )[:3]
            trail = ", ".join(f"{path.split('/')[-1][:22]} {n}" for n, path in where)
            print(f"  {property_id:<16}{count:>6}   {trail}")
    else:
        print("  none — subjects rarely name the property; body text will carry it")

    grand = sum(r["in_window"] for r in reports)
    known = sum(r["with_known_external"] for r in reports)
    attach = sum(r["with_attachments"] for r in reports)
    print("\n" + "=" * 78)
    print(f"  {grand:,} messages in window across surveyed folders")
    print(f"  {known:,} involve a contact from the registry")
    print(f"  {attach:,} carry attachments")
    print("=" * 78 + "\n")

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        serialisable = [
            {**r, "properties": dict(r["properties"]),
             "top_unknown_domains": dict(r["top_unknown_domains"].most_common(25))}
            for r in reports
        ]
        path.write_text(json.dumps({
            "mailbox": client.mailbox,
            "window_start": since.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "excluded_folders": [{"path": f["path"], "count": f["count"]} for f in skipped],
            "folders": serialisable,
        }, indent=2), encoding="utf-8")
        print(f"  raw report written to {path}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
