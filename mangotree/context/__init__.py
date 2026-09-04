"""Context tiers — what a chunk is embedded *with*.

See docs/03-CONTEXT-AND-MEMORY.md. Tier 1 and Tier 2 are embedded alongside the
chunk; Tier 3 is assembled at answer time and deliberately never embedded,
because a property's live state changes and re-embedding the corpus every time a
balance moves is not a system anyone can operate.
"""
