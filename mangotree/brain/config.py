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
#: How a newly arrived record is read into its file (2026-11-14).
#:   "lean" — the model is handed the property's page, the last 14 days of emails
#:            in full, a one-line list of every document on file, and the new
#:            record with its own attachments in full; anything else it opens or
#:            searches for itself, up to EVENT_LOOKUPS times.
#:   "full" — the earlier way: every email AND every attachment of 14 days in
#:            full with every record, and no way to look anything up.
#: Reply drafts and chat are not affected: they keep the full record.
EVENT_MODE = os.environ.get("MT_BRAIN_EVENT_MODE", "lean").strip().lower()
#: Look-ups (open a document, search, recent mail …) one new record may use.
#: Five since 2026-11-21 (admin: "a look up of 5 by ai"); three before.
EVENT_LOOKUPS = 5
#: When new records are read into the files and replies are drafted, on the firm's clock
#: (US Eastern): twice a day and at no other time by itself (admin, 2026-11-21: "step 2 and
#: step 3 should run automatically only twice a day once 11 am EST and 6 am EST"). A person
#: can still start a reading by hand. A run missed at its hour (the server was down) is made
#: up within RUN_LATE_HOURS, not later.
RUN_HOURS = (6, 11)
RUN_LATE_HOURS = 4
#: The lean reading is made at high effort: what it is handed is small, so care is
#: affordable. The second reading MUST use the same effort — a different effort
#: makes the API store the whole prompt a second time instead of reusing it.
EVENT_EFFORT = os.environ.get("MT_BRAIN_EFFORT_EVENT_LEAN", "high")
#: A second reading of every entry before it is filed: what the record says that
#: the entry left out, what it got wrong, an action nobody was given, and whether
#: the reply decision is right. What it finds goes back to the writer once.
EVENT_CHECK = os.environ.get("MT_BRAIN_EVENT_CHECK", "1").strip().lower() not in ("0", "false", "no")
#: The document list: a document newer than this gets a line saying what it is;
#: an older one is listed by name and date only. All of them can be opened.
DOC_LIST_FULL_DAYS = 90
DOC_LIST_LINE_CHARS = 280
DOC_LIST_MAX = 600
#: The mail a new record is read against (2026-11-14). A file whose conversations
#: of the last 14 days come to no more than this, once cleaned, is given whole.
#: A busier file is given the conversations of the last two days and those
#: connected to the new record in full, and every other one in a single line.
#: Whether a quoted message that stands in the chain as its own email is left out
#: where it is quoted again. OFF: an email is kept as it was sent, its chain under
#: it (admin directive 2026-11-14: "it's a more safer option"). Only the signature
#: comes off. The code for leaving repeats out stays, to be measured, not used.
MAIL_DROP_REPEATS = os.environ.get("MT_BRAIN_DROP_REPEATS", "0").strip().lower() in ("1", "true", "yes")
MAIL_FULL_CHARS = 80_000
#: Room for conversations older than two days that are connected to the new record.
MAIL_CONNECTED_CHARS = 30_000
#: A file with this many emails in the last day is busy enough for the stable
#: parts of its prompt to be kept for an hour (written at twice the input price,
#: read at a twentieth) rather than five minutes.
BUSY_FILE_EMAILS = 3
#: Whether a busy file's stable parts are kept for an hour at all. Off since 2026-11-21: new
#: records are read in two runs a day, one file's records back to back (``updater.by_file``),
#: so the next reading of a file comes within minutes and the five-minute store is enough —
#: the hour would cost twice the input price to write and never be used.
EVENT_CACHE_HOUR = os.environ.get("MT_BRAIN_CACHE_HOUR", "0").strip().lower() in ("1", "true", "yes")
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
#: Whether opening the Today page also starts a reading of unread mail, outside the two daily
#: runs. On unless switched off (admin, 2026-11-21, kept it). The "Check for new mail" button
#: always starts one.
SETTING_READ_ON_OPEN = "brain_read_on_open"
#: When on, a finished draft is also placed in the Outlook Drafts folder.
SETTING_OUTLOOK_DRAFTS = "brain_outlook_drafts"
