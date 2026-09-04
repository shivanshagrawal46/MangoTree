"""Property-aware segmentation — the defence against cross-property contamination.

The problem
-----------
One email says:

    "Varnum tile is done. Decatur needs $4k for the roof."

Tag that email with ``[varnum, decatur_st]`` and embed it whole, and a question
about Decatur retrieves the Varnum sentence. The model then has fluent, cited,
*wrong* evidence in front of it — and confidently misattributes Varnum's progress
to Decatur. Ranking cannot save you here: the contaminating text is genuinely
similar and genuinely in a document about that property.

The fix
-------
Push the property tag **down to the segment**. Split the text into paragraph-ish
units, attribute each unit to the property *it* discusses, and tag chunks only
with the properties their own content concerns. A Decatur query then never has
the Varnum sentence in its candidate pool at all.

Attribution rules, in order:

1. **Explicit mention** — the segment names a property. A numbered address is
   decisive; a bare short name ("Varnum") is strong enough to attribute but not
   to auto-assign, because every single-word alias in this registry identifies
   exactly one property and the shared ones are blocked outright.
2. **Carry-forward** — a segment with no property of its own inherits the
   previous segment's, because prose continues a subject across sentences
   ("Varnum tile is done. The painter starts Monday.").
3. **Carry-forward is bounded**: it stops at a heading, at any mention of a
   *different* property however weak, or after ``CARRY_LIMIT`` segments — an
   unbounded carry would re-create the contamination it exists to prevent.
4. **Document-level fallback** — if the whole artifact resolved to exactly one
   property, unattributed segments belong to it. With two or more, they stay
   ``ambiguous`` and are excluded from single-property retrieval.

Rule 4 is the important asymmetry: for a single-property document, unattributed
text is safe; for a multi-property document, unattributed text is *precisely*
the dangerous case, so it is withheld rather than guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from mangotree.config.registry import PROPERTY_INDEX, properties_possibly_named_in
from mangotree.resolve.property_resolver import (
    CONFIDENCE_BAR,
    _match_aliases,
)

#: How many consecutive unattributed segments may inherit the previous property.
CARRY_LIMIT = 3

#: A segment must reach this to claim a property by explicit mention.
SEGMENT_BAR = 0.45

_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s|\d+[.)]\s+[A-Z]|[A-Z][A-Za-z \-/]{2,50}:\s*$|-{3,}|={3,})"
)
_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass
class Segment:
    index: int
    text: str
    property_ids: List[str] = field(default_factory=list)
    attribution: str = "unattributed"   # explicit | carried | document | ambiguous
    confidence: float = 0.0
    is_heading: bool = False

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "property_ids": self.property_ids,
            "attribution": self.attribution,
            "confidence": round(self.confidence, 3),
        }


def split_segments(text: str) -> List[str]:
    """Split into paragraph-ish units, keeping list items as their own segments.

    List items matter: draw-request emails are usually bulleted, and one bullet
    per property is the single most common multi-property shape in this corpus.
    """
    if not text:
        return []

    raw_blocks = re.split(r"\n\s*\n", text)
    segments: List[str] = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        # A block of list items becomes one segment per item so a per-property
        # bullet is never merged with its neighbours.
        if sum(1 for l in lines if _LIST_ITEM.match(l)) >= 2:
            current: List[str] = []
            for line in lines:
                if _LIST_ITEM.match(line) and current:
                    segments.append("\n".join(current).strip())
                    current = [line]
                else:
                    current.append(line)
            if current:
                segments.append("\n".join(current).strip())
        else:
            segments.append(block)

    return [s for s in segments if s]


def _explicit_properties(text: str) -> Dict[str, float]:
    """Properties explicitly named in this segment, with confidence."""
    hits = _match_aliases(text, "alias_body")
    return {pid: hit.confidence for pid, hit in hits.items() if hit.confidence >= SEGMENT_BAR}


def _any_mentions(text: str) -> Set[str]:
    """Every property the text could concern, however weakly.

    Two sources, because neither alone is complete: the alias matcher catches
    below-bar mentions like a bare "Varnum", and the registry's contested-street
    lookup catches names such as "Bayshore" that are deliberately in no property's
    alias list and are therefore invisible to the matcher.
    """
    return set(_match_aliases(text, "alias_body")) | properties_possibly_named_in(text)


def _names_a_different_property(text: str, carried: Sequence[str]) -> bool:
    """True when this segment names a property other than the carried one.

    Carry-forward is the segmenter's most dangerous rule, because it assigns a
    property to text that never named one. It has to stop the moment the text
    names someone else — and *below-bar* mentions have to count here, even though
    they are too weak to attribute on their own.

    Without this check the sentence "Decatur still needs $4,000 for the roof"
    following a Varnum paragraph inherited ``varnum`` and put Decatur's money on
    Varnum's ledger. A weak signal is not strong enough to claim a segment, but
    it is more than strong enough to veto someone else's claim.
    """
    return bool(_any_mentions(text) - set(carried))


def segment_text(
    text: str,
    *,
    document_property_ids: Sequence[str] = (),
) -> List[Segment]:
    """Split text and attribute each segment to the properties it concerns."""
    raw = split_segments(text)
    segments: List[Segment] = []

    doc_props = list(document_property_ids)
    single_property_doc = len(doc_props) == 1

    carried: List[str] = []
    carried_conf = 0.0
    carry_used = 0

    for index, block in enumerate(raw):
        is_heading = bool(_HEADING.match(block)) and len(block) < 120
        explicit = _explicit_properties(block)

        if explicit:
            ordered = sorted(explicit.items(), key=lambda kv: -kv[1])
            prop_ids = [pid for pid, _ in ordered]
            best = ordered[0][1]
            segment = Segment(
                index=index, text=block, property_ids=prop_ids,
                attribution="explicit", confidence=best, is_heading=is_heading,
            )
            carried, carried_conf, carry_used = prop_ids, best, 0

        elif (
            carried
            and carry_used < CARRY_LIMIT
            and not is_heading
            and not _names_a_different_property(block, carried)
        ):
            carry_used += 1
            # Carried attribution decays: the further from the explicit mention,
            # the weaker the claim.
            confidence = carried_conf * (0.85 ** carry_used)
            segment = Segment(
                index=index, text=block, property_ids=list(carried),
                attribution="carried", confidence=confidence, is_heading=is_heading,
            )

        elif single_property_doc:
            segment = Segment(
                index=index, text=block, property_ids=list(doc_props),
                attribution="document", confidence=0.6, is_heading=is_heading,
            )

        else:
            # Multi-property (or unresolved) document with no local signal:
            # withhold rather than guess.
            segment = Segment(
                index=index, text=block, property_ids=[],
                attribution="ambiguous", confidence=0.0, is_heading=is_heading,
            )
            carried, carried_conf, carry_used = [], 0.0, 0

        segments.append(segment)

    return segments


def properties_for_retrieval(segments: Sequence[Segment]) -> Set[str]:
    """Union of every property any segment is confident about."""
    out: Set[str] = set()
    for segment in segments:
        if segment.attribution != "ambiguous":
            out.update(segment.property_ids)
    return out


def segments_for_property(
    segments: Sequence[Segment], property_id: str
) -> List[Segment]:
    """Only the segments that concern one property.

    This is the function that makes per-property analysis honest: everything the
    analysis sees came from text about *that* property.
    """
    return [s for s in segments if property_id in s.property_ids]


def contamination_report(segments: Sequence[Segment]) -> dict:
    """Diagnostics for the multi-property case — used by the leak test."""
    per_property: Dict[str, int] = {}
    for segment in segments:
        for pid in segment.property_ids:
            per_property[pid] = per_property.get(pid, 0) + 1
    return {
        "segments": len(segments),
        "properties": sorted(per_property),
        "segments_per_property": per_property,
        "ambiguous": sum(1 for s in segments if s.attribution == "ambiguous"),
        "carried": sum(1 for s in segments if s.attribution == "carried"),
        "explicit": sum(1 for s in segments if s.attribution == "explicit"),
    }
