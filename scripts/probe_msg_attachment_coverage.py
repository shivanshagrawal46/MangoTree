"""The .msg emails were already ingested — but is their *evidence* here?

An email arriving via the Graph API brings its attachments with it, so in
principle nothing is missing. That is worth proving rather than assuming: the
documents inside these seven messages are change orders, mortgage statements and
a preliminary ALTA, and a gap there would be invisible from the email alone.

Checks each attachment named in the .msg against the corpus by filename, and
reports whether it was extracted and indexed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")

import extract_msg

from mangotree.storage.mongo import get_mongo

ROOT = Path(r"E:\LP Remodeling Projects\Hold Properties")


def main() -> None:
    mongo = get_mongo()
    art = mongo.artifacts

    total = present = extracted = indexed = missing = 0
    gaps = []

    for path in sorted(ROOT.rglob("*.msg")):
        mid = None
        names = []
        with extract_msg.Message(str(path)) as msg:
            mid = (getattr(msg, "messageId", None) or "").strip() or None
            for att in msg.attachments:
                if isinstance(att.data, extract_msg.msg_classes.msg.MSGFile):
                    continue
                names.append(att.getFilename() or "")

        email = art.find_one({"internet_message_id": mid}, {"sha256": 1, "subject": 1})
        print(f"\n  {path.name[:70]}")
        print(f"    in corpus   {'yes  ' + email['sha256'][:12] if email else 'NO'}")
        if not email:
            continue

        children = list(art.find(
            {"parent_email_shas": email["sha256"]},
            {"filename": 1, "sha256": 1, "extraction": 1, "property_ids": 1},
        ))
        by_name = {(c.get("filename") or "").lower(): c for c in children}
        print(f"    attachments stored for this email: {len(children)}")

        for name in names:
            total += 1
            hit = by_name.get(name.lower())
            if not hit:
                missing += 1
                gaps.append((path.name, name))
                print(f"      MISSING   {name[:56]}")
                continue
            present += 1
            status = (hit.get("extraction") or {}).get("status")
            n_chunks = mongo.chunks.count_documents({"artifact_sha": hit["sha256"]})
            if status == "complete":
                extracted += 1
            if n_chunks:
                indexed += 1
            print(f"      ok  {str(status):<9} {n_chunks:>3} chunks  {name[:46]}")

    print(f"\n  {'=' * 60}")
    print(f"    attachments in .msg files   {total:>4}")
    print(f"    present in corpus           {present:>4}")
    print(f"    extraction complete         {extracted:>4}")
    print(f"    produced chunks             {indexed:>4}")
    print(f"    MISSING                     {missing:>4}")
    for src, name in gaps:
        print(f"      {src[:40]} -> {name[:44]}")
    print()


if __name__ == "__main__":
    main()
