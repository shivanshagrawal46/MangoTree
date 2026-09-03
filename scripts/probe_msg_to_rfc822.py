"""Can a .msg be converted to RFC822 faithfully enough to reuse the mail pipeline?

Converting rather than writing a second ingestion path is worth a lot: the
existing pipeline already handles participant filtering, threading, body
cleaning, attachment storage, dedup and property resolution. A .msg that becomes
ordinary RFC822 bytes inherits all of it and stays indistinguishable from mail
pulled via API.

This checks the conversion preserves the three things the pipeline depends on:
the Message-ID, the participant headers, and the attachment bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

import extract_msg

from mangotree.ingest.mime_parser import parse_rfc822

ROOT = Path(r"E:\LP Remodeling Projects\Hold Properties")


def main() -> None:
    paths = sorted(ROOT.rglob("*.msg"))
    print(f"\n  {len(paths)} file(s)\n")

    for path in paths:
        print(f"  {path.name}")
        try:
            with extract_msg.Message(str(path)) as msg:
                original_atts = [
                    a for a in msg.attachments
                    if not isinstance(a.data, extract_msg.msg_classes.msg.MSGFile)
                ]
                eml = msg.asEmailMessage().as_bytes()
        except Exception as exc:
            print(f"    CONVERT FAILED: {type(exc).__name__}: {exc}\n")
            continue

        try:
            parsed = parse_rfc822(eml)
        except Exception as exc:
            print(f"    REPARSE FAILED: {type(exc).__name__}: {exc}\n")
            continue

        kept = len(parsed.attachments)
        logos = sum(1 for a in parsed.attachments if a.likely_logo)
        print(f"    rfc822 bytes   {len(eml):>10,}")
        print(f"    message-id     {(parsed.message_id or 'MISSING')[:58]}")
        print(f"    from/to        {parsed.headers.get('from', '?')[:40]} -> {str(parsed.headers.get('to'))[:34]}")
        print(f"    date           {parsed.date}")
        print(f"    body           {len(parsed.body_text or ''):,} chars text, {len(parsed.body_html or ''):,} html")
        print(f"    attachments    {kept} parsed ({logos} flagged logo) vs {len(original_atts)} in the .msg")
        for att in parsed.attachments:
            flag = " [logo]" if att.likely_logo else ""
            print(f"      {att.size / 1024:>9,.0f} KB  {(att.filename or '?')[:46]}{flag}")
        print()


if __name__ == "__main__":
    main()
