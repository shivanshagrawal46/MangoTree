"""Per-property timelines — what happened, when, and how we know.

Admin directive 2026-08-31: "we have to also make sure we have a full timeline
based for each property so that we could know that which event happen when —
time is very essential here."

Two passes, deliberately separate:

* **Document-level (deterministic).** Every artifact is itself an event: a deed
  of trust exists as of its date, a title policy was issued on its date. Free,
  exact, and complete by construction — every document produces exactly one
  event, so the timeline can never be missing a document it holds.
* **In-document (model-extracted).** The events *described inside* documents:
  an inspection passed on 26 Feb, a wire went out on 9 Jun, a deadline falls on
  26 Jul. These are the ones that actually answer "what happened when", and they
  only exist in prose.

Every event carries its source artifact and page, so any date on the timeline can
be walked back to the line of the document that asserts it.
"""
