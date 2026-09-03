"""
Download every attachment from MongoDB GridFS to the local filesystem.

Default destination is configured by ATTACHMENTS_DIR in .env (defaults to
D:\\fraud_emails_attachments). Layout:

    <ATTACHMENTS_DIR>/
        2025-12/
            <pst_entry_id>__<sanitized_subject>/
                Document1.pdf
                Image2.png
        2026-01/
            ...
        _manifest.csv      (one row per file: pst_entry_id, date, sender,
                            subject, filename, sha256, size_bytes, path)

Filenames are sanitized for Windows (illegal chars stripped, length limited).
Duplicate filenames within the same email folder get a numeric suffix.

Idempotent: re-running skips files that already exist with the matching
sha256. Partial downloads are detected (size mismatch) and re-downloaded.

Usage:
    python scripts/download_attachments.py
    python scripts/download_attachments.py --workers 8     # parallel downloads
    python scripts/download_attachments.py --dest D:\\my_path
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tqdm import tqdm

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.utils.logger import configure_logger, logger

# ---------------------------------------------------------------------------
# Filename sanitization
# ---------------------------------------------------------------------------
_INVALID_WIN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRIM = re.compile(r"^[\s.]+|[\s.]+$")
# Zero-width joiners, BOM, formatting chars, RTL marks, etc.
_INVISIBLE_UNICODE = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\ufff9-\ufffb]"
)
# Reserved Windows device names
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_NAME_LEN = 80           # for filenames
MAX_FOLDER_NAME_LEN = 60    # for the per-email folder name


def _long_path(path: Path) -> str:
    """Return a Windows long-path-safe string representation of a Path.

    Windows has a 260-char MAX_PATH limit unless paths are prefixed with
    \\?\. We apply the prefix transparently so deeply-nested or long
    filenames still work.
    """
    s = str(path.resolve())
    if sys.platform.startswith("win") and not s.startswith("\\\\?\\"):
        if s.startswith("\\\\"):  # UNC path
            s = "\\\\?\\UNC\\" + s.lstrip("\\")
        else:
            s = "\\\\?\\" + s
    return s


def _sanitize(name: str, max_len: int) -> str:
    """Make a string safe to use as a Windows file/folder name."""
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
        # Preserve extension if present
        if "." in name:
            base, ext = name.rsplit(".", 1)
            ext = ext[:10]
            name = base[: max_len - len(ext) - 1] + "." + ext
        else:
            name = name[:max_len]

    if name.upper() in _RESERVED_NAMES:
        name = "_" + name
    return name


def _email_folder_name(email_doc: dict) -> str:
    pst_id = email_doc.get("pst_entry_id") or "unknown"
    subject = (email_doc.get("subject") or "").strip()
    if subject:
        clean_subj = _sanitize(subject, MAX_FOLDER_NAME_LEN)
        return f"{pst_id}__{clean_subj}"
    return str(pst_id)


def _month_folder(email_doc: dict) -> str:
    d = email_doc.get("date") or email_doc.get("date_sent") or email_doc.get("date_received")
    if isinstance(d, datetime):
        return d.strftime("%Y-%m")
    return "no-date"


def _unique_path(folder: Path, filename: str) -> Path:
    """If filename already exists in folder, suffix with _2, _3, ..."""
    candidate = folder / filename
    if not candidate.exists():
        return candidate

    if "." in filename:
        base, ext = filename.rsplit(".", 1)
        ext = "." + ext
    else:
        base, ext = filename, ""
    n = 2
    while True:
        candidate = folder / f"{base}_{n}{ext}"
        if not candidate.exists():
            return candidate
        n += 1


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Download worker
# ---------------------------------------------------------------------------
def _download_one(mongo: MongoClientWrapper, attachment: dict, base_dir: Path,
                  email_index: dict) -> tuple[bool, dict]:
    """
    Returns (downloaded?, manifest_row).
    'downloaded' is False if the file was already present and matching.
    """
    email_id = attachment.get("email_id")
    email_doc = email_index.get(email_id, {})
    month = _month_folder(email_doc)
    folder_name = _email_folder_name(email_doc)
    target_folder = base_dir / month / folder_name
    target_folder.mkdir(parents=True, exist_ok=True)

    raw_filename = attachment.get("filename") or "unnamed"
    safe_name = _sanitize(raw_filename, MAX_NAME_LEN)

    expected_size = int(attachment.get("size_bytes") or 0)
    expected_sha = attachment.get("sha256") or ""

    # If a file with the right name + size + sha exists, skip
    candidate = target_folder / safe_name
    if candidate.exists() and expected_size > 0 and candidate.stat().st_size == expected_size:
        if not expected_sha or _sha256_of_file(candidate) == expected_sha:
            row = _manifest_row(email_doc, attachment, candidate, base_dir, "skipped")
            return False, row

    out_path = _unique_path(target_folder, safe_name)
    gridfs_id = attachment.get("gridfs_id")
    if gridfs_id is None:
        return False, _manifest_row(email_doc, attachment, out_path, base_dir, "missing_gridfs_id")

    try:
        with mongo.gridfs.open_download_stream(gridfs_id) as stream:
            # Use long-path-safe write for Windows
            with open(_long_path(out_path), "wb") as f:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as exc:
        if out_path.exists():
            try:
                out_path.unlink()
            except Exception:
                pass
        return False, _manifest_row(email_doc, attachment, out_path, base_dir, f"error: {exc}")

    return True, _manifest_row(email_doc, attachment, out_path, base_dir, "ok")


def _manifest_row(email_doc: dict, attachment: dict, path: Path, base_dir: Path, status: str) -> dict:
    sender = email_doc.get("from") or {}
    d = email_doc.get("date") or email_doc.get("date_sent")
    return {
        "status": status,
        "pst_entry_id": email_doc.get("pst_entry_id", ""),
        "date": d.strftime("%Y-%m-%d %H:%M:%S") if isinstance(d, datetime) else "",
        "from_email": sender.get("email", ""),
        "subject": (email_doc.get("subject") or "")[:200],
        "filename_original": attachment.get("filename", ""),
        "filename_saved": path.name,
        "size_bytes": attachment.get("size_bytes", 0),
        "sha256": attachment.get("sha256", ""),
        "relative_path": str(path.relative_to(base_dir)) if path.is_absolute() and base_dir in path.parents else str(path),
        "absolute_path": str(path),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(settings: Settings, mongo: MongoClientWrapper, dest: Path, workers: int) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    logger.info(f"Destination: {dest}")

    total_attachments = mongo.attachments.count_documents({})
    if total_attachments == 0:
        logger.info("No attachments to download.")
        return

    logger.info(f"Loading {total_attachments:,} attachment metadata records…")
    attachments = list(mongo.attachments.find({}))

    # Pre-fetch every parent email's metadata in one go to avoid N+1 queries
    email_ids = list({a["email_id"] for a in attachments if a.get("email_id")})
    logger.info(f"Loading {len(email_ids):,} parent emails…")
    email_docs = list(mongo.emails.find(
        {"_id": {"$in": email_ids}},
        {"pst_entry_id": 1, "subject": 1, "from": 1, "date": 1,
         "date_sent": 1, "date_received": 1},
    ))
    email_index = {d["_id"]: d for d in email_docs}
    logger.info(f"Indexed {len(email_index):,} emails.")

    # Manifest written incrementally (newline-flushed) so partial runs leave a trail
    manifest_path = dest / "_manifest.csv"
    write_header = not manifest_path.exists() or manifest_path.stat().st_size == 0

    n_ok = 0
    n_skipped = 0
    n_err = 0
    bytes_dl = 0

    with manifest_path.open("a", newline="", encoding="utf-8") as mf:
        writer = csv.DictWriter(mf, fieldnames=[
            "status", "pst_entry_id", "date", "from_email", "subject",
            "filename_original", "filename_saved", "size_bytes", "sha256",
            "relative_path", "absolute_path",
        ])
        if write_header:
            writer.writeheader()

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(_download_one, mongo, a, dest, email_index): a
                for a in attachments
            }
            with tqdm(total=len(attachments), desc="Downloading", unit="file") as bar:
                for fut in as_completed(futures):
                    bar.update(1)
                    try:
                        downloaded, row = fut.result()
                    except Exception as exc:
                        logger.error(f"Worker error: {exc}")
                        n_err += 1
                        continue
                    writer.writerow(row)
                    mf.flush()
                    if row["status"] == "ok":
                        n_ok += 1
                        bytes_dl += int(row["size_bytes"] or 0)
                    elif row["status"] == "skipped":
                        n_skipped += 1
                    else:
                        n_err += 1

    logger.info(
        f"Complete. Downloaded {n_ok}, skipped {n_skipped}, errors {n_err}. "
        f"{bytes_dl / 1024 / 1024:.1f} MB written. Manifest: {manifest_path}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Download all attachments from MongoDB to local disk.")
    parser.add_argument("--dest", type=str, default=None, help="Override ATTACHMENTS_DIR")
    parser.add_argument("--workers", type=int, default=8, help="Parallel download workers (default 8)")
    args = parser.parse_args()

    settings = Settings.load()
    configure_logger(settings.logs_dir)

    dest = Path(args.dest) if args.dest else settings.attachments_dir

    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    try:
        mongo.ping()
        run(settings, mongo, dest, args.workers)
        return 0
    finally:
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(main())
