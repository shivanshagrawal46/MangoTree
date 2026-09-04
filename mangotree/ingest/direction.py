"""Direction attribution — sent vs received, per mailbox occurrence.

Why this module exists
----------------------
Rakesh Sir composes business mail inside ``rakesh.bhargava@gmail.com`` but sends
it under the ``rakesh@mtreh.com`` "send mail as" alias. The naive rule
("sent if From == mailbox address") therefore **misfiles his own outbound mail as
received from a stranger** — a corrupted record, worse than a missing one.

The rule we use instead is folder-first:

1. If the message carries the provider's sent marker (Gmail ``SENT`` label /
   Outlook Sent Items), it is ``sent``. Only the account owner's outbound mail
   lands there — the provider writes it as part of the send operation itself, so
   membership is *sufficient* evidence and no header inspection is needed.
2. Otherwise, if the From address belongs to the same **person** who owns the
   mailbox (via the alias registry), it is ``sent`` — this catches sent mail that
   was archived out of the Sent folder.
3. Otherwise it is ``received``.

Direction belongs to the *occurrence*, never to the artifact: one email is
legitimately ``sent`` in Rakesh's Gmail and ``received`` in a monitored
counterparty mailbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Sequence

from mangotree.config.registry import INGESTED_MAILBOXES, person_for_address

GMAIL_SENT_LABEL = "SENT"
GMAIL_DRAFT_LABEL = "DRAFT"


class Direction(str, Enum):
    SENT = "sent"
    RECEIVED = "received"
    DRAFT = "draft"


@dataclass
class DirectionResult:
    direction: Direction
    basis: str
    #: True when the sender used a send-as alias rather than the mailbox address.
    via_alias: bool = False
    alias_used: Optional[str] = None
    #: person_id credited as the author (never a raw address)
    author_person_id: Optional[str] = None


def mailbox_owner_person_id(mailbox: str) -> Optional[str]:
    entry = INGESTED_MAILBOXES.get((mailbox or "").lower())
    if entry:
        return entry.get("person_id")
    person = person_for_address(mailbox)
    return person.person_id if person else None


def resolve_direction(
    *,
    mailbox: str,
    from_addrs: Sequence[str],
    labels: Optional[Iterable[str]] = None,
    folder: Optional[str] = None,
) -> DirectionResult:
    """Decide direction for one message *as seen in one mailbox*."""
    labels = {str(label).upper() for label in (labels or [])}
    folder_norm = (folder or "").strip().lower()

    mailbox_lc = (mailbox or "").lower()
    owner_id = mailbox_owner_person_id(mailbox_lc)
    from_addr = (from_addrs[0].lower() if from_addrs else "")
    from_person = person_for_address(from_addr)

    author_id = from_person.person_id if from_person else None
    via_alias = bool(
        owner_id and author_id == owner_id and from_addr and from_addr != mailbox_lc
    )

    # 1) provider sent marker — authoritative
    if GMAIL_DRAFT_LABEL in labels or folder_norm in {"drafts", "draft"}:
        return DirectionResult(
            Direction.DRAFT, "provider draft marker", via_alias, from_addr or None, author_id or owner_id
        )

    if GMAIL_SENT_LABEL in labels or folder_norm in {"sent", "sent items", "sent mail"}:
        basis = "provider SENT marker"
        if via_alias:
            basis += f" (sent-as alias {from_addr})"
        # In the Sent folder the author is the mailbox owner by construction,
        # even if the From header shows an alias we have not registered yet.
        return DirectionResult(
            Direction.SENT, basis, via_alias, from_addr or None, author_id or owner_id
        )

    # 2) same person, different address — archived sent mail
    if owner_id and author_id and author_id == owner_id:
        return DirectionResult(
            Direction.SENT,
            "From address resolves to the mailbox owner via the alias registry",
            via_alias,
            from_addr or None,
            author_id,
        )

    # 3) default
    return DirectionResult(
        Direction.RECEIVED, "not in a sent folder and From is not the mailbox owner",
        False, from_addr or None, author_id,
    )
