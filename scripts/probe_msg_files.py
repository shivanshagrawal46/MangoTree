"""Inspect the .msg files before writing a parser for them.

Three things decide the parser's shape:

* whether a file holds one message or many (Outlook embeds messages as
  attachments, so a 19 MB "Briardale Texts.msg" may be an archive);
* whether an Internet Message-ID is present, since that — not the file bytes —
  is what lets us tell a genuinely new mail from one already pulled via API;
* what attachments ride along, because those need extraction afterwards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

import extract_msg

from mangotree.storage.mongo import get_mongo

ROOT = Path(r"E:\LP Remodeling Projects\Hold Properties")


def describe(msg, depth: int = 0) -> tuple[int, int]:
    """Print one message and recurse into embedded ones. Returns (messages, attachments)."""
    pad = "  " * (depth + 2)
    subject = (msg.subject or "(no subject)")[:66]
    sender = msg.sender or "?"
    mid = getattr(msg, "messageId", None)
    print(f"{pad}subject   {subject}")
    print(f"{pad}from      {str(sender)[:66]}")
    print(f"{pad}date      {msg.date}")
    print(f"{pad}msg-id    {mid or 'MISSING'}")
    body = msg.body or ""
    print(f"{pad}body      {len(body):,} chars")

    messages, attachments = 1, 0
    for att in msg.attachments:
        inner = att.data
        if isinstance(inner, extract_msg.msg_classes.msg.MSGFile):
            print(f"{pad}  [embedded message]")
            m, a = describe(inner, depth + 2)
            messages += m
            attachments += a
        else:
            name = att.getFilename() or "(unnamed)"
            size = len(inner) if isinstance(inner, bytes) else 0
            attachments += 1
            print(f"{pad}  attach  {size / 1024:>9,.0f} KB  {name[:52]}")
    return messages, attachments


def main() -> None:
    mongo = get_mongo()
    known_ids = {
        d["internet_message_id"]
        for d in mongo.artifacts.find(
            {"internet_message_id": {"$ne": None}}, {"internet_message_id": 1}
        )
    }
    print(f"\n  Message-IDs already in the corpus: {len(known_ids):,}")

    paths = sorted(ROOT.rglob("*.msg"))
    grand_msgs = grand_atts = 0
    seen_ids = []

    for path in paths:
        print(f"\n{'=' * 92}")
        print(f"  {path.name}   ({path.stat().st_size / 1024 / 1024:.2f} MB)")
        print("=" * 92)
        try:
            with extract_msg.Message(str(path)) as msg:
                m, a = describe(msg)
                grand_msgs += m
                grand_atts += a
                if getattr(msg, "messageId", None):
                    seen_ids.append(msg.messageId)
        except Exception as exc:
            print(f"    FAILED: {type(exc).__name__}: {exc}")

    print(f"\n{'=' * 92}")
    print(f"  messages found      {grand_msgs:>6,}")
    print(f"  attachments found   {grand_atts:>6,}")
    already = sum(1 for i in seen_ids if i in known_ids)
    print(f"  top-level msg-ids   {len(seen_ids):>6,}")
    print(f"    already ingested  {already:>6,}   <- would dedup away")
    print(f"    genuinely new     {len(seen_ids) - already:>6,}")
    print()


if __name__ == "__main__":
    main()
