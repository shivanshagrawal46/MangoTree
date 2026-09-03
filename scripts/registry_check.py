"""Verify the seeded registry matches the admin's list exactly.

The list is held here verbatim so the check is against what was actually asked
for, not against the registry restating itself. Extras matter as much as
omissions: the instruction was "that's it, not anything more".
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.registry import (
    ADDRESS_INDEX, PEOPLE, PROPERTIES, PROPERTY_CONTACTS,
)
from mangotree.storage.mongo import get_mongo

#: Verbatim from the admin, 2026-09-01.
EXPECTED = {
    "sweetinfo@thevines.farm", "jarmstrong808@gmail.com", "robsellsdmv@gmail.com",
    "kim.gallihugh@c21nm.com", "endy@cornerstoneremodelingva.com",
    "cfields971@gmail.com", "mekicross@gmail.com", "a.parva@aparchllc.com",
    "carpentrykvc@gmail.com",
    "wes@roiblocks.com", "wes@lpremodel.com", "panos@roiblocks.com",
    "kelly@lpremodel.com", "alicia@lpremodel.com", "alicia@roiblocks.com",
    "rakesh@mtreh.com", "rakesh.bhargava@gmail.com", "neha@mtreh.com",
    "manjunath@mtreh.com", "jp@mtreh.com",
    "marti@closewithpotomac.com", "rwoodall@kvstitle.com",
    "bgallagher@g-e-law.com", "ndoyle@g-e-law.com", "cnattans@g-e-law.com",
    "kmadden@g-e-law.com", "gallagher@briantgallagherlaw.com",
    "advancecpa@gmail.com", "equinn@quinnlegal.com",
    "mgonzalez@kcwilson.com", "mgonzalez@kcwilsonassociates.com",
    "wendy@rslaytonpc.com", "charlenejones@kw.com",
    "seth@sayleslegal.com", "seth@saylesatlaw.com",
    "gwen.bass@dc.gov", "patricia.watson@dc.gov",
    "mortgage.payoff@usbank.com", "payoff@navyfederal.org",
    "jessica@lovelivedc.com",
    # Kept at the admin's explicit direction on 2026-09-01.
    "bill@conduitbankers.com",
}


def main() -> None:
    actual = set(ADDRESS_INDEX)
    missing = EXPECTED - actual
    extra = actual - EXPECTED

    print(f"expected addresses  {len(EXPECTED)}")
    print(f"registry addresses  {len(actual)}")
    print(f"people              {len(PEOPLE)}")
    print(f"properties          {len(PROPERTIES)}\n")

    if missing:
        print("MISSING from registry:")
        for a in sorted(missing):
            print(f"  {a}")
    if extra:
        print("EXTRA, not on the admin's list:")
        for a in sorted(extra):
            print(f"  {a}  ({ADDRESS_INDEX[a].display_name})")
    if not missing and not extra:
        print("Addresses match the admin's list exactly.")

    no_address = [p for p in PEOPLE if not p.all_addresses]
    if no_address:
        print("\nregistered with no address (cannot auto-match mail):")
        for p in no_address:
            print(f"  {p.display_name} — {p.role}")

    db = get_mongo().db
    print(f"\nseeded in MongoDB: {db['people'].count_documents({})} people, "
          f"{db['properties'].count_documents({})} properties")

    print("\nproperties:")
    for prop in sorted(PROPERTIES, key=lambda p: p.property_id):
        contacts = PROPERTY_CONTACTS.get(prop.property_id, [])
        disk = "disk" if prop.disk_folder else "no disk folder"
        print(f"  {prop.property_id:<15}{prop.canonical_address:<26}"
              f"{prop.deal_type:<10}{disk:<16}{len(contacts)} contacts")


if __name__ == "__main__":
    main()
