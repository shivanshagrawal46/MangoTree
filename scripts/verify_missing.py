"""Split "not ingested" into dedup (correct) and genuine misses (a bug).

41 PDFs appear absent from Mongo, and many are named `... (1).pdf` / `... (2).pdf`
— the signature of Windows copies of identical content, which SHA-256 dedup is
*supposed* to collapse. But "looks like a duplicate" is not evidence. This hashes
every absent file and checks whether its content is already stored under another
path. Only files whose hash is nowhere in the database are real losses.
"""
from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo

ROOT = Path(SETTINGS.disk_corpus_root)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    db = get_mongo().db
    artifacts = list(db["artifacts"].find(
        {"source_type": "disk_file"},
        {"relative_path": 1, "sha256": 1, "filename": 1},
    ))
    by_sha: dict[str, list[str]] = defaultdict(list)
    for a in artifacts:
        by_sha[a.get("sha256", "")].append(a.get("relative_path") or "")
    ingested_paths = {a.get("relative_path") for a in artifacts}

    on_disk = {}
    for path in ROOT.rglob("*"):
        if path.is_file() and not path.name.startswith("~$") \
           and path.name.lower() != "thumbs.db":
            on_disk[str(path.relative_to(ROOT))] = path

    absent = sorted(set(on_disk) - ingested_paths)

    dedup: list[tuple[str, str]] = []
    genuine: list[tuple[str, int]] = []

    for rel in absent:
        path = on_disk[rel]
        try:
            sha = sha256_of(path)
        except Exception as exc:
            genuine.append((rel, -1))
            print(f"  hash failed: {rel}: {exc}")
            continue
        if sha in by_sha:
            dedup.append((rel, by_sha[sha][0]))
        else:
            genuine.append((rel, path.stat().st_size))

    print("=" * 78)
    print("MISSING-FILE TRIAGE")
    print("=" * 78)
    print(f"  files absent from Mongo by path   {len(absent)}")
    print(f"  ...content already stored (dedup) {len(dedup)}")
    print(f"  ...GENUINELY not ingested         {len(genuine)}")

    print("\n--- correctly deduplicated (content is present under another path) ---")
    for rel, kept in dedup[:50]:
        print(f"  {rel}\n      -> already stored as: {kept}")

    print("\n--- GENUINE MISSES, by extension ---")
    by_ext: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for rel, size in genuine:
        by_ext[Path(rel).suffix.lower() or "(none)"].append((rel, size))
    for ext, items in sorted(by_ext.items(), key=lambda kv: -len(kv[1])):
        total = sum(s for _, s in items if s > 0)
        print(f"\n  {ext}  ({len(items)} files, {total/1e6:.1f} MB)")
        for rel, size in sorted(items, key=lambda t: -t[1]):
            print(f"      {size/1024:>9.0f} KB  {rel}")

    # Documents that were ingested but produced no text are a second class of
    # gap: present in the database, invisible to retrieval.
    print("\n" + "=" * 78)
    print("INGESTED BUT NO TEXT (present in Mongo, invisible to search)")
    print("=" * 78)
    no_text = list(db["artifacts"].find(
        {"source_type": "disk_file",
         "$or": [{"text": {"$exists": False}}, {"text": ""}]},
        {"relative_path": 1, "filename": 1, "mime_type": 1},
    ))
    groups: dict[str, list[str]] = defaultdict(list)
    for a in no_text:
        ext = Path(a.get("relative_path") or a.get("filename") or "").suffix.lower()
        groups[ext or "(none)"].append(a.get("relative_path") or "")
    for ext, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  {ext}  ({len(items)})")
        for rel in items:
            print(f"      {rel}")


if __name__ == "__main__":
    main()
