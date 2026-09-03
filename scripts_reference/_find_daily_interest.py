"""Find chunks containing the exact daily-interest figures Claude flagged."""
import sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper

PAT = re.compile(r"(3[,.]?225[.,]?50|672[.,]?33|3225\.|672\.33)", re.IGNORECASE)

s = Settings.load()
m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
m.ping()

hits = []
for ch in m.chunks.find({"source_type": "attachment"}, {
    "filename": 1, "extraction_method": 1, "ocr_confidence": 1,
    "page_start": 1, "body": 1, "sha256": 1,
}):
    body = ch.get("body") or ""
    if PAT.search(body):
        hits.append({
            "fn": ch.get("filename"),
            "method": ch.get("extraction_method"),
            "conf": ch.get("ocr_confidence"),
            "page": ch.get("page_start"),
            "sha": ch.get("sha256")[:8],
            "snippet": _snip(body) if (_snip := None) else None,
        })

if not hits:
    print("No chunks contained $3,225.50 or $672.33 — these are calculated daily amounts derived from the principal.")
    print("Searching for the source numbers and rates instead...")

    PAT2 = re.compile(r"(\$3,?225|\$672|18%|interest at default|per\s*diem)", re.IGNORECASE)
    seen = set()
    for ch in m.chunks.find({"source_type": "attachment"}, {
        "filename": 1, "extraction_method": 1, "ocr_confidence": 1,
        "page_start": 1, "body": 1, "sha256": 1,
    }):
        body = (ch.get("body") or "")
        if not PAT2.search(body):
            continue
        sha = ch.get("sha256")
        if sha in seen:
            continue
        seen.add(sha)
        method = ch.get("extraction_method")
        conf = ch.get("ocr_confidence")
        is_ocr = method and "ocr" in method
        if not is_ocr:
            continue
        match = PAT2.search(body)
        start = max(0, match.start() - 100)
        end = min(len(body), match.end() + 150)
        snippet = body[start:end].replace("\n", " | ")
        print(f"\n→ {ch.get('filename')[:60]}")
        print(f"  method={method}  conf={conf:.3f}  page={ch.get('page_start')}")
        print(f"  snippet: …{snippet}…")
else:
    for h in hits:
        print(h)
