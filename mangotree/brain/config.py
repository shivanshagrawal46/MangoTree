"""Brain knobs, in one place."""
from __future__ import annotations

import os

#: The only model the brain uses — status, drafts, chat, everything.
MODEL = "claude-opus-5-5"

#: Effort is the one dial for depth, latency and cost on this model (thinking is
#: always on). The API default is ``medium``; set explicitly everywhere.
EFFORT_CHAT = os.environ.get("MT_BRAIN_EFFORT_CHAT", "medium")
EFFORT_EVENT = os.environ.get("MT_BRAIN_EFFORT_EVENT", "medium")
EFFORT_DRAFT = os.environ.get("MT_BRAIN_EFFORT_DRAFT", "medium")
EFFORT_BOOTSTRAP = os.environ.get("MT_BRAIN_EFFORT_BOOTSTRAP", "high")
#: The second reading of every answer and draft. This is the check that protects
#: the firm from a wrong statement, so it runs at high effort.
EFFORT_VERIFY = os.environ.get("MT_BRAIN_EFFORT_VERIFY", "high")
EFFORT_LESSON = os.environ.get("MT_BRAIN_EFFORT_LESSON", "medium")

#: The file for mail that belongs to no single property (portfolio-level,
#: unplaced, other business). Nothing that arrives is left unread.
GENERAL_ID = "_general"
GENERAL_NAME = "General & portfolio"

#: "The last 7 days" that rides with every prompt.
ACTIVITY_DAYS = 7
#: How far back the first build reads mail in full.
BOOTSTRAP_MAIL_DAYS = 45
#: Per-item and total character budgets when records are placed in a prompt.
#: A truncated item always says so and names how to open the rest.
#: Sized so that, in practice, nothing is cut: the longest email body in a recent
#: fortnight was 29k characters and the longest attachment 180k. (At the first
#: build these were 12k / 24k / 90k, and 19 of 71 recent attachments were read
#: only in part.) The model has a 1M-token window; use it.
EMAIL_BODY_CHARS = 60_000
ATTACHMENT_CHARS = 300_000
ATTACHMENTS_TOTAL_CHARS = 900_000
THREAD_MESSAGES = 30
#: The actual emails — not summaries — of this many days ride with every chat
#: turn, every draft and every update for a property, in full.
MAIL_DAYS = 14
MAIL_BLOCK_CHARS = 700_000
#: Attachments are FILES, not part of an email (admin directive 2026-11-12). Every file
#: attached or uploaded in the same window is given to the model in full, once
#: each, under its own heading. The largest fortnight measured was 420k characters.
FILES_BLOCK_CHARS = 1_000_000
#: What the document search may place in one prompt. Past this, further passages are
#: listed by number and opened on demand. (A general-file draft whose five
#: searches each fanned out reached 1.43M tokens and was refused, 2026-11-12.)
SEED_CHARS = 450_000
#: When a conversation with the model has grown this large, it is told to finish
#: with what it has. The model's window is 1,000,000 tokens.
CONTEXT_TOKEN_LIMIT = 780_000
BOOTSTRAP_TOTAL_CHARS = 420_000

#: Chat: tool rounds before the model is told to answer from what it has.
CHAT_MAX_ROUNDS = 12
CHAT_MAX_TOKENS = 32_000
CHAT_HISTORY_MESSAGES = 40
DRAFT_MAX_ROUNDS = 8

#: Who the system drafts for by default.
DEFAULT_SENDER = "rakesh"
SENDER_NAMES = {"rakesh": "Rakesh", "jp": "JP", "manjunath": "Manjunath"}

#: Automatic processing of new mail (the scheduler calls the updater). Can be
#: switched off from settings without a deploy.
SETTING_ENABLED = "brain_enabled"
#: When on, a finished draft is also placed in the Outlook Drafts folder.
SETTING_OUTLOOK_DRAFTS = "brain_outlook_drafts"
