"""Participant analysis and the ingest/skip decision.

The rule (admin, 2026-09-02 — ``docs/13-INGESTION-SPEC.md``)
-----------------------------------------------------------
Applied in order, first match wins:

1. every address is RKB                      -> excluded, internal mail
2. otherwise any **external** address present -> ingest
3. otherwise the subject names one of the 15  -> ingest
4. otherwise                                  -> excluded

This is the rule the corpus was measured with before ingestion was authorised:
3,417 qualifying messages out of 47,099 in the window. Counting and ingesting
must apply the same rule or the corpus silently comes out short, so this
function is the only place the decision is made.

It replaces an earlier rule that additionally required a visible RKB address on
every message. That requirement was removed deliberately: Bcc is stripped from
the recipient's copy, so a lawyer writing to the builder and blind-copying
Rakesh Sir produced a message with no RKB address in its headers at all. The old
rule discarded those as "not our business record" while they sat in his mailbox.

Skipped messages are **counted, never stored** — provider id and reason only —
so reconciliation can distinguish "seen and deliberately excluded" from "missed".
Unknown addresses on skipped mail are still surfaced as discovery candidates, to
be promoted into the registry deliberately rather than silently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Set

from mangotree.config.registry import (
    ADDRESS_INDEX,
    RKB_DOMAINS,
    Person,
    Side,
    person_for_address,
    properties_named_in,
    side_for_address,
)

_ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

#: Domains that are never business counterparties even if they appear beside an
#: RKB address — bulk senders, notifications, marketing.
NOISE_DOMAIN_HINTS = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "notifications", "notification", "bounce", "postmaster", "newsletter",
)


class Decision(str, Enum):
    INGEST = "ingest"
    #: rule 1 — every participant is an RKB address
    SKIP_INTERNAL_ONLY = "skip_internal_only"
    #: rule 4 — no registry contact and no property named in the subject
    SKIP_NO_SIGNAL = "skip_no_signal"
    SKIP_OUT_OF_RANGE = "skip_out_of_range"


@dataclass
class ParticipantSet:
    """Every address on a message, split by side, with people resolved."""

    from_addrs: List[str] = field(default_factory=list)
    to_addrs: List[str] = field(default_factory=list)
    cc_addrs: List[str] = field(default_factory=list)
    bcc_addrs: List[str] = field(default_factory=list)

    @property
    def all_addrs(self) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for addr in (*self.from_addrs, *self.to_addrs, *self.cc_addrs, *self.bcc_addrs):
            if addr and addr not in seen:
                seen.add(addr)
                out.append(addr)
        return out

    # -- side partitions -------------------------------------------------
    @property
    def rkb_addrs(self) -> List[str]:
        return [a for a in self.all_addrs if side_for_address(a) is Side.RKB]

    @property
    def known_external_addrs(self) -> List[str]:
        return [a for a in self.all_addrs if side_for_address(a) is Side.EXTERNAL]

    @property
    def unknown_addrs(self) -> List[str]:
        return [a for a in self.all_addrs if side_for_address(a) is None]

    @property
    def people(self) -> List[Person]:
        seen: Set[str] = set()
        out: List[Person] = []
        for addr in self.all_addrs:
            person = person_for_address(addr)
            if person and person.person_id not in seen:
                seen.add(person.person_id)
                out.append(person)
        return out

    @property
    def person_ids(self) -> List[str]:
        return [p.person_id for p in self.people]


@dataclass
class FilterResult:
    decision: Decision
    participants: ParticipantSet
    reason: str
    #: unknown addresses that look like real business counterparties — surfaced
    #: for deliberate promotion into the registry (never auto-ingested).
    discovery_candidates: List[str] = field(default_factory=list)

    @property
    def ingest(self) -> bool:
        return self.decision is Decision.INGEST


def extract_addresses(header_value: Optional[str]) -> List[str]:
    """Pull bare addresses out of a raw header value.

    Handles ``"Name" <a@b.com>, c@d.com`` and de-duplicates case-insensitively
    while preserving order.
    """
    if not header_value:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for match in _ADDR_RE.findall(header_value):
        addr = match.strip().lower().rstrip(".")
        if addr not in seen:
            seen.add(addr)
            out.append(addr)
    return out


def build_participants(headers: Dict[str, str]) -> ParticipantSet:
    """Build a ParticipantSet from a case-insensitive header mapping."""
    lower = {k.lower(): v for k, v in headers.items() if v}
    return ParticipantSet(
        from_addrs=extract_addresses(lower.get("from")),
        to_addrs=extract_addresses(lower.get("to")),
        cc_addrs=extract_addresses(lower.get("cc")),
        bcc_addrs=extract_addresses(lower.get("bcc")),
    )


def _looks_like_noise(address: str) -> bool:
    local, _, domain = address.partition("@")
    blob = f"{local}.{domain}"
    return any(hint in blob for hint in NOISE_DOMAIN_HINTS)


def _plausible_business_address(address: str) -> bool:
    """A conservative filter for discovery candidates.

    We do not try to be clever here — anything that survives is only ever a
    *suggestion* for a human to promote into the registry.
    """
    if _looks_like_noise(address):
        return False
    domain = address.rsplit("@", 1)[-1]
    return domain not in RKB_DOMAINS


def decide(participants: ParticipantSet, *, subject: str = "") -> FilterResult:
    """Apply the admin rule to a participant set and its subject line.

    ``subject`` is required for rule 3 and defaults to empty only so that
    callers testing participants alone stay readable. Omitting a real subject in
    production would silently disable rule 3, which is why the pipeline passes
    it explicitly.
    """
    known_external = participants.known_external_addrs
    unknown = participants.unknown_addrs
    everyone = participants.all_addrs

    # 1 — internal mail. Checked first so it outranks a property in the subject:
    # a Rakesh-to-JP message titled "Varnum payoff" is still internal.
    if everyone and not known_external and not unknown:
        return FilterResult(
            Decision.SKIP_INTERNAL_ONLY,
            participants,
            "every participant is an RKB address",
        )

    # 2 — a registered counterparty is on the message.
    if known_external:
        return FilterResult(
            Decision.INGEST,
            participants,
            f"registry contact present ({', '.join(known_external[:3])})",
        )

    # 3 — no known contact, but the subject names a property we hold.
    named = properties_named_in(subject)
    if named:
        return FilterResult(
            Decision.INGEST,
            participants,
            f"subject names {', '.join(sorted(named))}",
        )

    # 4 — nothing ties this to the portfolio.
    return FilterResult(
        Decision.SKIP_NO_SIGNAL,
        participants,
        "no registry contact and no property named in the subject",
        discovery_candidates=[a for a in unknown if _plausible_business_address(a)],
    )
