"""Which From address did Rakesh Sir's Gmail sent mail actually use?

Read-only, counts only. Nothing fetched beyond headers, nothing stored.

Why it matters
--------------
The entire reason Gmail is in scope is that business mail sent through the
`rakesh@mtreh.com` "send mail as" dropdown lives only in Gmail's SENT label and
can never appear in Outlook. The scope count proved 485 sent messages qualify,
but not which address they were sent from — so the premise was still assumed
rather than measured. This measures it.

Listing is done with Gmail's `from:` search, which reads the From header and
costs one page request per 500 ids rather than one fetch per message. Headers
are pulled only for the send-as subset, which is the set actually in question.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from email.utils import getaddresses, parseaddr
from typing import Dict, List, Optional, Set

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS  # noqa: E402
from scripts.mail_scope_count import classify  # noqa: E402

SEND_AS = "rakesh@mtreh.com"
PERSONAL = "rakesh.bhargava@gmail.com"


def ids_for(client, query: str) -> Set[str]:
    return {m["id"] for m in client.iter_message_ids(query=query, label_ids=["SENT"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-qualifying", action="store_true",
                        help="raw split only, no header pass")
    args = parser.parse_args()

    from mangotree.ingest.gmail_client import GmailClient

    window = SETTINGS.backfill_since
    base = f"after:{window:%Y/%m/%d} -in:chats"

    client = GmailClient().authenticate()
    print(f"\n=== Gmail SENT — From-address split ===")
    print(f"    {client.address}   window from {window:%Y-%m-%d}\n")

    everything = ids_for(client, base)
    send_as = ids_for(client, f"{base} from:{SEND_AS}")
    personal = ids_for(client, f"{base} from:{PERSONAL}")
    other = everything - send_as - personal

    print(f"  total sent in window                {len(everything):>7,}")
    print(f"  From: {SEND_AS:<28}{len(send_as):>7,}   <- invisible to Outlook")
    print(f"  From: {PERSONAL:<28}{len(personal):>7,}")
    print(f"  From: some other address            {len(other):>7,}")
    both = send_as & personal
    if both:
        print(f"  counted in both queries             {len(both):>7,}")

    if args.skip_qualifying:
        return 0

    # Only the send-as set needs a header pass: it is the set whose existence the
    # Gmail integration was built to rescue, and it is far smaller than SENT.
    print(f"\n  Reading headers for the {len(send_as):,} send-as messages...")
    ordered: List[str] = sorted(send_as)
    cutoff_ms = int(window.timestamp() * 1000)

    def fetch(message_id: str) -> Optional[dict]:
        try:
            return client.get_metadata(message_id)
        except Exception:
            return None

    buckets: Counter = Counter()
    with_attachment_ids = {
        m["id"] for m in client.iter_message_ids(
            query=f"{base} from:{SEND_AS} has:attachment", label_ids=["SENT"]
        )
    }
    qualifying_attachments = 0
    properties: Counter = Counter()
    recipients: Counter = Counter()

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for record in pool.map(fetch, ordered):
            done += 1
            if done % 250 == 0:
                print(f"    {done:>6}/{len(ordered)}", flush=True)
            if not record or record["internal_date_ms"] < cutoff_ms:
                continue
            headers = record["headers"]
            addresses = [
                addr.strip().lower()
                for _n, addr in getaddresses([
                    headers.get("from", ""), headers.get("to", ""),
                    headers.get("cc", ""), headers.get("bcc", ""),
                ])
                if addr and "@" in addr
            ]
            verdict = classify(addresses, headers.get("subject", ""))
            buckets[verdict["bucket"]] += 1
            if verdict["bucket"] in ("A_known_contact", "B_property_subject"):
                if record["id"] in with_attachment_ids:
                    qualifying_attachments += 1
                for property_id in verdict["properties"]:
                    properties[property_id] += 1
                for addr in verdict["known_external"]:
                    recipients[addr] += 1

    qualifying = buckets["A_known_contact"] + buckets["B_property_subject"]
    print(f"\n  --- of the {len(ordered):,} sent as {SEND_AS} ---")
    print(f"    A  known contact present              {buckets['A_known_contact']:>6,}")
    print(f"    B  property in subject only           {buckets['B_property_subject']:>6,}")
    print(f"    C  RKB-internal only     (excluded)   {buckets['C_internal']:>6,}")
    print(f"    D  no signal             (excluded)   {buckets['D_no_signal']:>6,}")
    print(f"    {'QUALIFYING':<38}{qualifying:>6,}")
    print(f"    {'  of those, with attachments':<38}{qualifying_attachments:>6,}")

    if properties:
        print("\n  properties named in these subjects")
        for property_id, count in properties.most_common():
            print(f"    {property_id:<18}{count:>5,}")

    if recipients:
        print("\n  most frequent external contacts on this traffic")
        for addr, count in recipients.most_common(12):
            print(f"    {addr:<44}{count:>5,}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
