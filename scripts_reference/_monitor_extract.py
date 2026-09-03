"""Compact one-line status of attachment extraction."""
from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings
from src.db.mongo import MongoClientWrapper


def main(pid: Optional[str] = None) -> int:
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    m.ping()

    unique_total = len(m.attachments.distinct("sha256", {"sha256": {"$exists": True, "$ne": None}}))
    unique_extracted = len(m.attachments.distinct(
        "sha256",
        {"sha256": {"$exists": True, "$ne": None}, "extraction.method": {"$exists": True}},
    ))

    five_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
    fifteen_ago = datetime.datetime.utcnow() - datetime.timedelta(minutes=15)

    last5_unique = len(m.attachments.distinct(
        "sha256", {"extraction.extracted_at": {"$gte": five_ago}}
    ))
    last15_unique = len(m.attachments.distinct(
        "sha256", {"extraction.extracted_at": {"$gte": fifteen_ago}}
    ))

    last_doc = m.attachments.find_one(
        {"extraction.extracted_at": {"$exists": True}},
        sort=[("extraction.extracted_at", -1)],
    )
    last_ts = last_doc["extraction"]["extracted_at"] if last_doc else None
    last_method = last_doc["extraction"].get("method", "?") if last_doc else "?"
    last_filename = (last_doc.get("filename") or "?")[:40] if last_doc else "?"

    pct = (100.0 * unique_extracted / unique_total) if unique_total else 0
    eta_min = ((unique_total - unique_extracted) / max(last5_unique / 5.0, 0.1)) if last5_unique else float("inf")

    process_alive = "?"
    if pid:
        try:
            import psutil
            process_alive = "alive" if psutil.pid_exists(int(pid)) else "dead"
        except Exception:
            process_alive = "?"

    print(
        f"  unique={unique_extracted}/{unique_total} ({pct:.1f}%)  "
        f"last5m=+{last5_unique}  last15m=+{last15_unique}  "
        f"last_extract={last_ts.strftime('%H:%M:%S') if last_ts else '-'}  "
        f"({last_method}: {last_filename})  "
        f"eta~{eta_min:.0f}min"
        + (f"  proc={process_alive}" if pid else "")
    )
    m.close()
    return 0


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else None
    raise SystemExit(main(pid))
