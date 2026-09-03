"""Event-driven single-message ingestion for the real-time Gmail worker.

Given one or more Gmail message ids that just arrived (or just got the
watched label), run the SAME pipeline as the batch tools but scoped to only
those messages — so it finishes in seconds, not the ~18 min the batch
occurrence-sync (Phase D) takes:

    fetch + store (3-way dedup)  ->  force-vision OCR new attachments  ->
    chunk + contextual summary + embed (scoped)  ->  enrich/link (scoped)
    ->  verify parity.

Reuses the exact helpers from scripts/build_email_chunks_v2.py so realtime
chunks are byte-for-byte identical to batch-built ones.

CLI (for local testing against the real DB):
    python -m src.ingest.realtime_ingest --gmail-id 19f... [--gmail-id ...]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from bson import ObjectId

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.settings import Settings                       # noqa: E402
from src.db.mongo import MongoClientWrapper                # noqa: E402
from src.db.repository import EmailRepository              # noqa: E402
from src.ingest.gmail_client import GmailClient            # noqa: E402
from src.ingest.gmail_ingest import ingest_one_email, OUT_INSERTED  # noqa: E402
from src.rag import evidence_schema as ev                  # noqa: E402
from src.graph.schema import authority_for, DEFAULT_AUTHORITY  # noqa: E402
from src.utils.logger import logger                        # noqa: E402

# Reuse the batch chunk/embed machinery verbatim.
from scripts.build_email_chunks_v2 import (                # noqa: E402
    _process_one_sha256,
    _process_one_body,
    _gather_jobs,
    _attachment_already_done,
    _ensure_v2_indexes,
    _date_sort_key,
    _latest_date,
    _Flusher,
    CHUNK_SIZE_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    EMBEDDING_MODEL,
    V2_CHUNKS_COLLECTION,
    V2_ATTACHMENTS_COLLECTION,
    ContextualSummarizer,
    VoyageEmbedder,
)

PY = sys.executable
DEFAULT_LABEL = "__....Boris Lawsuit"


def _run(cmd: List[str], step: str) -> None:
    logger.info(f"[rt] >>> {step}")
    res = subprocess.run(cmd, cwd=str(REPO))
    if res.returncode != 0:
        raise RuntimeError(f"step '{step}' failed (exit {res.returncode})")


def _sync_occurrences(chunks_col, sha256: str, occs: List[Dict[str, Any]]) -> int:
    """Scoped Phase D: refresh one attachment's occurrences[] + mirror fields
    (used when a NEW email reuses an attachment that already has chunks)."""
    occs_sorted = sorted(occs, key=_date_sort_key)
    primary = occs_sorted[0]
    res = chunks_col.update_many(
        {"sha256": sha256, "source_type": "attachment"},
        {"$set": {
            "occurrences": occs_sorted,
            "latest_date": _latest_date(occs_sorted),
            "email_id": primary["email_id"],
            "attachment_id": primary.get("attachment_id"),
            "filename": primary.get("filename"),
            "date": primary.get("date"),
            "date_ym": primary.get("date_ym"),
            "from_email": primary.get("from_email"),
            "to_emails": primary.get("to_emails") or [],
            "subject": primary.get("subject"),
            "folder_path": primary.get("folder_path"),
        }},
    )
    return res.modified_count


def _enrich(mongo: MongoClientWrapper, eids: List[ObjectId], shas: List[str]) -> None:
    """Scoped enrichment chain — identical result to the manual runs."""
    ch = mongo.db[V2_CHUNKS_COLLECTION]
    scope = {"$or": [
        {"source_type": "attachment", "sha256": {"$in": shas}},
        {"source_type": "email_body", "email_id": {"$in": eids}},
    ]}
    # authority (scoped, matches global values)
    for st in ("attachment", "email_body"):
        ch.update_many(
            {**scope, "source_type": st, "doc_source_type": {"$exists": False}},
            {"$set": {"doc_authority_score": authority_for(st)}})
    ch.update_many({**scope, "doc_authority_score": {"$exists": False}},
                   {"$set": {"doc_authority_score": DEFAULT_AUTHORITY}})
    # corpus / privilege (idempotent global scan; only tags untagged)
    _run([PY, "-m", "scripts.tag_chunk_corpus"], "tag-corpus")
    # entity linkage (scoped via sha-file)
    keys = list(shas) + [f"email:{e}" for e in eids]
    tmp = REPO / f".rt_shas_{datetime.now().strftime('%H%M%S%f')}.txt"
    tmp.write_text("\n".join(keys) + "\n", encoding="utf-8")
    try:
        _run([PY, "-m", "scripts.backfill_chunk_entities", "--sha-file", str(tmp)],
             "entity-backfill")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def process_gmail_ids(
    gmail_ids: Sequence[str],
    *,
    label_names: Optional[List[str]] = None,
    settings: Optional[Settings] = None,
    mongo: Optional[MongoClientWrapper] = None,
    client: Optional[GmailClient] = None,
    corpus: str = ev.CORPUS_LEGAL_CORRESPONDENCE,
    privilege: Optional[str] = None,
    run_ocr: bool = True,
) -> Dict[str, Any]:
    """Fetch, store, OCR, chunk/embed, enrich and verify a specific set of
    Gmail messages. Idempotent: already-ingested ids are skipped cleanly.
    Returns a summary dict."""
    label_names = label_names or [DEFAULT_LABEL]
    settings = settings or Settings.load()
    own_mongo = mongo is None
    mongo = mongo or MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    if own_mongo:
        mongo.ping()
    client = client or GmailClient().authenticate()

    repo = EmailRepository(mongo)
    run_id = repo.start_run(pst_meta={"origin": "gmail_push", "labels": label_names})

    # 1) fetch + store ----------------------------------------------------
    inserted_eids: List[ObjectId] = []
    seen = 0
    for gid in gmail_ids:
        seen += 1
        try:
            meta = client.get_metadata(gid)
            raw = client.get_raw(gid)
            res = ingest_one_email(
                raw, gmail_id=gid, thread_id=meta.get("thread_id"),
                label_names=label_names, mongo=mongo, repo=repo, run_id=run_id,
                settings=settings, corpus=corpus,
                privilege_status=privilege,
                custodian="Gmail mailbox (real-time push)")
            if res.get("outcome") == OUT_INSERTED:
                em = mongo.emails.find_one({"pst_entry_id": "gmail:" + gid}, {"_id": 1})
                if em:
                    inserted_eids.append(em["_id"])
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[rt] ingest failed for {gid}: {exc}")
            repo.log_error(run_id, "gmail:" + gid, "realtime_ingest", str(exc))
    repo.finish_run(run_id, {"messages_seen": seen,
                             "messages_inserted": len(inserted_eids)},
                    status="completed")

    if not inserted_eids:
        logger.info(f"[rt] {seen} message(s) seen, nothing new to ingest.")
        if own_mongo:
            mongo.close()
        return {"seen": seen, "inserted": 0, "chunks": 0, "status": "NOOP"}

    logger.info(f"[rt] inserted {len(inserted_eids)} new email(s); running pipeline.")

    # 2) force-vision OCR (resume => only the new SHAs) -------------------
    if run_ocr:
        _run([PY, "scripts/ocr_attachments_v2.py", "--force-vision", "--workers", "3"],
             "force-vision-ocr")

    av2 = mongo.db[V2_ATTACHMENTS_COLLECTION]
    ch = mongo.db[V2_CHUNKS_COLLECTION]
    _ensure_v2_indexes(ch)

    # which attachment SHAs did these new emails bring?
    new_emails = list(mongo.emails.find(
        {"_id": {"$in": inserted_eids}}, {"_id": 1, "attachment_ids": 1}))
    new_att_ids = [aid for e in new_emails for aid in (e.get("attachment_ids") or [])]
    new_shas = sorted({a["sha256"] for a in av2.find(
        {"_id": {"$in": new_att_ids}}, {"sha256": 1}) if a.get("sha256")})

    summarizer = ContextualSummarizer(api_key=settings.anthropic_api_key,
                                      model="claude-sonnet-4-6")
    embedder = VoyageEmbedder(api_key=settings.voyage_api_key, model=EMBEDDING_MODEL)
    flusher = _Flusher(chunks_col=ch, embedder=embedder,
                       embedding_model=EMBEDDING_MODEL, batch_size=64, dry=False)

    # 3a) attachments — gather full occurrences for the touched SHAs once
    if new_shas:
        att_ids_for_shas = [a["_id"] for a in av2.find(
            {"sha256": {"$in": new_shas}}, {"_id": 1})]
        ref_email_ids = [e["_id"] for e in mongo.emails.find(
            {"attachment_ids": {"$in": att_ids_for_shas}}, {"_id": 1})]
        jobs, ext_map, _ = _gather_jobs(
            mongo, av2, email_ids=ref_email_ids,
            do_bodies=False, do_attachments=True)
        for sha in new_shas:
            occs = jobs.get(sha)
            if not occs:
                continue
            if _attachment_already_done(ch, sha):
                n = _sync_occurrences(ch, sha, occs)
                logger.info(f"[rt] sha {sha[:12]} already chunked — synced {n} chunks")
            else:
                r = _process_one_sha256(
                    sha, occs, extension=ext_map.get(sha), attachments_v2=av2,
                    chunk_size=CHUNK_SIZE_TOKENS, chunk_overlap=CHUNK_OVERLAP_TOKENS,
                    summarizer=summarizer)
                if r:
                    flusher.add_attachment_group(sha, r["docs"])

    # 3b) email bodies
    for eid in inserted_eids:
        r = _process_one_body(
            eid, emails_col=mongo.emails,
            chunk_size=CHUNK_SIZE_TOKENS, chunk_overlap=CHUNK_OVERLAP_TOKENS,
            summarizer=summarizer)
        if r:
            flusher.add_body_group(eid, r["docs"])

    flusher.flush(force=True)
    logger.info(f"[rt] chunks written: {flusher.n_chunks_written}")

    # 4) enrichment (scoped) --------------------------------------------
    _enrich(mongo, inserted_eids, new_shas)

    # 5) verify ----------------------------------------------------------
    scope = {"$or": [
        {"source_type": "attachment", "sha256": {"$in": new_shas}},
        {"source_type": "email_body", "email_id": {"$in": inserted_eids}},
    ]}
    total = ch.count_documents(scope)
    gaps = []
    for f in ("corpus", "privilege_status", "doc_authority_score",
              "entity_ids", "occurrences", "embedding.0"):
        if ch.count_documents({**scope, f: {"$exists": True}}) != total:
            gaps.append(f)
    linked = ch.count_documents({**scope, "entity_ids.0": {"$exists": True}})
    status = "OK" if not gaps else f"GAPS:{','.join(gaps)}"
    summary = {"seen": seen, "inserted": len(inserted_eids),
               "att_shas": len(new_shas), "chunks": total,
               "linked": linked, "status": status}
    logger.info(f"[rt] DONE {summary}")
    if own_mongo:
        mongo.close()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gmail-id", action="append", required=True,
                    help="Gmail message id to ingest (repeatable).")
    ap.add_argument("--label", action="append", help="Label name(s).")
    ap.add_argument("--privilege", default=None)
    args = ap.parse_args()
    summary = process_gmail_ids(args.gmail_id, label_names=args.label,
                                privilege=args.privilege)
    print(summary)
    return 0 if summary.get("status") in ("OK", "NOOP") else 1


if __name__ == "__main__":
    raise SystemExit(main())
