"""Underwriting and closing (2026-11-20).

One file per deal, from a new address to a recorded security instrument:

    ARV  ->  quick check  ->  the file  ->  clear to close  ->  closing

* ``states``    — what each state changes (Maryland, Virginia, DC, Florida): the
                  instrument, the foreclosure route and time, rate and point limits,
                  recording tax, the contractor's licence register.
* ``engine``    — the three tests (65% rule, foreclosure test, budget test), the kind
                  of deal and the verdict. Arithmetic only; no model.
* ``checklist`` — the fixed list each person owes, built from the kind of deal, the
                  state and who owns the house; and the lock that keeps a deal from
                  closing while anything a foreclosure would need is missing.
* ``store``     — the file itself (collection ``uw_files``).

RKB is the lender. Every rule here answers one question first: if the house does
not sell and RKB has to foreclose, is RKB whole, and does it hold everything the
foreclosure will ask for.
"""
