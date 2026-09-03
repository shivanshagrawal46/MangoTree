"""Sprint 3 · 3.3 — backfill entity_refs onto EVERY chunk in email_chunks_v2.

THE linkage gap: email/attachment chunks had no graph links, so "anything on
520 E 81st?" never reached David's emails about it. This does a deterministic
(no-LLM, cheap, idempotent) pass:

  * Build an alias index from canonical entities:
      person/llc/org  -> distinctive name aliases
      property        -> canonical_address + address_variants (+ parcel digits)
  * Aho-Corasick-style single-pass match over each chunk's text.
  * Write entity_refs.{people,llcs,orgs,properties,cases} as canonical IDs,
    UNION-ed with any refs the doc-chunk already carries (never clobbered).
  * Also stamp primary_* and side/corpus flags for fan-out + Clean mode.

Resumable: re-running recomputes refs from scratch (idempotent). Safe to run
repeatedly as the graph improves.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from pymongo import UpdateOne

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.normalize import norm_name, parcel_digits, address_key
from src.utils.logger import logger

CHUNKS = "email_chunks_v2"
# generic tokens that must never alone trigger a match
_STOP = {"the", "and", "llc", "inc", "corp", "estate", "realty", "asset", "management",
         "properties", "property", "associates", "holdings", "real", "new", "york",
         "trust", "bank", "lending", "capital", "group", "company", "co", "as", "to"}


def _is_distinctive(phrase: str) -> bool:
    # Full multi-word firm/person names kept even if tokens are common (the full
    # phrase is specific, e.g. 'IPA Asset Management'); single short generic
    # tokens are not.
    p = phrase.strip()
    if len(p) < 5:
        return False
    toks = re.findall(r"[a-z0-9]+", p.lower())
    if len(toks) >= 2 and len(p) >= 8:
        return True
    nonstop = [t for t in toks if t not in _STOP]
    return any(len(t) >= 6 for t in nonstop) or any(c.isdigit() for c in p)


def build_alias_index(ents) -> Dict[str, Set[str]]:
    """phrase(lower) -> set of entity_ids. Longer phrases win at match time."""
    idx: Dict[str, Set[str]] = defaultdict(set)
    for e in ents.find({"is_active": {"$ne": False}},
                       {"_id": 1, "kind": 1, "canonical_name": 1, "aliases": 1,
                        "canonical_address": 1, "address_variants": 1, "parcel_id": 1}):
        eid, kind = e["_id"], e.get("kind")
        phrases: Set[str] = set()
        if kind in ("person", "llc", "org", "case"):
            for a in [e.get("canonical_name")] + (e.get("aliases") or []):
                if a and _is_distinctive(a):
                    phrases.add(a.strip().lower())
        if kind == "property":
            for a in [e.get("canonical_address")] + (e.get("address_variants") or []):
                if a and len(a.strip()) >= 6:
                    phrases.add(a.strip().lower())
        for p in phrases:
            idx[p].add(eid)
    return idx


def build_addr_index(ents) -> Dict[str, Set[str]]:
    """addr_core key (house# + street word) -> property entity_ids. Matches an
    email's '147 Eagle Hill Court' to canonical '147 EAGLE HILL CT, ...'."""
    aidx: Dict[str, Set[str]] = defaultdict(set)
    for e in ents.find({"kind": "property", "is_active": {"$ne": False}},
                       {"_id": 1, "canonical_address": 1, "address_variants": 1}):
        for a in [e.get("canonical_address")] + (e.get("address_variants") or []):
            if not a:
                continue
            k = address_key(a)
            if k and re.match(r"^\d", k):  # must lead with a house number
                aidx[k].add(e["_id"])
    return aidx


# house-number + up to 2 following words (to derive an addr_core key from text)
_ADDR_SCAN = re.compile(r"\b(\d{1,5})\s+([A-Za-z][A-Za-z0-9]+(?:\s+[A-Za-z0-9]+){0,2})")


def addr_hits(text: str, aidx: Dict[str, Set[str]]) -> Set[str]:
    out: Set[str] = set()
    for mo in _ADDR_SCAN.finditer(text):
        k = address_key(f"{mo.group(1)} {mo.group(2)}")
        if k in aidx:
            out |= aidx[k]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sha-file", default=None,
                    help="only (re)link chunks whose sha256 is listed in this file "
                         "(one sha per line). Used for targeted re-enrichment after "
                         "a scoped re-chunk.")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents, chunks = m.db["entities"], m.db[CHUNKS]
    kind_of = {e["_id"]: e.get("kind") for e in ents.find({}, {"kind": 1})}
    side_of = {e["_id"]: e.get("side") for e in ents.find({}, {"side": 1})}
    david_ids = {e["_id"] for e in ents.find({"is_david": True}, {"_id": 1})}

    idx = build_alias_index(ents)
    aidx = build_addr_index(ents)
    phrases = sorted(idx.keys(), key=len, reverse=True)
    logger.info(f"alias index: {len(phrases)} phrases, addr-core index: {len(aidx)} keys, "
                f"over {len(kind_of)} entities")
    # one big alternation regex (word-boundary), longest-first so specific wins
    big = re.compile(r"(?<![a-z0-9])(" +
                     "|".join(re.escape(p) for p in phrases) +
                     r")(?![a-z0-9])", re.IGNORECASE)

    scope = {}
    if args.sha_file:
        from pathlib import Path as _P
        shas = sorted({ln.strip() for ln in _P(args.sha_file).read_text(
            encoding="utf-8").splitlines() if ln.strip()})
        scope = {"sha256": {"$in": shas}}
        logger.info(f"scoped to {len(shas)} sha from {args.sha_file}")
    cur = chunks.find(scope, {"_id": 1, "text": 1, "body": 1, "entity_refs": 1,
                              "document_id": 1})
    if args.limit:
        cur = cur.limit(args.limit)
    ops: List[UpdateOne] = []
    n = n_linked = 0
    for ch in cur:
        n += 1
        text = ((ch.get("body") or "") + " " + (ch.get("text") or "")).lower()
        hits: Set[str] = set()
        for mtok in big.findall(text):
            hits |= idx.get(mtok.strip().lower(), set())
        # address-core matching: '147 Eagle Hill Court' -> canonical property
        hits |= addr_hits(text, aidx)
        # merge with existing doc-derived refs
        existing = ch.get("entity_refs") or {}
        buckets: Dict[str, Set[str]] = {"people": set(existing.get("people") or []),
                                        "llcs": set(existing.get("llcs") or []),
                                        "orgs": set(existing.get("orgs") or []),
                                        "properties": set(existing.get("properties") or []),
                                        "cases": set(existing.get("cases") or [])}
        for eid in hits:
            k = kind_of.get(eid)
            if k == "person":
                buckets["people"].add(eid)
            elif k == "llc":
                buckets["llcs"].add(eid)
            elif k == "org":
                buckets["orgs"].add(eid)
            elif k == "property":
                buckets["properties"].add(eid)
            elif k == "case":
                buckets["cases"].add(eid)
        all_ids = set().union(*buckets.values())
        if all_ids:
            n_linked += 1
        refs = {k: sorted(v) for k, v in buckets.items()}
        touches_david = bool(all_ids & david_ids)
        sides = sorted({side_of.get(e) for e in all_ids if side_of.get(e)})
        ops.append(UpdateOne({"_id": ch["_id"]}, {"$set": {
            "entity_refs": refs,
            "entity_ids": sorted(all_ids),
            "primary_property_id": (refs["properties"][0] if refs["properties"]
                                    else ch.get("primary_property_id")),
            "touches_david": touches_david,
            "entity_sides": sides,
            "entity_backfill_at": now,
        }}))
        if len(ops) >= 500:
            chunks.bulk_write(ops, ordered=False)
            ops = []
            logger.info(f"  ...{n} chunks processed, {n_linked} linked")
    if ops:
        chunks.bulk_write(ops, ordered=False)
    logger.info("================ ENTITY_REFS BACKFILL DONE ================")
    logger.info(f"chunks processed={n}  linked_to_>=1_entity={n_linked}  "
                f"({100*n_linked/max(n,1):.1f}%)")
    # index for fan-out
    from pymongo import ASCENDING
    for path in ["entity_ids", "entity_refs.people", "entity_refs.llcs",
                 "entity_refs.properties", "touches_david"]:
        try:
            chunks.create_index([(path, ASCENDING)], name="ix_" + path.replace(".", "_"))
        except Exception:  # noqa: BLE001
            pass
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
