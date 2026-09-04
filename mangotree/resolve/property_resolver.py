"""Property resolution — deterministic first, review queue for the rest.

Resolution returns a **set** of properties: one email routinely concerns several
(the 904 vs 910 Bayshore pair is the standing hazard). Signals, strongest first:

1. ``disk_folder``      — for the E:\\ corpus the folder *is* the property.
2. explicit alias hit   — "904 Bayshore", "1512 Varnum", "3731 9th St".
3. attachment filenames — documents are named after their property far more
                          reliably than email bodies are.
4. thread inheritance   — a reply inherits its thread's resolution unless the
                          body contradicts it.
5. deal-contact hint    — a message with Ali Parva on it is probably Ridge Road.

Ambiguous single-token aliases (a bare "Bayshore") are never sufficient on their
own: they raise ``ambiguous`` and go to review rather than guessing between two
loans on the same street.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Set

from mangotree.config.registry import (
    ALIAS_PATTERNS,
    AMBIGUOUS_ALIASES,
    PROPERTY_CONTACTS,
    PROPERTY_INDEX,
    PROPERTIES,
    normalize_text,
)


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"      # matched, but not confidently enough to assign
    UNRESOLVED = "unresolved"    # no signal at all


@dataclass
class PropertyHit:
    property_id: str
    confidence: float
    signals: List[str] = field(default_factory=list)

    @property
    def canonical(self) -> str:
        prop = PROPERTY_INDEX.get(self.property_id)
        return prop.canonical_address if prop else self.property_id


@dataclass
class Resolution:
    status: ResolutionStatus
    hits: List[PropertyHit] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def property_ids(self) -> List[str]:
        return [h.property_id for h in self.hits]

    @property
    def needs_review(self) -> bool:
        return self.status is not ResolutionStatus.RESOLVED


#: A hit must reach this to be auto-assigned without review.
CONFIDENCE_BAR = 0.70

#: Where the alias was found. Subject and filenames are deliberate labels;
#: body text is looser but still direct evidence.
_SIGNAL_WEIGHTS = {
    "disk_folder": 1.00,
    "alias_subject": 0.85,
    "alias_filename": 0.80,
    "alias_body": 0.75,
    "thread_inherit": 0.60,
    "contact_hint": 0.35,
}

#: How *identifying* the matched alias is. This matters more than where it was
#: found: "910 Bayshore" in a body is far stronger evidence than "Bayshore" in a
#: subject, because a street number names exactly one loan while a street name
#: may name several. Without this distinction the resolver either floods the
#: review queue with unambiguous addresses or, worse, treats a bare street name
#: as decisive.
#:
#: ``single`` was 0.55 until 2026-09-02, which put a bare "Varnum" in body text at
#: 0.41 — just under the segmenter's 0.45 bar. Every bare short name therefore
#: failed to attribute, and the admin's account of this corpus is that the bare
#: short name is the *usual* form in email. The discount was also double-counting:
#: it existed because a street name "may name several", but ``AMBIGUOUS_ALIASES``
#: already blocks the only shared one (Bayshore), and an audit of the registry
#: found zero unblocked collisions among the seven single-word aliases.
#:
#: At 0.85 a bare name in body text scores 0.64 — above the segmenter's bar so the
#: text attributes, still below ``CONFIDENCE_BAR`` so it never auto-assigns
#: without review. That is the intended reading: strong enough to keep a
#: property's sentences together, not strong enough to decide a loan on its own.
_SPECIFICITY = {
    "numbered": 1.00,   # carries a street number: "910 Bayshore", "1512 Varnum St NW"
    "multi": 0.90,      # multi-word, no number: "Narrow Guage", "Tower Road"
    "single": 0.85,     # one bare word: "Varnum", "Euclid" — unique in this registry
    "ambiguous": 0.40,  # a street shared by two properties: "Bayshore"
}

_HAS_NUMBER = re.compile(r"\b\d{1,6}(?:st|nd|rd|th)?\b")


def _specificity(alias: str) -> str:
    norm = normalize_text(alias)
    if norm in AMBIGUOUS_ALIASES:
        return "ambiguous"
    if _HAS_NUMBER.search(norm):
        return "numbered"
    return "multi" if len(norm.split()) > 1 else "single"


def _match_aliases(text: str, signal: str) -> Dict[str, PropertyHit]:
    """Find every property alias present in ``text``.

    Longest aliases are tested first and their span is consumed, so matching
    "904 Bayshore Dr" prevents a second, ambiguous "Bayshore" hit on the same
    words.
    """
    hits: Dict[str, PropertyHit] = {}
    if not text:
        return hits

    norm = normalize_text(text)
    if not norm:
        return hits

    consumed: List[tuple] = []

    def overlaps(start: int, end: int) -> bool:
        return any(not (end <= s or start >= e) for s, e in consumed)

    for property_id, pattern, _tokens, alias in ALIAS_PATTERNS:
        for match in pattern.finditer(norm):
            if overlaps(match.start(), match.end()):
                continue
            consumed.append((match.start(), match.end()))

            kind = _specificity(alias)
            confidence = _SIGNAL_WEIGHTS.get(signal, 0.5) * _SPECIFICITY[kind]
            label = f"{signal}:{alias}({kind})"

            existing = hits.get(property_id)
            if existing is None or confidence > existing.confidence:
                hits[property_id] = PropertyHit(
                    property_id=property_id,
                    confidence=confidence,
                    signals=[label],
                )
            elif label not in existing.signals:
                existing.signals.append(label)
    return hits


def _merge(into: Dict[str, PropertyHit], new: Dict[str, PropertyHit]) -> None:
    """Combine hits, taking the strongest confidence and unioning signals.

    Independent signals reinforce each other: two mid-strength hits on the same
    property lift confidence rather than merely tying.
    """
    for property_id, hit in new.items():
        existing = into.get(property_id)
        if existing is None:
            into[property_id] = hit
            continue
        combined = 1.0 - (1.0 - existing.confidence) * (1.0 - hit.confidence)
        existing.confidence = min(0.99, combined)
        for signal in hit.signals:
            if signal not in existing.signals:
                existing.signals.append(signal)


def resolve_property(
    *,
    subject: str = "",
    body: str = "",
    filenames: Sequence[str] = (),
    disk_folder: Optional[str] = None,
    thread_property_ids: Sequence[str] = (),
    person_ids: Sequence[str] = (),
) -> Resolution:
    """Resolve a message/document to zero or more properties."""
    hits: Dict[str, PropertyHit] = {}
    notes: List[str] = []

    # 1) disk folder — authoritative for the on-disk corpus
    if disk_folder:
        for prop in PROPERTIES:
            if prop.disk_folder and prop.disk_folder == disk_folder:
                hits[prop.property_id] = PropertyHit(
                    prop.property_id, _SIGNAL_WEIGHTS["disk_folder"], [f"disk_folder:{disk_folder}"]
                )
                break
        else:
            notes.append(f"disk folder '{disk_folder}' is not in the property registry")

    # 2/3) aliases in subject, filenames, body
    _merge(hits, _match_aliases(subject, "alias_subject"))
    for name in filenames:
        _merge(hits, _match_aliases(name, "alias_filename"))
    _merge(hits, _match_aliases(body, "alias_body"))

    # 4) thread inheritance — only when the message itself said nothing
    if not hits and thread_property_ids:
        for property_id in thread_property_ids:
            hits[property_id] = PropertyHit(
                property_id, _SIGNAL_WEIGHTS["thread_inherit"], ["thread_inherit"]
            )
        notes.append("inherited from thread (message body carried no property signal)")

    # 5) deal-contact hint — weak, never sufficient alone
    if person_ids:
        person_set = set(person_ids)
        for property_id, contacts in PROPERTY_CONTACTS.items():
            overlap = person_set.intersection(contacts)
            if overlap:
                _merge(
                    hits,
                    {
                        property_id: PropertyHit(
                            property_id,
                            _SIGNAL_WEIGHTS["contact_hint"],
                            [f"contact_hint:{','.join(sorted(overlap))}"],
                        )
                    },
                )

    if not hits:
        return Resolution(ResolutionStatus.UNRESOLVED, [], notes or ["no property signal found"])

    ordered = sorted(hits.values(), key=lambda h: h.confidence, reverse=True)
    confident = [h for h in ordered if h.confidence >= CONFIDENCE_BAR]

    if confident:
        # Keep every property that cleared the bar — multi-property is normal.
        return Resolution(ResolutionStatus.RESOLVED, confident, notes)

    notes.append(
        "best signal below the confidence bar: "
        + ", ".join(f"{h.canonical}={h.confidence:.2f}" for h in ordered[:3])
    )
    return Resolution(ResolutionStatus.AMBIGUOUS, ordered[:3], notes)
