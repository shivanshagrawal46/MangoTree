"""Why a given phrase does or does not resolve to a property."""
import sys

sys.path.insert(0, ".")

from mangotree.chunk.segmenter import segment_text
from mangotree.config.registry import properties_named_in

PHRASES = [
    "At 1330 Decatur the contractor finished the roof.",
    "At 912 Decatur the contractor finished the roof.",
    "At Decatur the contractor finished the roof.",
    "At 1512 Varnum the contractor finished the roof.",
    "At 904 Bayshore the tile is done.",
    "At 910 Bayshore the tile is done.",
]

print()
for phrase in PHRASES:
    print(f"  {phrase}")
    print(f"      properties_named_in -> {sorted(properties_named_in(phrase))}")
    for seg in segment_text(phrase, document_property_ids=[]):
        print(f"      segment -> {seg.property_ids} ({seg.attribution})")
    print()
