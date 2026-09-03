"""Where did the unnumbered 'Bayshore Dr.' folder come from?

No such folder exists on disk — only '904 Bayshore Dr' and '910 Bayshore Dr.'.
Either the ingest is mangling paths, or these artifact records predate a folder
rename on the E: drive. The two have very different fixes, and the second is the
more dangerous one to leave alone: stale folder names mean stale property
attribution, and 904 vs 910 is exactly the pair that must never blur.
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from mangotree.storage.mongo import get_mongo


def main() -> None:
    db = get_mongo().db

    print("=== distinct top-level folders recorded in Mongo ===")
    tops: Counter = Counter()
    for a in db["artifacts"].find({"source_type": "disk_file"},
                                  {"relative_path": 1, "folder": 1}):
        rel = a.get("relative_path") or ""
        tops[rel.split("\\")[0] if "\\" in rel else rel.split("/")[0]] += 1
    for name, count in sorted(tops.items()):
        print(f"  {count:>5}  {name}")

    print("\n=== artifacts under an unnumbered Bayshore path ===")
    rows = list(db["artifacts"].find(
        {"source_type": "disk_file",
         "relative_path": {"$regex": r"^Bayshore Dr"}},
        {"relative_path": 1, "folder": 1, "property_ids": 1,
         "run_id": 1, "first_seen_at": 1, "ingested_at": 1, "sha256": 1},
    ))
    print(f"  count: {len(rows)}")
    for r in rows[:20]:
        print(f"\n  {r.get('relative_path')}")
        print(f"      folder     : {r.get('folder')}")
        print(f"      properties : {r.get('property_ids')}")
        print(f"      run_id     : {r.get('run_id')}")
        print(f"      first_seen : {r.get('first_seen_at') or r.get('ingested_at')}")

    print("\n=== disk ingestion runs on record ===")
    for run in db["ingestion_runs"].find(
        {"$or": [{"kind": "disk"}, {"source": "disk"}]},
        {"run_id": 1, "kind": 1, "source": 1, "started_at": 1,
         "finished_at": 1, "root": 1},
    ).sort("started_at", 1):
        print(f"  {run.get('run_id')}  {run.get('started_at')}  root={run.get('root')}")

    print("\n=== 904 vs 910 attribution check ===")
    for pid in ("bayshore_904", "bayshore_910"):
        arts = list(db["artifacts"].find(
            {"source_type": "disk_file", "property_ids": pid},
            {"relative_path": 1},
        ))
        folders: Counter = Counter()
        for a in arts:
            rel = a.get("relative_path") or ""
            folders[rel.split("\\")[0]] += 1
        print(f"\n  {pid}: {len(arts)} artifacts")
        for name, count in folders.most_common():
            print(f"      {count:>4}  {name}")


if __name__ == "__main__":
    main()
