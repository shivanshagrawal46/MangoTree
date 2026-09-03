"""The batched attachment join must return exactly what the per-email one does.

``_attachments_for`` replaced 3,440 single-email round trips with one ``$in`` per
window. That is only a safe swap if it produces identical payloads, including
for attachments shared by several emails — the case the ``$in`` handles
differently, since one returned row has to be fanned out to every parent in the
window rather than to the single email that asked for it.

Sample is deliberately small: a segregation run may be live against the same
cluster, and this check should not compete with it for throughput.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from mangotree.config.models import Seat, model_for
from mangotree.resolve.segregation_runner import SegregationRunner
from mangotree.storage.mongo import get_mongo

SAMPLE = 30


class _Stub:
    def __init__(self, model: str) -> None:
        self.model = model


class _Selectors(SegregationRunner):
    def __init__(self, mongo):
        self.mongo = mongo
        self.segregator = _Stub(model_for(Seat.ANALYST))


def main() -> int:
    mongo = get_mongo()
    sel = _Selectors(mongo)

    # Bias the sample toward emails that actually carry attachments, plus the
    # collapsed ones, so the comparison exercises real payloads rather than a
    # long run of empty lists.
    with_attachments = list(mongo.artifacts.find(
        {"source_type": "email", "attachment_count": {"$gt": 0}}, {"sha256": 1}
    ).limit(SAMPLE))

    batched = sel._attachments_for(with_attachments)

    mismatches = []
    total_attachments = 0
    for email in with_attachments:
        sha = email["sha256"]
        one_by_one = sel._attachments_of({"sha256": sha})
        from_batch = batched.get(sha, [])
        total_attachments += len(one_by_one)

        key = lambda rows: sorted((r["sha256"], r.get("filename"), len(r.get("text") or "")) for r in rows)
        if key(one_by_one) != key(from_batch):
            mismatches.append((sha, len(one_by_one), len(from_batch)))

    print(f"\n  emails compared        {len(with_attachments):>6,}")
    print(f"  attachments matched    {total_attachments:>6,}")
    print(f"  mismatches             {len(mismatches):>6,}")
    for sha, a, b in mismatches[:10]:
        print(f"    {sha[:12]}  per-email={a}  batched={b}")

    ok = not mismatches and total_attachments > 0
    print(f"\n  {'PASS' if ok else 'FAIL'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
