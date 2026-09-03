"""
Decode RFC 2047 encoded subjects on existing emails + chunks (Sprint 2).

Wires the built decoder (src.cleaner.headers.decode_mime_header) to the
live data: any stored `subject` still in =?utf-8?..?= form is decoded to
plain text. Updates the `subject` metadata field on `emails` and
`email_chunks_v2` (helps display + BM25). Idempotent; only touches rows
whose subject is still encoded.

NOTE: the chunk's embedded TEXT header also contains the old encoded
subject; that is cosmetic (already embedded) and left as-is. Re-embedding
121 chunks for a cosmetic header is not worth the cost; the searchable
`subject` field is what matters.

Usage:  python scripts/decode_subjects.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pymongo import UpdateOne

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.cleaner.headers import is_encoded_word, decode_mime_header

ENCODED_RE = r"=\?[^?]+\?[bBqQ]\?"


def _fix_collection(col, label: str, dry: bool) -> int:
    cur = col.find({"subject": {"$regex": ENCODED_RE}}, {"subject": 1})
    ops = []
    samples = []
    for d in cur:
        subj = d.get("subject") or ""
        if not is_encoded_word(subj):
            continue
        decoded = decode_mime_header(subj)
        if decoded and decoded != subj:
            ops.append(UpdateOne({"_id": d["_id"]}, {"$set": {"subject": decoded}}))
            if len(samples) < 4:
                samples.append(decoded[:80])
    print(f"  {label}: {len(ops)} encoded subjects to decode")
    for sdec in samples:
        print(f"     -> {sdec}")
    if not dry and ops:
        for i in range(0, len(ops), 500):
            col.bulk_write(ops[i:i+500], ordered=False)
    return len(ops)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db = m.db
    print(f"{'DRY-RUN' if args.dry_run else 'APPLY'}: decoding RFC 2047 subjects")
    n1 = _fix_collection(db["emails"], "emails", args.dry_run)
    n2 = _fix_collection(db["email_chunks_v2"], "email_chunks_v2", args.dry_run)
    print(f"total: {n1 + n2} subjects {'would be' if args.dry_run else ''} decoded")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
