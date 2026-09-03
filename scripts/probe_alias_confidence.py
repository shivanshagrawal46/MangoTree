"""Confidence assigned to each alias form, against the segmenter's bar."""
import sys

sys.path.insert(0, ".")

from mangotree.chunk.segmenter import SEGMENT_BAR
from mangotree.resolve.property_resolver import _match_aliases

PHRASES = [
    "Varnum is complete and the final draw has been released.",
    "1512 Varnum is complete and the final draw has been released.",
    "Varnum St is complete.",
    "Decatur still needs $4,000 for the roof repair.",
    "912 Decatur still needs $4,000 for the roof repair.",
    "Briardale closing is scheduled.",
    "Chita is under contract.",
    "Tahona estate paperwork is filed.",
    "Euclid NOI was recorded.",
    "Tower Road payoff authorisation received.",
    "The tile at 904 Bayshore is done.",
    "Allison inspection passed.",
]

print(f"\n  SEGMENT_BAR = {SEGMENT_BAR}\n")
print(f"  {'conf':>6}  {'pass':>5}  property        phrase")
print("  " + "-" * 76)
for phrase in PHRASES:
    hits = _match_aliases(phrase, "alias_body")
    if not hits:
        print(f"  {'--':>6}  {'NO':>5}  {'(none)':<15} {phrase[:44]}")
        continue
    for pid, hit in sorted(hits.items(), key=lambda kv: -kv[1].confidence):
        verdict = "yes" if hit.confidence >= SEGMENT_BAR else "NO"
        print(f"  {hit.confidence:>6.2f}  {verdict:>5}  {pid:<15} {phrase[:44]}")
print()
