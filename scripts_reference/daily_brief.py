"""
Daily Brief generator (Sprint 5) — READ-ONLY.

Each run produces a single morning briefing from the live corpus:
  * What arrived recently (new emails in the window)
  * Approaching deadlines (dates-with-consequence within N days)
  * Open loops (asks waiting on us)
  * New / pending findings (contradictions, anachronisms, money conflicts,
    voidable transfers, quoted alterations)
  * "Questions you should ask next" — a prioritized, evidence-tied list

Anchored to an "as-of" date (default = the latest email in the corpus, so
deadlines are relative to the corpus's own present). Writes nothing to the
DB; emits a Markdown brief (+ optional JSON). Intended to be run nightly
(cron / Task Scheduler) — the scheduling is just a wrapper around this.

Usage:
  python scripts/daily_brief.py
  python scripts/daily_brief.py --arrival-days 7 --deadline-days 21
  python scripts/daily_brief.py --as-of 2026-07-02 --out brief.md
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.deadline_radar import extract_deadlines, upcoming


def _aware(dt):
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def build_brief(db, *, arrival_days: int = 7, deadline_days: int = 21,
                as_of=None) -> dict:
    """Assemble the Daily Brief as STRUCTURED data (reused by the CLI and the
    /api/brief endpoint). Read-only."""
    from collections import Counter as _Counter
    emails, findings = db["emails"], db["findings"]

    if as_of is None:
        latest = list(emails.find({"date": {"$ne": None}}, {"date": 1})
                      .sort("date", -1).limit(1))
        as_of = _aware(latest[0]["date"]) if latest else datetime.now(timezone.utc)
    as_of = _aware(as_of)
    arrival_cut = as_of - timedelta(days=arrival_days)
    scan_cut = as_of - timedelta(days=30)

    arrivals = list(emails.find(
        {"date": {"$gte": arrival_cut, "$lte": as_of}},
        {"subject": 1, "from": 1, "date": 1}).sort("date", -1))

    dls, seen_dl = [], set()
    for e in emails.find({"date": {"$gte": scan_cut, "$lte": as_of}},
                         {"body_text": 1}):
        for d in extract_deadlines(e.get("body_text") or "", today=as_of.date()):
            k = (d.when, d.consequence)
            if k in seen_dl:
                continue
            seen_dl.add(k)
            dls.append(d)
    up = sorted(upcoming(dls, within_days=deadline_days), key=lambda x: x.when)

    open_loops = list(findings.find({"finding_type": "open_loop", "status": "pending"}))
    pending = list(findings.find({"status": "pending"}))
    by_type = _Counter(f.get("finding_type") for f in pending)

    questions = []
    for d in up[:4]:
        questions.append(f"What is our plan for {d.consequence} on {d.when.isoformat()} "
                         f"({d.days_out} days out)?")
    for f in open_loops[:4]:
        questions.append("Respond to: " + (f.get("title", "").replace("Unanswered request: ", "")))
    for f in [x for x in pending if x.get("finding_type") == "money_conflict"][:2]:
        questions.append(f"Reconcile: {f.get('title')}")
    for f in [x for x in pending if x.get("finding_type") in
              ("contradiction", "anachronism", "voidable_transfer", "llc_timing",
               "insurance_cancellation")][:3]:
        questions.append(f"Address the {f.get('finding_type')}: {f.get('title')}")

    def _ds(dt):
        return dt.date().isoformat() if hasattr(dt, "date") else str(dt or "")

    return {
        "as_of": as_of.date().isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "deadlines": [{"when": d.when.isoformat(), "days_out": d.days_out,
                       "consequence": d.consequence, "sentence": d.sentence[:200]}
                      for d in up],
        "open_loops": [{"id": f.get("_id"), "title": f.get("title")}
                       for f in open_loops[:20]],
        "arrivals": [{"date": _ds(e.get("date")),
                      "from": (e.get("from") or {}).get("email") or "?",
                      "subject": e.get("subject") or "(no subject)"}
                     for e in arrivals[:20]],
        "findings_by_type": dict(by_type.most_common()),
        "questions": questions[:8],
        "arrival_days": arrival_days,
        "deadline_days": deadline_days,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arrival-days", type=int, default=7)
    ap.add_argument("--deadline-days", type=int, default=21)
    ap.add_argument("--as-of", default="", help="YYYY-MM-DD (default: latest email date)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    db = m.db

    as_of = (datetime.strptime(args.as_of, "%Y-%m-%d").replace(tzinfo=timezone.utc)
             if args.as_of else None)
    b = build_brief(db, arrival_days=args.arrival_days,
                    deadline_days=args.deadline_days, as_of=as_of)
    m.close()

    # ---- render markdown from the structured brief ----
    L = [f"# Daily Brief - as of {b['as_of']}",
         f"_Generated {b['generated_at']} - read-only_\n",
         f"## Approaching deadlines (next {b['deadline_days']} days)"]
    if b["deadlines"]:
        for d in b["deadlines"]:
            L.append(f"- {d['when']} ({d['days_out']}d) - {d['consequence']}: {d['sentence'][:160]}")
    else:
        L.append("- none detected in window")

    L.append("\n## Waiting on us (open loops)")
    L += [f"- {f['title']}" for f in b["open_loops"][:12]] or ["- none"]

    L.append(f"\n## New arrivals (last {b['arrival_days']} days): {len(b['arrivals'])}")
    for e in b["arrivals"][:12]:
        L.append(f"- {e['date']} - {e['from']} - {e['subject']}")

    L.append("\n## Findings ledger (pending)")
    for t, n in b["findings_by_type"].items():
        L.append(f"- {t}: {n}")

    L.append("\n## Questions you should ask next")
    for i, q in enumerate(b["questions"], 1):
        L.append(f"{i}. {q}")

    brief = "\n".join(L)
    print(brief.encode("ascii", "replace").decode("ascii"))

    out = args.out or f"_daily_brief_{b['as_of']}.md"
    Path(out).write_text(brief, encoding="utf-8")
    print(f"\n[brief written to {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
