"""Outbound mail from rakesh@mtreh.com — admin directive 2026-09-16.

Two things leave the system by email: the next-steps sheets for JP Sir and
Manjunath Sir (on Rakesh's confirmation) and the follow-up reminders the
standard procedure calls for. Everything goes through the ``outbox`` so that a
send that could not happen (no Mail.Send consent yet, Graph down) is visible and
retried, never silently lost — and so every system email is on record with the
conversation id that lets a reply be recognised.
"""
