"""Every unknown email address in skipped mail, with what you need to judge it.

A bare ranked list of addresses is not a decision aid — "bgallagher@g-e-law.com,
28 messages" tells you nothing about whether those 28 matter. This groups by
organisation (people write from three addresses at one law firm), shows real
subject lines, and flags whether the traffic touches any of the 15 properties in
scope.

Grouping by domain is the important part: Gallagher Law appears as three separate
addresses in a flat list and looks minor three times over, when in fact it is the
single largest counterparty in the skipped set.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from mangotree.config.registry import ADDRESS_INDEX, PROPERTIES
from mangotree.storage.mongo import get_mongo

#: Domains the admin has already ruled out. Listed so the report can separate
#: "already decided" from "awaiting a decision" rather than re-presenting noise.
KNOWN_NOISE = {
    "compelceos.com", "foason.com", "rsprecision.com", "crescentproperties.com",
    "cultureindex.com", "mailchimp.com", "mail.mailchimp.com",
}

ALIASES = []
for prop in PROPERTIES:
    for alias in (prop.canonical_address, *prop.aliases):
        if len(alias) >= 4:
            ALIASES.append((alias.lower(), prop.property_id))


def properties_in(text: str) -> set:
    low = (text or "").lower()
    return {pid for alias, pid in ALIASES if alias in low}


def main() -> None:
    db = get_mongo().db

    by_address = defaultdict(lambda: {
        "count": 0, "subjects": [], "properties": set(), "reasons": set(),
    })

    for s in db["skipped"].find():
        candidates = s.get("discovery_candidates") or []
        subject = s.get("subject") or ""
        reason = s.get("reason") or ""
        hits = properties_in(subject)
        for address in candidates:
            key = str(address).strip().lower()
            if not key or "@" not in key:
                continue
            entry = by_address[key]
            entry["count"] += 1
            entry["reasons"].add(reason)
            entry["properties"] |= hits
            if subject and len(entry["subjects"]) < 6:
                if subject not in entry["subjects"]:
                    entry["subjects"].append(subject)

    # Drop anyone already in the registry — Bill Leroy was approved earlier and
    # should not reappear as an open question.
    for address in list(by_address):
        if address in ADDRESS_INDEX:
            del by_address[address]

    by_domain = defaultdict(list)
    for address, entry in by_address.items():
        by_domain[address.split("@")[-1]].append((address, entry))

    def domain_total(item):
        return sum(e["count"] for _, e in item[1])

    ordered = sorted(by_domain.items(), key=domain_total, reverse=True)

    pending = [(d, rows) for d, rows in ordered if d not in KNOWN_NOISE]
    noise = [(d, rows) for d, rows in ordered if d in KNOWN_NOISE]

    total_msgs = sum(e["count"] for _, rows in ordered for _, e in rows)
    print("=" * 78)
    print(f"UNKNOWN COUNTERPARTIES — {len(by_address)} addresses across "
          f"{len(by_domain)} domains, {total_msgs} skipped messages")
    print("=" * 78)

    print("\n" + "#" * 78)
    print("# AWAITING YOUR DECISION")
    print("#" * 78)
    for domain, rows in pending:
        total = sum(e["count"] for _, e in rows)
        props = set()
        for _, e in rows:
            props |= e["properties"]
        print(f"\n{'=' * 74}")
        print(f"@{domain}   —   {total} messages, {len(rows)} address(es)")
        if props:
            print(f"  TOUCHES IN-SCOPE PROPERTIES: {sorted(props)}")
        for address, entry in sorted(rows, key=lambda r: -r[1]["count"]):
            print(f"\n  {address}  ({entry['count']} messages)")
            for subject in entry["subjects"]:
                print(f"      - {subject[:96]}")

    print("\n\n" + "#" * 78)
    print("# ALREADY RULED OUT AS NOISE (no action needed)")
    print("#" * 78)
    for domain, rows in noise:
        total = sum(e["count"] for _, e in rows)
        addresses = ", ".join(a for a, _ in rows)
        print(f"  {total:>4} msgs  @{domain}   ({addresses})")


if __name__ == "__main__":
    main()
