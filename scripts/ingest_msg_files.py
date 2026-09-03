"""Ingest the Outlook .msg files in the disk corpus.

Targeted rather than a full disk re-ingest: re-walking all 353 files would
re-hash hundreds of megabytes of video to reach seven messages. The work is
idempotent either way — this is only about not paying for the walk.

Each file is converted to RFC822 and run through the ordinary mail pipeline, so
threading, participant rules and attachment storage behave exactly as they do
for API-fetched mail.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

from mangotree.config.registry import PROPERTIES
from mangotree.core.logging import logger
from mangotree.ingest.msg_parser import MsgIngestor
from mangotree.ingest.pipeline import EmailPipeline
from mangotree.storage.mongo import get_mongo

ROOT = Path(r"E:\LP Remodeling Projects\Hold Properties")


def property_for_folder(folder_name: str):
    for prop in PROPERTIES:
        if prop.disk_folder and prop.disk_folder == folder_name:
            return prop.property_id
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not ROOT.exists():
        print(f"Disk corpus not reachable: {ROOT}")
        return 1

    mongo = get_mongo()
    run_id = datetime.now(timezone.utc).strftime("msg-%Y%m%d-%H%M%S")
    pipeline = EmailPipeline(mongo, run_id=run_id)
    ingestor = MsgIngestor(mongo, pipeline, run_id=run_id)

    paths = sorted(ROOT.rglob("*.msg"))
    print(f"\n  {len(paths)} .msg file(s) under {ROOT}")
    print(f"  {len(ingestor.known_message_ids):,} Message-IDs already in the corpus\n")

    if args.dry_run:
        for path in paths:
            print(f"    would ingest  {path.relative_to(ROOT)}")
        return 0

    before_emails = mongo.artifacts.count_documents({"source_type": "email"})
    before_atts = mongo.artifacts.count_documents({"source_type": "attachment"})

    for path in paths:
        relative = str(path.relative_to(ROOT))
        folder = relative.split("\\")[0].split("/")[0]
        logger.info("ingesting %s", relative)
        ingestor.ingest_file(path, folder=folder, relative=relative)

    after_emails = mongo.artifacts.count_documents({"source_type": "email"})
    after_atts = mongo.artifacts.count_documents({"source_type": "attachment"})

    print("\n  MSG INGEST")
    for key, value in ingestor.stats.as_dict().items():
        if key != "errors":
            print(f"    {key:<24} {value:>6}")
    for err in ingestor.stats.errors:
        print(f"    ! {err}")

    print("\n  PIPELINE")
    for key, value in pipeline.stats.as_dict().items():
        if key not in ("skipped_by_reason", "discovery_candidates"):
            print(f"    {key:<24} {value}")
    if pipeline.stats.skipped:
        print(f"    skipped_by_reason        {dict(pipeline.stats.skipped)}")

    print("\n  CORPUS DELTA")
    print(f"    emails       {before_emails:>6,} -> {after_emails:>6,}  (+{after_emails - before_emails})")
    print(f"    attachments  {before_atts:>6,} -> {after_atts:>6,}  (+{after_atts - before_atts})")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
