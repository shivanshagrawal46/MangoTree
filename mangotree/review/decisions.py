"""apply_human_decision — one call, every place a placement lives.

A property assignment is not one field on one document. ``property_ids`` and
``placement`` live on the artifact, on each of its chunks (the vector and
lexical filters read the chunk), on its attachments (they inherit from the
email that carried them), on the occurrences, and on any timeline events it
produced. A UI that wrote only the artifact would leave the property chat
unable to find the document it had just been told about.

So the UI calls this and nothing else. Four actions:

    assign     — file under one or more properties (N is fine)
    common     — confirm it is portfolio-level; stays visible in property chats
    discard    — mark as no use; excluded from every search. Never deleted.
    register   — a non-registry address becomes a candidate property: every
                 document carrying that ``deal_address`` is filed under a new
                 property id. The registry code list itself is edited by hand
                 (it is code); this records the decision and files the paper.

Every decision records who and when, closes the matching review entries, and
is idempotent — applying the same decision twice changes nothing the second
time. Nothing here deletes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from mangotree.core.logging import logger
from mangotree.storage.mongo import Mongo

ACTIONS = ("assign", "common", "discard", "register")


@dataclass
class DecisionOutcome:
    action: str
    artifact_shas: List[str]
    property_ids: List[str]
    artifacts_updated: int = 0
    chunks_updated: int = 0
    attachments_updated: int = 0
    occurrences_updated: int = 0
    events_updated: int = 0
    reviews_closed: int = 0
    doc_summaries_updated: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _placement(property_ids: Sequence[str], common_kind: Optional[str]) -> str:
    if property_ids:
        return "property"
    if common_kind in ("portfolio", "business"):
        return common_kind
    return "unplaced"


def _slug(address: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", address.lower()).strip("_")
    return s[:40] or "new_property"


def apply_human_decision(
    mongo: Mongo,
    *,
    action: str,
    artifact_sha: Optional[str] = None,
    artifact_shas: Sequence[str] = (),
    property_ids: Sequence[str] = (),
    deal_address: Optional[str] = None,
    new_property_id: Optional[str] = None,
    decided_by: str,
    note: str = "",
) -> DecisionOutcome:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}")
    now = datetime.now(timezone.utc)
    shas: List[str] = list(artifact_shas) + ([artifact_sha] if artifact_sha else [])

    # --- register: resolve the address to its documents and a new id -----------
    if action == "register":
        if not deal_address:
            raise ValueError("register needs deal_address")
        pid = new_property_id or _slug(deal_address)
        rows = list(mongo.artifacts.find(
            {"deal_address": {"$regex": re.escape(deal_address.split(",")[0].strip()), "$options": "i"}},
            {"sha256": 1}))
        shas = list({r["sha256"] for r in rows} | set(shas))
        property_ids = [pid]
        mongo.properties.update_one(
            {"property_id": pid},
            {"$set": {"property_id": pid, "canonical_address": deal_address, "registered_by": decided_by,
                      "registered_at": now, "source": "human_decision", "pending_code_registry": True},
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        out = DecisionOutcome("register", shas, property_ids,
                              notes=[f"property '{pid}' recorded in properties collection; add it to "
                                     f"config/registry.py to make it a first-class registered property"])
        action = "assign"
    else:
        out = DecisionOutcome(action, shas, list(property_ids))

    if not shas:
        raise ValueError("no artifacts to decide on")

    # --- what the decision sets ---------------------------------------------------
    decision_record = {
        "action": out.action, "property_ids": list(property_ids), "decided_by": decided_by,
        "decided_at": now, "note": note[:1000],
    }
    if action == "assign":
        if not property_ids:
            raise ValueError("assign needs property_ids")
        art_set = {"property_ids": list(property_ids), "scope": "property", "placement": "property",
                   "resolution_status": "resolved_by_human", "human_decision": decision_record}
        art_unset = {"discarded": ""}
        chunk_set = {"property_ids": list(property_ids), "scope": "property", "placement": "property"}
        chunk_unset = {"discarded": ""}
    elif action == "common":
        art_set = {"property_ids": [], "scope": "common", "common_kind": "portfolio", "placement": "portfolio",
                   "resolution_status": "resolved_by_human", "human_decision": decision_record}
        art_unset = {"discarded": ""}
        chunk_set = {"property_ids": [], "scope": "common", "common_kind": "portfolio", "placement": "portfolio"}
        chunk_unset = {"discarded": ""}
    else:  # discard — a status, never a delete; excluded from search by privileged-style flag
        art_set = {"discarded": True, "privileged": True, "resolution_status": "discarded",
                   "human_decision": decision_record}
        art_unset = {}
        chunk_set = {"discarded": True, "privileged": True}
        chunk_unset = {}
        out.notes.append("discard marks the item privileged=True so every scope filter excludes it; bytes and chunks are kept")

    def upd(coll, query, set_, unset_):
        u: Dict[str, Any] = {"$set": set_}
        if unset_:
            u["$unset"] = unset_
        return coll.update_many(query, u).modified_count

    # --- artifacts and their chunks ------------------------------------------------
    out.artifacts_updated = upd(mongo.artifacts, {"sha256": {"$in": shas}}, art_set, art_unset)
    out.chunks_updated = upd(mongo.chunks, {"artifact_sha": {"$in": shas}}, chunk_set, chunk_unset)
    out.doc_summaries_updated = upd(mongo.db["doc_summaries"], {"artifact_sha": {"$in": shas}},
                                    {k: v for k, v in chunk_set.items() if k in ("property_ids", "placement", "privileged")}, {})

    # --- attachments inherit from an email decided here -------------------------------
    if action in ("assign", "common"):
        emails = [d["sha256"] for d in mongo.artifacts.find(
            {"sha256": {"$in": shas}, "source_type": "email"}, {"sha256": 1})]
        if emails:
            att_query = {"source_types": "attachment", "parent_email_shas": {"$in": emails},
                         # Only attachments that have no human decision of their own.
                         "human_decision": {"$exists": False}}
            att_shas = [d["sha256"] for d in mongo.artifacts.find(att_query, {"sha256": 1})]
            if att_shas:
                inherit = dict(art_set)
                inherit["human_decision"] = {**decision_record, "inherited_from_email": True}
                out.attachments_updated = upd(mongo.artifacts, {"sha256": {"$in": att_shas}}, inherit, art_unset)
                upd(mongo.chunks, {"artifact_sha": {"$in": att_shas}}, chunk_set, chunk_unset)
                upd(mongo.db["doc_summaries"], {"artifact_sha": {"$in": att_shas}},
                    {k: v for k, v in chunk_set.items() if k in ("property_ids", "placement", "privileged")}, {})
                shas = shas + att_shas

    # --- occurrences and timeline events ------------------------------------------------
    if action == "assign":
        out.occurrences_updated = upd(mongo.occurrences, {"artifact_sha": {"$in": shas}},
                                      {"property_id": list(property_ids)[0], "property_ids": list(property_ids)}, {})
        events = mongo.db["timeline_events"]
        # One event row per property: re-key existing rows to the first property and
        # note the others; a later timeline rebuild will produce the full set.
        out.events_updated = upd(events, {"source_sha": {"$in": shas}},
                                 {"property_id": list(property_ids)[0], "human_reassigned": True}, {})
    elif action == "discard":
        out.events_updated = upd(mongo.db["timeline_events"], {"source_sha": {"$in": shas}},
                                 {"discarded": True, "needs_review": True}, {})

    # --- close review entries -----------------------------------------------------------
    close_query: Dict[str, Any] = {"artifact_sha": {"$in": shas}, "status": {"$ne": "closed"}}
    if deal_address:
        close_query = {"$or": [close_query, {"kind": "registry_candidate",
                                             "payload.spellings": {"$regex": re.escape(deal_address.split(',')[0].strip()), "$options": "i"}}]}
    out.reviews_closed = mongo.review_queue.update_many(
        close_query,
        {"$set": {"status": "closed", "resolved_by": decided_by, "resolved_at": now,
                  "resolution": out.action, "resolution_note": note[:500]}},
    ).modified_count

    logger.info("human decision %s by %s: %d artifacts, %d chunks, %d attachments, %d reviews closed",
                out.action, decided_by, out.artifacts_updated, out.chunks_updated, out.attachments_updated, out.reviews_closed)
    return out
