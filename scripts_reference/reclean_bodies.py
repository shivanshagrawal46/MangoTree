"""
Re-run the body cleaner over every email already in MongoDB and update the
`body_text` field with the freshly cleaned version.

Processes in CHUNKS so progress is visible in the log every few seconds.
The original raw body (`body_text_raw`) and HTML (`body_html`) are NEVER
modified — only `body_text` is recomputed.

Usage:
    python scripts/reclean_bodies.py
    python scripts/reclean_bodies.py --dry-run            # report only
    python scripts/reclean_bodies.py --sample 50          # only N emails
    python scripts/reclean_bodies.py --chunk-size 100     # tune batch size
"""
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import UpdateOne

from config.settings import Settings
from src.cleaner import clean_email_body, html_to_text
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger


def _resolve_raw(doc: dict) -> tuple[str, bool]:
    """Return (raw_plaintext, was_freshly_converted_from_html).

    If body_text_raw is missing, we convert body_html once and tell the caller
    to persist the result so the next reclean run is ~100x faster.
    """
    raw = doc.get("body_text_raw") or ""
    if raw:
        return raw, False
    html = doc.get("body_html") or ""
    if not html:
        return "", False
    return html_to_text(html), True


def _new_clean_body(raw: str) -> str:
    return clean_email_body(raw, strip_quotes=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-clean all email bodies in MongoDB.")
    parser.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    parser.add_argument("--sample", type=int, default=None, help="Only process N emails")
    parser.add_argument("--chunk-size", type=int, default=100, help="Emails per chunk (default 100)")
    args = parser.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)
    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)

    try:
        mongo.ping()

        total = mongo.emails.count_documents({})
        if args.sample:
            total = min(total, args.sample)
        logger.info(f"Re-cleaning {total:,} emails in chunks of {args.chunk_size} (dry-run={args.dry_run})")

        # Collect all _ids first (cheap) so we can iterate in stable order
        # without holding an open cursor across slow regex work.
        logger.info("Fetching email ids…")
        all_ids = [d["_id"] for d in mongo.emails.find({}, {"_id": 1}).sort([("date", 1)])]
        if args.sample:
            all_ids = all_ids[: args.sample]
        logger.info(f"Got {len(all_ids):,} ids.")

        n_changed = 0
        n_unchanged = 0
        n_emptied = 0
        chars_before = 0
        chars_after = 0
        start = time.time()

        chunk_size = max(1, args.chunk_size)
        chunks = (len(all_ids) + chunk_size - 1) // chunk_size

        for chunk_idx in range(chunks):
            chunk_ids = all_ids[chunk_idx * chunk_size: (chunk_idx + 1) * chunk_size]
            t0 = time.time()

            docs = list(mongo.emails.find(
                {"_id": {"$in": chunk_ids}},
                projection={
                    "body_text": 1, "body_text_raw": 1, "body_html": 1,
                },
            ))

            updates: list[UpdateOne] = []
            chunk_changed = 0
            chunk_emptied = 0

            # Parallel HTML->text for docs that don't have body_text_raw yet.
            # BeautifulSoup releases the GIL during parsing so threads help.
            html_jobs = [
                (i, d.get("body_html") or "")
                for i, d in enumerate(docs)
                if not (d.get("body_text_raw") or "") and (d.get("body_html") or "")
            ]
            converted: dict[int, str] = {}
            if html_jobs:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    for (i, _), text in zip(
                        html_jobs,
                        pool.map(html_to_text, [j[1] for j in html_jobs]),
                    ):
                        converted[i] = text

            for doc_idx, doc in enumerate(docs):
                old = doc.get("body_text") or ""
                try:
                    raw = doc.get("body_text_raw") or ""
                    freshly_from_html = False
                    if not raw and doc_idx in converted:
                        raw = converted[doc_idx]
                        freshly_from_html = bool(raw)
                    new = _new_clean_body(raw)
                except Exception as exc:
                    logger.warning(f"Skip email {doc['_id']}: cleaner crashed ({exc})")
                    new = old
                    raw = doc.get("body_text_raw") or ""
                    freshly_from_html = False
                chars_before += len(old)
                chars_after += len(new)

                # Always cache the HTML→text conversion so future reclean runs
                # don't re-parse 20KB of HTML per message.
                set_fields: dict = {}
                if freshly_from_html and raw:
                    set_fields["body_text_raw"] = raw

                if new != old:
                    chunk_changed += 1
                    if not new.strip() and old.strip():
                        chunk_emptied += 1
                    set_fields["body_text"] = new
                    set_fields["body_text_recleaned_at"] = datetime.now(timezone.utc)
                else:
                    n_unchanged += 1

                if set_fields and not args.dry_run:
                    updates.append(UpdateOne({"_id": doc["_id"]}, {"$set": set_fields}))

            if updates and not args.dry_run:
                mongo.emails.bulk_write(updates, ordered=False)

            n_changed += chunk_changed
            n_emptied += chunk_emptied
            elapsed = time.time() - t0
            done = (chunk_idx + 1) * chunk_size
            done = min(done, len(all_ids))
            total_elapsed = time.time() - start
            rate = done / total_elapsed if total_elapsed > 0 else 0
            eta_sec = (len(all_ids) - done) / rate if rate > 0 else 0

            logger.info(
                f"chunk {chunk_idx + 1}/{chunks} ({done:,}/{len(all_ids):,}) "
                f"— changed {chunk_changed}, emptied {chunk_emptied}, "
                f"{elapsed:.1f}s/chunk, {rate:.1f} emails/s, ETA {int(eta_sec)}s"
            )

        delta = chars_before - chars_after
        pct = (delta / chars_before * 100) if chars_before else 0
        logger.info(
            f"DONE. changed={n_changed:,} unchanged={n_unchanged:,} "
            f"emptied={n_emptied:,}. "
            f"Body trimmed {delta:,} chars ({pct:.1f}% smaller). "
            f"{'(DRY-RUN)' if args.dry_run else 'Database updated.'}"
        )
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
