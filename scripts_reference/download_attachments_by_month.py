"""
Download every UNIQUE attachment from MongoDB GridFS to disk, organized by
year-month (flat: no per-email subfolders), with NO duplicates.

Layout:
    <DEST>/
        2021-06/
            invoice.pdf
            contract.pdf
            ...
        2021-07/
            ...
        _manifest.csv

Dedup strategy
--------------
Attachments are grouped by sha256.  For each unique sha256 we save the binary
exactly ONCE, in the month folder of the earliest email that carried it.
Other emails referencing the same sha256 are recorded in the manifest with
the same path so you can still trace which email sent which file.

Collision handling: if two different sha256s have the same filename in the
same month folder, the second gets `__<sha256[:8]>` appended before the
extension.

Usage:
    python scripts/download_attachments_by_month.py
    python scripts/download_attachments_by_month.py --dest "D:\\fraud_emails_attachments_monthly"
    python scripts/download_attachments_by_month.py --workers 12
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger

DEFAULT_DEST = Path(r"D:\fraud_emails_attachments_monthly")

# ---------------------------------------------------------------------------
# Filename sanitization (Windows-safe)
# ---------------------------------------------------------------------------
_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_INVISIBLE_UNICODE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]"
)
_TRIM = re.compile(r"^[\s.]+|[\s.]+$")
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_NAME_LEN = 120


def _sanitize(name: str, max_len: int = MAX_NAME_LEN) -> str:
    if not name:
        return "unnamed"
    name = _INVISIBLE_UNICODE.sub("", name)
    name = _INVALID_WIN_CHARS.sub("_", name)
    name = name.replace("\n", " ").replace("\r", " ")
    name = _TRIM.sub("", name).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        return "unnamed"
    if len(name) > max_len:
        if "." in name:
            base, ext = name.rsplit(".", 1)
            ext = ext[:10]
            name = base[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]
    if name.upper() in _RESERVED_NAMES:
        name = "_" + name
    return name


def _long_path(path: Path) -> str:
    """Windows long-path-safe representation."""
    s = str(path.resolve())
    if sys.platform.startswith("win") and not s.startswith("\\\\?\\"):
        if s.startswith("\\\\"):
            s = "\\\\?\\UNC\\" + s.lstrip("\\")
        else:
            s = "\\\\?\\" + s
    return s


def _month_folder(d) -> str:
    if isinstance(d, datetime):
        return d.strftime("%Y-%m")
    return "no-date"


# ---------------------------------------------------------------------------
# Planning: group by sha256, pick canonical (= earliest dated) email
# ---------------------------------------------------------------------------
def _build_plan(mongo: MongoClientWrapper, dest: Path):
    """Return (jobs, manifest_index, all_attachments, email_idx).

    jobs: one job per UNIQUE sha256 — the file we'll actually write.
        {sha256, gridfs_id, size_bytes, target_path, month, filename}

    manifest_index: sha256 -> manifest_master_row including 'target_path'.
        Used to record EVERY attachment row in the CSV (even duplicates that
        won't trigger a download), so the user can see which emails carried
        which file.
    """
    logger.info("Loading attachment metadata…")
    attachments = list(mongo.attachments.find(
        {},
        {"sha256": 1, "gridfs_id": 1, "size_bytes": 1, "filename": 1, "email_id": 1},
    ))
    logger.info(f"  {len(attachments):,} attachment rows loaded")

    # Pre-fetch parent emails for date / from / subject info
    email_ids = list({a["email_id"] for a in attachments if a.get("email_id")})
    logger.info(f"Loading {len(email_ids):,} parent emails…")
    emails = mongo.emails.find(
        {"_id": {"$in": email_ids}},
        {"date": 1, "date_sent": 1, "date_received": 1,
         "from": 1, "subject": 1, "pst_entry_id": 1},
    )
    email_idx = {e["_id"]: e for e in emails}
    logger.info(f"  {len(email_idx):,} emails indexed")

    # Group by sha256
    by_sha: dict[str, list[dict]] = {}
    no_sha: list[dict] = []
    for a in attachments:
        sha = a.get("sha256")
        if sha:
            by_sha.setdefault(sha, []).append(a)
        else:
            no_sha.append(a)

    if no_sha:
        logger.warning(f"{len(no_sha)} attachment rows have no sha256 — they'll each be downloaded individually.")

    logger.info(f"Unique sha256 groups: {len(by_sha):,} (covers {len(attachments) - len(no_sha):,} rows)")

    # Build jobs
    jobs: list[dict] = []
    manifest_index: dict[str, dict] = {}
    used_paths_per_month: dict[str, set[str]] = {}

    def _email_date(eid):
        e = email_idx.get(eid) or {}
        return e.get("date") or e.get("date_sent") or e.get("date_received")

    # Stable, deterministic ordering: smallest sha first
    for sha in sorted(by_sha.keys()):
        rows = by_sha[sha]
        # Pick the earliest-dated email as canonical (so file lands in earliest month)
        rows.sort(key=lambda a: _email_date(a.get("email_id")) or datetime.max)
        canonical = rows[0]
        canon_email = email_idx.get(canonical.get("email_id")) or {}

        month = _month_folder(_email_date(canonical.get("email_id")))
        raw_name = canonical.get("filename") or "unnamed"
        safe_name = _sanitize(raw_name)

        # Resolve filename collisions inside the month folder
        used = used_paths_per_month.setdefault(month, set())
        if safe_name.lower() in used:
            if "." in safe_name:
                base, ext = safe_name.rsplit(".", 1)
                safe_name = f"{base}__{sha[:8]}.{ext}"
            else:
                safe_name = f"{safe_name}__{sha[:8]}"
            safe_name = _sanitize(safe_name)
        used.add(safe_name.lower())

        target = dest / month / safe_name
        jobs.append({
            "sha256": sha,
            "gridfs_id": canonical.get("gridfs_id"),
            "size_bytes": int(canonical.get("size_bytes") or 0),
            "target_path": target,
            "month": month,
            "filename": safe_name,
        })
        manifest_index[sha] = {
            "target_path": str(target),
            "filename_saved": safe_name,
            "month": month,
            "duplicate_count": len(rows),
        }

    # Extra rows for attachments that have no sha (rare). Treated as unique.
    for i, a in enumerate(no_sha):
        eid = a.get("email_id")
        e = email_idx.get(eid) or {}
        d = e.get("date") or e.get("date_sent")
        month = _month_folder(d)
        raw_name = a.get("filename") or "unnamed"
        safe_name = _sanitize(raw_name)
        used = used_paths_per_month.setdefault(month, set())
        if safe_name.lower() in used:
            base, _, ext = safe_name.rpartition(".")
            safe_name = f"{base or safe_name}__nohash{i}{('.' + ext) if ext else ''}"
            safe_name = _sanitize(safe_name)
        used.add(safe_name.lower())
        target = dest / month / safe_name
        jobs.append({
            "sha256": "",
            "gridfs_id": a.get("gridfs_id"),
            "size_bytes": int(a.get("size_bytes") or 0),
            "target_path": target,
            "month": month,
            "filename": safe_name,
        })

    # Log size summary
    total_bytes = sum(j["size_bytes"] for j in jobs)
    logger.info(
        f"Plan: {len(jobs):,} unique files to download "
        f"({total_bytes / 1024 / 1024:.1f} MB) across {len(used_paths_per_month):,} month folders."
    )
    return jobs, manifest_index, attachments, email_idx


# ---------------------------------------------------------------------------
# Download worker
# ---------------------------------------------------------------------------
def _download_job(mongo: MongoClientWrapper, job: dict) -> tuple[str, dict]:
    """Returns (status, info_dict). status in {ok, skipped, error}."""
    target: Path = job["target_path"]
    expected_size = job["size_bytes"]
    gridfs_id = job["gridfs_id"]

    if gridfs_id is None:
        return "error", {"reason": "missing_gridfs_id", "size": 0}

    target.parent.mkdir(parents=True, exist_ok=True)
    long_target = _long_path(target)

    # Skip if already-correct file is on disk
    if target.exists():
        try:
            existing_size = target.stat().st_size
        except OSError:
            existing_size = -1
        if expected_size > 0 and existing_size == expected_size:
            return "skipped", {"size": expected_size}

    try:
        with mongo.gridfs.open_download_stream(gridfs_id) as stream:
            with open(long_target, "wb") as f:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as exc:
        # Best-effort cleanup of partial files
        try:
            if target.exists():
                target.unlink()
        except Exception:
            pass
        return "error", {"reason": str(exc), "size": 0}

    return "ok", {"size": expected_size}


# ---------------------------------------------------------------------------
# Manifest writer
# ---------------------------------------------------------------------------
def _write_manifest(dest: Path, attachments: list[dict], email_idx: dict,
                    manifest_index: dict[str, dict]) -> Path:
    manifest_path = dest / "_manifest.csv"
    fieldnames = [
        "month", "saved_filename", "saved_relative_path",
        "sha256", "size_bytes",
        "duplicate_count", "is_duplicate_reference",
        "email_pst_entry_id", "email_date", "email_from", "email_subject",
        "original_filename",
    ]
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for a in attachments:
            sha = a.get("sha256") or ""
            entry = manifest_index.get(sha) or {}
            target_path = entry.get("target_path", "")
            email = email_idx.get(a.get("email_id")) or {}
            d = email.get("date") or email.get("date_sent")
            sender = (email.get("from") or {}).get("email", "")
            saved_rel = ""
            if target_path:
                try:
                    saved_rel = str(Path(target_path).relative_to(dest))
                except ValueError:
                    saved_rel = target_path
            writer.writerow({
                "month": entry.get("month", _month_folder(d)),
                "saved_filename": entry.get("filename_saved", ""),
                "saved_relative_path": saved_rel,
                "sha256": sha,
                "size_bytes": a.get("size_bytes", 0),
                "duplicate_count": entry.get("duplicate_count", 1),
                "is_duplicate_reference": "yes" if entry.get("duplicate_count", 1) > 1 else "no",
                "email_pst_entry_id": email.get("pst_entry_id", ""),
                "email_date": d.strftime("%Y-%m-%d %H:%M:%S") if isinstance(d, datetime) else "",
                "email_from": sender,
                "email_subject": (email.get("subject") or "")[:200],
                "original_filename": a.get("filename", ""),
            })
    return manifest_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(mongo: MongoClientWrapper, dest: Path, workers: int) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    logger.info(f"Destination: {dest}")

    jobs, manifest_index, all_attachments, email_idx = _build_plan(mongo, dest)
    if not jobs:
        logger.info("Nothing to download.")
        return 0

    n_ok = n_skipped = n_err = 0
    bytes_ok = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_download_job, mongo, j): j for j in jobs}
        with tqdm(total=len(jobs), desc="Downloading", unit="file") as bar:
            for fut in as_completed(futures):
                bar.update(1)
                job = futures[fut]
                try:
                    status, info = fut.result()
                except Exception as exc:
                    n_err += 1
                    logger.error(f"  worker crashed on {job['filename']}: {exc}")
                    continue
                if status == "ok":
                    n_ok += 1
                    bytes_ok += info.get("size", 0)
                elif status == "skipped":
                    n_skipped += 1
                else:
                    n_err += 1
                    logger.warning(f"  failed: {job['filename']} -> {info.get('reason')}")

    logger.info("Writing manifest…")
    manifest_path = _write_manifest(dest, all_attachments, email_idx, manifest_index)

    elapsed = time.time() - t0
    logger.info(
        f"Done in {elapsed:.1f}s. "
        f"Downloaded {n_ok:,}, skipped {n_skipped:,}, errors {n_err:,}. "
        f"{bytes_ok / 1024 / 1024:.1f} MB written. "
        f"Manifest: {manifest_path}"
    )
    return 0 if n_err == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=str, default=str(DEFAULT_DEST),
                        help=f"Destination root folder (default {DEFAULT_DEST})")
    parser.add_argument("--workers", type=int, default=12,
                        help="Parallel download workers (default 12)")
    args = parser.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)

    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    try:
        mongo.ping()
        return run(mongo, Path(args.dest), args.workers)
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
