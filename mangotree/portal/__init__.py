"""The contractor portal (admin directive 2026-10-08): Wes signs in with his own
account and answers the next steps written for him, item by item.

Three rules hold everything together:

* **Default deny.** A contractor session is refused by every RKB route
  (``api.auth.current_user``); the portal has its own routes and reads only
  the projection built for it (``contractor_sheets``), never the internal
  collections — no "why", no evidence, no money, no RKB tasks, no documents.
* **Append-only trail.** Everything a contractor does — opened the sheet,
  replied, reported an item done — is one entry in ``contractor_events``,
  hash-chained to the previous entry. Nothing there is ever updated or
  deleted; a correction is a new entry.
* **Their words become records.** A reply is also stored as a record on the
  property (a document authored by Wes, source ``portal``), so the next-steps
  writer reads it in "responses since the previous sheet" the next morning and
  it is quotable like any email. "Reported done" is shown to RKB as reported;
  only RKB closes an item.
"""
