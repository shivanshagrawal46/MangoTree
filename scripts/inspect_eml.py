"""Read a saved .eml off disk so its participants can be registered correctly.

Adding a person to the registry from a display name alone is how a registry ends
up with an address that never matches. The headers carry the real addresses, and
the body says what the person's role actually is.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.ingest.mime_parser import parse_rfc822

TARGET = sys.argv[1] if len(sys.argv) > 1 else "Email from Bill leroy.eml"


def main() -> None:
    root = Path(SETTINGS.disk_corpus_root)
    matches = [p for p in root.rglob("*") if p.is_file() and TARGET.lower() in p.name.lower()]
    if not matches:
        print(f"not found: {TARGET}")
        return

    path = matches[0]
    print(f"file: {path}")
    print(f"size: {path.stat().st_size:,} bytes\n")

    parsed = parse_rfc822(path.read_bytes())

    print("=== headers ===")
    headers = parsed.headers
    for key in ("from", "to", "cc", "bcc", "reply-to", "date", "subject",
                "message-id", "in-reply-to", "references", "return-path",
                "thread-topic"):
        value = headers.get(key) if hasattr(headers, "get") else None
        if value:
            print(f"  {key:<14}{value}")

    print("\n=== every header present ===")
    try:
        for key in sorted({k for k in headers.keys()}):
            print(f"  {key}")
    except Exception as exc:
        print(f"  (could not enumerate: {exc})")

    print("\n=== attachments ===")
    for att in (parsed.attachments or []):
        name = getattr(att, "filename", None) or (
            att.get("filename") if isinstance(att, dict) else "?"
        )
        size = getattr(att, "size", None) or (
            len(att.get("data", b"")) if isinstance(att, dict) else 0
        )
        print(f"  {name}  ({size:,} bytes)")

    print("\n=== body ===")
    body = getattr(parsed, "body_clean", None) or getattr(parsed, "text", "") or ""
    print(body[:3000])


if __name__ == "__main__":
    main()
