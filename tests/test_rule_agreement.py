"""The counting rule and the ingesting rule must never disagree.

The corpus was measured at 3,417 qualifying messages by
``scripts/mail_scope_count.py`` and approved at that size. Ingestion applies
``participants.decide``. Those are two pieces of code expressing one rule, and
if they drift the symptom is a corpus that is quietly smaller than approved —
no error, no warning, just missing evidence discovered months later when an
answer has a hole in it.

This test is the alarm. It runs both implementations over the same inputs and
fails the moment they diverge.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from mangotree.ingest.participants import Decision, build_participants, decide  # noqa: E402
from scripts.mail_scope_count import classify  # noqa: E402

#: bucket from the counting rule -> decision the pipeline must reach
EXPECTED = {
    "A_known_contact": Decision.INGEST,
    "B_property_subject": Decision.INGEST,
    "C_internal": Decision.SKIP_INTERNAL_ONLY,
    "D_no_signal": Decision.SKIP_NO_SIGNAL,
}

CASES = [
    ("rakesh@mtreh.com", "jp@mtreh.com", "", "Varnum payoff update"),
    ("rakesh@mtreh.com", "wes@roiblocks.com", "", "weekly update"),
    ("rakesh@mtreh.com", "stranger@example.com", "", "RE: 4304 Varnum St"),
    ("rakesh@mtreh.com", "stranger@example.com", "", "newsletter"),
    ("rakesh.bhargava@gmail.com", "rakesh@mtreh.com", "", "notes"),
    ("bgallagher@g-e-law.com", "rakesh@mtreh.com", "jp@mtreh.com", "Chita Court"),
    ("neha@mtreh.com", "manjunath@mtreh.com", "", "Ridge Road site visit"),
    ("rakesh@mtreh.com", "x@out.com", "", "Bayshore update"),
    ("rakesh@mtreh.com", "x@out.com", "", "904 Bayshore Dr roof"),
    ("newhire@mtreh.com", "rakesh@mtreh.com", "", "Tahona"),
    # Bcc case: counsel to builder, Rakesh Sir blind-copied, so no RKB address
    # survives in the headers. Both rules must still admit it.
    ("bgallagher@g-e-law.com", "wes@roiblocks.com", "", "Varnum foreclosure"),
    ("newsletter@marketing.com", "someone@else.com", "", "sale ends friday"),
    ("kelly@lpremodel.com", "rakesh@mtreh.com", "advancecpa@gmail.com", "draw request"),
    ("stranger@example.com", "other@example.com", "", "513 Allison St NW"),
    ("rakesh@mtreh.com", "", "", "no recipients at all"),
]


@pytest.mark.parametrize("sender,to,cc,subject", CASES)
def test_both_rules_agree(sender: str, to: str, cc: str, subject: str) -> None:
    addresses = [a for a in (sender, to, cc) if a]

    counted = classify(addresses, subject)["bucket"]
    headers = {"From": sender, "To": to, "Cc": cc, "Subject": subject}
    ingested = decide(build_participants(headers), subject=subject).decision

    assert ingested is EXPECTED[counted], (
        f"rules disagree on {subject!r}: counting says {counted}, "
        f"pipeline says {ingested.value}"
    )


def test_every_bucket_is_covered() -> None:
    """A guard that stays honest as cases are added or removed.

    Agreement on a set of cases that happens to omit a whole branch would pass
    while proving nothing about that branch.
    """
    seen = {
        classify([a for a in (s, t, c) if a], subj)["bucket"]
        for s, t, c, subj in CASES
    }
    assert seen == set(EXPECTED), f"buckets never exercised: {set(EXPECTED) - seen}"
