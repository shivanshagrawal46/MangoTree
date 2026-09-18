"""Ingest a meeting transcript through the standard chain.

    python scripts/ingest_transcript.py "Call notes with Wes Sept 15.docx" \
        --title "Brentford deal underwriting, project updates, and system automation with Wes" \
        --date 2026-09-15 --with wes --by shivansh

Stores the file, extracts the text, has Opus 5 decide which properties the call
covers (a call can land on several), then chunks / contexts / questions / embeds,
writes the document-summary vector and the timeline events. The 2 a.m. cycle
picks it up from there (placed_at) for resolution, tasks, cards and Wes issues.
Safe to re-run: every stage selects its own pending work.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from mangotree.ingest.transcript import TranscriptIngestor  # noqa: E402
from mangotree.storage.mongo import get_mongo  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--title", required=True)
    ap.add_argument("--date", required=True, help="meeting date, YYYY-MM-DD")
    ap.add_argument("--with", dest="others", default="wes", help="comma-separated person_ids besides rakesh")
    ap.add_argument("--by", default="admin")
    ap.add_argument("--source", default="manual", help="manual | granola | fireflies …")
    ap.add_argument("--note", default=None)
    ap.add_argument("--by-headings", action="store_true",
                    help="force placement by the document's property headings (detected automatically when two or more are present)")
    args = ap.parse_args()

    path = Path(args.file)
    data = path.read_bytes()
    when = datetime.strptime(args.date, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
    participants = ["rakesh"] + [p.strip() for p in args.others.split(",") if p.strip()]

    mongo = get_mongo()
    ing = TranscriptIngestor(mongo)
    stored = ing.store(data, filename=path.name, title=args.title, meeting_date=when,
                       participants=participants, added_by=args.by, source=args.source, note=args.note)
    print("stored:", json.dumps(stored, default=str))

    def emit(kind, payload):
        print(f"[{datetime.now():%H:%M:%S}] {payload.get('text') if isinstance(payload, dict) else payload}", flush=True)

    trace = ing.process(stored["sha256"], emit=emit, by_headings=args.by_headings)
    print(json.dumps(trace, default=str, indent=2))
    return 0 if not trace.get("error") and not trace.get("segregation_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
