"""ARV — After Repair Value and underwriting (added 2026-10-14).

A subject address plus what the person knows about the house goes in; what
comes out is an appraiser-style sales-comparison valuation — comparable sales
found, scored, adjusted line by line to the subject *as repaired*, reconciled
into a value with a range and a confidence grade — and the investor math
built on top of it (the MAO Rehab Calculator, in code): fixed costs, rehab
with adders, the 70% rule, profit, ROI, capital needed, LTARV/LTC, and a
sensitivity grid. Claude Opus 5.5 (effort high) then reads the whole file,
critiques the comps and adjustments, gives its own opinion of value, asks the
questions that would move the number, and answers follow-ups.

Everything deterministic lives in ``engine.py`` and is recomputed instantly
whenever an input changes; the model is consulted for judgement, never for
arithmetic. United States only.

Modules
  config     keys and defaults read from the environment (nothing required)
  geocode    US Census geocoder (free, no key) with OpenStreetMap as fallback
  providers  comparable-sale sources: RentCast, Claude web search, manual
  market     free market context: FRED rates, Zillow ZHVI by ZIP, FEMA flood, Census ACS
  rehab      scope tiers, regional cost multipliers, the line-item template
  engine     comp scoring, adjustments, reconciliation, confidence, underwriting
  analyst    Claude Opus 5.5: comp discovery, the written analysis, the chat
  store      persistence (``arv_analyses``)
  pipeline   the job that runs it end to end, emitting progress
  report     PDF and Excel exports
"""
