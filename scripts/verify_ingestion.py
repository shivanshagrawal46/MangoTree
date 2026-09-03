"""End-to-end verification of the twelve admin requirements.

Every check answers a question the admin actually asked, and each prints the
evidence rather than a bare pass. A check that only says "ok" is not worth much
at 7am when the question is "can I trust this".

Exit code is non-zero if any HARD check fails. Soft checks report and warn.
"""
from __future__ import annotations

import sys
from collections import Counter
from typing import List, Tuple

sys.path.insert(0, ".")

from mangotree.config.models import EMBEDDING_MODEL, Seat, model_for
from mangotree.config.registry import PROPERTIES
from mangotree.storage.mongo import get_mongo

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"

results: List[Tuple[str, str, str]] = []


def check(status: str, requirement: str, detail: str) -> None:
    results.append((status, requirement, detail))


def main() -> int:
    mongo = get_mongo()
    db = mongo.db

    artifacts = db["artifacts"]
    chunks = db["chunks"]

    emails = artifacts.count_documents({"source_type": "email"})
    attachments = artifacts.count_documents({"source_type": "attachment"})
    disk = artifacts.count_documents({"source_type": "disk_file"})

    # -- 12: SHA-256 deduplication -----------------------------------
    total = artifacts.count_documents({})
    distinct_sha = len(artifacts.distinct("sha256"))
    check(
        PASS if total == distinct_sha else FAIL,
        "12 no duplicates (SHA-256)",
        f"{total} artifacts, {distinct_sha} distinct sha256",
    )
    missing_sha = artifacts.count_documents({"sha256": {"$in": [None, ""]}})
    check(
        PASS if missing_sha == 0 else FAIL,
        "12 every file hashed",
        f"{missing_sha} artifact(s) without a sha256",
    )

    # -- 2: metadata stored ------------------------------------------
    no_participants = artifacts.count_documents(
        {"source_type": "email", "participants": {"$exists": False}}
    )
    check(
        PASS if no_participants == 0 else FAIL,
        "2 email metadata stored",
        f"{emails} emails, {no_participants} missing participants",
    )
    no_date = artifacts.count_documents({"source_type": "email", "date": None})
    check(
        PASS if no_date == 0 else WARN,
        "2 email dates present",
        f"{no_date} email(s) with no date",
    )

    # -- 3: Opus 5 saw every email and attachment --------------------
    opus = model_for(Seat.ANALYST)
    seg_emails = artifacts.count_documents(
        {"source_type": "email", "segregation.model": opus}
    )
    seg_attach = artifacts.count_documents(
        {"source_type": "attachment", "segregation.model": opus}
    )
    check(
        PASS if seg_emails == emails else FAIL,
        f"3 every email through {opus}",
        f"{seg_emails}/{emails} emails carry a decision",
    )
    check(
        PASS if seg_attach == attachments else FAIL,
        f"3 every attachment through {opus}",
        f"{seg_attach}/{attachments} attachments carry a decision",
    )

    # -- 4: unresolved -> review AND to the named property -----------
    unresolved = list(artifacts.find(
        {"segregation.unresolved": True},
        {"sha256": 1, "property_ids": 1, "segregation.fallback_used": 1},
    ))
    queued = {
        d["artifact_sha"]
        for d in db["review_queue"].find(
            {"kind": {"$in": ["property_unresolved", "property_low_confidence"]}},
            {"artifact_sha": 1},
        )
    }
    not_queued = [d for d in unresolved if d["sha256"] not in queued]
    check(
        PASS if not not_queued else FAIL,
        "4 unresolved items are queued for review",
        f"{len(unresolved)} unresolved, {len(not_queued)} missing from the queue",
    )
    rescued = sum(
        1 for d in unresolved
        if (d.get("segregation") or {}).get("fallback_used", "").startswith("named_in")
    )
    check(
        INFO,
        "4 unresolved also filed under the named property",
        f"{rescued}/{len(unresolved)} recovered a property from subject/body",
    )

    # -- 5/6: scope routing and per-property isolation ---------------
    common = artifacts.count_documents({"scope": "common"})
    scoped = artifacts.count_documents({"scope": "property"})
    check(
        INFO, "5 routing", f"{scoped} filed to a property, {common} to the common store"
    )

    leaky = list(chunks.aggregate([
        {"$match": {"$expr": {"$gt": [{"$size": {"$ifNull": ["$property_ids", []]}}, 1]}}},
        {"$limit": 5},
        {"$project": {"chunk_id": 1, "property_ids": 1, "_id": 0}},
    ]))
    leaky_total = chunks.count_documents(
        {"$expr": {"$gt": [{"$size": {"$ifNull": ["$property_ids", []]}}, 1]}}
    )
    check(
        PASS if leaky_total == 0 else FAIL,
        "6 no chunk spans two properties",
        f"{leaky_total} multi-property chunk(s)" + (f" e.g. {leaky[:2]}" if leaky else ""),
    )

    common_chunks = chunks.count_documents({"property_ids": []})
    check(
        INFO,
        "6 common store is reachable only from the global chat",
        f"{common_chunks} chunk(s) carry no property, so no property filter can match them",
    )

    # -- 9: chunking 1000/200 ----------------------------------------
    sizes = list(chunks.aggregate([
        {"$group": {
            "_id": None,
            "mean": {"$avg": "$token_count"},
            "max": {"$max": "$token_count"},
        }}
    ]))
    if sizes:
        mean, largest = sizes[0]["mean"] or 0, sizes[0]["max"] or 0
        check(
            PASS if largest <= 1200 else FAIL,
            "9 chunk budget respected",
            f"mean {mean:.0f} tokens, max {largest} (ceiling 1200)",
        )
    else:
        check(WARN, "9 chunk budget respected", "no chunks yet")

    with_context = chunks.count_documents({"context": {"$nin": [None, ""]}})
    total_chunks = chunks.count_documents({})
    coverage = (with_context / total_chunks * 100) if total_chunks else 0
    check(
        PASS if coverage >= 95 else WARN,
        "9 three-tier context present",
        f"{with_context}/{total_chunks} chunks carry context ({coverage:.1f}%)",
    )

    # -- 10: one embedding space -------------------------------------
    models = [m for m in chunks.distinct("embedding_model") if m]
    check(
        PASS if models == [EMBEDDING_MODEL] else (WARN if not models else FAIL),
        "10 voyage-4-large is the only embedding space",
        f"models present: {models or 'none yet'}",
    )
    unembedded = chunks.count_documents({"embedding_status": {"$ne": "ok"}})
    check(
        PASS if unembedded == 0 else WARN,
        "10 every chunk embedded",
        f"{unembedded} chunk(s) not embedded",
    )

    # -- 11: entity linkage and knowledge graph ----------------------
    entities = db["entities"].count_documents({})
    edges = db["entity_edges"].count_documents({})
    linked = chunks.count_documents({"entity_ids": {"$exists": True, "$ne": []}})
    check(
        PASS if entities > 0 and edges > 0 else FAIL,
        "11 knowledge graph built",
        f"{entities} entities, {edges} edges",
    )
    check(
        PASS if linked > 0 else FAIL,
        "11 vectors linked to entities",
        f"{linked}/{total_chunks} chunks carry entity_ids",
    )

    # -- 1/8: extraction routing -------------------------------------
    engines = Counter(
        (d.get("extraction") or {}).get("method", "not-yet-extracted")
        for d in artifacts.find(
            {"source_type": {"$in": ["attachment", "disk_file"]}},
            {"extraction.method": 1},
        )
    )
    check(INFO, "1/8 extraction engines", ", ".join(f"{k}={v}" for k, v in engines.most_common()))
    forbidden = [k for k in engines if "rapid" in str(k).lower()]
    check(
        PASS if not forbidden else FAIL,
        "1 no banned OCR engine",
        f"forbidden engines present: {forbidden}" if forbidden else "Claude/GPT-5 only",
    )

    # -- per-property spread -----------------------------------------
    print("\n  --- artifacts per property ---")
    for prop in PROPERTIES:
        count = artifacts.count_documents({"property_ids": prop.property_id})
        chunk_count = chunks.count_documents({"property_ids": prop.property_id})
        flag = "  <-- empty" if count == 0 else ""
        print(f"    {prop.canonical_address:<24} {count:>5} artifacts  {chunk_count:>6} chunks{flag}")

    # -- report ------------------------------------------------------
    print(f"\n  corpus: {emails} emails, {attachments} attachments, {disk} disk files\n")
    print("  " + "=" * 74)
    failures = 0
    for status, requirement, detail in results:
        if status == FAIL:
            failures += 1
        print(f"  [{status:<4}] {requirement:<46} {detail}")
    print("  " + "=" * 74)

    if failures:
        print(f"\n  {failures} HARD FAILURE(S)\n")
        return 1
    print("\n  all hard checks passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
