"""Attach every unattributed email attachment to its property.

    python scripts/inherit_attachment_properties.py           # report only
    python scripts/inherit_attachment_properties.py --apply    # write
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.resolve.attachment_inherit import inherit_properties
from mangotree.storage.mongo import get_mongo

APPLY = "--apply" in sys.argv


def main() -> None:
    mongo = get_mongo()
    stats, decisions = inherit_properties(mongo, apply=APPLY)

    print("=" * 78)
    print("ATTACHMENT PROPERTY INHERITANCE" + ("" if APPLY else "  (dry run)"))
    print("=" * 78)
    for key, value in stats.as_dict().items():
        print(f"  {key:<26}{value}")

    by_method: dict[str, list] = {}
    for d in decisions:
        by_method.setdefault(d.method, []).append(d)

    for method, items in sorted(by_method.items(), key=lambda kv: -len(kv[1])):
        print(f"\n--- {method}  ({len(items)}) ---")
        for d in items[:25]:
            flag = "  [REVIEW]" if d.needs_review else ""
            print(f"  {d.property_ids}  {d.filename}{flag}")
            if d.note:
                print(f"        {d.note}")
        if len(items) > 25:
            print(f"  ... and {len(items) - 25} more")

    unresolved = stats.considered - len(decisions)
    print(f"\n  still unattributed: {unresolved}")

    if not APPLY:
        print("\n(dry run — pass --apply to write)")


if __name__ == "__main__":
    main()
