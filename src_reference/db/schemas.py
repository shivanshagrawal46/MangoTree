"""
Document shape (no ODM — kept as plain dicts for transparency).

emails
------
{
  _id: ObjectId,
  pst_entry_id: str,                  # libpff identifier - unique per PST
  internet_message_id: str | None,    # RFC 5322 Message-ID (preserved if present)
  content_hash: str,                  # sha256 of (from|to|subject|date|body)

  subject: str,
  subject_normalized: str,            # lowercased + Re/Fwd stripped (for thread grouping)

  from: {name, email, domain},
  to:   [{name, email, domain}, ...],
  cc:   [{name, email, domain}, ...],
  bcc:  [{name, email, domain}, ...],
  reply_to: {name, email, domain} | None,

  date: ISODate | None,                # Canonical date (sent > received > modified)
  date_sent: ISODate | None,           # When sender clicked "Send"
  date_received: ISODate | None,       # When delivered to mailbox
  date_modified: ISODate | None,       # Last modified in mailbox
  date_year: int | None,               # 2024
  date_month: int | None,              # 1..12
  date_day: int | None,                # 1..31
  date_ym: str | None,                 # "2024-03"   (great for monthly aggregations)
  date_ymd: str | None,                # "2024-03-15"
  date_weekday: str | None,            # "Monday"

  body_text: str,                     # cleaned plain text (FOR RAG)
  body_text_raw: str,                 # original plain-text body, untouched
  body_html: str,                     # original HTML body, untouched
  body_format: "text" | "html" | "rtf" | "mixed",

  has_attachments: bool,
  attachment_count: int,
  attachment_ids: [ObjectId, ...],    # references to attachments collection

  in_reply_to: str | None,            # Message-ID of parent (from headers)
  references: [str, ...],             # thread chain Message-IDs
  thread_id: str | None,              # heuristic id used to group conversations

  folder_path: str,                   # e.g. "Inbox" or "Inbox/Subfolder"
  importance: "Low" | "Normal" | "High" | None,
  is_read: bool | None,
  size_bytes: int | None,

  headers_raw: { ...all transport headers... },

  pst_source: { file_name, file_sha256 },
  ingested_at: ISODate,
  ingestion_run_id: ObjectId,
}

attachments
-----------
{
  _id: ObjectId,
  email_id: ObjectId,                 # parent email
  email_pst_entry_id: str,
  filename: str,
  display_name: str | None,
  extension: str,                     # ".pdf", ".docx" (lowercased, no dot? -> with dot for clarity)
  content_type: str | None,
  size_bytes: int,
  sha256: str,
  is_inline: bool,
  content_id: str | None,             # for inline images (RFC 2392 cid:)
  gridfs_id: ObjectId,                # binary in GridFS bucket "attachment_files"
  ingested_at: ISODate,
}

folders
-------
{ _id, path, name, email_count }

ingestion_runs
--------------
{
  _id, status, started_at, completed_at,
  pst_file: {path, name, size_bytes, sha256},
  totals: { messages_seen, messages_inserted, messages_skipped,
            attachments_inserted, errors }
}

ingestion_errors
----------------
{ _id, run_id, pst_entry_id, stage, error, traceback, created_at }
"""
