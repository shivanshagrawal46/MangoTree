"""Retrieval metadata carried on every chunk.

A chunk is what search returns, so anything a query needs to *narrow* by has to
live on the chunk itself. Fields only present on the parent artifact can be read
after a result comes back, but they cannot shape the search — and for a date
that is the difference between "search March 2025" and "search everything, then
discard what is not March 2025".

Defined once and used by both the indexer and the backfill, because two copies
of this logic would drift and the drift would be invisible: a filter that
disagrees with the data silently returns the wrong candidate set rather than an
error.

``occurrences`` is folded down rather than copied wholesale. The full array on
every chunk would multiply storage across the ~12,800 chunks for little gain;
what a query actually needs is "which mailboxes and folders was this seen in"
and "which emails carried this document", both of which are small.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: Kept for documentation and for the index definition to iterate over.
RETRIEVAL_FIELDS = (
    "from_email",
    "date_ym",
    "date_year",
    "latest_date",
    "folder_path",
    "filename",
    "extension",
    "scope",
    "common_kind",
    "common_topics",
    "placement",
    "parent_email_shas",
    "occurrence_count",
)


def _first(values: Optional[Sequence[str]]) -> Optional[str]:
    for value in values or []:
        if value:
            return value.lower()
    return None


def chunk_metadata(
    artifact: dict,
    *,
    occurrences: Sequence[dict] = (),
    parent_emails: Sequence[dict] = (),
) -> dict:
    """Retrieval metadata for chunks of ``artifact``.

    ``parent_emails`` matters for attachments: a PDF carries no sender or date
    of its own, but the emails that delivered it do, and those are what a person
    means when they ask "who sent this and when".
    """
    source_type = artifact.get("source_type")
    filename = artifact.get("filename") or ""
    extension = (Path(filename).suffix or "").lower() or None

    date = artifact.get("date")
    # An attachment forwarded three times has three dates; the earliest is when
    # the document entered the record and the latest is when it was last acted
    # on. Both are useful, so the primary date stays the artifact's own and
    # latest_date tracks the most recent appearance.
    dates = [date] if date else []
    dates += [e.get("date") for e in parent_emails if e.get("date")]
    dates += [o.get("date") for o in occurrences if o.get("date")]
    dated = [d for d in dates if hasattr(d, "strftime")]

    if source_type == "email":
        from_email = _first((artifact.get("participants") or {}).get("from"))
    else:
        from_email = _first(
            [
                addr
                for email in parent_emails
                for addr in ((email.get("participants") or {}).get("from") or [])
            ]
        )

    folders: List[str] = []
    if artifact.get("relative_path"):
        # Disk provenance: the folder is the property, so the path is evidence.
        folders.append(str(Path(artifact["relative_path"]).parent).replace("\\", "/"))
    for occ in occurrences:
        folder = occ.get("folder")
        if folder and folder not in folders:
            folders.append(folder)
    for email in parent_emails:
        for path in email.get("_folders") or []:
            if path and path not in folders:
                folders.append(path)

    latest = max(dated) if dated else None

    return {
        "from_email": from_email,
        "date_ym": date.strftime("%Y-%m") if hasattr(date, "strftime") else None,
        "date_year": date.year if hasattr(date, "year") else None,
        "latest_date": latest,
        "folder_path": folders or None,
        "filename": filename or None,
        "extension": extension,
        # Mirrors the artifact so a property chat can exclude the common store
        # during the search rather than after it. Absent on chunks until now,
        # which made the filter match nothing at all.
        "scope": artifact.get("scope") or ("property" if artifact.get("property_ids") else "common"),
        # Only set on confident-common artifacts, by the common-store classifier.
        # ``portfolio`` is searched in property chats; ``business`` is not.
        "common_kind": artifact.get("common_kind"),
        "common_topics": artifact.get("common_topics") or None,
        # property | portfolio | unplaced | business — one token for the scope
        # filter in both search indexes.
        "placement": (
            "property" if artifact.get("property_ids")
            else artifact.get("common_kind") if artifact.get("common_kind") in ("portfolio", "business")
            else "unplaced"
        ),
        "parent_email_shas": list(artifact.get("parent_email_shas") or []) or None,
        "occurrence_count": (
            len(artifact.get("parent_email_shas") or []) or len(occurrences) or 1
        ),
    }


def occurrences_by_artifact(mongo, shas: Sequence[str]) -> Dict[str, List[dict]]:
    """Occurrence rows grouped by artifact, in one query."""
    grouped: Dict[str, List[dict]] = {}
    if not shas:
        return grouped
    cursor = mongo.occurrences.find(
        {"artifact_sha": {"$in": list(shas)}},
        {"artifact_sha": 1, "folder": 1, "mailbox": 1, "date": 1, "direction": 1},
    )
    for row in cursor:
        grouped.setdefault(row["artifact_sha"], []).append(row)
    return grouped
