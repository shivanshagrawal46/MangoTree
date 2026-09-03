"""Prove every file on the E: drive is either ingested or explicitly skipped.

The claim "we ingested the disk documents" is worth nothing without this: the
dangerous failure is not a loud error, it is a file that was never enumerated and
so never appears in any success or failure count. This walks the source tree
itself and reconciles it against MongoDB, so the only two possible verdicts for
any file are *ingested* or *skipped with a reason*.
"""
from __future__ import annotations

import hashlib
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()

sys.path.insert(0, ".")

from mangotree.config.settings import SETTINGS
from mangotree.storage.mongo import get_mongo

ROOT = Path(SETTINGS.disk_corpus_root)


def main() -> None:
    mongo = get_mongo()
    db = mongo.db

    if not ROOT.exists():
        print(f"Disk root not reachable: {ROOT}")
        return

    on_disk: dict[str, Path] = {}
    ext_counts: Counter = Counter()
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        # Windows/Office lock files and thumbnails are not documents.
        if path.name.startswith("~$") or path.name.lower() == "thumbs.db":
            continue
        rel = str(path.relative_to(ROOT))
        on_disk[rel] = path
        ext_counts[path.suffix.lower() or "(none)"] += 1

    artifacts = list(db["artifacts"].find(
        {"source_type": "disk_file"},
        {"relative_path": 1, "sha256": 1, "filename": 1, "text": 1,
         "property_ids": 1, "doc_class": 1, "extract_status": 1, "page_count": 1},
    ))

    ingested_paths = {a.get("relative_path") for a in artifacts if a.get("relative_path")}

    # A file routed to the email pipeline and rejected there is *accounted for*,
    # not missing — it lives in `skipped` under `provider_id`, keyed by relative
    # path. Omitting this join is what made an earlier version of this audit
    # report a correctly-logged skip as a silent data loss.
    skipped = {
        s.get("provider_id"): s
        for s in db["skipped"].find(
            {"provider": "disk"},
            {"provider_id": 1, "reason": 1, "detail": 1, "subject": 1},
        )
    }
    # Same courtesy for the review queue: visibly pending is not missing.
    queued = {
        q.get("reference"): q
        for q in db["review_queue"].find({}, {"reference": 1, "reason": 1})
    }

    accounted = ingested_paths | set(skipped) | set(queued)
    unmatched_by_path = sorted(set(on_disk) - accounted)

    # Path is the wrong unit of accounting on its own. SHA-256 dedup means a file
    # copied to two locations is stored once, so the second path legitimately has
    # no artifact of its own while its *content* is fully present. Hashing the
    # leftovers is what turns this audit from suggestive into authoritative:
    # only a file whose content appears nowhere is actually lost.
    known_shas = {a.get("sha256") for a in artifacts if a.get("sha256")}
    deduped: list[tuple[str, str]] = []
    missing: list[str] = []
    sha_to_path = {a.get("sha256"): a.get("relative_path") for a in artifacts}
    for rel in unmatched_by_path:
        try:
            digest = _sha256_of(on_disk[rel])
        except Exception as exc:
            missing.append(f"{rel}  (hash failed: {exc})")
            continue
        if digest in known_shas:
            deduped.append((rel, sha_to_path.get(digest, "?")))
        else:
            missing.append(rel)

    orphans = sorted(ingested_paths - set(on_disk))

    print("=" * 74)
    print("DISK COVERAGE AUDIT")
    print("=" * 74)
    print(f"  root                     {ROOT}")
    print(f"  files on disk            {len(on_disk)}")
    print(f"  artifacts in Mongo       {len(artifacts)}")
    print(f"  unique sha256            {len({a.get('sha256') for a in artifacts})}")
    print(f"  logged as skipped        {len(skipped)}")
    print(f"  queued for review        {len(queued)}")
    print(f"  deduped by SHA-256       {len(deduped)}   (content stored under another path)")
    print(f"  UNACCOUNTED FOR          {len(missing)}   <-- must be 0")
    print(f"  ingested, not on disk    {len(orphans)}   (stale paths / renames)")

    if skipped:
        print("\n--- skipped with a logged reason (accounted for, not lost) ---")
        for path_key, record in sorted(skipped.items()):
            print(f"  {record.get('reason')}: {path_key}")
            if record.get("detail"):
                print(f"      {record['detail']}")

    print("\n--- files by extension on disk ---")
    for ext, count in ext_counts.most_common():
        print(f"  {ext:<10}{count:>6}")

    if missing:
        by_ext: dict[str, list[str]] = defaultdict(list)
        for rel in missing:
            by_ext[Path(rel).suffix.lower() or "(none)"].append(rel)
        print("\n--- NOT INGESTED, grouped by extension ---")
        for ext, items in sorted(by_ext.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  {ext}  ({len(items)} files)")
            for rel in items[:12]:
                size = on_disk[rel].stat().st_size
                print(f"      {size/1024:>9.0f} KB  {rel}")
            if len(items) > 12:
                print(f"      ... and {len(items) - 12} more")

    # Text and property coverage among what we did ingest.
    no_text = [a for a in artifacts if not (a.get("text") or "").strip()]
    no_prop = [a for a in artifacts if not (a.get("property_ids") or [])]
    print("\n--- quality of the ingested set ---")
    print(f"  artifacts with no text        {len(no_text)}")
    print(f"  artifacts with no property    {len(no_prop)}")
    for a in no_text[:15]:
        print(f"      no text: {a.get('relative_path')}")
    for a in no_prop[:15]:
        print(f"      no property: {a.get('relative_path')}")

    # Chunk coverage per artifact — an extracted document that produced no chunk
    # is invisible to retrieval, which is the same as not being ingested.
    chunk_shas = set(db["chunks"].distinct("artifact_sha"))
    with_text = {a.get("sha256") for a in artifacts if (a.get("text") or "").strip()}
    unindexed = with_text - chunk_shas
    print(f"\n  documents with text but NO chunks   {len(unindexed)}")
    if unindexed:
        lookup = {a.get("sha256"): a.get("relative_path") for a in artifacts}
        for sha in list(unindexed)[:20]:
            print(f"      {lookup.get(sha)}")

    print("\n--- chunks and context coverage ---")
    total_chunks = db["chunks"].count_documents({})
    with_t1 = db["chunks"].count_documents({"tier1": {"$nin": [None, ""]}})
    with_t2 = db["chunks"].count_documents({"tier2": {"$nin": [None, ""]}})
    print(f"  chunks total             {total_chunks}")
    print(f"  with tier1 context       {with_t1}  ({with_t1/max(total_chunks,1):.1%})")
    print(f"  with tier2 context       {with_t2}  ({with_t2/max(total_chunks,1):.1%})")

    print("\n--- OCR engine audit (must be Claude/GPT only) ---")
    engines = Counter()
    for row in db["artifacts"].aggregate([
        {"$unwind": {"path": "$pages", "preserveNullAndEmptyArrays": False}},
        {"$group": {"_id": "$pages.engine", "n": {"$sum": 1}}},
    ]):
        engines[row["_id"] or "(none)"] = row["n"]
    for engine, count in engines.most_common():
        flag = "" if engine in {
            "native-text", "claude-sonnet-4-6", "claude-opus-5", "gpt-5", "(none)"
        } else "   <-- FORBIDDEN"
        print(f"  {str(engine):<22}{count:>7}{flag}")


if __name__ == "__main__":
    main()
