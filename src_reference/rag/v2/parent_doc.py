"""
Parent Document Expansion.

Why this exists:
  Each chunk in our corpus is ~500 tokens of one document. When the user
  asks about a specific paragraph (e.g. "Term 5 of the stipulation"), we
  retrieve only the chunk containing that paragraph. Critical context
  (party names from the caption, definitions from earlier paragraphs,
  cross-referenced terms) lives in OTHER chunks of the SAME document.

  When two or more retrieved chunks come from the same parent document,
  we infer the user is interested in that document and we EXPAND by
  pulling the entire document's chunks (or a sensible window around the
  hits). This gives Claude the full document context — same level of
  understanding a lawyer would have when reading the doc end-to-end.

Design notes:
  • We never expand for emails — they're already small and self-contained.
    Only attachments (PDF / DOCX) get parent expansion.
  • Token budgets are *per-parent* and ADAPTIVE: when only one parent is
    hot we give it 8K tokens, when two we give 5K each, when 3-5 we give
    3-4K each. The formula is in `_per_parent_budget()`.
  • Original chunks are kept in their RANKED order; expanded chunks are
    appended in chunk_index order to preserve narrative flow.
  • Pure read; no MongoDB writes.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from src.db.mongo import MongoClientWrapper
from src.utils.logger import logger


@dataclass
class ExpansionResult:
    """Output of parent_document_expand: chunks ready to send to Claude."""

    chunks: List[Dict[str, Any]]
    expanded_attachment_ids: List[str]
    tokens_added: int


def parent_document_expand(
    mongo: MongoClientWrapper,
    *,
    retrieved_chunks: Sequence[Dict[str, Any]],
    min_chunks_for_expansion: int = 2,
    max_chunks_per_parent: int = 20,
    max_parents: int = 5,
    token_budget_single: int = 8000,
) -> ExpansionResult:
    """
    Detect clusters of retrieved chunks from the same attachment and pull
    in the rest of that attachment's chunks (with adaptive per-parent budgets).

    Args:
      retrieved_chunks            — output of hybrid search / rerank
      min_chunks_for_expansion    — how many hits from one parent triggers
                                    expansion (default 2 — conservative)
      max_chunks_per_parent       — per-parent chunk-count safety cap
      max_parents                 — cap on the number of parents to expand
      token_budget_single         — budget when only ONE parent expands;
                                    multi-parent budgets scale down via
                                    `_per_parent_budget()`

    Returns:
      ExpansionResult with merged chunks (originals first, in original
      order, then expansion chunks appended).
    """
    if not retrieved_chunks:
        return ExpansionResult(chunks=[], expanded_attachment_ids=[], tokens_added=0)

    counts: Dict[str, int] = defaultdict(int)
    seen_chunk_ids: set = set()
    for c in retrieved_chunks:
        seen_chunk_ids.add(str(c.get("_id")))
        att_id = c.get("attachment_id")
        if att_id:
            counts[str(att_id)] += 1

    # Rank parents by hit-count (hottest first) and cap at max_parents.
    parents_sorted = sorted(
        (a for a, n in counts.items() if n >= min_chunks_for_expansion),
        key=lambda a: -counts[a],
    )
    parents_to_expand = parents_sorted[:max_parents]

    if not parents_to_expand:
        return ExpansionResult(
            chunks=list(retrieved_chunks),
            expanded_attachment_ids=[],
            tokens_added=0,
        )

    per_parent_budget = _per_parent_budget(
        len(parents_to_expand), single_budget=token_budget_single
    )

    added: List[Dict[str, Any]] = []
    total_tokens_added = 0
    expanded_ids: List[str] = []

    for att_id in parents_to_expand:
        try:
            extra = _fetch_attachment_chunks(
                mongo,
                attachment_id=att_id,
                exclude_chunk_ids=seen_chunk_ids,
                limit=max_chunks_per_parent,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Parent-doc expand failed for att={att_id}: {exc}")
            continue

        if not extra:
            continue

        # Pull chunks into THIS parent's budget. Each parent gets its own
        # independent allowance so a long parent doesn't starve the others.
        spent_this_parent = 0
        any_added_for_this_parent = False
        for ec in extra:
            t = _approx_chunk_tokens(ec)
            if spent_this_parent + t > per_parent_budget and any_added_for_this_parent:
                break
            added.append(ec)
            seen_chunk_ids.add(str(ec.get("_id")))
            spent_this_parent += t
            total_tokens_added += t
            any_added_for_this_parent = True

        if any_added_for_this_parent:
            expanded_ids.append(att_id)

    if not added:
        return ExpansionResult(
            chunks=list(retrieved_chunks),
            expanded_attachment_ids=[],
            tokens_added=0,
        )

    logger.debug(
        f"Parent-doc expanded {len(expanded_ids)} attachments "
        f"(+{len(added)} chunks, +{total_tokens_added} tok, "
        f"per-parent={per_parent_budget})"
    )
    return ExpansionResult(
        chunks=list(retrieved_chunks) + added,
        expanded_attachment_ids=expanded_ids,
        tokens_added=total_tokens_added,
    )


def neighbor_expand(
    mongo: MongoClientWrapper,
    *,
    retrieved_chunks: Sequence[Dict[str, Any]],
    window: int = 1,
    max_added: int = 40,
    per_parent_cap: int = 8,
) -> List[Dict[str, Any]]:
    """Pull the immediate neighbors (chunk_index ±window) of EVERY retrieved
    chunk from the same parent document.

    Unlike `parent_document_expand` (which needs 2+ hits from one doc to fire),
    this fires on a SINGLE hit — closing the "a fact got split across the chunk
    boundary and only one half was retrieved" gap (e.g. a lien amount whose
    body continued into the next chunk). Cheap, additive, fail-safe: neighbors
    are appended after the originals; the downstream evidence cap trims if
    needed. Pure read.
    """
    if not retrieved_chunks:
        return list(retrieved_chunks)
    seen = {str(c.get("_id")) for c in retrieved_chunks}
    # parent key -> (the field used, the value, set of wanted neighbor indices)
    wanted_by_parent: Dict[str, Dict[str, Any]] = {}
    for c in retrieved_chunks:
        ci = c.get("chunk_index")
        if ci is None:
            continue
        pk = c.get("attachment_id") or c.get("document_id")
        field = "attachment_id" if c.get("attachment_id") else "document_id"
        if pk is None:
            continue
        key = f"{field}:{pk}"
        slot = wanted_by_parent.setdefault(key, {"field": field, "value": pk, "idx": set()})
        for d in range(-window, window + 1):
            if d != 0 and ci + d >= 0:
                slot["idx"].add(ci + d)

    added: List[Dict[str, Any]] = []
    for slot in wanted_by_parent.values():
        if len(added) >= max_added:
            break
        want = sorted(slot["idx"])
        if not want:
            continue
        try:
            rows = mongo.chunks.find(
                {slot["field"]: slot["value"], "chunk_index": {"$in": want}},
                _PROJECTION,
            ).limit(per_parent_cap * 2)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"neighbor_expand query failed: {exc}")
            continue
        cnt = 0
        for r in rows:
            cid = str(r.get("_id"))
            if cid in seen:
                continue
            added.append(r)
            seen.add(cid)
            cnt += 1
            if cnt >= per_parent_cap or len(added) >= max_added:
                break
    if added:
        logger.debug(f"neighbor_expand added {len(added)} boundary-neighbor chunks")
    return list(retrieved_chunks) + added


def _per_parent_budget(n_parents: int, *, single_budget: int = 8000) -> int:
    """
    Per-parent token budget, scaled to the number of hot parents.

    Rationale: a single hot parent gets the full single_budget (default
    8K). With more hot parents we keep each budget reasonable so the
    aggregate doesn't blow past the total cap downstream.

    Returns budgets approximately:
      1 parent  → 8000
      2 parents → 5000 each
      3 parents → 4000 each
      4-5 parents → 3000 each
    """
    if n_parents <= 1:
        return single_budget
    if n_parents == 2:
        return int(single_budget * 0.625)  # 5000
    if n_parents == 3:
        return int(single_budget * 0.5)    # 4000
    return int(single_budget * 0.375)      # 3000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Same projection shape as hybrid_search so downstream code is happy.
_PROJECTION: Dict[str, Any] = {
    "_id": 1,
    "text": 1,
    "body": 1,
    "source_type": 1,
    "email_id": 1,
    "attachment_id": 1,
    "document_id": 1,
    "filename": 1,
    "page_start": 1,
    "page_end": 1,
    "date": 1,
    "from_email": 1,
    "to_emails": 1,
    "subject": 1,
    "folder_path": 1,
    "chunk_index": 1,
    "sha256": 1,
    "latest_date": 1,
    "occurrences": 1,
    # evidentiary spine (so the provenance footer stays correct on expanded chunks)
    "corpus": 1,
    "privilege_status": 1,
    "doc_source_type": 1,
}


def _fetch_attachment_chunks(
    mongo: MongoClientWrapper,
    *,
    attachment_id: Any,
    exclude_chunk_ids: set,
    limit: int,
) -> List[Dict[str, Any]]:
    """Return chunks of a given attachment in chunk_index order, excluding ones we already have."""
    # We allow attachment_id to be a string or ObjectId-coercible value;
    # we let MongoDB handle the matching with $in to be safe.
    candidates: List[Any] = [attachment_id]
    try:
        from bson import ObjectId  # local import to keep top-level light
        if isinstance(attachment_id, str) and len(attachment_id) == 24:
            candidates.append(ObjectId(attachment_id))
    except Exception:
        pass

    cursor = (
        mongo.chunks.find(
            {"attachment_id": {"$in": candidates}},
            _PROJECTION,
        )
        .sort([("chunk_index", 1)])
        .limit(max(1, limit) * 2)  # fetch a bit more, we'll filter excludes
    )
    out: List[Dict[str, Any]] = []
    for doc in cursor:
        cid = str(doc.get("_id"))
        if cid in exclude_chunk_ids:
            continue
        out.append(doc)
        if len(out) >= limit:
            break
    return out


def _approx_chunk_tokens(doc: Dict[str, Any]) -> int:
    """Approximate token count for budgeting (no tiktoken call here)."""
    body = doc.get("body") or doc.get("text") or ""
    # Rough rule of thumb: 1 token ≈ 4 characters in English text.
    return max(1, len(body) // 4)
