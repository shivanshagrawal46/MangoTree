"""
Find OCR-damaged attachments that contain the specific dollar amounts
Claude flagged as 'OCR impaired' in the Confession of Judgement answer.

Looks for:
  - $6,450,990 / 6450990
  - $2,017,000 / 2017000
  - $3,225.50 (daily interest)
  - $672.33   (daily interest on Fort Hill)
Then prints the source attachment's filename, OCR method, avg confidence
and the offending text snippet so we know exactly which scanned PDFs
need re-extraction.
"""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


PATTERNS = [
    r"6[ ,]?450[ ,]?990",
    r"2[ ,]?017[ ,]?000",
    r"3[ ,]?225\.50",
    r"672\.33",
    r"8[ ,]?591[ ,]?948",
    r"7[ ,]?097[ ,]?514",
    r"9[ ,]?238[ ,]?472",
    r"confession of judg",
    r"event of default",
]
COMBO = re.compile("|".join(PATTERNS), re.IGNORECASE)


def main() -> int:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    # Pull every chunk that mentions any of the COJ-related numbers/phrases
    # and join back to its attachment record.
    matched = {}  # sha256 -> info
    for ch in m.chunks.find({"source_type": "attachment"}, {
        "sha256": 1, "filename": 1, "extraction_method": 1, "ocr_confidence": 1,
        "page_start": 1, "page_end": 1, "body": 1, "date": 1
    }):
        body = (ch.get("body") or "")[:5000]
        if not COMBO.search(body):
            continue
        sha = ch["sha256"]
        prev = matched.get(sha)
        cur = {
            "sha256": sha,
            "filename": ch.get("filename"),
            "method": ch.get("extraction_method"),
            "ocr_conf": ch.get("ocr_confidence"),
            "page_start": ch.get("page_start"),
            "page_end": ch.get("page_end"),
            "date": ch.get("date"),
            "snippet": _snip(body, COMBO),
        }
        # Keep the one with lowest OCR confidence so we surface the worst page first
        if not prev or (cur["ocr_conf"] is not None and (prev["ocr_conf"] is None or cur["ocr_conf"] < prev["ocr_conf"])):
            matched[sha] = cur

    if not matched:
        print("No COJ-related chunks found. (Surprising — check patterns)")
        return 0

    # Sort: OCR-extracted ones first, sorted by lowest confidence
    def sort_key(d):
        method = d["method"] or ""
        is_ocr = "ocr" in method
        conf = d["ocr_conf"] if d["ocr_conf"] is not None else 1.0
        return (not is_ocr, conf)

    rows = sorted(matched.values(), key=sort_key)

    print(f"Found {len(rows)} attachments containing COJ amounts / phrases.\n")
    print("=" * 100)
    print(f"{'#':>3}  {'method':<11}  {'OCR conf':>9}  {'pages':>7}  {'date':<11}  filename")
    print("=" * 100)
    for i, d in enumerate(rows, 1):
        conf = f"{d['ocr_conf']:.3f}" if d["ocr_conf"] is not None else "    —"
        date_s = d["date"].strftime("%Y-%m-%d") if d["date"] else "—"
        pages = f"p.{d['page_start']}" if d["page_start"] else "—"
        fname = (d["filename"] or "—")[:55]
        print(f"{i:>3}  {d['method']:<11}  {conf:>9}  {pages:>7}  {date_s:<11}  {fname}")

    # Show snippets for the worst 5 (likely OCR-damaged ones)
    print("\n" + "=" * 100)
    print("WORST 5 OCR-DAMAGED SNIPPETS (these are what Claude flagged):")
    print("=" * 100)
    bad = [d for d in rows if d["method"] and "ocr" in d["method"]][:5]
    if not bad:
        # Fallback: just show first 3 chunks
        bad = rows[:3]
    for i, d in enumerate(bad, 1):
        print(f"\n— file [{i}]: {d['filename']}")
        print(f"  method: {d['method']}  |  OCR conf: {d['ocr_conf']}")
        print(f"  snippet: {d['snippet']!r}")

    return 0


def _snip(text: str, pat) -> str:
    m = pat.search(text)
    if not m:
        return text[:120]
    start = max(0, m.start() - 60)
    end = min(len(text), m.end() + 60)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


if __name__ == "__main__":
    raise SystemExit(main())
