"""
Sprint 2 / Step 1 — Build the David entity store from 'List of LLC formed.xlsx'.

Creates the `entities/` collection (the linkage backbone) and stores, in
best-retrieval form:
  • David's LLCs (kind="llc") — canonical name, aliases, DOS filing date,
    agent, property address (+ normalized), city/county, is_david=true,
    parcel_id (linked later when title reports are ingested).
  • People (kind="person") — David DeRosa (principal), Hajrije Velovic
    (family), Matthew Tannenbaum (forming attorney) — linked to the LLCs
    they own/form.

Idempotent: entities are upserted by a deterministic _id derived from the
canonical name, so re-running is safe.

Usage:
  python -m scripts.build_entities_from_llc --dry-run
  python -m scripts.build_entities_from_llc            # live (writes)
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.rag.evidence_schema import DEFAULT_MATTER_ID
from src.utils.logger import logger

LLC_XLSX = "List of LLC formed.xlsx"
ENTITIES_COLLECTION = "entities"


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def norm_name(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"[.,]", " ", s)
    s = re.sub(r"\bL\.?L\.?C\.?\b|\bINC\b|\bP\.?C\.?\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm_addr(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"\bUNIT\b|\bAPT\b|#|\bSTE\b|\bSUITE\b", " ", s)
    repl = {"STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
            "LANE": "LN", "COURT": "CT", "BOULEVARD": "BLVD", "PLACE": "PL"}
    for k, v in repl.items():
        s = re.sub(rf"\b{k}\b", v, s)
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return s or "x"


def _to_dt(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
    return None


# Canonical person resolution for the registered-agent column.
def canonical_agent(agent_raw: str, owner_name: str) -> Optional[Dict[str, str]]:
    """Return {name, role} for a person agent, or None if the agent is the
    LLC itself (self-agent)."""
    a = (agent_raw or "").upper()
    if not a:
        return None
    if "TANNENBAUM" in a:
        return {"name": "Matthew Tannenbaum", "role": "forming_attorney"}
    if "DEROSA" in a or "DE ROSA" in a:
        return {"name": "David DeRosa", "role": "principal"}
    if "VELOVIC" in a:
        return {"name": "Hajrije Velovic", "role": "family"}
    # Self-agent: the registered agent IS the LLC (name ends LLC / equals owner)
    if a.endswith("LLC") or norm_name(a) == norm_name(owner_name) or a == "THE LLC":
        return None
    # Unknown person agent — keep but flag for review.
    return {"name": agent_raw.strip().title(), "role": "agent_unconfirmed"}


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    import openpyxl

    settings = Settings.load()
    path = Path(LLC_XLSX)
    if not path.exists():
        path = settings.project_root / LLC_XLSX
    if not path.exists():
        logger.error(f"LLC Excel not found: {LLC_XLSX}")
        return 2

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))[1:]  # skip header

    llc_entities: List[Dict[str, Any]] = []
    person_map: Dict[str, Dict[str, Any]] = {}   # canonical person name -> entity
    now = datetime.now(timezone.utc)

    for r in rows:
        if not r or not r[0]:
            continue
        owner = str(r[0]).strip()
        filing = _to_dt(r[1]) if len(r) > 1 else None
        agent_raw = str(r[2]).strip() if len(r) > 2 and r[2] else ""
        addr = str(r[3]).strip() if len(r) > 3 and r[3] else ""
        city = str(r[4]).strip() if len(r) > 4 and r[4] else ""
        state = str(r[5]).strip() if len(r) > 5 and r[5] else ""
        county = str(r[6]).strip() if len(r) > 6 and r[6] else ""

        agent = canonical_agent(agent_raw, owner)
        agent_entity_id = None
        if agent:
            pid = "ent_per_" + slug(agent["name"])
            agent_entity_id = pid
            p = person_map.get(pid)
            if not p:
                p = {
                    "_id": pid, "kind": "person", "matter_id": DEFAULT_MATTER_ID,
                    "canonical_name": agent["name"], "name_norm": norm_name(agent["name"]),
                    "aliases": [agent["name"]],
                    "david_role": agent["role"],
                    "is_david_network": True,
                    "is_david": agent["role"] in ("principal", "family"),
                    "controls_llc_ids": [], "agent_for_llc_ids": [],
                    "source": LLC_XLSX, "created_at": now, "updated_at": now,
                }
                person_map[pid] = p
            if agent_raw and agent_raw not in p["aliases"]:
                p["aliases"].append(agent_raw)

        llc_id = "ent_llc_" + slug(owner)
        ent = {
            "_id": llc_id, "kind": "llc", "matter_id": DEFAULT_MATTER_ID,
            "canonical_name": owner, "name_norm": norm_name(owner),
            "aliases": [owner],
            "dos_filing_date": filing,
            "agent_name": agent_raw or None,
            "agent_entity_id": agent_entity_id,
            "property_address": addr or None,
            "property_address_norm": norm_addr(addr) if addr else None,
            "city": city or None, "state": state or None, "county": county or None,
            "parcel_id": None,            # linked when title reports ingest
            "is_david": True, "is_david_network": True,
            "source": LLC_XLSX, "created_at": now, "updated_at": now,
        }
        llc_entities.append(ent)
        if agent_entity_id:
            person_map[agent_entity_id].setdefault("agent_for_llc_ids", [])
            if agent["role"] in ("principal", "family"):
                person_map[agent_entity_id]["controls_llc_ids"].append(llc_id)
            else:
                person_map[agent_entity_id]["agent_for_llc_ids"].append(llc_id)

    persons = list(person_map.values())

    logger.info(f"Parsed {len(llc_entities)} LLC entities, {len(persons)} person entities")
    logger.info("People: " + ", ".join(f"{p['canonical_name']}({p['david_role']})" for p in persons))

    if args.dry_run:
        logger.info("DRY RUN — no writes. Sample LLC entities:")
        for e in llc_entities[:6]:
            logger.info(f"  {e['_id']} | {e['canonical_name']} | agent={e['agent_name']} | "
                        f"addr={e['property_address']} | {e['city']},{e['county']}")
        return 0

    mongo = MongoClientWrapper(settings.mongo_uri, settings.mongo_db_name)
    mongo.ping()
    col = mongo.db[ENTITIES_COLLECTION]
    from pymongo import ASCENDING
    for keys, name in [
        ([("kind", ASCENDING), ("matter_id", ASCENDING)], "ix_kind_matter"),
        ([("name_norm", ASCENDING)], "ix_name_norm"),
        ([("aliases", ASCENDING)], "ix_aliases"),
        ([("property_address_norm", ASCENDING)], "ix_addr_norm"),
        ([("parcel_id", ASCENDING)], "ix_parcel"),
        ([("is_david", ASCENDING)], "ix_is_david"),
    ]:
        try:
            col.create_index(keys, name=name)
        except Exception:  # noqa: BLE001
            pass

    from pymongo import UpdateOne
    ops = [UpdateOne({"_id": e["_id"]}, {"$set": e}, upsert=True) for e in (llc_entities + persons)]
    res = col.bulk_write(ops, ordered=False)
    logger.info(f"Upserted entities: matched={res.matched_count} upserted={len(res.upserted_ids)}")
    logger.info(f"entities/ now holds: {col.count_documents({})} docs "
                f"(llc={col.count_documents({'kind':'llc'})}, person={col.count_documents({'kind':'person'})})")
    mongo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
