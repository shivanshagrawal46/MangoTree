"""Sprint 3 · 3.1.4 + 3.2 — bank entities + full relationship edge set from
grounded facts (now that mortgages/liens/chain-of-title are extracted).

Creates:
  * `bank` entities from mortgage lenders + lien creditors (kind=bank, third_party)
  * edges (with provenance: source_doc_id, source_quote, confidence, as_of):
      GRANTEE_OF / GRANTOR_OF   (person/llc <-> property, from chain_of_title)
      HAS_MORTGAGE              (property -> bank)
      LENT_TO                   (bank -> borrower)
      HAS_LIEN                  (property -> creditor/bank)
      MEMBER_OF                 (person -> llc, from agent/control links)
Idempotent (upsert by {type,src,dst}).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.detect.dates import parse_date
from src.detect.detectors import _name_to_entity, _resolve
from src.graph.normalize import norm_name, slug
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.graph.schema import (REL_GRANTEE_OF, REL_GRANTOR_OF, REL_HAS_MORTGAGE,
                              REL_LENT_TO, REL_HAS_LIEN, REL_MEMBER_OF, SIDE_THIRD)
from src.utils.logger import logger

_BANK_HINT = ("bank", "mortgage", "lending", "loan", "financial", "credit union",
              "n.a", "fsb", "savings", "capital", "funding", "trust", "wells fargo",
              "chase", "citi", "hsbc", "quicken", "rocket", "freedom", "nationstar",
              "ditech", "ocwen", "carrington", "flagstar", "us bank", "santander")


def main() -> int:
    ap_live = "--dry-run" not in sys.argv
    now = datetime.now(timezone.utc)
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents, docs, rels = m.db["entities"], m.db["documents"], m.db["relationships"]
    name_idx = _name_to_entity(ents)

    counts = {"banks": 0, REL_GRANTEE_OF: 0, REL_GRANTOR_OF: 0, REL_HAS_MORTGAGE: 0,
              REL_LENT_TO: 0, REL_HAS_LIEN: 0, REL_MEMBER_OF: 0}

    def edge(t, src, dst, doc_id, quote, as_of=None, amount=None, conf=0.85):
        if not src or not dst:
            return
        rels.update_one({"type": t, "src": src, "dst": dst}, {"$set": {
            "type": t, "src": src, "dst": dst, "source_doc_id": doc_id,
            "source_quote": quote, "as_of": as_of, "amount": amount,
            "confidence": conf, "updated_at": now}}, upsert=True)
        counts[t] = counts.get(t, 0) + 1

    def get_or_make_bank(name: str) -> Optional[str]:
        if not name or len(name.strip()) < 4:
            return None
        ex = _resolve(name, name_idx)
        if ex:
            return ex["_id"]
        low = name.lower()
        if not any(h in low for h in _BANK_HINT):
            return None  # only create bank entities for clear financial names
        bid = "ent_bank_" + slug(name)
        ents.update_one({"_id": bid}, {"$set": {
            "_id": bid, "kind": "bank", "matter_id": DEFAULT_MATTER_ID,
            "canonical_name": name, "name_norm": norm_name(name), "aliases": [name],
            "side": SIDE_THIRD, "is_david": False, "source": "grounded_facts",
            "updated_at": now}, "$setOnInsert": {"created_at": now}}, upsert=True)
        name_idx[norm_name(name)] = {"_id": bid, "kind": "bank", "is_david": False}
        counts["banks"] += 1
        return bid

    for d in docs.find({"grounded_facts": {"$exists": True}},
                       {"grounded_facts": 1, "property_ids": 1}):
        gf = d.get("grounded_facts") or {}
        prop = (d.get("property_ids") or [None])[0]
        did = d["_id"]
        if not prop:
            continue
        for it in gf.get("chain_of_title", []):
            q, dt = it.get("source_quote"), parse_date(it.get("dated") or it.get("recorded") or "")
            ge = _resolve(it.get("grantee") or "", name_idx)
            gr = _resolve(it.get("grantor") or "", name_idx)
            if ge:
                edge(REL_GRANTEE_OF, ge["_id"], prop, did, q, dt, it.get("amount"))
            if gr:
                edge(REL_GRANTOR_OF, gr["_id"], prop, did, q, dt, it.get("amount"))
        for it in gf.get("mortgages", []):
            q, dt = it.get("source_quote"), parse_date(it.get("dated") or it.get("recorded") or "")
            bank = get_or_make_bank(it.get("lender") or "")
            borrower = _resolve(it.get("borrower") or "", name_idx)
            if bank:
                edge(REL_HAS_MORTGAGE, prop, bank, did, q, dt, it.get("amount"))
                if borrower:
                    edge(REL_LENT_TO, bank, borrower["_id"], did, q, dt, it.get("amount"))
        for it in gf.get("liens", []):
            q, dt = it.get("source_quote"), parse_date(it.get("dated") or "")
            cred = get_or_make_bank(it.get("creditor") or "") or (
                _resolve(it.get("creditor") or "", name_idx) or {}).get("_id")
            if cred:
                edge(REL_HAS_LIEN, prop, cred, did, q, dt, it.get("amount"))

    # MEMBER_OF from existing LLC agent/control links
    for e in ents.find({"kind": "llc", "agent_entity_id": {"$exists": True, "$ne": None}},
                       {"agent_entity_id": 1}):
        edge(REL_MEMBER_OF, e["agent_entity_id"], e["_id"], None, "LLC registered agent", conf=0.7)

    logger.info(f"graph edges built: {counts}")
    logger.info(f"bank entities now: {ents.count_documents({'kind': 'bank'})}  "
                f"relationships total: {rels.estimated_document_count()}")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
