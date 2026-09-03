"""
Parse ORIGINAL message dates onto quoted chunks (Sprint 2 follow-up).

Quoted chunks are currently dated by the email they were FOUND in. For a
forensic timeline, a 2024 message forwarded in 2026 should sit at its
ORIGINAL date. This parses `quoted_original.date_text` into a real date and:
  * stores `original_date` (+ date_source)
  * preserves the found-in date as `found_in_date`
  * sets the chunk's primary date/date_ym/date_year to the ORIGINAL when
    confidently parsed (else leaves the found-in date untouched)

Scoped to source_batch=quoted_recovery_v1. Idempotent. Reversible: re-run
or clear original_date. Read `date_text` -> write date fields only.

Usage:  python scripts/backfill_quoted_dates.py [--dry-run]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pymongo import UpdateOne

from config.settings import Settings
from src.db.mongo import MongoClientWrapper

BATCH = "quoted_recovery_v1"

try:
    from dateutil import parser as _dtp
    _HAS_DU = True
except ImportError:
    _HAS_DU = False


def parse_date(text: str):
    if not text:
        return None
    t = text.replace(" at ", " ").strip()
    t = re.sub(r"\s+", " ", t)
    dt = None
    if _HAS_DU:
        try:
            dt = _dtp.parse(t, fuzzy=True)
        except (ValueError, OverflowError):
            dt = None
    if dt is None:
        # minimal fallback: "Month DD, YYYY"
        m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", t)
        if m:
            months = {mn.lower(): i for i, mn in enumerate(
                ["", "january", "february", "march", "april", "may", "june",
                 "july", "august", "september", "october", "november", "december"])}
            mon = months.get(m.group(1).lower())
            if not mon:
                short = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,
                         "aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
                mon = short.get(m.group(1)[:3].lower())
            if mon:
                try:
                    dt = datetime(int(m.group(3)), mon, int(m.group(2)))
                except ValueError:
                    dt = None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # sanity window — reject absurd parses
    if not (1990 <= dt.year <= 2027):
        return None
    return dt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ch = m.db["email_chunks_v2"]
    cur = ch.find({"source_batch": BATCH},
                  {"_id": 1, "quoted_original": 1, "date": 1})
    ops = []
    parsed = 0
    total = 0
    for d in cur:
        total += 1
        dtext = (d.get("quoted_original") or {}).get("date_text")
        od = parse_date(dtext or "")
        if od is None:
            continue
        # SANITY: a quoted message cannot post-date the email it was quoted
        # in. If it does, the "date" was mis-extracted from body content (e.g.
        # a deadline mentioned in the text), so we DON'T promote it — keep the
        # found-in date and skip. (Guards against future-dating the timeline.)
        found_dt = d.get("date")
        if isinstance(found_dt, datetime):
            fd = found_dt if found_dt.tzinfo else found_dt.replace(tzinfo=timezone.utc)
            from datetime import timedelta
            if od > fd + timedelta(days=1):
                continue
        parsed += 1
        set_fields = {
            "original_date": od,
            "date_source": "quoted_original",
            "found_in_date": d.get("date"),
            # promote original date to the primary timeline fields
            "date": od,
            "latest_date": od,
            "date_ym": od.strftime("%Y-%m"),
            "date_year": od.year,
            "date_month": od.month,
        }
        ops.append(UpdateOne({"_id": d["_id"]}, {"$set": set_fields}))

    print(f"quoted chunks: {total:,} | parseable original dates: {parsed:,} "
          f"({100*parsed/max(total,1):.1f}%)")
    if args.dry_run:
        print("DRY-RUN: no writes. Sample of parsed dates:")
        for op in ops[:5]:
            print("   ", op._doc["$set"]["original_date"].date(),
                  "<- found_in", op._doc["$set"]["found_in_date"])
        m.close()
        return 0
    written = 0
    for i in range(0, len(ops), 500):
        ch.bulk_write(ops[i:i+500], ordered=False)
        written += len(ops[i:i+500])
    print(f"updated {written:,} quoted chunks with original dates.")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
