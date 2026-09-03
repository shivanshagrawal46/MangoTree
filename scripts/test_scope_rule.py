"""Check the qualifying rule against hand-written cases before it meets live mail.

Cheap insurance: the rule has four branches and an ordering dependency (internal
exclusion must beat a property match), so a silent inversion would produce a
plausible-looking but wrong count of exactly the mail we care about.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from scripts.mail_scope_count import classify  # noqa: E402

CASES = [
    ("Rakesh -> JP, Varnum in subject", ["rakesh@mtreh.com", "jp@mtreh.com"],
     "Varnum payoff update", "C_internal"),
    ("Rakesh -> Wes (builder)", ["rakesh@mtreh.com", "wes@roiblocks.com"],
     "weekly update", "A_known_contact"),
    ("Rakesh -> unknown, property in subject",
     ["rakesh@mtreh.com", "someone@random.com"], "RE: 4304 Varnum St",
     "B_property_subject"),
    ("Rakesh -> unknown, no property",
     ["rakesh@mtreh.com", "someone@random.com"], "newsletter", "D_no_signal"),
    ("Rakesh gmail -> Rakesh mtreh (self)",
     ["rakesh.bhargava@gmail.com", "rakesh@mtreh.com"], "notes", "C_internal"),
    ("Lawyer -> Rakesh + JP",
     ["bgallagher@g-e-law.com", "rakesh@mtreh.com", "jp@mtreh.com"],
     "Chita Court foreclosure", "A_known_contact"),
    ("Neha -> Manjunath, property named",
     ["neha@mtreh.com", "manjunath@mtreh.com"], "Ridge Road site visit",
     "C_internal"),
    ("bare 'Bayshore' is too ambiguous to attribute",
     ["rakesh@mtreh.com", "x@out.com"], "Bayshore update", "D_no_signal"),
    ("'904 Bayshore' is specific enough",
     ["rakesh@mtreh.com", "x@out.com"], "904 Bayshore Dr roof",
     "B_property_subject"),
    ("unlisted @mtreh.com staff still counts as internal",
     ["newhire@mtreh.com", "rakesh@mtreh.com"], "Tahona", "C_internal"),
]


def main() -> int:
    failures = 0
    print(f"\n  {'case':<46}{'expected':<22}{'got':<22}")
    print("  " + "-" * 88)
    for name, addresses, subject, expected in CASES:
        verdict = classify(addresses, subject)
        got = verdict["bucket"]
        ok = got == expected
        failures += 0 if ok else 1
        mark = "ok  " if ok else "FAIL"
        props = ",".join(sorted(verdict["properties"])) or "-"
        print(f"  {mark} {name:<42}{expected:<22}{got:<22}{props}")

    print()
    if failures:
        print(f"  {failures} case(s) failed — rule does not match the instruction\n")
        return 1
    print(f"  all {len(CASES)} cases match the stated rule\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
