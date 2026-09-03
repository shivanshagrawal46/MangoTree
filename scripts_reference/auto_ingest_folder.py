"""Autonomous incremental ingestion for a Gmail label ("folder").

This is the hands-off version of the manual pipeline we run for the
"Boris Lawsuit" folder. One invocation = one full, idempotent pass:

    1. Find the checkpoint (latest email already in DB for the label).
    2. Live-pull anything newer from Gmail (3-way dedup; incoming AND
       outgoing that carry the label).
    3. If nothing new  -> exit fast (~30s), touch nothing.
    4. If new mail:
         - force-vision OCR the new attachments (Claude Sonnet 4.6 ->
           GPT-5 -> RapidOCR),
         - chunk + contextual summary (Sonnet 4.6) + embed (Voyage),
         - enrichment chain scoped to the new chunks: authority score,
           corpus/privilege tag, entity linkage,
         - verify field parity and log a one-line summary.

Designed to be run on a schedule (e.g. Windows Task Scheduler every N
minutes). A lock file prevents overlapping runs. Safe to run forever.

Usage:
    python scripts/auto_ingest_folder.py --label "__....Boris Lawsuit"

Exit codes: 0 = success (including "nothing new"); 1 = a step failed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import Settings                     # noqa: E402
from src.db.mongo import MongoClientWrapper              # noqa: E402
from src.graph.schema import authority_for, DEFAULT_AUTHORITY  # noqa: E402
from src.utils.logger import logger                      # noqa: E402

PY = sys.executable
LOG_DIR = REPO / "logs"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "folder"


def _run(cmd: list[str], step: str) -> None:
    """Run a sub-step, streaming output; raise on non-zero exit."""
    logger.info(f"[auto] >>> {step}: {' '.join(cmd[1:])}")
    res = subprocess.run(cmd, cwd=str(REPO))
    if res.returncode != 0:
        raise RuntimeError(f"step '{step}' failed (exit {res.returncode})")


def _summary_log(slug: str, line: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / f"auto_ingest_{slug}.log", "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


class Lock:
    """Simple stale-tolerant file lock so poll runs never overlap."""

    def __init__(self, slug: str, timeout_min: int):
        self.path = REPO / f".auto_ingest_{slug}.lock"
        self.timeout = timeout_min * 60
        self.acquired = False

    def acquire(self) -> bool:
        if self.path.exists():
            age = time.time() - self.path.stat().st_mtime
            if age < self.timeout:
                return False
            logger.warning(f"[auto] stale lock ({age/60:.0f} min) — stealing.")
        self.path.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}")
        self.acquired = True
        return True

    def release(self) -> None:
        if self.acquired:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--label", default="__....Boris Lawsuit",
                    help='Gmail label/folder to keep in sync.')
    ap.add_argument("--privilege", default=None,
                    help="Privilege override at pull (e.g. adverse_party for David folders).")
    ap.add_argument("--lookback-days", type=int, default=2,
                    help="Overlap window before checkpoint (dedup absorbs it).")
    ap.add_argument("--ocr-workers", type=int, default=3)
    ap.add_argument("--build-workers", type=int, default=8)
    ap.add_argument("--lock-timeout-min", type=int, default=45)
    args = ap.parse_args()

    slug = _slug(args.label)
    lock = Lock(slug, args.lock_timeout_min)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not lock.acquire():
        logger.info("[auto] another run is active — skipping this poll.")
        return 0

    try:
        s = Settings.load()
        mongo = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
        mongo.ping()
        em = mongo.emails
        av2 = mongo.db["attachments_v2"]
        ch = mongo.db["email_chunks_v2"]

        base_q = {"source.origin": "gmail_api", "gmail_labels": args.label}

        # 1) checkpoint --------------------------------------------------
        last = list(em.find(base_q, {"date": 1}).sort("date", -1).limit(1))
        if last and last[0].get("date"):
            after = (last[0]["date"] - timedelta(days=args.lookback_days)).strftime("%Y-%m-%d")
        else:
            after = None
        logger.info(f"[auto] label={args.label!r} checkpoint_after={after}")

        # scope marker: anything ingested from here on belongs to this run
        run_start = datetime.now(timezone.utc) - timedelta(minutes=1)

        # 2) live pull ---------------------------------------------------
        pull_cmd = [PY, "-m", "scripts.ingest_gmail", "pull",
                    "--label", args.label, "--live"]
        if after:
            pull_cmd += ["--after", after]
        if args.privilege:
            pull_cmd += ["--privilege", args.privilege]
        _run(pull_cmd, "gmail-pull")

        # 3) what did THIS run insert? ----------------------------------
        new_q = {**base_q, "ingested_at": {"$gte": run_start}}
        new_emails = list(em.find(new_q, {"_id": 1, "attachment_ids": 1, "date": 1}))
        n_new = len(new_emails)
        logger.info(f"[auto] new emails this run: {n_new}")

        if n_new == 0:
            latest = last[0]["date"] if last else None
            _summary_log(slug, f"{ts} | label={slug} | new_emails=0 | latest={latest} | NOOP")
            logger.info("[auto] nothing new — done.")
            mongo.close()
            return 0

        # 4) heavy pipeline ---------------------------------------------
        _run([PY, "scripts/ocr_attachments_v2.py", "--force-vision",
              "--workers", str(args.ocr_workers)], "force-vision-ocr")
        _run([PY, "scripts/build_email_chunks_v2.py",
              "--workers", str(args.build_workers)], "chunk-embed")

        # scope for enrichment
        eids = [e["_id"] for e in new_emails]
        att_ids = [aid for e in new_emails for aid in (e.get("attachment_ids") or [])]
        shas = sorted({a["sha256"] for a in av2.find(
            {"_id": {"$in": att_ids}}, {"sha256": 1})})
        scope = {"$or": [
            {"source_type": "attachment", "sha256": {"$in": shas}},
            {"source_type": "email_body", "email_id": {"$in": eids}},
        ]}

        # 4a) authority (scoped, matches global values) ------------------
        n_auth = 0
        for st in ("attachment", "email_body"):
            r = ch.update_many(
                {**scope, "source_type": st, "doc_source_type": {"$exists": False}},
                {"$set": {"doc_authority_score": authority_for(st)}})
            n_auth += r.modified_count
        ch.update_many({**scope, "doc_authority_score": {"$exists": False}},
                       {"$set": {"doc_authority_score": DEFAULT_AUTHORITY}})
        logger.info(f"[auto] authority stamped on {n_auth} new chunks")

        # 4b) corpus / privilege ----------------------------------------
        _run([PY, "-m", "scripts.tag_chunk_corpus"], "tag-corpus")

        # 4c) entity linkage (scoped) -----------------------------------
        all_keys = shas + [f"email:{e}" for e in eids]
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         dir=str(REPO), encoding="utf-8") as tf:
            tf.write("\n".join(all_keys) + "\n")
            sha_file = tf.name
        try:
            _run([PY, "-m", "scripts.backfill_chunk_entities",
                  "--sha-file", sha_file], "entity-backfill")
        finally:
            try:
                os.unlink(sha_file)
            except OSError:
                pass

        # 5) verify parity ----------------------------------------------
        total = ch.count_documents(scope)
        req = ["corpus", "privilege_status", "evidentiary_class",
               "doc_authority_score", "entity_ids", "entity_refs",
               "touches_david", "occurrences", "entity_backfill_at"]
        gaps = []
        for f in req:
            if ch.count_documents({**scope, f: {"$exists": True}}) != total:
                gaps.append(f)
        if ch.count_documents({**scope, "context": {"$nin": [None, ""]}}) != total:
            gaps.append("context")
        if ch.count_documents({**scope, "embedding.0": {"$exists": True}}) != total:
            gaps.append("embedding")
        linked = ch.count_documents({**scope, "entity_ids.0": {"$exists": True}})

        latest = list(em.find(base_q, {"date": 1, "subject": 1})
                      .sort("date", -1).limit(1))
        latest_date = latest[0]["date"] if latest else None
        status = "OK" if not gaps else f"GAPS:{','.join(gaps)}"
        line = (f"{ts} | label={slug} | new_emails={n_new} att_shas={len(shas)} "
                f"chunks={total} linked={linked} latest={latest_date} | {status}")
        _summary_log(slug, line)
        logger.info(f"[auto] {line}")
        mongo.close()
        return 0 if not gaps else 1

    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[auto] run failed: {exc}")
        _summary_log(slug, f"{ts} | label={slug} | ERROR: {exc}")
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
