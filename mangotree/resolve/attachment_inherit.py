"""Give attachments the property of the email that carried them.

Why inheritance and not just resolution
---------------------------------------
An attachment is often named in a way that says nothing about which deal it
belongs to: `ALTA Buyer's Settlement Statement (81).pdf`, `image.png`,
`Invoice Calculations.xlsx`. Resolved on its own filename it is unattributable,
so it lands nowhere and is invisible to every property's analysis — while the
email that carried it resolved cleanly.

The email is the context. `Tahona ALTA.pdf` arrived on a thread titled
"8514 Tahona Dr — DO NOT CLOSE/WIRE TODAY", and that thread is what tells us what
the settlement statement is for.

Precedence, and why it is ordered this way
------------------------------------------
1. **The attachment's own explicit signal wins.** If the filename names a
   property, that is direct evidence about the file itself, and it survives even
   when the parent disagrees — people attach one deal's document to another
   deal's thread constantly.
2. **Otherwise inherit the parent's properties.** Marked as inherited, never
   presented as if the file itself asserted it.
3. **Conflicts are surfaced, not silently merged.** An attachment whose filename
   says 904 Bayshore on a thread about 910 Bayshore is exactly the case that must
   never be quietly resolved to both — that is how one loan's numbers end up in
   the other's analysis.

A multi-property parent is a real case too: an email covering three deals with
one attachment cannot tell us which deal the attachment belongs to, so it is
inherited across all of them and flagged for review rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from mangotree.core.logging import logger


@dataclass
class InheritStats:
    considered: int = 0
    inherited: int = 0
    own_signal_kept: int = 0
    conflicts: int = 0
    parent_unresolved: int = 0
    no_parent: int = 0
    ambiguous_multi_parent: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "inherited": self.inherited,
            "own_signal_kept": self.own_signal_kept,
            "conflicts": self.conflicts,
            "parent_unresolved": self.parent_unresolved,
            "no_parent": self.no_parent,
            "ambiguous_multi_parent": self.ambiguous_multi_parent,
            "errors": self.errors[:20],
        }


@dataclass
class InheritDecision:
    sha: str
    filename: str
    property_ids: List[str]
    method: str
    parent_property_ids: List[str] = field(default_factory=list)
    own_property_ids: List[str] = field(default_factory=list)
    needs_review: bool = False
    note: str = ""


def _own_signal(filename: str) -> List[str]:
    """Properties named by the attachment's own filename.

    Deliberately reuses the shared resolver so alias handling — including the
    904/910 Bayshore distinction — is defined in exactly one place.
    """
    from mangotree.resolve.property_resolver import resolve_property

    resolution = resolve_property(subject=filename, filenames=[filename])
    return list(resolution.property_ids)


def decide_for_attachment(
    attachment: dict, parents: Sequence[dict], *, stats: InheritStats
) -> Optional[InheritDecision]:
    stats.considered += 1
    sha = attachment.get("sha256", "")
    filename = attachment.get("filename") or ""

    own = _own_signal(filename)
    parent_props: List[str] = []
    for parent in parents:
        for pid in (parent.get("property_ids") or []):
            if pid not in parent_props:
                parent_props.append(pid)

    if not parents:
        stats.no_parent += 1
        if own:
            return InheritDecision(
                sha=sha, filename=filename, property_ids=own,
                method="own_filename_no_parent", own_property_ids=own,
            )
        return None

    if own:
        conflict = bool(parent_props) and not set(own) & set(parent_props)
        if conflict:
            stats.conflicts += 1
            # Keep the attachment's own reading and flag it. Merging both would
            # put this document into a property no evidence links it to.
            return InheritDecision(
                sha=sha, filename=filename, property_ids=own,
                method="own_filename_conflicts_parent",
                parent_property_ids=parent_props, own_property_ids=own,
                needs_review=True,
                note=(f"filename indicates {own} but the carrying email resolved "
                      f"to {parent_props} — verify before relying on either"),
            )
        stats.own_signal_kept += 1
        return InheritDecision(
            sha=sha, filename=filename, property_ids=own,
            method="own_filename", parent_property_ids=parent_props,
            own_property_ids=own,
        )

    if not parent_props:
        stats.parent_unresolved += 1
        return None

    ambiguous = len(parent_props) > 1
    if ambiguous:
        stats.ambiguous_multi_parent += 1
    stats.inherited += 1
    return InheritDecision(
        sha=sha, filename=filename, property_ids=parent_props,
        method="inherited_from_email", parent_property_ids=parent_props,
        needs_review=ambiguous,
        note=("carrying email covers multiple properties; which one this "
              "attachment belongs to is not determined by the email alone")
        if ambiguous else "",
    )


def inherit_properties(
    mongo, *, apply: bool = True, limit: Optional[int] = None
) -> tuple[InheritStats, List[InheritDecision]]:
    """Resolve every attachment that currently has no property."""
    stats = InheritStats()
    decisions: List[InheritDecision] = []

    query = {
        # ``source_types`` so an attachment that also lives in the disk corpus is
        # still treated as attached evidence here.
        "source_types": "attachment",
        "$or": [
            {"property_ids": None},
            {"property_ids": {"$size": 0}},
            {"property_ids": {"$exists": False}},
        ],
    }
    attachments = list(mongo.artifacts.find(
        query,
        {"sha256": 1, "filename": 1, "parent_email_shas": 1, "property_ids": 1},
    ))
    if limit:
        attachments = attachments[:limit]

    # One batched lookup for every parent, rather than a query per attachment.
    parent_shas = {
        sha
        for a in attachments
        for sha in (a.get("parent_email_shas") or [])
    }
    parents_by_sha: Dict[str, dict] = {
        p["sha256"]: p
        for p in mongo.artifacts.find(
            {"sha256": {"$in": list(parent_shas)}},
            {"sha256": 1, "property_ids": 1, "subject": 1},
        )
    } if parent_shas else {}

    for attachment in attachments:
        parents = [
            parents_by_sha[sha]
            for sha in (attachment.get("parent_email_shas") or [])
            if sha in parents_by_sha
        ]
        decision = decide_for_attachment(attachment, parents, stats=stats)
        if decision:
            decisions.append(decision)

    if apply and decisions:
        from pymongo import UpdateOne

        now = datetime.now(timezone.utc)
        operations = []
        for d in decisions:
            operations.append(UpdateOne(
                {"sha256": d.sha},
                {"$set": {
                    "property_ids": d.property_ids,
                    # Provenance is not optional here: an inherited property is a
                    # weaker claim than a resolved one, and an analyst reading a
                    # citation is entitled to know which they are looking at.
                    "property_attribution": {
                        "method": d.method,
                        "parent_property_ids": d.parent_property_ids,
                        "own_property_ids": d.own_property_ids,
                        "inherited": d.method == "inherited_from_email",
                        "needs_review": d.needs_review,
                        "note": d.note,
                        "resolved_at": now,
                    },
                }},
            ))
        mongo.artifacts.bulk_write(operations, ordered=False)
        logger.info("Attachment inheritance applied to %d artifacts", len(operations))

        review = [d for d in decisions if d.needs_review]
        if review:
            mongo.db["review_queue"].bulk_write([
                UpdateOne(
                    {"reference": f"attachment:{d.sha}"},
                    {"$set": {
                        "reference": f"attachment:{d.sha}",
                        "reason": "attachment_property_ambiguous",
                        "detail": d.note,
                        "filename": d.filename,
                        "property_ids": d.property_ids,
                        "created_at": now,
                    }},
                    upsert=True,
                )
                for d in review
            ], ordered=False)

    return stats, decisions
