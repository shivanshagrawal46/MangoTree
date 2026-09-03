"""Copy every original from the local raw store to DigitalOcean Spaces, verified.

    python scripts/migrate_originals_to_spaces.py --dry-run
    python scripts/migrate_originals_to_spaces.py
    python scripts/migrate_originals_to_spaces.py --verify        # re-check sizes only

Needs in .env: DO_SPACES_KEY, DO_SPACES_SECRET, DO_SPACES_REGION, DO_SPACES_BUCKET
(and optionally DO_SPACES_ENDPOINT). Does NOT need OBJECT_STORE=spaces yet — flip
that after the migration reports every object present.

Uploads keep the original filename and content type as object metadata, so a
download from Spaces is the real PDF / .xlsx / .eml. Nothing local is deleted:
the local directory becomes the read-through cache.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, ".")

from dotenv import load_dotenv

load_dotenv()

from mangotree.config.settings import SETTINGS  # noqa: E402
from mangotree.storage.mongo import get_mongo  # noqa: E402
from mangotree.storage.objectstore import LocalObjectStore, SpacesObjectStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    missing = [k for k in ("DO_SPACES_KEY", "DO_SPACES_SECRET", "DO_SPACES_BUCKET") if not os.environ.get(k)]
    if missing:
        print(f"\n  Set these in .env first: {', '.join(missing)}\n")
        return 1

    local = LocalObjectStore(SETTINGS.raw_store)
    spaces = SpacesObjectStore(key=os.environ["DO_SPACES_KEY"], secret=os.environ["DO_SPACES_SECRET"],
                               region=os.environ.get("DO_SPACES_REGION", "nyc3"), bucket=os.environ["DO_SPACES_BUCKET"],
                               endpoint=os.environ.get("DO_SPACES_ENDPOINT") or None)
    mongo = get_mongo()
    names = {a["sha256"]: (a.get("filename") or (f"{a['sha256'][:12]}.eml" if a.get("source_type") == "email" else a["sha256"]), a.get("content_type"),
                           ",".join(a.get("property_ids") or []) or a.get("placement") or "")
             for a in mongo.artifacts.find({}, {"sha256": 1, "filename": 1, "content_type": 1, "source_type": 1, "property_ids": 1, "placement": 1})}

    files = [p for p in local.root.rglob("*") if p.is_file() and not p.name.endswith((".meta.json", ".partial"))]
    total_bytes = sum(p.stat().st_size for p in files)
    print(f"\n  local originals: {len(files):,} files, {total_bytes / 1e9:.2f} GB  ->  {spaces.endpoint}/{spaces.bucket}/{spaces.prefix}")
    if args.dry_run:
        print("  dry run — nothing uploaded\n")
        return 0

    def one(path: Path) -> tuple[str, str, int]:
        sha = path.name
        size = path.stat().st_size
        if args.verify:
            return sha, "present" if spaces.exists(sha) else "MISSING", size
        if spaces.exists(sha):
            return sha, "skip", size
        filename, ctype, props = names.get(sha, (sha, None, ""))
        meta = {"content_type": ctype or "", "properties": props}
        # Stream large files rather than reading them fully; put() takes bytes,
        # so use upload_file for anything over 64 MB.
        if size > 64 * 1024 * 1024:
            spaces.client.upload_file(str(path), spaces.bucket, spaces._key(sha),
                                      ExtraArgs={"ContentType": spaces._content_type(filename, meta),
                                                 "ContentDisposition": f'inline; filename="{filename[:200]}"',
                                                 "Metadata": {"sha256": sha, "original-filename": filename[:900], "properties": props[:900]}})
        else:
            spaces.put(sha, path.read_bytes(), filename, meta)
        return sha, "uploaded", size

    started = time.time()
    counts = {"uploaded": 0, "skip": 0, "present": 0, "MISSING": 0, "error": 0}
    done_bytes = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(one, p): p for p in files}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                sha, status, size = f.result()
                counts[status] = counts.get(status, 0) + 1
                done_bytes += size
            except Exception as exc:
                counts["error"] += 1
                print(f"    error {futs[f].name[:12]}: {str(exc)[:120]}")
            if i % 200 == 0 or i == len(files):
                rate = done_bytes / max(1e-6, time.time() - started) / 1e6
                print(f"    {i:>5}/{len(files)}  {rate:6.1f} MB/s  {counts}")

    print(f"\n  {json.dumps(counts)}")
    if not args.verify:
        st = spaces.stats()
        print(f"  Spaces now holds {st['objects']:,} objects, {st['bytes'] / 1e9:.2f} GB")
        if st["objects"] >= len(files) and counts["error"] == 0:
            print("\n  Every original is on the server. Set OBJECT_STORE=spaces in .env and restart the API.\n")
    return 0 if counts.get("error", 0) == 0 and counts.get("MISSING", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
