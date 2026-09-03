"""Is a bare single-word alias ever shared by two properties?

The segmenter discounts single-word aliases on the theory that a bare street name
may name several loans. AMBIGUOUS_ALIASES already removes the shared ones, so
this checks whether the discount is still earning its keep or double-counting a
risk that has already been handled.
"""
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from mangotree.config.registry import AMBIGUOUS_ALIASES, PROPERTIES, normalize_text

owners = defaultdict(set)
for prop in PROPERTIES:
    for alias in (prop.canonical_address, *prop.aliases):
        norm = normalize_text(alias)
        if len(norm.split()) == 1:
            owners[norm].add(prop.property_id)

print("\n  single-word aliases and who claims them\n")
collisions = 0
for alias in sorted(owners):
    claimants = sorted(owners[alias])
    flag = ""
    if alias in AMBIGUOUS_ALIASES:
        flag = "  [already blocked as ambiguous]"
    elif len(claimants) > 1:
        flag = "  <-- COLLISION, not blocked"
        collisions += 1
    print(f"    {alias:<14} {','.join(claimants):<28}{flag}")

print(f"\n  unblocked collisions: {collisions}")
print(f"  AMBIGUOUS_ALIASES:    {sorted(AMBIGUOUS_ALIASES)}\n")
