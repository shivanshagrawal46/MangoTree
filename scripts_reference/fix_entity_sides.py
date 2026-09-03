"""Apply the user's locked side rules to entities the original pass missed.

Root cause: `apply_entity_sides.py` only stamped side=david_network where
`is_david` was already True, and is_david was only set when an LLC name could
be matched to a KNOWN property address. Address-coded LLCs with no linked
address (82 CO LLC, 159W LLC, 43LA LLC, 82YA LLC, 132 West 130th LLC, ...),
standalone Directional Lending, all IPA variants, and Brian's co-victim
entities therefore stayed unsided. This applies the rules by NAME.

Rules (user-locked):
  • LLC whose name is address-coded (starts with a house number)  -> david_network
  • IPA / Island Properties / Island Property Associates variants  -> david_network
  • Directional Lending                                            -> david_network
  • No Nebraska Realty, Washington New Realty (+ Brian Detmer)      -> co_victim
Number-only LLC names (e.g. "2034 LLC") with no street are AMBIGUOUS and are
listed for confirmation, NOT auto-assigned.

  python -m scripts.fix_entity_sides            # DRY-RUN (preview only)
  python -m scripts.fix_entity_sides --live     # apply
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone

from config.settings import Settings
from src.db.mongo import MongoClientWrapper
from src.graph.schema import SIDE_DAVID, SIDE_COVICTIM, SIDE_OUR
from src.utils.logger import logger

# Name starts with a house number followed by a street word/initial:
#   "82 CO LLC", "159W LLC", "43LA LLC", "132 West 130th LLC", "9RO LLC"
_ADDR_CODED = re.compile(r"^\s*\d+\s*[A-Za-z]")
# Number-only (ambiguous): "2034 LLC", "1032 LLC" — leading digits, no letter
# immediately attached and no street word.
_NUMBER_ONLY = re.compile(r"^\s*\d+\s+(?:llc|inc|corp|l\.?l\.?c\.?)\b", re.I)

_IPA_HINTS = ("ipa", "island properties", "island property")
_DAVID_NAMES = ("directional lending",)
_COVICTIM_NAMES = ("no nebraska realty", "washington new realty", "brian detmer")
_OUR_NAMES = ("mango tree", "mangotree")


def _is_combined(name: str) -> bool:
    up = (name or "").upper()
    return (" & " in up) or (" AND " in up) or (up.count(",") >= 2)


def classify(name: str) -> str:
    """Returns: 'david' | 'covictim' | 'split' | 'confirm' | 'skip'."""
    up = (name or "").strip()
    low = up.lower()
    # Our own entity (Mango Tree) — even inside an "ET AL", but NOT when mixed
    # with another party via & / AND (those must split).
    if any(h in low for h in _OUR_NAMES) and not _is_combined(up):
        return "our"
    # Combined multi-party strings must be SPLIT, never given one side.
    if _is_combined(up):
        return "split"
    if any(h in low for h in _COVICTIM_NAMES):
        return "covictim"
    if any(h in low for h in _IPA_HINTS) or any(h in low for h in _DAVID_NAMES):
        return "david"
    if _NUMBER_ONLY.match(up):
        return "confirm"          # number-only e.g. "2034 LLC"
    if _ADDR_CODED.match(up):
        # Address-coded LLC -> David's signature. Address-coded INC/CORP is a
        # grey area (David uses LLCs) -> route to confirm.
        if re.search(r"\bllc\b|l\.l\.c", low):
            return "david"
        return "confirm"
    return "skip"


def main() -> int:
    live = "--live" in sys.argv
    s = Settings.load()
    m = MongoClientWrapper(s.mongo_uri, s.mongo_db_name)
    ents = m.db["entities"]
    now = datetime.now(timezone.utc)

    # only act on active, currently-unsided (or unknown) LLC/person entities,
    # and never override an existing explicit side.
    cur = ents.find({"kind": {"$in": ["llc", "person", "org"]},
                     "is_active": {"$ne": False}},
                    {"canonical_name": 1, "side": 1, "is_david": 1})
    to_david, to_covictim, to_our, confirm, to_split = [], [], [], [], []
    for e in cur:
        if e.get("side") in (SIDE_DAVID, SIDE_COVICTIM, SIDE_OUR, "third_party"):
            continue  # respect existing explicit classification
        name = e.get("canonical_name") or ""
        cat = classify(name)
        if cat == "david":
            to_david.append((e["_id"], name))
        elif cat == "covictim":
            to_covictim.append((e["_id"], name))
        elif cat == "our":
            to_our.append((e["_id"], name))
        elif cat == "split":
            to_split.append((e["_id"], name))
        elif cat == "confirm":
            confirm.append((e["_id"], name))

    if live:
        for eid, _ in to_david:
            ents.update_one({"_id": eid}, {"$set": {
                "side": SIDE_DAVID, "is_david": True, "is_david_network": True,
                "side_source": "fix_entity_sides_user_rules", "updated_at": now}})
        for eid, _ in to_covictim:
            ents.update_one({"_id": eid}, {"$set": {
                "side": SIDE_COVICTIM, "is_david": False,
                "side_source": "fix_entity_sides_user_rules", "updated_at": now}})
        for eid, _ in to_our:
            ents.update_one({"_id": eid}, {"$set": {
                "side": SIDE_OUR, "is_ours": True, "is_david": False,
                "side_source": "fix_entity_sides_user_rules", "updated_at": now}})
        for eid, _ in to_split:
            ents.update_one({"_id": eid}, {"$set": {
                "needs_split": True, "needs_review": True,
                "split_reason": "multi_party_owner_string", "updated_at": now}})

    tag = "APPLIED" if live else "DRY-RUN (nothing written)"
    logger.info(f"=== fix_entity_sides {tag} ===")

    def dump(title, rows):
        logger.info(f"-> {title}: {len(rows)}")
        for eid, nm in rows:
            logger.info(f"     {nm!r}  [{eid}]")

    dump("david_network (auto: address-coded LLC + IPA/Directional)", to_david)
    dump("our_side (Mango Tree)", to_our)
    dump("co_victim (Brian's)", to_covictim)
    dump("NEEDS SPLIT (combined multi-party - NOT sided)", to_split)
    dump("CONFIRM (address-coded Inc / number-only - NEED YOUR CALL)", confirm)
    if not live:
        logger.info("Re-run with --live to apply (david/co_victim/needs_split). "
                    "CONFIRM items are never auto-applied.")
    m.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
