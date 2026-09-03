"""
Forensic MongoDB scan — find every chunk containing a literal substring.

Used to verify whether retrieval missed something. NOT a part of the
runtime RAG pipeline; this is a debugging utility.

Usage:
  python scripts/forensic_lookup.py "$450,000"
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)

from api.rag_singleton import get_mongo  # noqa: E402


def main() -> int:
    needle = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "$450,000"

    mongo = get_mongo()

    # Try both with-$ and without-$ variants when relevant.
    needles = [needle]
    if needle.startswith("$"):
        needles.append(needle[1:])
    elif re.match(r"^\d", needle):
        needles.append(f"${needle}")

    print(f"\nScanning every chunk in `email_chunks` for any of: {needles}\n")

    seen_ids: set = set()
    rows = []
    for n in needles:
        # Escape regex metacharacters but allow literal `$`.
        pat = re.escape(n)
        cursor = mongo.chunks.find(
            {"$or": [
                {"body": {"$regex": pat, "$options": "i"}},
                {"text": {"$regex": pat, "$options": "i"}},
            ]},
            {
                "_id": 1, "filename": 1, "from_email": 1, "date": 1,
                "subject": 1, "body": 1, "text": 1, "chunk_index": 1,
            },
        )
        for d in cursor:
            cid = str(d.get("_id"))
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            rows.append(d)

    if not rows:
        print(f"  No chunk contains '{needle}'. Confirmed absence.\n")
        return 0

    rows.sort(key=lambda d: (d.get("date") or "", d.get("filename") or ""))
    print(f"  Found {len(rows)} chunks. Listing each with the matching context:\n")
    for i, d in enumerate(rows, start=1):
        date_s = d.get("date").isoformat() if d.get("date") else "-"
        sender = d.get("from_email") or "-"
        fname = d.get("filename") or "(email body)"
        ci = d.get("chunk_index", "?")
        body = d.get("body") or d.get("text") or ""
        # Find the matching substring with ~80 chars of context on each side.
        snippet = ""
        for n in needles:
            m = re.search(re.escape(n), body, re.IGNORECASE)
            if m:
                start = max(0, m.start() - 80)
                end = min(len(body), m.end() + 80)
                snippet = body[start:end].replace("\n", " ").replace("\r", "")
                snippet = re.sub(r"\s+", " ", snippet).strip()
                break

        print(f"[{i:>2}] {date_s}  sender={sender}")
        print(f"     file={fname}  chunk_index={ci}")
        print(f"     ...{snippet}...")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
