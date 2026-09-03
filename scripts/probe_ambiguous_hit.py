"""Does an ambiguous alias produce a hit the veto can see?"""
import sys

sys.path.insert(0, ".")

from mangotree.resolve.property_resolver import _match_aliases

for phrase in (
    "Bayshore inspection was rescheduled.",
    "904 Bayshore inspection was rescheduled.",
    "Bayshore Drive inspection was rescheduled.",
):
    hits = _match_aliases(phrase, "alias_body")
    print(f"\n  {phrase}")
    if not hits:
        print("      (no hits at all)")
    for pid, hit in hits.items():
        print(f"      {pid:<14} {hit.confidence:.2f}  {hit.signals}")
print()
