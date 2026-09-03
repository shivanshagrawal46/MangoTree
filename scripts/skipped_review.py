"""Build the review list for skipped mail: who was the unknown party, and what were they writing about?

Read-only. Fetches metadata headers only (no bodies) for messages we skipped,
so Rakesh Sir can approve or reject each unknown counterparty.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, ".")

from mangotree.config.registry import PROPERTIES
from mangotree.config.settings import SETTINGS
from mangotree.ingest.gmail_client import GmailClient
from mangotree.storage.mongo import get_mongo

KNOWN = set()
for p in PROPERTIES:
    for a in (p.canonical_address,) + tuple(p.aliases):
        KNOWN.add((p.property_id, a.lower()))


def property_hits(text: str) -> set:
    t = (text or "").lower()
    return {pid for pid, alias in KNOWN if alias in t}


def main() -> None:
    db = get_mongo().db

    skipped = list(
        db.skipped.find(
            {"reason": {"$in": ["skip_unknown_external", "skip_no_rkb"]}, "provider": "gmail"},
            {"provider_id": 1, "reason": 1, "discovery_candidates": 1, "date": 1},
        )
    )
    print(f"gmail messages to inspect: {len(skipped)}")

    gc = GmailClient(
        client_secret_path=SETTINGS.gmail_client_secret,
        token_path=SETTINGS.gmail_token_path,
    )
    gc.authenticate()

    def fetch(rec):
        try:
            data = gc._call(
                gc.service.users().messages().get(
                    userId="me",
                    id=rec["provider_id"],
                    format="metadata",
                    metadataHeaders=["Subject", "From", "To", "Cc", "Date"],
                )
            )
            hdrs = {
                h["name"].lower(): h["value"]
                for h in data.get("payload", {}).get("headers", [])
            }
            return rec, hdrs, data.get("labelIds", [])
        except Exception as e:  # pragma: no cover
            return rec, {"error": str(e)}, []

    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for out in ex.map(fetch, skipped):
            results.append(out)

    by_party = defaultdict(list)
    for rec, hdrs, labels in results:
        subject = hdrs.get("subject", "(no subject)")
        hits = property_hits(subject)
        for cand in rec.get("discovery_candidates") or ["(none)"]:
            by_party[cand].append(
                {
                    "date": rec.get("date"),
                    "subject": subject,
                    "from": hdrs.get("from", ""),
                    "labels": [l for l in labels if l in ("INBOX", "SENT")],
                    "property_hits": sorted(hits),
                }
            )

    ranked = sorted(by_party.items(), key=lambda kv: -len(kv[1]))

    print("\n" + "=" * 100)
    print("SKIPPED COUNTERPARTIES — APPROVE OR REJECT EACH")
    print("=" * 100)
    for party, msgs in ranked:
        n_prop = sum(1 for m in msgs if m["property_hits"])
        flag = "  <<< MENTIONS OUR PROPERTIES" if n_prop else ""
        print(f"\n### {party}   ({len(msgs)} messages, {n_prop} mention a property){flag}")
        seen = set()
        for m in sorted(msgs, key=lambda x: str(x["date"]))[:12]:
            key = m["subject"][:70]
            if key in seen:
                continue
            seen.add(key)
            d = str(m["date"])[:10]
            fold = ",".join(m["labels"]) or "?"
            props = ("  [" + ",".join(m["property_hits"]) + "]") if m["property_hits"] else ""
            print(f"    {d}  {fold:6s}  {m['subject'][:78]}{props}")


if __name__ == "__main__":
    main()
