"""Does a bare street name inherit the previous property's attribution?

This is the contamination case the admin cares most about: one email covering two
properties, where the second is named without a house number.
"""
import sys

sys.path.insert(0, ".")

from mangotree.chunk.segmenter import segment_text

CASES = {
    "bare aliases, both properties": (
        "Varnum is complete and the final draw has been released.\n\n"
        "Decatur still needs $4,000 for the roof repair.\n\n"
        "Please approve the Decatur amount this week."
    ),
    "numbered aliases, both properties": (
        "1512 Varnum is complete and the final draw has been released.\n\n"
        "912 Decatur still needs $4,000 for the roof repair."
    ),
    "bare alias after numbered": (
        "1512 Varnum is complete and the final draw has been released.\n\n"
        "Decatur still needs $4,000 for the roof repair."
    ),
}

for title, text in CASES.items():
    print(f"\n=== {title} ===")
    for seg in segment_text(text, document_property_ids=[]):
        tag = ",".join(seg.property_ids) or "-none-"
        print(f"  [{tag:<12}] {seg.attribution:<10} {seg.text[:70]}")
print()
