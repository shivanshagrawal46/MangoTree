"""
Diagnostic — does MongoDB's $text BM25 channel actually match '$450,000'?

Tests four phrasings to see which (if any) retrieve the chunks that
contain literal '$450,000'.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=True)

from api.rag_singleton import get_mongo  # noqa: E402

mongo = get_mongo()

phrasings = [
    "$450,000",
    "450,000",
    "450000",
    '"$450,000"',
    '\\"450,000\\"',
]

print("\n=== BM25 channel diagnostic ===\n")
for q in phrasings:
    try:
        cur = (
            mongo.chunks.find(
                {"$text": {"$search": q}},
                {"score": {"$meta": "textScore"}, "filename": 1, "from_email": 1, "date": 1},
            )
            .sort([("score", {"$meta": "textScore"})])
            .limit(5)
        )
        rows = list(cur)
        print(f"BM25 search='{q}'  ->  {len(rows)} hits")
        for r in rows[:3]:
            print(f"   score={r['score']:.2f}  date={r.get('date')}  "
                  f"sender={r.get('from_email')}  file={r.get('filename')}")
    except Exception as e:
        print(f"BM25 search='{q}'  ERROR: {e}")
    print()


print("=== Direct regex channel (what filename lookup uses) ===\n")
import re as _re
for q in ["$450,000", "450,000"]:
    pat = _re.escape(q)
    rows = list(mongo.chunks.find(
        {"body": {"$regex": pat, "$options": "i"}},
        {"filename": 1, "from_email": 1, "date": 1},
    ).limit(10))
    print(f"REGEX body~='{q}' -> {len(rows)} hits (showing 5)")
    for r in rows[:5]:
        print(f"   date={r.get('date')}  sender={r.get('from_email')}  file={r.get('filename')}")
    print()
