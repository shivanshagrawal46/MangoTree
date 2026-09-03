"""Fetch a message that failed to parse and reproduce the failure with a traceback.

Guessing at a MIME bug from the exception text alone is how you fix the wrong
thing. This pulls the exact bytes and runs the real parser over them.
"""
from __future__ import annotations

import sys
import traceback

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.ingest.graph_auth import GraphDelegatedAuth
from mangotree.ingest.graph_client import GraphClient
from mangotree.ingest.mime_parser import parse_rfc822
from mangotree.storage.mongo import get_mongo


def failing_ids(limit: int) -> list:
    mongo = get_mongo()
    rows = mongo.errors.find(
        {"stage": {"$in": ["parse", "outlook_process"]}}, {"key": 1, "error": 1}
    ).limit(limit)
    return [(r.get("key"), r.get("error", "")) for r in rows]


def main() -> int:
    targets = sys.argv[1:]
    if not targets:
        found = failing_ids(10)
        if not found:
            print("no recorded parse failures")
            return 0
        print(f"\n  {len(found)} recorded failure(s):")
        for target, error in found:
            print(f"    {error[:70]:<72} {str(target)[:40]}")
        targets = [t for t, _ in found]

    auth = GraphDelegatedAuth(
        tenant_id=SETTINGS.graph_tenant_id,
        client_id=SETTINGS.graph_client_id,
        mailbox=SETTINGS.graph_mailbox,
    )
    client = GraphClient(auth)

    for target in targets[:5]:
        print("\n" + "=" * 78)
        print(f"  {str(target)[:70]}")
        try:
            raw = client.raw_mime(target)
        except Exception as exc:
            print(f"  could not fetch: {exc}")
            continue

        print(f"  {len(raw)} bytes")
        head = raw[:1400].decode("utf-8", errors="replace")
        for line in head.splitlines():
            low = line.lower()
            if low.startswith((
                "content-type", "content-transfer", "subject", "from",
                "mime-version", "charset", "content-disposition",
            )):
                print(f"    | {line[:150]}")

        try:
            parsed = parse_rfc822(raw)
            print(f"  PARSED OK — subject={parsed.subject[:60]!r} "
                  f"attachments={len(parsed.attachments)} body={len(parsed.body_text)}")
        except Exception:
            print("  FAILED:")
            traceback.print_exc()

        out = f"logs/failing_{str(target)[:16].replace('/', '_')}.eml"
        with open(out, "wb") as handle:
            handle.write(raw)
        print(f"  saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
